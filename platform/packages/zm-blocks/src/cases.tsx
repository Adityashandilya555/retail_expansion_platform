// What the case blocks share (#92): one `GET /app/cases/{code}` per page (the code comes from the
// route), its loading and error states, and how stages, statuses and values read.
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
