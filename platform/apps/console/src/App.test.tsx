import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { components } from "@zm/api-client";
import { expectNoAxeViolations } from "@zm/ui/test";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { ConsoleApp } from "./App";
import { currentOwner, forget, refresh, signedIn, signOut } from "./session";

type ClientDetail = components["schemas"]["ClientDetail"];
type StepOut = components["schemas"]["StepOut"];
type Handler = (r: Request) => Response | Promise<Response>;

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const problem = (status: number, errors: { path: string; message: string }[] | null = null) =>
  json({ type: "about:blank", title: "x", status, detail: "x", request_id: "r", errors }, status);
const OWNER = { id: "6f0c1b9e-5d2a-4c1e-9a7b-3d8e2f1a0b4c", email: "adi@zm.demo", name: "Adi" };
const TOKEN = { access_token: "o1", token_type: "bearer", expires_in: 900 };

function api(routes: Record<string, Handler>): string[] {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const key = `${request.method} ${new URL(request.url).pathname}`;
      calls.push(key);
      return routes[key]?.(request) ?? problem(404);
    }),
  );
  return calls;
}

/** An owner signed in with the refresh cookie, plus `routes`. */
const signedInApi = (routes: Record<string, Handler>) =>
  api({ "POST /owner/auth/refresh": () => json(TOKEN), "GET /owner/me": () => json(OWNER), ...routes });

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <ConsoleApp />
    </MemoryRouter>,
  );

