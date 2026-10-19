import { Group, Text, ThemeIcon, Tooltip, VisuallyHidden } from "@mantine/core";
import { Check, Circle, CircleDot, X, type LucideIcon } from "lucide-react";

export type StageState = "done" | "open" | "queued" | "closed";
export type Stage = { key: string; label: string; state: StageState };

const LOOK: Record<StageState, { icon: LucideIcon; color?: string; variant: "filled" | "light" | "outline" }> = {
  done: { icon: Check, color: "green", variant: "filled" },
  open: { icon: CircleDot, variant: "filled" }, // primary = the user's accent
  queued: { icon: Circle, color: "gray", variant: "outline" },
  closed: { icon: X, color: "red", variant: "filled" },
};

/**
 * The stages of one case, in order (Matrix-bd's stage tracker). Each item reads as "<stage>: <state>"
 * for assistive tech; the compact variant (table rows) shows icons only, with the label in a tooltip.
 */
export function StageTracker({ stages, compact = false, label = "Stages" }: { stages: Stage[]; compact?: boolean; label?: string }) {
  return (
    <Group component="ol" aria-label={label} gap={compact ? 4 : "xs"} wrap={compact ? "nowrap" : "wrap"} m={0} p={0} style={{ listStyle: "none" }}>
      {stages.map((stage) => {
        const look = LOOK[stage.state];
        const Icon = look.icon;
        const badge = (
          <ThemeIcon size={compact ? "xs" : "sm"} radius="xl" color={look.color} variant={look.variant}>
            <Icon size={compact ? 10 : 12} strokeWidth={2} aria-hidden />
          </ThemeIcon>
        );
        return (
          <Group component="li" key={stage.key} gap={6} wrap="nowrap" aria-current={stage.state === "open" ? "step" : undefined}>
            {compact ? (
              <Tooltip label={`${stage.label}: ${stage.state}`} withArrow>
                <span>{badge}</span>
              </Tooltip>
            ) : (
              badge
            )}
            {compact ? (
              <VisuallyHidden>{`${stage.label}: ${stage.state}`}</VisuallyHidden>
            ) : (
              <>
                <Text size="sm" fw={stage.state === "open" ? 600 : 400} c={stage.state === "queued" ? "dimmed" : undefined}>
                  {stage.label}
                </Text>
                <VisuallyHidden>{`: ${stage.state}`}</VisuallyHidden>
              </>
            )}
          </Group>
        );
      })}
    </Group>
  );
}
