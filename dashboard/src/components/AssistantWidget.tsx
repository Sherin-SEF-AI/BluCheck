"use client";
import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { askAssistant, executeAssistantAction, getToken, type AssistantMsg, type PendingAction } from "@/lib/api";

type ChatItem = AssistantMsg & { pending?: PendingAction[]; done?: string[] };

// Page-aware recommended actions shown in the empty state.
function suggestionsFor(page: string): string[] {
  if (page.startsWith("/inspection")) return ["Why was this rejected?", "Reprocess this inspection", "Notify this driver"];
  if (page.startsWith("/metrics")) return ["Which vehicles are overdue?", "Send reminders to overdue drivers", "Set cadence to 12 hours"];
  if (page.startsWith("/model")) return ["How is the agent doing vs humans?", "Analyze the overrides", "Switch to shadow mode"];
  if (page.startsWith("/fleet")) return ["Worst-performing vehicles", "Which vehicles are overdue?"];
  if (page.startsWith("/inspections")) return ["Summarise today's rejections", "Which vehicles are overdue?"];
  return ["Give me a fleet overview", "Which vehicles are overdue?", "How is the agent doing vs humans?"];
}

// ---- tiny, self-contained markdown renderer (bold, code, headings, bullets, tables) ----
function inline(text: string, key: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0, m: RegExpExecArray | null, i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) parts.push(<strong key={`${key}-b${i}`}>{tok.slice(2, -2)}</strong>);
    else parts.push(<code key={`${key}-c${i}`} className="asst-code">{tok.slice(1, -1)}</code>);
    last = m.index + tok.length; i++;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function renderMarkdown(text: string): React.ReactNode {
  const lines = text.replace(/\r/g, "").split("\n");
  const out: React.ReactNode[] = [];
  let i = 0, k = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    // Table: consecutive lines with a pipe.
    if (line.includes("|") && i + 1 < lines.length && /^[\s|:-]+$/.test(lines[i + 1])) {
      const head = line.split("|").map((c) => c.trim()).filter((_, idx, a) => !(idx === 0 && a[0] === "") && !(idx === a.length - 1 && a[a.length - 1] === ""));
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes("|")) {
        rows.push(lines[i].split("|").map((c) => c.trim()).filter((_, idx, a) => !(idx === 0 && a[0] === "") && !(idx === a.length - 1 && a[a.length - 1] === "")));
        i++;
      }
      out.push(
        <div key={k++} className="asst-tablewrap"><table className="asst-table"><thead><tr>{head.map((h, hi) => <th key={hi}>{inline(h, `h${hi}`)}</th>)}</tr></thead>
          <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, ci) => <td key={ci}>{inline(c, `r${ri}c${ci}`)}</td>)}</tr>)}</tbody></table></div>
      );
      continue;
    }
    // Heading.
    const h = /^(#{1,3})\s+(.*)$/.exec(line);
    if (h) { out.push(<div key={k++} className="asst-h">{inline(h[2], `hd${k}`)}</div>); i++; continue; }
    // Bullet list.
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*[-*]\s+/, "")); i++; }
      out.push(<ul key={k++} className="asst-ul">{items.map((it, ii) => <li key={ii}>{inline(it, `li${k}-${ii}`)}</li>)}</ul>);
      continue;
    }
    out.push(<p key={k++} className="asst-p">{inline(line, `p${k}`)}</p>);
    i++;
  }
  return out;
}

