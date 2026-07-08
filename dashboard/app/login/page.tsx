"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { login, saveSession } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await login(email.trim().toLowerCase(), password);
      if (res.role !== "admin") {
        setError("This dashboard is for administrators only.");
        return;
      }
      saveSession(res.access_token, res.role);
      router.push("/inspections/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="container" style={{ maxWidth: 380, marginTop: 120 }}>
      <h1 style={{ letterSpacing: 1 }}>BluCheck</h1>
      <p className="dim" style={{ marginTop: -8 }}>Administrator sign in</p>
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 24 }}>
        <input placeholder="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        <input placeholder="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        {error ? <div className="error">{error}</div> : null}
        <button type="submit" disabled={busy}>{busy ? "Signing in..." : "Sign in"}</button>
      </form>
    </div>
  );
}
