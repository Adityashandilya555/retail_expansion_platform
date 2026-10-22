import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { blocks, type AppUi, type UiPage } from "@zm/blocks";
import { sampleUi, testBlocks } from "@zm/blocks/test";
import { accentStorageKey } from "@zm/ui";
import { MemoryRouter } from "react-router";
import { afterEach, beforeAll, expect, test, vi } from "vitest";
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

// The real blocks come with #30 and #92; until then the shell draws stand-ins with their manifests.
// Vitest isolates modules per test file, so the registry is only changed here.
beforeAll(() => Object.assign(blocks, testBlocks));

afterEach(() => {
  forget();
  sessionStorage.clear();
  localStorage.clear();
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
    "GET /app/ui": (r) => (r.headers.get("authorization") === "Bearer t1" ? json(sampleUi) : problem(401, "Sign in again")),
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

  expect(await screen.findByRole("region", { name: "task_inbox" })).toBeDefined(); // Home, the first nav item
  expect(calls).toEqual(["POST /auth/workspace", "POST /auth/login", "GET /app/me", "GET /app/ui"]);
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
  signedInApi(sampleUi);
  renderAt("/app");
  expect(await screen.findByRole("button", { name: "Account: Legal Executive" })).toBeDefined();
});

test("without a session the app goes back to the landing page", async () => {
  api({ "POST /auth/refresh": () => problem(401, "Sign in again") });
  renderAt("/app");
  expect(await screen.findByLabelText(/Workspace code/)).toBeDefined();
});

test("signing in after a signed-out visit to /app opens the shell", async () => {
  api({
    "POST /auth/refresh": () => problem(401, "Sign in again"),
    "POST /auth/workspace": () => json(WORKSPACE),
    "POST /auth/login": () => json(TOKEN),
    "GET /app/me": () => json(ME),
    "GET /app/ui": () => json(sampleUi),
  });
  renderAt("/app");
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText(/Workspace code/), "PILOT{Enter}");
  await user.type(await screen.findByLabelText(/Email/), "legal.l1@pilot.demo");
  await user.type(screen.getByLabelText(/Password/), "s3cret{Enter}");
  expect(await screen.findByRole("region", { name: "task_inbox" })).toBeDefined();
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

// --- the shell (#27) ------------------------------------------------------------------------------

/** A signed-in session (refresh cookie) whose `GET /app/ui` answers `ui`, read at request time. */
function signedInApi(ui: AppUi | (() => AppUi), routes: Record<string, Handler> = {}): string[] {
  return api({
    "POST /auth/refresh": () => json(TOKEN),
    "GET /app/me": () => json(ME),
    "GET /app/ui": () => json(typeof ui === "function" ? ui() : ui),
    ...routes,
  });
}

const nav = () => within(screen.getByRole("navigation", { name: "Main" }));
const blockNames = () => screen.queryAllByRole("region").map((r) => r.getAttribute("aria-label"));

test("Legal L1 opens /app/legal: the nav comes from the release, the layout and its blocks render", async () => {
  const calls = signedInApi(sampleUi);
  renderAt("/app/legal");
  expect(await screen.findByRole("region", { name: "task_inbox" })).toBeDefined();
  expect(blockNames()).toEqual(["task_inbox", "case_table"]);
  expect(nav().getAllByRole("button").map((b) => b.textContent)).toEqual(["Home", "Legal"]);
  expect(nav().getByRole("button", { name: "Legal" }).getAttribute("aria-current")).toBe("page");
  expect(calls).toEqual(["POST /auth/refresh", "GET /app/me", "GET /app/ui"]);

  await userEvent.setup().click(nav().getByRole("button", { name: "Home" }));
  expect(screen.getByRole("region", { name: "task_inbox" }).textContent).toContain('"scope":"mine"');
  expect(nav().getByRole("button", { name: "Home" }).getAttribute("aria-current")).toBe("page");
});

test("v2 published with another block order: a reload draws the new order, with no frontend deploy", async () => {
  let ui = sampleUi;
  signedInApi(() => ui);
  renderAt("/app/legal");
  await screen.findByRole("region", { name: "task_inbox" });
  expect(blockNames()).toEqual(["task_inbox", "case_table"]);

  cleanup();
  forget();
  const reorder = (p: UiPage) => (p.key === "legal" ? { ...p, regions: { main: [...(p.regions.main ?? [])].reverse() } } : p);
  ui = { ...sampleUi, release: { version: 2 }, pages: sampleUi.pages.map(reorder) };
  renderAt("/app/legal");
  await screen.findByRole("region", { name: "task_inbox" });
  expect(blockNames()).toEqual(["case_table", "task_inbox"]);
});

test("a BD-only user has no Legal nav item, and /app/legal is not found (the API never sent it)", async () => {
  signedInApi({
    ...sampleUi,
    nav: [...sampleUi.nav.filter((n) => n.page !== "legal"), { page: "bd", label: "Business development", visible_to: ["bd"] }],
    pages: sampleUi.pages.map((p) => (p.key === "legal" ? { ...p, key: "bd", department: "bd" } : p)),
  });
  renderAt("/app/legal");
  expect(await screen.findByRole("heading", { name: "Page not found" })).toBeDefined();
  expect(nav().getAllByRole("button").map((b) => b.textContent)).toEqual(["Home", "Business development"]);
});

test("an unknown block or invalid props show a placeholder; the rest of the page works", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  signedInApi({
    ...sampleUi,
    pages: [
      {
        key: "home",
        layout: "standard",
        regions: {
          main: [
            { block: "sales_chart", props: {} },
            { block: "task_inbox", props: { scope: "everyone" } },
            { block: "case_table", props: {} },
          ],
        },
      },
    ],
  });
  renderAt("/app");
  expect(await screen.findByRole("region", { name: "case_table" })).toBeDefined();
  expect(screen.getAllByRole("alert").map((a) => a.textContent)).toEqual([
    expect.stringContaining("sales_chart: unknown block"),
    expect.stringContaining("props/scope must be equal to one of the allowed values"),
  ]);
  await userEvent.setup().click(nav().getByRole("button", { name: "Legal" })); // the shell still works
  expect(await screen.findByRole("heading", { name: "Page not found" })).toBeDefined();
  vi.mocked(console.error).mockRestore();
});

