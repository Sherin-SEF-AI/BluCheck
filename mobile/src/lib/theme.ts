// BluRabbit brand palette: white-and-blue, shared with the dashboard. Clean white
// surfaces, deep royal-blue accent (#1a3ca2), monospace for data.
export const colors = {
  bg: "#f4f7fc",
  surface: "#ffffff",
  surfaceRaised: "#eef3fc",
  border: "#d8e1f0",
  text: "#0f1c3f",
  textDim: "#5a678a",
  accent: "#1a3ca2", // brand primary blue
  onAccent: "#ffffff", // text on solid accent
  ok: "#1f9d55",
  warn: "#c7891f",
  danger: "#d0433f",
  mono: "monospace",
};

export const statusColor: Record<string, string> = {
  uploading: colors.textDim,
  processing: colors.warn,
  pending: colors.accent,
  approved: colors.ok,
  rejected: colors.danger,
  failed: colors.danger,
};
