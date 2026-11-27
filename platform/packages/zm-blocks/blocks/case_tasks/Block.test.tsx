import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expectNoAxeViolations } from "@zm/ui/test";
import { afterEach, expect, test, vi } from "vitest";
import { problem, sampleCase, sampleTask } from "../../src/test";
import { casePage, drawPage, location } from "../../src/test/render";

const PATH = "/app/cases/MX-0001";
const tasksPage = casePage({ tasks: [{ block: "case_tasks", props: {} }] });
const committed = { submission_id: "s1", status: "committed", case_id: "c-1", release_version: 1 };
const done = {
  task: "legal_dd_checklist",
  task_name: "Fill DD checklist",
  by: "u-l1",
  by_name: "Legal Executive",
  outcome: null,
  comment: null,
  at: "2026-10-21T10:00:00Z",
};

const rows = (table: HTMLElement) =>
  [...table.querySelectorAll("tbody tr")].map((tr) => [...tr.querySelectorAll("td")].map((td) => td.textContent));

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("?tab=tasks&task=<id> opens the DD checklist inline; submitting removes it and adds a history row, no reload", async () => {
  let state = sampleCase();
  let sent: unknown;
  const calls = drawPage(tasksPage, {
    path: `${PATH}?tab=tasks&task=t-dd`,
    routes: {
      "GET /app/cases/MX-0001": () => state,
      "GET /app/tasks/t-dd": () => sampleTask,
      "POST /app/tasks/t-dd/submit": async (r) => {
        sent = await r.json();
        state = sampleCase({ tasks: [], open_tasks: 0, my_tasks: 0, history: [...sampleCase().history, done] });
        return committed;
      },
    },
  });
  const form = await screen.findByRole("group", { name: "Fill DD checklist" });
  // the earlier steps' values, by the release's labels
  expect(screen.getByText("Signed LOI")).toBeDefined();
  expect(screen.getByText("loi-mx-0001.pdf")).toBeDefined();
  const history = screen.getByRole("table", { name: "Decision history" });
  expect(rows(history).map((r) => r[0])).toEqual(["Send to Legal"]);

  const user = userEvent.setup();
  await user.click(within(within(form).getByRole("radiogroup", { name: "Title document clear" })).getByRole("radio", { name: "Yes" }));
  await user.click(screen.getByRole("button", { name: "Submit" }));

  await waitFor(() => expect(screen.queryByRole("group", { name: "Fill DD checklist" })).toBeNull());
  expect(await screen.findByText("Nothing for you to do on this site")).toBeDefined();
  expect(rows(history).map((r) => r.slice(0, 4))).toEqual([
    ["Send to Legal", "BD Manager", "—", ""],
    ["Fill DD checklist", "Legal Executive", "—", ""],
  ]);
  expect(screen.getByRole("table", { name: "Decision history" })).toBe(history); // same table: no reload
  await waitFor(() => expect(location()).toBe(`${PATH}?tab=tasks`)); // the router may apply it a moment later
  expect(await screen.findByText("Fill DD checklist is done.")).toBeDefined(); // the toast
  expect(sent).toEqual({ values: { dd_title_doc: true } });
  const post = calls.find((c) => c.method === "POST");
  expect(post?.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
});

test("a task opens from the list and its id goes into the URL", async () => {
  const calls = drawPage(tasksPage, {
    path: `${PATH}?tab=tasks`,
    routes: { "GET /app/cases/MX-0001": () => sampleCase(), "GET /app/tasks/t-dd": () => sampleTask },
  });
  await userEvent.setup().click(await screen.findByRole("button", { name: /Fill DD checklist/ }));
  expect(await screen.findByRole("group", { name: "Fill DD checklist" })).toBeDefined();
  expect(location()).toBe(`${PATH}?tab=tasks&task=t-dd`);
  // the form's document slot then lists the case's versions of its field
  await waitFor(() => expect(calls.map((c) => new URL(c.url).pathname)).toEqual(["/app/cases/MX-0001", "/app/tasks/t-dd", "/app/cases/MX-0001/files"]));
});

test("the API's 422 shows on the field", async () => {
  drawPage(tasksPage, {
    path: `${PATH}?tab=tasks&task=t-dd`,
    routes: {
      "GET /app/cases/MX-0001": () => sampleCase(),
      "GET /app/tasks/t-dd": () => sampleTask,
      "POST /app/tasks/t-dd/submit": () => problem(422, [{ path: "/dd_notes", message: "Say why the title isn't clear" }]),
    },
  });
  await screen.findByRole("group", { name: "Fill DD checklist" });
  await userEvent.setup().click(screen.getByRole("button", { name: "Submit" }));
  expect(await screen.findByText("Say why the title isn't clear")).toBeDefined();
  expect(screen.getByRole("group", { name: "Fill DD checklist" })).toBeDefined(); // still open
});

test("tasks the user can only read aren't listed; no history says so", async () => {
  const lead = { id: "t-v", name: "Finalise DD verdict", stage: "legal", assignee: null, can_act: false };
  drawPage(tasksPage, {
    path: `${PATH}?tab=tasks`,
    routes: { "GET /app/cases/MX-0001": () => sampleCase({ tasks: [lead], history: [] }) },
  });
  expect(await screen.findByText("Nothing for you to do on this site")).toBeDefined();
  expect(screen.queryByText("Finalise DD verdict")).toBeNull();
  expect(screen.getByText("No decisions yet")).toBeDefined();
});

test("a teammate's task opened from the team inbox (?task=) shows read-only", async () => {
  const lead = { id: "t-v", name: "Finalise DD verdict", stage: "legal", assignee: "u-l2b", can_act: false };
  const readOnly = { ...sampleTask, editable: false, task: { ...sampleTask.task, id: "t-v", name: "Finalise DD verdict" } };
  drawPage(tasksPage, {
    path: `${PATH}?tab=tasks&task=t-v`,
    routes: { "GET /app/cases/MX-0001": () => sampleCase({ tasks: [lead] }), "GET /app/tasks/t-v": () => readOnly },
  });
  expect(await screen.findByRole("button", { name: /Finalise DD verdict.*View only/ })).toBeDefined();
  expect(await screen.findByRole("group", { name: "Fill DD checklist" })).toBeDefined(); // its form, read-only
  expect(screen.queryByRole("button", { name: "Submit" })).toBeNull();
  expect(screen.queryByText("Nothing for you to do on this site")).toBeNull();
});

const DD_VALUE = { key: "cases/MX-0001/legal_dd_checklist/dd_report/1-dd.pdf", sha256: "a".repeat(64), name: "dd.pdf" };
const DD_V1 = {
  id: "f1",
  field: "dd_report",
  version: 1,
  name: "dd.pdf",
  size_bytes: 5,
  content_type: "application/pdf",
  uploaded_by: "u-l1",
  uploaded_by_name: "Legal Executive",
  uploaded_at: "2026-11-27T10:00:00Z",
};

/** Storage answering the signed PUT at once. */
class StorageXhr {
  static urls: string[] = [];
  upload = {};
  status = 200;
  onload: (() => void) | null = null;
  open(_: string, url: string) {
    StorageXhr.urls.push(url);
  }
  setRequestHeader() {}
  send() {
    queueMicrotask(() => this.onload?.());
  }
}

test("a document slot uploads for the task, lists the case's versions and submits the confirmed value", async () => {
  vi.stubGlobal("XMLHttpRequest", StorageXhr);
  let uploaded: unknown, sent: unknown;
  let versions: unknown[] = [];
  const calls = drawPage(tasksPage, {
    path: `${PATH}?tab=tasks&task=t-dd`,
    routes: {
      "GET /app/cases/MX-0001": () => sampleCase(),
      "GET /app/tasks/t-dd": () => sampleTask,
      "GET /app/cases/MX-0001/files": () => versions,
      "POST /app/files/upload": async (r) => {
        uploaded = await r.json();
        return { file_id: "f1", upload_url: "http://storage.test/put?token=t", key: DD_VALUE.key, expires_at: "2026-11-27T12:00:00Z" };
      },
      "POST /app/files/f1/confirm": () => {
        versions = [DD_V1];
        return DD_VALUE;
      },
      "POST /app/tasks/t-dd/submit": async (r) => {
        sent = await r.json();
        return committed;
      },
    },
  });
  await screen.findByRole("group", { name: "Fill DD checklist" });
  const user = userEvent.setup();
  const input = document.querySelector("input[type=file]") as HTMLInputElement;
  await user.upload(input, new File(["%PDF-"], "dd.pdf", { type: "application/pdf" }));

  expect(await screen.findByRole("button", { name: "Download dd.pdf v1" })).toBeDefined();
  expect(uploaded).toEqual({ task_id: "t-dd", field: "dd_report", name: "dd.pdf", size_bytes: 5 });
  expect(StorageXhr.urls).toEqual(["http://storage.test/put?token=t"]);
  const listed = calls.filter((c) => new URL(c.url).pathname === "/app/cases/MX-0001/files");
  expect(listed.map((c) => new URL(c.url).searchParams.get("field"))).toEqual(["dd_report", "dd_report"]);

  await user.click(screen.getByRole("button", { name: "Submit" }));
  await waitFor(() => expect(sent).toEqual({ values: { dd_report: DD_VALUE } }));
});

test("a workspace without Storage: the slot says uploads aren't available", async () => {
  drawPage(tasksPage, {
    path: `${PATH}?tab=tasks&task=t-dd`,
    routes: {
      "GET /app/cases/MX-0001": () => sampleCase(),
      "GET /app/tasks/t-dd": () => sampleTask,
      "GET /app/cases/MX-0001/files": () => problem(503),
    },
  });
  expect(await screen.findByText("Uploads aren't available in this workspace")).toBeDefined();
  expect(screen.queryByRole("button", { name: "Choose file" })).toBeNull();
});

test.each(["light", "dark"] as const)("axe passes in %s with a form open", async (scheme) => {
  drawPage(tasksPage, {
    path: `${PATH}?tab=tasks&task=t-dd`,
    routes: { "GET /app/cases/MX-0001": () => sampleCase(), "GET /app/tasks/t-dd": () => sampleTask },
    scheme,
  });
  await screen.findByRole("group", { name: "Fill DD checklist" });
  await expectNoAxeViolations();
});
