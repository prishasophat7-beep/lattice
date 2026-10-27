// Client for the local Lattice engine (app/backend). Inside Tauri the Rust shell starts the
// engine and hands us its address and a per-launch token; in a plain browser (UI development)
// they come from VITE_BACKEND_URL / VITE_API_TOKEN in .env.local.

export interface Citation {
  doc_id: string;
  doc_name: string;
  node_id: string;
  title: string;
  locator: string;
  pages: number[] | null;
}

export interface AskResult {
  question: string;
  found: boolean;
  answer: string;
  citations: Citation[];
  considered: Citation[];
  seconds: Record<string, number>;
  model_requests: number;
  /** Figures in the answer that do not appear in its sources. Optional: older engines omit it. */
  unverified_numbers?: string[];
  /** Id of the saved history entry; feedback refers to it. */
  history_id?: string | null;
  /** an answer reopened from history: the vote given on it */
  feedback?: Given | null;
  /** A greeting or chit-chat answered with a fixed reply (no search, no sources). */
  smalltalk?: boolean;
  /** A follow-up as the engine understood it ("and for M10?" -> "What torque for M10 bolts?"). */
  standalone_question?: string | null;
  /** "manuals": cited pipeline; "chat": the tool-using assistant. Older engines omit it. */
  mode?: Mode;
  /** The manual's own sentences backing the answer; citation indexes into citations */
  evidence?: { text: string; citation: number }[];
  /** The question fits several manuals equally: which one is meant? */
  clarify?: Clarification | null;
  /** Pictures for the answer; citation indexes into citations */
  figures?: Figure[];
  /** Only a list of pictures ("show me the diagrams"), found without the model */
  pictures_only?: boolean;
  /** A summary of a whole manual (or of all of them), in Markdown */
  summary?: boolean;
  /** Chat mode: the answer, or a part of it set apart as a quote, is general knowledge */
  general?: boolean;
  /** Chat mode: names of attached files the answer was given */
  files?: string[];
  /** Not stated in the documents in so many words, but worked out from what they state */
  worked_out?: boolean;
  /** Where the conversation is after this answer (the "Working in" line). Older engines omit it. */
  focus?: Focus | null;
}

/** A section a conversation is at: follow-ups are read there first; "next" / "go back" step on. */
export interface Focus {
  doc_id: string;
  node_id: string;
  /** the manual's title, then the headings down to the section */
  path: string[];
  /** titles of the sections "go back" and "next" read */
  previous?: string | null;
  next?: string | null;
  /** the same step in other parts of the manual: label is the part ("Single X Classification") */
  elsewhere?: { label: string; node_id: string; title?: string }[];
}

/** A file attached to a Chat-mode conversation, converted to text by the engine. */
export interface Attachment {
  id: string;
  name: string;
  chars: number;
}

/** A picture: an image the web platform extracted (image), or a page of the original PDF (page only). */
export interface Figure {
  citation: number;
  page?: number | null;
  image?: string | null;
  /** the picture's caption in the document, and what the export says it shows */
  caption?: string | null;
  description?: string | null;
}

export interface Clarification {
  prompt: string;
  /** label: the manual; detail: the section that matched. With question (a vague question's
   * meanings): picking it asks that question instead of searching only these manuals */
  options: { label: string; detail: string; doc_ids: string[]; question?: string | null; section?: string | null }[];
}

export type Mode = "manuals" | "chat";

export interface Health {
  status: "ok" | "no_knowledge_base";
  /** "line": manuals only; "office": manuals and Chat mode */
  profile?: "line" | "office";
  chat_available?: boolean;
  default_mode?: Mode;
  /** "local": the built-in model the shell runs; "remote": an endpoint set in Manage */
  provider?: "local" | "remote";
  /** the model Chat mode uses (office profile), when it differs */
  chat_model?: string | null;
  engine_version: string;
  /** when the engine was built, e.g. "15 Oct 2026 09:14" */
  engine_built?: string;
  kb_version: string | null;
  kb_error: string | null;
  model: string;
  /** null: not a local model server (demo mode, remote endpoint) */
  llm_ready: boolean | null;
  llm_detail: string;
  requests_left_today: number | null;
  /** search by meaning: null (not set up), "indexing", "ready" or "error: ..." */
  semantic?: string | null;
  /** Chat mode on a small built-in model: answers from the documents only, no general knowledge */
  chat_documents_only?: boolean;
  /** billions of parameters of the built-in model (from its file name), when known */
  builtin_model_b?: number | null;
  /** while indexing: [sections done, all sections] */
  semantic_progress?: [number, number] | null;
  /** the embedding model searching by meaning, and whether it runs on this computer */
  semantic_model?: string | null;
  semantic_local?: boolean;
}

