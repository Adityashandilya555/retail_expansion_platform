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

afterEach(() => vi.restoreAllMocks());

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
  expect(location()).toBe(`${PATH}?tab=tasks`);
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
  expect(calls.map((c) => new URL(c.url).pathname)).toEqual(["/app/cases/MX-0001", "/app/tasks/t-dd"]);
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

test.each(["light", "dark"] as const)("axe passes in %s with a form open", async (scheme) => {
  drawPage(tasksPage, {
    path: `${PATH}?tab=tasks&task=t-dd`,
    routes: { "GET /app/cases/MX-0001": () => sampleCase(), "GET /app/tasks/t-dd": () => sampleTask },
    scheme,
  });
  await screen.findByRole("group", { name: "Fill DD checklist" });
  await expectNoAxeViolations();
});
