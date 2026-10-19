import { ActionIcon, Group, Text, Title } from "@mantine/core";
import { ArrowLeft } from "lucide-react";
import type { ReactNode } from "react";

export function PageHeader({
  title,
  description,
  actions,
  onBack,
  backLabel = "Back",
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  onBack?: () => void;
  backLabel?: string;
}) {
  return (
    <Group justify="space-between" align="flex-start" wrap="nowrap" mb="lg">
      <Group gap="sm" align="flex-start" wrap="nowrap">
        {onBack && (
          <ActionIcon variant="subtle" color="gray" size="lg" aria-label={backLabel} onClick={onBack}>
            <ArrowLeft size={18} strokeWidth={1.5} aria-hidden />
          </ActionIcon>
        )}
        <div>
          <Title order={1} size="h2">
            {title}
          </Title>
          {description && (
            <Text c="dimmed" size="sm">
              {description}
            </Text>
          )}
        </div>
      </Group>
      {actions && <Group gap="xs">{actions}</Group>}
    </Group>
  );
}
