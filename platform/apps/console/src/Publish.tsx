// The Publish dialog (#43, S2-09): what changes against the live release, what must be fixed first,
// then the confirm. Publishing itself is S1-08b's endpoint; it checks the draft again, so a check
// that couldn't run here doesn't block it.
import { Button, Group, List, Modal, Stack, Text } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "@zm/api-client";
import { Notice, notifyInfo } from "@zm/ui";
import { useState } from "react";
import { api, must } from "./session";

type DraftDiff = components["schemas"]["DraftDiff"];
type KeyChanges = components["schemas"]["KeyChanges"];

function keyed(label: string, changes: KeyChanges): string[] {
  const parts = (["added", "changed", "removed"] as const)
    .filter((kind) => changes[kind].length > 0)
    .map((kind) => `${kind} ${changes[kind].join(", ")}`);
  return parts.length ? [`${label}: ${parts.join("; ")}`] : [];
}

/** One line per kind of change, in the order an owner reads a release: data, flow, screens, look. */
export function summary(diff: DraftDiff): string[] {
  return [
    ...keyed("Fields", diff.fields),
    ...keyed("Stages", diff.stages),
    ...keyed("Tasks", diff.tasks),
    ...(diff.blocks.length ? [`Pages with changed blocks: ${diff.blocks.join(", ")}`] : []),
    ...(diff.theme ? [`Theme: ${diff.theme.live ?? "none"} → ${diff.theme.draft ?? "none"}`] : []),
    ...(diff.accent ? [`Accent: ${diff.accent.live ?? "none"} → ${diff.accent.draft ?? "none"}`] : []),
  ];
}

export function PublishDialog({
  clientId,
  clientName,
  next,
  opened,
  onClose,
}: {
  clientId: string;
  clientName: string;
  /** The version this publish would create; the live one is `next - 1` (none before v1). */
  next: number;
  opened: boolean;
  onClose: () => void;
}) {
  const queries = useQueryClient();
  const path = { params: { path: { client_id: clientId } } };
  // the dock shares "validate" and refreshes it after each change, so this stays current
  const problems = useQuery({
    queryKey: ["validate", clientId],
    queryFn: () => must(api.GET("/owner/clients/{client_id}/draft/validate", path)),
    enabled: opened,
  });
  const diff = useQuery({
    queryKey: ["diff", clientId],
    queryFn: () => must(api.GET("/owner/clients/{client_id}/draft/diff", path)),
    enabled: opened,
  });
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const live = next - 1;
  const lines = diff.data ? summary(diff.data) : [];
  // a check that failed doesn't block: publishing checks the draft again; a cached one waits for its
  // refetch, so the summary on screen is the one that gets published
  const blocked = problems.isFetching || diff.isFetching || (problems.data?.length ?? 0) > 0;

  function close() {
    setFailed(null);
    onClose();
  }

  async function publish() {
    setBusy(true);
    setFailed(null);
    try {
      const done = await must(api.POST("/owner/clients/{client_id}/publish", path));
      await Promise.all(["client", "diff", "draft"].map((key) => queries.invalidateQueries({ queryKey: [key, clientId] })));
      close();
      if (done.created) notifyInfo("Published", `Release v${done.version} is live.`);
      else notifyInfo("No new release", `Nothing changed since v${done.version}.`);
    } catch (e) {
      // the dialog stays open with the reason; the active release is unchanged (S1-08b writes nothing on failure)
      setFailed(e instanceof Error && e.message !== "Request failed" ? e.message : "Couldn't publish.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      opened={opened}
      onClose={close}
      title={`Publish v${next}?`}
      closeButtonProps={{ "aria-label": "Close" }}
      // Mantine draws the dialog's header as <header>, which reads as a second page banner
      attributes={{ header: { role: "presentation" } }}
    >
      <Stack>
        <Text size="sm">The draft is compiled and deployed to {clientName}'s own Operaton as release v{next}.</Text>
        <Text size="sm">Running sites stay on their release; new sites start on v{next}.</Text>

        {diff.isError && (
          <Notice tone="warning" title="Couldn't load the changes">
            <Text size="sm">{diff.error.message}</Text>
          </Notice>
        )}
        {diff.data &&
          (lines.length ? (
            <List size="sm" aria-label={live ? `Changes since v${live}` : "What v1 holds"}>
              {lines.map((line) => (
                <List.Item key={line}>{line}</List.Item>
              ))}
            </List>
          ) : (
            <Text size="sm">{live ? `No changes since v${live}.` : "The draft is empty."}</Text>
          ))}

        {problems.isError && (
          <Notice tone="warning" title="Couldn't check the draft">
            <Text size="sm">Publishing checks it again.</Text>
          </Notice>
        )}
        {problems.data && problems.data.length > 0 && (
          <Notice tone="error" title="Fix these first">
            <List size="sm" aria-label="Fix before publishing">
              {problems.data.map((p) => (
                <List.Item key={`${p.path} ${p.message}`}>{`${p.path}: ${p.message}`}</List.Item>
              ))}
            </List>
          </Notice>
        )}

        {failed && (
          <Notice tone="error" title="Not published">
            <Text size="sm">{failed}</Text>
          </Notice>
        )}

        <Group justify="flex-end">
          <Button variant="default" onClick={close}>
            Cancel
          </Button>
          <Button loading={busy} disabled={blocked} onClick={() => void publish()}>
            Publish
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