/** The local model as the Tauri shell manages it. */
export interface ModelInfo {
  name: string | null;
  /** size of the installed model file in bytes */
  size?: number | null;
  running: boolean;
  error: string | null;
}

export interface KbDocument {
  doc_id: string;
  name: string;
  title: string;
  description: string;
  kind: "pages" | "markdown";
  sections: number;
  fallback_summaries: number;
}

/** A finding of the checks run on every load: what Lattice did not understand of the knowledge base. */
export interface KbCheck {
  level: "bad" | "warn" | "info";
  message: string;
}

export interface KbHealth {
  documents: { title: string; sections: number; empty_sections: number; pictures: number; described: number;
    loose_descriptions: number }[];
  checks: KbCheck[];
}

export interface KbInfo {
  kb_version: string;
  source_format: "pageindex" | "markdown" | "export" | "semkb";
  created_at: string | null;
  /** Catalog entries in kb.json whose id names no document: the web team's bundle has a mistake. */
  catalog_unmatched?: string[];
  /** web-platform exports: documents left out because the export had no text for them */
  skipped?: string[];
  /** null when the checks could not run */
  health?: KbHealth | null;
  documents: KbDocument[];
}

export interface Versions {
  active: string | null;
  installed: string[];
}

export interface DocSummary {
  doc_id: string;
  name: string;
  title: string;
  description: string;
  kind: "pages" | "markdown";
  sections: number;
  tags: string[];
  has_original: boolean;
  pages: number | null;
  /** the file name the title was made readable from, when they differ */
  file?: string | null;
}

export interface TocSection {
  node_id: string;
  title: string;
  depth: number;
  parent: string | null;
  locator: string;
  summary: string;
  summary_is_fallback: boolean;
  has_children: boolean;
}

export interface Toc {
  doc_id: string;
  name: string;
  title: string;
  description: string;
  kind: "pages" | "markdown";
  sections: TocSection[];
}

export interface SectionView {
  doc_id: string;
  doc_name: string;
  doc_title: string;
  node_id: string;
  title: string;
  path: string[];
  locator: string;
  pages: number[] | null;
  kind: "pages" | "markdown";
  text: string;
  has_original: boolean;
  /** Pictures in this section and its subsections (web-platform exports). Older engines omit it. */
  images?: { image: string; section: string; page: number | null; caption?: string; description?: string }[];
}

export interface SearchHit {
  doc_id: string;
  doc_title: string;
  kind: "pages" | "markdown";
  node_id: string;
  title: string;
  path: string[];
  locator: string;
  score: number;
  snippet: string;
}

export interface HistoryEntry {
  id: string;
  ts: string;
  /** Missing on entries saved before conversations existed: each is then its own conversation. */
  conversation_id?: string | null;
  standalone_question?: string | null;
  mode?: Mode;
  question: string;
  tags: string[];
  found: boolean;
  answer: string;
  citations: Citation[];
  unverified_numbers?: string[];
  evidence?: { text: string; citation: number }[];
  clarify?: Clarification | null;
  figures?: Figure[];
  summary?: boolean;
  /** Chat mode (saved since 0.1.0 of Sep 28; older entries omit them) */
  general?: boolean;
  files?: string[];
  seconds: number | null;
  /** a "not in the documents" answer: the sections checked */
  considered?: Citation[];
  /** the latest vote on this answer */
  feedback?: Given | null;
  /** saved when asked: the answer never came (stopped, or the app closed) */
  pending?: boolean;
  worked_out?: boolean;
  focus?: Focus | null;
}

/** A vote given on an answer. */
export interface Given {
  rating: "helpful" | "wrong";
  comment: string;
}

export interface FeedbackSummary {
  total: number;
  helpful: number;
  wrong: number;
}

export interface Connection {
  kind: "local" | "remote";
  base_url: string;
  model: string;
  /** kept for older config files; every model now replies in plain text */
  output_mode: "text" | "native" | "prompted" | "tool";
  reasoning: boolean;
  /** the model's context window in tokens; 0 or absent: asked from the server */
  context_tokens?: number;
}

