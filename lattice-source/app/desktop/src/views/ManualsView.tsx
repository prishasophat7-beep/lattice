import { useEffect, useState } from "react";
import { IconBook, IconChevronRight, IconFileTypePdf, IconSearch } from "@tabler/icons-react";
import { api, ApiError, DocSummary, SearchHit, Toc } from "../api";
import Highlight from "../components/Highlight";

/** The same words, whatever the case, punctuation and spacing. */
function sameText(a: string, b: string): boolean {
  const plain = (s: string) => s.toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  return plain(a) === plain(b);
}

export default function ManualsView({
  docs,
  station,
  onOpenSection,
}: {
  docs: DocSummary[];
  station: string | null;
  onOpenSection: (docId: string, nodeId: string, highlight: string[]) => void;
}) {
  const shown = station ? docs.filter((d) => d.tags.includes(station)) : docs;
  const [selected, setSelected] = useState<string | null>(shown[0]?.doc_id ?? null);
  const [toc, setToc] = useState<Toc | null>(null);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!shown.some((d) => d.doc_id === selected)) setSelected(shown[0]?.doc_id ?? null);
  }, [shown, selected]);

  useEffect(() => {
    if (!selected) return setToc(null);
    let cancelled = false;
    api.toc(selected).then(
      (t) => {
        if (cancelled) return;
        setToc(t);
        setError(null);
      },
      (e) => !cancelled && setError(e instanceof ApiError ? e.message : String(e)),
    );
    return () => {
      cancelled = true;
    };
  }, [selected]);

  // Search as you type, after a short pause
  useEffect(() => {
    const text = q.trim();
    if (!text) return setHits(null);
    // a slower answer to an earlier search ("vp") must not replace the one for "vpn"; an error once
    // shown stayed on screen after every later search worked
    let cancelled = false;
    const timer = setTimeout(() => {
      api.search(text, station ? [station] : []).then(
        (h) => {
          if (cancelled) return;
          setHits(h);
          setError(null);
        },
        (e) => !cancelled && setError(e instanceof ApiError ? e.message : String(e)),
      );
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [q, station]);

  const terms = q.trim().split(/\s+/).filter((t) => t.length > 1);

  return (
    <div className="manuals-view">
      <div className="view-head">
        <h1>Manuals</h1>
        <label className="search-box" htmlFor="manual-search">
          <IconSearch size="1.15em" aria-hidden />
          <input
            id="manual-search"
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search sections: a word, a code, a name"
          />
        </label>
      </div>
      {error && <p className="notice notice-bad">{error}</p>}

      {hits !== null ? (
        <section className="search-results" aria-live="polite">
          <p className="meta">
            {hits.length === 0 ? "No sections match." : `${hits.length} section${hits.length === 1 ? "" : "s"} found`}
          </p>
          <ul className="hit-list">
            {hits.map((h) => (
              <li key={`${h.doc_id}-${h.node_id}`}>
                <button type="button" className="hit" onClick={() => onOpenSection(h.doc_id, h.node_id, terms)}>
                  <span className="hit-head">
                    <span className="src-title">{h.title}</span>
                    {h.kind === "pages" && <span className="src-loc">{h.locator}</span>}
                  </span>
                  <span className="meta">
                    {h.doc_title} › {h.path.join(" › ")}
                  </span>
                  <span className="hit-snippet">
                    <Highlight text={h.snippet} terms={terms} />
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : (
        <div className="manuals-split">
          <ul className="doc-list" aria-label="Manuals">
            {shown.map((d) => (
              <li key={d.doc_id}>
                <button
                  type="button"
                  className={`doc-item${selected === d.doc_id ? " active" : ""}`}
                  onClick={() => setSelected(d.doc_id)}
                >
                  <IconBook size="1.2em" aria-hidden />
                  <span className="doc-item-text">
                    <span className="doc-title">{d.title}</span>
                    {d.file && <span className="meta doc-file" title={d.file}>{d.file}</span>}
                    <span className="meta">
                      {d.sections} section{d.sections === 1 ? "" : "s"}{d.pages ? ` · ${d.pages} pages` : ""}
                      {d.has_original && (
                        <>
                          {" · "}
                          <IconFileTypePdf size="1em" aria-label="PDF included" />
                        </>
                      )}
                    </span>
                    {d.tags.length > 0 && (
                      <span className="tag-row">
                        {d.tags.map((t) => (
                          <span key={t} className="tag">
                            {t}
                          </span>
                        ))}
                      </span>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ul>

          <section className="toc" aria-label="Table of contents">
            {toc && (
              <>
                <h2>{toc.title}</h2>
                {toc.description && <p className="lede">{toc.description}</p>}
                <ol className="toc-list">
                  {toc.sections.map((s) => (
                    <li key={s.node_id} style={{ paddingLeft: `${s.depth * 1.25}rem` }}>
                      <button
                        type="button"
                        className="toc-item"
                        onClick={() => onOpenSection(toc.doc_id, s.node_id, [])}
                      >
                        <span className="toc-head">
                          <span className={s.depth === 0 ? "toc-title top" : "toc-title"}>{s.title}</span>
                          {toc.kind === "pages" && <span className="src-loc">{s.locator}</span>}
                          <IconChevronRight size="1em" className="source-open" aria-hidden />
                        </span>
                        {/* a summary that only repeats the title ("2. Safety" under "2. Safety") says nothing */}
                        {!s.summary_is_fallback && !sameText(s.summary, s.title) && (
                          <span className="toc-summary">{s.summary}</span>
                        )}
                      </button>
                    </li>
                  ))}
                </ol>
              </>
            )}
            {!toc && shown.length === 0 && <p className="meta">No manuals are tagged for this station.</p>}
          </section>
        </div>
      )}
    </div>
  );
}
