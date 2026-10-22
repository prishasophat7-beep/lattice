import { FigureThumb } from "./AnswerCard";
import { Fragment, useEffect, useRef, useState } from "react";
import { IconExternalLink, IconFileText, IconX } from "@tabler/icons-react";
import { api, ApiError, SectionView } from "../api";
import type { SourceTarget } from "../types";
import Highlight from "./Highlight";
import Markdown from "./Markdown";

/** Splits page-based section text ("[page 10]\n...") into page blocks. */
function pageBlocks(text: string): { page: number | null; body: string }[] {
  const parts = text.split(/\[page (\d+)\]\n?/);
  if (parts.length === 1) return [{ page: null, body: text }];
  const out: { page: number | null; body: string }[] = [];
  if (parts[0].trim()) out.push({ page: null, body: parts[0] });
  for (let i = 1; i < parts.length; i += 2) out.push({ page: Number(parts[i]), body: parts[i + 1] ?? "" });
  return out;
}

/** Markdown text split at its "[Figure]" lines, where a web-platform export had a picture. */
function figureChunks(text: string): string[] {
  return text.split(/^\[Figure(?::[^\]\n]*)?\]\s*$/m);
}

/** PDF text keeps the page's line breaks and tabs; tidy it for reading without losing lines. */
function tidy(body: string): string {
  return body.replace(/\t+/g, " ").replace(/ {2,}/g, " ").trim();
}

export default function SourcePanel({ target, onClose }: { target: SourceTarget; onClose: () => void }) {
  const [section, setSection] = useState<SectionView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pdf, setPdf] = useState<{ url: string; page: number } | null>(null);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);

  // Open at the section's title; scroll (this panel only, not the window) to the first highlighted
  // figure or word only when it is out of view: it is often far down a long page.
  useEffect(() => {
    let pane = bodyRef.current?.parentElement ?? null; // the panel's scrolling box
    while (pane && !/(auto|scroll)/.test(getComputedStyle(pane).overflowY)) pane = pane.parentElement;
    if (!pane) return;
    pane.scrollTop = 0;
    const mark = bodyRef.current?.querySelector("mark");
    if (!mark) return;
    const top = mark.getBoundingClientRect().top - pane.getBoundingClientRect().top;
    if (top > pane.clientHeight * 0.7) pane.scrollTop = top - pane.clientHeight / 3;
  }, [section]);

  useEffect(() => {
    let cancelled = false;
    setSection(null);
    setError(null);
    setPdfError(null); // an earlier section's "could not open the PDF"
    api.section(target.docId, target.nodeId).then(
      (s) => !cancelled && setSection(s),
      (e) =>
        !cancelled &&
        setError(
          e instanceof ApiError && e.status === 404
            ? "This section is not in the current knowledge base. It was probably cited from an earlier version."
            : e instanceof ApiError
              ? e.message
              : String(e),
        ),
    );
    return () => {
      cancelled = true;
    };
  }, [target.docId, target.nodeId]);

  // Free the PDF's memory when it is closed or replaced; Escape closes it
  useEffect(() => {
    if (!pdf) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setPdf(null);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      URL.revokeObjectURL(pdf.url);
    };
  }, [pdf]);

  const openPdf = async (page: number) => {
    setPdfError(null);
    try {
      setPdf({ url: await api.originalUrl(target.docId), page });
    } catch (e) {
      setPdfError(e instanceof ApiError ? e.message : String(e));
    }
  };

  // pictures are shown where the text had them; any left over are listed after the text
  const images = section?.images ?? [];
  // without the picture files (an export imported without them), each picture is a note of what it shows
  const chunks =
    section?.kind === "markdown"
      ? images.length
        ? figureChunks(section.text)
        : [section.text.replace(/^\[Figure(?::[ \t]*([^\]\n]*))?\][ \t]*$/gm, (_, d: string | undefined) => (d ? `*(Picture: ${d.trim()})*` : ""))]
      : [];
  const inline = images.slice(0, Math.max(0, chunks.length - 1));
  const rest = images.slice(inline.length);

  return (
    <aside className="source-panel" aria-label="Source">
      <div className="panel-bar">
        <span className="panel-title">
          <IconFileText size="1.15em" aria-hidden />
          Source{section?.pages ? ` · ${section.locator}` : ""}
        </span>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close source">
          <IconX size="1.15em" />
        </button>
      </div>

      {error && <p className="notice notice-bad">{error}</p>}
      {!section && !error && <p className="meta">Loading...</p>}

      {section && (
        <div className="source-body" ref={bodyRef}>
          <p className="meta">{section.doc_title}</p>
          {section.path.length > 1 && <p className="crumbs">{section.path.join(" › ")}</p>}
          {section.has_original && section.pages && (
            <button type="button" className="btn btn-small" onClick={() => openPdf(section.pages![0])}>
              <IconExternalLink size="1.1em" aria-hidden />
              Open PDF at page {section.pages[0]}
            </button>
          )}
          {pdfError && <p className="notice notice-bad">{pdfError}</p>}
          {section.kind === "markdown" &&
            chunks.map((chunk, i) => (
              <Fragment key={i}>
                {chunk.trim() && <Markdown text={chunk} terms={target.highlight} />}
                {i < inline.length && (
                  <div className="figure-inline">
                    <FigureThumb
                      docId={section.doc_id}
                      image={inline[i].image}
                      page={inline[i].page}
                      doc={section.doc_title}
                      section={inline[i].section}
                      caption={inline[i].caption}
                      description={inline[i].description}
                    />
                  </div>
                )}
              </Fragment>
            ))}
          {rest.length > 0 && (
            <div className="figures" aria-label="Pictures in this section">
              <p className="side-label">Pictures in this section</p>
              <div className="figure-row">
                {rest.map((im) => (
                  <FigureThumb
                    key={im.image}
                    docId={section.doc_id}
                    image={im.image}
                    page={im.page}
                    doc={section.doc_title}
                    section={im.section}
                    caption={im.caption}
                    description={im.description}
                  />
                ))}
              </div>
            </div>
          )}
          {section.kind === "pages" && pageBlocks(section.text).map((b, i) => (
            <section key={i} className="page-block">
              {b.page !== null && (
                <div className="page-label">
                  <span>Page {b.page}</span>
                  {section.has_original && (
                    <button type="button" className="link-btn" onClick={() => openPdf(b.page!)}>
                      Open in PDF
                    </button>
                  )}
                </div>
              )}
              <p className="source-text-body">
                <Highlight text={tidy(b.body)} terms={target.highlight} />
              </p>
            </section>
          ))}
        </div>
      )}

      {pdf && (
        <div className="overlay" role="dialog" aria-modal="true" aria-label="Original PDF" onClick={() => setPdf(null)}>
          <div className="pdf-frame" onClick={(e) => e.stopPropagation()}>
            <div className="panel-bar">
              <span className="panel-title">
                {section?.doc_name} · page {pdf.page}
              </span>
              <button type="button" className="icon-btn" onClick={() => setPdf(null)} aria-label="Close PDF">
                <IconX size="1.15em" />
              </button>
            </div>
            <iframe title="Original PDF" src={`${pdf.url}#page=${pdf.page}&navpanes=0`} />
          </div>
        </div>
      )}
    </aside>
  );
}
