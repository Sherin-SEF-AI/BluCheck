/**
 * Resilient upload queue for capture videos.
 *
 * Uploads a video as an S3 multipart upload using presigned part URLs. Progress is
 * persisted to the device filesystem so an interrupted upload resumes after an app
 * restart, and each part is retried with exponential backoff. A recorded clip is never
 * discarded until the server has confirmed the completed upload. Live byte progress is
 * emitted to a listener for the UI (not persisted, to avoid disk thrash).
 */

import * as FileSystem from "expo-file-system";
import { ApiError, completeUpload, getUploadUrl, type Gps } from "./api";
import { MAX_PART_RETRIES, PART_SIZE } from "./config";

// "dead" = permanently unrecoverable (e.g. the inspection belongs to another driver,
// or no longer exists); such items are dropped from the queue and never retried.
export type QueueItemStatus = "pending" | "uploading" | "completed" | "error" | "dead";

// A server response that will never succeed on retry: wrong owner, missing, or gone.
function isPermanentFailure(err: unknown): boolean {
  return err instanceof ApiError && [400, 403, 404, 410].includes(err.status);
}

export type QueueItem = {
  inspectionId: string;
  kind: "exterior" | "interior";
  videoUri: string;
  gps: Gps | null;
  recordedAtUtc: string | null;
  durationS: number | null;
  resolution: string | null;
  uploadId: string | null;
  fileSize: number;
  partSize: number;
  totalParts: number;
  completedParts: Record<number, string>; // partNumber -> ETag
  status: QueueItemStatus;
  lastError: string | null;
};

export type UploadProgress = {
  inspectionId: string;
  kind: "exterior" | "interior";
  fraction: number; // 0..1 across the whole file
  bytesSent: number;
  fileSize: number;
  status: QueueItemStatus;
};

const QUEUE_PATH = `${FileSystem.documentDirectory}blucheck-upload-queue.json`;
// Inspections whose every captured clip has finished uploading. Tracked separately from the queue
// because completed queue items are dropped (and their clips deleted) as soon as they upload — so
// the Uploads screen can follow server-side analysis without racing that cleanup.
const ANALYZE_PATH = `${FileSystem.documentDirectory}blucheck-analyze.json`;

// ---- Live progress emitter (UI subscribes; not persisted) ----
let progressListener: ((p: UploadProgress) => void) | null = null;
export function setProgressListener(fn: ((p: UploadProgress) => void) | null): void {
  progressListener = fn;
}
function emit(p: UploadProgress): void {
  progressListener?.(p);
}

async function readQueue(): Promise<QueueItem[]> {
  const info = await FileSystem.getInfoAsync(QUEUE_PATH);
  if (!info.exists) return [];
  try {
    return JSON.parse(await FileSystem.readAsStringAsync(QUEUE_PATH)) as QueueItem[];
  } catch {
    return [];
  }
}

// Atomic write: write to a temp file then move over the target, so a crash mid-write can never
// leave a truncated/corrupt queue file (the old file stays intact until the move succeeds).
async function writeQueue(items: QueueItem[]): Promise<void> {
  const tmp = `${QUEUE_PATH}.tmp`;
  await FileSystem.writeAsStringAsync(tmp, JSON.stringify(items));
  await FileSystem.moveAsync({ from: tmp, to: QUEUE_PATH });
}

// Serialize all read-modify-write mutations of the queue file through one async chain, so
// concurrent callers (upload screen, app-start kick, retry) can't clobber each other's writes.
let queueChain: Promise<unknown> = Promise.resolve();
function withQueueLock<T>(fn: () => Promise<T>): Promise<T> {
  const run = queueChain.then(fn, fn);
  queueChain = run.then(() => undefined, () => undefined);
  return run as Promise<T>;
}

// Delete a finished/dead item's source clip to reclaim device storage (H9). Best-effort.
async function deleteClip(uri: string): Promise<void> {
  try { await FileSystem.deleteAsync(uri, { idempotent: true }); } catch { /* ignore */ }
}

export async function getQueue(): Promise<QueueItem[]> {
  return readQueue();
}

async function readAnalyze(): Promise<string[]> {
  const info = await FileSystem.getInfoAsync(ANALYZE_PATH);
  if (!info.exists) return [];
  try {
    return JSON.parse(await FileSystem.readAsStringAsync(ANALYZE_PATH)) as string[];
  } catch {
    return [];
  }
}

