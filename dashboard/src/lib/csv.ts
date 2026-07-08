// Safe CSV construction. Two hazards handled:
//  1. Delimiter/quote/newline breakage: any cell containing , " or a newline is quoted and
//     inner quotes are doubled per RFC 4180.
//  2. CSV/formula injection: a cell that starts with = + - @ (or tab/CR) can execute in Excel/
//     Sheets. We neutralize it by prefixing a single quote so the value is shown, not run.
export function csvCell(value: unknown): string {
  let s = value == null ? "" : String(value);
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  if (/[",\n\r]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
  return s;
}

export function csvRow(cells: unknown[]): string {
  return cells.map(csvCell).join(",");
}

export function toCsv(header: unknown[], rows: unknown[][]): string {
  return [csvRow(header), ...rows.map(csvRow)].join("\n");
}
