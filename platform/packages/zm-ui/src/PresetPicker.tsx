import { Button, Group, Paper, Radio, SimpleGrid, Stack, Text, useComputedColorScheme } from "@mantine/core";
import { PRESETS, presetCssVariables, type Accent, type Preset } from "./theme";

/** A preset drawn live, not a screenshot: Mantine components under that preset's own CSS variables. */
function Preview({ preset, accent }: { preset: Preset; accent: Accent }) {
  const scheme = useComputedColorScheme("light");
  return (
    <Paper withBorder p="sm" mt="sm" style={presetCssVariables(preset, accent, scheme)} aria-hidden>
      <Text ff="heading" fw="var(--mantine-heading-font-weight)">
        Indiranagar
      </Text>
      <Text size="xs" c="dimmed">
        Legal · due in 3 days
      </Text>
      <Group gap="xs" mt="xs">
        <Button component="span" size="compact-xs">
          Approve
        </Button>
        <Button component="span" size="compact-xs" variant="default">
          Send back
        </Button>
      </Group>
    </Paper>
  );
}

/** One card per preset, each a live preview in `accent` (or the preset's own default): the owner
 * console's preset picker and /dev/ui's switcher. */
export function PresetPicker({
  label,
  value,
  onChange,
  accent,
}: {
  label: string;
  value: string;
  onChange: (key: string) => void;
  accent?: Accent | null;
}) {
  return (
    <Radio.Group label={label} value={value} onChange={onChange}>
      <SimpleGrid cols={{ base: 1, sm: 2 }} mt="xs">
        {Object.values(PRESETS).map((preset) => (
          <Radio.Card key={preset.key} value={preset.key} p="md">
            <Group wrap="nowrap" align="flex-start" gap="sm">
              <Radio.Indicator />
              <Stack gap={2}>
                <Text fw={600}>{preset.label}</Text>
                <Text size="sm" c="dimmed">
                  {preset.description}
                </Text>
              </Stack>
            </Group>
            <Preview preset={preset} accent={accent ?? preset.defaultAccent} />
          </Radio.Card>
        ))}
      </SimpleGrid>
    </Radio.Group>
  );
}
