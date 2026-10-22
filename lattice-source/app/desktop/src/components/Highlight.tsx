import { ReactNode } from "react";

function escape(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Regex matching any of the terms: numbers only as whole numbers ("27.8" does not match inside
 *  "127.85", "22" not inside "22H2"), words case-insensitively from their start ("rate" not in "Corporate"). */
export function highlightPattern(terms: string[]): RegExp | null {
  const clean = [...new Set(terms.map((t) => t.trim()).filter((t) => t.length > 1 || /^\d$/.test(t)))];
  if (!clean.length) return null;
  const pattern = clean
    .sort((a, b) => b.length - a.length)
    .map((t) =>
      /^[\d.,]+$/.test(t)
        ? `(?<![\\p{L}\\d.,])(?<!\\p{L}[\\p{L}\\d]*-)${escape(t)}(?![\\p{L}\\d]|[.,]\\d)`
        : `(?<![\\p{L}\\p{N}])${escape(t)}`,
    )
    .join("|");
  return new RegExp(`(${pattern})`, "giu");
}

/** Renders text with every occurrence of the given terms wrapped in <mark>. */
export default function Highlight({ text, terms }: { text: string; terms: string[] }) {
  const re = highlightPattern(terms);
  if (!re) return <>{text}</>;
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(re)) {
    const i = m.index ?? 0;
    if (i > last) out.push(text.slice(last, i));
    out.push(<mark key={i}>{m[0]}</mark>);
    last = i + m[0].length;
  }
  out.push(text.slice(last));
  return <>{out}</>;
}
