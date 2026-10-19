import {
  ActionIcon,
  CheckIcon,
  ColorSwatch,
  Group,
  Popover,
  SegmentedControl,
  Stack,
  Text,
  useMantineColorScheme,
  useMantineTheme,
  type MantineColorScheme,
} from "@mantine/core";
import { Palette } from "lucide-react";
import { ACCENTS } from "./theme";
import { useAppearance } from "./ZmProvider";

const SCHEMES: { label: string; value: MantineColorScheme }[] = [
  { label: "Light", value: "light" },
  { label: "Dark", value: "dark" },
  { label: "Auto", value: "auto" },
];

const title = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** The only appearance choice users have (D19): colour scheme and accent. */
export function AppearanceMenu() {
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  const { accent, setAccent } = useAppearance();
  const theme = useMantineTheme();

  return (
    <Popover position="bottom-end" shadow="md" width={232}>
      <Popover.Target>
        <ActionIcon variant="subtle" color="gray" size="lg" aria-label="Appearance">
          <Palette size={18} strokeWidth={1.5} aria-hidden />
        </ActionIcon>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="sm">
          <Text size="sm" fw={600}>
            Colour scheme
          </Text>
          <SegmentedControl
            aria-label="Colour scheme"
            fullWidth
            size="xs"
            value={colorScheme}
            onChange={(value) => setColorScheme(value as MantineColorScheme)}
            data={SCHEMES}
          />
          <Text size="sm" fw={600}>
            Accent
          </Text>
          <Group gap="xs" role="group" aria-label="Accent">
            {ACCENTS.map((option) => (
              <ColorSwatch
                key={option}
                component="button"
                type="button"
                color={theme.colors[option]?.[6] ?? ""}
                size={26}
                aria-label={`Accent: ${title(option)}`}
                aria-pressed={option === accent}
                onClick={() => setAccent(option)}
              >
                {option === accent && <CheckIcon size={12} color="var(--mantine-color-white)" />}
              </ColorSwatch>
            ))}
          </Group>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}
