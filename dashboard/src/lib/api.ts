// Dashboard API client. Talks to the BluCheck API. The base URL is baked at build time
// from NEXT_PUBLIC_API_BASE_URL (set by scripts/deploy-dashboard.sh).

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const TOKEN_KEY = "blucheck.jwt";
const ROLE_KEY = "blucheck.role";

export function saveSession(token: string, role: string): void {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(ROLE_KEY, role);
}
export function getToken(): string | null {
  return typeof window === "undefined" ? null : localStorage.getItem(TOKEN_KEY);
}
export function clearSession(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(ROLE_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string>),
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });
  if (res.status === 401) {
    clearSession();
    if (typeof window !== "undefined") window.location.href = "/login/";
    throw new ApiError(401, "Session expired");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* keep */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ----- Types -----
export type Login = { access_token: string; role: string };

export type InspectionListItem = {
  id: string;
  status: string;
  vehicle_plate: string;
  driver_name: string;
  gps_lat: number | null;
  gps_lon: number | null;
  captured_at_utc: string | null;
  created_at: string;
  decision_source: "agent" | "human" | null;
  overall_score: number | null;
  integrity_risk: "low" | "medium" | "high" | null;
};
export type InspectionList = {
  items: InspectionListItem[];
  total: number;
  page: number;
  page_size: number;
};

export type FrameOut = {
  id: string;
  seq: number;
  offset_ms: number;
  absolute_ts_utc: string | null;
  gps_lat: number | null;
  gps_lon: number | null;
  thumb_url: string;
  full_url_endpoint: string;
  width: number | null;
  height: number | null;
  selected: boolean;
  blur_score: number | null;
};
export type CaptureDetail = {
  id: string;
  kind: string;
  status: string;
  duration_s: number | null;
  recorded_at_utc: string | null;
  gps_lat: number | null;
  gps_lon: number | null;
  resolution: string | null;
  frame_count: number;
  frames: FrameOut[];
};
export type InspectionDetail = {
  id: string;
  status: string;
  vehicle_plate: string;
  driver_name: string;
  gps_lat: number | null;
  gps_lon: number | null;
  gps_accuracy_m: number | null;
  captured_at_utc: string | null;
  captured_at_local: string | null;
  device_meta: Record<string, unknown> | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  reject_reason: string | null;
  reject_labels: ZoneIssueLabel[];
  integrity: { risk: "low" | "medium" | "high"; reasons: string[]; signals?: Record<string, unknown> } | null;
  appeal_recommendation: { ruling: string; reason: string; confidence: number } | null;
  scoring: ScoringDetail | null;
  decision_source: "agent" | "human" | null;
  ocr_plate: string | null;
  ocr_matched: boolean | null;
  reinspection_of: string | null;
  reinspection_of_reason: string | null;
  created_at: string;
  captures: CaptureDetail[];
};

export type Metrics = {
  counts_by_status: Record<string, number>;
  average_review_seconds: number | null;
  rejects_by_vehicle: { vehicle_plate: string; rejects: number }[];
};

// ----- Calls -----
export function login(email: string, password: string): Promise<Login> {
  return request("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
}

export function listInspections(params: Record<string, string>): Promise<InspectionList> {
  const qs = new URLSearchParams(params).toString();
  return request(`/inspections?${qs}`);
}

export function getInspection(id: string): Promise<InspectionDetail> {
  return request(`/inspections/${id}`);
}

export function getFrameUrl(inspectionId: string, frameId: string): Promise<{ url: string; expires_in: number }> {
  return request(`/inspections/${inspectionId}/frames/${frameId}/url`);
}

export function reprocessInspection(id: string): Promise<{ ok: boolean; status: string; captures_reset: number }> {
  return request(`/inspections/${id}/reprocess`, { method: "POST" });
}

export function rerunAnalysis(id: string): Promise<{ ok: boolean; status: string; scoring_cleared: number }> {
  return request(`/inspections/${id}/rerun-analysis`, { method: "POST" });
}

// Fetches the VLM-annotated frame (issues boxed) as a blob and returns an object URL.
export async function getAnnotatedFrame(inspectionId: string, frameId: string): Promise<string> {
  const token = getToken();
  const res = await fetch(`${API_BASE_URL}/inspections/${inspectionId}/frames/${frameId}/annotated`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* keep */ }
    throw new ApiError(res.status, detail);
  }
  return URL.createObjectURL(await res.blob());
}

export type ZoneIssueLabel = { zone_key: string; issue_key: string };

export function review(
  id: string,
  action: "approve" | "reject",
  opts: { reason?: string; labels?: ZoneIssueLabel[]; viewedFrameIds?: string[]; scoringResultId?: string } = {}
): Promise<unknown> {
  return request(`/inspections/${id}/review`, {
    method: "POST",
    body: JSON.stringify({
      action,
      reason: opts.reason ?? null,
      labels: opts.labels ?? [],
      viewed_frame_ids: opts.viewedFrameIds ?? [],
      scoring_result_id: opts.scoringResultId ?? null,
    }),
  });
}

// ----- API keys (developer integrations) -----
export type ApiKey = { id: string; name: string; key_prefix: string; active: boolean; created_at: string; last_used_at: string | null };
export type ApiKeyCreated = { id: string; name: string; key: string; key_prefix: string; created_at: string };
export function listApiKeys(): Promise<{ keys: ApiKey[] }> { return request("/apikeys"); }
export function createApiKey(name: string): Promise<ApiKeyCreated> {
  return request("/apikeys", { method: "POST", body: JSON.stringify({ name }) });
}
export function revokeApiKey(id: string): Promise<{ ok: boolean }> {
  return request(`/apikeys/${id}`, { method: "DELETE" });
}

// ----- Agentic admin assistant -----
export type AssistantMsg = { role: "user" | "assistant"; content: string };
export type PendingAction = { tool: string; args: Record<string, unknown>; title: string; detail: string };
export type AssistantReply = { answer: string; pending_actions: PendingAction[] };
export type AssistantContext = { page?: string; inspection_id?: string };
export function askAssistant(messages: AssistantMsg[], context?: AssistantContext): Promise<AssistantReply> {
  return request("/assistant/ask", { method: "POST", body: JSON.stringify({ messages, context: context ?? null }) });
}
export function executeAssistantAction(tool: string, args: Record<string, unknown>): Promise<{ ok: boolean; message: string }> {
  return request("/assistant/execute", { method: "POST", body: JSON.stringify({ tool, args }) });
}

// ----- Overdue cadence enforcement -----
export type OverdueVehicle = {
  driver_id: string; plate: string; name: string;
  last_approved_at: string | null; hours_overdue: number | null; never: boolean; severity: "due" | "critical";
};
export type OverdueList = { cadence_hours: number; count: number; items: OverdueVehicle[] };
export function getOverdue(): Promise<OverdueList> { return request("/metrics/overdue"); }
export function getCadence(): Promise<{ cadence_hours: number }> { return request("/metrics/cadence"); }
export function setCadence(cadence_hours: number): Promise<{ cadence_hours: number }> {
  return request("/metrics/cadence", { method: "POST", body: JSON.stringify({ cadence_hours }) });
}
export function runOverdue(): Promise<{ overdue: number; reminded: number; escalated: number; cadence_hours: number }> {
  return request("/metrics/run-overdue", { method: "POST" });
}

// ----- Observability: model health, costs, weekly digest -----
export type ModelHealth = {
  vision_ok: boolean; last_incident_at: string | null; last_incident_model: string | null;
  last_incident_message: string | null; last_incident_source: string | null; last_success_at: string | null;
};
export function getModelHealth(): Promise<ModelHealth> { return request("/model/health"); }

export type CostEstimate = {
  period: string; inference_calls: number; images_sent: number; inference_usd: number;
  storage_gb: number; storage_usd: number; aws_baseline_usd: number; total_est_usd: number; assumptions: string[];
};
export function getCosts(): Promise<CostEstimate> { return request("/metrics/costs"); }

export type Digest = { text: string; generated_at: string | null; stale: boolean };
export function getDigest(): Promise<Digest> { return request("/metrics/digest"); }
export function generateDigest(): Promise<Digest> { return request("/metrics/digest/generate", { method: "POST" }); }

// ----- Self-tuning policy agent -----
export type TuningEvidence = {
  days: number; overrides: number; too_strict: number; too_lenient: number;
  strict_zones: Record<string, number>; lenient_zones: Record<string, number>;
};
export type TuningSuggestion = {
  no_change: boolean; confidence: number; summary: string;
  scoring_config: Record<string, unknown> | null;
  thresholds: { overall?: { auto_approve?: number; auto_reject?: number } } | null;
  evidence: TuningEvidence;
};
export function getTuningSuggestion(days = 30): Promise<TuningSuggestion> {
  return request(`/model/tuning-suggestion?days=${days}`);
}

export type RephraseResult = { reason: string; labels: ZoneIssueLabel[] };
export function rephraseReview(text: string, context?: unknown[]): Promise<RephraseResult> {
  return request(`/inspections/review-rephrase`, {
    method: "POST",
    body: JSON.stringify({ text, context: context ?? null }),
  });
}

export type TaxonomyItem = { key: string; label: string };
export function getZones(): Promise<TaxonomyItem[]> {
  return request("/taxonomy/zones");
}
export function getIssues(): Promise<TaxonomyItem[]> {
  return request("/taxonomy/issues");
}

export function getMetrics(): Promise<Metrics> {
  return request("/metrics/summary");
}

export type Trends = {
  reviews_by_day: { day: string; approved: number; rejected: number }[];
  per_driver: { driver: string; total: number; approved: number; rejected: number; approval_rate: number | null }[];
  average_review_seconds: number | null;
};
export function getTrends(): Promise<Trends> {
  return request("/metrics/trends");
}

// ----- Daily compliance -----
export type ComplianceDriver = {
  driver_id: string; name: string; car_number: string | null;
  inspected: boolean; last_inspection_at: string | null; last_status: string | null;
};
export type Compliance = {
  date: string; total_drivers: number; inspected_count: number; missing_count: number;
  rate: number | null; drivers: ComplianceDriver[];
};
export function getCompliance(date?: string): Promise<Compliance> {
  return request(`/metrics/compliance${date ? `?date=${date}` : ""}`);
}

// ----- Per-vehicle trends -----
export type VehicleTrend = {
  vehicle_id: string; plate: string; model: string | null; active: boolean;
  total: number; approved: number; rejected: number; pending: number;
  avg_score: number | null; last_score: number | null; last_status: string | null;
  last_decided_by: "agent" | "human" | null; last_inspected_at: string | null;
};
export function getVehicleTrends(): Promise<{ vehicles: VehicleTrend[] }> {
  return request("/metrics/vehicles");
}

// ----- Vehicle admin -----
export type VehicleAdmin = { id: string; registration_plate: string; model: string | null; active: boolean };
export function listVehiclesAdmin(): Promise<VehicleAdmin[]> {
  return request("/vehicles?include_inactive=true");
}
export function createVehicle(body: { registration_plate: string; model?: string | null; active?: boolean }): Promise<VehicleAdmin> {
  return request("/vehicles", { method: "POST", body: JSON.stringify(body) });
}
export function updateVehicle(id: string, body: Partial<{ registration_plate: string; model: string | null; active: boolean }>): Promise<VehicleAdmin> {
  return request(`/vehicles/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

// ----- User admin -----
export type UserAdmin = { id: string; name: string; email: string; role: string; active: boolean; created_at: string };
export function listUsers(role?: string): Promise<UserAdmin[]> {
  return request(`/admin/users${role ? `?role=${role}` : ""}`);
}
export function createUser(body: { name: string; email: string; password: string; role: "driver" | "admin" }): Promise<UserAdmin> {
  return request("/admin/users", { method: "POST", body: JSON.stringify(body) });
}
export function updateUser(id: string, body: Partial<{ name: string; active: boolean; password: string }>): Promise<UserAdmin> {
  return request(`/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

// ----- Audit -----
// ----- Model management -----
export type ModelVersion = { id: string; name: string; vlm_model: string | null; prompt_version: string | null; thresholds: Record<string, unknown> | null; mode: string; active: boolean; created_at: string };
export type ModelPerformance = {
  mode: string;
  thresholds: { overall?: { auto_approve?: number; auto_reject?: number } } | null;
  model_name: string | null;
  total_scored: number;
  total_with_human: number;
  agreement_rate: number | null;
  per_zone_agreement: { zone_key: string; agreement: number; n: number }[];
  confusion: { tp: number; tn: number; fp: number; fn: number };
  avg_confidence_agree: number | null;
  avg_confidence_disagree: number | null;
  agreement_by_day: { day: string; agreement: number; n: number }[];
  avg_latency_ms: number | null;
  overrides?: {
    count: number; approve_overrides: number; reject_overrides: number;
    avg_delta: number | null; reviewed: number; supervisor_right: number; band_right: number;
  } | null;
};
export function getModelVersion(): Promise<ModelVersion> { return request("/model/version"); }
export function getPerformance(): Promise<ModelPerformance> { return request("/model/performance"); }
export function setMode(mode: "shadow" | "assist" | "auto" | "disabled"): Promise<ModelVersion> {
  return request("/model/mode", { method: "POST", body: JSON.stringify({ mode }) });
}
export function setThresholds(thresholds: Record<string, unknown>): Promise<ModelVersion> {
  return request("/model/thresholds", { method: "POST", body: JSON.stringify({ thresholds }) });
}

// ----- Scoring config (tunable math) -----
export type ScoringConfig = {
  effective: Record<string, unknown>;
  stored: Record<string, unknown> | null;
  defaults: Record<string, unknown>;
};
export function getScoringConfig(): Promise<ScoringConfig> { return request("/model/scoring-config"); }
export function patchScoringConfig(scoring_config: Record<string, unknown>): Promise<ScoringConfig> {
  return request("/model/scoring-config", { method: "PATCH", body: JSON.stringify({ scoring_config }) });
}

// ----- Agentic SOP generator -----
export type SopProposal = {
  scoring_config: Record<string, unknown>;
  thresholds: { overall?: { auto_approve?: number; auto_reject?: number } };
  summary: string;
  priorities: string[];
};
export function generateSop(sop: string): Promise<SopProposal> {
  return request("/model/sop/generate", { method: "POST", body: JSON.stringify({ sop }) });
}
export function applySop(sop: string, scoring_config: Record<string, unknown>, thresholds: Record<string, unknown>): Promise<ScoringConfig> {
  return request("/model/sop/apply", { method: "POST", body: JSON.stringify({ sop, scoring_config, thresholds }) });
}

// ----- Saved policy library (edit / delete / activate + recommended templates) -----
export type Policy = {
  id: string;
  name: string;
  sop: string;
  scoring_config: Record<string, unknown>;
  thresholds: { overall?: { auto_approve?: number; auto_reject?: number } };
  summary: string;
  active: boolean;
  created_at: string | null;
  updated_at: string | null;
};
export type RecommendedSop = { title: string; sop: string };
export type PolicyList = { policies: Policy[]; active_id: string | null; recommended: RecommendedSop[] };
export type PolicyMutate = { policies: Policy[]; active_id: string | null };

export function listPolicies(): Promise<PolicyList> {
  return request("/model/policies");
}
export function savePolicy(p: { name: string; sop: string; scoring_config: Record<string, unknown>; thresholds: Record<string, unknown>; summary?: string; activate?: boolean }): Promise<PolicyMutate> {
  return request("/model/policies", { method: "POST", body: JSON.stringify(p) });
}
export function updatePolicy(id: string, patch: { name?: string; sop?: string; scoring_config?: Record<string, unknown>; thresholds?: Record<string, unknown>; summary?: string }): Promise<PolicyMutate> {
  return request(`/model/policies/${id}`, { method: "PUT", body: JSON.stringify(patch) });
}
export function deletePolicy(id: string): Promise<PolicyMutate> {
  return request(`/model/policies/${id}`, { method: "DELETE" });
}
export function activatePolicy(id: string): Promise<PolicyMutate> {
  return request(`/model/policies/${id}/activate`, { method: "POST" });
}

// ----- Calibration + validation harness -----
export type CalibrationBin = { lo: number; hi: number; n: number; correct: number; rate: number | null; calibrated: number | null };
export type Calibration = { n_samples: number; base_rate: number | null; min_bin_support: number; bins: CalibrationBin[]; built_at: string };
export function buildCalibration(days?: number): Promise<Calibration> {
  return request("/model/calibrate", { method: "POST", body: JSON.stringify(days ? { days } : {}) });
}
export type ValidationReport = {
  window_days: number | null; n_reviewed: number; agreement_rate: number | null;
  confusion: { tp: number; tn: number; fp: number; fn: number };
  false_approve_rate: number | null; false_reject_rate: number | null;
  per_zone: { zone_key: string; precision: number | null; recall: number | null; n: number }[];
  note: string | null;
};
export function validateModel(days?: number): Promise<ValidationReport> {
  return request(`/model/validate${days ? `?days=${days}` : ""}`);
}
export type RecommendResult = {
  n_reviewed: number; current: Record<string, unknown>;
  recommended: Record<string, unknown> | null; evaluated: number; note: string | null;
};
export function recommendThresholds(maxFalseApprove: number, sweepBlend: boolean, days?: number): Promise<RecommendResult> {
  return request("/model/recommend-thresholds", { method: "POST", body: JSON.stringify({ max_false_approve_rate: maxFalseApprove, sweep_blend: sweepBlend, days }) });
}

// ----- Autonomous agent -----
export type AgentActivityItem = {
  inspection_id: string;
  vehicle_plate: string;
  driver_name: string;
  status: string;
  decision_source: "agent" | "human" | null;
  overall_score: number | null;
  overall_confidence: number | null;
  reasons: ZoneIssueLabel[];
  created_at: string;
  reviewed_at: string | null;
};
export type AgentSummary = {
  mode: string;
  model_name: string | null;
  online: boolean;
  auto_approved: number;
  auto_rejected: number;
  escalated: number;
  awaiting_human: number;
  scored_total: number;
  avg_latency_ms: number | null;
};
export type AgentActivity = { summary: AgentSummary; items: AgentActivityItem[] };
export function getAgentActivity(): Promise<AgentActivity> { return request("/model/activity"); }
export type RunPendingResult = { approved: number; rejected: number; escalated: number; scored_missing: number };
export function runPending(): Promise<RunPendingResult> { return request("/model/run-pending", { method: "POST" }); }

export type ScoringDetail = {
  id: string; model_version_id: string; model_name: string | null;
  overall_score: number | null; overall_confidence: number | null; decision: string;
  reasoning?: string | null; created_at: string;
  zones: { zone_key: string; score: number | null; confidence: number | null; issues: { issue_key: string; severity?: string; description?: string; confidence?: number }[] }[];
};

export type AuditEntry = { id: string; actor_id: string | null; action: string; entity: string; entity_id: string; detail: Record<string, unknown> | null; created_at: string };
export type AuditList = { items: AuditEntry[]; total: number; page: number; page_size: number };
export function listAudit(params: Record<string, string>): Promise<AuditList> {
  const qs = new URLSearchParams(params).toString();
  return request(`/admin/audit?${qs}`);
}