/** Admin settings (Manage). The API key is never sent back, only whether one is stored. */
export interface AppConfig {
  profile: "line" | "office";
  default_mode: Mode;
  work_topics_only: boolean;
  chat_available: boolean;
  connection: Connection & { api_key_set: boolean };
  /** Chat mode's own model; null: the same as Manuals mode */
  chat_connection: (Connection & { api_key_set: boolean }) | null;
  /** An embedding model on an office server for search by meaning; null: keywords only */
  embeddings?: (EmbeddingConnection & { api_key_set: boolean }) | null;
}

export interface EmbeddingConnection {
  base_url: string;
  model: string;
}
export type EmbeddingUpdate = EmbeddingConnection & { api_key?: string | null };

/** api_key: undefined/null keeps the stored key; a string replaces it ("" clears it). */
export type ConnectionUpdate = Connection & { api_key?: string | null };
export type ConfigUpdate = Omit<AppConfig, "chat_available" | "connection" | "chat_connection" | "embeddings"> & {
  connection: ConnectionUpdate;
  chat_connection: ConnectionUpdate | null;
  embeddings: EmbeddingUpdate | null;
};

export type AskEvent =
  | { stage: "selecting" }
  /** the question was saved before its answer (History keeps it if the answer never comes) */
  | { stage: "held"; history_id: string }
  | { stage: "reading"; sections: Citation[] }
  | { stage: "tool"; tool: string; detail: string }
  | { stage: "writing" }
  | { stage: "retrying" }
  | { stage: "delta"; text: string }
  | { stage: "draft"; text: string }
  | { stage: "done"; result: AskResult }
  | { stage: "error"; status: number; message: string };

/** The largest file Chat mode reads (the engine's attachments.MAX_BYTES) */
export const MAX_ATTACHMENT_BYTES = 15 * 1024 ** 2;

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

interface Backend {
  url: string;
  token: string;
}

export const inTauri = "__TAURI_INTERNALS__" in window;

let backendPromise: Promise<Backend> | null = null;

function backend(): Promise<Backend> {
  if (!backendPromise) {
    backendPromise = (async () => {
      if (inTauri) {
        const { invoke } = await import("@tauri-apps/api/core");
        return invoke<Backend>("backend_info");
      }
      return {
        url: import.meta.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8765",
        token: import.meta.env.VITE_API_TOKEN ?? "",
      };
    })();
  }
  return backendPromise;
}

