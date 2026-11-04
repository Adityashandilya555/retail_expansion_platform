// Appearance follows the user across devices (#93): on every `GET /app/me` (sign-in, load, tab
// focus, reconnect) the server's choice wins over this browser's and is written where @zm/ui's
// ZmProvider (and the pre-paint script) read it; every pick in the appearance menu is saved back.
// A pick the server didn't get stays unsynced, so it wins until a later `GET /app/me` saves it;
// a choice kept here from before #93 is uploaded when the server has none. ZmProvider skips
// accents it doesn't know.
import { accentStorageKey, appearanceScope, isAccent, notifyError, schemeStorageKey, type AppearanceChange } from "@zm/ui";
import { api, type Me } from "./session";

const KEYS = ["color_scheme", "accent"] as const;
const unsyncedKey = (userId: string) => `zm:appearance-unsynced:${appearanceScope(userId)}`;
const unsynced = (userId: string) => localStorage.getItem(unsyncedKey(userId))?.split(",") ?? [];

/** This browser's choices for the user; anything the menu couldn't have stored is left out. */
function stored(userId: string): AppearanceChange {
  const color_scheme = localStorage.getItem(schemeStorageKey(userId));
  const accent = localStorage.getItem(accentStorageKey(userId));
  return {
    ...((color_scheme === "light" || color_scheme === "dark" || color_scheme === "auto") && { color_scheme }),
    ...(isAccent(accent) && { accent }),
  };
}

/** Writes the user's saved choices to this browser, before the shell's ZmProvider reads them. */
export function applyPreferences(me: Me | null): Me | null {
  if (!me) return me;
  try {
    const pending = unsynced(me.id);
    const here = stored(me.id);
    const upload: AppearanceChange = {};
    for (const key of KEYS) {
      const saved = me.preferences[key];
      const storageKey = key === "accent" ? accentStorageKey(me.id) : schemeStorageKey(me.id);
      if (saved && !pending.includes(key)) localStorage.setItem(storageKey, saved);
      else if (here[key]) Object.assign(upload, { [key]: here[key] });
    }
    if (Object.keys(upload).length) void put(me.id, upload);
  } catch {
    // Blocked storage: this visit starts from the system scheme and the client's accent.
  }
  return me;
}

/** PUTs the change; its keys stay unsynced for this user until a PUT of them succeeds. */
async function put(userId: string, change: AppearanceChange): Promise<boolean> {
  let ok = false;
  try {
    ok = (await api.PUT("/app/me/preferences", { body: change })).response.ok;
  } catch {
    // Offline: same as a refusal.
  }
  try {
    const sent = Object.keys(change);
    const left = unsynced(userId).filter((key) => !sent.includes(key));
    if (!ok) left.push(...sent);
    if (left.length) localStorage.setItem(unsyncedKey(userId), left.join(","));
    else localStorage.removeItem(unsyncedKey(userId));
  } catch {
    // Blocked storage: the pick lasts for this visit only anyway.
  }
  return ok;
}

/** Saves one pick in the appearance menu; it already applies in this browser either way. */
export async function savePreferences(userId: string, change: AppearanceChange): Promise<void> {
  if (await put(userId, change)) return;
  notifyError("We couldn't save your appearance to your account yet. It stays in this browser, and we'll try again.");
}
