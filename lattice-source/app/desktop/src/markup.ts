/** Markdown a model may use in a Manuals answer: bold, a list on its own lines, a heading or a table. */
export const MARKUP = /\*\*[^*\n]+\*\*|^\s*(?:[-*] |\d+\. |#{1,4} |\|)/m;

/** Numbered steps a small model wrote on one line ("1. Go to Projects. 2. Enter a name.") as a list. */
export function stepsOnLines(text: string): string {
  if (!/^\s*1\. /.test(text) || !/ 2\. /.test(text)) return text;
  return text.replace(/ (\d{1,2})\. (?=\S)/g, (m, n: string) => (Number(n) >= 2 ? `\n${n}. ` : m));
}

/** Text without Markdown markers, for one-line previews ("**Bluefin** is ..." -> "Bluefin is ..."). */
export function plainText(text: string): string {
  return text
    .replace(/\*\*|__|`/g, "")
    .replace(/^\s*(?:#{1,6}|[-*]|\d+\.)\s+/gm, "")
    .replace(/\[(\d+)\]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}
