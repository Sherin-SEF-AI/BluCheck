import { API_BASE_URL } from "./config";
import { getToken, clearSession } from "./auth";

export type Vehicle = {
  id: string;
  registration_plate: string;
  model: string | null;
  active: boolean;
};

export type Gps = { lat: number; lon: number; accuracy_m: number | null };

export type InspectionSummary = {
  id: string;
  status: string;
  vehicle_plate: string;
  driver_name: string;
  captured_at_utc: string | null;
  created_at: string;
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string>),
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  if (res.status === 401) {
    await clearSession();
    throw new ApiError(401, "Session expired");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      const d = body.detail;
      if (typeof d === "string") {
        detail = d;
      } else if (Array.isArray(d)) {
        // FastAPI validation errors are an array of {msg, loc, ...} objects.
        detail = d.map((x) => x?.msg ?? JSON.stringify(x)).join("; ");
      } else if (d) {
        detail = JSON.stringify(d);
      }
    } catch {
      // keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export type AuthResult = { access_token: string; role: string; name: string; car_number: string | null };

// Driver login step 1: scan the number plate; the server OCRs it and returns the matching car.
export type PlateResolve = { car_number: string; name: string };
export async function plateResolve(imageB64: string): Promise<PlateResolve> {
  return request("/auth/plate-resolve", { method: "POST", body: JSON.stringify({ image_b64: imageB64 }) });
}

// Driver login step 2: confirm the 4-digit PIN for the (scanned or typed) car number.
export async function pinLogin(carNumber: string, pin: string): Promise<AuthResult> {
  return request("/auth/pin-login", { method: "POST", body: JSON.stringify({ car_number: carNumber, pin }) });
}

export async function register(name: string, carNumber: string, pin: string): Promise<AuthResult> {
  return request("/auth/register", { method: "POST", body: JSON.stringify({ name, car_number: carNumber, pin }) });
}

export async function savePushToken(pushToken: string): Promise<void> {
  await request("/auth/push-token", { method: "POST", body: JSON.stringify({ push_token: pushToken }) });
}

export type PlateVerify = { read_plate: string | null; matched: boolean; expected: string | null; candidates: string[] };
export async function verifyPlate(imageB64: string): Promise<PlateVerify> {
  return request("/inspections/verify-plate", { method: "POST", body: JSON.stringify({ image_b64: imageB64 }) });
}

// One-shot pre-check the app runs automatically before recording: is this a vehicle (so
// non-car footage is rejected on the phone) + read the plate (no separate scan tap).
export type Precheck = { is_vehicle: boolean; vehicle_confidence: number | null; labels: string[]; read_plate: string | null; matched: boolean; expected: string | null };
export async function precheckCapture(imageB64: string): Promise<Precheck> {
  return request("/inspections/precheck", { method: "POST", body: JSON.stringify({ image_b64: imageB64 }) });
}

export async function listVehicles(): Promise<Vehicle[]> {
  return request("/vehicles");
}

export async function createInspection(input: {
  vehicle_id: string;
  gps: Gps;
  captured_at_utc: string;
  captured_at_local: string;
  device_meta: Record<string, unknown>;
  ocr_plate?: string | null;
  ocr_matched?: boolean | null;
  reinspection_of?: string | null;
}): Promise<{ inspection_id: string; status: string }> {
  return request("/inspections", { method: "POST", body: JSON.stringify(input) });
}

export type UploadUrlResponse = {
  key: string;
  upload_id: string;
  part_size: number;
  parts: { part_number: number; url: string }[];
};

export async function getUploadUrl(
  inspectionId: string,
  kind: "exterior" | "interior",
  partCount: number,
  uploadId?: string
): Promise<UploadUrlResponse> {
  return request(`/inspections/${inspectionId}/captures/${kind}/upload-url`, {
    method: "POST",
    body: JSON.stringify({ content_type: "video/mp4", part_count: partCount, upload_id: uploadId ?? null }),
  });
}

export async function completeUpload(
  inspectionId: string,
  kind: "exterior" | "interior",
  input: {
    upload_id: string;
    parts: { part_number: number; etag: string }[];
    duration_s: number | null;
    recorded_at_utc: string | null;
    gps: Gps | null;
    resolution: string | null;
  }
): Promise<{ inspection_status: string }> {
  return request(`/inspections/${inspectionId}/captures/${kind}/complete`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function listMyInspections(): Promise<{ items: InspectionSummary[] }> {
  return request("/inspections?page=1&page_size=50");
}

export type ZoneIssueLabel = { zone_key: string; issue_key: string };
export type FrameThumb = { id: string; seq: number; thumb_url: string; selected: boolean };
export type CaptureThumbs = { kind: string; frames: FrameThumb[] };
export type InspectionDetail = {
  id: string;
  status: string;
  vehicle_id: string;
  vehicle_plate: string;
  reject_reason: string | null;
  reject_labels: ZoneIssueLabel[];
  captures?: CaptureThumbs[];
};

export async function getMyInspection(id: string): Promise<InspectionDetail> {
  return request(`/inspections/${id}`);
}

// Driver disputes an automated rejection -> re-opens for a human reviewer.
export async function appealInspection(id: string): Promise<{ ok: boolean; status: string }> {
  return request(`/inspections/${id}/appeal`, { method: "POST" });
}
