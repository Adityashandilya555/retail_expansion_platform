import { Badge } from "@mantine/core";
import type { ReactNode } from "react";
import { TONE_COLORS } from "./theme";

export type StatusTone = "done" | "open" | "queued" | "closed" | "warning";

// Colour never carries meaning alone: every tone has a text label (children may refine it).
const TONES: Record<StatusTone, { color?: string; label: string }> = {
  done: { color: TONE_COLORS.success, label: "Done" },
  open: { label: "Open" }, // primary = the user's accent
  queued: { color: TONE_COLORS.neutral, label: "Queued" },
  closed: { color: TONE_COLORS.error, label: "Closed" },
  warning: { color: TONE_COLORS.warning, label: "Attention" },
};

export function StatusPill({ tone, children }: { tone: StatusTone; children?: ReactNode }) {
  const { color, label } = TONES[tone];
  return (
    <Badge variant="light" color={color} data-tone={tone}>
      {children ?? label}
    </Badge>
  );
}
