/** Where an answer's pictures go: after the paragraph or step that is about them, instead of all
 *  under the answer. Nothing here knows a particular manual: a picture is placed by the words shown
 *  in it (its caption, and the labels its description quotes: "Add Folder", "Save") and by the
 *  answer's source markers ("[2]" after the text taken from source 2). */

export type Block = { start: number; end: number; text: string };

export type FigureText = {
  citation: number; // index into the answer's citations
  caption?: string | null;
  description?: string | null;
};

const LIST_ITEM = /^ {0,3}(?:[-*+]|\d{1,3}[.)])\s/;
const FENCE = /^ {0,3}(```|~~~)/;
const HEADING = /^ {0,3}#{1,6}\s/;
const TABLE_ROW = /^ {0,3}\|/;

/** The answer's top-level Markdown blocks (paragraphs, list items with what is nested under them,
 *  headings, tables, code), with their place in the text, so the text can be cut between them. */
export function blocksOf(text: string): Block[] {
  const lines = text.split("\n");
  const blocks: Block[] = [];
  let pos = 0;
  let cur: { start: number; end: number; kind: string } | null = null;
  let fence: string | null = null;
  const close = () => {
    if (cur) blocks.push({ start: cur.start, end: cur.end, text: text.slice(cur.start, cur.end) });
    cur = null;
  };
  for (const line of lines) {
    const start = pos;
    const end = pos + line.length;
    pos = end + 1;
    if (fence) {
      cur!.end = end;
      if (line.trim().startsWith(fence)) {
        fence = null;
        close();
      }
      continue;
    }
    const f = FENCE.exec(line);
    if (f) {
      close();
      fence = f[1];
      cur = { start, end, kind: "code" };
      continue;
    }
    if (!line.trim()) {
      // a blank line ends a paragraph; a list item goes on if the next line is indented under it
      if (cur && cur.kind !== "item") close();
      continue;
    }
    const indented = /^\s{2,}/.test(line);
    if (HEADING.test(line)) {
      close();
      blocks.push({ start, end, text: line });
      continue;
    }
    if (TABLE_ROW.test(line)) {
      if (cur?.kind !== "table") {
        close();
        cur = { start, end, kind: "table" };
      }
      cur!.end = end;
      continue;
    }
    if (LIST_ITEM.test(line) && !indented) {
      close();
      cur = { start, end, kind: "item" };
      continue;
    }
    if (cur?.kind === "item") {
      // under the item (indented, or a lazy continuation of its line): part of it; after a blank
      // line, unindented text is a new paragraph
      const gap = text.slice(cur.end, start).includes("\n\n");
      if (indented || !gap) {
        cur.end = end;
        continue;
      }
      close();
    }
    if (cur && cur.kind !== "para") close();
    if (!cur) cur = { start, end, kind: "para" };
    cur.end = end;
  }
  close();
  return blocks;
}

const STOP = new Set(
  ("a an the and or of to in on at for with by from is are be as it its this that these those into " +
    "click select open press choose then window dialog button icon tab menu option field box screen " +
    "image picture screenshot figure shows shown")
    .split(" "),
);

const words = (s: string) =>
  (s.toLowerCase().match(/[a-z0-9]+(?:[_'-][a-z0-9]+)*/g) ?? []).filter((w) => !STOP.has(w));

/** What a picture shows, as phrases an answer about it would use: its caption and the labels its
 *  description quotes. */
export function labelsOf(f: FigureText): string[][] {
  // "Save", “Save” or 'Save' (not the apostrophes of "user's" and "users'")
  const quoted = [
    ...(f.description ?? "").matchAll(/["“]([^"”\n]{2,40})["”]|(?<![\w'])['‘]([^'’\n]{2,40})['’](?![\w'])/g),
  ].map((m) => m[1] ?? m[2]);
  const shows = /Shows: (.*)$/.exec(f.description ?? "");
  const listed = shows ? shows[1].split(",") : [];
  const out = new Map<string, string[]>();
  for (const phrase of [f.caption ?? "", ...quoted, ...listed]) {
    const w = words(phrase);
    if (w.length && w.join("").length >= 3) out.set(w.join(" "), w);
  }
  return [...out.values()];
}

const has = (block: string[], phrase: string[]) => {
  for (let i = 0; i + phrase.length <= block.length; i++) {
    if (phrase.every((w, j) => block[i + j] === w)) return true;
  }
  return false;
};

/** For each picture, the block it goes after; -1: under the answer, as before (nothing in the
 *  answer is about it). Pictures keep their order: a manual shows them in the order its steps
 *  need them, and the answer follows the same steps. */
export function placeFigures(blocks: Block[], figures: FigureText[]): number[] {
  if (!blocks.length || !figures.length) return figures.map(() => -1);
  const blockWords = blocks.map((b) => words(b.text));
  const labels = figures.map(labelsOf);
  // a label every picture shows (the toolbar of each screenshot) says nothing about which is meant
  const df = new Map<string, number>();
  for (const ls of labels) for (const l of new Set(ls.map((p) => p.join(" ")))) df.set(l, (df.get(l) ?? 0) + 1);
  const score = figures.map((f, fi) =>
    blocks.map((b, bi) => {
      let s = 0;
      for (const p of labels[fi]) {
        if (has(blockWords[bi], p)) s += (p.length > 1 ? 2 : 1) / df.get(p.join(" "))!;
      }
      if (b.text.includes(`[${f.citation + 1}]`)) s += 1.5;
      return s;
    }),
  );
  // best placement keeping the pictures' order (a picture may share a block with the one before it)
  const n = figures.length;
  const m = blocks.length;
  const best: number[][] = Array.from({ length: n }, () => new Array(m).fill(0));
  const from: number[][] = Array.from({ length: n }, () => new Array(m).fill(0));
  for (let i = 0; i < n; i++) {
    let runMax = -Infinity;
    let runArg = 0;
    for (let j = 0; j < m; j++) {
      const prev = i ? best[i - 1][j] : 0;
      if (prev > runMax) {
        runMax = prev;
        runArg = j;
      }
      best[i][j] = runMax + score[i][j];
      from[i][j] = runArg;
    }
  }
  // ties go to the earliest block: a picture stands right after the first text about it
  let j = 0;
  for (let k = 1; k < m; k++) if (best[n - 1][k] > best[n - 1][j]) j = k;
  const at = new Array<number>(n);
  for (let i = n - 1; i >= 0; i--) {
    at[i] = j;
    j = from[i][j];
  }
  // a picture nothing in the answer is about stays under it, unless the pictures around it were
  // placed (then it goes with the one before it, in the manual's order)
  const placed = at.map((_, i) => score[i][at[i]] > 0);
  if (!placed.some(Boolean)) return figures.map(() => -1);
  return at.map((b, i) => {
    if (placed[i]) return b;
    const before = placed.slice(0, i).lastIndexOf(true);
    const after = placed.indexOf(true, i + 1);
    return before >= 0 && after >= 0 ? at[before] : -1;
  });
}
