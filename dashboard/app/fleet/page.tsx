"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import Nav from "@/components/Nav";
import StatusBadge from "@/components/StatusBadge";
import { ScoreBar, SourceBadge } from "@/components/AgentBits";
import { Icon } from "@/components/Icon";
import { getToken, getVehicleTrends, type VehicleTrend } from "@/lib/api";
import { toCsv } from "@/lib/csv";
import { fmtIST } from "@/lib/time";

type SortKey = "plate" | "avg_score" | "rejected" | "total" | "last";

export default function FleetPage() {
  const [rows, setRows] = useState<VehicleTrend[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>("rejected");
  const [activeOnly, setActiveOnly] = useState(true);

  useEffect(() => {
    if (typeof window !== "undefined" && !getToken()) window.location.href = "/login/";
  }, []);

  const load = useCallback(async () => {
    try {
      setRows((await getVehicleTrends()).vehicles);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load");
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const sorted = useMemo(() => {
    const r = rows.filter((v) => (activeOnly ? v.active : true));
    const cmp: Record<SortKey, (a: VehicleTrend, b: VehicleTrend) => number> = {
      plate: (a, b) => a.plate.localeCompare(b.plate),
      avg_score: (a, b) => (a.avg_score ?? 999) - (b.avg_score ?? 999),
      rejected: (a, b) => b.rejected - a.rejected,
      total: (a, b) => b.total - a.total,
      last: (a, b) => (b.last_inspected_at ?? "").localeCompare(a.last_inspected_at ?? ""),
    };
    return [...r].sort(cmp[sort]);
  }, [rows, sort, activeOnly]);

  function exportCsv() {
    const head = ["plate", "model", "active", "total", "approved", "rejected", "pending", "avg_score", "last_score", "last_status", "last_decided_by", "last_inspected_at"];
    const csv = toCsv(head, sorted.map((v) => [v.plate, v.model ?? "", v.active, v.total, v.approved, v.rejected, v.pending, v.avg_score ?? "", v.last_score ?? "", v.last_status ?? "", v.last_decided_by ?? "", v.last_inspected_at ?? ""]));
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url; a.download = "blucheck-fleet.csv"; a.click();
    URL.revokeObjectURL(url);
  }

  const fleetAvg = useMemo(() => {
    const s = sorted.map((v) => v.avg_score).filter((x): x is number => x != null);
    return s.length ? Math.round(s.reduce((a, b) => a + b, 0) / s.length) : null;
  }, [sorted]);

  return (
    <>
      <Nav />
      <div className="container">
        <div className="page-head">
          <div>
            <h1>Fleet cleanliness</h1>
            <div className="sub">Per-vehicle history, average score, and repeat-reject vehicles across your fleet.</div>
          </div>
          <div className="filters" style={{ marginBottom: 0 }}>
            <button className={activeOnly ? "" : "ghost"} onClick={() => setActiveOnly((v) => !v)}>{activeOnly ? "Active only" : "All vehicles"}</button>
            <button className="ghost" onClick={exportCsv}><Icon name="metrics" size={15} /> Export CSV</button>
          </div>
        </div>
        {err ? <div className="error" style={{ marginBottom: 12 }}>{err}</div> : null}

        <div className="tiles" style={{ marginBottom: 16 }}>
          <div className="tile"><div className="v">{sorted.length}</div><div className="k">Vehicles</div></div>
          <div className="tile"><div className="v">{fleetAvg ?? "—"}</div><div className="k">Fleet avg score</div></div>
          <div className="tile bad"><div className="v">{sorted.reduce((a, v) => a + v.rejected, 0)}</div><div className="k">Total rejects</div></div>
          <div className="tile"><div className="v">{sorted.reduce((a, v) => a + v.total, 0)}</div><div className="k">Total inspections</div></div>
        </div>

        <div className="filters">
          <span className="dim mono" style={{ fontSize: 12 }}>sort:</span>
          {([["rejected", "Most rejects"], ["avg_score", "Lowest avg score"], ["total", "Most inspections"], ["last", "Recently inspected"], ["plate", "Plate"]] as [SortKey, string][]).map(([k, label]) => (
            <button key={k} className={sort === k ? "" : "ghost"} onClick={() => setSort(k)}>{label}</button>
          ))}
        </div>

        <div className="card" style={{ padding: 0 }}>
          <table className="fit tbl-fleet">
            <thead><tr><th>Plate</th><th>Model</th><th>Avg score</th><th>Inspections</th><th>Approved</th><th>Rejected</th><th>Latest</th></tr></thead>
            <tbody>
              {sorted.map((v) => (
                <tr key={v.vehicle_id}>
                  <td className="mono">{v.plate}{!v.active ? <span className="dim" style={{ fontSize: 11 }}> (inactive)</span> : null}</td>
                  <td className="dim">{v.model ?? "-"}</td>
                  <td><ScoreBar score={v.avg_score} /></td>
                  <td className="mono">{v.total}</td>
                  <td className="mono" style={{ color: "var(--ok)" }}>{v.approved}</td>
                  <td className="mono" style={{ color: v.rejected > 0 ? "var(--danger)" : "var(--text-dim)" }}>{v.rejected}</td>
                  <td>
                    {v.last_status ? (
                      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                        <StatusBadge status={v.last_status} />
                        {v.last_decided_by ? <SourceBadge source={v.last_decided_by} /> : null}
                        <span className="dim mono" style={{ fontSize: 11 }}>{v.last_inspected_at ? fmtIST(v.last_inspected_at).slice(5, 16) : ""}</span>
                      </div>
                    ) : <span className="dim">never</span>}
                  </td>
                </tr>
              ))}
              {sorted.length === 0 ? <tr><td colSpan={7} className="dim" style={{ textAlign: "center", padding: 28 }}>No vehicles.</td></tr> : null}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
