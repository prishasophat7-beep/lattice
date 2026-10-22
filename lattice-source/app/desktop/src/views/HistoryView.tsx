import { IconCircleCheck, IconInfoCircle, IconSearch, IconTrash } from "@tabler/icons-react";
import { useLayoutEffect, useRef, useState } from "react";
import { plainText } from "../markup";
import type { Conversation } from "../types";

function dayLabel(ts: string): string {
  const d = new Date(ts);
  const today = new Date();
  const yesterday = new Date(today.getTime() - 86_400_000);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
}

/** A conversation matches when every word of the search is in one of its questions or answers. */
function matches(c: Conversation, words: string[]): boolean {
  const text = c.entries.map((e) => `${e.question} ${plainText(e.answer)}`).join(" ").toLocaleLowerCase();
  return words.every((w) => text.includes(w));
}

export default function HistoryView({
  conversations,
  activeId,
  onOpen,
  onDelete,
}: {
  conversations: Conversation[];
  activeId: string;
  onOpen: (conversationId: string) => void;
  /** Delete a conversation from this machine's history */
  onDelete?: (conversationId: string) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // opens at the newest conversations, not wherever the view before it was scrolled to (final check)
  const viewRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    viewRef.current?.scrollTo({ top: 0 });
  }, []);
  const words = query.toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const shown = words.length ? conversations.filter((c) => matches(c, words)) : conversations;

  const groups = new Map<string, Conversation[]>();
  for (const c of shown) {
    const key = dayLabel(c.last.ts);
    groups.set(key, [...(groups.get(key) ?? []), c]);
  }

  const remove = async (id: string) => {
    if (!onDelete) return;
    setDeleting(id);
    setError(null);
    try {
      await onDelete(id);
    } catch (e) {
      // said, not swallowed: a failed delete left the row as it was with no word why (final check)
      setError(`The conversation could not be deleted: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setDeleting(null);
      setConfirming(null);
    }
  };

  return (
    <div className="history-view" ref={viewRef}>
      <div className="view-head">
        <h1>History</h1>
        <p className="meta">Conversations on this machine. Nothing leaves it.</p>
      </div>
      {conversations.length > 0 && (
        <label className="search-box history-search">
          <IconSearch size="1.1em" aria-hidden />
          <input
            type="search"
            placeholder="Search questions and answers"
            aria-label="Search history"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
      )}
      {error && (
        <p className="notice notice-bad" role="alert">
          {error}
        </p>
      )}
      {conversations.length === 0 && <p className="lede">Questions you ask will appear here.</p>}
      {conversations.length > 0 && shown.length === 0 && <p className="lede">No conversations match “{query.trim()}”.</p>}
      {[...groups.entries()].map(([day, list]) => (
        <section key={day} className="history-group">
          <p className="side-label">{day}</p>
          <ul className="history-list">
            {list.map((c) => {
              const first = c.entries[0];
              const more = c.entries.length - 1;
              const tags = [...new Set(c.entries.flatMap((e) => e.tags ?? []))];
              return (
                <li key={c.id} className="history-row">
                  <button
                    type="button"
                    className={`history-open${c.id === activeId ? " active" : ""}`}
                    onClick={() => onOpen(c.id)}
                  >
                    {c.last.found ? (
                      <IconCircleCheck size="1.2em" className="text-good" aria-label="Answered" />
                    ) : (
                      <IconInfoCircle size="1.2em" className="text-caution" aria-label="Not in the documents" />
                    )}
                    <span className="history-text">
                      <span className="history-q">
                        {first.question}
                        {tags.map((t) => (
                          <span key={t} className="tag">
                            {t}
                          </span>
                        ))}
                      </span>
                      <span className="meta history-a">
                        {more > 0 && `${more} follow-up${more === 1 ? "" : "s"} · `}
                        {plainText(c.last.answer)}
                      </span>
                    </span>
                    <span className="meta">
                      {new Date(c.last.ts).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
                    </span>
                  </button>
                  {onDelete &&
                    (confirming === c.id ? (
                      <span className="history-confirm" role="group" aria-label="Delete this conversation?">
                        <span className="meta">Delete?</span>
                        <button
                          type="button"
                          className="btn btn-small btn-danger"
                          disabled={deleting === c.id}
                          onClick={() => remove(c.id)}
                        >
                          Delete
                        </button>
                        <button type="button" className="btn btn-small btn-ghost" onClick={() => setConfirming(null)}>
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="icon-btn history-delete"
                        aria-label={`Delete conversation: ${first.question}`}
                        title="Delete conversation"
                        onClick={() => setConfirming(c.id)}
                      >
                        <IconTrash size="1.1em" aria-hidden />
                      </button>
                    ))}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