async function writeAnalyze(ids: string[]): Promise<void> {
  // Keep the list bounded so it can't grow without limit across many inspections.
  const tmp = `${ANALYZE_PATH}.tmp`;
  await FileSystem.writeAsStringAsync(tmp, JSON.stringify(ids.slice(-20)));
  await FileSystem.moveAsync({ from: tmp, to: ANALYZE_PATH });
}

// Inspection ids whose uploads are all delivered — the UI follows their server-side analysis.
export async function getAnalyzable(): Promise<string[]> {
  return readAnalyze();
}

// Drop an inspection from the tracker once its analysis has reached a terminal result.
export async function clearAnalyzable(inspectionId: string): Promise<void> {
  await withQueueLock(async () => {
    const ids = await readAnalyze();
    const next = ids.filter((x) => x !== inspectionId);
    if (next.length !== ids.length) await writeAnalyze(next);
  });
}

export async function pendingCount(): Promise<number> {
  return (await readQueue()).filter((i) => i.status !== "completed" && i.status !== "dead").length;
}

export async function enqueueCapture(input: {
  inspectionId: string;
  kind: "exterior" | "interior";
  videoUri: string;
  gps: Gps | null;
  recordedAtUtc: string | null;
  durationS: number | null;
  resolution: string | null;
}): Promise<void> {
  const info = await FileSystem.getInfoAsync(input.videoUri, { size: true });
  if (!info.exists) throw new Error("Recorded video is missing");
  const fileSize = info.size ?? 0;
  const totalParts = Math.max(1, Math.ceil(fileSize / PART_SIZE));

  const item: QueueItem = {
    ...input,
    uploadId: null,
    fileSize,
    partSize: PART_SIZE,
    totalParts,
    completedParts: {},
    status: "pending",
    lastError: null,
  };

  await withQueueLock(async () => {
    const items = await readQueue();
    const filtered = items.filter(
      (i) => !(i.inspectionId === item.inspectionId && i.kind === item.kind)
    );
    filtered.push(item);
    await writeQueue(filtered);
  });
}

