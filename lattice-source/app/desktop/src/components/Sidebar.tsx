import LatticeMark from "./LatticeMark";
import {
  IconBooks,
  IconHistory,
  IconMaximize,
  IconMessageQuestion,
  IconMinimize,
  IconSettings,
  IconTextSize,
} from "@tabler/icons-react";
import type { Prefs } from "../prefs";
import type { Conversation, View } from "../types";

export type ModelStatus = "ready" | "loading" | "missing" | "failed" | "demo" | "unreachable";

const NAV: { view: View; label: string; Icon: typeof IconBooks }[] = [
  { view: "ask", label: "Ask", Icon: IconMessageQuestion },
  { view: "manuals", label: "Manuals", Icon: IconBooks },
  { view: "history", label: "History", Icon: IconHistory },
];

const STATUS_TEXT: Record<ModelStatus, string> = {
  ready: "Model ready",
  loading: "Model loading",
  missing: "No model installed",
  failed: "Model stopped",
  demo: "Demo mode",
  unreachable: "Model server unreachable",
};

export default function Sidebar({
  view,
  onView,
  tags,
  prefs,
  onPrefs,
  recent,
  activeConversation,
  onRecent,
  modelStatus,
  kbLabel,
  onManage,
}: {
  view: View;
  onView: (v: View) => void;
  tags: string[];
  prefs: Prefs;
  onPrefs: (p: Partial<Prefs>) => void;
  recent: Conversation[];
  activeConversation: string | null;
  onRecent: (conversationId: string) => void;
  modelStatus: ModelStatus;
  kbLabel: string | null;
  onManage: () => void;
}) {
  return (
    <nav className="sidebar" aria-label="Main">
      <div className="brand">
        <LatticeMark />
        <span className="brand-name">Lattice</span>
      </div>

      <ul className="nav">
        {NAV.map(({ view: v, label, Icon }) => (
          <li key={v}>
            <button
              type="button"
              className={`nav-item${view === v ? " active" : ""}`}
              aria-current={view === v ? "page" : undefined}
              onClick={() => onView(v)}
            >
              <Icon size="1.2em" aria-hidden />
              {label}
            </button>
          </li>
        ))}
      </ul>

      {tags.length > 0 && (
        <div className="side-group">
          <p className="side-label" id="station-label">
            Station
          </p>
          <div className="chips" role="radiogroup" aria-labelledby="station-label">
            {[null, ...tags].map((t) => (
              <button
                key={t ?? "all"}
                type="button"
                role="radio"
                aria-checked={prefs.station === t}
                className={`chip${prefs.station === t ? " on" : ""}`}
                onClick={() => onPrefs({ station: t })}
              >
                {t ?? "All"}
              </button>
            ))}
          </div>
        </div>
      )}

      {recent.length > 0 && (
        <div className="side-group side-recent">
          <p className="side-label">Recent</p>
          <ul className="recent">
            {recent.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  className={`recent-item${c.id === activeConversation ? " active" : ""}`}
                  aria-current={c.id === activeConversation ? "true" : undefined}
                  onClick={() => onRecent(c.id)}
                  title={c.entries[0].question}
                >
                  {c.entries[0].question}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="side-footer">
        <div className="status-line">
          <span className={`dot dot-${modelStatus}`} aria-hidden="true" />
          {STATUS_TEXT[modelStatus]}
        </div>
        {kbLabel && <div className="status-line meta">{kbLabel}</div>}
        <div className="side-tools">
          <button
            type="button"
            className={`icon-btn${prefs.size === "large" ? " on" : ""}`}
            aria-pressed={prefs.size === "large"}
            title={prefs.size === "large" ? "Standard text size" : "Large text and buttons"}
            aria-label="Large text and buttons"
            onClick={() => onPrefs({ size: prefs.size === "large" ? "standard" : "large" })}
          >
            <IconTextSize size="1.2em" />
          </button>
          <button
            type="button"
            className={`icon-btn${prefs.kiosk ? " on" : ""}`}
            aria-pressed={prefs.kiosk}
            title={prefs.kiosk ? "Leave full screen" : "Full screen (kiosk)"}
            aria-label="Full screen"
            onClick={() => onPrefs({ kiosk: !prefs.kiosk })}
          >
            {prefs.kiosk ? <IconMinimize size="1.2em" /> : <IconMaximize size="1.2em" />}
          </button>
          <button type="button" className="icon-btn" title="Manage" aria-label="Manage" onClick={onManage}>
            <IconSettings size="1.2em" />
          </button>
        </div>
      </div>
    </nav>
  );
}
