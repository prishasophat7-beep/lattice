import { useEffect, useState } from "react";
import { IconPlugConnected } from "@tabler/icons-react";
import { api, ApiError, AppConfig, Connection, ConfigUpdate, EmbeddingConnection, model } from "../api";

type TestResult = { ok: boolean; detail: string; models: string[] };

const NEW_REMOTE: Connection = { kind: "remote", base_url: "", model: "", output_mode: "native", reasoning: false };

function strip(c: Connection & { api_key_set: boolean }): Connection {
  const { api_key_set: _set, ...rest } = c;
  return rest;
}

/** Manage → Assistant: profile, Chat mode defaults and which language models answer. */
export default function AssistantSettings({
  onSaved,
  onDirty,
}: {
  onSaved: () => Promise<void>;
  /** whether the form has changes not saved yet (closing Manage asks before dropping them) */
  onDirty?: (dirty: boolean) => void;
}) {
  const [saved, setSaved] = useState<AppConfig | null>(null);
  const [baseline, setBaseline] = useState("");
  const [form, setForm] = useState<Omit<ConfigUpdate, "connection" | "chat_connection" | "embeddings"> | null>(null);
  const [main, setMain] = useState<Connection>({ ...NEW_REMOTE, kind: "local" });
  const [chat, setChat] = useState<Connection | null>(null); // null: same model as Manuals mode
  const [mainKey, setMainKey] = useState(""); // empty: keep the stored key
  const [chatKey, setChatKey] = useState("");
  const [emb, setEmb] = useState<EmbeddingConnection | null>(null); // null: keyword search only
  const [embKey, setEmbKey] = useState("");
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "bad"; text: string } | null>(null);

  const load = (c: AppConfig) => {
    const loaded = {
      form: { profile: c.profile, default_mode: c.default_mode, work_topics_only: c.work_topics_only },
      main: strip(c.connection),
      chat: c.chat_connection ? strip(c.chat_connection) : null,
      emb: c.embeddings ? { base_url: c.embeddings.base_url, model: c.embeddings.model } : null,
      keys: ["", "", ""],
    };
    setSaved(c);
    setForm(loaded.form);
    setMain(loaded.main);
    setChat(loaded.chat);
    setEmb(loaded.emb);
    setMainKey("");
    setChatKey("");
    setEmbKey("");
    setBaseline(JSON.stringify(loaded));
  };
  useEffect(() => {
    api.config().then(load, (e) => setMessage({ kind: "bad", text: e instanceof ApiError ? e.message : String(e) }));
  }, []);
  const current = JSON.stringify({ form, main, chat, emb, keys: [mainKey, chatKey, embKey] });
  const dirty = !!baseline && current !== baseline;
  useEffect(() => onDirty?.(dirty), [dirty]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!form || !saved) return message ? <p className="notice notice-bad">{message.text}</p> : null;

  const set = (patch: Partial<typeof form>) => setForm({ ...form, ...patch });
  const key = (typed: string) => (typed.trim() ? typed.trim() : null);

  const save = async () => {
    setWorking(true);
    setMessage(null);
    try {
      const wasLocal = saved.connection.kind === "local";
      const next = await api.saveConfig({
        ...form,
        connection: { ...main, api_key: key(mainKey) },
        chat_connection: form.profile === "office" && chat ? { ...chat, api_key: key(chatKey) } : null,
        embeddings: form.profile === "office" && emb ? { ...emb, api_key: key(embKey) } : null,
      });
      // the built-in model server only runs while Manuals mode uses it (it needs 1-3 GB of memory)
      if (wasLocal !== (next.connection.kind === "local")) await model.setEnabled(next.connection.kind === "local");
      load(next);
      await onSaved();
      setMessage({ kind: "ok", text: "Settings saved." });
    } catch (e) {
      setMessage({ kind: "bad", text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setWorking(false);
    }
  };

  return (
    <div className="settings">
      <fieldset>
        <legend>Where this machine is used</legend>
        <label className="radio">
          <input type="radio" checked={form.profile === "line"} onChange={() => set({ profile: "line" })} />
          <span>
            <strong>Assembly line</strong>
            <span className="meta block">Answers only from the documents, with citations. No Chat mode.</span>
          </span>
        </label>
        <label className="radio">
          <input type="radio" checked={form.profile === "office"} onChange={() => set({ profile: "office" })} />
          <span>
            <strong>Office</strong>
            <span className="meta block">
              Manuals mode plus Chat mode, which can also answer from general knowledge (clearly marked).
            </span>
          </span>
        </label>
      </fieldset>

      <fieldset>
        <legend>{form.profile === "office" ? "Model for Manuals mode" : "Language model"}</legend>
        <label className="radio">
          <input type="radio" checked={main.kind === "local"} onChange={() => setMain({ ...main, kind: "local" })} />
          <span>
            <strong>Built-in (this computer)</strong>
            <span className="meta block">Works offline. Suited to questions about the documents.</span>
          </span>
        </label>
        <label className="radio">
          <input type="radio" checked={main.kind === "remote"} onChange={() => setMain({ ...main, kind: "remote" })} />
          <span>
            <strong>Model server on the network</strong>
            <span className="meta block">An OpenAI-compatible endpoint, e.g. vLLM.</span>
          </span>
        </label>
        {main.kind === "remote" && (
          <RemoteFields
            id="main"
            conn={main}
            onChange={setMain}
            apiKey={mainKey}
            onApiKey={setMainKey}
            keySaved={saved.connection.api_key_set}
            test={(c) => api.testConnection(c, "main")}
          />
        )}
      </fieldset>

      {form.profile === "office" && (
        <fieldset>
          <legend>Chat mode</legend>
          <label className="field">
            <span>Start in</span>
            <select
              value={form.default_mode}
              onChange={(e) => set({ default_mode: e.target.value as ConfigUpdate["default_mode"] })}
            >
              <option value="manuals">Manuals mode</option>
              <option value="chat">Chat mode</option>
            </select>
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={form.work_topics_only}
              onChange={(e) => set({ work_topics_only: e.target.checked })}
            />
            Work topics only (politely decline anything unrelated to work)
          </label>
          <span className="meta">Model for Chat mode</span>
          <label className="radio">
            <input type="radio" checked={chat === null} onChange={() => setChat(null)} />
            <span>
              <strong>Same as Manuals mode</strong>
            </span>
          </label>
          <label className="radio">
            <input type="radio" checked={chat !== null} onChange={() => setChat(chat ?? { ...NEW_REMOTE })} />
            <span>
              <strong>A different model server</strong>
              <span className="meta block">
                For example a strong network model for chat, while Manuals-mode questions stay on the built-in model.
              </span>
            </span>
          </label>
          {chat && (
            <RemoteFields
              id="chat"
              conn={chat}
              onChange={setChat}
              apiKey={chatKey}
              onApiKey={setChatKey}
              keySaved={saved.chat_connection?.api_key_set ?? false}
              test={(c) => api.testConnection(c, "chat")}
            />
          )}
        </fieldset>
      )}

      {form.profile === "office" && (
        <fieldset>
          <legend>Search by meaning: embedding server</legend>
          <label className="check">
            <input
              type="checkbox"
              checked={emb !== null}
              onChange={(e) => setEmb(e.target.checked ? (emb ?? { base_url: "", model: "" }) : null)}
            />
            Use an embedding model on a server
          </label>
          <span className="meta block">
            Finds sections worded differently from the question, or asked in another language, in Manuals and Chat
            mode. Used instead of an embedding model on this computer (see Search by meaning below). Office machines
            only: an assembly-line machine uses the embedding model on this computer.
          </span>
          {emb && (
            <EmbeddingFields
              conn={emb}
              onChange={setEmb}
              apiKey={embKey}
              onApiKey={setEmbKey}
              keySaved={saved.embeddings?.api_key_set ?? false}
            />
          )}
        </fieldset>
      )}

      {message && <p className={`notice ${message.kind === "bad" ? "notice-bad" : "notice-ok"}`}>{message.text}</p>}
      <button type="button" className="btn btn-primary" disabled={working} onClick={save}>
        {working ? "Saving..." : "Save settings"}
      </button>
      <p className="meta">API keys are stored in this user's app data folder on this computer.</p>
    </div>
  );
}

function RemoteFields({
  id,
  conn,
  onChange,
  apiKey,
  onApiKey,
  keySaved,
  test,
}: {
  id: string;
  conn: Connection;
  onChange: (c: Connection) => void;
  apiKey: string;
  onApiKey: (k: string) => void;
  keySaved: boolean;
  test: (c: Connection & { api_key: string | null }) => Promise<TestResult>;
}) {
  const [result, setResult] = useState<TestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const change = (patch: Partial<Connection>) => {
    onChange({ ...conn, ...patch });
    setResult(null);
  };

  const runTest = async () => {
    setTesting(true);
    setResult(null);
    try {
      setResult(await test({ ...conn, api_key: apiKey.trim() ? apiKey.trim() : null }));
    } catch (e) {
      setResult({ ok: false, detail: e instanceof ApiError ? e.message : String(e), models: [] });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="remote-fields">
      <label className="field">
        <span>Endpoint URL</span>
        <input
          value={conn.base_url}
          placeholder="http://llm-server.company.lan:8000/v1"
          onChange={(e) => change({ base_url: e.target.value })}
        />
      </label>
      <label className="field">
        <span>Model</span>
        <input
          value={conn.model}
          list={`models-${id}`}
          placeholder="as the server names it"
          onChange={(e) => change({ model: e.target.value })}
        />
        <datalist id={`models-${id}`}>
          {result?.models.map((m) => <option key={m} value={m} />)}
        </datalist>
      </label>
      <label className="field">
        <span>API key</span>
        <input
          type="password"
          value={apiKey}
          autoComplete="off"
          placeholder={keySaved ? "saved (leave empty to keep)" : "if the server needs one"}
          onChange={(e) => {
            onApiKey(e.target.value);
            setResult(null);
          }}
        />
      </label>
      <label className="check">
        <input type="checkbox" checked={conn.reasoning} onChange={(e) => change({ reasoning: e.target.checked })} />
        Reasoning model (thinks before answering; gets more room for it)
      </label>
      <label className="field">
        <span>Structured replies</span>
        <select
          value={conn.output_mode}
          onChange={(e) => change({ output_mode: e.target.value as Connection["output_mode"] })}
        >
          <option value="native">JSON schema (vLLM, llama.cpp)</option>
          <option value="tool">Tool call</option>
          <option value="prompted">Prompt only (any model)</option>
        </select>
      </label>
      <div className="inline-actions">
        <button type="button" className="btn btn-small" disabled={testing || !conn.base_url} onClick={runTest}>
          <IconPlugConnected size="1.1em" aria-hidden />
          Test connection
        </button>
        {result && (
          <span className={`meta ${result.ok ? "text-good" : "text-bad"}`}>
            {result.ok ? `Connected${result.models.length ? ` · ${result.models.length} models available` : ""}` : result.detail}
          </span>
        )}
      </div>
    </div>
  );
}

function EmbeddingFields({
  conn,
  onChange,
  apiKey,
  onApiKey,
  keySaved,
}: {
  conn: EmbeddingConnection;
  onChange: (c: EmbeddingConnection) => void;
  apiKey: string;
  onApiKey: (k: string) => void;
  keySaved: boolean;
}) {
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);
  const [testing, setTesting] = useState(false);
  const change = (patch: Partial<EmbeddingConnection>) => {
    onChange({ ...conn, ...patch });
    setResult(null);
  };
  const runTest = async () => {
    setTesting(true);
    setResult(null);
    try {
      setResult(await api.testEmbeddings({ ...conn, api_key: apiKey.trim() ? apiKey.trim() : null }));
    } catch (e) {
      setResult({ ok: false, detail: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="remote-fields">
      <label className="field">
        <span>Endpoint URL</span>
        <input
          value={conn.base_url}
          placeholder="http://embeddings.company.lan:8080/v1"
          onChange={(e) => change({ base_url: e.target.value })}
        />
      </label>
      <label className="field">
        <span>Model</span>
        <input value={conn.model} placeholder="as the server names it" onChange={(e) => change({ model: e.target.value })} />
      </label>
      <label className="field">
        <span>API key</span>
        <input
          type="password"
          value={apiKey}
          autoComplete="off"
          placeholder={keySaved ? "saved (leave empty to keep)" : "if the server needs one"}
          onChange={(e) => {
            onApiKey(e.target.value);
            setResult(null);
          }}
        />
      </label>
      <div className="inline-actions">
        <button
          type="button"
          className="btn btn-small"
          disabled={testing || !conn.base_url || !conn.model}
          onClick={runTest}
        >
          <IconPlugConnected size="1.1em" aria-hidden />
          Test connection
        </button>
        {result && <span className={`meta ${result.ok ? "text-good" : "text-bad"}`}>{result.detail}</span>}
      </div>
    </div>
  );
}