test("/app/cases/MX-0001 draws the flow's case page on the tab from ?tab=", async () => {
  signedInApi(sampleUi);
  renderAt("/app/cases/MX-0001?tab=tasks");
  expect(await screen.findByRole("region", { name: "case_tasks" })).toBeDefined();
  expect(screen.getByRole("region", { name: "case_header" }).textContent).toContain('"title_field":"site_name"');
  expect(screen.getByRole("tab", { name: "Tasks" }).getAttribute("aria-selected")).toBe("true");
});

test("without a case page for the flow, /app/cases/:code draws the built-in one", async () => {
  signedInApi({ ...sampleUi, pages: sampleUi.pages.filter((p) => p.layout !== "case") });
  renderAt("/app/cases/MX-0001");
  expect(await screen.findByRole("region", { name: "case_fields" })).toBeDefined();
  expect(screen.getByRole("region", { name: "case_header" }).textContent).toContain("{}");
  expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Overview", "Tasks"]);
});

test("a case code no flow uses is not found", async () => {
  signedInApi(sampleUi);
  renderAt("/app/cases/QSR-0001");
  expect(await screen.findByRole("heading", { name: "Page not found" })).toBeDefined();
});

const primary = () =>
  [...document.querySelectorAll("style")]
    .map((s) => s.textContent)
    .join("")
    .match(/--mantine-primary-color-filled: var\(--mantine-color-(\w+)-filled\)/)?.[1];

test("the shell wears the client's accent (teal) unless the user picked another", async () => {
  signedInApi({ ...sampleUi, client: { name: "Matrix Retail", theme: "quartz", accent: "teal" } });
  renderAt("/app");
  await screen.findByRole("region", { name: "task_inbox" });
  expect(primary()).toBe("teal");

  cleanup();
  forget();
  localStorage.setItem(accentStorageKey(ME.id), "copper");
  renderAt("/app");
  await screen.findByRole("region", { name: "task_inbox" });
  expect(primary()).toBe("copper");
});

test("before the first publish the shell says so, and the user can still sign out", async () => {
  const calls = signedInApi(sampleUi, {
    "GET /app/ui": () => problem(404, "Nothing has been published yet"),
    "POST /auth/logout": () => new Response(null, { status: 204 }),
  });
  renderAt("/app");
  expect(await screen.findByText("Nothing has been published for this workspace yet.")).toBeDefined();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Account: Legal Executive" }));
  await user.click(await screen.findByRole("menuitem", { name: "Sign out" }));
  expect(await screen.findByLabelText(/Workspace code/)).toBeDefined();
  expect(calls).toEqual(["POST /auth/refresh", "GET /app/me", "GET /app/ui", "POST /auth/logout"]); // no silent re-sign-in
});

test("a failing GET /app/ui shows an error with Retry", async () => {
  let fail = true;
  signedInApi(sampleUi, { "GET /app/ui": () => (fail ? problem(500, "Boom") : json(sampleUi)) });
  renderAt("/app");
  expect(await screen.findByText("Couldn't load this workspace.")).toBeDefined();
  fail = false;
  await userEvent.setup().click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("region", { name: "task_inbox" })).toBeDefined();
});
