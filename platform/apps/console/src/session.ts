// Owner sign-in (#22): like the client app's session (apps/web/src/session.ts), on /owner/auth.
// The access token lives only in memory; one refresh at a time, so a doubled call can't look
// like a reused refresh token.
import { createApiClient, type components } from "@zm/api-client";

type TokenOut = components["schemas"]["TokenOut"];
export type OwnerMe = components["schemas"]["OwnerMe"];

let token: string | null = null;
let timer: ReturnType<typeof setTimeout> | undefined;
let inflight: Promise<boolean> | null = null;
// Bumped by forget(): a refresh started before a sign-out must not bring the session back.
let generation = 0;

export const api = createApiClient(import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000", () => token);

function keep(t: TokenOut) {
  token = t.access_token;
  clearTimeout(timer);
  timer = setTimeout(() => void refresh(), Math.max(t.expires_in - 60, 5) * 1000);
}

export function forget() {
  generation += 1;
  clearTimeout(timer);
  token = null;
  inflight = null;
}

export const signedIn = () => token !== null;

export async function signIn(email: string, password: string): Promise<string | null> {
  try {
    const { data } = await api.POST("/owner/auth/login", { body: { email, password } });
    if (!data) return "Wrong email or password.";
    keep(data);
    return null;
  } catch {
    return "Can't reach Z-Matrix right now. Check your connection and try again.";
  }
}

export function refresh(): Promise<boolean> {
  const started = generation;
  inflight ??= (async () => {
    try {
      const { data } = await api.POST("/owner/auth/refresh");
      if (started !== generation) return false; // signed out meanwhile: drop the new token
      if (data) keep(data);
      else forget();
      return data !== undefined;
    } catch {
      return false;
    } finally {
      if (started === generation) inflight = null; // don't clear a newer session's refresh
    }
  })();
  return inflight;
}

export async function signOut() {
  forget();
  try {
    await api.POST("/owner/auth/logout");
  } catch {
    // Offline: the cookie stays until it expires; the token is already gone.
  }
}

export async function loadMe(): Promise<OwnerMe | null> {
  const { data } = await api.GET("/owner/me");
  return data ?? null;
}
