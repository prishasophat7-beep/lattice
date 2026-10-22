import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import LatticeMark from "./components/LatticeMark";
import { IconBooks, IconCpu } from "@tabler/icons-react";
import {
  api,
  ApiError,
  Attachment,
  Citation,
  DocSummary,
  Health,
  HistoryEntry,
  KbInfo,
  Mode,
  model,
  ModelInfo,
  setFullscreen,
} from "./api";
import ManagePanel from "./components/ManagePanel";
import Sidebar, { ModelStatus } from "./components/Sidebar";
import SourcePanel from "./components/SourcePanel";
import { loadPrefs, Prefs, savePrefs } from "./prefs";
import {
  conversationOf,
  groupConversations,
  highlightTermsIn,
  newConversationId,
  SourceTarget,
  Turn,
  View,
} from "./types";
import AskView, { Progress } from "./views/AskView";
import HistoryView from "./views/HistoryView";
import ManualsView from "./views/ManualsView";

const STARTUP_TIMEOUT_MS = 90_000;
const HISTORY_LOADED = 300; // entries loaded for the History view and the sidebar

function turnFromHistory(h: HistoryEntry): Turn {
  return {
    id: newTurnId(),
    question: h.question,
    // a question whose answer never came shows as stopped: Ask again replaces it (by its history id)
    stage: h.pending ? "error" : "done",
    error: h.pending ? h.answer : undefined,
    mode: h.mode ?? "manuals",
    fromHistory: true,
    askedAt: h.ts,
    station: h.tags?.[0] ?? null,
    result: {
      mode: h.mode ?? "manuals",
      question: h.question,
      found: h.found,
      answer: h.answer,
      citations: h.citations ?? [],
      considered: h.considered ?? [],
      feedback: h.feedback ?? null,
      seconds: h.seconds != null ? { total: h.seconds } : {},
      model_requests: 0,
      unverified_numbers: h.unverified_numbers ?? [],
      evidence: h.evidence ?? [],
      history_id: h.id,
      standalone_question: h.standalone_question ?? null,
      clarify: h.clarify ?? null,
      figures: h.figures ?? [],
      general: h.general,
      worked_out: h.worked_out ?? false,
      focus: h.focus,
      files: h.files ?? [],
      summary: h.summary ?? false,
    },
  };
}

/** A line for the empty Ask screen when search by meaning cannot help (null when it can, or is not wanted). */
/** What to expect from the built-in model on this computer's processor. Measured on an 8 GB
 * laptop: the 0.8B model answers in 1 to 3 minutes, the 4B one in 1 to 11 (typically 4 to 6). */
function slowHint(billions?: number | null): string {
  const runs = "The built-in model runs on this computer's processor: ";
  if (billions != null && billions >= 3) {
    return runs + "with this larger model an answer can take 5 minutes or more, longer on a computer with 8 GB of memory.";
  }
  return runs + "an answer usually takes 1 to 3 minutes.";
}

function searchNote(health: Health): string | null {
  if (health.model === "demo") return null;
  const s = health.semantic;
  if (s == null) return "Search by meaning is off: questions are matched by their words, so reworded questions may be missed. It is set up in";
  if (s === "indexing") {
    const [done, all] = health.semantic_progress ?? [0, 0];
    const count = all ? ` (${done.toLocaleString()} of ${all.toLocaleString()} sections)` : "";
    return `Search by meaning is being prepared${count}; until then questions are matched by their words. Progress is shown in`;
  }
  if (s.startsWith("error") || s.startsWith("unavailable"))
    return "Search by meaning is not working: questions are matched by their words only. Details are in";
  return null;
}

function modelStatus(health: Health, info: ModelInfo | null): ModelStatus {
  if (health.model === "demo") return "demo";
  if (health.provider === "remote") return health.llm_ready === false ? "unreachable" : "ready";
  if (info && !info.name) return "missing";
  if (info?.error) return "failed";
  if (health.llm_ready === false) return "loading";
  return "ready";
}

