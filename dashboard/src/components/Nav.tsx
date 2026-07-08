"use client";
import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { clearSession, getModelVersion, getToken } from "@/lib/api";
import { Icon } from "@/components/Icon";

const LINKS = [
  { href: "/", label: "Agent", icon: "agent" },
  { href: "/inspections/", label: "Queue", icon: "queue" },
  { href: "/fleet/", label: "Fleet", icon: "fleet" },
  { href: "/metrics/", label: "Metrics", icon: "metrics" },
  { href: "/model/", label: "Performance", icon: "performance" },
  { href: "/manage/", label: "Manage", icon: "manage" },
  { href: "/audit/", label: "Audit", icon: "audit" },
];

const MODE_LABEL: Record<string, string> = {
  auto: "Full Auto",
  assist: "Semi Auto",
  shadow: "Manual",
  disabled: "Off",
};

export default function Nav() {
  const router = useRouter();
  const pathname = usePathname() || "/";
  const [theme, setTheme] = useState<"dark" | "light">("light");
  const [mode, setMode] = useState<string | null>(null);

  useEffect(() => {
    // Auth guard: every admin page renders <Nav/>, so this covers the whole dashboard. Without
    // a token, redirect to login before any admin UI or data loads.
    if (typeof window !== "undefined" && !getToken()) {
      window.location.href = "/login/";
      return;
    }
    const saved = (localStorage.getItem("blucheck.theme") as "dark" | "light") || "light";
    setTheme(saved);
    document.documentElement.setAttribute("data-theme", saved);
    getModelVersion().then((v) => setMode(v.mode)).catch(() => undefined);
  }, []);

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    localStorage.setItem("blucheck.theme", next);
    document.documentElement.setAttribute("data-theme", next);
  }

  function isActive(href: string) {
    if (href === "/") return pathname === "/" || pathname === "";
    return pathname.startsWith(href.replace(/\/$/, ""));
  }

  const modeOn = mode === "auto" || mode === "assist";

  return (
    <div className="nav">
      <span className="brand">
        <Icon name="logo" size={22} />
        BluCheck
        <span className={`dot ${modeOn ? "on" : ""}`} title={modeOn ? "Agent active" : "Agent idle"} />
      </span>
      {LINKS.map((l) => (
        <a key={l.href} href={l.href} className={`link ${isActive(l.href) ? "active" : ""}`}>
          <Icon name={l.icon} size={16} />
          <span>{l.label}</span>
        </a>
      ))}
      <span className="spacer" style={{ flex: 1 }} />
      {mode ? (
        <a href="/" className="src agent" style={{ marginRight: 8 }} title="Current autonomy mode">
          <Icon name={mode === "auto" ? "fullAuto" : mode === "assist" ? "semiAuto" : "manual"} size={14} />
          {MODE_LABEL[mode] ?? mode}
        </a>
      ) : null}
      <span className="icon-btn" onClick={toggleTheme} title="Toggle theme">
        <Icon name={theme === "dark" ? "sun" : "moon"} size={16} />
      </span>
      <span
        className="icon-btn"
        onClick={() => {
          clearSession();
          router.push("/login/");
        }}
        title="Sign out"
      >
        <Icon name="logout" size={16} />
      </span>
    </div>
  );
}
