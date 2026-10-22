// Per-machine display preferences, kept in the webview's local storage. Every access is guarded:
// storage can be unavailable, and the app must work the same without it.

export interface Prefs {
  size: "standard" | "large";
  station: string | null;
  kiosk: boolean;
}

const KEY = "oasis.prefs.v1";
const DEFAULTS: Prefs = { size: "standard", station: null, kiosk: false };

export function loadPrefs(): Prefs {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? { ...DEFAULTS, ...JSON.parse(raw) } : DEFAULTS;
  } catch {
    return DEFAULTS;
  }
}

export function savePrefs(prefs: Prefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(prefs));
  } catch {
    /* storage unavailable: preferences last for this session only */
  }
}
