"use client";
import { useCallback, useEffect, useState } from "react";
import Nav from "@/components/Nav";
import {
  getModelVersion, getPerformance, setMode, setThresholds,
  getScoringConfig, patchScoringConfig, buildCalibration, validateModel, recommendThresholds,
  generateSop, applySop,
  listPolicies, savePolicy, updatePolicy, deletePolicy, activatePolicy,
  getTuningSuggestion,
  type ModelPerformance, type ModelVersion, type ScoringConfig, type Calibration,
  type ValidationReport, type RecommendResult, type SopProposal,
  type Policy, type RecommendedSop, type TuningSuggestion,
} from "@/lib/api";

const MODES: { key: "shadow" | "assist" | "auto" | "disabled"; label: string; hint: string }[] = [
  { key: "shadow", label: "Shadow", hint: "Scores only. No action. (default)" },
  { key: "assist", label: "Assist", hint: "Attach scores + suggestions to the human queue." },
  { key: "auto", label: "Auto", hint: "Confidence-gated auto approve/reject." },
  { key: "disabled", label: "Disabled (kill switch)", hint: "No scoring. Full human review." },
];

export default function ModelPage() {
  const [mv, setMv] = useState<ModelVersion | null>(null);
  const [perf, setPerf] = useState<ModelPerformance | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [approve, setApprove] = useState<number | "">("");
  const [reject, setReject] = useState<number | "">("");
  const [scfg, setScfg] = useState<ScoringConfig | null>(null);
  const [blend, setBlend] = useState<number | "">("");
  const [maxImg, setMaxImg] = useState<number | "">("");
  const [calib, setCalib] = useState<Calibration | null>(null);
  const [validation, setValidation] = useState<ValidationReport | null>(null);
  const [rec, setRec] = useState<RecommendResult | null>(null);
  const [maxFa, setMaxFa] = useState(0.05);
  const [fullAuto, setFullAuto] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [sopText, setSopText] = useState("");
  const [proposal, setProposal] = useState<SopProposal | null>(null);
  const [activeSop, setActiveSop] = useState<string | null>(null);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [recommended, setRecommended] = useState<RecommendedSop[]>([]);
  const [policyName, setPolicyName] = useState("");
  const [tuning, setTuning] = useState<TuningSuggestion | null>(null);
  const [tuningBusy, setTuningBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [v, p, sc] = await Promise.all([getModelVersion(), getPerformance(), getScoringConfig()]);
      setMv(v); setPerf(p); setScfg(sc);
      const ov = (v.thresholds as any)?.overall ?? {};
      // Thresholds are read from the server (single source of truth) -- no hardcoded 85/40.
      setApprove(typeof ov.auto_approve === "number" ? ov.auto_approve : 85);
      setReject(typeof ov.auto_reject === "number" ? ov.auto_reject : 40);
      setBlend(Number(sc.effective.blend_mean_weight));
      setMaxImg(Number(sc.effective.max_images_per_call));
      setFullAuto(Boolean((v.thresholds as { full_autonomy?: boolean } | null)?.full_autonomy));
      setActiveSop((sc.stored as { _sop?: string } | null)?._sop ?? null);
      const pl = await listPolicies();
      setPolicies(pl.policies); setActiveId(pl.active_id); setRecommended(pl.recommended);
    } catch (e) { setErr(e instanceof Error ? e.message : "Failed to load"); }
  }, []);

  async function doGenerateSop() {
    if (sopText.trim().length < 4) return;
    setBusy(true); setErr(null); setNote(null); setProposal(null);
    try { setProposal(await generateSop(sopText)); }
    catch (e) { setErr(e instanceof Error ? e.message : "Could not generate SOP"); } finally { setBusy(false); }
  }
  async function doApplySop() {
    if (!proposal) return;
    if (!window.confirm("Apply this policy now WITHOUT saving it to your library? It becomes the live cleanliness criteria. To keep it for later, give it a name and use Save instead.")) return;
    setBusy(true); setErr(null); setNote(null);
    try {
      await applySop(sopText, proposal.scoring_config, proposal.thresholds as Record<string, unknown>);
      setNote("Policy applied — live for new inspections (not saved to the library).");
      setProposal(null);
      await load();
    } catch (e) { setErr(e instanceof Error ? e.message : "Failed to apply"); } finally { setBusy(false); }
  }
  async function doAnalyzeTuning() {
    setTuningBusy(true); setErr(null); setNote(null); setTuning(null);
    try { setTuning(await getTuningSuggestion(30)); }
    catch (e) { setErr(e instanceof Error ? e.message : "Could not analyze overrides"); } finally { setTuningBusy(false); }
  }
  async function doApplyTuning() {
    if (!tuning || tuning.no_change || !tuning.scoring_config) return;
    if (!window.confirm("Apply this self-tuning adjustment? It updates the live zone weights, severity caps and thresholds based on where humans overturned the agent.")) return;
    setBusy(true); setErr(null); setNote(null);
    try {
      await applySop(tuning.summary || "Auto-tuned from human overrides", tuning.scoring_config, (tuning.thresholds ?? {}) as Record<string, unknown>);
      setNote("Self-tuning applied — live for new inspections.");
      setTuning(null);
      await load();
    } catch (e) { setErr(e instanceof Error ? e.message : "Failed to apply"); } finally { setBusy(false); }
  }

  async function doSavePolicy(activate: boolean) {
    if (!proposal) return;
    const name = policyName.trim() || (sopText.trim().slice(0, 40) || "Untitled policy");
    setBusy(true); setErr(null); setNote(null);
    try {
      const res = await savePolicy({
        name, sop: sopText, scoring_config: proposal.scoring_config,
        thresholds: proposal.thresholds as Record<string, unknown>, summary: proposal.summary, activate,
      });
      setPolicies(res.policies); setActiveId(res.active_id);
      setNote(activate ? `Policy “${name}” saved and made active — live for new inspections.` : `Policy “${name}” saved to your library.`);
      setProposal(null); setPolicyName("");
      if (activate) await load();
    } catch (e) { setErr(e instanceof Error ? e.message : "Failed to save"); } finally { setBusy(false); }
  }
  async function doActivatePolicy(p: Policy) {
    if (!window.confirm(`Make “${p.name}” the live cleanliness policy? It replaces the current scoring criteria for new inspections.`)) return;
    setBusy(true); setErr(null); setNote(null);
    try {
      const res = await activatePolicy(p.id);
      setPolicies(res.policies); setActiveId(res.active_id);
      setNote(`“${p.name}” is now the active policy.`);
      await load();
    } catch (e) { setErr(e instanceof Error ? e.message : "Failed to activate"); } finally { setBusy(false); }
  }
  async function doDeletePolicy(p: Policy) {
    if (!window.confirm(`Delete policy “${p.name}”? This cannot be undone.${p.active ? "\n\nThis is the ACTIVE policy; the live scoring config stays as-is but no policy will be marked active." : ""}`)) return;
    setBusy(true); setErr(null); setNote(null);
    try {
      const res = await deletePolicy(p.id);
      setPolicies(res.policies); setActiveId(res.active_id);
      setNote(`Policy “${p.name}” deleted.`);
    } catch (e) { setErr(e instanceof Error ? e.message : "Failed to delete"); } finally { setBusy(false); }
  }
  async function doRenamePolicy(p: Policy) {
    const name = window.prompt("Rename policy", p.name);
    if (!name || name.trim() === p.name) return;
    setBusy(true); setErr(null); setNote(null);
    try {
      const res = await updatePolicy(p.id, { name: name.trim() });
      setPolicies(res.policies); setActiveId(res.active_id);
    } catch (e) { setErr(e instanceof Error ? e.message : "Failed to rename"); } finally { setBusy(false); }
  }
  function editPolicyText(p: Policy) {
    setSopText(p.sop); setPolicyName(p.name); setProposal(null); setNote(`Loaded “${p.name}”. Edit the text, Generate policy, then Save to update or add a new one.`);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  function useTemplate(sop: string) {
    setSopText(sop); setProposal(null); setPolicyName("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function toggleFullAuto() {
    const next = !fullAuto;
    if (next && !window.confirm("Enable FULL AUTONOMY?\n\nEvery inspection will be auto-approved or auto-rejected by the agent with NO human and NO calibration safety net. Uncertain cases are decided (defaulting to reject). The 'Disabled' kill switch still stops everything.")) return;
    setBusy(true); setErr(null); setNote(null);
    try {
      await setThresholds({ ...(mv?.thresholds as Record<string, unknown> ?? {}), full_autonomy: next });
      setNote(next ? "Full autonomy ON — the agent decides everything, no human." : "Full autonomy off — calibration-gated (safe) behavior restored.");
      await load();
    } catch (e) { setErr(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); }
  }
  useEffect(() => { load(); }, [load]);

  async function saveScoring() {
    setBusy(true); setErr(null); setNote(null);
    try {
      await patchScoringConfig({ blend_mean_weight: Number(blend), max_images_per_call: Number(maxImg) });
      setNote("Scoring config saved (applies to new scores, no redeploy).");
      await load();
    } catch (e) { setErr(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); }
  }
  async function doCalibrate() {
    setBusy(true); setErr(null); setNote(null);
    try { setCalib(await buildCalibration()); setNote("Calibration curve rebuilt."); }
    catch (e) { setErr(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); }
  }
  async function doValidate() {
    setBusy(true); setErr(null);
    try { setValidation(await validateModel()); }
    catch (e) { setErr(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); }
  }
  async function doRecommend() {
    setBusy(true); setErr(null);
    try { setRec(await recommendThresholds(maxFa, false)); }
    catch (e) { setErr(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); }
  }

  async function changeMode(m: "shadow" | "assist" | "auto" | "disabled") {
    if (m === "auto" && !window.confirm("Enable AUTO mode? The model will auto-approve/reject within the confidence bands. Make sure shadow agreement is acceptable first.")) return;
    setBusy(true); setErr(null);
    try { await setMode(m); await load(); } catch (e) { setErr(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); }
  }
  async function saveThresholds() {
    setBusy(true); setErr(null);
    try {
      await setThresholds({
        ...(mv?.thresholds as Record<string, unknown> ?? {}),
        overall: { auto_approve: Number(approve), auto_reject: Number(reject) },
      });
      await load();
    } catch (e) { setErr(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); }
  }

  return (
    <>
      <Nav />
      <div className="container">
        <h2>Model performance</h2>
        {err ? <div className="error">{err}</div> : null}
        {note ? <div className="banner-row agent" style={{ color: "var(--accent)" }}>{note}</div> : null}
        {!mv || !perf ? <div className="dim">Loading...</div> : (
          <>
            <div className="dim" style={{ marginBottom: 12 }}>
              {perf.model_name ?? mv.vlm_model} · prompt {mv.prompt_version} · current mode <span className={`badge ${mv.mode === "disabled" ? "rejected" : mv.mode === "auto" ? "approved" : "pending"}`}>{mv.mode}</span>
            </div>

            <div className="section-title" style={{ marginTop: 0 }}>CLEANLINESS SOP · describe your policy, the agent configures scoring</div>
            <div className="card">
              {activeSop ? <div className="review-hint" style={{ marginTop: 0, marginBottom: 10 }}>Active policy: <span style={{ color: "var(--text)" }}>&ldquo;{activeSop}&rdquo;</span></div> : null}
              <textarea
                value={sopText}
                onChange={(e) => setSopText(e.target.value)}
                rows={3}
                placeholder="e.g. Prioritise the interior passenger area (seats, floor, dashboard). Small dirt on the exterior is fine, but flag big exterior dirt. Be lenient on the boot."
                style={{ width: "100%", resize: "vertical", fontFamily: "inherit" }}
              />
              <div className="filters" style={{ marginTop: 10, marginBottom: 0 }}>
                <button disabled={busy || sopText.trim().length < 4} onClick={doGenerateSop}>
                  {busy && !proposal ? "Generating…" : "Generate policy"}
                </button>
                <span className="review-hint" style={{ marginTop: 0 }}>An agent turns plain English into per-zone weights, severity caps and thresholds. Review before applying.</span>
              </div>

              {proposal ? (
                <div style={{ marginTop: 14, borderTop: "1px solid var(--border)", paddingTop: 14 }}>
                  <div style={{ color: "var(--text)", fontSize: 13, lineHeight: 1.55, marginBottom: 10 }}>{proposal.summary}</div>
                  <div className="review-hint" style={{ marginTop: 0, marginBottom: 6 }}>PROPOSED ZONE PRIORITY (higher = matters more)</div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 5, marginBottom: 12, maxWidth: 460 }}>
                    {Object.entries((proposal.scoring_config.zone_weight ?? {}) as Record<string, number>)
                      .sort((a, b) => b[1] - a[1])
                      .map(([z, w]) => (
                        <div key={z} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span className="mono dim" style={{ width: 140, fontSize: 12 }}>{z}</span>
                          <div style={{ flex: 1, height: 8, background: "var(--surface-raised)", borderRadius: 4, overflow: "hidden" }}>
                            <div style={{ width: `${Math.min(100, (w / 2) * 100)}%`, height: "100%", background: "var(--accent)" }} />
                          </div>
                          <span className="mono" style={{ width: 34, textAlign: "right", fontSize: 12 }}>{w.toFixed(1)}</span>
                        </div>
                      ))}
                  </div>
                  <div className="mono dim" style={{ fontSize: 12, marginBottom: 12 }}>
                    severity caps — minor ≤{(proposal.scoring_config.severity_cap as Record<string, number>)?.minor}, moderate ≤{(proposal.scoring_config.severity_cap as Record<string, number>)?.moderate}, severe ≤{(proposal.scoring_config.severity_cap as Record<string, number>)?.severe}
                    {" · "}mean/worst blend {String(proposal.scoring_config.blend_mean_weight)}
                    {" · "}auto-approve ≥{proposal.thresholds.overall?.auto_approve}, auto-reject ≤{proposal.thresholds.overall?.auto_reject}
                  </div>
                  <div className="filters" style={{ marginBottom: 8, alignItems: "center" }}>
                    <input
                      value={policyName}
                      onChange={(e) => setPolicyName(e.target.value)}
                      placeholder="Policy name (e.g. Passenger-first)"
                      style={{ minWidth: 240, fontFamily: "inherit" }}
                    />
                    <button disabled={busy} onClick={() => doSavePolicy(true)}>Save & activate</button>
                    <button className="ghost" disabled={busy} onClick={() => doSavePolicy(false)}>Save to library</button>
                  </div>
                  <div className="filters" style={{ marginBottom: 0 }}>
                    <button className="ghost" disabled={busy} onClick={doApplySop}>Apply once (don&rsquo;t save)</button>
                    <button className="ghost" disabled={busy} onClick={() => setProposal(null)}>Discard</button>
                    <span className="review-hint" style={{ marginTop: 0 }}>Save keeps it in your library to reuse, edit or switch back to. Fine-tune exact numbers below.</span>
                  </div>
                </div>
              ) : null}

              {recommended.length ? (
                <div style={{ marginTop: 14, borderTop: "1px solid var(--border)", paddingTop: 12 }}>
                  <div className="review-hint" style={{ marginTop: 0, marginBottom: 8 }}>RECOMMENDED STARTERS · click to load, then Generate</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {recommended.map((r) => (
                      <button key={r.title} className="ghost" disabled={busy} onClick={() => useTemplate(r.sop)}
                        title={r.sop} style={{ fontSize: 12 }}>{r.title}</button>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>

            <div className="section-title">SAVED POLICIES {activeId ? "" : "· none active"}</div>
            <div className="card">
              {policies.length === 0 ? (
                <div className="review-hint" style={{ marginTop: 0 }}>No saved policies yet. Generate one above and Save it to build your library.</div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  {policies.map((p) => (
                    <div key={p.id} style={{ display: "flex", flexDirection: "column", gap: 6, border: "1px solid var(--border)", borderRadius: 8, padding: "10px 12px", background: p.active ? "var(--surface-raised)" : "transparent" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                        <span style={{ color: "var(--text)", fontWeight: 600 }}>{p.name}</span>
                        {p.active ? <span className="badge approved">ACTIVE</span> : null}
                        {p.thresholds.overall ? <span className="mono dim" style={{ fontSize: 11 }}>approve ≥{p.thresholds.overall.auto_approve} · reject ≤{p.thresholds.overall.auto_reject}</span> : null}
                        <div style={{ flex: 1 }} />
                        {!p.active ? <button disabled={busy} onClick={() => doActivatePolicy(p)} style={{ fontSize: 12 }}>Activate</button> : null}
                        <button className="ghost" disabled={busy} onClick={() => editPolicyText(p)} style={{ fontSize: 12 }}>Edit</button>
                        <button className="ghost" disabled={busy} onClick={() => doRenamePolicy(p)} style={{ fontSize: 12 }}>Rename</button>
                        <button className="ghost" disabled={busy} onClick={() => doDeletePolicy(p)} style={{ fontSize: 12, color: "var(--danger, #d66)" }}>Delete</button>
                      </div>
                      {p.summary ? <div className="dim" style={{ fontSize: 12, lineHeight: 1.5 }}>{p.summary}</div> : <div className="dim" style={{ fontSize: 12, fontStyle: "italic" }}>{p.sop.slice(0, 160)}{p.sop.length > 160 ? "…" : ""}</div>}
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="section-title">SELF-TUNING · learn from human overrides</div>
            <div className="card">
              <div className="filters" style={{ marginTop: 0, marginBottom: tuning ? 12 : 0 }}>
                <button disabled={tuningBusy} onClick={doAnalyzeTuning}>{tuningBusy ? "Analyzing…" : "Analyze overrides"}</button>
                <span className="review-hint" style={{ marginTop: 0 }}>An agent reviews the last 30 days of cases where a human overturned the agent and proposes a modest policy adjustment. Review before applying.</span>
              </div>
              {tuning ? (
                <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
                  <div className="mono dim" style={{ fontSize: 12, marginBottom: 8 }}>
                    {tuning.evidence.overrides} override(s) · {tuning.evidence.too_strict} too-strict (agent rejected, human approved) · {tuning.evidence.too_lenient} too-lenient
                    {" · "}confidence {(tuning.confidence * 100).toFixed(0)}%
                  </div>
                  <div style={{ color: "var(--text)", fontSize: 13, lineHeight: 1.55, marginBottom: 10 }}>{tuning.summary}</div>
                  {tuning.no_change || !tuning.scoring_config ? (
                    <div className="review-hint" style={{ marginTop: 0 }}>No change recommended.</div>
                  ) : (
                    <>
                      <div className="review-hint" style={{ marginTop: 0, marginBottom: 6 }}>PROPOSED ZONE PRIORITY</div>
                      <div style={{ display: "flex", flexDirection: "column", gap: 5, marginBottom: 12, maxWidth: 460 }}>
                        {Object.entries((tuning.scoring_config.zone_weight ?? {}) as Record<string, number>)
                          .sort((a, b) => b[1] - a[1])
                          .map(([z, w]) => (
                            <div key={z} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                              <span className="mono dim" style={{ width: 140, fontSize: 12 }}>{z}</span>
                              <div style={{ flex: 1, height: 8, background: "var(--surface-raised)", borderRadius: 4, overflow: "hidden" }}>
                                <div style={{ width: `${Math.min(100, (w / 2) * 100)}%`, height: "100%", background: "var(--accent)" }} />
                              </div>
                              <span className="mono" style={{ width: 34, textAlign: "right", fontSize: 12 }}>{w.toFixed(1)}</span>
                            </div>
                          ))}
                      </div>
                      <div className="mono dim" style={{ fontSize: 12, marginBottom: 12 }}>
                        auto-approve ≥{tuning.thresholds?.overall?.auto_approve}, auto-reject ≤{tuning.thresholds?.overall?.auto_reject}
                      </div>
                      <div className="filters" style={{ marginBottom: 0 }}>
                        <button disabled={busy} onClick={doApplyTuning}>Apply adjustment</button>
                        <button className="ghost" disabled={busy} onClick={() => setTuning(null)}>Dismiss</button>
                      </div>
                    </>
                  )}
                </div>
              ) : null}
            </div>

            <div className="section-title">MODE</div>
            <div className="stat-row">
              {MODES.map((m) => (
                <button key={m.key} className={mv.mode === m.key ? "" : "ghost"} disabled={busy || mv.mode === m.key}
                  onClick={() => changeMode(m.key)} title={m.hint} style={{ minWidth: 140 }}>
                  {m.label}
                </button>
              ))}
            </div>
            <div className="review-hint">{MODES.find((m) => m.key === mv.mode)?.hint}</div>

            <div className="section-title">FULL AUTONOMY</div>
            <div className="filters" style={{ alignItems: "center" }}>
              <button className={fullAuto ? "" : "ghost"} disabled={busy || mv.mode !== "auto"} onClick={toggleFullAuto} style={{ minWidth: 160 }}>
                {fullAuto ? "● Full autonomy ON" : "Turn on full autonomy"}
              </button>
              <span className="review-hint" style={{ marginTop: 0 }}>
                {mv.mode !== "auto"
                  ? "Requires Auto mode. In full autonomy the agent decides every inspection with no human and no calibration gate (uncertain → reject). 'Disabled' still kills everything."
                  : fullAuto
                    ? "Agent decides everything, no human. Uncertain cases default to reject. Kill switch: switch to Disabled."
                    : "Bypass the human/calibration safety net: the agent auto-approves/rejects every inspection."}
              </span>
            </div>

            <div className="section-title">DECISION THRESHOLDS (overall score)</div>
            <div className="filters">
              <label className="mono dim">auto-approve ≥ <input type="number" min={0} max={100} value={approve} onChange={(e) => setApprove(e.target.value === "" ? "" : Number(e.target.value))} style={{ width: 70 }} /></label>
              <label className="mono dim">auto-reject ≤ <input type="number" min={0} max={100} value={reject} onChange={(e) => setReject(e.target.value === "" ? "" : Number(e.target.value))} style={{ width: 70 }} /></label>
              <button className="ghost" disabled={busy} onClick={saveThresholds}>Save thresholds</button>
              <span className="review-hint" style={{ marginTop: 0 }}>Read from the server; this is the single place to edit them.</span>
            </div>

            <div className="section-title">SCORING MATH (tunable, versioned, no redeploy)</div>
            <div className="filters">
              <label className="mono dim">mean/worst blend <input type="number" min={0} max={1} step={0.05} value={blend} onChange={(e) => setBlend(e.target.value === "" ? "" : Number(e.target.value))} style={{ width: 70 }} /></label>
              <label className="mono dim">images/call <input type="number" min={1} max={8} value={maxImg} onChange={(e) => setMaxImg(e.target.value === "" ? "" : Number(e.target.value))} style={{ width: 60 }} /></label>
              <button className="ghost" disabled={busy} onClick={saveScoring}>Save scoring config</button>
              <span className="review-hint" style={{ marginTop: 0 }}>{scfg?.stored ? "custom" : "using defaults"} · zone weights + severity caps in effective config</span>
            </div>

            <div className="section-title">CONFIDENCE CALIBRATION</div>
            <div className="card">
              <div className="filters" style={{ marginBottom: calib ? 12 : 0 }}>
                <button className="ghost" disabled={busy} onClick={doCalibrate}>Build calibration curve</button>
                <span className="review-hint" style={{ marginTop: 0 }}>
                  Auto mode only acts where calibrated confidence clears the floor; no calibration ⇒ everything routes to a human.
                </span>
              </div>
              {calib ? (
                <>
                  <div className="mono dim" style={{ fontSize: 12, marginBottom: 8 }}>
                    fit on {calib.n_samples} labeled inspections · base rate {calib.base_rate != null ? `${Math.round(calib.base_rate * 100)}%` : "-"} · min support {calib.min_bin_support}/bin
                  </div>
                  <div style={{ display: "flex", alignItems: "flex-end", gap: 6, height: 90 }}>
                    {calib.bins.map((b) => (
                      <div key={b.lo} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 3 }}
                        title={`conf ${b.lo}-${b.hi}: n=${b.n}, calibrated=${b.calibrated ?? "insufficient"}`}>
                        <div style={{ width: "100%", background: b.n >= calib.min_bin_support ? "var(--accent)" : "var(--border)", height: `${(b.calibrated ?? 0) * 70}px` }} />
                        <div className="mono" style={{ fontSize: 9, color: "var(--text-dim)" }}>{b.lo.toFixed(1)}</div>
                      </div>
                    ))}
                  </div>
                  <div className="review-hint" style={{ marginTop: 6 }}>Blue = enough data to trust; grey = insufficient (auto stays off there). Height = calibrated correctness.</div>
                </>
              ) : null}
            </div>

            <div className="section-title">VALIDATION (report-only)</div>
            <div className="card">
              <div className="filters" style={{ marginBottom: validation ? 12 : 0 }}>
                <button className="ghost" disabled={busy} onClick={doValidate}>Run validation</button>
                <label className="mono dim">max false-approve <input type="number" min={0} max={1} step={0.01} value={maxFa} onChange={(e) => setMaxFa(Number(e.target.value))} style={{ width: 64 }} /></label>
                <button className="ghost" disabled={busy} onClick={doRecommend}>Recommend thresholds</button>
              </div>
              {validation ? (
                <div className="stat-row" style={{ marginBottom: 8 }}>
                  <div className="stat"><div className="value">{validation.agreement_rate != null ? `${Math.round(validation.agreement_rate * 100)}%` : "-"}</div><div className="label">agreement (n={validation.n_reviewed})</div></div>
                  <div className="stat"><div className="value" style={{ color: "var(--danger)" }}>{validation.false_approve_rate != null ? `${Math.round(validation.false_approve_rate * 100)}%` : "-"}</div><div className="label">false-approve (missed dirty)</div></div>
                  <div className="stat"><div className="value" style={{ color: "var(--warn)" }}>{validation.false_reject_rate != null ? `${Math.round(validation.false_reject_rate * 100)}%` : "-"}</div><div className="label">false-reject</div></div>
                </div>
              ) : null}
              {validation?.note ? <div className="review-hint" style={{ marginTop: 0 }}>{validation.note}</div> : null}
              {rec ? (
                <div className="mono" style={{ fontSize: 12, marginTop: 8 }}>
                  {rec.recommended
                    ? <>Recommended: approve ≥ <b>{String(rec.recommended.auto_approve)}</b>, reject ≤ <b>{String(rec.recommended.auto_reject)}</b> · agreement {Math.round(Number(rec.recommended.agreement) * 100)}% · FA {Math.round(Number(rec.recommended.false_approve_rate) * 100)}% <span className="dim">({rec.evaluated} settings swept, report-only)</span></>
                    : <span className="dim">{rec.note}</span>}
                </div>
              ) : null}
            </div>

            {perf.overrides && perf.overrides.count > 0 ? (
              <>
                <div className="section-title">SUPERVISOR OVERRIDES</div>
                <div className="stat-row">
                  <div className="stat"><div className="value">{perf.overrides.count}</div><div className="label">band overrides</div></div>
                  <div className="stat"><div className="value">{perf.overrides.reviewed}</div><div className="label">later reviewed</div></div>
                  <div className="stat"><div className="value" style={{ color: "var(--ok)" }}>{perf.overrides.supervisor_right}</div><div className="label">supervisor right</div></div>
                  <div className="stat"><div className="value" style={{ color: "var(--text-dim)" }}>{perf.overrides.band_right}</div><div className="label">band would be right</div></div>
                </div>
                <div className="review-hint">When the LLM supervisor overrode the deterministic band, how often it beat the band against the eventual human call.</div>
              </>
            ) : null}

            <div className="section-title">AGREEMENT vs HUMANS</div>
            <div className="stat-row">
              <div className="stat"><div className="value">{perf.agreement_rate != null ? `${Math.round(perf.agreement_rate * 100)}%` : "-"}</div><div className="label">overall agreement</div></div>
              <div className="stat"><div className="value">{perf.total_with_human}</div><div className="label">scored + reviewed</div></div>
              <div className="stat"><div className="value">{perf.total_scored}</div><div className="label">total scored</div></div>
              <div className="stat"><div className="value">{perf.avg_latency_ms != null ? `${Math.round(perf.avg_latency_ms)}ms` : "-"}</div><div className="label">avg latency</div></div>
            </div>

            <div className="section-title">CONFUSION (dirty = positive)</div>
            <div className="card" style={{ maxWidth: 360 }}>
              <table>
                <tbody className="mono">
                  <tr><td></td><td className="dim">human reject</td><td className="dim">human approve</td></tr>
                  <tr><td className="dim">model reject</td><td>{perf.confusion.tp}</td><td style={{ color: "var(--danger)" }}>{perf.confusion.fp}</td></tr>
                  <tr><td className="dim">model approve</td><td style={{ color: "var(--danger)" }}>{perf.confusion.fn}</td><td>{perf.confusion.tn}</td></tr>
                </tbody>
              </table>
              <div className="mono dim" style={{ fontSize: 11, marginTop: 8 }}>
                confidence on agreements {perf.avg_confidence_agree ?? "-"} · on disagreements {perf.avg_confidence_disagree ?? "-"}
              </div>
            </div>

            <div className="section-title">PER-ZONE AGREEMENT</div>
            <div className="card" style={{ padding: 0, overflow: "hidden" }}>
              <table>
                <thead><tr><th>Zone</th><th>Agreement</th><th>n</th></tr></thead>
                <tbody>
                  {perf.per_zone_agreement.map((z) => (
                    <tr key={z.zone_key}><td className="mono">{z.zone_key}</td><td className="mono">{Math.round(z.agreement * 100)}%</td><td className="mono">{z.n}</td></tr>
                  ))}
                  {perf.per_zone_agreement.length === 0 ? <tr><td colSpan={3} className="dim" style={{ textAlign: "center", padding: 16 }}>No labelled overlap yet.</td></tr> : null}
                </tbody>
              </table>
            </div>

            <div className="section-title">AGREEMENT DRIFT</div>
            <div className="card">
              {perf.agreement_by_day.length === 0 ? <div className="dim">Not enough data yet.</div> : (
                <div style={{ display: "flex", alignItems: "flex-end", gap: 8, height: 100 }}>
                  {perf.agreement_by_day.map((d) => (
                    <div key={d.day} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }} title={`${d.day}: ${Math.round(d.agreement * 100)}% (n=${d.n})`}>
                      <div style={{ width: "100%", background: "var(--accent)", height: `${d.agreement * 80}px` }} />
                      <div className="mono" style={{ fontSize: 9, color: "var(--text-dim)" }}>{d.day.slice(5)}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </>
  );
}
