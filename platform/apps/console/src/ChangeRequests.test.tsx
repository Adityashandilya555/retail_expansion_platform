import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { components } from "@zm/api-client";
import { expectNoAxeViolations } from "@zm/ui/test";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { ConsoleApp } from "./App";
import { forget } from "./session";

type ReviewOut = components["schemas"]["ReviewOut"];
type ReviewDetail = components["schemas"]["ReviewDetail"];
type Handler = (r: Request) => Response | Promise<Response>;

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const problem = (status: number, detail = "x") => json({ type: "about:blank", title: "x", status, detail, request_id: "r", errors: null }, status);
const OWNER = { id: "6f0c1b9e-5d2a-4c1e-9a7b-3d8e2f1a0b4c", email: "adi@zm.demo", name: "Adi" };
const PILOT = "0c9f7a52-3b1e-4c8d-9f6a-2e5b7d1c4a90";
const QSR = "1d8e6b41-2a0f-4b7c-8e59-1f4a6c0b3d81";
const CR = "7d2e4b10-9c3a-4f5e-8b6d-1a2c3e4f5a6b";
const AT = "2026-11-10T09:00:00Z";
const NONE = { added: [], removed: [], changed: [] };

function review(over: Partial<ReviewOut> = {}): ReviewOut {
  return {
    id: CR,
    client_id: PILOT,
    client_slug: "matrix-retail",
    client_name: "Matrix Retail",
    kind: "add_field",
    payload: { kind: "add_field", form: "start", fields: [{ label: "State", type: "text" }, { label: "District", type: "text" }] },
    summary: "Add State, District to the start form",
    note: "Our state report needs them",
    status: "submitted",
    review_note: null,
    shipped_in_version: null,
    created_at: AT,
    updated_at: AT,
    ...over,
  };
}

const detailOf = (over: Partial<ReviewDetail> = {}): ReviewDetail => ({
  ...review(),
  impact: {
    problems: [],
    notes: ["Add State, District to the start form. New sites fill it in; running sites keep their release's form, and their records show it empty."],
  },
  linked_changes: 0,
  ...over,
});

function api(routes: Record<string, Handler>): string[] {
  const calls: string[] = [];
  const all: Record<string, Handler> = {
    "POST /owner/auth/refresh": () => json({ access_token: "o1", token_type: "bearer", expires_in: 900 }),
    "GET /owner/me": () => json(OWNER),
    "GET /owner/clients": () =>
      json([
        { id: PILOT, slug: "matrix-retail", name: "Matrix Retail", workspace_code: "MR-1", template_key: "matrix-retail", status: "ready", created_at: AT, last_release: 1 },
        { id: QSR, slug: "qsr", name: "QSR", workspace_code: "QSR-1", template_key: "qsr", status: "ready", created_at: AT, last_release: 1 },
      ]),
    ...routes,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const key = `${request.method} ${url.pathname}`;
      calls.push(`${key}${url.search}`);
      return all[key]?.(request) ?? problem(404);
    }),
  );
  return calls;
}

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <ConsoleApp />
    </MemoryRouter>,
  );

afterEach(() => {
  forget();
  vi.unstubAllGlobals();
});

test("the queue lists every client's requests and filters by status and client", async () => {
  const calls = api({
    "GET /owner/change-requests": () => json([review(), review({ id: "c2", client_id: QSR, client_slug: "qsr", client_name: "QSR", summary: "Make city required", status: "in_review" })]),
  });
  renderAt("/change-requests");
  const user = userEvent.setup();
  const table = await screen.findByRole("table", { name: "Change requests" });
  await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(3));
  expect(within(table).getByText("Add State, District to the start form")).toBeDefined();
  expect(within(table).getByText("QSR")).toBeDefined();
  expect(within(table).getByText("In review")).toBeDefined();

  await user.selectOptions(screen.getByRole("combobox", { name: "Status" }), "in_review");
  await user.selectOptions(screen.getByRole("combobox", { name: "Client" }), QSR);
  await waitFor(() => expect(calls).toContain(`GET /owner/change-requests?status=in_review&client_id=${QSR}`));
  await expectNoAxeViolations();
});

