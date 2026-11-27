// Preview (#42, S2-08): the draft in the client app, as one role, in a new tab, beside what the
// draft changes against the live release. The API hands out a 15-minute read-only token; it goes
// to the client app in the URL fragment, which no server sees.
import { Button, Group, List, NativeSelect, Stack, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { summary } from "./Publish";
import { api, must } from "./session";

const WEB = import.meta.env.VITE_WEB_URL ?? "http://localhost:5173";

/** `path` on the client app (zm-web), where Preview opens and invite links (#169) point. A
 * trailing `/` on VITE_WEB_URL doesn't double the slash. */
export const onWeb = (path: string) => new URL(path, WEB).href;

type Department = { key: string; name: string; levels: { n: number }[] };

export function PreviewPanel({ clientId, disabled }: { clientId: string; disabled?: boolean }) {
  const path = { params: { path: { client_id: clientId } } };
  const draft = useQuery({ queryKey: ["draft", clientId], queryFn: () => must(api.GET("/owner/clients/{client_id}/draft", path)) });
  const diff = useQuery({ queryKey: ["diff", clientId], queryFn: () => must(api.GET("/owner/clients/{client_id}/draft/diff", path)) });
  const [picked, setPicked] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const why = useId();

  const org = draft.data?.workflow.org as { departments?: Department[] } | undefined;
  const roles = (org?.departments ?? []).flatMap((d) => d.levels.map((l) => ({ value: `${d.key}:${l.n}`, label: `${d.name} L${l.n}` })));
  const role = picked || roles[0]?.value;
  const lines = diff.data ? summary(diff.data) : [];

  async function open() {
    if (!role) return;
    const [department = "", level] = role.split(":");
    // opened before the request, while the click still counts as the owner's: a tab opened after
    // an await is a blocked pop-up
    const tab = window.open("", "_blank");
    setBusy(true);
    setFailed(null);
    try {
      const body = { as: { department, level: Number(level) } };
      const { access_token } = await must(api.POST("/owner/clients/{client_id}/preview-token", { ...path, body }));
      const url = onWeb(`/preview#${access_token}`);
      if (tab) {
        tab.opener = null;
        tab.location.replace(url);
      } else window.open(url, "_blank", "noopener");
    } catch (e) {
      tab?.close();
      setFailed(e instanceof Error && e.message !== "Request failed" ? e.message : "Couldn't open the preview.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Stack gap="xs">
      <Group align="flex-end" gap="xs">
        <NativeSelect label="Preview as" value={role ?? ""} disabled={roles.length === 0} onChange={(e) => setPicked(e.currentTarget.value)} data={roles} />
        <Button variant="default" loading={busy} disabled={disabled || !role} aria-describedby={failed ? why : undefined} onClick={() => void open()}>
          Preview
        </Button>
      </Group>
      {failed && (
        <Text id={why} size="sm" role="alert">
          {failed}
        </Text>
      )}
      {diff.data &&
        (lines.length ? (
          <List size="sm" aria-label="Changes against the live release">
            {lines.map((line) => (
              <List.Item key={line}>{line}</List.Item>
            ))}
          </List>
        ) : (
          <Text size="sm">The draft matches the live release.</Text>
        ))}
    </Stack>
  );
}
