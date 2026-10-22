import { Alert } from "@mantine/core";
import type { ReactNode } from "react";
import { TONE_COLORS } from "./theme";

/** An inline message about a state: `error` (something failed) or `warning` (a person must act). */
export function Notice({ tone, title, children }: { tone: "error" | "warning"; title: string; children?: ReactNode }) {
  return (
    <Alert variant="light" color={TONE_COLORS[tone]} title={title}>
      {children}
    </Alert>
  );
}
