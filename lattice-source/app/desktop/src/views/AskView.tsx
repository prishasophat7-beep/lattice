import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import {
  IconArrowUp,
  IconBook2,
  IconChevronLeft,
  IconChevronRight,
  IconInfoCircle,
  IconMapPin,
  IconMessages,
  IconPaperclip,
  IconPlayerStop,
  IconRefresh,
  IconSparkles,
  IconX,
} from "@tabler/icons-react";
import type { Attachment, Citation, DocSummary, Focus, Mode } from "../api";
import AnswerCard from "../components/AnswerCard";
import Markdown from "../components/Markdown";
import { MARKUP, stepsOnLines } from "../markup";
import type { Turn } from "../types";

/** Files Chat mode can read (the engine converts them to text) */
const ATTACHABLE = ".pdf,.docx,.xlsx,.txt,.md,.csv,.json,.log,.tsv";

const PLACEHOLDER: Record<Mode, string> = {
  manuals: "Ask a question about the documents",
  chat: "Ask anything about your work; the assistant checks the documents first",
};

/** The "Working in" line: the manual, then the last three headings ("Multi X Classification ›
 * Creating a MultiX solution › Training"); the whole path is in its tooltip. */
function placeLine(path: string[]): string[] {
  // a heading named after its document is not said twice ("skylink_limited_warranty.pdf ›
  // skylink_limited_warranty.pdf"), as in the source labels
  path = path.filter((p, i) => i === 0 || p.trim() !== path[0].trim());
  return path.length <= 4 ? path : [path[0], "…", ...path.slice(-3)];
}

