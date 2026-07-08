"use client";
import type { ReactElement } from "react";

// One cohesive line-icon set: 24px grid, 1.75 stroke, rounded joins, currentColor.
// Custom-drawn so nothing looks like stock emoji and everything shares a visual language.
const ICONS: Record<string, ReactElement> = {
  // Brand mark: a check inside a soft rounded-square (app-tile feel).
  logo: (
    <>
      <rect x="3" y="3" width="18" height="18" rx="5.5" fill="currentColor" opacity="0.14" stroke="none" />
      <path d="M7.7 12.4l2.7 2.7 5.9-6.4" />
    </>
  ),
  // Agent: the AI "sparkle" glyph -- a large four-point spark with a small companion. Clean at
  // every size; the universal mark for an intelligent agent.
  agent: (
    <path
      fill="currentColor"
      stroke="none"
      d="M9.7 5.4 C9.7 9.66 13.04 13 17.3 13 C13.04 13 9.7 16.34 9.7 20.6 C9.7 16.34 6.36 13 2.1 13 C6.36 13 9.7 9.66 9.7 5.4 Z M18.2 3.3 C18.2 5.04 19.56 6.4 21.3 6.4 C19.56 6.4 18.2 7.76 18.2 9.5 C18.2 7.76 16.84 6.4 15.1 6.4 C16.84 6.4 18.2 5.04 18.2 3.3 Z"
    />
  ),
  // Queue: clipboard with lines.
  queue: (
    <>
      <rect x="5" y="4.5" width="14" height="16" rx="2.4" />
      <path d="M9 4.5a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 4.5v1.2a.8.8 0 0 1-.8.8H9.8a.8.8 0 0 1-.8-.8z" fill="currentColor" opacity="0.16" />
      <path d="M9 11h6M9 14.5h6M9 18h3.5" />
    </>
  ),
  // Metrics: bar chart.
  metrics: (
    <>
      <path d="M4.5 20h15" />
      <path d="M7.5 20v-6.5" />
      <path d="M12 20V6.5" />
      <path d="M16.5 20v-9.5" />
    </>
  ),
  // Performance: target.
  performance: (
    <>
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
    </>
  ),
  // Manage: sliders.
  manage: (
    <>
      <path d="M4 7.5h8" />
      <path d="M16 7.5h4" />
      <circle cx="14" cy="7.5" r="2" />
      <path d="M4 16.5h4" />
      <path d="M12 16.5h8" />
      <circle cx="10" cy="16.5" r="2" />
    </>
  ),
  // Audit: shield with check.
  audit: (
    <>
      <path d="M12 3l7 3v5.2c0 4.4-3 7.4-7 8.8-4-1.4-7-4.4-7-8.8V6z" />
      <path d="M9 12l2 2 4-4.2" />
    </>
  ),
  // Full auto: robot + spark (autonomous).
  fullAuto: (
    <>
      <rect x="4.5" y="9" width="12.5" height="10.5" rx="3" />
      <path d="M10.75 9V6.6" />
      <circle cx="10.75" cy="5.5" r="1.05" fill="currentColor" stroke="none" />
      <circle cx="8.4" cy="14.2" r="1.05" fill="currentColor" stroke="none" />
      <circle cx="13.1" cy="14.2" r="1.05" fill="currentColor" stroke="none" />
      <path d="M19 4l-1.3 2.4L20 7.4l-2.6 .5L17 10.4l-1.2-2.3L13.2 7.6l2.2-1z" fill="currentColor" stroke="none" />
    </>
  ),
  // Semi auto: half-filled circle (assisted).
  semiAuto: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor" stroke="none" opacity="0.9" />
    </>
  ),
  // Manual / human: person.
  manual: (
    <>
      <circle cx="12" cy="8" r="3.4" />
      <path d="M5.5 20c0-3.6 2.9-6.2 6.5-6.2s6.5 2.6 6.5 6.2" />
    </>
  ),
  human: (
    <>
      <circle cx="12" cy="8" r="3.4" />
      <path d="M5.5 20c0-3.6 2.9-6.2 6.5-6.2s6.5 2.6 6.5 6.2" />
    </>
  ),
  // Agent chip (used in the source badge): CPU.
  chip: (
    <>
      <rect x="7" y="7" width="10" height="10" rx="1.6" />
      <rect x="10.3" y="10.3" width="3.4" height="3.4" rx="0.7" fill="currentColor" stroke="none" />
      <path d="M10 7V4.4M14 7V4.4M10 19.6V17M14 19.6V17M7 10H4.4M7 14H4.4M19.6 10H17M19.6 14H17" />
    </>
  ),
  bolt: (
    <>
      <path d="M13 2.5L5 13.2h6l-1 8.3 9-11.4h-6z" fill="currentColor" stroke="none" opacity="0.92" />
    </>
  ),
  // Compliance: calendar with a check.
  compliance: (
    <>
      <rect x="4" y="5" width="16" height="16" rx="2.5" />
      <path d="M4 9.5h16M8 3.5v3M16 3.5v3" />
      <path d="M9 14.5l2 2 4-4" />
    </>
  ),
  // Fleet: car with proper wheels.
  fleet: (
    <>
      <path d="M3.4 13.4l1.7-4.1A2.2 2.2 0 0 1 7.2 8h9.6a2.2 2.2 0 0 1 2.1 1.3l1.7 4.1" />
      <path d="M3 13.4h18v3.1a1 1 0 0 1-1 1h-1.3M6.3 17.5H4a1 1 0 0 1-1-1v-3.1" />
      <circle cx="7.5" cy="17.4" r="1.7" />
      <circle cx="16.5" cy="17.4" r="1.7" />
    </>
  ),
  reinspect: (
    <>
      <path d="M20 11a8 8 0 1 0-.7 3.3" />
      <path d="M20 5v4h-4" />
    </>
  ),
  power: (
    <>
      <path d="M12 3v8" />
      <path d="M7.5 6.3a7 7 0 1 0 9 0" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2.2M12 19.8V22M2 12h2.2M19.8 12H22M4.9 4.9l1.6 1.6M17.5 17.5l1.6 1.6M19.1 4.9l-1.6 1.6M6.5 17.5l-1.6 1.6" />
    </>
  ),
  moon: <path d="M20.5 14.8A8.5 8.5 0 1 1 9.2 3.5a6.6 6.6 0 0 0 11.3 11.3z" />,
  logout: (
    <>
      <path d="M15 5.5H6.5A1.5 1.5 0 0 0 5 7v10a1.5 1.5 0 0 0 1.5 1.5H15" />
      <path d="M18 12H9.5M15.5 8.5L19 12l-3.5 3.5" />
    </>
  ),
};

export function Icon({ name, size = 18, className }: { name: string; size?: number; className?: string }) {
  const paths = ICONS[name];
  if (!paths) return null;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={{ display: "block", flexShrink: 0 }}
      aria-hidden="true"
    >
      {paths}
    </svg>
  );
}
