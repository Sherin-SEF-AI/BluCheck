"""Driver push notifications.

Two delivery paths, chosen by token shape:
- Expo push (token looks like ``ExponentPushToken[...]``) via the Expo push service.
- Firebase Cloud Messaging v1 (a raw device/FCM registration token) via the Google
  FCM HTTP v1 API, authenticated with a service-account key stored in Secrets Manager
  at ``blucheck/fcm``.

FCM is what delivers notifications when the driver's app is fully closed. Everything is
best-effort: failures are logged and never raised, so a delivery problem can never block
a review or an agent decision. When no FCM secret is configured the FCM path simply logs
and returns, so the backend is safe to deploy before the credentials exist.
"""

from __future__ import annotations

import json
import logging
import os

import boto3
import requests

logger = logging.getLogger("blucheck.push")

EXPO_URL = "https://exp.host/--/api/v2/push/send"
# Derive from RESOURCE_PREFIX (set on every task) so a non-default prefix still resolves the right
# secret ("${prefix}/fcm"), matching how the worker resolves the RunPod secret. An explicit
# FCM_SECRET_NAME still overrides.
FCM_SECRET_NAME = os.environ.get("FCM_SECRET_NAME") or f"{os.environ.get('RESOURCE_PREFIX', 'blucheck')}/fcm"
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

_secrets = boto3.client("secretsmanager", region_name=AWS_REGION)
# Cache the service-account credentials + resolved project id across calls.
_fcm_cache: dict = {}


# send_push outcomes: "ok" delivered; "unregistered" the token is dead (app uninstalled /
# reinstalled) and should be cleared; "error" a transient failure; "skipped" push disabled/no token.
OK, UNREGISTERED, ERROR, SKIPPED = "ok", "unregistered", "error", "skipped"


def send_push(token: str | None, title: str, body: str, data: dict | None = None) -> str:
    if not token:
        return SKIPPED
    if token.startswith("ExponentPushToken") or token.startswith("ExpoPushToken"):
        return _send_expo(token, title, body, data)
    return _send_fcm(token, title, body, data)


def send_to_driver(db, driver, title: str, body: str, data: dict | None = None) -> str:
    """Send to a driver and self-heal a dead token: if the push service reports the token is no
    longer registered (e.g. after an app reinstall), clear it so we stop sending to a dead token
    and the driver re-registers a fresh one on next login. Returns the send status. Best-effort;
    never raises. `driver` is any object with `push_token` (and optionally `id`)."""
    if driver is None or not getattr(driver, "push_token", None):
        return SKIPPED
    status = send_push(driver.push_token, title, body, data)
    if status == UNREGISTERED:
        logger.info("clearing unregistered push token for driver=%s", getattr(driver, "id", "?"))
        try:
            driver.push_token = None
            db.add(driver)
            db.commit()
        except Exception as err:  # noqa: BLE001 - clearing is best-effort
            logger.warning("could not clear stale push token: %s", err)
            db.rollback()
    return status


def _send_expo(token: str, title: str, body: str, data: dict | None) -> str:
    try:
        r = requests.post(
            EXPO_URL,
            json={"to": token, "title": title, "body": body, "data": data or {}, "sound": "default"},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if r.status_code >= 300:
            logger.warning("expo push non-2xx: %s %s", r.status_code, r.text[:200])
            return ERROR
        # Expo returns 200 with a per-message receipt; a dead token shows as DeviceNotRegistered.
        if "DeviceNotRegistered" in r.text:
            return UNREGISTERED
        return OK
    except requests.RequestException as err:
        logger.error("expo push failed: %s", err)
        return ERROR


def _fcm_config() -> dict | None:
    """Load {project_id, credentials} from Secrets Manager, cached. Returns None if the
    secret or the google-auth dependency is unavailable (FCM simply disabled)."""
    if _fcm_cache.get("loaded"):
        return _fcm_cache.get("cfg")
    _fcm_cache["loaded"] = True
    try:
        raw = _secrets.get_secret_value(SecretId=FCM_SECRET_NAME)["SecretString"]
        sa = json.loads(raw)
        from google.oauth2 import service_account  # deferred: optional dependency

        creds = service_account.Credentials.from_service_account_info(
            sa, scopes=["https://www.googleapis.com/auth/firebase.messaging"]
        )
        _fcm_cache["cfg"] = {"project_id": sa["project_id"], "creds": creds}
    except Exception as err:  # noqa: BLE001 - FCM is optional; disable cleanly if unset
        logger.info("FCM not configured (%s); skipping native push", type(err).__name__)
        _fcm_cache["cfg"] = None
    return _fcm_cache.get("cfg")


def _send_fcm(token: str, title: str, body: str, data: dict | None) -> str:
    cfg = _fcm_config()
    if not cfg:
        return SKIPPED
    try:
        from google.auth.transport.requests import Request

        cfg["creds"].refresh(Request())
        access_token = cfg["creds"].token
        # FCM data values must be strings.
        str_data = {k: str(v) for k, v in (data or {}).items()}
        payload = {
            "message": {
                "token": token,
                "notification": {"title": title, "body": body},
                "data": str_data,
                "android": {"priority": "high", "notification": {"channel_id": "reviews"}},
            }
        }
        r = requests.post(
            f"https://fcm.googleapis.com/v1/projects/{cfg['project_id']}/messages:send",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        if r.status_code >= 300:
            logger.warning("fcm push non-2xx: %s %s", r.status_code, r.text[:200])
            # 404 NOT_FOUND / UNREGISTERED (or 400 INVALID_ARGUMENT on the token) => dead token.
            if r.status_code == 404 or "UNREGISTERED" in r.text or "NOT_FOUND" in r.text:
                return UNREGISTERED
            return ERROR
        return OK
    except Exception as err:  # noqa: BLE001 - never let a push failure propagate
        logger.error("fcm push failed: %s", err)
        return ERROR
