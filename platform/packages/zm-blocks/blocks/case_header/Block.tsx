import { Code, Group, Stack, Text, Title } from "@mantine/core";
import { StageTracker, StatusPill } from "@zm/ui";
import { display, flowOf, STATUS, trackerStages, WithCase } from "../../src/cases";
import type { BlockProps } from "../../src/registry";

/** `props` as manifest.json allows them (the renderer has already validated them). */
type Props = { title_field?: string; fields?: string[] };

/** case_header v1 (S1-25, #92): code, title, status, header fields and the full stage tracker. */
export function CaseHeader({ props, ui, page }: BlockProps) {
  const { title_field, fields = [] } = props as Props;
  const flow = flowOf(ui, page.flow);
  return (
    <WithCase ui={ui} page={page}>
      {(c, label) => {
        const named = title_field ? c.fields[title_field] : undefined;
        const [tone, status] = STATUS[c.status];
        return (
          <Stack gap="sm">
            <Group gap="xs">
              <Code>{c.code}</Code>
              <StatusPill tone={tone}>{status}</StatusPill>
            </Group>
            <Title order={1}>{typeof named === "string" && named ? named : c.title}</Title>
            {fields.length > 0 && (
              <Group component="dl" gap="lg" m={0}>
                {fields.map((key) => (
                  <Group key={key} gap={6} wrap="nowrap">
                    <Text component="dt" size="sm" c="dimmed">
                      {flow?.start_fields.find((f) => f.key === key)?.label ?? key}
                    </Text>
                    <Text component="dd" size="sm" m={0}>
                      {display(c.fields[key])}
                    </Text>
                  </Group>
                ))}
              </Group>
            )}
            <StageTracker stages={trackerStages(c.stages)} label={`${label} stages`} />
          </Stack>
        );
      }}
    </WithCase>
  );
}