function authHeader(token: string): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function query(params: Record<string, string | string[] | number | undefined>): string {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined) continue;
    for (const v of Array.isArray(value) ? value : [value]) q.append(key, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const detail = (await res.json()).detail;
    if (typeof detail === "string") return detail;
    // a request the engine's checks refused ([{"msg": "String should have at most ...", ...}]): shown
    // as JSON before; its message is still technical, but readable
    if (Array.isArray(detail) && detail.length && typeof detail[0]?.msg === "string") {
      return `The request was not accepted: ${detail[0].msg}.`;
    }
    return JSON.stringify(detail);
  } catch {
    return res.statusText;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { url, token } = await backend();
  let res: Response;
  try {
    res = await fetch(url + path, {
      ...init,
      headers: { "Content-Type": "application/json", ...authHeader(token), ...init.headers },
    });
  } catch {
    throw new ApiError(0, "The assistant's engine is not responding.");
  }
  if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  health: () => request<Health>("/api/health"),
  kb: () => request<KbInfo>("/api/kb"),
  versions: () => request<Versions>("/api/kb/versions"),
  importKb: (path: string) =>
    post<{ imported: string; skipped?: string[]; checks?: KbCheck[] }>("/api/kb/import", { path }),
  activate: (kb_version: string) => post<{ active: string }>("/api/kb/activate", { kb_version }),
  /** Delete an installed version that is not the active one. */
  removeVersion: (kb_version: string) =>
    request<{ removed: string; installed: string[] }>(`/api/kb/versions/${encodeURIComponent(kb_version)}`,
      { method: "DELETE" }),

  docs: () => request<DocSummary[]>("/api/docs"),
  tags: () => request<string[]>("/api/tags"),
  toc: (docId: string) => request<Toc>(`/api/docs/${encodeURIComponent(docId)}/toc`),
  section: (docId: string, nodeId: string) =>
    request<SectionView>(`/api/docs/${encodeURIComponent(docId)}/sections/${encodeURIComponent(nodeId)}`),
  search: (q: string, tags: string[]) => request<SearchHit[]>(`/api/search${query({ q, tags })}`),
  suggestions: (tags: string[]) => request<string[]>(`/api/suggestions${query({ tags })}`),
  /** Newest first; with a conversation id, that conversation's questions oldest first. */
  history: (limit = 50, conversation_id?: string) =>
    request<HistoryEntry[]>(`/api/history${query(conversation_id ? { limit, conversation_id } : { limit })}`),
  /** Delete one conversation (all its questions) from this machine's history. */
  deleteConversation: (conversation_id: string) =>
    request<{ deleted: number }>(`/api/history${query({ conversation_id })}`, { method: "DELETE" }),
  feedback: (history_id: string, rating: "helpful" | "wrong", comment = "") =>
    post<{ id: string }>("/api/feedback", { history_id, rating, comment }),
  feedbackSummary: () => request<FeedbackSummary>("/api/feedback/summary"),
  exportFeedback: (path: string) => post<{ path: string; count: number }>("/api/feedback/export", { path }),

  config: () => request<AppConfig>("/api/config"),
  saveConfig: (c: ConfigUpdate) => request<AppConfig>("/api/config", { method: "PUT", body: JSON.stringify(c) }),
  /** target "chat": use the stored Chat-mode key when none is given */
  testConnection: (c: ConnectionUpdate, target: "main" | "chat" = "main") =>
    post<{ ok: boolean; detail: string; models: string[]; warning?: string }>(`/api/config/test${query({ target })}`, c),
  testEmbeddings: (e: EmbeddingUpdate) => post<{ ok: boolean; detail: string }>("/api/config/test-embeddings", e),

  /** Upload a file for Chat mode; the engine converts it to text and keeps it with the conversation. */
  async attach(conversationId: string, file: File): Promise<Attachment> {
    // said before uploading it (a larger file was refused with the engine's raw size check)
    if (file.size > MAX_ATTACHMENT_BYTES) {
      throw new ApiError(413, `The file is larger than ${MAX_ATTACHMENT_BYTES / 1024 ** 2} MB.`);
    }
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    return post<Attachment>("/api/attachments", {
      conversation_id: conversationId,
      name: file.name,
      data_base64: btoa(binary),
    });
  },
  attachments: (conversationId: string) =>
    request<Attachment[]>(`/api/attachments${query({ conversation_id: conversationId })}`),

  /** The original PDF as an object URL (the engine needs the auth header, so no direct link). */
  async originalUrl(docId: string): Promise<string> {
    const { url, token } = await backend();
    const res = await fetch(`${url}/api/docs/${encodeURIComponent(docId)}/original`, { headers: authHeader(token) });
    if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
    return URL.createObjectURL(await res.blob());
  },

  /** A page of the original PDF as a picture (object URL; revoke it when done). */
  /** A picture the web platform extracted from a document (object URL; revoke it when done). */
  async docImageUrl(docId: string, name: string): Promise<string> {
    const { url, token } = await backend();
    const res = await fetch(`${url}/api/docs/${encodeURIComponent(docId)}/images/${encodeURIComponent(name)}`, {
      headers: authHeader(token),
    });
    if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
    return URL.createObjectURL(await res.blob());
  },

  /** crop: only the figure on the page */
  async pageImageUrl(docId: string, page: number, crop = false): Promise<string> {
    const { url, token } = await backend();
    const res = await fetch(`${url}/api/docs/${encodeURIComponent(docId)}/pages/${page}/image?crop=${crop}`, {
      headers: authHeader(token),
    });
    if (!res.ok) throw new ApiError(res.status, await errorMessage(res));
    return URL.createObjectURL(await res.blob());
  },

  /** Ask a question; onEvent receives progress, then exactly one "done" or "error" event. */
  async askStream(
    question: string,
    tags: string[],
    conversationId: string,
    options: {
      mode: Mode; replaces?: string | null; docIds?: string[]; section?: string; attachments?: string[];
      /** where the conversation is; focusOff: the user closed the "Working in" line */
      focus?: Focus | null; focusOff?: boolean;
    },
    onEvent: (e: AskEvent) => void,
    signal?: AbortSignal,
  ) {
    const { url, token } = await backend();
    let res: Response;
    try {
      res = await fetch(url + "/api/ask/stream", {
        method: "POST",
        signal,
        headers: { "Content-Type": "application/json", ...authHeader(token) },
        body: JSON.stringify({
          question,
          tags,
          conversation_id: conversationId,
          mode: options.mode,
          replaces: options.replaces ?? null,
          doc_ids: options.docIds ?? [],
          section: options.section ?? null,
          attachments: options.attachments ?? [],
          focus: options.focus ? { doc_id: options.focus.doc_id, node_id: options.focus.node_id } : null,
          focus_off: options.focusOff ?? false,
        }),
      });
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      onEvent({ stage: "error", status: 0, message: "The assistant's engine is not responding." });
      return;
    }
    if (!res.ok || !res.body) {
      onEvent({ stage: "error", status: res.status, message: await errorMessage(res) });
      return;
    }
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    let finished = false; // a "done" or "error" event arrived
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += value;
        let sep: number;
        while ((sep = buffer.indexOf("\n\n")) >= 0) {
          const chunk = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          const data = chunk
            .split("\n")
            .filter((l) => l.startsWith("data: "))
            .map((l) => l.slice(6))
            .join("\n");
          if (!data) continue;
          const event = JSON.parse(data) as AskEvent;
          if (event.stage === "done" || event.stage === "error") finished = true;
          onEvent(event);
        }
      }
    } catch (err) {
      // Stop: the reader is aborted on purpose. Anything else (the engine stopped mid-answer) is said,
      // not left as "Thinking..." for ever (final check: an uncaught AbortError on Stop)
      if ((err as Error).name === "AbortError" || signal?.aborted) return;
    }
    if (!finished && !signal?.aborted) {
      onEvent({ stage: "error", status: 0, message: "The connection to the assistant's engine was lost before the answer was complete." });
    }
  },
};