async function persistItem(item: QueueItem): Promise<void> {
  await withQueueLock(async () => {
    const items = await readQueue();
    const idx = items.findIndex(
      (i) => i.inspectionId === item.inspectionId && i.kind === item.kind
    );
    if (idx >= 0) items[idx] = item;
    else items.push(item);
    await writeQueue(items);
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

async function uploadPart(
  tmpName: string,
  url: string,
  videoUri: string,
  position: number,
  length: number,
  onPartFraction: (f: number) => void
): Promise<string> {
  // Unique per inspection+kind+part so concurrent/interleaved uploads can never collide on the
  // same cache file (H4).
  const tmp = `${FileSystem.cacheDirectory}${tmpName}`;
  const sliceB64 = await FileSystem.readAsStringAsync(videoUri, {
    encoding: FileSystem.EncodingType.Base64,
    position,
    length,
  });
  await FileSystem.writeAsStringAsync(tmp, sliceB64, {
    encoding: FileSystem.EncodingType.Base64,
  });

  try {
    let attempt = 0;
    // eslint-disable-next-line no-constant-condition
    while (true) {
      try {
        const task = FileSystem.createUploadTask(
          url,
          tmp,
          { httpMethod: "PUT", uploadType: FileSystem.FileSystemUploadType.BINARY_CONTENT },
          (data) => {
            if (data.totalBytesExpectedToSend > 0) {
              onPartFraction(data.totalBytesSent / data.totalBytesExpectedToSend);
            }
          }
        );
        const res = await task.uploadAsync();
        if (!res || res.status < 200 || res.status >= 300) {
          throw new Error(`part upload HTTP ${res?.status ?? "?"}`);
        }
        const etag = res.headers.ETag ?? res.headers.etag;
        if (!etag) throw new Error("part upload returned no ETag");
        onPartFraction(1);
        return etag.replace(/"/g, "");
      } catch (err) {
        attempt += 1;
        if (attempt >= MAX_PART_RETRIES) throw err;
        await sleep(Math.min(30000, 1000 * 2 ** attempt));
      }
    }
  } finally {
    await FileSystem.deleteAsync(tmp, { idempotent: true });
  }
}

export async function processItem(item: QueueItem): Promise<QueueItem> {
  item.status = "uploading";
  item.lastError = null;
  await persistItem(item);

  const reportOverall = (completed: number, partFraction: number) => {
    const fraction = Math.min(1, (completed + partFraction) / item.totalParts);
    emit({
      inspectionId: item.inspectionId,
      kind: item.kind,
      fraction,
      bytesSent: Math.round(fraction * item.fileSize),
      fileSize: item.fileSize,
      status: "uploading",
    });
  };

  try {
    const presigned = await getUploadUrl(
      item.inspectionId,
      item.kind,
      item.totalParts,
      item.uploadId ?? undefined
    );
    item.uploadId = presigned.upload_id;
    await persistItem(item);

    const urlByPart = new Map(presigned.parts.map((p) => [p.part_number, p.url]));
    let completed = Object.keys(item.completedParts).length;
    reportOverall(completed, 0);

    for (let partNumber = 1; partNumber <= item.totalParts; partNumber += 1) {
      if (item.completedParts[partNumber]) continue;
      const position = (partNumber - 1) * item.partSize;
      const length = Math.min(item.partSize, item.fileSize - position);
      const url = urlByPart.get(partNumber);
      if (!url) throw new Error(`missing presigned URL for part ${partNumber}`);

      const tmpName = `part-${item.inspectionId}-${item.kind}-${partNumber}.bin`;
      const etag = await uploadPart(tmpName, url, item.videoUri, position, length, (f) =>
        reportOverall(completed, f)
      );
      item.completedParts[partNumber] = etag;
      completed += 1;
      await persistItem(item);
      reportOverall(completed, 0);
    }

    await completeUpload(item.inspectionId, item.kind, {
      upload_id: item.uploadId,
      parts: Object.entries(item.completedParts).map(([n, etag]) => ({
        part_number: Number(n),
        etag,
      })),
      duration_s: item.durationS,
      recorded_at_utc: item.recordedAtUtc,
      gps: item.gps,
      resolution: item.resolution,
    });

    item.status = "completed";
    await persistItem(item);
    emit({
      inspectionId: item.inspectionId,
      kind: item.kind,
      fraction: 1,
      bytesSent: item.fileSize,
      fileSize: item.fileSize,
      status: "completed",
    });
  } catch (err) {
    // Permanent failures (foreign/missing inspection) are marked dead so the queue
    // stops retrying them; transient failures stay retryable.
    const permanent = isPermanentFailure(err);
    item.status = permanent ? "dead" : "error";
    item.lastError = err instanceof Error ? err.message : String(err);
    await persistItem(item);
    emit({
      inspectionId: item.inspectionId,
      kind: item.kind,
      fraction: Object.keys(item.completedParts).length / item.totalParts,
      bytesSent: 0,
      fileSize: item.fileSize,
      status: item.status,
    });
  }
  return item;
}

// In-flight guard: only one processQueue may run at a time. A second call while running is a
// no-op (the running pass already picks up every pending item), preventing double uploads and
// racing writes.
let processing = false;
export async function processQueue(): Promise<void> {
  if (processing) return;
  processing = true;
  try {
    const items = await readQueue();
    for (const item of items) {
      if (item.status === "completed" || item.status === "dead") continue;
      await processItem(item);
    }
    // Drop finished and permanently-failed items, deleting their source clips first (H9).
    await withQueueLock(async () => {
      const all = await readQueue();
      const done = all.filter((i) => i.status === "completed" || i.status === "dead");
      const remaining = all.filter((i) => i.status !== "completed" && i.status !== "dead");

      // Record inspections whose EVERY queued clip uploaded successfully, so the UI can follow
      // their analysis after these completed items (and their clips) are removed below.
      const byInsp = new Map<string, QueueItem[]>();
      for (const i of all) {
        const list = byInsp.get(i.inspectionId) ?? [];
        list.push(i);
        byInsp.set(i.inspectionId, list);
      }
      const ready: string[] = [];
      for (const [id, its] of byInsp) {
        if (its.length > 0 && its.every((i) => i.status === "completed")) ready.push(id);
      }
      if (ready.length) {
        const prev = await readAnalyze();
        await writeAnalyze([...new Set([...prev, ...ready])]);
      }

      await writeQueue(remaining);
      for (const i of done) await deleteClip(i.videoUri);
    });
  } finally {
    processing = false;
  }
}

// Cancel a stuck/pending upload: remove it from the queue and delete its clip. Used by the UI.
export async function cancelItem(inspectionId: string, kind: "exterior" | "interior"): Promise<void> {
  await withQueueLock(async () => {
    const all = await readQueue();
    const target = all.find((i) => i.inspectionId === inspectionId && i.kind === kind);
    await writeQueue(all.filter((i) => !(i.inspectionId === inspectionId && i.kind === kind)));
    if (target) await deleteClip(target.videoUri);
  });
}
