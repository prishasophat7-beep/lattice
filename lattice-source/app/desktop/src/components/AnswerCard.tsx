import { useEffect, useState } from "react";
import {
  IconAlertTriangle,
  IconBook2,
  IconCheck,
  IconChevronRight,
  IconCircleCheck,
  IconCopy,
  IconFileText,
  IconInfoCircle,
  IconMessages,
  IconPencil,
  IconRefresh,
  IconThumbDown,
  IconThumbUp,
  IconPaperclip,
  IconWorld,
  IconBulb,
  IconCut,
  IconRoute,
  IconTable,
  IconX,
} from "@tabler/icons-react";
import { api, ApiError, AskResult, Citation, Figure, Given, Note } from "../api";
import { LIST_ITEM, blocksOf, placeFigures } from "../figurePlace";
import { MARKUP, stepsOnLines } from "../markup";
import { figuresIn, highlightTermsIn } from "../types";
import Highlight from "./Highlight";
import Markdown from "./Markdown";

type Vote = "helpful" | "wrong";

const FLAGGED_SHOWN = 6; // figures named in "Check against the source"

const NOTE_ICONS: Record<Note["kind"], typeof IconCut> = {
  unsourced: IconAlertTriangle,
  cut_off: IconCut,
  partial_table: IconTable,
  mixed_parts: IconRoute,
};

/** A note on the answer, set apart from its text: the answer was cut off, named no sources, ... */
function NoteBadge({ note }: { note: Note }) {
  const Icon = NOTE_ICONS[note.kind] ?? IconInfoCircle;
  return (
    <span className={`note-badge note-${note.kind}`}>
      <Icon size="1.05em" aria-hidden />
      <span>{note.text}</span>
    </span>
  );
}

/** A heading as a button label: "2 Dashboard:" -> "2 Dashboard" */
function headingOf(title: string): string {
  return title.replace(/\s*:\s*$/, "");
}

