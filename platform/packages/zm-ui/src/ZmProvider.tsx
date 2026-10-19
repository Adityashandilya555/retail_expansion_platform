import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "@fontsource-variable/inter";
import "@fontsource-variable/plus-jakarta-sans";
import "@fontsource-variable/jetbrains-mono";
import { MantineProvider, localStorageColorSchemeManager } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { type Accent, type Preset, createZmTheme, isAccent, resolveAccent, resolvePreset } from "./theme";

/** The client's look as the API sends it (`/auth/workspace`, `GET /app/ui`): preset key + default accent. */
export type ZmClientLook = { theme?: string | null; accent?: string | null };

/**
 * Appearance is stored per person, so people sharing a browser don't inherit each other's
 * choices. Signed out (landing, login) uses the `anon` scope. While someone is signed in,
 * `zm:appearance-user` names their scope; the inline script in each app's index.html reads it
 * before React loads (no flash), and sign-out clears it.
 */
export const APPEARANCE_USER_KEY = "zm:appearance-user";
const ANON = "anon";

export function appearanceScope(userId?: string | null): string {
  return userId ? encodeURIComponent(userId) : ANON;
}

export function accentStorageKey(userId?: string | null): string {
  return `zm:accent:${appearanceScope(userId)}`;
}

/** Also built by the inline script in each app's index.html. */
export function schemeStorageKey(userId?: string | null): string {
  return `zm:color-scheme:${appearanceScope(userId)}`;
}

type Appearance = { accent: Accent; preset: Preset; setAccent: (accent: Accent) => void };
const AppearanceContext = createContext<Appearance | null>(null);

function storedAccent(userId?: string | null): string | null {
  try {
    return localStorage.getItem(accentStorageKey(userId));
  } catch {
    return null;
  }
}

function rememberUser(userId?: string | null): void {
  try {
    if (userId) localStorage.setItem(APPEARANCE_USER_KEY, appearanceScope(userId));
    else localStorage.removeItem(APPEARANCE_USER_KEY);
  } catch {
    // Blocked storage: the next load just starts from the system scheme.
  }
}

export function ZmProvider({
  client,
  userId,
  forceColorScheme,
  env,
  children,
}: {
  client?: ZmClientLook;
  /** The signed-in user (from the API); omit when signed out. Scopes the stored appearance. */
  userId?: string | null;
  /** For previews and tests; users pick the scheme in the appearance menu. */
  forceColorScheme?: "light" | "dark";
  env?: "default" | "test";
  children: ReactNode;
}) {
  const preset = useMemo(() => resolvePreset(client?.theme), [client?.theme]);
  const [stored, setStored] = useState(() => ({ userId, accent: storedAccent(userId) }));
  if (stored.userId !== userId) setStored({ userId, accent: storedAccent(userId) }); // user switched
  const userAccent = stored.accent;
  const colorSchemeManager = useMemo(
    () => localStorageColorSchemeManager({ key: schemeStorageKey(userId) }),
    [userId],
  );

  useEffect(() => rememberUser(userId), [userId]);
  const accent = resolveAccent({ user: userAccent, client: client?.accent, preset });

  useEffect(() => {
    if (client?.accent && !isAccent(client.accent)) {
      console.warn(`[zm-ui] unknown accent "${client.accent}", using "${preset.defaultAccent}"`);
    }
  }, [client?.accent, preset.defaultAccent]);

  const setAccent = useCallback(
    (next: Accent) => {
      setStored({ userId, accent: next });
      try {
        localStorage.setItem(accentStorageKey(userId), next);
      } catch {
        // Private mode or blocked storage: the choice lasts for this session only.
      }
    },
    [userId],
  );

  const theme = useMemo(() => createZmTheme({ preset, accent }), [preset, accent]);
  const appearance = useMemo(() => ({ accent, preset, setAccent }), [accent, preset, setAccent]);

  return (
    <MantineProvider
      key={appearanceScope(userId)}
      theme={theme}
      colorSchemeManager={colorSchemeManager}
      defaultColorScheme="auto"
      forceColorScheme={forceColorScheme}
      env={env}
    >
      <Notifications />
      <AppearanceContext.Provider value={appearance}>{children}</AppearanceContext.Provider>
    </MantineProvider>
  );
}

export function useAppearance(): Appearance {
  const appearance = useContext(AppearanceContext);
  if (!appearance) throw new Error("useAppearance must be used inside <ZmProvider>");
  return appearance;
}
