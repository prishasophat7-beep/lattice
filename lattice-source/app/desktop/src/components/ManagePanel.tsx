import { useCallback, useEffect, useState } from "react";
import { IconDownload, IconFileImport, IconX } from "@tabler/icons-react";
import {
  api,
  ApiError,
  embedModel,
  FeedbackSummary,
  Health,
  inTauri,
  KbInfo,
  model,
  ModelInfo,
  pickBundleFile,
  pickEmbedModelFile,
  pickExportFile,
  pickModelFile,
  Versions,
} from "../api";
import { Progress } from "../views/AskView";
import AssistantSettings from "./AssistantSettings";

/** The search-by-meaning index: being built, ready, or failed (the failure is explained below it). */
function SemanticPill({ status, progress }: { status?: string | null; progress?: [number, number] | null }) {
  if (status === "ready") return <span className="pill pill-ok">Ready</span>;
  if (status?.startsWith("unavailable")) return <span className="pill pill-warn">Not responding: keyword search only</span>;
  if (status?.startsWith("error")) return <span className="pill pill-warn">Not indexed</span>;
  if (status === "indexing" && progress && progress[1] > 0) {
    // a large library takes many minutes on a processor: say how far it got (keyword search works meanwhile)
    const [done, all] = progress;
    return (
      <span className="pill pill-warn" title="Keyword search works meanwhile">
        Indexing {done.toLocaleString()} of {all.toLocaleString()} sections ({Math.floor((100 * done) / all)}%)
      </span>
    );
  }
  return <span className="pill pill-warn">{status === "indexing" ? "Indexing documents" : "Loading"}</span>;
}

