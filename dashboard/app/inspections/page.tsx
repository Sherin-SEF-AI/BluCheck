"use client";
import { useCallback, useEffect, useState } from "react";
import Nav from "@/components/Nav";
import StatusBadge from "@/components/StatusBadge";
import { ScoreBar, SourceBadge } from "@/components/AgentBits";
import { getToken, listInspections, review, type InspectionListItem } from "@/lib/api";
import { toCsv } from "@/lib/csv";
import { usePolling } from "@/lib/usePolling";
import { fmtIST } from "@/lib/time";

const STATUSES = ["pending", "processing", "approved", "rejected", "failed", "uploading"];

const fmt = fmtIST;

export default function InspectionsPage() {
  const [items, setItems] = useState<InspectionListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  // Default to all statuses so newly-uploaded (processing) and pending inspections
  // always appear without a filter hiding them.
  const [status, setStatus] = useState("");
  const [plate, setPlate] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [order, setOrder] = useState<"newest" | "uncertainty">("newest");
  const pageSize = 25;

  // Restore saved filters on first mount.
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("blucheck.filters") || "{}");
      // Deliberately do NOT restore a saved status filter: a stale status (e.g. an old
      // "approved") would hide freshly uploaded inspections. Status always starts at All.
      if (typeof saved.plate === "string") setPlate(saved.plate);
      if (typeof saved.dateFrom === "string") setDateFrom(saved.dateFrom);
      if (typeof saved.dateTo === "string") setDateTo(saved.dateTo);
    } catch {
      /* ignore */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Persist filters whenever they change.
  useEffect(() => {
    localStorage.setItem("blucheck.filters", JSON.stringify({ status, plate, dateFrom, dateTo }));
  }, [status, plate, dateFrom, dateTo]);

  async function exportCsv() {
    setExporting(true);
    try {
      const cells: unknown[][] = [];
      let p = 1;
      for (;;) {
        const params: Record<string, string> = { page: String(p), page_size: "200" };
        if (status) params.status = status;
        if (dateFrom) params.date_from = new Date(dateFrom).toISOString();
        if (dateTo) params.date_to = new Date(dateTo).toISOString();
        const res = await listInspections(params);
        for (const i of res.items) {
          if (plate && !i.vehicle_plate.toLowerCase().includes(plate.toLowerCase())) continue;
          cells.push([i.vehicle_plate, i.driver_name, i.status, i.captured_at_utc ?? "", i.gps_lat ?? "", i.gps_lon ?? "", i.created_at]);
        }
        if (res.items.length < 200) break;
        p += 1;
        if (p > 50) break; // safety cap
      }
      const csv = toCsv(["plate", "driver", "status", "captured_at_utc", "gps_lat", "gps_lon", "created_at"], cells);
      const blob = new Blob([csv], { type: "text/csv" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `blucheck-inspections-${status || "all"}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(false);
    }
  }

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // Bulk approve only. Rejections require genuine per-zone/issue labels for the training
  // dataset, so they are done one at a time on the detail page.
  async function bulkApprove() {
    setBulkBusy(true);
    try {
      const targets = items.filter((i) => selected.has(i.id) && i.status === "pending");
      for (const t of targets) {
        await review(t.id, "approve");
      }
      setSelected(new Set());
      await load();
    } finally {
      setBulkBusy(false);
    }
  }

  useEffect(() => {
    if (typeof window !== "undefined" && !getToken()) window.location.href = "/login/";
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = { page: String(page), page_size: String(pageSize) };
      if (status) params.status = status;
      if (dateFrom) params.date_from = new Date(dateFrom).toISOString();
      if (dateTo) params.date_to = new Date(dateTo).toISOString();
      if (order === "uncertainty") params.order = "uncertainty";
      const res = await listInspections(params);
      // Plate filter is client-side over the returned page for simplicity.
      const filtered = plate
        ? res.items.filter((i) => i.vehicle_plate.toLowerCase().includes(plate.toLowerCase()))
        : res.items;
      setItems(filtered);
      setTotal(res.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [page, status, dateFrom, dateTo, plate, order]);

  useEffect(() => {
    load();
  }, [load]);

  // Auto-refresh so newly-extracted inspections appear without a manual reload. Pauses on
  // hidden tabs to avoid pointless background polling of the API.
  usePolling(load, 2000);

  const pages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <>
      <Nav />
      <div className="container">
        <h2>Inspection queue</h2>
        <div className="filters">
          <select value={status} onChange={(e) => { setPage(1); setStatus(e.target.value); }}>
            <option value="">All statuses</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <input placeholder="Plate" value={plate} onChange={(e) => setPlate(e.target.value)} />
          <input type="date" value={dateFrom} onChange={(e) => { setPage(1); setDateFrom(e.target.value); }} />
          <input type="date" value={dateTo} onChange={(e) => { setPage(1); setDateTo(e.target.value); }} />
          <button className="ghost" onClick={load}>Refresh</button>
          <button className="ghost" onClick={exportCsv} disabled={exporting}>{exporting ? "Exporting..." : "Export CSV"}</button>
          <button className="ghost" onClick={() => { setPage(1); setOrder((o) => (o === "newest" ? "uncertainty" : "newest")); }} title="Active learning: most uncertain model verdicts first">
            Order: {order === "uncertainty" ? "Most uncertain" : "Newest"}
          </button>
        </div>

        {error ? <div className="error">{error}</div> : null}

        {selected.size > 0 ? (
          <div className="card" style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 12 }}>
            <span className="mono">{selected.size} selected</span>
            <button disabled={bulkBusy} onClick={bulkApprove}>Approve selected</button>
            <button className="ghost" onClick={() => setSelected(new Set())}>Clear</button>
            <span className="dim mono" style={{ fontSize: 11 }}>bulk approve only; reject needs per-zone labels</span>
          </div>
        ) : null}

        <div className="card" style={{ padding: 0 }}>
          <table className="fit tbl-queue">
            <thead>
              <tr>
                <th style={{ width: 32 }}></th>
                <th>Plate</th>
                <th>Driver</th>
                <th>Captured (IST)</th>
                <th>Score</th>
                <th>Decided by</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {items.map((i) => (
                <tr key={i.id} style={{ cursor: "pointer" }} onClick={() => { window.location.href = `/inspection/?id=${i.id}`; }}>
                  <td onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" checked={selected.has(i.id)} onChange={() => toggle(i.id)} />
                  </td>
                  <td className="mono">
                    {i.vehicle_plate}
                    {i.integrity_risk && i.integrity_risk !== "low" ? (
                      <span className={`badge ${i.integrity_risk === "high" ? "rejected" : "pending"}`} title={`${i.integrity_risk} fraud risk`} style={{ marginLeft: 6, fontSize: 10 }}>⚠</span>
                    ) : null}
                  </td>
                  <td>{i.driver_name}</td>
                  <td className="mono">{fmt(i.captured_at_utc)}</td>
                  <td><ScoreBar score={i.overall_score} /></td>
                  <td><SourceBadge source={i.decision_source} /></td>
                  <td><StatusBadge status={i.status} /></td>
                </tr>
              ))}
              {loading && items.length === 0
                ? Array.from({ length: 5 }).map((_, i) => (
                    <tr key={`sk-${i}`}>
                      {Array.from({ length: 7 }).map((__, j) => (
                        <td key={j}><div className="skeleton" style={{ height: 16 }} /></td>
                      ))}
                    </tr>
                  ))
                : null}
              {!loading && items.length === 0 ? (
                <tr><td colSpan={7} className="dim" style={{ textAlign: "center", padding: 32 }}>
                  {status ? `No ${status} inspections.` : "No inspections match these filters."}
                </td></tr>
              ) : null}
            </tbody>
          </table>
        </div>

        <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 16 }}>
          <button className="ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Prev</button>
          <span className="mono dim">page {page} / {pages} ({total} total)</span>
          <button className="ghost" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</button>
          <span className="mono dim" style={{ marginLeft: "auto" }}>auto-refreshing every 2s</span>
        </div>
      </div>
    </>
  );
}
