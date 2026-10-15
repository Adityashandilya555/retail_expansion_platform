import { Alert, Button, Group, Loader, Table, Text, UnstyledButton } from "@mantine/core";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { useMemo, useState, type KeyboardEvent, type ReactNode } from "react";

export type Column<T> = {
  key: string;
  header: string;
  /** Cell content; defaults to `String(row[key])`. */
  render?: (row: T) => ReactNode;
  /** Makes the column sortable. */
  sortValue?: (row: T) => string | number;
  align?: "left" | "right";
};

type Sort = { key: string; dir: "ascending" | "descending" } | null;

/** A thin Mantine Table: sortable columns, clickable rows, loading/empty/error states. */
export function DataTable<T>({
  caption,
  columns,
  rows,
  rowKey,
  onRowClick,
  loading = false,
  error = null,
  onRetry,
  emptyText = "Nothing here yet",
}: {
  caption: string;
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  emptyText?: string;
}) {
  const [sort, setSort] = useState<Sort>(null);

  const sorted = useMemo(() => {
    const column = sort && columns.find((c) => c.key === sort.key);
    if (!sort || !column?.sortValue) return rows;
    const value = column.sortValue;
    const factor = sort.dir === "ascending" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const x = value(a);
      const y = value(b);
      return factor * (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y)));
    });
  }, [rows, columns, sort]);

  const toggle = (key: string) =>
    setSort((s) => (s?.key === key && s.dir === "ascending" ? { key, dir: "descending" } : { key, dir: "ascending" }));

  const onRowKey = (event: KeyboardEvent, row: T) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onRowClick?.(row);
    }
  };

  const fullRow = (content: ReactNode) => (
    <Table.Tr>
      <Table.Td colSpan={columns.length}>{content}</Table.Td>
    </Table.Tr>
  );

  return (
    <Table.ScrollContainer minWidth={480}>
      <Table captionSide="top">
        <Table.Caption>{caption}</Table.Caption>
        <Table.Thead>
          <Table.Tr>
            {columns.map((c) => {
              const dir = sort?.key === c.key ? sort.dir : undefined;
              const SortIcon = dir === "ascending" ? ArrowUp : dir === "descending" ? ArrowDown : ArrowUpDown;
              return (
                <Table.Th key={c.key} ta={c.align} aria-sort={c.sortValue ? (dir ?? "none") : undefined}>
                  {c.sortValue ? (
                    <UnstyledButton onClick={() => toggle(c.key)} fw={600} fz="sm">
                      <Group gap={4} wrap="nowrap" justify={c.align === "right" ? "flex-end" : "flex-start"}>
                        {c.header}
                        <SortIcon size={14} strokeWidth={1.5} aria-hidden />
                      </Group>
                    </UnstyledButton>
                  ) : (
                    c.header
                  )}
                </Table.Th>
              );
            })}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {loading
            ? fullRow(
                <Group role="status" gap="xs">
                  <Loader size="xs" aria-hidden />
                  <Text size="sm">Loading…</Text>
                </Group>,
              )
            : error
              ? fullRow(
                  <Alert color="red" variant="light" role="alert">
                    <Group justify="space-between">
                      <Text size="sm">{error}</Text>
                      {onRetry && (
                        <Button size="xs" variant="light" color="red" onClick={onRetry}>
                          Retry
                        </Button>
                      )}
                    </Group>
                  </Alert>,
                )
              : sorted.length === 0
                ? fullRow(
                    <Text size="sm" c="dimmed" ta="center" py="md">
                      {emptyText}
                    </Text>,
                  )
                : sorted.map((row) => (
                    <Table.Tr
                      key={rowKey(row)}
                      onClick={onRowClick ? () => onRowClick(row) : undefined}
                      onKeyDown={onRowClick ? (e) => onRowKey(e, row) : undefined}
                      tabIndex={onRowClick ? 0 : undefined}
                      style={onRowClick ? { cursor: "pointer" } : undefined}
                    >
                      {columns.map((c) => (
                        <Table.Td key={c.key} ta={c.align}>
                          {c.render ? c.render(row) : String((row as Record<string, unknown>)[c.key] ?? "")}
                        </Table.Td>
                      ))}
                    </Table.Tr>
                  ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
