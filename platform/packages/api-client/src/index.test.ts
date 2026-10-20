import { afterEach, expect, expectTypeOf, test, vi } from "vitest";
import { createApiClient, type Problem } from "./index";

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Stubs fetch with a fresh response per call; returns the requests it saw. */
function stubFetch(respond: () => Response): Request[] {
  const seen: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      seen.push(request);
      return respond();
    }),
  );
  return seen;
}

test("calls the API base URL with the current token", async () => {
  const seen = stubFetch(() => Response.json({ ok: true }));
  let token: string | null = "t1";
  const api = createApiClient("https://api.example.com", () => token);

  const { data } = await api.GET("/healthz");
  expect(data).toEqual({ ok: true });
  token = null;
  await api.GET("/healthz");

  const [first, second] = seen;
  expect(first?.url).toBe("https://api.example.com/healthz");
  expect(first?.headers.get("authorization")).toBe("Bearer t1");
  expect(second?.headers.has("authorization")).toBe(false);
});

test("errors come back as problem+json, typed from the generated Problem", async () => {
  const problem: Problem = { type: "about:blank", title: "Bad Gateway", status: 502, detail: "x", request_id: "r1", errors: null };
  stubFetch(() => new Response(JSON.stringify(problem), { status: 502, headers: { "content-type": "application/problem+json" } }));
  const { error, response } = await createApiClient("https://api.example.com").GET("/healthz");
  expect(response.status).toBe(502);
  expect(error).toEqual(problem);
  // checked by tsc: the generated `default` response types the error as exactly Problem
  expectTypeOf(error).toEqualTypeOf<Problem | undefined>();
});
