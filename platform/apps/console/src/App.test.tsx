import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { ConsoleApp } from "./App";
import { forget, refresh, signedIn, signOut } from "./session";

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const problem = (status: number) =>
  json({ type: "about:blank", title: "x", status, detail: "x", request_id: "r", errors: null }, status);
const OWNER = { id: "6f0c1b9e-5d2a-4c1e-9a7b-3d8e2f1a0b4c", email: "adi@zm.demo", name: "Adi" };

function api(routes: Record<string, (r: Request) => Response | Promise<Response>>): string[] {
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

test("an owner signs in with the keyboard only", async () => {
  const calls = api({
    "POST /owner/auth/refresh": () => problem(401),
    "POST /owner/auth/login": () => json({ access_token: "o1", token_type: "bearer", expires_in: 900 }),
    "GET /owner/me": (r) => (r.headers.get("authorization") === "Bearer o1" ? json(OWNER) : problem(401)),
  });
  renderAt("/");
  expect(await screen.findByRole("heading", { level: 1, name: "Owner console" })).toBeDefined();
  const user = userEvent.setup();
  expect(document.activeElement).toBe(screen.getByLabelText(/Email/));
  await user.keyboard("adi@zm.demo");
  await user.tab();
  await user.keyboard("long-password{Enter}");
  expect(await screen.findByRole("heading", { name: "Signed in as Adi" })).toBeDefined();
  expect(calls).toEqual(["POST /owner/auth/refresh", "POST /owner/auth/login", "GET /owner/me"]);
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
  api({
    "POST /owner/auth/refresh": () => json({ access_token: "o1", token_type: "bearer", expires_in: 900 }),
    "GET /owner/me": () => json(OWNER),
  });
  renderAt("/");
  expect(await screen.findByRole("heading", { name: "Signed in as Adi" })).toBeDefined();
});

test("a refresh that lands after sign-out doesn't sign the owner back in (Codex, #108)", async () => {
  let land: (r: Response) => void = () => {};
  api({
    "POST /owner/auth/refresh": () => new Promise<Response>((resolve) => (land = resolve)),
    "POST /owner/auth/logout": () => new Response(null, { status: 204 }),
  });
  const late = refresh();
  await signOut();
  land(json({ access_token: "o1", token_type: "bearer", expires_in: 900 }));
  expect(await late).toBe(false);
  expect(signedIn()).toBe(false);
});
