import { useEffect, useRef, useState } from "react";
import { View, Text, Pressable, StyleSheet, ActivityIndicator, Platform } from "react-native";
import { CameraView, useCameraPermissions, useMicrophonePermissions, type CameraType } from "expo-camera";
import { useLocalSearchParams, router } from "expo-router";
import * as Device from "expo-device";
import * as Application from "expo-application";
import * as Haptics from "expo-haptics";
import { createInspection, precheckCapture, type Gps, type Precheck } from "@/lib/api";
import { captureGps, LocationDeniedError } from "@/lib/gps";
import { enqueueCapture, processQueue } from "@/lib/uploadQueue";
import { colors } from "@/lib/theme";

const CLIP_SECONDS = 15;
// Flow: ready -> prechecking (auto photo: is-this-a-vehicle check) -> [fail -> retry] -> exterior
// (auto record) -> exterior_done (Start interior) -> interior (auto record) -> submitting.
// A targeted re-clean (groups param) records only the flagged group(s), skipping the rest.
type Step = "ready" | "prechecking" | "precheck_fail" | "exterior" | "exterior_done" | "interior" | "submitting" | "submit_error";
type Clip = { uri: string; recordedAt: string };

// Night inspections need light: the torch turns on automatically after dark (local time). The
// driver can still override it. Camera APIs do not expose ambient lux, so time-of-day is used.
function isNight(): boolean {
  const h = new Date().getHours();
  return h < 6 || h >= 18;
}

