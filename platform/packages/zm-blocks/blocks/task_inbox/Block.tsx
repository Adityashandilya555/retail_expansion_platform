import { Code, Group, Stack, Title, VisuallyHidden } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import type { components } from "@zm/api-client";
import { DataTable, StatusPill, type Column } from "@zm/ui";
import { useId, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { useBlocksApi } from "../../src/api";
import type { BlockProps } from "../../src/registry";

type TaskItem = components["schemas"]["TaskItem"];
type ColumnKey = "case" | "task" | "stage" | "created" | "assignee";
/** `props` as manifest.json allows them (the renderer has already validated them). */
type Props = { scope?: "mine" | "team"; flow?: string; group_by?: "case" | "none"; columns?: ColumnKey[] };

/** task_inbox v1 (S1-14, #30): open tasks by case; a row opens the task on its case page (D19). */
export function TaskInbox({ props, ui }: BlockProps) {
  const { scope = "mine", flow, group_by = "case", columns } = props as Props;
  const api = useBlocksApi();
  const navigate = useNavigate();
  const headingId = useId();
  const tasks = useQuery({
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

  // ponytail: no case title in the header until GET /app/tasks sends one (#91 defines it)
  const stageName = (t: TaskItem) =>
    ui.flows.find((f) => f.key === t.flow)?.stages.find((s) => s.key === t.stage)?.name ?? t.stage;
  const all: Record<ColumnKey, Column<TaskItem>> = {
    case: { key: "case", header: "Case", render: (t) => <Code>{t.case_code}</Code>, sortValue: (t) => t.case_code },
    task: { key: "task", header: "Task", render: (t) => t.name, sortValue: (t) => t.name },
    stage: { key: "stage", header: "Stage", render: (t) => stageName(t) ?? "—" },
    created: { key: "created", header: "Created", render: (t) => new Date(t.created).toLocaleDateString(), sortValue: (t) => t.created },
    assignee: { key: "assignee", header: "Assignee", render: (t) => (t.assignee ? "Assigned" : "Unassigned") },
  };
  const grouped = group_by === "case";
  const shown = (columns ?? (grouped ? ["task", "stage", "created"] : ["case", "task", "stage", "created"])).map((k) => all[k]);
  const title = (tasks.data?.scope ?? scope) === "team" ? "Team queue" : "My work";
  const rows = tasks.data?.rows ?? [];

  const table = (caption: ReactNode, items: TaskItem[], key?: string) => (
    <DataTable
      key={key}
      caption={caption}
      columns={shown}
      rows={items}
      rowKey={(t) => t.id}
      onRowClick={(t) => navigate(`/app/cases/${encodeURIComponent(t.case_code)}?tab=tasks&task=${encodeURIComponent(t.id)}`)}
      loading={tasks.isPending}
      error={tasks.isError ? "Couldn't load your tasks." : null}
      onRetry={() => void tasks.refetch()}
      emptyText="Nothing waiting for you"
    />
  );

  const byCase = new Map<string, TaskItem[]>();
  for (const t of rows) byCase.set(t.case_code, [...(byCase.get(t.case_code) ?? []), t]);

  return (
    <Stack component="section" aria-labelledby={headingId} gap="sm">
      <Title order={2} size="h3" id={headingId}>
        {title}
      </Title>
      {grouped && rows.length > 0
        ? [...byCase].map(([code, items]) => {
            const stages = [...new Set(items.map(stageName))].filter((s): s is string => s !== null);
            const header = (
              <Group gap="xs">
                <Code>{code}</Code>
                {stages.map((s) => (
                  <StatusPill key={s} tone="open">
                    {s}
                  </StatusPill>
                ))}
              </Group>
            );
            return table(header, items, code);
          })
        : table(<VisuallyHidden>{title}</VisuallyHidden>, rows)}
    </Stack>
  );
}
