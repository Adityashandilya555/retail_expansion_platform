import { Badge } from "@mantine/core";
import type { ReactNode } from "react";

export type StatusTone = "done" | "open" | "queued" | "closed" | "warning";

// Colour never carries meaning alone: every tone has a text label (children may refine it).
const TONES: Record<StatusTone, { color?: string; label: string }> = {
  done: { color: "green", label: "Done" },
  open: { label: "Open" }, // primary = the user's accent
  queued: { color: "gray", label: "Queued" },
  closed: { color: "red", label: "Closed" },
  warning: { color: "orange", label: "Attention" },
};

export function StatusPill({ tone, children }: { tone: StatusTone; children?: ReactNode }) {
  const { color, label } = TONES[tone];
  return (
    <Badge variant="light" color={color} data-tone={tone}>
      {children ?? label}
    </Badge>
  );
}
