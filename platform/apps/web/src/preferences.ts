// Appearance follows the user across devices (#93): after sign-in the server's choice wins over
// this browser's and is written where @zm/ui's ZmProvider (and the pre-paint script) read it;
// every pick in the appearance menu is saved back. ZmProvider skips accents it doesn't know.
import { accentStorageKey, notifyError, schemeStorageKey, type AppearanceChange } from "@zm/ui";
import { api, type Me } from "./session";

/** Writes the user's saved choices to this browser, before the shell's ZmProvider reads them. */
export function applyPreferences(me: Me | null): Me | null {
  if (!me) return me;
  const { color_scheme, accent } = me.preferences;
  try {
    if (color_scheme) localStorage.setItem(schemeStorageKey(me.id), color_scheme);
    if (accent) localStorage.setItem(accentStorageKey(me.id), accent);
  } catch {
    // Blocked storage: this visit starts from the system scheme and the client's accent.
  }
  return me;
}

/** Saves one pick in the appearance menu; it already applies in this browser either way. */
export async function savePreferences(change: AppearanceChange): Promise<void> {
  try {
    if ((await api.PUT("/app/me/preferences", { body: change })).response.ok) return;
  } catch {
    // Offline: same as a refusal.
  }
  notifyError("Your appearance couldn't be saved, so your other devices won't get it.");
}