function friendlyError(status: number, message: string): string {
  if (status === 0) return "The assistant's engine is not responding. Wait a moment and try again.";
  if (status === 503) return "The language model is busy or still loading. Try again in a moment.";
  // the engine says which limit: a model service's allowance used up, or this setup's daily budget
  if (status === 429) return message || "The model service's usage limit is used up. Try again after it resets.";
  return message || "Something went wrong while answering.";
}

function toolActivity(tool: string, detail: string): string {
  if (tool === "search_manuals") return detail ? `Searching the documents for “${detail}”...` : "Searching the documents...";
  if (tool === "read_section") return "Reading a section...";
  if (tool === "manual_outline") return detail ? `Reading the contents of ${detail}...` : "Reading a document's contents...";
  if (tool === "find_pictures") return detail ? `Looking for pictures of “${detail}”...` : "Looking for pictures...";
  if (tool === "ask_which_manual") return "Checking which document you mean...";
  if (tool === "semantic_search") return detail ? `Searching by meaning for “${detail}”...` : "Searching by meaning...";
  if (tool === "list_documents") return "Listing the documents...";
  if (tool === "read_table") return "Reading a table...";
  if (tool === "calculate") return detail ? `Calculating ${detail}...` : "Calculating...";
  if (tool === "read_attachment") return "Reading your attached file...";
  if (tool === "query_table") return "Working through a table...";
  if (tool === "find_facts") return detail ? `Looking for the rules on “${detail}”...` : "Looking for the rules...";
  if (tool === "find_text") return detail ? `Finding “${detail}” in the documents...` : "Finding the exact words...";
  if (tool === "note") return "Checking the facts against the documents...";
  if (tool === "ask_user") return "Checking what you mean...";
  return `Using ${tool.replace(/_/g, " ")}...`;
}

