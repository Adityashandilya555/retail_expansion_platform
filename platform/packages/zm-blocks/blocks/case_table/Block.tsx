import { Chip, Code, Group, Stack, Text, TextInput } from "@mantine/core";
import { DataTable, Num, StageTracker, StatusPill, type Column } from "@zm/ui";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { currentStage, daysSince, display, flowOf, inTile, isOverdue, STATUS, tileOf, trackerStages, useCases, type CaseRow } from "../../src/cases";
import type { BlockProps } from "../../src/registry";

/** `props` as manifest.json allows them (the renderer has already validated them). */
type Props = { flow?: string; columns?: string[]; stages?: string[] };

/** Whole days since the case's current stage started. */
function daysIn(c: CaseRow): number | null {
  const started = currentStage(c.stages)?.started_at;
  return started ? daysSince(started) : null;
}

/** case_table v1 (S1-25, #92): a flow's cases, e.g. the Sites list. A row opens the case page.
 * A kpi_strip tile picked in the URL (`?kpi=`, #94) shows the cases it counts instead of the stage
 * filters, so the table and the tile agree. */
export function CaseTable({ props, ui }: BlockProps) {
  const { flow: flowKey, columns = ["code", "title", "stage", "status", "open_tasks"], stages: only } = props as Props;
  const flow = flowOf(ui, flowKey);
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const cases = useCases(flow?.key);
  if (!flow) throw new Error("the release has no flow");

  const label = flow.case_label;
  const plural = `${label.toLowerCase()}s`;
  const choices = flow.stages.filter((s) => !only || only.includes(s.key));
  const picked = params.getAll("stage");
  const tile = tileOf(params.get("kpi"));
  const term = search.trim().toLowerCase();
  const rows = (cases.data?.rows ?? []).filter((c) => {
    const at = currentStage(c.stages)?.key ?? "";
    return (
      (tile ? inTile(tile, c, flow) : (!only || only.includes(at)) && (picked.length === 0 || picked.includes(at))) &&
      (!term || c.code.toLowerCase().includes(term) || c.title.toLowerCase().includes(term))
    );
  });

  const order = (c: CaseRow) => flow.stages.findIndex((s) => s.key === currentStage(c.stages)?.key);
  const known: Record<string, Column<CaseRow>> = {
    code: { key: "code", header: "Code", render: (c) => <Code>{c.code}</Code>, sortValue: (c) => c.code },
    title: { key: "title", header: label, render: (c) => c.title, sortValue: (c) => c.title },
    stage: {
      key: "stage",
      header: "Stage",
      render: (c) => (
        <Group gap="xs" wrap="nowrap">
          <Text size="sm">{currentStage(c.stages)?.name ?? "—"}</Text>
          <StageTracker compact stages={trackerStages(c.stages)} label={`${c.code} stages`} />
        </Group>
      ),
      sortValue: order,
    },
    status: {
      key: "status",
      header: "Status",
      render: (c) => (
        <Group gap="xs" wrap="nowrap">
          <StatusPill tone={STATUS[c.status][0]}>{STATUS[c.status][1]}</StatusPill>
          {isOverdue(c, flow) && <StatusPill tone="warning">Overdue</StatusPill>}
        </Group>
      ),
      sortValue: (c) => c.status,
    },
    age: { key: "age", header: "Days in stage", align: "right", render: (c) => <Num value={daysIn(c)} />, sortValue: (c) => daysIn(c) ?? -1 },
    open_tasks: { key: "open_tasks", header: "Open tasks", align: "right", render: (c) => <Num value={c.open_tasks} />, sortValue: (c) => c.open_tasks },
    my_tasks: { key: "my_tasks", header: "My tasks", align: "right", render: (c) => <Num value={c.my_tasks} />, sortValue: (c) => c.my_tasks },
  };
  // any other column is one of the flow's start fields
  const shown = columns.map(
    (key): Column<CaseRow> =>
      known[key] ?? {
        key,
        header: flow.start_fields.find((f) => f.key === key)?.label ?? key,
        render: (c) => display(c.fields[key]),
        sortValue: (c) => display(c.fields[key]),
      },
  );

  return (
    <Stack gap="sm">
      <Group justify="space-between" align="flex-end" gap="sm">
        {choices.length > 1 ? (
          <Chip.Group
            multiple
            value={picked}
            onChange={(keys) =>
              setParams(
                (p) => {
                  p.delete("stage");
                  p.delete("kpi"); // the chips take over from a picked tile
                  for (const key of keys) p.append("stage", key);
                  return p;
                },
                { replace: true },
              )
            }
          >
            <Group gap="xs">
              {choices.map((s) => (
                <Chip key={s.key} value={s.key} size="sm">
                  {s.name}
                </Chip>
              ))}
            </Group>
          </Chip.Group>
        ) : (
          <span />
        )}
        <TextInput
          type="search"
          aria-label={`Search ${plural}`}
          placeholder="Search by code or name"
          value={search}
          onChange={(e) => setSearch(e.currentTarget.value)}
          w={240}
        />
      </Group>
      <DataTable
        caption={`${label}s`}
        columns={shown}
        rows={rows}
        rowKey={(c) => c.code}
        onRowClick={(c) => navigate(`/app/cases/${encodeURIComponent(c.code)}`)}
        loading={cases.isPending}
        error={cases.isError ? `Couldn't load the ${plural}.` : null}
        onRetry={() => void cases.refetch()}
        emptyText={`No ${plural} yet`}
      />
      {cases.data?.truncated && (
        <Text size="xs" c="dimmed">
          {`Showing the newest ${cases.data.rows.length} ${plural}.`}
        </Text>
      )}
    </Stack>
  );
}
