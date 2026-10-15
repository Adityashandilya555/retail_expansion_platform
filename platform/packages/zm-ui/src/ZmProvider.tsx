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

export const ACCENT_STORAGE_KEY = "zm:accent";
/** Also read by the inline script in each app's index.html, before React loads. */
export const SCHEME_STORAGE_KEY = "zm:color-scheme";

const colorSchemeManager = localStorageColorSchemeManager({ key: SCHEME_STORAGE_KEY });

type Appearance = { accent: Accent; preset: Preset; setAccent: (accent: Accent) => void };
const AppearanceContext = createContext<Appearance | null>(null);

function storedAccent(): string | null {
  try {
    return localStorage.getItem(ACCENT_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function ZmProvider({
  client,
  forceColorScheme,
  env,
  children,
}: {
  client?: ZmClientLook;
  /** For previews and tests; users pick the scheme in the appearance menu. */
  forceColorScheme?: "light" | "dark";
  env?: "default" | "test";
  children: ReactNode;
}) {
  const preset = useMemo(() => resolvePreset(client?.theme), [client?.theme]);
  const [userAccent, setUserAccent] = useState<string | null>(storedAccent);
  const accent = resolveAccent({ user: userAccent, client: client?.accent, preset });

  useEffect(() => {
    if (client?.accent && !isAccent(client.accent)) {
      console.warn(`[zm-ui] unknown accent "${client.accent}", using "${preset.defaultAccent}"`);
    }
  }, [client?.accent, preset.defaultAccent]);

  const setAccent = useCallback((next: Accent) => {
    setUserAccent(next);
    try {
      localStorage.setItem(ACCENT_STORAGE_KEY, next);
    } catch {
      // Private mode or blocked storage: the choice lasts for this session only.
    }
  }, []);

  const theme = useMemo(() => createZmTheme({ preset, accent }), [preset, accent]);
  const appearance = useMemo(() => ({ accent, preset, setAccent }), [accent, preset, setAccent]);

  return (
    <MantineProvider
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
