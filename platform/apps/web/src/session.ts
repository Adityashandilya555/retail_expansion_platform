// Client sign-in (#22): the access token lives only in memory; the refresh token is an httpOnly
// cookie the API sets on /auth. One refresh at a time: two with the same cookie would look like a
// reused token, and the API would revoke the whole session.
import { createApiClient, type components } from "@zm/api-client";

type TokenOut = components["schemas"]["TokenOut"];
export type Me = components["schemas"]["Me"];
/** The workspace behind the code, with the client's look for the sign-in page. */
export type Workspace = components["schemas"]["WorkspaceOut"] & { code: string };
export type Outcome<T> = { ok: true; value: T } | { ok: false; message: string };

let token: string | null = null;
let timer: ReturnType<typeof setTimeout> | undefined;
let inflight: Promise<boolean> | null = null;

export const api = createApiClient(import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000", () => token);

const WORKSPACE_KEY = "zm:workspace";
const OFFLINE = "Can't reach Z-Matrix right now. Check your connection and try again.";

/** The workspace chosen on the landing page, kept for this tab so /login survives a reload. */
export function savedWorkspace(): Workspace | null {
  try {
    const raw = sessionStorage.getItem(WORKSPACE_KEY);
    return raw ? (JSON.parse(raw) as Workspace) : null;
  } catch {
    return null;
  }
}

function keep(t: TokenOut) {
  token = t.access_token;
  clearTimeout(timer);
  timer = setTimeout(() => void refresh(), Math.max(t.expires_in - 60, 5) * 1000);
}

/** Drops the in-memory session (signing out, and between tests). */
export function forget() {
  clearTimeout(timer);
  token = null;
  inflight = null;
}

export const signedIn = () => token !== null;

export async function findWorkspace(code: string): Promise<Outcome<Workspace>> {
  try {
    const { data, response } = await api.POST("/auth/workspace", { body: { workspace_code: code } });
    if (data) {
      const workspace = { ...data, code };
      try {
        sessionStorage.setItem(WORKSPACE_KEY, JSON.stringify(workspace));
      } catch {
        // Blocked storage: the workspace lasts until a reload.
      }
      return { ok: true, value: workspace };
    }
    const message =
      response.status === 429
        ? "Too many attempts. Wait a minute and try again."
        : "We couldn't find that workspace. Check the code and try again.";
    return { ok: false, message };
  } catch {
    return { ok: false, message: OFFLINE };
  }
}

export async function signIn(code: string, email: string, password: string): Promise<Outcome<null>> {
  try {
    const { data } = await api.POST("/auth/login", { body: { workspace_code: code, email, password } });
    if (!data) return { ok: false, message: "Wrong email or password." };
    keep(data);
    return { ok: true, value: null };
  } catch {
    return { ok: false, message: OFFLINE };
  }
}

/** A new access token from the refresh cookie; true when signed in. */
export function refresh(): Promise<boolean> {
  inflight ??= (async () => {
    try {
      const { data } = await api.POST("/auth/refresh");
      if (data) keep(data);
      else forget();
      return data !== undefined;
    } catch {
      return false;
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

export async function signOut() {
  forget();
  try {
    await api.POST("/auth/logout");
  } catch {
    // Offline: the cookie stays until it expires; the token is already gone.
  }
}

export async function loadMe(): Promise<Me | null> {
  const { data } = await api.GET("/app/me");
  return data ?? null;
}
