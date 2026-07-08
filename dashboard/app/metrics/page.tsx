"use client";
import { useEffect, useState } from "react";
import Nav from "@/components/Nav";
import { getMetrics, getTrends, type Metrics, type Trends } from "@/lib/api";

export default function MetricsPage() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [trends, setTrends] = useState<Trends | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [m, t] = await Promise.all([getMetrics(), getTrends()]);
        setMetrics(m);
        setTrends(t);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load metrics");
      }
    })();
  }, []);

  return (
    <>
      <Nav />
      <div className="container">
        <h2>Metrics</h2>
        {error ? <div className="error">{error}</div> : null}
        {!metrics ? (
          <div className="dim">Loading...</div>
        ) : (
          <>
            <div className="section-title">STATUS COUNTS</div>
            <div className="stat-row">
              {Object.entries(metrics.counts_by_status).map(([status, count]) => (
                <div className="stat" key={status}>
                  <div className="value">{count}</div>
                  <div className="label">{status}</div>
                </div>
              ))}
              {Object.keys(metrics.counts_by_status).length === 0 ? <div className="dim">No inspections yet.</div> : null}
            </div>

            <div className="section-title">AVERAGE TIME TO REVIEW</div>
            <div className="stat">
              <div className="value">
                {metrics.average_review_seconds != null
                  ? `${(metrics.average_review_seconds / 3600).toFixed(1)}h`
                  : "-"}
              </div>
              <div className="label">capture to decision</div>
            </div>

            {trends && trends.reviews_by_day.length > 0 ? (
              <>
                <div className="section-title">REVIEWS BY DAY</div>
                <div className="card">
                  <div style={{ display: "flex", alignItems: "flex-end", gap: 8, height: 120 }}>
                    {trends.reviews_by_day.map((d) => {
                      const max = Math.max(...trends.reviews_by_day.map((x) => x.approved + x.rejected), 1);
                      const total = d.approved + d.rejected;
                      return (
                        <div key={d.day} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }} title={`${d.day}: ${d.approved} approved, ${d.rejected} rejected`}>
                          <div style={{ width: "100%", display: "flex", flexDirection: "column", justifyContent: "flex-end", height: 90 }}>
                            <div style={{ height: `${(d.rejected / max) * 90}px`, background: "var(--danger)" }} />
                            <div style={{ height: `${(d.approved / max) * 90}px`, background: "var(--ok)" }} />
                          </div>
                          <div className="mono" style={{ fontSize: 9, color: "var(--text-dim)" }}>{d.day.slice(5)}</div>
                          <div className="mono" style={{ fontSize: 10 }}>{total}</div>
                        </div>
                      );
                    })}
                  </div>
                  <div className="mono dim" style={{ fontSize: 11, marginTop: 8 }}>green = approved · red = rejected</div>
                </div>

                <div className="section-title">PER DRIVER</div>
                <div className="card" style={{ padding: 0, overflow: "hidden" }}>
                  <table>
                    <thead><tr><th>Driver</th><th>Total</th><th>Approved</th><th>Rejected</th><th>Approval rate</th></tr></thead>
                    <tbody>
                      {trends.per_driver.map((p) => (
                        <tr key={p.driver}>
                          <td>{p.driver}</td>
                          <td className="mono">{p.total}</td>
                          <td className="mono">{p.approved}</td>
                          <td className="mono">{p.rejected}</td>
                          <td className="mono">{p.approval_rate != null ? `${Math.round(p.approval_rate * 100)}%` : "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            ) : null}

            <div className="section-title">REJECTS BY VEHICLE (repeat offenders)</div>
            <div className="card" style={{ padding: 0, overflow: "hidden" }}>
              <table>
                <thead>
                  <tr><th>Plate</th><th>Rejects</th></tr>
                </thead>
                <tbody>
                  {metrics.rejects_by_vehicle.map((r) => (
                    <tr key={r.vehicle_plate}>
                      <td className="mono">{r.vehicle_plate}</td>
                      <td className="mono">{r.rejects}</td>
                    </tr>
                  ))}
                  {metrics.rejects_by_vehicle.length === 0 ? (
                    <tr><td colSpan={2} className="dim" style={{ textAlign: "center", padding: 24 }}>No rejects recorded.</td></tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </>
  );
}
