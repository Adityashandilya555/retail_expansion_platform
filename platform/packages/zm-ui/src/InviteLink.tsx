import { Button, Group, Stack, Text, TextInput } from "@mantine/core";
import { useClipboard } from "@mantine/hooks";

/** A one-time invite link (S3-01) for the inviter to copy and hand over, shown once, right after the
 * invite: the API never answers it again. Read-only, so it can still be selected by hand when the
 * browser has no clipboard (plain http) or refuses it, which the panel then says. */
export function InviteLink({ url, expiresAt }: { url: string; expiresAt: string }) {
  const clipboard = useClipboard();
  const until = new Date(expiresAt).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  return (
    <Stack gap="xs">
      <Group align="flex-end" wrap="nowrap" gap="xs">
        <TextInput
          flex={1}
          readOnly
          label="Invite link"
          description={`Works once, until ${until}. Also emailed if email is set up.`}
          value={url}
          onFocus={(e) => e.currentTarget.select()}
        />
        <Button variant="default" onClick={() => clipboard.copy(url)}>
          {clipboard.copied ? "Copied" : "Copy link"}
        </Button>
      </Group>
      {clipboard.error && (
        <Text size="sm" role="alert">
          This browser didn't let us copy. Select the link and copy it yourself: it isn't shown again.
        </Text>
      )}
    </Stack>
  );
}
