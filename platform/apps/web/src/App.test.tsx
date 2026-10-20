import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { ClientApp } from "./App";
import { forget, refresh, signedIn, signOut } from "./session";

type Handler = (request: Request) => Response | Promise<Response>;
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const problem = (status: number, detail: string) =>
  json({ type: "about:blank", title: "x", status, detail, request_id: "r", errors: null }, status);

const WORKSPACE = { client_name: "Matrix Retail", theme: "quartz", accent: "teal" };
const TOKEN = { access_token: "t1", token_type: "bearer", expires_in: 900 };
const ME = {
  id: "0b4c6f2e-1111-4a2b-9c3d-000000000001",
  email: "legal.l1@pilot.demo",
  name: "Legal Executive",
  is_client_admin: false,
  client: { slug: "pilot-local", name: "Matrix Retail" },
  memberships: [{ department: "legal", level: 1, scope: "own" }],
};

/** Stubs the API by "METHOD /path"; returns the calls it saw. */
function api(routes: Record<string, Handler>): string[] {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const key = `${request.method} ${new URL(request.url).pathname}`;
      calls.push(key);
      return routes[key]?.(request) ?? problem(404, "Not found");
    }),
  );
  return calls;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ClientApp />
    </MemoryRouter>,
  );
}

afterEach(() => {
  forget();
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

test("an unknown workspace code shows a generic error and stays on the landing page", async () => {
  api({ "POST /auth/workspace": () => problem(404, "Workspace not found") });
  renderAt("/");
  const user = userEvent.setup();
  await user.type(screen.getByLabelText(/Workspace code/), "NOPE{Enter}");
  expect(await screen.findByText("We couldn't find that workspace. Check the code and try again.")).toBeDefined();
  expect(screen.getByLabelText(/Workspace code/)).toBeDefined();
});

test("keyboard only: workspace code, then email and password, then the app", async () => {
  const calls = api({
    "POST /auth/workspace": () => json(WORKSPACE),
    "POST /auth/login": () => json(TOKEN),
    "GET /app/me": (r) => (r.headers.get("authorization") === "Bearer t1" ? json(ME) : problem(401, "Sign in again")),
  });
  renderAt("/");
  const user = userEvent.setup();
  expect(document.activeElement).toBe(screen.getByLabelText(/Workspace code/)); // focused on arrival
  await user.keyboard("PILOT{Enter}");

  expect(await screen.findByRole("heading", { level: 1, name: "Matrix Retail" })).toBeDefined();
  // the sign-in page already wears the client's accent (D19)
  const css = [...document.querySelectorAll("style")].map((s) => s.textContent).join("");
  expect(css).toContain("--mantine-primary-color-filled: var(--mantine-color-teal-filled)");

  expect(document.activeElement).toBe(screen.getByLabelText(/Email/)); // focus follows the step
  await user.keyboard("legal.l1@pilot.demo");
  await user.tab();
  await user.keyboard("s3cret{Enter}");

  expect(await screen.findByRole("heading", { name: "Signed in as Legal Executive" })).toBeDefined();
  expect(screen.getByText(/legal L1/)).toBeDefined();
  expect(calls).toEqual(["POST /auth/workspace", "POST /auth/login", "GET /app/me"]);
});

test("a wrong password shows the same generic error", async () => {
  api({
    "POST /auth/workspace": () => json(WORKSPACE),
    "POST /auth/login": () => problem(401, "Wrong workspace code, email or password"),
  });
  renderAt("/");
  const user = userEvent.setup();
  await user.keyboard("PILOT{Enter}");
  await screen.findByRole("heading", { level: 1, name: "Matrix Retail" });
  await user.type(screen.getByLabelText(/Email/), "legal.l1@pilot.demo");
  await user.type(screen.getByLabelText(/Password/), "nope{Enter}");
  expect(await screen.findByText("Wrong email or password.")).toBeDefined();
});

test("the app signs in silently with the refresh cookie", async () => {
  api({ "POST /auth/refresh": () => json(TOKEN), "GET /app/me": () => json(ME) });
  renderAt("/app");
  expect(await screen.findByRole("heading", { name: "Signed in as Legal Executive" })).toBeDefined();
});

test("without a session the app goes back to the landing page", async () => {
  api({ "POST /auth/refresh": () => problem(401, "Sign in again") });
  renderAt("/app");
  expect(await screen.findByLabelText(/Workspace code/)).toBeDefined();
});

test("two refreshes at once send one request (a second would look like token reuse)", async () => {
  const calls = api({ "POST /auth/refresh": () => json(TOKEN) });
  const [a, b] = await Promise.all([refresh(), refresh()]);
  expect([a, b]).toEqual([true, true]);
  expect(calls).toEqual(["POST /auth/refresh"]);
});

test("the sign-in page without a workspace goes back to the landing page", async () => {
  api({});
  renderAt("/login");
  expect(await screen.findByLabelText(/Workspace code/)).toBeDefined();
});

test("a refresh that lands after sign-out doesn't sign the user back in (Codex, #108)", async () => {
  let land: (r: Response) => void = () => {};
  api({
    "POST /auth/refresh": () => new Promise<Response>((resolve) => (land = resolve)),
    "POST /auth/logout": () => new Response(null, { status: 204 }),
  });
  const late = refresh(); // e.g. the scheduled one, already on its way
  await signOut();
  land(json(TOKEN)); // the server did rotate, but the person has signed out since
  expect(await late).toBe(false);
  expect(signedIn()).toBe(false);
});