export default function AssistantWidget() {
  const pathname = usePathname();
  const [mounted, setMounted] = useState(false);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => setMounted(true), []);
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" }); }, [items, busy]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!mounted || pathname === "/login" || !getToken()) return null;

  const inspId = typeof window !== "undefined" && pathname.startsWith("/inspection")
    ? (new URLSearchParams(window.location.search).get("id") ?? undefined) : undefined;
  const context = { page: pathname, inspection_id: inspId };

  async function send(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    const history: AssistantMsg[] = [...items.map((m) => ({ role: m.role, content: m.content })), { role: "user", content: q }];
    setItems((prev) => [...prev, { role: "user", content: q }]);
    setInput("");
    setBusy(true);
    try {
      const reply = await askAssistant(history, context);
      setItems((prev) => [...prev, { role: "assistant", content: reply.answer, pending: reply.pending_actions, done: [] }]);
    } catch (e) {
      setItems((prev) => [...prev, { role: "assistant", content: e instanceof Error ? `⚠️ ${e.message}` : "⚠️ Something went wrong." }]);
    } finally {
      setBusy(false);
    }
  }

  async function confirmAction(itemIdx: number, action: PendingAction) {
    const key = `${itemIdx}:${action.tool}`;
    setConfirming(key);
    try {
      const res = await executeAssistantAction(action.tool, action.args);
      setItems((prev) => prev.map((m, i) => i === itemIdx
        ? { ...m, pending: (m.pending ?? []).filter((p) => p !== action), done: [...(m.done ?? []), res.message || (res.ok ? "Done." : "Failed.")] }
        : m));
    } catch (e) {
      setItems((prev) => prev.map((m, i) => i === itemIdx
        ? { ...m, done: [...(m.done ?? []), e instanceof Error ? `⚠️ ${e.message}` : "⚠️ Action failed."] } : m));
    } finally {
      setConfirming(null);
    }
  }
  function cancelAction(itemIdx: number, action: PendingAction) {
    setItems((prev) => prev.map((m, i) => i === itemIdx
      ? { ...m, pending: (m.pending ?? []).filter((p) => p !== action), done: [...(m.done ?? []), "Cancelled."] } : m));
  }

  return (
    <>
      {!open ? (
        <button className="asst-launcher" onClick={() => setOpen(true)} title="Ask the assistant" aria-label="Open assistant">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M9.5 3.2c.16 3.9 3.4 7.14 7.3 7.3-3.9.16-7.14 3.4-7.3 7.3-.16-3.9-3.4-7.14-7.3-7.3 3.9-.16 7.14-3.4 7.3-7.3z" />
            <path d="M18.2 13.5c.09 2 1.75 3.66 3.75 3.75-2 .09-3.66 1.75-3.75 3.75-.09-2-1.75-3.66-3.75-3.75 2-.09 3.66-1.75 3.75-3.75z" />
          </svg>
        </button>
      ) : null}

      {open ? (
        <div className="asst-overlay" onClick={(e) => { if (e.target === e.currentTarget) setOpen(false); }}>
          <div className="asst-window" role="dialog" aria-label="Assistant">
            <div className="asst-header">
              <span className="asst-title">✨ BluCheck Assistant</span>
              <span className="asst-sub">Ask about the fleet — I can also take actions</span>
              <button className="asst-close" onClick={() => setOpen(false)} aria-label="Close">✕</button>
            </div>

            <div className="asst-messages" ref={scrollRef}>
              {items.length === 0 ? (
                <div className="asst-empty">
                  <div className="asst-empty-title">How can I help?</div>
                  <div className="asst-chips">
                    {suggestionsFor(pathname).map((s) => <button key={s} className="asst-chip" onClick={() => send(s)}>{s}</button>)}
                  </div>
                </div>
              ) : null}
              {items.map((m, i) => (
                <div key={i} className={`asst-msg ${m.role}`}>
                  <div className={`asst-bubble ${m.role}`}>
                    {m.role === "assistant" ? renderMarkdown(m.content) : m.content}
                    {m.pending && m.pending.length > 0 ? (
                      <div className="asst-confirms">
                        {m.pending.map((a, ai) => (
                          <div key={ai} className="asst-confirm">
                            <div className="asst-confirm-title">⚠ {a.title}</div>
                            {a.detail ? <div className="asst-confirm-detail">{a.detail}</div> : null}
                            <div className="asst-confirm-btns">
                              <button className="ghost" disabled={confirming !== null} onClick={() => cancelAction(i, a)}>Cancel</button>
                              <button disabled={confirming !== null} onClick={() => confirmAction(i, a)}>
                                {confirming === `${i}:${a.tool}` ? "Working…" : "Confirm"}
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    ) : null}
                    {m.done && m.done.length > 0 ? (
                      <div className="asst-actions">
                        {m.done.map((d, di) => <span key={di} className="asst-action">✓ {d}</span>)}
                      </div>
                    ) : null}
                  </div>
                </div>
              ))}
              {busy ? <div className="asst-msg assistant"><div className="asst-bubble assistant asst-typing"><span></span><span></span><span></span></div></div> : null}
            </div>

            <form className="asst-input" onSubmit={(e) => { e.preventDefault(); send(input); }}>
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask anything… e.g. who's overdue, set cadence to 12 hours"
                autoFocus
              />
              <button type="submit" disabled={busy || !input.trim()}>Send</button>
            </form>
          </div>
        </div>
      ) : null}
    </>
  );
}
