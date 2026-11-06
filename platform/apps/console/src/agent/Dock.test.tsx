import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import type { components } from "@zm/api-client";
import { expectNoAxeViolations, renderWithZm } from "@zm/ui/test";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { ConsoleApp } from "../App";
import { forget } from "../session";
import { Dock } from "./Dock";

type ChangeOut = components["schemas"]["ChangeOut"];
type Transcript = components["schemas"]["Transcript"];
type Handler = (r: Request) => Response | Promise<Response>;

const ID = "0c9f7a52-3b1e-4c8d-9f6a-2e5b7d1c4a90";
const CID = "7d2e4b10-9c3a-4f5e-8b6d-1a2c3e4f5a6b";
const OWNER = "6f0c1b9e-5d2a-4c1e-9a7b-3d8e2f1a0b4c";
const AT = "2026-11-06T08:00:00Z";
const CLIENT = `/owner/clients/${ID}`;
const AGENT = `${CLIENT}/agent/conversations`;
const USAGE = { input_tokens: 1200, cache_creation_input_tokens: 0, cache_read_input_tokens: 3000, output_tokens: 300 };
const NOC = "Added: Fire NOC upload";
const L2 = "Approval of legal_licensing: legal L2";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const problem = (status: number, detail = "x") => json({ type: "about:blank", title: "x", status, detail, request_id: "r", errors: null }, status);

function change(id: number, summary: string, over: Partial<ChangeOut> = {}): ChangeOut {
  const patch = [{ op: "add", path: "/fields/fire_noc", value: { type: "document" } }];
  return { id, at: AT, author_owner_id: OWNER, conversation_id: CID, change_request_id: null, tool: "add_document_slot", patch, summary, undoes: null, after_sha256: "a1", ...over };
}

/** The API as the dock calls it: no conversations yet, a valid draft, `log` as the change log, plus `routes`. */
function dockApi(routes: Record<string, Handler>, log: () => ChangeOut[] = () => []): string[] {
  const calls: string[] = [];
  const all: Record<string, Handler> = {
    [`GET ${AGENT}`]: () => json([]),
    [`POST ${AGENT}`]: async (r) => json({ id: CID, owner_id: OWNER, mode: ((await r.json()) as { mode: string }).mode, started_at: AT }, 201),
    [`GET ${CLIENT}/draft/validate`]: () => json([]),
    [`GET ${CLIENT}/draft/changes`]: (r) => json(log().slice(0, Number(new URL(r.url).searchParams.get("limit") ?? 100))),
    ...routes,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const key = `${request.method} ${new URL(request.url).pathname}`;
      calls.push(key);
      return all[key]?.(request) ?? problem(404);
    }),
  );
  return calls;
}

