"use client";
import { useCallback, useEffect, useState } from "react";
import Nav from "@/components/Nav";
import StatusBadge from "@/components/StatusBadge";
import { Icon } from "@/components/Icon";
import { getToken, getCompliance, type Compliance } from "@/lib/api";
import { toCsv } from "@/lib/csv";
import { fmtIST } from "@/lib/time";

export default function CompliancePage() {
  const [data, setData] = useState<Compliance | null>(null);
  const [date, setDate] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "missing" | "done">("all");

  useEffect(() => {
    if (typeof window !== "undefined" && !getToken()) window.location.href = "/login/";
  }, []);

  const load = useCallback(async () => {
    try {
      setData(await getCompliance(date || undefined));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load");
    }
  }, [date]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, [load]);

  function exportCsv() {
    if (!data) return;
    const csv = toCsv(
      ["driver", "car_number", "inspected", "last_status", "last_inspection_at"],
      data.drivers.map((d) => [d.name, d.car_number ?? "", d.inspected ? "yes" : "no", d.last_status ?? "", d.last_inspection_at ?? ""]),
    );
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url; a.download = `blucheck-compliance-${data.date}.csv`; a.click();
    URL.revokeObjectURL(url);
  }

  const drivers = (data?.drivers ?? []).filter((d) =>
    filter === "all" ? true : filter === "missing" ? !d.inspected : d.inspected
  );
  const ratePct = data?.rate != null ? Math.round(data.rate * 100) : null;

  return (
    <>
      <Nav />
      <div className="container">
        <div className="page-head">
          <div>
            <h1>Daily inspection compliance</h1>
            <div className="sub">Who has submitted an inspection today. IST day · {data?.date ?? "…"}</div>
          </div>
          <div className="filters" style={{ marginBottom: 0 }}>
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} title="View a past day (IST)" />
            <button className="ghost" onClick={exportCsv}><Icon name="metrics" size={15} /> Export CSV</button>
          </div>
        </div>
        {err ? <div className="error" style={{ marginBottom: 12 }}>{err}</div> : null}

        <div className="tiles" style={{ marginBottom: 8 }}>
          <div className="tile"><div className="v">{data?.total_drivers ?? "—"}</div><div className="k">Active drivers</div></div>
          <div className="tile ok"><div className="v">{data?.inspected_count ?? "—"}</div><div className="k">Inspected today</div></div>
          <div className="tile bad"><div className="v">{data?.missing_count ?? "—"}</div><div className="k">Not yet inspected</div></div>
          <div className="tile"><div className="v">{ratePct != null ? `${ratePct}%` : "—"}</div><div className="k">Compliance rate</div></div>
        </div>

        {/* Compliance bar */}
        {data && data.total_drivers > 0 ? (
          <div className="card" style={{ marginBottom: 16 }}>
            <div style={{ display: "flex", height: 12, borderRadius: 6, overflow: "hidden", background: "var(--surface-raised)" }}>
              <div style={{ width: `${ratePct}%`, background: "var(--ok)" }} title={`${data.inspected_count} inspected`} />
              <div style={{ width: `${100 - (ratePct ?? 0)}%`, background: "var(--danger)", opacity: 0.55 }} title={`${data.missing_count} missing`} />
            </div>
          </div>
        ) : null}

        <div className="filters">
          {(["all", "missing", "done"] as const).map((f) => (
            <button key={f} className={filter === f ? "" : "ghost"} onClick={() => setFilter(f)}>
              {f === "all" ? "All" : f === "missing" ? "Not inspected" : "Inspected"}
            </button>
          ))}
        </div>

        <div className="card" style={{ padding: 0 }}>
          <table className="fit tbl-compliance">
            <thead><tr><th></th><th>Driver</th><th>Car</th><th>Today</th><th>Last inspection (IST)</th></tr></thead>
            <tbody>
              {drivers.map((d) => (
                <tr key={d.driver_id}>
                  <td style={{ width: 28 }}>
                    <span style={{ color: d.inspected ? "var(--ok)" : "var(--danger)", display: "inline-flex" }}>
                      <Icon name={d.inspected ? "compliance" : "power"} size={16} />
                    </span>
                  </td>
                  <td>{d.name}</td>
                  <td className="mono">{d.car_number ?? "-"}</td>
                  <td>{d.inspected ? (d.last_status ? <StatusBadge status={d.last_status} /> : <span className="badge approved">done</span>) : <span className="badge rejected">missing</span>}</td>
                  <td className="mono dim">{d.last_inspection_at ? fmtIST(d.last_inspection_at) : "—"}</td>
                </tr>
              ))}
              {drivers.length === 0 ? <tr><td colSpan={5} className="dim" style={{ textAlign: "center", padding: 28 }}>No drivers in this view.</td></tr> : null}
            </tbody>
          </table>
        </div>
        <div className="review-hint" style={{ marginTop: 12 }}>Auto-refreshing every 10s · IST business day.</div>
      </div>
    </>
  );
}