afterEach(() => {
  forget();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

// --- fixtures ---------------------------------------------------------------------------------

const ID = "0c9f7a52-3b1e-4c8d-9f6a-2e5b7d1c4a90";
const AT = "2026-10-22T08:00:00Z";
const STEPS = [
  "supabase_project",
  "vault_migrations",
  "data_api_off",
  "bucket",
  "registry",
  "operaton_service",
  "operaton_health",
  "seed_draft",
];
const TEMPLATES = [
  { key: "matrix-retail", name: "Matrix Retail: gated retail store launch" },
  { key: "qsr", name: "QSR store launch (local isolation)" },
];

/** Step i's status by index; the rest pending. */
function steps(statuses: Partial<Record<number, StepOut["status"]>> = {}, error: string | null = null): StepOut[] {
  return STEPS.map((step, i) => {
    const status = statuses[i] ?? "pending";
    return { step, seq: i + 1, status, attempts: status === "failed" ? 5 : 0, last_error: status === "failed" || status === "needs_human" ? error : null, updated_at: AT };
  });
}
const allDone = Object.fromEntries(STEPS.map((_, i) => [i, "done" as const]));

function detail(over: Partial<ClientDetail> = {}): ClientDetail {
  return {
    id: ID,
    slug: "matrix-retail",
    name: "Matrix Retail",
    workspace_code: "MATRIX-RETAIL-7Q2K",
    template_key: "matrix-retail",
    status: "provisioning",
    created_at: AT,
    last_release: null,
    steps: steps(),
    releases: [],
    ...over,
  };
}

// --- sign-in (#22) ----------------------------------------------------------------------------

test("an owner signs in with the keyboard only and lands on the clients list", async () => {
  const calls = api({
    "POST /owner/auth/refresh": () => problem(401),
    "POST /owner/auth/login": () => json(TOKEN),
    "GET /owner/me": (r) => (r.headers.get("authorization") === "Bearer o1" ? json(OWNER) : problem(401)),
    "GET /owner/clients": () => json([]),
  });
  renderAt("/");
  expect(await screen.findByRole("heading", { level: 1, name: "Owner console" })).toBeDefined();
  const user = userEvent.setup();
  expect(document.activeElement).toBe(screen.getByLabelText(/Email/));
  await user.keyboard("adi@zm.demo");
  await user.tab();
  await user.keyboard("long-password{Enter}");
  expect(await screen.findByRole("heading", { name: "Clients" })).toBeDefined();
  expect(await screen.findByText("No clients yet")).toBeDefined();
  expect(calls).toEqual(["POST /owner/auth/refresh", "POST /owner/auth/login", "GET /owner/me", "GET /owner/clients"]);
});

test("a wrong password shows a generic error", async () => {
  api({ "POST /owner/auth/login": () => problem(401) });
  renderAt("/login");
  const user = userEvent.setup();
  await user.type(screen.getByLabelText(/Email/), "adi@zm.demo");
  await user.type(screen.getByLabelText(/Password/), "nope{Enter}");
  expect(await screen.findByText("Wrong email or password.")).toBeDefined();
});

test("a refresh cookie signs the owner in silently", async () => {
  signedInApi({ "GET /owner/clients": () => json([]) });
  renderAt("/");
  expect(await screen.findByRole("button", { name: "Account: Adi" })).toBeDefined();
});

test("without a session the console goes to sign-in", async () => {
  api({ "POST /owner/auth/refresh": () => problem(401) });
  renderAt("/clients/new");
  expect(await screen.findByRole("heading", { level: 1, name: "Owner console" })).toBeDefined();
});

test("a refresh that lands after sign-out doesn't sign the owner back in (Codex, #108)", async () => {
  let land: (r: Response) => void = () => {};
  api({
    "POST /owner/auth/refresh": () => new Promise<Response>((resolve) => (land = resolve)),
    "POST /owner/auth/logout": () => new Response(null, { status: 204 }),
  });
  const late = refresh();
  await signOut();
  land(json(TOKEN));
  expect(await late).toBe(false);
  expect(signedIn()).toBe(false);
});

test("a stale token gets one refresh before the owner is sent to sign-in", async () => {
  let fresh = "o1";
  api({
    "POST /owner/auth/refresh": () => json({ ...TOKEN, access_token: fresh }),
    "GET /owner/me": (r) => (r.headers.get("authorization") === `Bearer ${fresh}` ? json(OWNER) : problem(401)),
  });
  expect(await currentOwner()).toEqual(OWNER);
  fresh = "o2"; // o1 expired, and its timed refresh never landed
  expect(await currentOwner()).toEqual(OWNER);
});

// --- clients (#29) ----------------------------------------------------------------------------

test("the clients list shows name, workspace code, status and last release; a row opens the client", async () => {
  signedInApi({
    "GET /owner/clients": () =>
      json([
        { ...detail({ status: "ready", last_release: 2 }), steps: undefined, releases: undefined },
        { ...detail({ id: "b", name: "QSR", workspace_code: "QSR-AB12", status: "failed" }), steps: undefined, releases: undefined },
      ]),
    [`GET /owner/clients/${ID}`]: () => json(detail({ status: "ready" })),
  });
  renderAt("/");
  const table = await screen.findByRole("table", { name: "Clients" });
  await within(table).findByText("Matrix Retail");
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows.map((r) => [...r.querySelectorAll("td")].map((td) => td.textContent))).toEqual([
    ["Matrix Retail", "MATRIX-RETAIL-7Q2K", "Ready", "v2"],
    ["QSR", "QSR-AB12", "Failed", "—"],
  ]);
  await userEvent.setup().click(within(table).getByText("Matrix Retail"));
  expect(await screen.findByRole("heading", { name: "Matrix Retail" })).toBeDefined();
});

test("creating Matrix Retail queues its provisioning, and the page follows it until ready", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let sent: unknown;
  let polls = 0;
  const calls = signedInApi({
    "GET /owner/clients": () => json([]),
    "GET /owner/templates": () => json(TEMPLATES),
    "POST /owner/clients": async (r) => {
      sent = await r.json();
      return json(detail(), 201);
    },
    [`GET /owner/clients/${ID}`]: () =>
      json(++polls < 2 ? detail({ steps: steps({ 0: "done", 1: "running" }) }) : detail({ status: "ready", steps: steps(allDone) })),
  });
  renderAt("/");
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  await user.click(await screen.findByRole("button", { name: "New client" }));
  await user.type(await screen.findByLabelText(/^Name/), "Matrix Retail");
  expect(screen.getByLabelText<HTMLInputElement>(/^Slug/).value).toBe("matrix-retail");
  const code = screen.getByLabelText<HTMLInputElement>(/^Workspace code/).value;
  expect(code).toMatch(/^MATRIX-RETAIL-[A-Z0-9]{4}$/);
  await user.selectOptions(await screen.findByLabelText(/^Template/), "matrix-retail");
  await user.click(screen.getByRole("button", { name: "Create client" }));

  expect(await screen.findByRole("heading", { name: "Matrix Retail" })).toBeDefined();
  expect(sent).toEqual({ name: "Matrix Retail", slug: "matrix-retail", workspace_code: code, template_key: "matrix-retail" });
  expect(screen.getByText("MATRIX-RETAIL-7Q2K")).toBeDefined();
  const provisioning = await screen.findByRole("table", { name: "Provisioning" });
  await waitFor(() => expect(within(provisioning).getByText("Running")).toBeDefined());
  expect(screen.queryByRole("button", { name: /Publish/ })).toBeNull();

  await act(() => vi.advanceTimersByTimeAsync(5000)); // the page polls every 5 s
  expect(await screen.findByRole("button", { name: "Publish draft as v1" })).toBeDefined();
  expect(within(screen.getByRole("table", { name: "Provisioning" })).getAllByText("Done")).toHaveLength(STEPS.length);
  const polled = calls.filter((c) => c === `GET /owner/clients/${ID}`).length;
  expect(polled).toBeGreaterThanOrEqual(2);
  await act(() => vi.advanceTimersByTimeAsync(15000)); // ready: no more polling
  expect(calls.filter((c) => c === `GET /owner/clients/${ID}`)).toHaveLength(polled);
});

