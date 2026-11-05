import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { blocks, type AppUi, type UiPage } from "@zm/blocks";
import { sampleUi, testBlocks } from "@zm/blocks/test";
import { accentStorageKey, schemeStorageKey } from "@zm/ui";
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
  preferences: { color_scheme: null, accent: null },
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

// The shell draws stand-ins with the real manifests (#92's blocks don't exist yet; task_inbox's own
// tests live with it). Vitest isolates modules per test file, so the registry is only changed here.
const realBlocks = { ...blocks };
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

test("the shell hands the signed-in API to the blocks: Home's task_inbox loads the user's tasks (#30)", async () => {
  Object.assign(blocks, { task_inbox: realBlocks.task_inbox });
  try {
    const task = { id: "t1", name: "DD checklist", flow: "store_launch", case_code: "MX-0001", stage: "legal" };
    signedInApi(sampleUi, {
      "GET /app/tasks": (r) =>
        r.headers.get("authorization") === "Bearer t1"
          ? json([{ ...task, created: "2026-10-20T09:00:00Z", assignee: null, form_key: "zm:dd" }])
          : problem(401, "Sign in again"),
    });
    renderAt("/app");
    const site = await screen.findByRole("table", { name: /MX-0001/ });
    expect(within(site).getByText("DD checklist")).toBeDefined();
  } finally {
    Object.assign(blocks, { task_inbox: testBlocks.task_inbox });
  }
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

// --- people (S2-23) -------------------------------------------------------------------------------

const ADMIN = { ...ME, name: "Client Admin", is_client_admin: true, memberships: [] };
const PRIYA = {
  id: "0b4c6f2e-1111-4a2b-9c3d-0000000000bb",
  email: "priya@pilot.demo",
  name: "Priya",
  status: "active",
  is_client_admin: false,
  memberships: [{ department: "legal", level: 1, scope: "own" }],
  created_at: "2026-11-01T09:00:00Z",
};
const DEPARTMENTS = [
  { key: "bd", name: "Business development", levels: [{ n: 1, name: "BD Manager" }] },
  { key: "legal", name: "Legal", levels: [{ n: 1, name: "Legal Executive" }, { n: 2, name: "Legal Supervisor" }] },
];

/** A signed-in client admin with the people API; returns the people requests it saw. */
function adminApi(routes: Record<string, Handler> = {}): Request[] {
  const seen: Request[] = [];
  const people: Record<string, Handler> = { "GET /app/admin/users": () => json([PRIYA]), ...routes };
  api({
    "POST /auth/refresh": () => json(TOKEN),
    "GET /app/me": () => json(ADMIN),
    "GET /app/ui": () => json(sampleUi),
    "GET /app/admin/departments": () => json(DEPARTMENTS),
    ...Object.fromEntries(Object.entries(people).map(([key, handler]) => [key, (r: Request) => (seen.push(r.clone()), handler(r))])),
  });
  return seen;
}

const bodies = (seen: Request[], method: string) => Promise.all(seen.filter((r) => r.method === method).map((r) => r.json()));

test("a client admin's People nav item opens /app/admin/people; other users have none", async () => {
  adminApi();
  renderAt("/app");
  await screen.findByRole("region", { name: "task_inbox" });
  await userEvent.setup().click(nav().getByRole("button", { name: "People" }));
  const people = await screen.findByRole("table", { name: "People" });
  expect(await within(people).findByText("Legal L1")).toBeDefined();
  expect(nav().getByRole("button", { name: "People" }).getAttribute("aria-current")).toBe("page");

  cleanup();
  forget();
  signedInApi(sampleUi);
  renderAt("/app/admin/people");
  expect(await screen.findByRole("heading", { name: "Page not found" })).toBeDefined();
  expect(nav().queryByRole("button", { name: "People" })).toBeNull();
});

test("the status filter asks the API for that status", async () => {
  const seen = adminApi();
  renderAt("/app/admin/people");
  await screen.findByRole("table", { name: "People" });
  await userEvent.setup().click(screen.getByText("Deactivated"));
  await vi.waitFor(() => expect(seen.map((r) => new URL(r.url).search)).toEqual(["", "?status=deactivated"]));
});

test("inviting Priya, Legal L1, sends her department and level; a taken email shows under the field", async () => {
  let taken = true;
  const seen = adminApi({
    "POST /app/admin/users": () =>
      taken
        ? json(
            {
              type: "about:blank",
              title: "Conflict",
              status: 409,
              detail: "Email taken",
              request_id: "r",
              errors: [{ path: "/email", message: "Someone here already uses this email" }],
            },
            409,
          )
        : json({ ...PRIYA, status: "invited" }, 201),
  });
  renderAt("/app/admin/people");
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Invite person" }));
  const dialog = within(await screen.findByRole("dialog", { name: "Invite person" }));
  await user.type(dialog.getByLabelText(/Email/), "priya@pilot.demo");
  await user.type(dialog.getByLabelText(/Name/), "Priya");
  await user.click(dialog.getByRole("button", { name: "Add department" }));
  await user.selectOptions(dialog.getByLabelText("Department 1"), "legal");
  await user.click(dialog.getByRole("button", { name: "Send invite" }));
  expect(await dialog.findByText("Someone here already uses this email")).toBeDefined();

  taken = false;
  await user.click(dialog.getByRole("button", { name: "Send invite" }));
  await vi.waitFor(() => expect(screen.queryByRole("dialog", { name: "Invite person" })).toBeNull());
  const posted = await bodies(seen, "POST");
  expect(posted[1]).toEqual({ email: "priya@pilot.demo", name: "Priya", memberships: [{ department: "legal", level: 1, scope: "own" }] });
});

test("moving Priya to L2, and deactivating her after a confirmation, PATCH her", async () => {
  const seen = adminApi({ [`PATCH /app/admin/users/${PRIYA.id}`]: () => json(PRIYA) });
  renderAt("/app/admin/people");
  const user = userEvent.setup();
  await user.click(await screen.findByText("Priya"));
  const drawer = within(await screen.findByRole("dialog", { name: "Priya" }));
  await user.selectOptions(drawer.getByLabelText("Level in Legal"), "2");
  await user.click(drawer.getByRole("button", { name: "Save" }));
  await vi.waitFor(() => expect(screen.queryByRole("dialog", { name: "Priya" })).toBeNull());

  await user.click(await screen.findByText("Priya"));
  await user.click(within(await screen.findByRole("dialog", { name: "Priya" })).getByRole("button", { name: "Deactivate" }));
  const confirm = within(await screen.findByRole("dialog", { name: "Deactivate Priya?" }));
  await user.click(confirm.getByRole("button", { name: "Deactivate" }));
  await vi.waitFor(() => expect(seen.filter((r) => r.method === "PATCH")).toHaveLength(2));
  expect(await bodies(seen, "PATCH")).toEqual([
    { memberships: [{ department: "legal", level: 2, scope: "own" }] },
    { status: "deactivated" },
  ]);
});

test("resending an invite POSTs the invited person's email, name and memberships again", async () => {
  const invited = { ...PRIYA, status: "invited" };
  const seen = adminApi({ "GET /app/admin/users": () => json([invited]), "POST /app/admin/users": () => json(invited, 201) });
  renderAt("/app/admin/people");
  const user = userEvent.setup();
  await user.click(await screen.findByText("Priya"));
  await user.click(within(await screen.findByRole("dialog", { name: "Priya" })).getByRole("button", { name: "Resend invite" }));
  await vi.waitFor(() => expect(screen.queryByRole("dialog", { name: "Priya" })).toBeNull());
  expect(await bodies(seen, "POST")).toEqual([{ email: PRIYA.email, name: "Priya", memberships: PRIYA.memberships }]);
});

// --- change requests (S2-24a) ---------------------------------------------------------------------

const TARGETS = {
  forms: [
    {
      id: "start",
      title: "Site",
      fields: [
        { key: "city", label: "City", type: "text" },
        { key: "store_model", label: "Store model", type: "select" },
      ],
    },
    { id: "bd_upload_loi", title: "Upload LOI", fields: [{ key: "loi_file", label: "LOI", type: "document" }] },
  ],
  field_types: ["text", "number", "select", "document"],
};
const STATE_AND_DISTRICT = {
  kind: "add_field",
  form: "start",
  fields: [
    { label: "State", type: "text", options: [] },
    { label: "District", type: "text", options: [] },
  ],
};
const REQUESTED = { note: null, review_note: null, shipped_in_version: null, created_at: "2026-11-02T09:00:00Z", updated_at: "2026-11-02T09:00:00Z" };
const REQUESTS = [
  { ...REQUESTED, id: "c2", kind: "add_field", payload: STATE_AND_DISTRICT, status: "submitted" },
  { ...REQUESTED, id: "c1", kind: "rename_field_label", payload: { kind: "rename_field_label", field: "city", label: "Town" }, status: "rejected", review_note: "Reports group by City" },
  { ...REQUESTED, id: "c0", kind: "add_field", payload: { kind: "add_field", form: "start" }, status: "shipped" }, // stored under older rules
];

/** A client admin with the change-request API; returns the change-request calls it saw. */
function changesApi(routes: Record<string, Handler> = {}): Request[] {
  return adminApi({
    "GET /app/admin/change-requests": () => json(REQUESTS),
    "GET /app/admin/change-requests/targets": () => json(TARGETS),
    ...routes,
  });
}

async function openRequestForm() {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Request a change" }));
  return { user, dialog: within(await screen.findByRole("dialog", { name: "Request a change" })) };
}

test("a client admin's Change requests page lists each request with its status and review note", async () => {
  changesApi();
  renderAt("/app");
  await screen.findByRole("region", { name: "task_inbox" });
  await userEvent.setup().click(nav().getByRole("button", { name: "Change requests" }));
  const table = await screen.findByRole("table", { name: "Change requests" });
  expect(await within(table).findByText("Site: add State (text), District (text)")).toBeDefined();
  expect(within(table).getByText("Rename City to Town")).toBeDefined();
  expect(within(table).getByText("Rejected")).toBeDefined();
  expect(within(table).getByText("Reports group by City")).toBeDefined();
  expect(within(table).getByText("Add fields to a form")).toBeDefined();

  cleanup();
  forget();
  signedInApi(sampleUi);
  renderAt("/app/admin/change-requests");
  expect(await screen.findByRole("heading", { name: "Page not found" })).toBeDefined();
  expect(nav().queryByRole("button", { name: "Change requests" })).toBeNull();
});

test("older change requests load a page at a time", async () => {
  const page = (offset: number, n: number) =>
    Array.from({ length: n }, (_, i) => ({ ...REQUESTS[1], id: `r${offset + i}` }));
  const seen = changesApi({
    "GET /app/admin/change-requests": (req) => {
      const offset = Number(new URL(req.url).searchParams.get("offset") ?? 0);
      return json(offset === 0 ? page(0, 100) : page(99, 4)); // a new request shifted r99 down
    },
  });
  renderAt("/app/admin/change-requests");
  const table = await screen.findByRole("table", { name: "Change requests" });
  await vi.waitFor(() => expect(within(table).getAllByText("Rename City to Town")).toHaveLength(100));
  await userEvent.setup().click(screen.getByRole("button", { name: "Load older requests" }));
  await vi.waitFor(() => expect(within(table).getAllByText("Rename City to Town")).toHaveLength(103));
  expect(screen.queryByRole("button", { name: "Load older requests" })).toBeNull(); // a short page is the end
  const lists = seen.filter((r) => r.method === "GET" && new URL(r.url).pathname === "/app/admin/change-requests");
  expect(lists.map((r) => new URL(r.url).search)).toEqual(["?limit=100&offset=0", "?limit=100&offset=100"]);
});

test("asking for State and District on the site form POSTs one add_field request with both", async () => {
  const seen = changesApi({ "POST /app/admin/change-requests": () => json(REQUESTS[0], 201) });
  renderAt("/app/admin/change-requests");
  const { user, dialog } = await openRequestForm();
  await user.selectOptions(dialog.getByLabelText(/^Form/), "start");
  await user.type(dialog.getByLabelText(/^Field 1/), "State");
  await user.click(dialog.getByRole("button", { name: "Add another field" }));
  await user.type(dialog.getByLabelText(/^Field 2/), "District");
  await user.type(dialog.getByLabelText(/^Note/), "For the GST registration");
  await user.click(dialog.getByRole("button", { name: "Send request" }));
  await vi.waitFor(() => expect(screen.queryByRole("dialog", { name: "Request a change" })).toBeNull());
  expect(await bodies(seen, "POST")).toEqual([{ payload: STATE_AND_DISTRICT, note: "For the GST registration" }]);
});

test("renaming City sends its field key; a target the release lacks shows under the selector", async () => {
  const refused = {
    type: "about:blank",
    title: "Unprocessable Content",
    status: 422,
    detail: "The request names something the published release doesn't have",
    request_id: "r",
    errors: [{ path: "/payload/field", message: "city isn't a field in the published release" }],
  };
  const seen = changesApi({ "POST /app/admin/change-requests": () => json(refused, 422) });
  renderAt("/app/admin/change-requests");
  const { user, dialog } = await openRequestForm();
  await user.selectOptions(dialog.getByLabelText(/^What do you need/), "add_select_option");
  const choices = (dialog.getByLabelText(/^Field/) as HTMLSelectElement).options;
  expect([...choices].map((o) => o.value)).toEqual(["", "store_model"]); // only lists take choices

  await user.selectOptions(dialog.getByLabelText(/^What do you need/), "rename_field_label");
  await user.selectOptions(dialog.getByLabelText(/^Field/), "city");
  await user.type(dialog.getByLabelText(/^New name/), "Town");
  await user.click(dialog.getByRole("button", { name: "Send request" }));
  expect(await dialog.findByText("city isn't a field in the published release")).toBeDefined();
  await user.click(dialog.getByRole("button", { name: "Send request" }));
  await vi.waitFor(() => expect(seen.filter((r) => r.method === "POST")).toHaveLength(2));
  const sent = { payload: { kind: "rename_field_label", field: "city", label: "Town" }, note: null };
  expect(await bodies(seen, "POST")).toEqual([sent, sent]);
  const keys = seen.filter((r) => r.method === "POST").map((r) => r.headers.get("Idempotency-Key"));
  expect(keys[0]).toMatch(/^[0-9a-f-]{36}$/);
  expect(keys[1]).toBe(keys[0]); // a resend from the same form can't store the request twice
});

test("/invite/:token sets the password and opens the app", async () => {
  const sent: unknown[] = [];
  api({
    "POST /auth/invite/accept": async (r) => {
      sent.push(await r.json());
      return json(TOKEN);
    },
    "GET /app/me": (r) => (r.headers.get("authorization") === "Bearer t1" ? json(ME) : problem(401, "Sign in again")),
    "GET /app/ui": () => json(sampleUi),
  });
  renderAt("/invite/pilot-local.abc123");
  await userEvent.setup().type(await screen.findByLabelText(/New password/), "a-long-enough-password{Enter}");
  expect(await screen.findByRole("region", { name: "task_inbox" })).toBeDefined();
  expect(sent).toEqual([{ token: "pilot-local.abc123", password: "a-long-enough-password" }]);
});

test("an expired invite link says so", async () => {
  api({ "POST /auth/invite/accept": () => problem(404, "This invite link is invalid or has expired") });
  renderAt("/invite/pilot-local.old");
  await userEvent.setup().type(await screen.findByLabelText(/New password/), "a-long-enough-password{Enter}");
  expect(await screen.findByText(/invalid or has expired/)).toBeDefined();
});

test("an accept failure blames the password only when the API refused the password", async () => {
  let refused = false;
  api({
    "POST /auth/invite/accept": () =>
      refused
        ? json(
            {
              type: "about:blank",
              title: "Unprocessable Content",
              status: 422,
              detail: "Some values are not valid",
              request_id: "r",
              errors: [{ path: "/password", message: "String should have at least 12 characters" }],
            },
            422,
          )
        : problem(502, "The process engine could not handle the request"),
  });
  renderAt("/invite/pilot-local.abc123");
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText(/New password/), "a-long-enough-password{Enter}");
  expect(await screen.findByText(/Try again, or ask your administrator/)).toBeDefined();
  expect(screen.queryByText(/Choose a password/)).toBeNull();

  refused = true;
  await user.click(screen.getByRole("button", { name: "Set password" }));
  expect(await screen.findByText("Choose a password of at least 12 characters.")).toBeDefined();
});

// --- appearance follows the user (#93) ------------------------------------------------------------

const scheme = () => document.documentElement.getAttribute("data-mantine-color-scheme");
const saved = (preferences: object) => ({ "GET /app/me": () => json({ ...ME, preferences }) });

test("dark + copper picked in another browser apply here, over this browser's older choice", async () => {
  localStorage.setItem(schemeStorageKey(ME.id), "light");
  localStorage.setItem(accentStorageKey(ME.id), "plum");
  signedInApi(sampleUi, saved({ color_scheme: "dark", accent: "copper" }));
  renderAt("/app");
  await screen.findByRole("region", { name: "task_inbox" });
  expect([scheme(), primary()]).toEqual(["dark", "copper"]);
  // and the next load's pre-paint script starts from them
  expect(localStorage.getItem(schemeStorageKey(ME.id))).toBe("dark");
  expect(localStorage.getItem(accentStorageKey(ME.id))).toBe("copper");
});

/** One pick per render: jsdom has no layout, so the popover hides itself after the first. */
async function pick(name: string) {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Appearance" }));
  await user.click(await screen.findByRole("button", { name }));
}

test("a pick in the appearance menu is saved to the server", async () => {
  const sent: unknown[] = [];
  signedInApi(sampleUi, {
    "PUT /app/me/preferences": async (r) => {
      sent.push(await r.json());
      return json({ color_scheme: null, accent: "copper" });
    },
  });
  renderAt("/app");
  await screen.findByRole("region", { name: "task_inbox" });
  await pick("Accent: Copper");
  await vi.waitFor(() => expect(sent).toEqual([{ accent: "copper" }]));
  expect(primary()).toBe("copper");
});

test("a pick the server refuses still applies here, and says it wasn't saved", async () => {
  signedInApi(sampleUi, { "PUT /app/me/preferences": () => problem(500, "Boom") });
  renderAt("/app");
  await screen.findByRole("region", { name: "task_inbox" });
  await pick("Accent: Plum");
  expect(await screen.findByText(/couldn't save your appearance to your account yet/)).toBeDefined();
  expect(primary()).toBe("plum");
});

const UNSYNCED = `zm:appearance-unsynced:${ME.id}`;
const refocus = () => window.dispatchEvent(new Event("visibilitychange")); // TanStack refetches /app/me

test("a pick that couldn't be saved survives the next /app/me, and a later retry saves it", async () => {
  const server = { color_scheme: null, accent: "copper" }; // saved from another device
  let up = false;
  const sent: unknown[] = [];
  signedInApi(sampleUi, {
    "GET /app/me": () => json({ ...ME, preferences: server }),
    "PUT /app/me/preferences": async (r) => {
      const body = (await r.json()) as object;
      sent.push(body);
      if (!up) return problem(503, "Down");
      Object.assign(server, body);
      return json(server);
    },
  });
  renderAt("/app");
  await screen.findByRole("region", { name: "task_inbox" });
  await pick("Accent: Plum");
  await screen.findByText(/couldn't save your appearance/);

  refocus(); // still down: the server's copper must not replace plum here
  await vi.waitFor(() => expect(sent).toHaveLength(2));
  expect(localStorage.getItem(accentStorageKey(ME.id))).toBe("plum");
  expect(localStorage.getItem(UNSYNCED)).toBe("accent");

  up = true;
  refocus();
  await vi.waitFor(() => expect(localStorage.getItem(UNSYNCED)).toBeNull());
  expect(sent).toEqual([{ accent: "plum" }, { accent: "plum" }, { accent: "plum" }]);
  expect(server.accent).toBe("plum");
});

test("a choice this browser kept from before #93 is uploaded when the server has none", async () => {
  localStorage.setItem(accentStorageKey(ME.id), "plum");
  const sent: unknown[] = [];
  signedInApi(sampleUi, {
    "PUT /app/me/preferences": async (r) => {
      sent.push(await r.json());
      return json({ color_scheme: null, accent: "plum" });
    },
  });
  renderAt("/app");
  await screen.findByRole("region", { name: "task_inbox" });
  await vi.waitFor(() => expect(sent).toEqual([{ accent: "plum" }])); // no scheme: none was picked here
  expect(primary()).toBe("plum");
});

test("a saved accent this shell doesn't know (neon) falls back to the client's, with a warning", async () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  signedInApi(sampleUi, saved({ color_scheme: null, accent: "neon" })); // the client's accent is teal
  renderAt("/app");
  await screen.findByRole("region", { name: "task_inbox" });
  expect(primary()).toBe("teal");
  expect(warn).toHaveBeenCalledWith(expect.stringContaining('unknown accent "neon"'));
  warn.mockRestore();
});
