import { Text, Timeline } from "@mantine/core";
import { flowOf, WithCase, type CaseDetail } from "../../src/cases";
import type { BlockProps } from "../../src/registry";

type CaseEvent = CaseDetail["events"][number];
const ENDS = { stage_started: "started", stage_done: "done", stage_closed: "closed" } as const;

/** case_activity v1 (S2-29, #94): the case's events from `GET /app/cases/{code}`, oldest first:
 * what, who and when; the API sends no values. */
export function CaseActivity({ ui, page }: BlockProps) {
  const flow = flowOf(ui, page.flow);
  const stageName = (key: string | null | undefined) => flow?.stages.find((s) => s.key === key)?.name ?? key;
  const title = (e: CaseEvent, label: string) =>
    e.kind === "created" ? `${label} created` : e.kind === "submitted" ? `${e.task_name ?? e.task} submitted` : `${stageName(e.stage)} ${ENDS[e.kind]}`;
  return (
    <WithCase ui={ui} page={page}>
      {(c, label) => (
        <Timeline role="list" aria-label="Activity" bulletSize={12} lineWidth={2}>
          {c.events.map((e, i) => (
            <Timeline.Item key={i} role="listitem" title={title(e, label)}>
              <Text size="sm" c="dimmed">
                {e.by_name && `${e.by_name} · `}
                <time dateTime={e.at}>{new Date(e.at).toLocaleString()}</time>
              </Text>
            </Timeline.Item>
          ))}
        </Timeline>
      )}
    </WithCase>
  );
}
