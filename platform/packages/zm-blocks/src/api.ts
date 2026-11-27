// How blocks reach the platform API (#30): the shell provides its signed-in client around the
// pages it draws, and blocks read data through it with TanStack Query.
import { queryOptions } from "@tanstack/react-query";
import type { ApiClient, Problem } from "@zm/api-client";
import { FileApiError, type FileApi } from "@zm/forms";
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

/** How a form's document slots reach the API (#51): uploads for one task or one flow's start form, and the case's versions once
 * there is a case. Every failure rejects with the API's status and message. */
export function fileApi(api: ApiClient, slot: { task_id: string } | { flow: string }, caseCode?: string): FileApi {
  const fail = (response: Response, error?: Problem) => new FileApiError(response.status, error?.detail ?? "Something went wrong; try again");
  return {
    async upload(field, file) {
      const { data, error, response } = await api.POST("/app/files/upload", { body: { ...slot, field, name: file.name, size_bytes: file.size } });
      if (!data) throw fail(response, error);
      return data;
    },
    async confirm(fileId) {
      const { data, error, response } = await api.POST("/app/files/{file_id}/confirm", { params: { path: { file_id: fileId } } });
      if (!data) throw fail(response, error);
      return data;
    },
    async url(fileId, attachment) {
      const { data, error, response } = await api.GET("/app/files/{file_id}/download", { params: { path: { file_id: fileId }, query: { attachment } } });
      if (!data) throw fail(response, error);
      return data.url;
    },
    ...(caseCode && {
      async versions(field: string) {
        const { data, error, response } = await api.GET("/app/cases/{code}/files", { params: { path: { code: caseCode }, query: { field } } });
        if (!data) throw fail(response, error);
        return data;
      },
    }),
  };
}