/** A turn's text/event-stream body that the test writes event by event, then ends or drops. */
function sse() {
  const encoder = new TextEncoder();
  let controller: ReadableStreamDefaultController<Uint8Array> | undefined;
  const body = new ReadableStream<Uint8Array>({ start: (c) => void (controller = c) });
  const raw = (text: string) => controller?.enqueue(encoder.encode(text));
  return {
    response: () => new Response(body, { headers: { "content-type": "text/event-stream" } }),
    raw,
    send: (event: string, data: object) => raw(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`),
    end: () => controller?.close(),
    drop: () => controller?.error(new TypeError("network error")),
  };
}

function renderDock(scheme?: "light" | "dark") {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderWithZm(
    <QueryClientProvider client={queries}>
      <Dock clientId={ID} />
    </QueryClientProvider>,
    { scheme },
  );
  return userEvent.setup();
}

const say = (user: UserEvent, text: string) => user.type(screen.getByLabelText("Message"), `${text}{Enter}`);
const log = () => within(screen.getByRole("log", { name: "Chat" }));

afterEach(() => {
  forget();
  vi.unstubAllGlobals();
});

test("text streams into a polite live region, and each chip appears when its edit is done", async () => {
  const turn = sse();
  let sent: unknown;
  const calls = dockApi({
    [`POST ${AGENT}/${CID}/messages`]: async (r) => {
      sent = await r.json();
      return turn.response();
    },
  });
  const user = renderDock();
  await say(user, "Add a Fire NOC upload");
  expect(screen.getByRole("log", { name: "Chat" }).getAttribute("aria-live")).toBe("polite");
  expect(await log().findByText("Add a Fire NOC upload")).toBeDefined();

  turn.send("text_delta", { text: "Adding" });
  turn.send("text_delta", { text: " the upload." });
  expect(await log().findByText("Adding the upload.")).toBeDefined();
  turn.send("tool_call", { name: "add_document_slot" });
  expect(await screen.findByText("Running add_document_slot…")).toBeDefined();
  expect(log().queryByRole("button", { name: NOC })).toBeNull(); // not before its edit is done
  turn.send("change", { change_id: 1, summary: NOC });
  expect(await log().findByRole("button", { name: NOC })).toBeDefined();
  turn.send("tool_call", { name: "set_approval_chain" });
  turn.send("change", { change_id: 2, summary: L2 });
  expect(await log().findByRole("button", { name: L2 })).toBeDefined();
  turn.send("text_delta", { text: "Done." });
  turn.send("done", { usage: USAGE });
  turn.end();
  expect(await log().findByText("Done.")).toBeDefined();
  expect(await screen.findByText(/last turn 1,200 in, 3,000 cached, 300 out; this conversation 1,200 in/)).toBeDefined();
  await waitFor(() => expect(screen.queryByText(/Running/)).toBeNull());

  expect(sent).toEqual({ text: "Add a Fire NOC upload" });
  expect(calls.filter((c) => c === `POST ${AGENT}`)).toHaveLength(1); // the conversation, started on the first message
  expect(calls.filter((c) => c === `GET ${CLIENT}/draft/validate`).length).toBeGreaterThan(1); // each change re-validates
});

test("retry drops the text its call streamed, and an event split across chunks still arrives", async () => {
  const turn = sse();
  dockApi({ [`POST ${AGENT}/${CID}/messages`]: () => turn.response() });
  const user = renderDock();
  await say(user, "Hi");
  turn.send("text_delta", { text: "Let me add" });
  expect(await log().findByText("Let me add")).toBeDefined();
  turn.send("retry", {});
  turn.raw("event: text_del");
  turn.raw('ta\ndata: {"text": "Hello."}\n\n: ping\n\n');
  expect(await log().findByText("Hello.")).toBeDefined();
  expect(log().queryByText(/Let me add/)).toBeNull();
  turn.send("done", { usage: USAGE });
  turn.end();
});

test("a chip opens its change's diff; Undo reverts the last change with an undone chip, or says why it can't", async () => {
  const turn = sse();
  let changes = [change(1, NOC)];
  let refuse = false;
  dockApi(
    {
      [`POST ${AGENT}/${CID}/messages`]: () => turn.response(),
      [`POST ${CLIENT}/draft/undo`]: () => {
        if (refuse) return problem(409, "Can't undo 'Added: Fire NOC upload': field 'fire_noc' is in v2");
        const inverse = [{ op: "remove", path: "/fields/fire_noc" }];
        const meanwhile = change(3, "Theme: ember", { tool: "set_theme", conversation_id: null }); // another owner's edit, just after
        changes = [meanwhile, change(2, `Undo: ${NOC}`, { tool: "undo", undoes: 1, patch: inverse }), ...changes];
        return json({ workflow: {}, template_key: "matrix-retail", base_version: null, updated_at: AT, updated_by: OWNER });
      },
    },
    () => changes,
  );
  const user = renderDock();
  await say(user, "Add a Fire NOC upload");
  turn.send("change", { change_id: 1, summary: NOC });
  turn.send("done", { usage: USAGE });
  turn.end();

  await user.click(await log().findByRole("button", { name: NOC }));
  const diff = await screen.findByRole("dialog", { name: NOC });
  expect(diff.textContent).toContain('"path": "/fields/fire_noc"');
  await user.keyboard("{Escape}");

  await user.click(screen.getByRole("button", { name: "Undo last change" }));
  const undone = await log().findByRole("button", { name: `Undo: ${NOC}` });
  expect(log().getByText("Undone")).toBeDefined();
  await user.click(undone);
  expect((await screen.findByRole("dialog", { name: `Undo: ${NOC}` })).textContent).toContain('"op": "remove"'); // the draft's diff shows the revert
  await user.keyboard("{Escape}");

  refuse = true;
  await user.click(screen.getByRole("button", { name: "Undo last change" }));
  expect(await log().findByText("Can't undo 'Added: Fire NOC upload': field 'fire_noc' is in v2")).toBeDefined();
  expect(log().getAllByRole("button", { name: `Undo: ${NOC}` })).toHaveLength(1);
});

test("an invalid draft disables Publish and shows its first problem; Preview waits for #42", async () => {
  const problems = [
    { path: "flows[0].stages[1].after[0]", message: "unknown stage 'fitout'" },
    { path: "fields.gst_no.type", message: "second problem" },
  ];
  dockApi({ [`GET ${CLIENT}/draft/validate`]: () => json(problems) });
  renderDock();
  const first = await screen.findByText(/unknown stage 'fitout'/);
  expect(first.textContent).toContain("flows[0].stages[1].after[0]");
  expect(screen.queryByText(/second problem/)).toBeNull();
  const publish = screen.getByRole("button", { name: "Publish" });
  expect(publish.hasAttribute("disabled")).toBe(true);
  expect(publish.getAttribute("aria-describedby")).toBe(first.id);
  const preview = screen.getByRole("button", { name: "Preview" });
  expect(preview.hasAttribute("disabled")).toBe(true);
  expect(document.getElementById(preview.getAttribute("aria-describedby") ?? "")?.textContent).toBe("Preview comes with #42.");
});

test("a valid draft publishes after a confirm, and says when nothing changed", async () => {
  let created = true;
  const calls = dockApi({ [`POST ${CLIENT}/publish`]: () => json({ version: created ? 3 : 2, deployment_id: "d", created, sha256: "s" }) });
  const user = renderDock();
  const publish = await screen.findByRole("button", { name: "Publish" });
  await waitFor(() => expect(publish.hasAttribute("disabled")).toBe(false));
  await user.click(publish);
  const dialog = await screen.findByRole("dialog", { name: "Publish the draft?" });
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(calls).not.toContain(`POST ${CLIENT}/publish`);

  await user.click(publish);
  await user.click(within(await screen.findByRole("dialog", { name: "Publish the draft?" })).getByRole("button", { name: "Publish" }));
  expect(await screen.findByText("Release v3 is live.")).toBeDefined();
  created = false;
  await user.click(publish);
  await user.click(within(await screen.findByRole("dialog", { name: "Publish the draft?" })).getByRole("button", { name: "Publish" }));
  expect(await screen.findByText("Nothing changed since v2.")).toBeDefined();
});

test("a dropped stream reads the conversation back and shows its final state without duplicate chips", async () => {
  const turn = sse();
  const saved: Transcript = {
    entries: [
      { kind: "owner", text: "Add a Fire NOC upload" },
      { kind: "agent", text: "Adding the upload." },
      { kind: "change", change_id: 1, summary: NOC },
      { kind: "change", change_id: 2, summary: L2 },
      { kind: "agent", text: "Done." },
    ],
    usage: { ...USAGE, output_tokens: 900 },
  };
  const calls = dockApi({
    [`POST ${AGENT}/${CID}/messages`]: () => turn.response(),
    [`GET ${AGENT}/${CID}`]: () => json(saved),
  });
  const user = renderDock();
  await say(user, "Add a Fire NOC upload");
  turn.send("text_delta", { text: "Adding the upload." });
  turn.send("change", { change_id: 1, summary: NOC });
  await log().findByRole("button", { name: NOC });
  turn.drop();

  expect(await log().findByText("Done.")).toBeDefined();
  expect(log().getAllByRole("button", { name: NOC })).toHaveLength(1);
  expect(log().getAllByRole("button", { name: L2 })).toHaveLength(1);
  expect(log().getAllByText("Add a Fire NOC upload")).toHaveLength(1);
  expect(log().getByText(/The connection dropped/)).toBeDefined();
  expect(screen.getByText(/this conversation 1,200 in, 3,000 cached, 900 out/)).toBeDefined();
  expect(calls).toContain(`GET ${AGENT}/${CID}`);
});

test("refusals, errors and a busy conversation show in the conversation; the message stays to send again", async () => {
  const turn = sse();
  let busy = true;
  dockApi({
    [`POST ${AGENT}/${CID}/messages`]: () =>
      busy ? problem(409, "A turn is already running in this conversation. Send again when it has finished.") : turn.response(),
  });
  const user = renderDock();
  await say(user, "Add a GST number");
  expect(await log().findByText(/A turn is already running/)).toBeDefined();
  expect(screen.getByLabelText<HTMLTextAreaElement>("Message").value).toBe("Add a GST number");

  busy = false;
  await user.click(screen.getByRole("button", { name: "Send" }));
  turn.send("refusal", { category: "cyber" });
  turn.send("error", { message: "The model is unavailable (APIConnectionError). Try again." });
  turn.send("done", { usage: USAGE });
  turn.end();
  expect(await log().findByText("The agent declined this request")).toBeDefined();
  expect(log().getByText(/Nothing in it was applied \(cyber\)/)).toBeDefined();
  expect(log().getByText("The model is unavailable (APIConnectionError). Try again.")).toBeDefined();
  expect(screen.getByLabelText<HTMLTextAreaElement>("Message").value).toBe("");
});

test("the conversation list opens a stored conversation; the mode toggle starts a new one in that mode", async () => {
  const turn = sse();
  const modes: unknown[] = [];
  dockApi({
    [`GET ${AGENT}`]: () => json([{ id: CID, owner_id: OWNER, mode: "edit", started_at: AT }]),
    [`GET ${AGENT}/${CID}`]: () => json({ entries: [{ kind: "agent", text: "Earlier reply." }, { kind: "change", change_id: 1, summary: NOC }], usage: USAGE }),
    [`POST ${AGENT}`]: async (r) => {
      modes.push(await r.json());
      return json({ id: CID, owner_id: OWNER, mode: "configure", started_at: AT }, 201);
    },
    [`POST ${AGENT}/${CID}/messages`]: () => turn.response(),
  });
  const user = renderDock();
  const select = await screen.findByLabelText("Conversation");
  await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(2));
  await user.selectOptions(select, CID);
  expect(await log().findByText("Earlier reply.")).toBeDefined();
  expect(log().getByRole("button", { name: NOC })).toBeDefined();
  expect(screen.getByRole("radio", { name: "Edit" })).toHaveProperty("checked", true);
  expect(screen.getByText(/this conversation 1,200 in/)).toBeDefined();

  await user.click(screen.getByRole("radio", { name: "Configure" }));
  expect(log().queryByText("Earlier reply.")).toBeNull();
  await say(user, "Set up Matrix Retail");
  turn.send("done", { usage: USAGE });
  turn.end();
  await waitFor(() => expect(modes).toEqual([{ mode: "configure" }]));
});

test.each(["light", "dark"] as const)("the dock passes axe in %s", async (scheme) => {
  dockApi({
    [`GET ${AGENT}`]: () => json([{ id: CID, owner_id: OWNER, mode: "edit", started_at: AT }]),
    [`GET ${AGENT}/${CID}`]: () =>
      json({
        entries: [
          { kind: "owner", text: "Add a Fire NOC upload" },
          { kind: "agent", text: "Adding the upload." },
          { kind: "change", change_id: 1, summary: NOC },
          { kind: "refusal", category: null },
        ],
        usage: USAGE,
      }),
    [`GET ${CLIENT}/draft/validate`]: () => json([{ path: "flows[0].stages[1].after[0]", message: "unknown stage 'fitout'" }]),
  });
  const user = renderDock(scheme);
  const select = await screen.findByLabelText("Conversation");
  await waitFor(() => expect(within(select).getAllByRole("option")).toHaveLength(2));
  await user.selectOptions(select, CID);
  await log().findByRole("button", { name: NOC });
  await screen.findByText(/unknown stage/);
  await expectNoAxeViolations();
});

test("the client page's Configure tab opens the dock", async () => {
  const steps = ["supabase_project", "seed_draft"].map((step, i) => ({ step, seq: i + 1, status: "done", attempts: 0, last_error: null, updated_at: AT }));
  dockApi({
    "POST /owner/auth/refresh": () => json({ access_token: "o1", token_type: "bearer", expires_in: 900 }),
    "GET /owner/me": () => json({ id: OWNER, email: "adi@zm.demo", name: "Adi" }),
    [`GET ${CLIENT}`]: () =>
      json({ id: ID, slug: "matrix-retail", name: "Matrix Retail", workspace_code: "MR-1", template_key: "matrix-retail", status: "ready", created_at: AT, last_release: null, steps, releases: [] }),
    [`GET ${CLIENT}/draft`]: () => json({ workflow: { schema_version: 1, apps: { theme: "quartz" } }, template_key: "matrix-retail", base_version: null, updated_at: AT, updated_by: null }),
  });
  render(
    <MemoryRouter initialEntries={[`/clients/${ID}`]}>
      <ConsoleApp />
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  await user.click(await screen.findByRole("tab", { name: "Configure" }));
  expect(await screen.findByLabelText("Message")).toBeDefined();
  expect(screen.getByRole("button", { name: "Publish" })).toBeDefined();
  await user.click(screen.getByRole("tab", { name: "Overview" }));
  expect(await screen.findByRole("table", { name: "Provisioning" })).toBeDefined();
});