export default function ManagePanel({
  health,
  kb,
  modelInfo,
  onClose,
  onChanged,
}: {
  health: Health;
  kb: KbInfo | null;
  modelInfo: ModelInfo | null;
  onClose: () => void;
  onChanged: (resetConversation: boolean) => Promise<void>;
}) {
  const [versions, setVersions] = useState<Versions | null>(null);
  const [removing, setRemoving] = useState<string | null>(null); // a version waiting for "Remove?" to be confirmed
  const [feedback, setFeedback] = useState<FeedbackSummary | null>(null);
  const [path, setPath] = useState("");
  const [working, setWorking] = useState(false);
  const [confirmOff, setConfirmOff] = useState(false); // "Turn off" deletes the model: asked first
  const [copying, setCopying] = useState<{ kind: "model" | "embed"; text: string } | null>(null); // a model file being copied in
  const [message, setMessage] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);

  const [embedInfo, setEmbedInfo] = useState<ModelInfo | null>(null);

  const load = useCallback(() => {
    api.versions().then(setVersions, () => setVersions(null));
    api.feedbackSummary().then(setFeedback, () => setFeedback(null));
  }, []);
  useEffect(load, [load]);

  // the embedding server's state, while Manage is open (it loads, or restarts after a crash)
  useEffect(() => {
    if (!inTauri) return;
    const poll = () => embedModel.info().then(setEmbedInfo, () => setEmbedInfo(null));
    poll();
    const timer = window.setInterval(poll, 3000);
    return () => window.clearInterval(timer);
  }, []);

  // settings changed but not saved: closing asks first (Escape or a click beside the panel dropped them)
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [askDiscard, setAskDiscard] = useState(false);
  const close = () => (settingsDirty ? setAskDiscard(true) : onClose());

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const run = async (action: () => Promise<string>, resetConversation = true) => {
    setWorking(true);
    setMessage(null);
    try {
      setMessage({ kind: "ok", text: await action() });
      await onChanged(resetConversation);
      load();
    } catch (e) {
      setMessage({ kind: "bad", text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setWorking(false);
    }
  };

  const importFrom = (p: string) =>
    run(async () => {
      const r = await api.importKb(p);
      setPath("");
      const left = r.skipped?.length ? ` Not imported (no text in the export): ${r.skipped.join(", ")}.` : "";
      return `Imported and switched to version ${r.imported}.${left}`;
    });

  const chooseBundle = async () => {
    const p = await pickBundleFile();
    if (p) importFrom(p);
  };

  const chooseModel = async () => {
    const p = await pickModelFile();
    if (!p) return;
    setCopying({ kind: "model", text: "Copying the model file to this computer (a minute or two)..." });
    run(async () => {
      const info = await model.import(p).finally(() => setCopying(null));
      return info.error ? `Model copied, but it did not start: ${info.error}` : `Installed ${info.name}. It is loading now.`;
    }, false); // the documents are the same: the conversation stays
  };

  const chooseEmbedModel = async () => {
    const p = await pickEmbedModelFile();
    if (!p) return;
    setCopying({ kind: "embed", text: "Copying the embedding model to this computer..." });
    run(async () => {
      const info = await embedModel.import(p).finally(() => setCopying(null));
      setEmbedInfo(info);
      return info.error
        ? `Model copied, but it did not start: ${info.error}`
        : `Installed ${info.name}. The documents are indexed in the background; keyword search works meanwhile.`;
    }, false);
  };

  const removeEmbedModel = () =>
    run(async () => {
      setEmbedInfo(await embedModel.remove());
      return "Search by meaning is off. Questions are searched by their words only.";
    }, false);

  const exportFeedback = async () => {
    const p = await pickExportFile();
    if (!p) return;
    run(async () => {
      const r = await api.exportFeedback(p);
      return `Saved ${r.count} feedback ${r.count === 1 ? "entry" : "entries"} to ${r.path}.`;
    }, false);
  };

  return (
    <div className="overlay overlay-side" role="dialog" aria-modal="true" aria-labelledby="manage-title" onClick={close}>
      <div className="panel" onClick={(e) => e.stopPropagation()}>
        <div className="panel-bar">
          <h2 id="manage-title">Manage</h2>
          <button type="button" className="icon-btn" onClick={close} aria-label="Close">
            <IconX size="1.2em" />
          </button>
        </div>

        {askDiscard && (
          <div className="notice notice-bad discard-bar" role="alert">
            <span>The assistant settings have changes that are not saved.</span>
            <span className="discard-actions">
              <button type="button" className="btn btn-small" onClick={() => setAskDiscard(false)}>
                Keep editing
              </button>
              <button type="button" className="btn btn-small btn-ghost" onClick={onClose}>
                Discard changes
              </button>
            </span>
          </div>
        )}

        {message && <p className={`notice ${message.kind === "bad" ? "notice-bad" : "notice-ok"}`}>{message.text}</p>}

        <section className="panel-section">
          <h3 className="side-label">Knowledge base</h3>
          {kb ? (
            <>
              <p>
                Version <span className="mono">{kb.kb_version}</span> ·{" "}
                {kb.source_format === "pageindex" ? "PageIndex" : kb.source_format === "semkb" ? "Knowledge-base folder" : "Markdown"}
                {kb.created_at && ` · built ${new Date(kb.created_at).toLocaleDateString()}`}
              </p>
              <table className="table">
                <thead>
                  <tr>
                    <th>Manual</th>
                    <th className="num">Sections</th>
                    <th>Summaries</th>
                  </tr>
                </thead>
                <tbody>
                  {kb.documents.map((d) => (
                    <tr key={d.doc_id}>
                      <td>
                        {d.title}
                        {d.title !== d.name && <span className="meta block">{d.name}</span>}
                      </td>
                      <td className="num">{d.sections}</td>
                      <td>
                        {d.fallback_summaries === 0 ? (
                          <span className="pill pill-ok">Complete</span>
                        ) : (
                          <span className="pill pill-warn">{d.fallback_summaries} missing</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {kb.skipped && kb.skipped.length > 0 && (
                <p className="notice notice-bad">
                  The export has no text for {kb.skipped.length === 1 ? "this document" : "these documents"}, so{" "}
                  {kb.skipped.length === 1 ? "it was" : "they were"} left out:{" "}
                  <span className="mono">{kb.skipped.join(", ")}</span>. Report this to the web team.
                </p>
              )}
              {kb.catalog_unmatched && kb.catalog_unmatched.length > 0 && (
                <p className="notice notice-bad">
                  The bundle's catalog (kb.json) lists {kb.catalog_unmatched.length === 1 ? "an id" : "ids"} that{" "}
                  {kb.catalog_unmatched.length === 1 ? "matches" : "match"} no document, so titles and station tags
                  are missing for {kb.catalog_unmatched.length === 1 ? "it" : "them"}:{" "}
                  <span className="mono">{kb.catalog_unmatched.join(", ")}</span>. Report this to the web team.
                </p>
              )}
            </>
          ) : (
            <p className="meta">No knowledge base installed.</p>
          )}
          {inTauri ? (
            <button type="button" className="btn" disabled={working} onClick={chooseBundle}>
              <IconFileImport size="1.1em" aria-hidden />
              {working ? "Working..." : "Import bundle..."}
            </button>
          ) : (
            <form
              className="inline-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (path.trim()) importFrom(path.trim());
              }}
            >
              <label htmlFor="bundle-path" className="visually-hidden">
                Bundle path
              </label>
              <input
                id="bundle-path"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder="C:\path\to\knowledge_base.zip"
              />
              <button type="submit" className="btn" disabled={working || !path.trim()}>
                Import
              </button>
            </form>
          )}
          <p className="meta">
            The bundle is checked file by file. If anything is wrong, nothing changes and the current version stays
            active.
          </p>
          {versions && versions.installed.length > 1 && (
            <ul className="versions">
              {versions.installed.map((v) => (
                <li key={v}>
                  <span className="mono">{v}</span>
                  {v === versions.active ? (
                    <span className="pill pill-ok">Active</span>
                  ) : (
                    <>
                      <button
                        type="button"
                        className="btn btn-small"
                        disabled={working}
                        onClick={() => run(async () => (await api.activate(v), `Switched to version ${v}.`))}
                      >
                        Switch to this version
                      </button>
                      {removing === v ? (
                        <span className="history-confirm" role="group" aria-label={`Remove version ${v}?`}>
                          <span className="meta">Remove from this computer?</span>
                          <button
                            type="button"
                            className="btn btn-small btn-danger"
                            disabled={working}
                            onClick={() => {
                              setRemoving(null);
                              run(async () => (await api.removeVersion(v), `Removed version ${v}.`), false);
                            }}
                          >
                            Remove
                          </button>
                          <button type="button" className="btn btn-small btn-ghost" onClick={() => setRemoving(null)}>
                            Cancel
                          </button>
                        </span>
                      ) : (
                        <button type="button" className="btn btn-small btn-ghost" disabled={working}
                          onClick={() => setRemoving(v)}>
                          Remove
                        </button>
                      )}
                    </>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="panel-section">
          <h3 className="side-label">Assistant</h3>
          <AssistantSettings onSaved={() => onChanged(false)} onDirty={setSettingsDirty} />
        </section>

        {inTauri && health.provider !== "remote" && (
          <section className="panel-section">
            <h3 className="side-label">Built-in model file</h3>
            {modelInfo?.name ? (
              <p>
                <span className="mono">{modelInfo.name}</span>{" "}
                {modelInfo.size ? <span className="meta">{fileSize(modelInfo.size)}</span> : null}{" "}
                {modelInfo.error ? (
                  <span className="pill pill-warn">Not running</span>
                ) : health.llm_ready ? (
                  <span className="pill pill-ok">Ready</span>
                ) : (
                  <span className="pill pill-warn">Loading</span>
                )}
              </p>
            ) : (
              <p className="meta">No model installed.</p>
            )}
            {modelInfo?.error && <p className="notice notice-bad">{modelInfo.error}</p>}
            <button type="button" className="btn" disabled={working} onClick={chooseModel}>
              <IconFileImport size="1.1em" aria-hidden />
              {modelInfo?.name ? "Replace model file..." : "Import model file..."}
            </button>
            {copying?.kind === "model" && <Progress text={copying.text} />}
            <p className="meta">Copying a model takes a minute or two. It replaces the current one.</p>
          </section>
        )}

        {(inTauri || health.semantic_model) && (
          <section className="panel-section">
            <h3 className="side-label">Search by meaning</h3>
            {health.semantic_model && !health.semantic_local ? (
              <p className="meta">
                Using the embedding server set under Assistant (<span className="mono">{health.semantic_model}</span>
                ). <SemanticPill status={health.semantic} progress={health.semantic_progress} />
              </p>
            ) : embedInfo?.name || health.semantic_model ? (
              <p>
                <span className="mono">{embedInfo?.name ?? health.semantic_model}</span>{" "}
                {embedInfo?.size ? <span className="meta">{fileSize(embedInfo.size)}</span> : null}{" "}
                {embedInfo?.error ? <span className="pill pill-warn">Not running</span> : <SemanticPill status={health.semantic} progress={health.semantic_progress} />}
              </p>
            ) : (
              <p className="meta">Off: questions are searched by their words only.</p>
            )}
            {embedInfo?.error && <p className="notice notice-bad">{embedInfo.error}</p>}
            {health.semantic?.startsWith("error") && (
              <p className="notice notice-bad">The documents could not be indexed: {health.semantic.slice(7)}</p>
            )}
            {inTauri && (
              <div className="btn-row">
                <button type="button" className="btn" disabled={working} onClick={chooseEmbedModel}>
                  <IconFileImport size="1.1em" aria-hidden />
                  {embedInfo?.name ? "Replace embedding model..." : "Import embedding model..."}
                </button>
                {embedInfo?.name && !confirmOff && (
                  <button type="button" className="btn" disabled={working} onClick={() => setConfirmOff(true)}>
                    Turn off
                  </button>
                )}
              </div>
            )}
            {confirmOff && (
              <div className="notice confirm-row" role="alertdialog" aria-label="Turn off search by meaning">
                <span>Turn off search by meaning and delete the embedding model from this computer?</span>
                <button
                  type="button"
                  className="btn btn-small"
                  disabled={working}
                  onClick={() => {
                    setConfirmOff(false);
                    removeEmbedModel();
                  }}
                >
                  Turn off and delete
                </button>
                <button type="button" className="btn btn-small btn-ghost" onClick={() => setConfirmOff(false)}>
                  Cancel
                </button>
              </div>
            )}
            {copying?.kind === "embed" && <Progress text={copying.text} />}
            <p className="meta">
              Finds sections worded differently from the question, and questions in other languages. Runs on this
              computer, offline (for example embeddinggemma-300M-Q8_0.gguf, about 330 MB; uses about 600 MB of memory).
              The documents are indexed once, in the background.
            </p>
          </section>
        )}

        <section className="panel-section">
          <h3 className="side-label">Feedback</h3>
          {feedback && (
            <p>
              {feedback.total} {feedback.total === 1 ? "vote" : "votes"}: {feedback.helpful} helpful, {feedback.wrong}{" "}
              wrong
            </p>
          )}
          {inTauri && (
            <button type="button" className="btn" disabled={working || !feedback?.total} onClick={exportFeedback}>
              <IconDownload size="1.1em" aria-hidden />
              Export for the web team...
            </button>
          )}
          <p className="meta">
            Answers marked wrong, with the question, answer and cited sections, so the knowledge-base team can fix the source.
          </p>
        </section>

        <section className="panel-section">
          <h3 className="side-label">Engine</h3>
          <p className="meta">
            Model: <span className="mono">{health.model}</span> · engine {health.engine_version}
            {health.engine_built && ` (built ${health.engine_built})`}
            {health.requests_left_today !== null && ` · ${health.requests_left_today} test requests left today`}
          </p>
        </section>
      </div>
    </div>
  );
}

/** "532 MB", "2.7 GB": a model file's size as Manage shows it. */
function fileSize(bytes: number): string {
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.round(bytes / 1e6)} MB`;
}