test("a request shows what it would do to the draft, and a reject needs a note the client sees", async () => {
  let current = detailOf();
  const bodies: unknown[] = [];
  const calls = api({
    [`GET /owner/change-requests/${CR}`]: () => json(current),
    [`POST /owner/change-requests/${CR}/reject`]: async (r) => {
      bodies.push(await r.json());
      current = { ...current, status: "rejected", review_note: "Reports group by City" };
      return json(current);
    },
  });
  renderAt(`/change-requests/${CR}`);
  const user = userEvent.setup();
  expect(await screen.findByRole("heading", { name: "Add State, District to the start form" })).toBeDefined();
  expect(screen.getByText("Our state report needs them")).toBeDefined();
  const notes = screen.getByRole("list", { name: "What it does" });
  expect(within(notes).getByText(/running sites keep their release's form/)).toBeDefined();
  expect(screen.getByText("No versioning problems: the draft would still publish.")).toBeDefined();

  const reject = screen.getByRole("button", { name: "Reject" });
  expect(reject).toHaveProperty("disabled", true); // a note first
  await user.type(screen.getByRole("textbox", { name: "Note for the client admin" }), "Reports group by City");
  await user.click(reject);
  await waitFor(() => expect(bodies).toEqual([{ note: "Reports group by City" }]));
  expect(await screen.findByText("Rejected")).toBeDefined();
  expect(screen.queryByRole("button", { name: "Apply with agent" })).toBeNull(); // decided
  expect(calls.filter((c) => c.startsWith("POST /owner/change-requests"))).toEqual([`POST /owner/change-requests/${CR}/reject`]);
  await expectNoAxeViolations();
});

test("problems the request would cause are listed", async () => {
  api({
    [`GET /owner/change-requests/${CR}`]: () =>
      json(detailOf({ impact: { problems: [{ path: "/payload/form", message: "start isn't in the draft any more" }], notes: [] } })),
  });
  renderAt(`/change-requests/${CR}`);
  const problems = await screen.findByRole("list", { name: "Problems" });
  expect(within(problems).getByText("/payload/form: start isn't in the draft any more")).toBeDefined();
});

test("approve waits for the agent's edits, then marks it approved for the next publish", async () => {
  let current = detailOf({ status: "in_review" });
  const calls = api({
    [`GET /owner/change-requests/${CR}`]: () => json(current),
    [`POST /owner/change-requests/${CR}/approve`]: () => {
      current = { ...current, status: "approved" };
      return json(current);
    },
  });
  const { unmount } = renderAt(`/change-requests/${CR}`);
  const user = userEvent.setup();
  const approve = await screen.findByRole("button", { name: "Approve" });
  expect(approve).toHaveProperty("disabled", true);
  expect(screen.getByText("Apply it with the agent first: no edit in the draft is linked to it yet.")).toBeDefined();
  unmount();

  current = detailOf({ status: "in_review", linked_changes: 3 });
  renderAt(`/change-requests/${CR}`);
  expect(await screen.findByText("3 edits in the draft are linked to it.")).toBeDefined();
  await user.click(screen.getByRole("button", { name: "Approve" }));
  expect(await screen.findByText("Approved")).toBeDefined();
  expect(screen.getByText("The next publish ships it.")).toBeDefined();
  expect(calls).toContain(`POST /owner/change-requests/${CR}/approve`);
});

test("Apply with agent opens the client's Configure tab with the request in the dock", async () => {
  api({
    [`GET /owner/change-requests/${CR}`]: () => json(detailOf()),
    [`GET /owner/clients/${PILOT}`]: () =>
      json({
        id: PILOT,
        slug: "matrix-retail",
        name: "Matrix Retail",
        workspace_code: "MR-1",
        template_key: "matrix-retail",
        status: "ready",
        created_at: AT,
        last_release: 1,
        steps: [],
        releases: [{ version: 1, deployment_id: "dep-1", published_at: AT, published_by: OWNER.id }],
      }),
    [`GET /owner/clients/${PILOT}/draft`]: () => json({ workflow: { schema_version: 1, apps: { theme: "quartz" } }, template_key: "matrix-retail", base_version: 1, updated_at: AT, updated_by: null }),
    [`GET /owner/clients/${PILOT}/draft/validate`]: () => json([]),
    [`GET /owner/clients/${PILOT}/draft/diff`]: () => json({ stages: NONE, tasks: NONE, fields: NONE, blocks: [], theme: null, accent: null }),
    [`GET /owner/clients/${PILOT}/draft/changes`]: () => json([]),
    [`GET /owner/clients/${PILOT}/agent/conversations`]: () => json([]),
  });
  renderAt(`/change-requests/${CR}`);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Apply with agent" }));
  expect(await screen.findByRole("tab", { name: "Configure", selected: true })).toBeDefined();
  const banner = await screen.findByText(/Applying change request: Add State, District to the start form/);
  expect(banner).toBeDefined();
});