async function pickFile(title: string, name: string, extension: string): Promise<string | null> {
  if (!inTauri) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const chosen = await open({ title, multiple: false, directory: false, filters: [{ name, extensions: [extension] }] });
  return typeof chosen === "string" ? chosen : null;
}

export const pickBundleFile = () => pickFile("Choose a knowledge-base bundle", "Knowledge-base bundle", "zip");
export const pickModelFile = () => pickFile("Choose a language model file", "GGUF model", "gguf");
export const pickEmbedModelFile = () =>
  pickFile("Choose an embedding model file (search by meaning)", "GGUF model", "gguf");

export async function pickExportFile(): Promise<string | null> {
  if (!inTauri) return null;
  const { save } = await import("@tauri-apps/plugin-dialog");
  const stamp = new Date().toISOString().slice(0, 10);
  const chosen = await save({
    title: "Save feedback for the web team",
    defaultPath: `lattice-feedback-${stamp}.json`,
    filters: [{ name: "JSON", extensions: ["json"] }],
  });
  return chosen ?? null;
}

/** Local model management lives in the Tauri shell; outside Tauri there is none. */
export const model = {
  async info(): Promise<ModelInfo | null> {
    if (!inTauri) return null;
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<ModelInfo>("model_info");
  },
  /** Start or stop the built-in model server (stopped while a remote model is used, to free memory). */
  async setEnabled(enabled: boolean): Promise<void> {
    if (!inTauri) return;
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("set_model_enabled", { enabled });
  },
  async import(path: string): Promise<ModelInfo> {
    const { invoke } = await import("@tauri-apps/api/core");
    try {
      return await invoke<ModelInfo>("import_model", { path });
    } catch (e) {
      throw new ApiError(0, String(e));
    }
  },
};

/** The embedding model for search by meaning on this computer (works offline), also in the shell. */
export const embedModel = {
  async info(): Promise<ModelInfo | null> {
    if (!inTauri) return null;
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<ModelInfo>("embed_model_info");
  },
  async import(path: string): Promise<ModelInfo> {
    const { invoke } = await import("@tauri-apps/api/core");
    try {
      return await invoke<ModelInfo>("import_embed_model", { path });
    } catch (e) {
      throw new ApiError(0, String(e));
    }
  },
  async remove(): Promise<ModelInfo> {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<ModelInfo>("remove_embed_model");
  },
};

/** Kiosk mode: full screen, no window chrome. Only inside Tauri. */
export async function setFullscreen(on: boolean): Promise<void> {
  if (!inTauri) return;
  const { getCurrentWindow } = await import("@tauri-apps/api/window");
  await getCurrentWindow().setFullscreen(on);
}