test("a failed poll keeps the last status on screen and says so", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let up = true;
  signedInApi({ [`GET /owner/clients/${ID}`]: () => (up ? json(detail({ steps: steps({ 0: "running" }) })) : problem(502)) });
  renderAt(`/clients/${ID}`);
  await within(await screen.findByRole("table", { name: "Provisioning" })).findByText("Running");
  up = false;
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(await screen.findByText("Couldn't refresh this client")).toBeDefined();
  expect(within(screen.getByRole("table", { name: "Provisioning" })).getByText("Running")).toBeDefined();
  up = true;
  await act(() => vi.advanceTimersByTimeAsync(5000));
  await waitFor(() => expect(screen.queryByText("Couldn't refresh this client")).toBeNull());
});

test("the form checks its fields before sending, and a taken workspace code shows on that field", async () => {
  const calls = signedInApi({
    "GET /owner/templates": () => json(TEMPLATES),
    "POST /owner/clients": () => problem(409, [{ path: "/workspace_code", message: "This workspace code is already taken" }]),
  });
  renderAt("/clients/new");
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Create client" }));
  expect(screen.getByText("Enter the client's name")).toBeDefined();
  expect(screen.getByText("Choose a template")).toBeDefined();

  await user.type(screen.getByLabelText(/^Name/), "Matrix Retail");
  const slug = screen.getByLabelText(/^Slug/);
  await user.clear(slug);
  await user.type(slug, "Matrix Retail");
  const code = screen.getByLabelText(/^Workspace code/);
  await user.clear(code);
  await user.type(code, "MX");
  await user.selectOptions(await screen.findByLabelText(/^Template/), "matrix-retail");
  await user.click(screen.getByRole("button", { name: "Create client" }));
  expect(screen.getByText("Use lowercase letters, digits and hyphens (1–40, not at the ends)")).toBeDefined();
  expect(screen.getByText("Use 3–64 letters, digits and hyphens")).toBeDefined();
  expect(calls).not.toContain("POST /owner/clients");

  await user.clear(slug);
  await user.type(slug, "matrix-retail");
  await user.clear(code);
  await user.type(code, "MATRIX");
  await user.click(screen.getByRole("button", { name: "Create client" }));
  expect(await screen.findByText("This workspace code is already taken")).toBeDefined();
  expect(code.getAttribute("aria-invalid")).toBe("true");
  expect(slug.getAttribute("aria-invalid")).not.toBe("true");
  expect(calls.filter((c) => c === "POST /owner/clients")).toHaveLength(1);
});

test("templates that fail to load say so on the field, not 'Choose a template'", async () => {
  signedInApi({ "GET /owner/templates": () => problem(502) });
  renderAt("/clients/new");
  const failed = "Couldn't load the templates. Reload the page to try again.";
  expect(await screen.findByText(failed)).toBeDefined();
  await userEvent.setup().click(screen.getByRole("button", { name: "Create client" }));
  expect(screen.getByText(failed)).toBeDefined();
  expect(screen.queryByText("Choose a template")).toBeNull();
});

