// What the case blocks share (#92): one `GET /app/cases/{code}` per page (the code comes from the
// route), its loading and error states, and how stages, statuses and values read; one
// `GET /app/cases` per flow, and which cases a KPI tile counts (#94).
import { Alert, Button, Group, Loader, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import type { components } from "@zm/api-client";
import { TONE_COLORS, type Stage, type StatusTone } from "@zm/ui";
import type { ReactNode } from "react";
import { useParams } from "react-router";
import { useBlocksApi } from "./api";
import type { AppUi, UiPage } from "./registry";

export type CaseRow = components["schemas"]["CaseRow"];
export type CaseDetail = components["schemas"]["CaseDetail"];
type StageOut = components["schemas"]["StageOut"];
type Flow = AppUi["flows"][number];

/** The page's flow (or the release's first), and what it calls a case, e.g. "Site". */
export function flowOf(ui: AppUi, key: string | null | undefined): Flow | undefined {
  return ui.flows.find((f) => f.key === key) ?? ui.flows[0];
}

export const STATUS: Record<CaseRow["status"], [StatusTone, string]> = {
  open: ["open", "Open"],
  completed: ["done", "Completed"],
  closed: ["closed", "Closed"],
};

export const trackerStages = (stages: StageOut[]): Stage[] => stages.map((s) => ({ key: s.key, label: s.name, state: s.state }));

/** The stage a case is in: its open one, else the last one that ran. */
export const currentStage = (stages: StageOut[]): StageOut | undefined =>
  stages.find((s) => s.state === "open") ?? stages.findLast((s) => s.state !== "queued");

/** A value as text: Yes/No, a file's name, an em dash when empty. */
export function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.map(display).join(", ");
  if (typeof value === "object") return "name" in value ? String(value.name) : JSON.stringify(value);
  return String(value);
}

/** A flow's cases (`GET /app/cases`): case_table and kpi_strip on one page share this request.
 * `truncated`: more matched than the newest 500 the API sends, so counts over `rows` are minimums. */
export function useCases(flow: string | undefined) {
  const api = useBlocksApi();
  return useQuery({
    queryKey: ["app", "cases", flow ?? null],
    queryFn: async () => {
      const { data, response } = await api.GET("/app/cases", { params: { query: { flow } } });
      if (!data) throw new Error(`GET /app/cases answered ${response.status}`);
      return { rows: data, truncated: response.headers.get("X-Truncated") === "true" };
    },
  });
}

/** Whole days since `at`. */
export const daysSince = (at: string) => Math.floor((Date.now() - Date.parse(at)) / 86_400_000);

/** The open stage the case has run longer than its `sla_days` (S2-29), checking every open stage
 * (parallel ones too) or only `stage` when given; the first such in flow order. The kpi_strip
 * `overdue` tiles and case_table's Overdue pill both read this. */
export function overdueStage(c: CaseRow, flow: Flow, stage?: string): StageOut | undefined {
  return c.stages.find((s) => {
    const sla = flow.stages.find((f) => f.key === s.key)?.sla_days;
    return s.state === "open" && (!stage || s.key === stage) && !!sla && !!s.started_at && daysSince(s.started_at) > sla;
  });
}

/** The stage case_table's "Days in stage" counts: the overdue one if any, else the current one. */
export const ageStage = (c: CaseRow, flow: Flow): StageOut | undefined => overdueStage(c, flow) ?? currentStage(c.stages);

/** A case_table's `stages` scope: cases whose current stage is one of them (all without one). */
export const inTableScope = (c: CaseRow, stages: readonly string[] | undefined): boolean =>
  !stages || stages.includes(currentStage(c.stages)?.key ?? "");

/** The `stages` of the page's case_table over `flow` (the table a picked tile filters), so a tile
 * counts exactly the rows the table shows once picked. */
export function tableStages(page: UiPage, ui: AppUi, flow: Flow): string[] | undefined {
  for (const placed of Object.values(page.regions ?? {})) {
    for (const ref of placed) {
      const props = (ref.props ?? {}) as { flow?: string; stages?: string[] };
      if (ref.block === "case_table" && flowOf(ui, props.flow)?.key === flow.key) return props.stages;
    }
  }
  return undefined;
}

export const TILE_KINDS = ["open_cases", "in_stage", "awaiting_me", "overdue", "done"] as const;
/** A kpi_strip tile (#94), as its manifest allows it. */
export type Tile = { kind: (typeof TILE_KINDS)[number]; stage?: string; label?: string };

/** A tile in the URL: `?kpi=<kind>` or `?kpi=<kind>.<stage>` (stage keys have no dots). */
export const tileKey = (t: Tile) => (t.stage ? `${t.kind}.${t.stage}` : t.kind);
export function tileOf(key: string | null): Tile | null {
  const [name, stage] = (key ?? "").split(".");
  const kind = TILE_KINDS.find((k) => k === name);
  return kind ? { kind, stage } : null;
}

/** Whether a tile counts the case; case_table shows exactly these cases while the tile is picked.
 * `stage` narrows a tile to cases in that stage; for `done`, to cases past it. */
export function inTile(t: Tile, c: CaseRow, flow: Flow): boolean {
  const at = (state: StageOut["state"]) => c.stages.some((s) => s.key === t.stage && s.state === state);
  switch (t.kind) {
    case "open_cases":
    case "in_stage":
      return t.stage ? at("open") : c.status === "open";
    case "awaiting_me":
      return c.my_tasks > 0 && (!t.stage || at("open"));
    case "overdue":
      return !!overdueStage(c, flow, t.stage);
    case "done":
      return t.stage ? at("done") : c.status === "completed";
  }
}

/** `/app/cases/:code`'s case: every case block on the page shares this one request. */
export function useCase() {
  const { code = "" } = useParams();
  const api = useBlocksApi();
  return useQuery({
    queryKey: ["app", "case", code],
    queryFn: async () => {
      const { data, response } = await api.GET("/app/cases/{code}", { params: { path: { code } } });
      if (!data) throw new Error(`GET /app/cases/${code} answered ${response.status}`);
      return data;
    },
  });
}

/** An inline error with Retry, as `DataTable` shows one. */
export function Failed({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Alert color={TONE_COLORS.error} variant="light" role="alert">
      <Group justify="space-between">
        <Text size="sm">{message}</Text>
        <Button size="xs" variant="light" color={TONE_COLORS.error} onClick={onRetry}>
          Retry
        </Button>
      </Group>
    </Alert>
  );
}

export function Loading() {
  return (
    <Group role="status" gap="xs">
      <Loader size="xs" aria-hidden />
      <Text size="sm">Loading…</Text>
    </Group>
  );
}

/** The case once loaded (with what the flow calls a case), or its loading and error states. */
export function WithCase({ ui, page, children }: { ui: AppUi; page: UiPage; children: (c: CaseDetail, label: string) => ReactNode }) {
  const found = useCase();
  const label = flowOf(ui, page.flow)?.case_label ?? "Case";
  if (found.isPending) return <Loading />;
  if (found.isError) return <Failed message={`Couldn't load this ${label.toLowerCase()}.`} onRetry={() => void found.refetch()} />;
  return children(found.data, label);
}