let turnCounter = 0;
const newTurnId = () => `t${Date.now()}-${++turnCounter}`;

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [kb, setKb] = useState<KbInfo | null>(null);
  const [modelInfo, setModelInfo] = useState<ModelInfo | null>(null);
  const [docs, setDocs] = useState<DocSummary[]>([]);
  const [tags, setTags] = useState<string[]>([]);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [startupError, setStartupError] = useState<string | null>(null);
  const [prefs, setPrefs] = useState<Prefs>(loadPrefs);
  const [view, setView] = useState<View>("ask");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conversationId, setConversationId] = useState(newConversationId);
  const [source, setSource] = useState<SourceTarget | null>(null);
  const [manageOpen, setManageOpen] = useState(false);
  const [mode, setMode] = useState<Mode>("manuals");
  // the engine stopped answering after it had started (a crash): the shell starts it again
  const [engineDown, setEngineDown] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const openingRef = useRef<string | null>(null); // conversation being loaded; the last click wins

  const refresh = useCallback(async () => {
    const h = await api.health();
    setHealth(h);
    setModelInfo(await model.info());
    if (h.status === "ok") {
      const [k, d, t, hist] = await Promise.all([api.kb(), api.docs(), api.tags(), api.history(HISTORY_LOADED)]);
      setKb(k);
      setDocs(d);
      setTags(t);
      setHistory(hist);
    } else {
      setKb(null);
      setDocs([]);
      setTags([]);
    }
    return h;
  }, []);

  // The Tauri shell starts the engine alongside the window; wait for it to answer.
  useEffect(() => {
    let cancelled = false;
    const started = Date.now();
    const poll = async () => {
      try {
        await refresh();
      } catch {
        if (cancelled) return;
        if (Date.now() - started > STARTUP_TIMEOUT_MS) {
          setStartupError("The assistant's engine did not start. Restart the app; if it keeps happening, contact IT.");
          return;
        }
        setTimeout(poll, 1000);
      }
    };
    poll();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  /** refresh, noting whether the engine answers at all */
  const check = useCallback(
    () => refresh().then(() => setEngineDown(false), () => setEngineDown(true)),
    [refresh],
  );

  const status = health ? modelStatus(health, modelInfo) : "loading";
  const chatAvailable = health?.chat_available ?? false;

  // Start in the machine's default mode; fall back to manuals when Chat mode is turned off.
  useEffect(() => {
    if (!health) return;
    setMode(health.chat_available ? (health.default_mode ?? "manuals") : "manuals");
  }, [health?.chat_available, health?.default_mode]); // eslint-disable-line react-hooks/exhaustive-deps

  // A 2-3 GB model takes a while to load; keep checking until it is ready.
  useEffect(() => {
    if (status !== "loading" || !health) return;
    const timer = setInterval(() => refresh().catch(() => undefined), 2000);
    return () => clearInterval(timer);
  }, [status, health, refresh]);

  // While idle, keep the model status honest (the shell restarts a model server that died, and a
  // remote endpoint may come back).
  // A stopped engine was not noticed: every question said "wait a moment and try again", for good.
  // Checked every few seconds until it is back.
  useEffect(() => {
    if (!engineDown && status !== "ready" && status !== "unreachable") return;
    const timer = setInterval(check, engineDown ? 3_000 : 30_000);
    return () => clearInterval(timer);
  }, [status, engineDown, check]);

  // Preferences: text size, kiosk, and the station filter (dropped if the knowledge base lacks it).
  useEffect(() => {
    document.documentElement.dataset.size = prefs.size;
    savePrefs(prefs);
  }, [prefs]);
  useEffect(() => {
    setFullscreen(prefs.kiosk).catch(() => undefined);
  }, [prefs.kiosk]);
  const station = prefs.station && tags.includes(prefs.station) ? prefs.station : null;

  useEffect(() => {
    if (health?.status !== "ok") return;
    api.suggestions(station ? [station] : []).then(setSuggestions, () => setSuggestions([]));
  }, [health?.status, kb?.kb_version, station]);

  const updatePrefs = (p: Partial<Prefs>) => setPrefs((old) => ({ ...old, ...p }));

  type AskOptions = {
    /** manuals mode: search every manual, ignoring the station filter */
    allManuals?: boolean;
    mode?: Mode;
    /** regenerate / edit: the turn this answer replaces (it must be the last one) */
    replace?: Turn;
    /** manuals picked after a clarifying question */
    docIds?: string[];
    /** the section a picked option stands for (one part of a manual) */
    section?: string;
  };

  // Chat mode: files uploaded for the next question (the engine keeps them with the conversation)
  // where the conversation is: as its latest answer left it, unless the user closed that line
  const [focusOff, setFocusOff] = useState(false);
  useEffect(() => setFocusOff(false), [conversationId]);
  /** where the conversation was before a turn (all of them without one) */
  const placeBefore = (turn?: Turn) => {
    const upTo = turn ? turns.findIndex((t) => t.id === turn.id) : -1;
    const earlier = upTo >= 0 ? turns.slice(0, upTo) : turns;
    return [...earlier].reverse().find((t) => t.result?.focus !== undefined)?.result?.focus ?? null;
  };
  const focus = focusOff ? null : placeBefore();
  const [pendingFiles, setPendingFiles] = useState<Attachment[]>([]);
  const [attaching, setAttaching] = useState(false);
  const [attachError, setAttachError] = useState<string | null>(null);
  const attachFile = async (file: File) => {
    setAttaching(true);
    setAttachError(null);
    try {
      const att = await api.attach(conversationId, file);
      setPendingFiles((fs) => [...fs, att]);
    } catch (e) {
      setAttachError(`${file.name}: ${e instanceof ApiError ? e.message : String(e)}`);
    } finally {
      setAttaching(false);
    }
  };

  const ask = async (question: string, options: AskOptions = {}) => {
    // one question at a time: a double click (or a double tap on a touch screen) sent it twice,
    // and the model worked through both
    if (abortRef.current) return;
    openingRef.current = null; // nor replace a question just asked (seen on a slow laptop)
    const id = newTurnId();
    const askMode = options.mode ?? mode;
    const scope = options.allManuals ? null : station;
    const replaced = options.replace;
    setView("ask");
    const files = askMode === "chat" && !replaced ? pendingFiles : [];
    // an answer's saved turn; a turn that failed or was stopped: the one saved for it, or the one it
    // was to replace (an error on Regenerate left it in History)
    const replaces = replaced ? (replaced.result?.history_id ?? replaced.heldId ?? replaced.replaces) : undefined;
    if (files.length) setPendingFiles([]);
    setTurns((ts) => [
      ...ts.filter((t) => t.id !== replaced?.id),
      { id, question, stage: "selecting", mode: askMode, station: scope, attachments: files.length ? files : undefined,
        startedAt: Date.now(), docIds: options.docIds, section: options.section, replaces },
    ]);
    const update = (patch: Partial<Turn> | ((t: Turn) => Partial<Turn>)) =>
      setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, ...(typeof patch === "function" ? patch(t) : patch) } : t)));
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await api.askStream(
        question,
        scope ? [scope] : [],
        conversationId,
        {
          mode: askMode,
          replaces: replaces ?? null,
          docIds: options.docIds,
          section: options.section,
          attachments: files.map((f) => f.id),
          // Regenerate / edit: where the conversation was before the answer it replaces
          focus: focusOff ? null : placeBefore(replaced),
          focusOff,
        },
        (e) => {
          // one step at a time, the same in both modes: each event replaces what the last one said (a
          // tool's "Reading a section..." stayed up while the answer was being written)
          if (e.stage === "held") update({ heldId: e.history_id });
          else if (e.stage === "selecting") update({ stage: "selecting", activity: "Finding the right section...", reading: undefined });
          else if (e.stage === "reading") update({ stage: "reading", reading: e.sections, activity: undefined });
          else if (e.stage === "tool") update({ stage: "reading", activity: toolActivity(e.tool, e.detail), reading: undefined });
          else if (e.stage === "writing") update({ stage: "reading", draft: "", activity: undefined, reading: undefined });
          else if (e.stage === "retrying") update({ stage: "reading", draft: "", activity: "The model service had a hiccup; trying again..." });
          else if (e.stage === "delta") update((t) => ({ stage: "reading", draft: (t.draft ?? "") + e.text }));
          else if (e.stage === "draft") update({ stage: "reading", draft: e.text });
          else if (e.stage === "done") {
            update({ stage: "done", result: e.result, draft: undefined });
            setFocusOff(false); // the answer says where the conversation is now
          }
          else if (e.stage === "error") {
            update({ stage: "error", error: friendlyError(e.status, e.message) });
            check(); // the model server or the engine may have stopped: show it in the status
          }
        },
        controller.signal,
      );
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
    api.history(HISTORY_LOADED).then(setHistory, () => undefined);
  };

  const deleteConversation = async (id: string) => {
    // its answer still being written would be saved after the delete and bring the conversation back
    if (id === conversationId) stop();
    await api.deleteConversation(id);
    setHistory((h) => h.filter((e) => conversationOf(e) !== id));
    if (id === conversationId) startConversation();
  };

  const stop = () => {
    abortRef.current?.abort();
    abortRef.current = null; // a new question can be asked straight away
    setTurns((ts) =>
      ts.map((t) => (t.stage === "selecting" || t.stage === "reading" ? { ...t, stage: "error", error: "Stopped." } : t)),
    );
  };

  const startConversation = () => {
    stop(); // an answer still being written belongs to the conversation being left
    openingRef.current = null; // a past conversation still loading must not replace the new one
    setTurns([]);
    setPendingFiles([]);
    setAttachError(null);
    setSource(null);
    setConversationId(newConversationId());
  };

  /** Show a past conversation in place of the current one; new questions continue it. */
  const openConversation = async (id: string) => {
    setView("ask");
    if (openingRef.current === null && id === conversationId && turns.length > 0) return;
    openingRef.current = id;
    const busy = turns.some((t) => t.stage === "selecting" || t.stage === "reading");
    if (busy) stop();
    let entries: HistoryEntry[];
    try {
      entries = await api.history(100, id);
    } catch {
      entries = groupConversations(history).find((c) => c.id === id)?.entries ?? [];
    }
    if (openingRef.current !== id) return; // another conversation was clicked meanwhile
    openingRef.current = null;
    setSource(null);
    setConversationId(id);
    setTurns(entries.map(turnFromHistory));
    // go on in the mode the conversation was in (Chat or Manuals)
    const lastMode = entries[entries.length - 1]?.mode;
    if (lastMode && (lastMode === "manuals" || health?.chat_available)) setMode(lastMode);
  };
  const conversations = groupConversations(history);

  const openCitation = (c: Citation, turn: Turn) =>
    setSource({ docId: c.doc_id, nodeId: c.node_id, highlight: turn.result ? highlightTermsIn(turn.result.answer) : [] });

  if (!health) {
    return (
      <div className="startup" role="status">
        <LatticeMark big />
        <p className="brand-name">Lattice</p>
        {startupError ? <p className="text-bad">{startupError}</p> : <Progress text="Starting the assistant..." />}
      </div>
    );
  }

  const shownHealth = health; // known to be there from here on (for askScreen below)
  const kbReady = health.status === "ok" && kb !== null;
  const ready = kbReady && (status === "ready" || status === "demo") && !engineDown;
  const notReadyText = engineDown
    ? "Restarting the assistant's engine..."
    : status === "loading" ? "Waiting for the language model to load..." : "The assistant is not ready yet";

  let main;
  if (!kbReady) {
    main = (
      <Blocker
        icon={<IconBooks size="2.2em" aria-hidden />}
        title="No knowledge base installed"
        text="The assistant answers from a knowledge-base bundle prepared by the web portal team. Ask your supervisor or IT for the latest bundle file (.zip) and import it."
        error={health.kb_error ? `The installed knowledge base could not be loaded: ${health.kb_error}` : null}
        action="Import a knowledge base"
        onAction={() => setManageOpen(true)}
      />
    );
  } else if (status === "missing") {
    main = (
      <Blocker
        icon={<IconCpu size="2.2em" aria-hidden />}
        title="No language model installed"
        text="The assistant needs a language model file (.gguf) to answer questions. It is delivered next to the installer, for example on the same USB drive. Import it once; it stays installed."
        action="Import a model file"
        onAction={() => setManageOpen(true)}
      />
    );
  } else {
    // The three screens stay mounted, the others hidden: a question half typed, a search in the
    // manuals or the history, the manual picked and where the page was scrolled to are all still
    // there on coming back (switching away used to clear them)
    main = (
      <>
        <div className="view-slot" hidden={view !== "ask"}>
          {askScreen()}
        </div>
        <div className="view-slot" hidden={view !== "manuals"}>
          <ManualsView
            key={kb?.kb_version} // a new knowledge base: its own manuals, no search of the old one
            docs={docs}
            station={station}
            onOpenSection={(docId, nodeId, highlight) => setSource({ docId, nodeId, highlight })}
          />
        </div>
        <div className="view-slot" hidden={view !== "history"}>
          <HistoryView
            conversations={conversations}
            activeId={conversationId}
            onOpen={openConversation}
            onDelete={deleteConversation}
          />
        </div>
      </>
    );
  }

  function askScreen() {
    const health = shownHealth;
    if (status === "unreachable") return (
      <Blocker
        icon={<IconCpu size="2.2em" aria-hidden />}
        title="The language model cannot be reached"
        text={`This machine uses a model server on the network (${health.model}), and it is not answering: ${health.llm_detail}. It is checked again every 30 seconds.`}
        action="Open model settings"
        onAction={() => setManageOpen(true)}
      />
    );
    return (
      <>
        {engineDown && (
          <p className="notice notice-bad banner" role="alert">
            The assistant's engine stopped and is being started again; this takes up to a minute. Your
            conversations are kept. If this message stays, restart Lattice.
          </p>
        )}
        {status === "loading" && (
          <div className="banner">
            <Progress text="Loading the language model. This takes up to a minute after start-up..." />
          </div>
        )}
        {status === "failed" && (
          <p className="notice notice-bad banner">
            The language model could not start: {modelInfo?.error} Open Manage to install a model file again.
          </p>
        )}
        <AskView
          turns={turns}
          ready={ready}
          notReadyText={notReadyText}
          docs={docs}
          station={station}
          suggestions={suggestions}
          demo={status === "demo"}
            onAsk={(q, replace) => ask(q, { replace, mode: replace?.mode })}
          onAskAll={(q) => ask(q, { allManuals: true })}
          onAskChat={chatAvailable && !health.chat_documents_only ? (q) => ask(q, { mode: "chat" }) : undefined}
          onRegenerate={(t) => ask(t.question, { mode: t.mode, replace: t, docIds: t.docIds, section: t.section })}
          onPickManuals={(t, docIds, q, section) =>
            // a part of a manual picked ("Single X Classification"): answered from that section; a vague
            // question's meaning is asked as it stands; otherwise the same question, in the picked manuals
            section
              ? ask(q ?? t.question, { mode: t.mode, replace: t, docIds, section })
              : q
                ? ask(q, { mode: t.mode, replace: t })
                : ask(t.question, { mode: t.mode, replace: t, docIds })
          }
          mode={mode}
          onMode={chatAvailable ? setMode : undefined}
          onStop={stop}
          onOpenSource={openCitation}
          onClear={startConversation}
          focus={focus}
          onClearFocus={() => setFocusOff(true)}
          onStep={(way, title) => ask(way === "next" ? `Next: ${title}` : `Go back: ${title}`)}
          onAskSection={(q, docId, section) => ask(q, { docIds: [docId], section })}
          pendingFiles={pendingFiles}
          onAttach={chatAvailable ? attachFile : undefined}
          attaching={attaching}
          attachError={attachError}
          slowModel={health.provider === "local" && status !== "demo" ? slowHint(health.builtin_model_b) : undefined}
          chatDocumentsOnly={health.chat_documents_only ?? false}
          searchNote={searchNote(health)}
          onManage={() => setManageOpen(true)}
        />
      </>
    );
  }

  return (
    <div className={`app${source ? " with-source" : ""}`}>
      <Sidebar
        view={view}
        onView={setView}
        tags={tags}
        prefs={{ ...prefs, station }}
        onPrefs={updatePrefs}
        recent={conversations}
        activeConversation={turns.length ? conversationId : null}
        onRecent={openConversation}
        modelStatus={status}
        kbLabel={kb ? `${kb.documents.length} ${kb.documents.length === 1 ? "document" : "documents"} · ${/^\d/.test(kb.kb_version) ? "v" : ""}${kb.kb_version}` : null}
        onManage={() => setManageOpen(true)}
      />
      <main className="main">{main}</main>
      {source && <SourcePanel target={source} onClose={() => setSource(null)} />}
      {manageOpen && (
        <ManagePanel
          health={health}
          kb={kb}
          modelInfo={modelInfo}
          onClose={() => setManageOpen(false)}
          onChanged={async (reset) => {
            await refresh();
            if (reset) startConversation();
          }}
        />
      )}
    </div>
  );
}

function Blocker({
  icon,
  title,
  text,
  error,
  action,
  onAction,
}: {
  icon: ReactNode;
  title: string;
  text: string;
  error?: string | null;
  action: string;
  onAction: () => void;
}) {
  return (
    <section className="blocker">
      <span className="blocker-icon">{icon}</span>
      <h1>{title}</h1>
      <p className="lede">{text}</p>
      {error && <p className="notice notice-bad">{error}</p>}
      <button type="button" className="btn btn-primary" onClick={onAction}>
        {action}
      </button>
    </section>
  );
}
