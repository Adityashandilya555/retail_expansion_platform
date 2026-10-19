// Typed client for the platform API (S1-05, #21). src/schema.d.ts is generated from openapi.json:
// after an API change run `uv run zm-api openapi` then `pnpm --filter @zm/api-client gen`.
import createClient from "openapi-fetch";
import type { paths } from "./schema";

export type { components, paths } from "./schema";

/** Every error response (RFC 9457, apps/api zm_api/errors.py); 422s add field errors by JSON Pointer. */
export type Problem = {
  type: string;
  title: string;
  status: number;
  detail?: string | null;
  request_id: string | null;
  errors?: { path: string; message: string }[];
};

/** `baseUrl` is `import.meta.env.VITE_API_BASE_URL`; `getToken` returns the in-memory access token (#22). */
export function createApiClient(baseUrl: string, getToken?: () => string | null | undefined) {
  const client = createClient<paths>({ baseUrl });
  client.use({
    onRequest({ request }) {
      const token = getToken?.();
      if (token) request.headers.set("Authorization", `Bearer ${token}`);
      return request;
    },
  });
  return client;
}
