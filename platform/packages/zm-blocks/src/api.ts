// How blocks reach the platform API (#30): the shell provides its signed-in client around the
// pages it draws, and blocks read data through it with TanStack Query.
import { queryOptions } from "@tanstack/react-query";
import type { ApiClient } from "@zm/api-client";
import { createContext, use } from "react";

export const BlocksApi = createContext<ApiClient | null>(null);

/** The signed-in client; outside the shell it throws, and the block becomes a placeholder. */
export function useBlocksApi(): ApiClient {
  const api = use(BlocksApi);
  if (!api) throw new Error("no API: the shell must wrap its pages in <BlocksApi>");
  return api;
}

/** `GET /app/tasks`, as task_inbox and the shell's nav badges (#94) share it: one request per
 * scope and flow. `scope` is the one answered: below L2, `team` falls back to the user's own. */
export function tasksQuery(api: ApiClient, scope: "mine" | "team", flow?: string) {
  return queryOptions({
    queryKey: ["app", "tasks", scope, flow ?? null],
    queryFn: async () => {
      const ask = (s: "mine" | "team") => api.GET("/app/tasks", { params: { query: { scope: s, flow } } });
      let shown = scope;
      let { data, response } = await ask(scope);
      // team tasks are for level 2 and above (#26), but GET /app/ui sends department pages to
      // every level: below L2 the page shows the user's own work instead of an error
      if (scope === "team" && response.status === 403) ({ data, response } = await ask((shown = "mine")));
      if (!data) throw new Error(`GET /app/tasks answered ${response.status}`);
      return { scope: shown, rows: data };
    },
  });
}
