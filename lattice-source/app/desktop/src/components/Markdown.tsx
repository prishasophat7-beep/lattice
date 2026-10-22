import ReactMarkdown, { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { highlightPattern } from "./Highlight";

type HastNode = { type: string; value?: string; tagName?: string; properties?: object; children?: HastNode[] };

/** Wraps matches of `re` in text nodes with <mark>, outside code blocks. */
function rehypeHighlight(re: RegExp | null) {
  return () => (tree: HastNode) => {
    if (!re) return;
    const walk = (node: HastNode) => {
      if (!node.children || node.tagName === "code") return;
      node.children = node.children.flatMap((child) => {
        if (child.type !== "text" || !child.value) {
          walk(child);
          return [child];
        }
        const out: HastNode[] = [];
        let last = 0;
        for (const m of child.value.matchAll(re)) {
          const i = m.index ?? 0;
          if (i > last) out.push({ type: "text", value: child.value.slice(last, i) });
          out.push({ type: "element", tagName: "mark", properties: {}, children: [{ type: "text", value: m[0] }] });
          last = i + m[0].length;
        }
        if (!out.length) return [child];
        if (last < child.value.length) out.push({ type: "text", value: child.value.slice(last) });
        return out;
      });
    };
    walk(tree);
  };
}

// Offline app: links and images inside manuals cannot be followed or loaded, so show their text.
const components: Components = {
  a: ({ children }) => <span className="md-link">{children}</span>,
  img: ({ alt }) => (alt ? <span className="md-img">[image: {alt}]</span> : null),
};

/** A Markdown manual section (headings, lists, tables), with the given terms highlighted.
 *  Raw HTML in the source is not rendered. */
export default function Markdown({ text, terms }: { text: string; terms: string[] }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight(highlightPattern(terms))]}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
