/* A small RFC 4180 CSV reader for lists typed or exported by hand (room
   registers), where a server round trip through core/ingest would add a
   batch for a file with no money in it. Handles quoted fields, doubled
   quotes, CRLF, a UTF-8 BOM, and sniffs ; or tab as the delimiter the way
   European Excel saves "CSV". Anything with money goes through the server. */

export interface CsvTable {
  headers: string[];
  rows: string[][];
  delimiter: string;
}

/** The delimiter with the most hits on the first line outside quotes. */
export function sniffDelimiter(text: string): string {
  const line = text.split(/\r?\n/, 1)[0] ?? "";
  let best = ",";
  let bestN = -1;
  for (const d of [",", ";", "\t", "|"]) {
    let n = 0;
    let q = false;
    for (const ch of line) {
      if (ch === '"') q = !q;
      else if (!q && ch === d) n++;
    }
    if (n > bestN) { best = d; bestN = n; }
  }
  return best;
}

export function parseCsv(input: string, delimiter?: string): CsvTable {
  const text = input.replace(/^﻿/, "");
  const d = delimiter ?? sniffDelimiter(text);
  const out: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let q = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') q = false;
      else cell += ch;
      continue;
    }
    if (ch === '"' && cell === "") q = true;
    else if (ch === d) { row.push(cell); cell = ""; }
    else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); cell = "";
      out.push(row); row = [];
    } else cell += ch;
  }
  if (cell !== "" || row.length) { row.push(cell); out.push(row); }
  const nonEmpty = out.filter(r => r.some(c => c.trim() !== ""));
  const [headers = [], ...rows] = nonEmpty;
  return { headers: headers.map(h => h.trim()), rows: rows.map(r => r.map(c => c.trim())), delimiter: d };
}

/** Lowercase, punctuation to spaces, collapsed: "Room No." -> "room no". */
export function normalizeHeader(h: string): string {
  return h.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

/** First header whose normalized form is one of the aliases; -1 when absent. */
export function findColumn(headers: readonly string[], aliases: readonly string[]): number {
  const norm = headers.map(normalizeHeader);
  for (const a of aliases) {
    const i = norm.indexOf(normalizeHeader(a));
    if (i >= 0) return i;
  }
  return -1;
}