export default function AskView({
  turns,
  ready,
  notReadyText,
  docs,
  station,
  suggestions,
  demo,
  mode,
  onMode,
  onAsk,
  onAskAll,
  onAskChat,
  onRegenerate,
  onPickManuals,
  onStop,
  onOpenSource,
  onClear,
  pendingFiles = [],
  onAttach,
  attaching = false,
  attachError = null,
  slowModel,
  searchNote = null,
  onManage,
  chatDocumentsOnly = false,
  focus = null,
  onClearFocus,
  onStep,
  onAskSection,
}: {
  turns: Turn[];
  ready: boolean;
  notReadyText: string;
  docs: DocSummary[];
  station: string | null;
  suggestions: string[];
  demo: boolean;
  mode: Mode;
  /** Present when Chat mode is available on this machine */
  onMode?: (m: Mode) => void;
  /** replace: the turn an edited question replaces */
  onAsk: (q: string, replace?: Turn) => void;
  onAskAll: (q: string) => void;
  onAskChat?: (q: string) => void;
  onRegenerate: (t: Turn) => void;
  /** answer a clarifying question: search only these manuals */
  onPickManuals: (t: Turn, docIds: string[], question?: string, section?: string) => void;
  onStop: () => void;
  onOpenSource: (c: Citation, turn: Turn) => void;
  onClear: () => void;
  /** Chat mode: files uploaded for the next question */
  pendingFiles?: Attachment[];
  /** Present when Chat mode is available: upload a file for the assistant to read */
  onAttach?: (file: File) => void;
  attaching?: boolean;
  attachError?: string | null;
  /** the built-in model on this computer's processor: answers take minutes */
  /** the built-in model answers on this computer's processor: how long an answer takes */
  slowModel?: string;
  /** why answers may miss reworded questions (search by meaning off or not ready); null when all is well */
  searchNote?: string | null;
  onManage?: () => void;
  /** Chat on a small built-in model: answers from the documents only, never from general knowledge */
  chatDocumentsOnly?: boolean;
  /** where the conversation is: shown above the question box, follow-ups are read there first */
  focus?: Focus | null;
  onClearFocus?: () => void;
  /** ask about the section after ("next") or before ("back") the one the conversation is at */
  onStep?: (way: "next" | "back", title: string) => void;
  /** ask about one section of a manual: the same step elsewhere, a closest section */
  onAskSection?: (question: string, docId: string, section: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const canAttach = mode === "chat" && !!onAttach;
  const [editing, setEditing] = useState<Turn | null>(null);
  // "Something else..." under a question back: the question it was about, to say in other words
  const [specifying, setSpecifying] = useState<Turn | null>(null);
  const busy = turns.some((t) => t.stage === "selecting" || t.stage === "reading");
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const last = turns[turns.length - 1];

  useEffect(() => {
    // follow the conversation; the welcome screen stays at its top (its heading, on a short window)
    if (turns.length) endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  useEffect(() => {
    if (!busy && !document.querySelector('[aria-modal="true"]')) inputRef.current?.focus();
  }, [busy]);

  // An edit only makes sense for the question still shown last
  useEffect(() => {
    if (editing && editing.id !== last?.id) {
      setEditing(null);
      // the question being edited goes with it (it was left in the box, to be sent in another conversation)
      setDraft((d) => (d === editing.question ? "" : d));
    }
  }, [editing, last?.id]);

  const submit = (text: string) => {
    const q = text.trim();
    if (!q || busy || !ready) return;
    setDraft("");
    onAsk(q, editing ?? undefined);
    setEditing(null);
    setSpecifying(null);
  };
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit(draft);
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit(draft);
    } else if (e.key === "Escape" && editing) {
      setEditing(null);
      setDraft("");
    } else if (e.key === "Escape" && specifying) {
      setSpecifying(null);
    }
  };
  const startSpecifying = (t: Turn) => {
    setEditing(null);
    setSpecifying(t);
    setDraft("");
    inputRef.current?.focus();
  };
  const startEdit = (t: Turn) => {
    setSpecifying(null);
    setEditing(t);
    setDraft(t.question);
    inputRef.current?.focus();
  };

  const shownDocs = station ? docs.filter((d) => d.tags.includes(station)) : docs;

  return (
    <div className="ask-view">
      <div className="thread" aria-live="polite">
        {turns.length === 0 ? (
          <section className="welcome">
            <h1>What do you need to know?</h1>
            <p className="lede">
              {mode === "chat" && chatDocumentsOnly
                ? "Chat mode: the assistant looks things up in the documents and cites them, reads files you attach, and can summarize or rewrite. The language model on this computer answers from the documents only, not general questions."
                : mode === "chat"
                ? "Chat mode: the assistant looks things up in the documents and cites them, and can also answer general questions. General answers are marked, because they do not come from the documents."
                : "Answers come only from the documents, with the page or section they came from. If the documents don't cover something, the assistant says so."}
            </p>
            {demo && (
              <p className="notice">Demo mode: no language model is connected; answers only quote the best match.</p>
            )}
            {searchNote && (
              <p className="meta search-note">
                <IconInfoCircle size="1.05em" aria-hidden />
                <span>
                  {searchNote}
                  {onManage && (
                    <>
                      {" "}
                      <button type="button" className="link-btn" onClick={onManage}>
                        Manage
                      </button>
                    </>
                  )}
                </span>
              </p>
            )}
            {suggestions.length > 0 && (
              <div className="suggestions">
                <p className="side-label">
                  <IconSparkles size="1.1em" aria-hidden /> Try asking
                </p>
                <div className="chips">
                  {suggestions.map((s) => (
                    <button key={s} type="button" className="chip chip-lg" disabled={!ready} onClick={() => submit(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <div>
              <p className="side-label">
                {station ? `Documents for ${station}` : "Documents in this knowledge base"} ({shownDocs.length})
              </p>
              <ul className="doc-cards">
                {shownDocs.map((d) => (
                  <li key={d.doc_id} className="doc-card">
                    <span className="doc-title">{d.title}</span>
                    {d.file && <span className="meta doc-file" title={d.file}>{d.file}</span>}
                    {d.description && <span className="doc-desc">{d.description}</span>}
                  </li>
                ))}
              </ul>
            </div>
          </section>
        ) : (
          <>
            <div className="thread-bar">
              <button type="button" className="btn btn-small btn-ghost" onClick={onClear} disabled={busy}>
                <IconRefresh size="1.1em" aria-hidden />
                New conversation
              </button>
            </div>
            {turns.map((t) => {
              const isLast = t.id === last?.id && !busy && ready;
              return (
                <TurnView
                  key={t.id}
                  turn={t}
                  onRetry={() => onRegenerate(t)}
                  onSearchAll={busy || !ready ? undefined : () => onAskAll(t.question)}
                  onAskChat={busy || !ready || !onAskChat ? undefined : () => onAskChat(t.question)}
                  onRegenerate={isLast && t.result?.history_id ? () => onRegenerate(t) : undefined}
                  onPickManuals={isLast ? (ids, q, s) => onPickManuals(t, ids, q, s) : undefined}
                  onAskSection={isLast && !busy && ready && onAskSection ? onAskSection : undefined}
                  onEdit={isLast && (t.result?.history_id || t.stage === "error") ? () => startEdit(t) : undefined}
                  onSomethingElse={isLast && t.result?.clarify ? () => startSpecifying(t) : undefined}
                  onOpenSource={onOpenSource}
                  hint={slowModel}
                />
              );
            })}
          </>
        )}
        <div ref={endRef} />
      </div>

      <form className="askbar" onSubmit={onSubmit}>
        {focus && turns.length > 0 && !editing && (
          <div className="focus-bar" role="group" aria-label="Where this conversation is">
            <IconMapPin size="1em" className="focus-icon" aria-hidden />
            <span className="focus-label">Working in:</span>
            <span className="focus-path" title={focus.path.join(" › ")}>
              {placeLine(focus.path).map((p, i, all) => (
                <span key={i} className={i === all.length - 1 ? "focus-here" : "focus-crumb"}>
                  {p}
                </span>
              ))}
            </span>
            <span className="focus-steps">
              {onStep && focus.previous && (
                <button
                  type="button"
                  className="focus-step"
                  disabled={!ready || busy}
                  title={`Go back to: ${focus.previous}`}
                  onClick={() => onStep("back", focus.previous!)}
                >
                  <IconChevronLeft size="1em" aria-hidden />
                  Back
                </button>
              )}
              {onStep && focus.next && (
                <button
                  type="button"
                  className="focus-step"
                  disabled={!ready || busy}
                  title={`Next: ${focus.next}`}
                  onClick={() => onStep("next", focus.next!)}
                >
                  Next: <span className="focus-next">{focus.next}</span>
                  <IconChevronRight size="1em" aria-hidden />
                </button>
              )}
            </span>
            {onClearFocus && (
              <button
                type="button"
                className="icon-btn"
                aria-label="Ask about anything, not just this section"
                title="Ask about anything, not just this section"
                onClick={onClearFocus}
              >
                <IconX size="1em" />
              </button>
            )}
          </div>
        )}
        {specifying && specifying.id === last?.id && !editing && (
          <div className="editing-bar">
            <span className="meta">Type what you meant by “{specifying.question}”, in your own words.</span>
            <button type="button" className="icon-btn" aria-label="Cancel" onClick={() => setSpecifying(null)}>
              <IconX size="1em" />
            </button>
          </div>
        )}
        {editing && (
          <div className="editing-bar">
            <span className="meta">Editing your last question: the new answer replaces the old one.</span>
            <button
              type="button"
              className="icon-btn"
              aria-label="Cancel editing"
              onClick={() => {
                setEditing(null);
                setDraft("");
              }}
            >
              <IconX size="1em" />
            </button>
          </div>
        )}
        {canAttach && (pendingFiles.length > 0 || attaching || attachError) && (
          <div className="attach-bar" aria-live="polite">
            {pendingFiles.map((f) => (
              <span key={f.id} className="chip attach-chip" title={`${f.chars.toLocaleString()} characters of text`}>
                <IconPaperclip size="0.95em" aria-hidden />
                {f.name}
              </span>
            ))}
            {attaching && <span className="meta">Reading the file...</span>}
            {attachError && <span className="meta text-bad">{attachError}</span>}
          </div>
        )}
        <div className="askbar-row">
          {onMode && (
            <div className="mode-switch" role="radiogroup" aria-label="Answer mode">
              <button
                type="button"
                role="radio"
                aria-checked={mode === "manuals"}
                className={mode === "manuals" ? "active" : ""}
                onClick={() => onMode("manuals")}
                title="Only from the documents, with citations"
              >
                <IconBook2 size="1.05em" aria-hidden />
                Manuals
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={mode === "chat"}
                className={mode === "chat" ? "active" : ""}
                onClick={() => onMode("chat")}
                title="Documents first, general knowledge when they don't cover it"
              >
                <IconMessages size="1.05em" aria-hidden />
                Chat
              </button>
            </div>
          )}
          {canAttach && (
            <>
              <input
                ref={fileRef}
                type="file"
                accept={ATTACHABLE}
                hidden
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (file) onAttach!(file);
                }}
              />
              <button
                type="button"
                className="icon-btn attach-btn"
                aria-label="Attach a file"
                title="Attach a file (PDF, Word, Excel or text) for the assistant to read"
                disabled={!ready || busy || attaching}
                onClick={() => fileRef.current?.click()}
              >
                <IconPaperclip size="1.2em" />
              </button>
            </>
          )}
          <label htmlFor="question" className="visually-hidden">
            Your question
          </label>
          <textarea
            id="question"
            ref={inputRef}
            rows={1}
            value={draft}
            disabled={!ready}
            placeholder={ready ? PLACEHOLDER[mode] : notReadyText}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
          />
          {busy ? (
            <button type="button" className="btn btn-secondary ask-btn" onClick={onStop}>
              <IconPlayerStop size="1.1em" aria-hidden />
              Stop
            </button>
          ) : (
            <button type="submit" className="btn btn-primary ask-btn" disabled={!ready || !draft.trim()}>
              <IconArrowUp size="1.1em" aria-hidden />
              {editing ? "Resend" : "Ask"}
            </button>
          )}
        </div>
      </form>
    </div>
  );
}

function TurnView({
  turn,
  onRetry,
  onSearchAll,
  onAskChat,
  onRegenerate,
  onPickManuals,
  onEdit,
  onOpenSource,
  onAskSection,
  onSomethingElse,
  hint,
}: {
  turn: Turn;
  hint?: string;
  onRetry: () => void;
  onSearchAll?: () => void;
  onAskChat?: () => void;
  onRegenerate?: () => void;
  onPickManuals?: (docIds: string[], question?: string, section?: string) => void;
  onEdit?: () => void;
  onOpenSource: (c: Citation, turn: Turn) => void;
  onAskSection?: (question: string, docId: string, section: string) => void;
  onSomethingElse?: () => void;
}) {
  const working = turn.stage === "selecting" || turn.stage === "reading";
  // stopped now, or reopened from history without its answer (stopped, or the app closed)
  const stopped = turn.error === "Stopped." || (turn.fromHistory && turn.stage === "error");
  return (
    <article className="turn">
      <p className="question">
        {turn.mode === "chat" && <span className="mode-tag">Chat</span>}
        {turn.question}
      </p>
      {turn.attachments && (
        <p className="question-files">
          {turn.attachments.map((f) => (
            <span key={f.id} className="chip attach-chip">
              <IconPaperclip size="0.95em" aria-hidden />
              {f.name}
            </span>
          ))}
        </p>
      )}
      {turn.result?.standalone_question && (
        <p className="meta rewritten">Searched as: “{turn.result.standalone_question}”</p>
      )}
      {working && turn.mode === "chat" && turn.draft && (
        <div className="answer answer-draft">
          <Markdown text={turn.draft} terms={[]} />
          <span className="cursor" aria-hidden="true" />
        </div>
      )}
      {working && turn.mode !== "chat" && turn.draft && (
        // formatted as it will be once done (bold, steps as a list), not raw "**" while it streams
        <div className="answer answer-draft">
          {MARKUP.test(turn.draft) ? (
            <div className="answer-md answer-md-large">
              <Markdown text={stepsOnLines(turn.draft)} terms={[]} />
              <span className="cursor" aria-hidden="true" />
            </div>
          ) : (
            <p className="answer-text">
              {turn.draft}
              <span className="cursor" aria-hidden="true" />
            </p>
          )}
        </div>
      )}
      {working && !turn.draft && <Progress text={progressText(turn)} since={turn.startedAt} hint={hint} />}
      {turn.stage === "error" && (
        <div className={`answer ${stopped ? "answer-stopped" : "answer-error"}`}>
          <p>{turn.error}</p>
          <div className="answer-actions">
            <button type="button" className="btn btn-small" onClick={onRetry}>
              {stopped ? "Ask again" : "Try again"}
            </button>
            {onEdit && (
              <button type="button" className="btn btn-small btn-ghost" onClick={onEdit}>
                Edit
              </button>
            )}
          </div>
        </div>
      )}
      {turn.stage === "done" && turn.result && (
        <AnswerCard
          result={turn.result}
          askedAt={turn.fromHistory ? turn.askedAt : undefined}
          station={turn.station ?? null}
          onSearchAll={onSearchAll}
          onAskChat={onAskChat}
          onRegenerate={onRegenerate}
          onPickManuals={onPickManuals}
          onEdit={onEdit}
          onOpenSource={(c) => onOpenSource(c, turn)}
          onAskSection={onAskSection}
          onSomethingElse={onSomethingElse}
        />
      )}
    </article>
  );
}

/** What the assistant is doing; with since, the time it has taken so far (shown after a few seconds),
 * and with hint, what to expect (shown once it has taken a while). */
/** What an answer being worked on is doing now, the same in both modes: the step the engine last
 * reported ("Finding the right section...", "Searching the documents for ..."), the sections it
 * reads, else "Thinking..." (the model working on its reply). Manuals mode starts by finding the
 * section; the Chat assistant by thinking. */
function progressText(turn: Turn): string {
  if (turn.activity) return turn.activity;
  if (turn.reading?.length) {
    const names = turn.reading.map((c) => {
      const where = c.pages ? c.locator : c.doc_name;
      // a one-sheet file's section is named like the file: "HX-900_spare_parts.xlsx", once
      return where && where !== c.title ? `${c.title} (${where})` : c.title;
    });
    return `Reading ${names.join(", ")}...`;
  }
  return turn.mode === "chat" || turn.stage === "reading" ? "Thinking..." : "Finding the right section...";
}

export function Progress({ text, since, hint }: { text: string; since?: number; hint?: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (since == null) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [since]);
  const secs = since == null ? 0 : Math.max(0, Math.floor((now - since) / 1000));
  return (
    <div className="progress" role="status">
      <span className="spinner" aria-hidden="true" />
      <span>
        {text}
        {secs >= 5 && <span className="meta progress-time"> {Math.floor(secs / 60)}:{String(secs % 60).padStart(2, "0")}</span>}
        {hint && secs >= 10 && <span className="meta progress-hint">{hint}</span>}
      </span>
    </div>
  );
}
