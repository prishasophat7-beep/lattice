import type { AskResult, Attachment, Citation, HistoryEntry, Mode } from "./api";

export type View = "ask" | "manuals" | "history";

export type Turn = {
  id: string;
  question: string;
  stage: "selecting" | "reading" | "done" | "error";
  mode: Mode;
  reading?: Citation[];
  /** Chat mode: the answer so far, while it is written */
  draft?: string;
  /** Chat mode: what the assistant is doing ("Searching the manuals for ...") */
  activity?: string;
  result?: AskResult;
  error?: string;
  /** Chat mode: files sent with this question */
  attachments?: Attachment[];
  /** Station filter the question was asked with (null: all manuals) */
  station?: string | null;
  /** Opened from history rather than asked now */
  fromHistory?: boolean;
  askedAt?: string;
  /** when it was asked in this session (Date.now()), for the time taken so far */
  startedAt?: number;
  /** the manuals and section an option picked after a question back stands for: Try again and
   * Regenerate ask about the same section again */
  docIds?: string[];
  section?: string;
  /** the saved answer this turn's question replaces (Regenerate, Edit): Try again after an error
   * replaces it too, not adds a turn after it */
  replaces?: string;
  /** the question as saved before its answer: Ask again after Stop replaces it */
  heldId?: string;
};

/** What the source panel shows: a section, and words or figures to highlight in it. */
export type SourceTarget = {
  docId: string;
  nodeId: string;
  highlight: string[];
};

/** Figures quoted in an answer, for highlighting in the source ("27.8%" -> "27.8"). */
export function figuresIn(text: string): string[] {
  // a number glued to letters is a code ("E17", "M6"), not a figure; so is the tail of "C-2087" or "HX9-4471";
  // a step number ("3. Create a new solution") is not one either
  text = text.replace(/^[ \t]*(?:[-*+][ \t]*)?\**\d{1,2}[.)]\**[ \t]/gm, " ");
  return [
    ...new Set(text.match(/(?<![\p{L}\d.,])(?<!\p{L}[\p{L}\d]*-)(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)/gu) ?? []),
  ];
}

/** Codes quoted in an answer ("C-2087", "HX9-4471", "PO-26-0503"), highlighted whole. */
export function codesIn(text: string): string[] {
  const codes = text.match(/(?<![\p{L}\d-])\p{Lu}[\p{L}\d]*(?:-[\p{L}\d]+)+/gu) ?? [];
  return [...new Set(codes.filter((c) => /\d/.test(c)))];
}

/** What to highlight in evidence and sources for an answer: its codes and its figures. */
export function highlightTermsIn(text: string): string[] {
  return [...codesIn(text), ...figuresIn(text)];
}

/** Questions asked in one thread, so follow-ups can refer to earlier ones. */
export type Conversation = {
  id: string;
  /** Oldest first (as far as the loaded history reaches) */
  entries: HistoryEntry[];
  last: HistoryEntry;
};

export function conversationOf(h: HistoryEntry): string {
  return h.conversation_id || h.id;
}

/** Group history (newest first) into conversations, most recently active first. */
export function groupConversations(history: HistoryEntry[]): Conversation[] {
  const byId = new Map<string, Conversation>();
  for (const h of history) {
    const id = conversationOf(h);
    const c = byId.get(id);
    if (c) c.entries.unshift(h);
    else byId.set(id, { id, entries: [h], last: h });
  }
  return [...byId.values()];
}

export const newConversationId = () => crypto.randomUUID().replace(/-/g, "");
