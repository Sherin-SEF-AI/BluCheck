"use client";
import { useCallback, useEffect, useState } from "react";
import Nav from "@/components/Nav";
import { listAudit, type AuditEntry } from "@/lib/api";
import { fmtIST } from "@/lib/time";

const fmt = (ts: string) => fmtIST(ts);

export default function AuditPage() {
  const [items, setItems] = useState<AuditEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [entity, setEntity] = useState("");
  const [action, setAction] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const pageSize = 50;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = { page: String(page), page_size: String(pageSize) };
      if (entity) params.entity = entity;
      if (action) params.action = action;
      const res = await listAudit(params);
      setItems(res.items);
      setTotal(res.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load the audit log");
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [page, entity, action]);
  useEffect(() => { load(); }, [load]);

  const pages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <>
      <Nav />
      <div className="container">
        <h2>Audit log</h2>
        <div className="filters">
          <select value={entity} onChange={(e) => { setPage(1); setEntity(e.target.value); }}>
            <option value="">All entities</option>
            <option value="inspection">inspection</option>
            <option value="vehicle">vehicle</option>
            <option value="user">user</option>
          </select>
          <input placeholder="Action contains (e.g. review_reject)" value={action} onChange={(e) => { setPage(1); setAction(e.target.value); }} />
          <button className="ghost" onClick={load}>Refresh</button>
        </div>
        {error ? <div className="error" style={{ marginBottom: 12 }}>{error}</div> : null}
        {loading ? <div className="dim" style={{ marginBottom: 12 }}>Loading…</div> : null}
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <table>
            <thead><tr><th>When (IST)</th><th>Action</th><th>Entity</th><th>Entity id</th><th>Detail</th></tr></thead>
            <tbody>
              {items.map((a) => (
                <tr key={a.id}>
                  <td className="mono">{fmt(a.created_at)}</td>
                  <td><span className="badge">{a.action}</span></td>
                  <td>{a.entity}</td>
                  <td className="mono" style={{ fontSize: 11 }}>{a.entity_id.slice(0, 8)}</td>
                  <td className="mono" style={{ fontSize: 11, maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {a.detail ? JSON.stringify(a.detail) : "-"}
                  </td>
                </tr>
              ))}
              {!loading && items.length === 0 ? (
                <tr><td colSpan={5} className="dim" style={{ textAlign: "center", padding: 24 }}>No audit entries.</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 16 }}>
          <button className="ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Prev</button>
          <span className="mono dim">page {page} / {pages} ({total} total)</span>
          <button className="ghost" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</button>
        </div>
      </div>
    </>
  );
}