test("a failed step shows its error, and Retry re-queues only that step", async () => {
  let state = detail({ status: "failed", steps: steps({ 0: "done", 1: "done", 2: "failed" }, "PATCH postgrest: 500") });
  const calls = signedInApi({
    [`GET /owner/clients/${ID}`]: () => json(state),
    [`POST /owner/clients/${ID}/provisioning/retry`]: () => {
      state = detail({ steps: steps({ 0: "done", 1: "done" }) });
      return json(state);
    },
  });
  renderAt(`/clients/${ID}`);
  const table = await screen.findByRole("table", { name: "Provisioning" });
  expect(within(table).getByText("PATCH postgrest: 500")).toBeDefined();
  expect(within(table).getByText("Failed")).toBeDefined();

  await userEvent.setup().click(screen.getByRole("button", { name: "Retry failed step" }));
  await waitFor(() => expect(within(screen.getByRole("table", { name: "Provisioning" })).queryByText("Failed")).toBeNull());
  expect(calls.filter((c) => c.startsWith("POST /owner/clients"))).toEqual([`POST /owner/clients/${ID}/provisioning/retry`]);
  expect(screen.queryByRole("button", { name: "Retry failed step" })).toBeNull();
});

test("a step waiting for a person shows the exact click path", async () => {
  const path = "Dashboard → zm-matrix-retail → Integrations → Data API → off";
  signedInApi({
    [`GET /owner/clients/${ID}`]: () => json(detail({ steps: steps({ 0: "done", 1: "done", 2: "needs_human" }, path) })),
  });
  renderAt(`/clients/${ID}`);
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain("data_api_off needs a person");
  expect(alert.textContent).toContain(path);
  expect(screen.getByRole("button", { name: "Retry failed step" })).toBeDefined(); // re-probes after the click
});

test("the form and the client page pass axe", async () => {
  signedInApi({
    "GET /owner/templates": () => json(TEMPLATES),
    [`GET /owner/clients/${ID}`]: () =>
      json(detail({ status: "ready", steps: steps({ ...allDone, 2: "needs_human" }, "Dashboard → Data API → off") })),
  });
  const { unmount } = renderAt("/clients/new");
  await screen.findByRole("option", { name: "Matrix Retail: gated retail store launch" });
  await expectNoAxeViolations();
  unmount();
  renderAt(`/clients/${ID}`);
  await screen.findByRole("table", { name: "Releases" });
  await expectNoAxeViolations();
});

test("when ready, Publish v1 asks first, then v1 appears in the releases list", async () => {
  let state = detail({ status: "ready", steps: steps(allDone) });
  const calls = signedInApi({
    [`GET /owner/clients/${ID}`]: () => json(state),
    [`POST /owner/clients/${ID}/publish`]: () => {
      const release = { version: 1, deployment_id: "dep-1", published_at: AT, published_by: OWNER.id };
      state = { ...state, last_release: 1, releases: [release] };
      return json({ version: 1, deployment_id: "dep-1", created: true, sha256: "x" });
    },
  });
  renderAt(`/clients/${ID}`);
  const user = userEvent.setup();
  expect(await screen.findByText("Nothing published yet")).toBeDefined();
  await user.click(await screen.findByRole("button", { name: "Publish draft as v1" }));

  const dialog = await screen.findByRole("dialog", { name: "Publish v1?" });
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(calls).not.toContain(`POST /owner/clients/${ID}/publish`);

  await user.click(screen.getByRole("button", { name: "Publish draft as v1" }));
  await user.click(within(await screen.findByRole("dialog", { name: "Publish v1?" })).getByRole("button", { name: "Publish" }));
  const releases = await screen.findByRole("table", { name: "Releases" });
  await waitFor(() => expect(within(releases).getByText("v1")).toBeDefined());
  expect(within(releases).getByText("dep-1")).toBeDefined();
  expect(await screen.findByRole("button", { name: "Publish draft as v2" })).toBeDefined();
  expect(calls.filter((c) => c === `POST /owner/clients/${ID}/publish`)).toHaveLength(1);
});

test("publishing an unchanged draft says so, not as an error", async () => {
  const v1 = { version: 1, deployment_id: "dep-1", published_at: AT, published_by: OWNER.id };
  signedInApi({
    [`GET /owner/clients/${ID}`]: () => json(detail({ status: "ready", steps: steps(allDone), last_release: 1, releases: [v1] })),
    [`POST /owner/clients/${ID}/publish`]: () => json({ version: 1, deployment_id: "dep-1", created: false, sha256: "x" }),
  });
  renderAt(`/clients/${ID}`);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Publish draft as v2" }));
  await user.click(within(await screen.findByRole("dialog", { name: "Publish v2?" })).getByRole("button", { name: "Publish" }));
  expect(await screen.findByText("Nothing changed since v1.")).toBeDefined();
  expect(screen.getByText("No new release")).toBeDefined();
  expect(screen.queryByText("Couldn't continue")).toBeNull();
});
