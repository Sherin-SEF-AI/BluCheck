"use client";
import { useCallback, useEffect, useState } from "react";
import Nav from "@/components/Nav";
import { API_BASE_URL, listApiKeys, createApiKey, revokeApiKey, type ApiKey, type ApiKeyCreated } from "@/lib/api";

function Copy({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button className="ghost" style={{ fontSize: 12 }} onClick={async () => {
      try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500); } catch { /* ignore */ }
    }}>{done ? "Copied ✓" : "Copy"}</button>
  );
}

function Code({ children }: { children: string }) {
  return (
    <div style={{ position: "relative" }}>
      <pre style={{ background: "var(--surface-raised)", border: "1px solid var(--border)", borderRadius: 8, padding: "12px 14px", overflowX: "auto", fontSize: 12.5, lineHeight: 1.55, margin: "8px 0" }}>{children}</pre>
      <div style={{ position: "absolute", top: 8, right: 8 }}><Copy text={children} /></div>
    </div>
  );
}

export default function DevelopersPage() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [name, setName] = useState("");
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setKeys((await listApiKeys()).keys); } catch (e) { setError(e instanceof Error ? e.message : "Failed to load keys"); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function doCreate() {
    if (name.trim().length < 1) return;
    setBusy(true); setError(null);
    try { setCreated(await createApiKey(name.trim())); setName(""); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "Failed to create key"); } finally { setBusy(false); }
  }
  async function doRevoke(k: ApiKey) {
    if (!window.confirm(`Revoke key "${k.name}"? Apps using it will stop working immediately.`)) return;
    setBusy(true); setError(null);
    try { await revokeApiKey(k.id); await load(); } catch (e) { setError(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); }
  }

  const base = API_BASE_URL;
  const KEY = created?.key ?? "blu_live_YOUR_KEY";

  return (
    <>
      <Nav />
      <div className="container">
        <h2>Developers</h2>
        <p className="dim" style={{ marginTop: -6 }}>Create an API key and integrate BluCheck&rsquo;s cleanliness AI into any app.</p>
        {error ? <div className="error">{error}</div> : null}

        {/* Create + list keys */}
        <div className="section-title" style={{ marginTop: 8 }}>API KEYS</div>
        <div className="card">
          <div className="filters" style={{ marginTop: 0, marginBottom: 0, alignItems: "center" }}>
            <input placeholder="Key name (e.g. Partner app, Zapier)" value={name} onChange={(e) => setName(e.target.value)} style={{ minWidth: 260 }} />
            <button disabled={busy || !name.trim()} onClick={doCreate}>Create key</button>
          </div>

          {created ? (
            <div style={{ marginTop: 14, border: "1px solid var(--accent)", borderRadius: 8, padding: "12px 14px", background: "rgba(26,60,162,0.06)" }}>
              <div style={{ fontWeight: 700, color: "var(--text)", marginBottom: 4 }}>Your new key — copy it now, it won&rsquo;t be shown again</div>
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <code style={{ fontSize: 14, background: "var(--surface-raised)", padding: "6px 10px", borderRadius: 6, wordBreak: "break-all" }}>{created.key}</code>
                <Copy text={created.key} />
                <button className="ghost" style={{ fontSize: 12 }} onClick={() => setCreated(null)}>Done</button>
              </div>
            </div>
          ) : null}

          {keys.length > 0 ? (
            <table style={{ marginTop: 14 }}>
              <thead><tr><th>Name</th><th>Key</th><th>Created</th><th>Last used</th><th></th></tr></thead>
              <tbody>
                {keys.map((k) => (
                  <tr key={k.id} style={{ opacity: k.active ? 1 : 0.5 }}>
                    <td>{k.name}</td>
                    <td className="mono">{k.key_prefix}…{k.active ? "" : <span className="badge rejected" style={{ marginLeft: 6 }}>revoked</span>}</td>
                    <td className="mono dim">{new Date(k.created_at).toLocaleDateString("sv-SE")}</td>
                    <td className="mono dim">{k.last_used_at ? new Date(k.last_used_at).toLocaleString("sv-SE", { timeZone: "Asia/Kolkata" }) : "—"}</td>
                    <td>{k.active ? <button className="ghost" style={{ fontSize: 12, color: "var(--danger)" }} onClick={() => doRevoke(k)}>Revoke</button> : null}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <div className="dim" style={{ marginTop: 12 }}>No keys yet. Create one to start integrating.</div>}
        </div>

        {/* Integration docs */}
        <div className="section-title">QUICKSTART</div>
        <div className="card">
          <p style={{ color: "var(--text)", marginTop: 0 }}>Send the key in the <code className="asst-code">X-API-Key</code> header. Base URL:</p>
          <Code>{base}</Code>

          <div className="review-hint" style={{ marginTop: 14 }}>SCORE VEHICLE IMAGES — POST /v1/score</div>
          <p className="dim" style={{ fontSize: 13, marginTop: 4 }}>Send 1–5 images (public URLs or base64). Returns an overall score, a clean/dirty/review decision, and a per-zone breakdown, judged by your active policy.</p>
          <Code>{`curl -X POST ${base}/v1/score \\
  -H "X-API-Key: ${KEY}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "images": [
      "https://example.com/car-interior.jpg",
      "https://example.com/car-exterior.jpg"
    ]
  }'`}</Code>
          <Code>{`// JavaScript
const res = await fetch("${base}/v1/score", {
  method: "POST",
  headers: { "X-API-Key": "${KEY}", "Content-Type": "application/json" },
  body: JSON.stringify({ images: [imageUrlOrBase64] }),
});
const result = await res.json();
// { is_vehicle, overall_score, decision: "clean"|"dirty"|"review", zones: [...] }`}</Code>

          <div className="review-hint" style={{ marginTop: 18 }}>LIST INSPECTIONS — GET /v1/inspections</div>
          <Code>{`curl "${base}/v1/inspections?status=rejected&limit=20" \\
  -H "X-API-Key: ${KEY}"`}</Code>

          <div className="review-hint" style={{ marginTop: 18 }}>ONE INSPECTION — GET /v1/inspections/{"{id}"}</div>
          <Code>{`curl "${base}/v1/inspections/INSPECTION_ID" \\
  -H "X-API-Key: ${KEY}"`}</Code>

          <p className="dim" style={{ fontSize: 12.5, marginTop: 14 }}>
            Zones: seats, floor_mats, dashboard_console, windows_glass, exterior_body, boot · Issues: trash, stain, dust, smudge, spill, mud.
            Scores are 0–100 (100 = spotless). A revoked key returns 401.
          </p>
        </div>
      </div>
    </>
  );
}