export default function Capture() {
  const { vehicleId, plate, reinspectionOf, groups, zones } = useLocalSearchParams<{
    vehicleId: string; plate: string; reinspectionOf?: string; groups?: string; zones?: string;
  }>();
  const cameraRef = useRef<CameraView>(null);
  const [camPerm, requestCam] = useCameraPermissions();
  const [micPerm, requestMic] = useMicrophonePermissions();

  // Targeted re-clean: which capture groups to re-film. Defaults to a full inspection.
  const targetGroups: ("exterior" | "interior")[] = (() => {
    const raw = (groups ?? "").split(",").map((s) => s.trim()).filter(Boolean);
    const set = raw.filter((g): g is "exterior" | "interior" => g === "exterior" || g === "interior");
    return set.length ? Array.from(new Set(set)) : ["exterior", "interior"];
  })();
  const needExterior = targetGroups.includes("exterior");
  const needInterior = targetGroups.includes("interior");
  const isReclean = !!reinspectionOf;
  const isTargeted = isReclean && targetGroups.length < 2;

  const [step, setStep] = useState<Step>("ready");
  const [remaining, setRemaining] = useState(CLIP_SECONDS);
  const [recording, setRecording] = useState(false);
  const [cameraReady, setCameraReady] = useState(false);
  const [torch, setTorch] = useState(isNight());
  const [facing, setFacing] = useState<CameraType>("back");
  const [error, setError] = useState<string | null>(null);

  const runningRef = useRef(false);
  const clips = useRef<{ exterior?: Clip; interior?: Clip; gps?: Gps; startedAt?: string }>({});

  useEffect(() => {
    if (!camPerm?.granted) requestCam();
    if (!micPerm?.granted) requestMic();
  }, [camPerm, micPerm]);

  const cameraReadyRef = useRef(false);
  useEffect(() => { cameraReadyRef.current = cameraReady; }, [cameraReady]);

  function deviceMeta() {
    return {
      device_model: Device.modelName ?? "unknown",
      os: `${Platform.OS} ${Device.osVersion ?? ""}`.trim(),
      app_version: Application.nativeApplicationVersion ?? "1.0.0",
    };
  }

  async function waitForCamera(): Promise<void> {
    for (let i = 0; i < 50 && !cameraReadyRef.current; i += 1) {
      await new Promise((r) => setTimeout(r, 150));
    }
  }

  async function recordOne(kind: "exterior" | "interior"): Promise<Clip> {
    setRecording(false);
    setRemaining(CLIP_SECONDS);
    await waitForCamera();
    await new Promise((r) => setTimeout(r, 300));

    const started = Date.now();
    setRecording(true);
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => undefined);
    const countdown = setInterval(() => {
      setRemaining(Math.max(0, CLIP_SECONDS - Math.floor((Date.now() - started) / 1000)));
    }, 250);
    const backupStop = setTimeout(() => {
      try { cameraRef.current?.stopRecording(); } catch { /* ignore */ }
    }, CLIP_SECONDS * 1000 + 400);

    const recordedAt = new Date().toISOString();
    try {
      const video = await cameraRef.current?.recordAsync({ maxDuration: CLIP_SECONDS });
      if (!video?.uri) throw new Error("Recording produced no file");
      return { uri: video.uri, recordedAt };
    } finally {
      clearInterval(countdown);
      clearTimeout(backupStop);
      setRecording(false);
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => undefined);
    }
  }

  // Start: GPS -> auto photo -> is-this-a-vehicle pre-check. Non-car footage is rejected here, on
  // the phone, before any recording. (Plate OCR is disabled for now.)
  async function beginFlow() {
    if (runningRef.current) return;
    runningRef.current = true;
    setError(null);
    try {
      const fix = await captureGps();
      clips.current.gps = fix;
      clips.current.startedAt = new Date().toISOString();
      await runPrecheck();
    } catch (e) {
      runningRef.current = false;
      setStep("ready"); setRecording(false);
      setError(e instanceof LocationDeniedError ? e.message : e instanceof Error ? e.message : "Could not start inspection");
    }
  }

  async function runPrecheck() {
    setStep("prechecking"); setError(null);
    await waitForCamera();
    await new Promise((r) => setTimeout(r, 250));
    let base64: string | undefined;
    try {
      const photo = await cameraRef.current?.takePictureAsync({ base64: true, quality: 0.5 });
      base64 = photo?.base64 ?? undefined;
      if (!base64) throw new Error("Could not capture a photo");
    } catch (e) {
      setStep("precheck_fail");
      setError(e instanceof Error ? e.message : "Could not capture a photo. Try again.");
      return;
    }
    let res: Precheck;
    try {
      res = await precheckCapture(base64);
    } catch {
      // If the check itself is unreachable, don't hard-block the driver.
      res = { is_vehicle: true, vehicle_confidence: null, labels: [], read_plate: null, matched: false, expected: plate ?? null };
    }
    if (!res.is_vehicle) {
      setStep("precheck_fail");
      setError("This doesn't look like a vehicle. Point the camera at the car and try again.");
      return;
    }
    // Car confirmed. Record whichever groups this run needs. A targeted interior-only re-clean
    // skips the exterior entirely and goes straight to interior.
    if (needExterior) {
      setStep("exterior");
      try {
        clips.current.exterior = await recordOne("exterior");
        if (needInterior) {
          setStep("exterior_done");
        } else {
          await submit();
        }
      } catch (e) {
        runningRef.current = false;
        setStep("ready"); setRecording(false);
        setError(e instanceof Error ? e.message : "Could not record exterior");
      }
    } else {
      // Interior-only re-clean.
      await startInterior();
    }
  }

  // Records the interior, then submits. All required clips must exist (submit guards on it).
  async function startInterior() {
    setError(null); setStep("interior");
    try {
      clips.current.interior = await recordOne("interior");
      await submit();
    } catch (e) {
      setStep(needExterior ? "exterior_done" : "ready"); setRecording(false);
      if (!needExterior) runningRef.current = false;
      setError(e instanceof Error ? e.message : "Could not record interior");
    }
  }

  async function submit(): Promise<void> {
    const { exterior, interior, gps, startedAt } = clips.current;
    const haveNeeded = (!needExterior || !!exterior) && (!needInterior || !!interior);
    if (!haveNeeded || !gps || !startedAt) {
      setStep(needExterior ? "exterior_done" : "ready");
      setError("The required clip(s) are missing. Please record again.");
      return;
    }
    setStep("submitting"); setError(null);
    try {
      // For a targeted re-clean, declare which kinds the backend should expect so it scores the
      // subset (rather than waiting for both). Omitted for a normal full inspection.
      const meta: Record<string, unknown> = deviceMeta();
      if (isReclean) meta.reclean_kinds = targetGroups;
      const created = await createInspection({
        vehicle_id: String(vehicleId), gps,
        captured_at_utc: startedAt, captured_at_local: new Date().toString(),
        device_meta: meta,
        reinspection_of: reinspectionOf ?? null,
      });
      const id = created.inspection_id;
      if (needExterior && exterior) {
        await enqueueCapture({ inspectionId: id, kind: "exterior", videoUri: exterior.uri, gps, recordedAtUtc: exterior.recordedAt, durationS: CLIP_SECONDS, resolution: "1920x1080" });
      }
      if (needInterior && interior) {
        await enqueueCapture({ inspectionId: id, kind: "interior", videoUri: interior.uri, gps, recordedAtUtc: interior.recordedAt, durationS: CLIP_SECONDS, resolution: "1920x1080" });
      }
      processQueue().catch(() => undefined);
      router.replace("/upload-status");
    } catch (e) {
      setStep("submit_error");
      setError(e instanceof Error ? e.message : "Could not submit inspection");
    }
  }

  if (!camPerm?.granted || !micPerm?.granted) {
    return (
      <View style={styles.center}>
        <Text style={styles.dim}>Camera and microphone access are required.</Text>
        <Pressable style={styles.button} onPress={() => { requestCam(); requestMic(); }}>
          <Text style={styles.buttonText}>Grant access</Text>
        </Pressable>
      </View>
    );
  }

  const isRecordingStep = step === "exterior" || step === "interior";
  // Picture mode only while pre-checking (still photo); video mode for everything else.
  const cameraMode = step === "prechecking" || step === "precheck_fail" || step === "ready" ? "picture" : "video";

  return (
    <View style={styles.container}>
      <CameraView
        ref={cameraRef}
        style={styles.camera}
        mode={cameraMode}
        facing={facing}
        videoQuality="1080p"
        enableTorch={torch}
        onCameraReady={() => setCameraReady(true)}
      />

      {isRecordingStep && cameraReady ? <View style={styles.frameGuide} pointerEvents="none" /> : null}

      {(isRecordingStep && !cameraReady) ? (
        <View style={styles.centerOverlay}>
          <ActivityIndicator color={colors.accent} />
          <Text style={styles.prompt}>Aligning camera...</Text>
        </View>
      ) : null}

      {(isRecordingStep || step === "exterior_done") ? (
        <View style={styles.topControls}>
          <Pressable style={styles.ctrlBtn} onPress={() => setTorch((t) => !t)}>
            <Text style={styles.ctrlText}>{torch ? "Torch On" : "Torch Off"}{isNight() ? " · auto" : ""}</Text>
          </Pressable>
          {!recording ? (
            <Pressable style={styles.ctrlBtn} onPress={() => setFacing((f) => (f === "back" ? "front" : "back"))}>
              <Text style={styles.ctrlText}>Flip</Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}

      <View style={styles.overlay}>
        <Text style={styles.plate}>{plate}</Text>

        {step === "ready" && (
          <>
            {isReclean ? (
              <View style={styles.recleanBanner}>
                <Text style={styles.recleanTitle}>RE-CLEAN{isTargeted ? " · targeted" : ""}</Text>
                {zones ? <Text style={styles.recleanZones}>Re-film only: {zones}</Text> : null}
                <Text style={styles.prompt}>
                  {needExterior && needInterior
                    ? `We'll record the exterior and interior (${CLIP_SECONDS}s each).`
                    : needInterior
                      ? `We'll record only the interior (${CLIP_SECONDS}s) - no full walk-around needed.`
                      : `We'll record only the exterior (${CLIP_SECONDS}s).`}
                </Text>
              </View>
            ) : (
              <Text style={styles.prompt}>
                Point the camera at your car and tap Start. We'll check it's a vehicle, then record
                exterior and interior clips of {CLIP_SECONDS}s each.
              </Text>
            )}
            {error ? <Text style={styles.error}>{error}</Text> : null}
            <Pressable style={styles.button} onPress={beginFlow}>
              <Text style={styles.buttonText}>{isReclean ? "Start re-clean" : "Start inspection"}</Text>
            </Pressable>
          </>
        )}

        {step === "prechecking" && (
          <View style={styles.recRow}>
            <ActivityIndicator color={colors.accent} />
            <Text style={styles.prompt}>Checking it's a vehicle...</Text>
          </View>
        )}

        {step === "precheck_fail" && (
          <>
            <Text style={styles.error}>{error}</Text>
            <Pressable style={styles.button} onPress={runPrecheck}>
              <Text style={styles.buttonText}>Try again</Text>
            </Pressable>
            <Pressable style={styles.skip} onPress={() => { runningRef.current = false; setStep("ready"); }}>
              <Text style={styles.skipText}>Cancel</Text>
            </Pressable>
          </>
        )}

        {isRecordingStep && (
          <>
            <Text style={styles.stepLabel}>{step === "exterior" ? "EXTERIOR" : "INTERIOR"}</Text>
            <Text style={styles.prompt}>
              {step === "exterior" ? "Slowly walk around the vehicle." : "Pan across seats, floor, and dashboard."}
            </Text>
            <View style={styles.recRow}>
              {recording ? <View style={styles.recDot} /> : <ActivityIndicator color={colors.accent} />}
              <Text style={styles.timer}>{recording ? `Recording... ${remaining}s left` : "Preparing..."}</Text>
            </View>
          </>
        )}

        {step === "exterior_done" && (
          <>
            <Text style={styles.stepLabel}>EXTERIOR DONE ✓</Text>
            <Text style={styles.prompt}>Now record the interior to complete the inspection.</Text>
            {error ? <Text style={styles.error}>{error}</Text> : null}
            <Pressable style={styles.button} onPress={startInterior}>
              <Text style={styles.buttonText}>Start interior</Text>
            </Pressable>
          </>
        )}

        {step === "submitting" && (
          <View style={styles.recRow}>
            <ActivityIndicator color={colors.accent} />
            <Text style={styles.prompt}>Submitting inspection...</Text>
          </View>
        )}

        {step === "submit_error" && (
          <>
            <Text style={styles.error}>{error}</Text>
            <Text style={styles.prompt}>Your clips are saved. Tap to submit again.</Text>
            <Pressable style={styles.button} onPress={submit}>
              <Text style={styles.buttonText}>Retry submit</Text>
            </Pressable>
          </>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.bg },
  camera: { flex: 1 },
  overlay: { position: "absolute", bottom: 0, left: 0, right: 0, padding: 20, gap: 10 },
  centerOverlay: { position: "absolute", top: 0, bottom: 0, left: 0, right: 0, alignItems: "center", justifyContent: "center", gap: 12 },
  center: { flex: 1, backgroundColor: colors.bg, alignItems: "center", justifyContent: "center", padding: 24, gap: 16 },
  topControls: { position: "absolute", top: 16, right: 16, flexDirection: "row", gap: 8 },
  ctrlBtn: { backgroundColor: "rgba(0,0,0,0.5)", borderColor: colors.border, borderWidth: 1, borderRadius: 6, paddingVertical: 8, paddingHorizontal: 12 },
  ctrlText: { color: colors.text, fontFamily: colors.mono, fontSize: 12 },
  frameGuide: { position: "absolute", top: "18%", left: "6%", right: "6%", bottom: "26%", borderColor: "rgba(74,157,142,0.6)", borderWidth: 2, borderRadius: 10 },
  plateGuide: { position: "absolute", top: "38%", left: "12%", right: "12%", height: "22%", borderColor: "rgba(74,157,142,0.8)", borderWidth: 2, borderRadius: 8 },
  plate: { color: colors.text, fontFamily: colors.mono, fontSize: 16, letterSpacing: 1 },
  stepLabel: { color: colors.accent, fontFamily: colors.mono, fontWeight: "700" },
  prompt: { color: colors.text },
  mono: { fontFamily: colors.mono, letterSpacing: 1 },
  recRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  recDot: { width: 14, height: 14, borderRadius: 7, backgroundColor: colors.danger },
  timer: { color: colors.text, fontFamily: colors.mono, fontSize: 22 },
  error: { color: colors.danger },
  dim: { color: colors.textDim, textAlign: "center" },
  button: { backgroundColor: colors.accent, borderRadius: 6, padding: 16, alignItems: "center" },
  buttonText: { color: "#ffffff", fontWeight: "700" },
  skip: { padding: 12, alignItems: "center" },
  skipText: { color: colors.textDim },
  recleanBanner: { borderColor: colors.accent, borderWidth: 1, borderRadius: 8, padding: 12, gap: 6, backgroundColor: "rgba(74,157,142,0.12)" },
  recleanTitle: { color: colors.accent, fontFamily: colors.mono, fontWeight: "700", letterSpacing: 1, fontSize: 12 },
  recleanZones: { color: colors.text, fontFamily: colors.mono, fontSize: 13 },
});
