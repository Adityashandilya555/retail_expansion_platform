import { SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { display, WithCase } from "../../src/cases";
import type { BlockProps } from "../../src/registry";

/** `props` as manifest.json allows them (the renderer has already validated them). */
type Props = { sections?: string[] };

/** case_fields v1 (S1-25, #92): the start-form values, read-only, as the API masked them by level. */
export function CaseFields({ props, ui, page }: BlockProps) {
  const { sections } = props as Props;
  return (
    <WithCase ui={ui} page={page}>
      {(c) => {
        const shown = c.sections.filter((s) => s.fields.length > 0 && (!sections || sections.includes(s.key)));
        if (shown.length === 0)
          return (
            <Text size="sm" c="dimmed">
              No details yet
            </Text>
          );
        return (
          <Stack gap="lg">
            {shown.map((s) => (
              <Stack key={s.key} gap="xs" component="section" aria-label={s.title}>
                <Title order={2} size="h5">
                  {s.title}
                </Title>
                <SimpleGrid component="dl" cols={{ base: 1, sm: 2 }} spacing="sm" m={0}>
                  {s.fields.map((f) => (
                    <div key={f.key}>
                      <Text component="dt" size="sm" c="dimmed">
                        {f.label}
                      </Text>
                      <Text component="dd" m={0}>
                        {display(f.value)}
                      </Text>
                    </div>
                  ))}
                </SimpleGrid>
              </Stack>
            ))}
          </Stack>
        );
      }}
    </WithCase>
  );
}
