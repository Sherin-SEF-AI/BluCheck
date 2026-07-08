"use client";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Nav from "@/components/Nav";
import StatusBadge from "@/components/StatusBadge";
import FrameGrid from "@/components/FrameGrid";
import MiniMap from "@/components/MiniMap";
import { SourceBadge } from "@/components/AgentBits";
import { Icon } from "@/components/Icon";
import {
  getInspection, review, getZones, getIssues, reprocessInspection, rerunAnalysis,
  type InspectionDetail, type FrameOut, type TaxonomyItem, type ZoneIssueLabel,
} from "@/lib/api";
import { fmtIST } from "@/lib/time";

const fmt = fmtIST;

function sevColor(sev?: string): string {
  if (sev === "severe") return "var(--danger)";
  if (sev === "moderate") return "var(--warn)";
  if (sev === "minor") return "var(--text-dim)";
  return "var(--warn)";
}

function Detail() {
  const params = useSearchParams();
  const id = params.get("id") ?? "";
  const [insp, setInsp] = useState<InspectionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ kind: "ok" | "err"; msg: string } | null>(null);
  const [pinned, setPinned] = useState<{ frame: FrameOut; url: string }[]>([]);
  // Taxonomy + structured reject labels.
  const [zones, setZones] = useState<TaxonomyItem[]>([]);
  const [issues, setIssues] = useState<TaxonomyItem[]>([]);
  const [labels, setLabels] = useState<ZoneIssueLabel[]>([]);
  const [zoneSel, setZoneSel] = useState("");
  const [issueSel, setIssueSel] = useState("");
  const viewed = useRef<Set<string>>(new Set());

  useEffect(() => {
    Promise.all([getZones(), getIssues()])
      .then(([z, i]) => {
        setZones(z);
        setIssues(i);
        setZoneSel(z[0]?.key ?? "");
        setIssueSel(i[0]?.key ?? "");
      })
      .catch(() => undefined);
  }, []);

  function addLabel() {
    if (!zoneSel || !issueSel) return;
    setLabels((prev) =>
      prev.some((l) => l.zone_key === zoneSel && l.issue_key === issueSel)
        ? prev
        : [...prev, { zone_key: zoneSel, issue_key: issueSel }]
    );
  }
  function removeLabel(idx: number) {
    setLabels((prev) => prev.filter((_, i) => i !== idx));
  }
  function labelText(l: ZoneIssueLabel): string {
    const z = zones.find((x) => x.key === l.zone_key)?.label ?? l.zone_key;
    const i = issues.find((x) => x.key === l.issue_key)?.label ?? l.issue_key;
    return `${z}: ${i}`;
  }

  function pin(frame: FrameOut, url: string) {
    setPinned((prev) => {
      if (prev.some((p) => p.frame.id === frame.id)) return prev;
      const next = [...prev, { frame, url }];
      return next.slice(-2); // keep the two most recent
    });
  }

  const load = useCallback(async () => {
    if (!id) return;
    try {
      setInsp(await getInspection(id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  // Prefill the reject panel with the issues the agent already flagged, so a human confirms/
  // edits instead of re-typing. Runs once per inspection id.
  useEffect(() => {
    const agentLabels: ZoneIssueLabel[] = [];
    for (const z of insp?.scoring?.zones ?? []) {
      for (const iss of z.issues || []) agentLabels.push({ zone_key: z.zone_key, issue_key: iss.issue_key });
    }
    if (agentLabels.length) setLabels(agentLabels);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [insp?.id]);

  async function act(action: "approve" | "reject") {
    if (action === "reject" && labels.length === 0) {
      setError("Select at least one zone and issue before rejecting.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await review(id, action, {
        reason: note || undefined,
        labels: action === "reject" ? labels : [],
        viewedFrameIds: Array.from(viewed.current),
        scoringResultId: insp?.scoring?.id,
      });
      await load();
      setLabels([]);
      setNote("");
      setToast({ kind: "ok", msg: action === "approve" ? "Approved" : "Rejected" });
      setTimeout(() => setToast(null), 2500);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Review failed");
      setToast({ kind: "err", msg: "Review failed" });
      setTimeout(() => setToast(null), 3000);
    } finally {
      setBusy(false);
    }
  }

  // Structured issues the agent detected, usable as reject labels for one-click confirm.
  function agentRejectLabels(): ZoneIssueLabel[] {
    const out: ZoneIssueLabel[] = [];
    for (const z of insp?.scoring?.zones ?? []) {
      for (const iss of z.issues || []) out.push({ zone_key: z.zone_key, issue_key: iss.issue_key });
    }
    return out;
  }
  // What the agent recommends for a still-pending inspection (null = it is unsure). Driven by
  // the server-computed decision only -- no hardcoded score bands here (single source of truth
  // is the model thresholds on the server).
  function agentRec(): { action: "approve" | "reject"; labels: ZoneIssueLabel[] } | null {
    const sc = insp?.scoring;
    if (!sc) return null;
    if (sc.decision === "auto_approve") return { action: "approve", labels: [] };
    if (sc.decision === "auto_reject") return { action: "reject", labels: agentRejectLabels() };
    return null;
  }
  async function confirmAgent() {
    const rec = agentRec();
    if (!rec) return;
    if (rec.action === "reject" && rec.labels.length === 0) {
      setError("Agent flagged low cleanliness but named no specific issue; reject manually below.");
      return;
    }
    setBusy(true); setError(null);
    try {
      await review(id, rec.action, {
        reason: "Confirmed agent recommendation",
        labels: rec.labels,
        viewedFrameIds: Array.from(viewed.current),
        scoringResultId: insp?.scoring?.id,
      });
      await load();
      setToast({ kind: "ok", msg: `Confirmed agent: ${rec.action}` });
      setTimeout(() => setToast(null), 2500);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  async function doReprocess() {
    if (!insp) return;
    if (!window.confirm("Reprocess this inspection? Failed/stuck captures are reset and re-extracted from the original video.")) return;
    setBusy(true); setError(null);
    try {
      const r = await reprocessInspection(id);
      setToast({ kind: "ok", msg: `Reprocessing (${r.captures_reset} capture(s) reset)` });
      setTimeout(() => setToast(null), 2500);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Reprocess failed");
    } finally {
      setBusy(false);
    }
  }

  async function doRerun() {
    if (!insp) return;
    if (!window.confirm("Re-run the full analysis? This clears the current score and re-runs Groq scoring + the supervisor agent's decision.")) return;
    setBusy(true); setError(null);
    try {
      const r = await rerunAnalysis(id);
      setToast({ kind: "ok", msg: `Re-analysing (cleared ${r.scoring_cleared} prior score)` });
      setTimeout(() => setToast(null), 2500);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Re-run failed");
    } finally {
      setBusy(false);
    }
  }

  const canReview = insp ? ["pending", "approved", "rejected"].includes(insp.status) : false;
  const canReprocess = insp ? ["failed", "processing"].includes(insp.status) : false;
  // Re-run the whole pipeline once frames exist (any decided/pending/scored inspection).
  const canRerun = insp ? ["pending", "approved", "rejected"].includes(insp.status) : false;

  // Keyboard review: A = approve, R = reject (ignored while typing in a field).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (!canReview || busy) return;
      if (e.key === "a" || e.key === "A") act("approve");
      else if (e.key === "r" || e.key === "R") act("reject");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canReview, busy, note, labels, id]);

  if (!id) return <div className="container error">No inspection id.</div>;
  if (error && !insp) return <div className="container error">{error}</div>;
  if (!insp) {
    return (
      <div className="container">
        <div className="skeleton" style={{ height: 28, width: 180, marginBottom: 16 }} />
        <div className="skeleton" style={{ height: 200, marginBottom: 16 }} />
        <div className="skeleton" style={{ height: 300 }} />
      </div>
    );
  }

  return (
    <div className="container">
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <h2 className="mono" style={{ margin: 0 }}>{insp.vehicle_plate}</h2>
        <StatusBadge status={insp.status} />
        {canReprocess ? (
          <button className="ghost" disabled={busy} onClick={doReprocess} title="Re-extract frames and re-run scoring for a failed or stuck inspection">
            Reprocess
          </button>
        ) : null}
        {canRerun ? (
          <button className="ghost" disabled={busy} onClick={doRerun} title="Clear the score and re-run Groq scoring + the supervisor agent decision">
            Re-run analysis
          </button>
        ) : null}
      </div>
      <p className="dim">Driver: {insp.driver_name} &nbsp;|&nbsp; Captured: <span className="mono">{fmt(insp.captured_at_utc)}</span></p>

      {insp.ocr_matched !== null || insp.ocr_plate ? (
        <div
          className="mono"
          style={{
            display: "inline-block",
            fontSize: 12,
            padding: "4px 10px",
            borderRadius: 4,
            marginBottom: 12,
            border: `1px solid ${insp.ocr_matched ? "var(--ok)" : "var(--warn)"}`,
            color: insp.ocr_matched ? "var(--ok)" : "var(--warn)",
          }}
          title="Plate read by OCR at capture time, correlated to the registered car"
        >
          {insp.ocr_matched
            ? `PLATE OK: ${insp.ocr_plate ?? insp.vehicle_plate} matches registered car`
            : `PLATE FLAG: OCR read ${insp.ocr_plate ? `"${insp.ocr_plate}"` : "nothing"} vs registered ${insp.vehicle_plate}`}
        </div>
      ) : null}

      {/* Who decided this inspection, and the agent's recommendation when still pending. */}
      {insp.decision_source ? (
        <div className={`banner-row ${insp.decision_source}`}>
          <SourceBadge source={insp.decision_source} />
          <span className="mono" style={{ fontSize: 13 }}>
            {insp.decision_source === "agent" ? "Decided autonomously by the cleanliness agent" : "Reviewed by a human"}
            {" · "}<StatusBadge status={insp.status} />
            {insp.scoring?.overall_score != null ? ` · score ${insp.scoring.overall_score}/100` : ""}
            {insp.reviewed_at ? ` · ${fmt(insp.reviewed_at)}` : ""}
          </span>
        </div>
      ) : insp.status === "pending" && insp.scoring ? (
        (() => {
          const rec = agentRec();
          return (
            <div className="banner-row agent" style={{ justifyContent: "space-between" }}>
              <span className="mono" style={{ fontSize: 13 }}>
                🤖 {rec ? <>Agent recommends: <b style={{ color: rec.action === "approve" ? "var(--ok)" : "var(--danger)" }}>{rec.action.toUpperCase()}</b>{insp.scoring.overall_score != null ? ` (score ${insp.scoring.overall_score}/100)` : ""}</> : <>Agent is <b style={{ color: "var(--warn)" }}>unsure</b> — needs your decision</>}
              </span>
              {rec ? (
                <button disabled={busy} onClick={confirmAgent} title="Apply the agent's recommendation">
                  Confirm agent&apos;s {rec.action}
                </button>
              ) : null}
            </div>
          );
        })()
      ) : null}

      {insp.reinspection_of ? (
        <div className="banner-row" style={{ borderColor: "var(--accent-soft)" }}>
          <span style={{ color: "var(--accent)", display: "inline-flex" }}><Icon name="reinspect" size={16} /></span>
          <span className="mono" style={{ fontSize: 13 }}>
            Re-inspection after a rejection.
            {insp.reinspection_of_reason ? <span className="dim"> Previously flagged: {insp.reinspection_of_reason}</span> : null}
            {" "}<a href={`/inspection/?id=${insp.reinspection_of}`}>view original</a>
          </span>
        </div>
      ) : null}

      <div style={{ display: "flex", gap: 24, flexWrap: "wrap", marginBottom: 8 }}>
        {insp.gps_lat != null && insp.gps_lon != null ? (
          <MiniMap lat={insp.gps_lat} lon={insp.gps_lon} />
        ) : null}
        <div className="card" style={{ flex: 1, minWidth: 260 }}>
          <div className="section-title" style={{ marginTop: 0 }}>DEVICE</div>
          <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 12 }}>
            {JSON.stringify(insp.device_meta ?? {}, null, 2)}
          </pre>
          <div className="mono dim" style={{ fontSize: 12, marginTop: 8 }}>
            accuracy: {insp.gps_accuracy_m != null ? `${insp.gps_accuracy_m.toFixed(1)} m` : "-"}
          </div>
          {insp.reject_reason ? <div className="error" style={{ marginTop: 8 }}>Reject reason: {insp.reject_reason}</div> : null}
        </div>
      </div>

      {insp.scoring ? (
        <div className="card" style={{ marginTop: 8, borderColor: "var(--accent)" }}>
          <div className="section-title" style={{ marginTop: 0 }}>
            MODEL ASSESSMENT
            <span className="dim" style={{ fontSize: 12 }}>
              &nbsp; {insp.scoring.model_name} &nbsp;|&nbsp; decision: {insp.scoring.decision}
            </span>
          </div>
          <div className="stat-row" style={{ marginBottom: 8 }}>
            <div className="stat"><div className="value">{insp.scoring.overall_score ?? "-"}</div><div className="label">overall score</div></div>
            <div className="stat"><div className="value">{insp.scoring.overall_confidence != null ? `${Math.round(insp.scoring.overall_confidence * 100)}%` : "-"}</div><div className="label">confidence</div></div>
          </div>
          {insp.scoring.reasoning ? (
            <div style={{ background: "var(--surface-raised)", borderRadius: 6, padding: "10px 12px", marginBottom: 10, fontSize: 13, lineHeight: 1.5 }}>
              <span className="dim mono" style={{ fontSize: 11 }}>AGENT REASONING&nbsp;&nbsp;</span>{insp.scoring.reasoning}
            </div>
          ) : null}
          {insp.scoring.zones.length > 0 ? (
            <table>
              <thead><tr><th>Zone</th><th>Score</th><th>Conf</th><th>Issues</th></tr></thead>
              <tbody>
                {insp.scoring.zones.map((z) => (
                  <tr key={z.zone_key}>
                    <td className="mono">{z.zone_key}</td>
                    <td className="mono">{z.score ?? "-"}</td>
                    <td className="mono">{z.confidence != null ? `${Math.round(z.confidence * 100)}%` : "-"}</td>
                    <td>
                      {(z.issues || []).length > 0 ? (
                        <span style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                          {(z.issues || []).map((i, idx) => (
                            <span key={idx} className="badge" style={{ borderColor: sevColor(i.severity), color: sevColor(i.severity) }} title={i.description}>
                              {i.issue_key}{i.severity ? ` · ${i.severity}` : ""}
                            </span>
                          ))}
                        </span>
                      ) : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <div className="dim">Model reported no assessable zones.</div>}
          <div className="review-hint">Your approve/reject below is recorded as agreeing with or overriding this verdict (training signal).</div>
        </div>
      ) : null}

      {pinned.length > 0 ? (
        <>
          <div className="section-title">COMPARE ({pinned.length}/2) <a href="#" style={{ fontSize: 12 }} onClick={(e) => { e.preventDefault(); setPinned([]); }}>clear</a></div>
          <div className="compare-tray">
            {pinned.map((p) => (
              <div className="slot" key={p.frame.id}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={p.url} alt={`pinned ${p.frame.seq}`} />
                <div className="slot-meta">
                  <span>frame {p.frame.seq}</span>
                  <span>{fmtIST(p.frame.absolute_ts_utc).slice(11)}</span>
                </div>
              </div>
            ))}
          </div>
        </>
      ) : null}

      {insp.captures.map((cap) => (
        <div key={cap.id}>
          <div className="section-title">
            {cap.kind.toUpperCase()} &nbsp;
            <span className="dim" style={{ fontSize: 12 }}>
              {cap.frame_count} frames &nbsp;|&nbsp; {cap.resolution ?? "?"} &nbsp;|&nbsp; {cap.status}
              &nbsp;|&nbsp; <span className="dim">hover a frame and click “pin” to compare</span>
            </span>
          </div>
          <FrameGrid inspectionId={insp.id} capture={cap} onPin={pin} onOpen={(fid) => viewed.current.add(fid)} />
        </div>
      ))}

      {insp.reject_labels && insp.reject_labels.length > 0 ? (
        <div className="card" style={{ marginTop: 16, borderColor: "var(--danger)" }}>
          <div className="section-title" style={{ marginTop: 0 }}>REJECTED FOR</div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {insp.reject_labels.map((l, i) => (
              <span key={i} className="badge rejected">{labelText(l)}</span>
            ))}
          </div>
        </div>
      ) : null}

      <div className="card" style={{ marginTop: 24 }}>
        <div className="section-title" style={{ marginTop: 0 }}>REVIEW</div>
        {!canReview ? (
          <div className="dim">This inspection is {insp.status} and cannot be reviewed yet.</div>
        ) : (
          <>
            <div className="dim" style={{ fontSize: 12, marginBottom: 8 }}>
              To reject, tag each dirty zone with an issue. Approvals need no tags.
            </div>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <select value={zoneSel} onChange={(e) => setZoneSel(e.target.value)}>
                {zones.map((z) => <option key={z.key} value={z.key}>{z.label}</option>)}
              </select>
              <select value={issueSel} onChange={(e) => setIssueSel(e.target.value)}>
                {issues.map((i) => <option key={i.key} value={i.key}>{i.label}</option>)}
              </select>
              <button className="ghost" onClick={addLabel}>Add issue</button>
              <input placeholder="Optional note" value={note} onChange={(e) => setNote(e.target.value)} style={{ flex: 1, minWidth: 160 }} />
            </div>
            {labels.length > 0 ? (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
                {labels.map((l, i) => (
                  <span key={i} className="badge rejected" style={{ cursor: "pointer" }} onClick={() => removeLabel(i)} title="click to remove">
                    {labelText(l)} ✕
                  </span>
                ))}
              </div>
            ) : null}
            {error ? <div className="error" style={{ marginTop: 8 }}>{error}</div> : null}
            <div style={{ display: "flex", gap: 12, marginTop: 12 }}>
              <button disabled={busy} onClick={() => act("approve")}>Approve</button>
              <button className="danger" disabled={busy || labels.length === 0} onClick={() => act("reject")}>Reject ({labels.length})</button>
            </div>
            <div className="review-hint">
              Shortcuts: A approve · R reject · ← → move frames · Esc close · scroll to zoom
            </div>
          </>
        )}
      </div>

      {toast ? <div className={`toast ${toast.kind}`}>{toast.msg}</div> : null}
    </div>
  );
}

export default function InspectionDetailPage() {
  return (
    <>
      <Nav />
      <Suspense fallback={<div className="container dim">Loading...</div>}>
        <Detail />
      </Suspense>
    </>
  );
}
