// Render timestamps in Indian Standard Time (Asia/Kolkata, UTC+5:30).
// sv-SE locale yields an ISO-like "YYYY-MM-DD HH:MM:SS" string.

export function fmtIST(ts: string | null): string {
  if (!ts) return "-";
  return new Date(ts).toLocaleString("sv-SE", { timeZone: "Asia/Kolkata" }) + " IST";
}

export function fmtTimeIST(ts: string | null): string {
  if (!ts) return "--:--:--";
  return new Date(ts).toLocaleTimeString("sv-SE", { timeZone: "Asia/Kolkata" });
}
