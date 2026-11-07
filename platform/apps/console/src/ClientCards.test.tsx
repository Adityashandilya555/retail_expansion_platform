import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { components } from "@zm/api-client";
import { expectNoAxeViolations, renderWithZm } from "@zm/ui/test";
import { afterEach, expect, test, vi } from "vitest";
import { ClientCards } from "./ClientCards";

type ClientOut = components["schemas"]["ClientOut"];
type ClientOverview = components["schemas"]["ClientOverview"];

const AT = "2026-11-02T09:30:00Z";
const ids = { matrix: "0c9f7a52-3b1e-4c8d-9f6a-2e5b7d1c4a90", qsr: "5e1d2c3b-4a59-4687-9a0b-1c2d3e4f5a6b", newco: "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d" };
const client = (id: string, name: string, status: ClientOut["status"] = "ready"): ClientOut => ({
  id,
  slug: name.toLowerCase().replace(/ /g, "-"),
  name,
  workspace_code: name.toUpperCase().replace(/ /g, "-"),
  template_key: "matrix-retail",
  status,
  created_at: AT,
  last_release: null,
});
const CLIENTS = [client(ids.matrix, "Matrix Retail"), client(ids.qsr, "QSR"), client(ids.newco, "New Co", "provisioning")];

const up = (ms: number) => ({ up: true, ms });
const down = { up: false, ms: null };
const OVERVIEW: ClientOverview[] = [
  {
    id: ids.matrix,
    slug: "matrix-retail",
    status: "ready",
    users: { active: 5, invited: 1, deactivated: 1 },
    cases_in_progress: [
      { flow: "store_launch", count: 4 },
      { flow: "refit", count: 0 },
    ],
    last_release: { version: 3, published_at: AT },
    health: { operaton: up(12.5), db: up(3.1) },
  },
  {
    id: ids.qsr,
    slug: "qsr",
    status: "ready",
    users: { active: 2, invited: 0, deactivated: 0 },
    cases_in_progress: null,
    last_release: null,
    health: { operaton: down, db: up(2) },
  },
  { id: ids.newco, slug: "new-co", status: "provisioning", users: null, cases_in_progress: null, last_release: null, health: null },
];

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function draw(answers: (() => Response)[], scheme?: "light" | "dark") {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (r: Request) => {
      calls.push(`${r.method} ${new URL(r.url).pathname}`);
      return (answers.shift() ?? (() => json(OVERVIEW)))();
    }),
  );
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderWithZm(
    <QueryClientProvider client={queries}>
      <ClientCards clients={CLIENTS} />
    </QueryClientProvider>,
    { scheme },
  );
  return calls;
}

/** The dd under a card's `term`. */
const value = (card: HTMLElement, term: string) => within(card).getByText(term).nextElementSibling?.textContent;

afterEach(() => vi.unstubAllGlobals());

test("Matrix Retail's card shows 5 / 1 / 1, its cases per flow, v3 and both up", async () => {
  const calls = draw([]);
  const card = await screen.findByRole("region", { name: "Matrix Retail" });
  expect(calls).toEqual(["GET /owner/clients/overview"]);
  expect(within(card).getByRole("heading", { level: 2 }).textContent).toBe("Matrix Retail");
  expect(within(card).getByText("Ready")).toBeDefined();
  expect(value(card, "Users")).toBe("5 active · 1 invited · 1 deactivated");
  expect(value(card, "Cases in progress")).toBe("store_launch 4 · refit 0");
  expect(value(card, "Last release")).toBe(`v3 · ${new Date(AT).toLocaleString()}`);
  expect(within(card).getByText("Operaton up · 12.5 ms")).toBeDefined();
  expect(within(card).getByText("Database up · 3.1 ms")).toBeDefined();
});

test("QSR's Operaton down reads red with its label; its database and counts still show", async () => {
  draw([]);
  const card = await screen.findByRole("region", { name: "QSR" });
  const red = within(card).getByText("Operaton down").closest(".mantine-Badge-root");
  const green = within(card).getByText("Database up · 2 ms").closest(".mantine-Badge-root");
  expect(red?.getAttribute("style")).toContain("red");
  expect(green?.getAttribute("style")).toContain("green");
  expect(value(card, "Users")).toBe("2 active · 0 invited · 0 deactivated");
  expect(value(card, "Cases in progress")).toBe("—");
  expect(value(card, "Last release")).toBe("Nothing published yet");
  // the other client's card is unaffected
  expect(within(screen.getByRole("region", { name: "Matrix Retail" })).getByText("Operaton up · 12.5 ms")).toBeDefined();
});

test("a client still provisioning shows its status and no counts", async () => {
  draw([]);
  const card = await screen.findByRole("region", { name: "New Co" });
  expect(within(card).getByText("Provisioning")).toBeDefined();
  expect(within(card).getByText("Counts and health show once it's ready.")).toBeDefined();
  expect(within(card).queryByText("Users")).toBeNull();
});

test("an overview that fails to load offers Retry", async () => {
  const calls = draw([() => json({ detail: "x" }, 502)]);
  await userEvent.setup().click(await screen.findByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("region", { name: "Matrix Retail" })).toBeDefined();
  expect(calls).toEqual(["GET /owner/clients/overview", "GET /owner/clients/overview"]);
});

test.each(["light", "dark"] as const)("axe passes in %s", async (scheme) => {
  draw([], scheme);
  await screen.findByRole("region", { name: "New Co" });
  await expectNoAxeViolations();
});
