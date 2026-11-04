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
import { ACCENTS, type Accent } from "./theme";
import { useAppearance } from "./ZmProvider";

const SCHEMES: { label: string; value: MantineColorScheme }[] = [
  { label: "Light", value: "light" },
  { label: "Dark", value: "dark" },
  { label: "Auto", value: "auto" },
];

export const accentLabel = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** The curated accents as swatches, `value` pressed: a user's pick here, a client's default in the console. */
export function AccentSwatches({ value, onChange }: { value: Accent; onChange: (accent: Accent) => void }) {
  const theme = useMantineTheme();
  return (
    <Group gap="xs" role="group" aria-label="Accent">
      {ACCENTS.map((option) => (
        <ColorSwatch
          key={option}
          component="button"
          type="button"
          color={theme.colors[option]?.[6] ?? ""}
          size={26}
          aria-label={`Accent: ${accentLabel(option)}`}
          aria-pressed={option === value}
          onClick={() => onChange(option)}
        >
          {option === value && <CheckIcon size={12} color="var(--mantine-color-white)" />}
        </ColorSwatch>
      ))}
    </Group>
  );
}

/** The only appearance choice users have (D19): colour scheme and accent. */
export function AppearanceMenu() {
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  const { accent, setAccent, onAppearanceChange } = useAppearance();

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
            onChange={(value) => {
              const scheme = value as MantineColorScheme;
              setColorScheme(scheme);
              onAppearanceChange?.({ color_scheme: scheme });
            }}
            data={SCHEMES}
          />
          <Text size="sm" fw={600}>
            Accent
          </Text>
          <AccentSwatches
            value={accent}
            onChange={(option) => {
              setAccent(option);
              onAppearanceChange?.({ accent: option });
            }}
          />
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}
