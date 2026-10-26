// Typed client for the platform API (S1-05, #21). src/schema.d.ts is generated from openapi.json:
// after an API change run `uv run zm-api openapi` then `pnpm --filter @zm/api-client gen`.
import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export type { components, paths } from "./schema";

/** Every error response (RFC 9457): the `default` response of each route, from zm_api.errors. */
export type Problem = components["schemas"]["Problem"];
/** A 422's per-field error, by JSON Pointer (the shape @zm/forms shows under fields). */
export type FieldError = components["schemas"]["FieldError"];

/** `baseUrl` is `import.meta.env.VITE_API_BASE_URL`; `getToken` returns the in-memory access token (#22).
 * Requests include credentials so the refresh cookie (httpOnly, scoped to the auth paths) is sent. */
export function createApiClient(baseUrl: string, getToken?: () => string | null | undefined) {
  // fetch looked up per request (openapi-fetch would capture it once), so a stub or polyfill set
  // after this module loads is used
  const client = createClient<paths>({ baseUrl, credentials: "include", fetch: (request) => globalThis.fetch(request) });
  client.use({
    onRequest({ request }) {
      const token = getToken?.();
      if (token) request.headers.set("Authorization", `Bearer ${token}`);
      return request;
    },
  });
  return client;
}

export type ApiClient = ReturnType<typeof createApiClient>;