export default function AnswerCard({
  result,
  onOpenSource,
  askedAt,
  station = null,
  onSearchAll,
  onAskChat,
  onRegenerate,
  onPickManuals,
  onEdit,
  onAskSection,
  onSomethingElse,
}: {
  result: AskResult;
  onOpenSource: (c: Citation) => void;
  askedAt?: string;
  /** Station filter the question was asked with */
  station?: string | null;
  onSearchAll?: () => void;
  /** Present when Chat mode is available: re-ask a question the manuals don't cover */
  onAskChat?: () => void;
  /** Present on the last answer: ask again / change the question, replacing this answer */
  onRegenerate?: () => void;
  /** Present on the last answer: search only the manuals picked after a clarifying question */
  /** question: a vague question's meaning, asked instead (see Clarification) */
  onPickManuals?: (docIds: string[], question?: string, section?: string) => void;
  onEdit?: () => void;
  /** Present on the last answer: ask about one section of a manual (the same step elsewhere, or
   * the closest section to a question the documents don't cover) */
  onAskSection?: (question: string, docId: string, section: string) => void;
  /** Present on the last answer: none of the options of a question back; type it instead */
  onSomethingElse?: () => void;
}) {
  const flagged = result.unverified_numbers ?? [];
  // figures the answer adds (a code like "E17" repeated from the question is not a figure to check)
  const asked = new Set(figuresIn(result.question));
  const hasFigures = !result.pictures_only && figuresIn(result.answer).some((f) => !asked.has(f));
  const seconds = result.seconds?.total;

  if (result.smalltalk) {
    return (
      <div className="answer">
        {result.mode === "chat" ? (
          <div className="answer-md">
            <Markdown text={result.answer} terms={[]} />
          </div>
        ) : (
          <p className="answer-text">{result.answer}</p>
        )}
      </div>
    );
  }

  if (result.clarify) {
    const all = result.clarify.options.flatMap((o) => o.doc_ids);
    return (
      <div className="answer answer-clarify">
        <p className="answer-text">{result.clarify.prompt}</p>
        <div className="clarify-options">
          {result.clarify.options.map((o) => (
            <button
              key={`${o.doc_ids.join()}-${o.section ?? o.label}`} // options of one manual share its id
              type="button"
              className="btn clarify-option"
              aria-label={`${o.label}, ${o.detail}`}
              disabled={!onPickManuals}
              onClick={() => onPickManuals?.(o.doc_ids, o.question ?? undefined, o.section ?? undefined)}
            >
              <IconBook2 size="1.15em" aria-hidden />
              <span>
                <span className="clarify-label">{o.label}</span>
                <span className="meta">{o.detail}</span>
              </span>
            </button>
          ))}
          {!result.clarify.options.some((o) => o.question) && (
            <button
              type="button"
              className="btn btn-ghost clarify-option"
              disabled={!onPickManuals}
              onClick={() => onPickManuals?.(all)}
            >
              Search all of these
            </button>
          )}
          {/* none of these: say it in your own words (picking the model's own "Something else - please
              clarify ..." option sent that text to it as the question) */}
          {onSomethingElse && (
            <button type="button" className="btn btn-ghost clarify-option" onClick={onSomethingElse}>
              Something else...
            </button>
          )}
        </div>
      </div>
    );
  }

  if (!result.found) {
    return (
      <div className="answer answer-notfound">
        <div className="answer-status status-caution">
          <IconInfoCircle size="1.15em" aria-hidden />
          Not in the documents
        </div>
        <p className="answer-text">{result.answer}</p>
        {result.considered.length > 0 && onAskSection ? (
          // not a dead end: the sections closest to the question, one click to read each
          <div className="section-chips" role="group" aria-label="The closest sections in the documents">
            <span className="meta">The closest I found:</span>
            {result.considered.map((c) => (
              <button
                key={`${c.doc_id}-${c.node_id}`}
                type="button"
                className="chip"
                title={`${c.doc_name} › ${c.locator}`}
                onClick={() => onAskSection(`What does "${headingOf(c.title)}" say?`, c.doc_id, c.node_id)}
              >
                {headingOf(c.title)}
              </button>
            ))}
          </div>
        ) : (
          result.considered.length > 0 && (
            <p className="meta">
              Checked: {result.considered.map((c) => `${c.title} (${c.pages ? c.locator : c.doc_name})`).join("; ")}
            </p>
          )
        )}
        {onAskChat && result.mode !== "chat" && (
          <p className="meta">
            <button type="button" className="link-btn" onClick={onAskChat}>
              <IconMessages size="1em" aria-hidden /> Ask in Chat mode
            </button>{" "}
            (may answer from general knowledge, marked as such)
          </p>
        )}
        {station && (
          <p className="meta">
            Only the documents for {station} were searched.
            {onSearchAll && (
              <>
                {" "}
                <button type="button" className="link-btn" onClick={onSearchAll}>
                  Search all documents
                </button>
              </>
            )}
          </p>
        )}
        <div className="answer-actions">
          {result.history_id && <Feedback historyId={result.history_id} given={result.feedback} />}
          <TurnActions onRegenerate={onRegenerate} onEdit={onEdit} />
          {(seconds !== undefined || askedAt) && (
            <span className="meta push">
              {askedAt ? new Date(askedAt).toLocaleString() : `${seconds!.toFixed(1)} s`}
            </span>
          )}
        </div>
      </div>
    );
  }

  const chat = result.mode === "chat";
  // wholly general knowledge (no sources); a general part next to cited facts is marked as a quote instead
  const files = result.files ?? [];
  // the engine says when it is not (general: false): the built-in model answers from the documents
  // only, so its answer without sources is unsourced, not general knowledge
  const general =
    chat && result.found && !result.smalltalk && result.citations.length === 0 && files.length === 0 &&
    result.general !== false;

  const body: { kind: "md" | "large" | "plain"; text: string } =
    chat || result.summary
      ? { kind: "md", text: result.answer }
      : MARKUP.test(result.answer)
        ? { kind: "large", text: stepsOnLines(result.answer) }
        : { kind: "plain", text: result.answer };
  // each picture after the paragraph or step about it; the rest under the answer, as before
  const figures = result.figures ?? [];
  const blocks = blocksOf(body.text);
  const at = result.pictures_only || general ? figures.map(() => -1) : placeFigures(blocks, figures);
  // inStep: the pictures belong to a step of a list, and line up with its text, not the card's edge
  const pieces: { text: string; figures: Figure[]; inStep?: boolean }[] = [];
  let from = 0;
  blocks.forEach((b, bi) => {
    const here = figures.filter((_, fi) => at[fi] === bi);
    if (here.length) {
      pieces.push({ text: body.text.slice(from, b.end).trim(), figures: here, inStep: LIST_ITEM.test(b.text) });
      from = b.end;
    }
  });
  const rest = body.text.slice(from).trim();
  if (rest || !pieces.length) pieces.push({ text: rest, figures: [] });
  const unplaced = figures.filter((_, fi) => at[fi] < 0);
  const thumb = (f: Figure) => {
    const c = result.citations[f.citation];
    return c ? (
      <FigureThumb
        key={`${c.doc_id}-${f.image ?? f.page}`}
        docId={c.doc_id}
        page={f.page}
        image={f.image}
        doc={c.doc_name}
        section={c.title}
        caption={f.caption}
        description={f.description}
      />
    ) : null;
  };

  return (
    <div className={`answer${general ? " answer-general" : ""}`}>
      {general && (
        <div className="answer-status status-caution" role="note">
          <IconWorld size="1.15em" aria-hidden />
          General answer, not from the company documents. Check before acting on it.
        </div>
      )}
      {result.worked_out && !general && (
        <div className="answer-status" role="note">
          <IconBulb size="1.15em" aria-hidden />
          Worked out from the documents: not stated in these words. Check the cited section.
        </div>
      )}
      {chat && result.found && files.length > 0 && (
        <div className="answer-status" role="note">
          <IconPaperclip size="1.15em" aria-hidden />
          From your attached {files.length === 1 ? "file" : "files"}: {files.join(", ")}
        </div>
      )}
      {flagged.length > 0 ? (
        <div className="answer-status status-caution" role="note">
          <IconAlertTriangle size="1.15em" aria-hidden />
          <span>
            {/* a long list (17 figures of a made-up column) named the first few, then how many more */}
            Check against the source: <strong>{flagged.slice(0, FLAGGED_SHOWN).join(", ")}</strong>
            {flagged.length > FLAGGED_SHOWN && ` and ${flagged.length - FLAGGED_SHOWN} more`}{" "}
            {flagged.length === 1 ? "does" : "do"} not appear in the document text
          </span>
        </div>
      ) : hasFigures && !general ? (
        <div className="answer-status status-ok">
          <IconCircleCheck size="1.15em" aria-hidden />
          Numbers match the document
        </div>
      ) : null}

      {pieces.map((p, i) => (
        <div key={i} className="answer-piece">
          {body.kind === "plain" ? (
            <p className="answer-text">{p.text}</p>
          ) : (
            // a Manuals answer with steps or bold text: shown formatted, at the size of a plain answer
            <div className={`answer-md${body.kind === "large" ? " answer-md-large" : ""}`}>
              <Markdown text={p.text} terms={[]} />
            </div>
          )}
          {p.figures.length > 0 && (
            <div className={`figure-row figure-row-inline${p.inStep ? " figure-row-step" : ""}`}
              aria-label="Figures from the document">
              {p.figures.map(thumb)}
            </div>
          )}
        </div>
      ))}

      {(result.notes?.length ?? 0) > 0 && (
        <div className="note-badges" role="note" aria-label="Notes on this answer">
          {result.notes!.map((n, i) => (
            <NoteBadge key={i} note={n} />
          ))}
        </div>
      )}

      {/* Manuals: the manual's own sentences; Chat: the quotes the assistant noted, checked */}
      {(result.evidence?.length ?? 0) > 0 && (
        <div className="evidence" aria-label="From the document">
          <p className="side-label">From the document</p>
          {result.evidence!.map((e, i) => {
            const c = result.citations[e.citation];
            return (
              <blockquote key={i}>
                {/* Markdown marks of the document ("*Showing first 10 rows.*", `code`), not its text;
                    "_" stays: column names such as response_time_ms have it */}
                <Highlight text={e.text.replace(/\*+|`+|__/g, "")} terms={highlightTermsIn(result.answer)} />
                {c && (
                  <button type="button" className="link-btn evidence-src" onClick={() => onOpenSource(c)}>
                    {c.title}
                    {c.pages ? ` · ${c.locator}` : ""}
                  </button>
                )}
              </blockquote>
            );
          })}
        </div>
      )}

      {unplaced.length > 0 && (
        <div className="figures" aria-label="Figures from the document">
          <p className="side-label">
            {result.pictures_only ? "Pictures" : unplaced.length < figures.length ? "More figures from the document" : "Figures from the document"}
          </p>
          <div className="figure-row">{unplaced.map(thumb)}</div>
        </div>
      )}

      {result.citations.length > 0 && (
        <ul className="sources" aria-label="Sources">
          {result.citations.map((c, i) => (
            <li key={`${c.doc_id}-${c.node_id}`}>
              <button type="button" className="source-row" onClick={() => onOpenSource(c)}>
                {chat ? <span className="src-num">{i + 1}</span> : <IconFileText size="1.15em" aria-hidden />}
                <SourceLabel c={c} />
                <IconChevronRight size="1.1em" className="source-open" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}

      {onAskSection && result.focus && (result.focus.elsewhere?.length ?? 0) > 0 && (
        // the same step in the manual's other parts ("Training" in Single X, Object Detection, ...)
        <div className="section-chips" role="group" aria-label="The same step in other parts of the manual">
          <span className="meta">Same step in:</span>
          {result.focus.elsewhere!.map((e) => (
            <button
              key={e.node_id}
              type="button"
              className="chip"
              onClick={() => {
                // "Training in Single X Classification" (older engines send no title: the heading)
                const step = e.title || result.focus!.path[result.focus!.path.length - 1];
                onAskSection(`${step} in ${e.label}`, result.focus!.doc_id, e.node_id);
              }}
            >
              {e.label}
            </button>
          ))}
        </div>
      )}

      <div className="answer-actions">
        {result.history_id && <Feedback historyId={result.history_id} given={result.feedback} />}
        <CopyButton text={copyText(result)} />
        <TurnActions onRegenerate={onRegenerate} onEdit={onEdit} />
        {(seconds !== undefined || askedAt) && (
          <span className="meta push">
            {askedAt ? new Date(askedAt).toLocaleString() : `${seconds!.toFixed(1)} s`}
          </span>
        )}
      </div>
    </div>
  );
}

/** "Manual › Section · page 10" for PDF manuals; "Manual › Chapter › Section" for Markdown ones. */
export function SourceLabel({ c }: { c: Citation }) {
  // a document's opening section is often titled with the document's own name: "leave_policy.pdf ›
  // leave_policy.pdf" said it twice
  const same = (t: string) => t.trim().toLowerCase() === c.doc_name.trim().toLowerCase();
  const parents = c.pages ? [] : c.locator.split(" > ").slice(0, -1).filter((p) => !same(p));
  const titled = !same(c.title);
  return (
    <span className="source-text">
      <span className={titled ? "src-doc" : "src-title"}>{c.doc_name}</span>
      {parents.map((p, i) => (
        <span key={i} className="src-parent">
          <span className="src-sep" aria-hidden="true">›</span> {p}
        </span>
      ))}
      {titled && <span className="src-sep" aria-hidden="true">›</span>}
      {titled && <span className="src-title">{c.title}</span>}
      {c.pages && <span className="src-loc">{c.locator}</span>}
    </span>
  );
}

/** A picture from the engine as an object URL; null while loading, "failed" on error. image: a
 * picture the web platform extracted; otherwise the PDF page (crop: only the figure on it). */
function usePageImage(
  docId: string,
  page: number | null | undefined,
  crop: boolean,
  enabled = true,
  image?: string | null,
): string | null | "failed" {
  const [src, setSrc] = useState<string | null | "failed">(null);
  useEffect(() => {
    if (!enabled) return;
    let url: string | null = null;
    let live = true;
    const load = image ? api.docImageUrl(docId, image) : api.pageImageUrl(docId, page ?? 1, crop);
    load.then(
      (u) => (live ? setSrc((url = u)) : URL.revokeObjectURL(u)),
      () => live && setSrc("failed"),
    );
    return () => {
      live = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [docId, page, crop, enabled, image]);
  return src;
}

/** What Copy puts on the clipboard: the answer, and the pictures shown with it by name. */
function copyText(result: AskResult): string {
  const pictures = (result.figures ?? []).flatMap((f) => {
    const c = result.citations[f.citation];
    if (!c) return [];
    const where = `${c.doc_name} › ${c.title}${f.page ? `, page ${f.page}` : ""}`;
    return [`- ${f.caption ? `${f.caption} (${where})` : where}`];
  });
  return pictures.length ? `${result.answer}\n\nPictures:\n${pictures.join("\n")}` : result.answer;
}

/** A cited page with a figure: the figure cut out of the page as a thumbnail, opening large. */
export function FigureThumb({
  docId,
  page,
  image,
  doc,
  section,
  caption,
  description,
}: {
  docId: string;
  page?: number | null;
  image?: string | null;
  doc: string;
  section: string;
  /** the picture's caption and what the export says it shows (web-platform exports) */
  caption?: string | null;
  description?: string | null;
}) {
  const figure = usePageImage(docId, page, true, true, image);
  const [open, setOpen] = useState(false);
  const [whole, setWhole] = useState(false);
  const pageImage = usePageImage(docId, page, false, open && whole && !image);
  // a picture the browser cannot draw (a damaged file in the knowledge base) left an empty white box
  const [broken, setBroken] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (figure === "failed" || broken) return null;
  const where = page ? ` · page ${page}` : "";
  const label = `${caption ? `${caption} · ` : ""}${doc} › ${section}${where}`;
  const shown = whole ? pageImage : figure;
  // what a screen reader says: the description when the export has one
  const alt = description || (caption ? `${caption}, in ${section}, ${doc}` : `Figure in ${section}, ${doc}`);
  return (
    <>
      <button type="button" className="figure-thumb" onClick={() => setOpen(true)} disabled={!figure}
        aria-label={`Show larger: ${caption || section}, ${doc}${where}`}
        title={description ? `${description}\n\nSelect to show larger` : "Show larger"}>
        <span className="figure-frame">
          {figure ? <img src={figure} alt={alt} onError={() => setBroken(true)} /> : <span className="spinner" aria-hidden="true" />}
        </span>
        <span className="figure-caption">
          <span className="figure-section">{caption || section}</span>
          <span className="meta figure-doc">
            {caption ? `${section} · ${doc}` : section.trim() === doc.trim() ? "" : doc}
            {where}
          </span>
        </span>
      </button>
      {open && (
        <div className="figure-view" role="dialog" aria-modal="true" aria-label={label} onClick={() => setOpen(false)}>
          <div className="figure-view-bar" onClick={(e) => e.stopPropagation()}>
            <span className="figure-view-title">{label}</span>
            {!image && (
            <div className="seg" role="radiogroup" aria-label="Show">
              <button type="button" role="radio" aria-checked={!whole} className={whole ? "" : "active"} onClick={() => setWhole(false)}>
                Figure
              </button>
              <button type="button" role="radio" aria-checked={whole} className={whole ? "active" : ""} onClick={() => setWhole(true)}>
                Whole page
              </button>
            </div>
            )}
            <button type="button" className="icon-btn" aria-label="Close" onClick={() => setOpen(false)}>
              <IconX size="1.2em" />
            </button>
          </div>
          <div className={`figure-view-body${whole ? " whole" : image ? " picture" : ""}`} onClick={(e) => e.stopPropagation()}>
            {shown && shown !== "failed" ? (
              <img src={shown} alt={whole ? `Page ${page} of ${doc}` : alt} />
            ) : shown === "failed" ? (
              <p className="notice notice-bad">The page could not be shown.</p>
            ) : (
              <span className="spinner" aria-hidden="true" />
            )}
          </div>
          {description && !whole && (
            <p className="figure-view-desc" onClick={(e) => e.stopPropagation()}>
              <span className="side-label">Description</span>
              {description}
            </p>
          )}
        </div>
      )}
    </>
  );
}

function TurnActions({ onRegenerate, onEdit }: { onRegenerate?: () => void; onEdit?: () => void }) {
  return (
    <>
      {onRegenerate && (
        <button type="button" className="btn btn-small btn-ghost" onClick={onRegenerate} title="Answer again">
          <IconRefresh size="1.1em" aria-hidden />
          Regenerate
        </button>
      )}
      {onEdit && (
        <button type="button" className="btn btn-small btn-ghost" onClick={onEdit} title="Change the question">
          <IconPencil size="1.1em" aria-hidden />
          Edit
        </button>
      )}
    </>
  );
}

/** Helpful / Wrong for an answer; given: the vote already on it (an answer reopened from history). A
 * vote can be changed: the latest one counts. */
function Feedback({ historyId, given }: { historyId: string; given?: Given | null }) {
  const [vote, setVote] = useState<Vote | null>(given?.rating ?? null);
  const [comment, setComment] = useState(given?.comment ?? "");
  const [state, setState] = useState<"idle" | "commenting" | "sending" | "sent" | "error">(given ? "sent" : "idle");
  const [error, setError] = useState("");

  const send = async (rating: Vote, text = "") => {
    setVote(rating);
    setState("sending");
    try {
      await api.feedback(historyId, rating, text);
      setState("sent");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setState("error");
    }
  };

  if (state === "sent") {
    return (
      <span className="meta feedback-done">
        <IconCheck size="1.1em" aria-hidden />
        {vote === "helpful" ? "Marked helpful" : "Marked wrong: noted for review by the knowledge-base team"}
        <button type="button" className="link-btn" onClick={() => setState("idle")}>
          Change
        </button>
      </span>
    );
  }

  if (state === "commenting") {
    return (
      <form
        className="feedback-form"
        onSubmit={(e) => {
          e.preventDefault();
          send("wrong", comment);
        }}
      >
        <label htmlFor={`fb-${historyId}`} className="visually-hidden">
          What is wrong with this answer
        </label>
        <input
          id={`fb-${historyId}`}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="What's wrong? (optional)"
          autoFocus
        />
        <button type="submit" className="btn btn-small">
          Send
        </button>
        <button type="button" className="btn btn-small btn-ghost" onClick={() => setState("idle")}>
          Cancel
        </button>
      </form>
    );
  }

  return (
    <span className="feedback">
      <button type="button" className="btn btn-small" disabled={state === "sending"} onClick={() => send("helpful")}>
        <IconThumbUp size="1.1em" aria-hidden />
        Helpful
      </button>
      <button
        type="button"
        className="btn btn-small"
        disabled={state === "sending"}
        onClick={() => setState("commenting")}
      >
        <IconThumbDown size="1.1em" aria-hidden />
        Wrong
      </button>
      {state === "error" && <span className="meta text-bad">{error}</span>}
    </span>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="btn btn-small"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          /* clipboard unavailable */
        }
      }}
    >
      {copied ? <IconCheck size="1.1em" aria-hidden /> : <IconCopy size="1.1em" aria-hidden />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}
