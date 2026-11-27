import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import { expectNoAxeViolations } from "@zm/ui/test";
import { afterEach, expect, test, vi } from "vitest";
import type { UiPage } from ".";
import { problem, sampleCases, startableUi } from "./test";
import { drawPage, location } from "./test/render";

// "New Site" (S3-02) on the Sites page, as BD L1 meets it: the start form in a modal, `POST /app/cases`.
const SITES: UiPage = {
  key: "sites",
  layout: "standard",
  regions: {
    main: [
      { block: "task_inbox", props: { scope: "mine" } },
      { block: "case_table", props: { flow: "store_launch" } },
    ],
  },
};
const START = { site_name: "Whitefield", city: "Bengaluru", address: "ITPL Main Road", store_model: "kiosk" };
const CREATED = {
  id: "c-4",
  code: "MX-0004",
  flow: "store_launch",
  release_version: 1,
  process_instance_id: "pi-4",
  created_at: "2026-10-25T09:00:00Z",
};
const readOnly = () =>
  Response.json(
    { type: "about:blank", title: "Forbidden", status: 403, detail: "A preview is read-only", request_id: "r", errors: null },
    { status: 403 },
  );
const reads = { "GET /app/cases": () => sampleCases, "GET /app/tasks": () => [] };

/** A screen from sm up: every `min-width` query matches. */
const wide = (query: string) =>
  ({
    matches: query.includes("min-width"),
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }) as unknown as MediaQueryList;

afterEach(() => vi.restoreAllMocks());

async function openStart(): Promise<{ user: UserEvent; dialog: HTMLElement }> {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "New Site" }));
  return { user, dialog: await screen.findByRole("dialog", { name: "New Site" }) };
}

/** One paste per text field (keystroke by keystroke is slow on a busy runner). */
async function fill(user: UserEvent, dialog: HTMLElement) {
  const form = within(dialog);
  for (const [label, value] of [[/Site name/, START.site_name], [/City/, START.city], [/Address/, START.address]] as const) {
    await user.click(form.getByLabelText(label));
    await user.paste(value);
  }
  await user.click(form.getByLabelText(/Store model/, { selector: "input" }));
  await user.click(await screen.findByRole("option", { name: "Kiosk" }));
}

const sent = (calls: Request[], key: string) => calls.filter((c) => `${c.method} ${new URL(c.url).pathname}` === key);

test("the four start fields start the site: its page opens, and the sites and inboxes reload", async () => {
  let body: unknown;
  const calls = drawPage(SITES, {
    path: "/app/sites",
    ui: startableUi,
    routes: {
      ...reads,
      "POST /app/cases": async (r) => {
        body = await r.json();
        return Response.json(CREATED, { status: 201 });
      },
    },
  });
  const { user, dialog } = await openStart();
  await fill(user, dialog);
  await user.click(within(dialog).getByRole("button", { name: "Submit" }));

  await waitFor(() => expect(location()).toBe("/app/cases/MX-0004"));
  expect(body).toEqual({ flow: "store_launch", values: START });
  expect(screen.queryByRole("dialog", { name: "New Site" })).toBeNull();
  // ["app","cases"] and ["app","tasks"] were invalidated: both lists asked again
  await waitFor(() => expect(sent(calls, "GET /app/cases")).toHaveLength(2));
  await waitFor(() => expect(sent(calls, "GET /app/tasks")).toHaveLength(2));
});

test("an empty required field says Required and sends nothing; the API's 422 lands on its field", async () => {
  const calls = drawPage(SITES, {
    path: "/app/sites",
    ui: startableUi,
    routes: { ...reads, "POST /app/cases": () => problem(422, [{ path: "/city", message: "Not a city we open in" }]) },
  });
  const { user, dialog } = await openStart();
  await user.type(within(dialog).getByLabelText(/Site name/), START.site_name);
  await user.click(within(dialog).getByRole("button", { name: "Submit" }));
  expect(within(dialog).getAllByText("Required")).toHaveLength(3); // city, address, store model
  expect(sent(calls, "POST /app/cases")).toEqual([]);

  await user.clear(within(dialog).getByLabelText(/Site name/));
  await fill(user, dialog);
  await user.click(within(dialog).getByRole("button", { name: "Submit" }));
  expect(await within(dialog).findByText("Not a city we open in")).toBeDefined();
  expect(within(dialog).getByLabelText(/City/).getAttribute("aria-invalid")).toBe("true");
  expect(location()).toBe("/app/sites"); // still here, the form still open
});

test("under a preview the form opens and Submit shows the read-only error", async () => {
  drawPage(SITES, { path: "/app/sites", ui: startableUi, routes: { ...reads, "POST /app/cases": readOnly } });
  const { user, dialog } = await openStart();
  await fill(user, dialog);
  await user.click(within(dialog).getByRole("button", { name: "Submit" }));
  expect(await screen.findByText("A preview is read-only")).toBeDefined();
  expect(screen.getByRole("dialog", { name: "New Site" })).toBe(dialog);
  expect(location()).toBe("/app/sites");
});

test("while Submit is in flight the modal can't be closed; the answer then opens the case", async () => {
  let answer: (r: Response) => void = () => {};
  drawPage(SITES, {
    path: "/app/sites",
    ui: startableUi,
    routes: { ...reads, "POST /app/cases": () => new Promise<Response>((resolve) => (answer = resolve)) },
  });
  const { user, dialog } = await openStart();
  await fill(user, dialog);
  await user.click(within(dialog).getByRole("button", { name: "Submit" }));
  const close = within(dialog).getByRole<HTMLButtonElement>("button", { name: "Close" });
  await waitFor(() => expect(close.disabled).toBe(true));
  await user.keyboard("{Escape}");
  expect(screen.getByRole("dialog", { name: "New Site" })).toBe(dialog);

  answer(Response.json(CREATED, { status: 201 }));
  await waitFor(() => expect(location()).toBe("/app/cases/MX-0004"));
});

test("the modal keeps focus inside, Escape closes it, and focus goes back to New Site", async () => {
  drawPage(SITES, { path: "/app/sites", ui: startableUi, routes: reads });
  const { user, dialog } = await openStart();
  await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
  const stops = dialog.querySelectorAll("input, textarea, button").length;
  for (let i = 0; i <= stops; i++) {
    await user.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
  }
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "New Site" })).toBeNull());
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "New Site" })));
});

test("full screen below sm, a dialog from sm up", async () => {
  drawPage(SITES, { path: "/app/sites", ui: startableUi, routes: reads });
  const phone = await openStart(); // jsdom's matchMedia matches no width: a phone
  expect(phone.dialog.closest("[data-full-screen]")).not.toBeNull();
  cleanup();

  vi.spyOn(window, "matchMedia").mockImplementation(wide);
  drawPage(SITES, { path: "/app/sites", ui: startableUi, routes: reads });
  const { dialog } = await openStart();
  expect(dialog.closest("[data-full-screen]")).toBeNull();
});

test.each(["light", "dark"] as const)("axe passes in %s with the form open", async (scheme) => {
  drawPage(SITES, { path: "/app/sites", ui: startableUi, routes: reads, scheme });
  await openStart();
  await expectNoAxeViolations();
});
