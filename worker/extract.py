"""Frame extraction: full-resolution JPEG frames plus smaller JPEG thumbnails, with GPS
and timestamp written into each image's metadata via exiftool.

Frames are extracted straight from the source video at full resolution and encoded as
high-quality JPEG (q~92). The scoring pipeline already re-encodes every frame to JPEG
before sending it to the model, so JPEG storage costs the model nothing while cutting
frame storage 5-10x versus lossless PNG; the quality is visually identical for a reviewer.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("blucheck.extract")


class ExtractionError(RuntimeError):
    pass


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ExtractionError(
            f"command failed ({proc.returncode}): {' '.join(cmd[:3])}... :: {proc.stderr.strip()[:500]}"
        )
    return proc


@dataclass
class ProbeResult:
    width: int
    height: int
    duration_s: float


def probe_video(path: str) -> ProbeResult:
    proc = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            path,
        ]
    )
    data = json.loads(proc.stdout)
    stream = (data.get("streams") or [{}])[0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    duration = float(data.get("format", {}).get("duration") or 0.0)
    if width == 0 or height == 0:
        raise ExtractionError("could not determine video dimensions")
    return ProbeResult(width=width, height=height, duration_s=duration)


def _gps_ref(value: float, positive: str, negative: str) -> tuple[float, str]:
    return (abs(value), positive if value >= 0 else negative)


@dataclass
class FrameArtifact:
    seq: int
    offset_ms: int
    absolute_ts_utc: datetime | None
    full_path: Path
    thumb_path: Path
    width: int
    height: int
    gps_lat: float | None
    gps_lon: float | None


def extract_capture(
    video_path: str,
    work_dir: str,
    *,
    recorded_at_utc: datetime | None,
    gps_lat: float | None,
    gps_lon: float | None,
    fps: int = 2,
    thumb_width: int = 480,
) -> list[FrameArtifact]:
    """Extract full-res JPEG frames and JPEG thumbnails, tag them, and return artifacts."""
    work = Path(work_dir)
    full_dir = work / "full"
    thumb_dir = work / "thumb"
    full_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)

    probe = probe_video(video_path)

    # Pass 1: full-resolution JPEG frames, no scaling. -q:v 2 is near-lossless (~q92) and
    # 5-10x smaller than PNG for photographic content, with no loss of cleanliness detail.
    _run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", video_path,
            "-vf", f"fps={fps}",
            "-vsync", "0",
            "-q:v", "2",
            str(full_dir / "frame_%06d.jpg"),
        ]
    )

    # Pass 2: JPEG thumbnails at the configured width, same fps so counts align.
    _run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", video_path,
            "-vf", f"fps={fps},scale={thumb_width}:-2",
            "-vsync", "0",
            "-q:v", "3",
            str(thumb_dir / "frame_%06d.jpg"),
        ]
    )

    full_frames = sorted(full_dir.glob("frame_*.jpg"))
    thumb_frames = sorted(thumb_dir.glob("frame_*.jpg"))
    if not full_frames:
        raise ExtractionError("ffmpeg produced no frames")
    if len(full_frames) != len(thumb_frames):
        raise ExtractionError(
            f"frame/thumb count mismatch: {len(full_frames)} vs {len(thumb_frames)}"
        )

    artifacts: list[FrameArtifact] = []
    for idx, (full_path, thumb_path) in enumerate(zip(full_frames, thumb_frames)):
        seq = idx + 1
        offset_ms = round((idx / fps) * 1000)
        absolute_ts = None
        if recorded_at_utc is not None:
            absolute_ts = recorded_at_utc + timedelta(milliseconds=offset_ms)

        _write_metadata(full_path, absolute_ts, gps_lat, gps_lon)
        _write_metadata(thumb_path, absolute_ts, gps_lat, gps_lon)

        artifacts.append(
            FrameArtifact(
                seq=seq,
                offset_ms=offset_ms,
                absolute_ts_utc=absolute_ts,
                full_path=full_path,
                thumb_path=thumb_path,
                width=probe.width,
                height=probe.height,
                gps_lat=gps_lat,
                gps_lon=gps_lon,
            )
        )

    logger.info("extracted %d frames from %s", len(artifacts), Path(video_path).name)
    return artifacts


def _write_metadata(
    image_path: Path,
    ts_utc: datetime | None,
    gps_lat: float | None,
    gps_lon: float | None,
) -> None:
    """Embed DateTimeOriginal and GPS into the image (works for both PNG and JPEG)."""
    args = ["exiftool", "-overwrite_original", "-q"]

    if ts_utc is not None:
        stamp = ts_utc.astimezone(timezone.utc).strftime("%Y:%m:%d %H:%M:%S")
        args += [
            f"-DateTimeOriginal={stamp}",
            f"-CreateDate={stamp}",
            "-OffsetTimeOriginal=+00:00",
        ]

    if gps_lat is not None and gps_lon is not None:
        lat_abs, lat_ref = _gps_ref(gps_lat, "N", "S")
        lon_abs, lon_ref = _gps_ref(gps_lon, "E", "W")
        args += [
            f"-GPSLatitude={lat_abs}",
            f"-GPSLatitudeRef={lat_ref}",
            f"-GPSLongitude={lon_abs}",
            f"-GPSLongitudeRef={lon_ref}",
        ]

    if len(args) == 3:  # nothing to write
        return

    args.append(str(image_path))
    # Metadata is a best-effort provenance stamp, not the frame itself. A failure here
    # (exiftool missing, unwritable tag) must NOT abort an otherwise-successful extraction
    # and send the capture to the DLQ — the frames are already on disk and usable.
    try:
        _run(args)
    except ExtractionError as err:
        logger.warning("metadata embed failed for %s: %s (continuing)", image_path.name, err)
