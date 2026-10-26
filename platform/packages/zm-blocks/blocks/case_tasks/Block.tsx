import { Accordion, Group, Stack, Text, Title } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ZmForm, type FormSchema, type ZmFormProps } from "@zm/forms";
import { DataTable, notifyError, notifyInfo, StatusPill } from "@zm/ui";
import { useSearchParams } from "react-router";
import { useBlocksApi } from "../../src/api";
import { Failed, flowOf, Loading, WithCase, type CaseDetail } from "../../src/cases";
import type { BlockProps } from "../../src/registry";

type Decision = CaseDetail["history"][number];

/** One task's form, inline: submitting closes it and refreshes the case and the inboxes. */
function TaskForm({ id, caseLabel }: { id: string; caseLabel: string }) {
  const api = useBlocksApi();
  const queries = useQueryClient();
  const [, setParams] = useSearchParams();
  const task = useQuery({
    queryKey: ["app", "task", id],
    queryFn: async () => {
      const { data, response } = await api.GET("/app/tasks/{task_id}", { params: { path: { task_id: id } } });
      if (!data) throw new Error(`GET /app/tasks/${id} answered ${response.status}`);
      return data;
    },
  });
  if (task.isPending) return <Loading />;
  if (task.isError) return <Failed message="Couldn't load this task." onRetry={() => void task.refetch()} />;

  const t = task.data;
  const caseData = Object.fromEntries(Object.entries(t.show).map(([key, value]) => [key, { label: t.show_labels[key] ?? key, value }]));
  const submit: ZmFormProps["onSubmit"] = async (values) => {
    const { data, error, response } = await api.POST("/app/tasks/{task_id}/submit", {
      params: { path: { task_id: id }, header: { "Idempotency-Key": crypto.randomUUID() } },
      body: { values },
    });
    if (!data) {
      if (response.status === 422 && error?.errors) return error.errors;
      notifyError(error?.detail ?? "Couldn't submit this task.");
      return;
    }
    notifyInfo("Submitted", `${t.task.name} is done.`);
    setParams(
      (p) => {
        p.delete("task");
        return p;
      },
      { replace: true },
    );
    await Promise.all([
      queries.invalidateQueries({ queryKey: ["app", "case"] }),
      queries.invalidateQueries({ queryKey: ["app", "tasks"] }),
    ]);
  };
  return (
    <ZmForm
      key={id}
      schema={t.form.schema as FormSchema}
      layout={t.form.layout as ZmFormProps["layout"]}
      readOnly={!t.editable}
      caseLabel={caseLabel}
      caseData={caseData}
      onSubmit={submit}
    />
  );
}

/** case_tasks v1 (S1-25, #92): the case's open tasks the user can act on, each opening its form
 * inline (`?task=<id>` opens one directly), and the case's decision history. */
export function CaseTasks({ ui, page }: BlockProps) {
  const [params, setParams] = useSearchParams();
  const open = params.get("task");
  const flow = flowOf(ui, page.flow);
  const stageName = (key: string | null) => flow?.stages.find((s) => s.key === key)?.name ?? key;
  return (
    <WithCase ui={ui} page={page}>
      {(c, label) => {
        // a lead opening a teammate's task from the team inbox (`?task=`) reads it
        const shown = c.tasks.filter((t) => t.can_act || t.id === open);
        return (
          <Stack gap="xl">
            <Stack gap="sm">
              <Title order={2} size="h4">
                Your tasks
              </Title>
              {shown.length === 0 ? (
                <Text size="sm" c="dimmed">
                  Nothing for you to do on this {label.toLowerCase()}
                </Text>
              ) : (
                <Accordion
                  variant="separated"
                  value={open}
                  onChange={(id) =>
                    setParams(
                      (p) => {
                        if (id) p.set("task", id);
                        else p.delete("task");
                        return p;
                      },
                      { replace: true },
                    )
                  }
                >
                  {shown.map((t) => (
                    <Accordion.Item key={t.id} value={t.id}>
                      <Accordion.Control>
                        <Group gap="sm">
                          <Text>{t.name}</Text>
                          {t.stage && <StatusPill tone="open">{stageName(t.stage)}</StatusPill>}
                          {!t.can_act && <StatusPill tone="queued">View only</StatusPill>}
                        </Group>
                      </Accordion.Control>
                      <Accordion.Panel>{open === t.id && <TaskForm id={t.id} caseLabel={label} />}</Accordion.Panel>
                    </Accordion.Item>
                  ))}
                </Accordion>
              )}
            </Stack>
            <DataTable<Decision>
              caption="Decision history"
              rows={c.history}
              rowKey={(d) => `${d.task}:${d.at}`}
              emptyText="No decisions yet"
              columns={[
                { key: "task", header: "Task", render: (d) => d.task_name },
                { key: "by", header: "Who", render: (d) => d.by_name ?? "—" },
                { key: "outcome", header: "Outcome", render: (d) => d.outcome ?? "—" },
                { key: "comment", header: "Comment", render: (d) => d.comment ?? "" },
                { key: "at", header: "When", render: (d) => new Date(d.at).toLocaleString(), sortValue: (d) => d.at },
              ]}
            />
          </Stack>
        );
      }}
    </WithCase>
  );
}
