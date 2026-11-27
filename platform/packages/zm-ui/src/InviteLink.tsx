import { Button, CopyButton, Group, TextInput } from "@mantine/core";

/** A one-time invite link (S3-01) for the inviter to copy and hand over, shown once, right after the
 * invite: the API never answers it again. Read-only, so it can still be selected by hand when the
 * browser refuses the clipboard. */
export function InviteLink({ url, expiresAt }: { url: string; expiresAt: string }) {
  const until = new Date(expiresAt).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  return (
    <Group align="flex-end" wrap="nowrap" gap="xs">
      <TextInput
        flex={1}
        readOnly
        label="Invite link"
        description={`Works once, until ${until}. Also emailed if email is set up.`}
        value={url}
        onFocus={(e) => e.currentTarget.select()}
      />
      <CopyButton value={url}>
        {({ copied, copy }) => (
          <Button variant="default" onClick={copy}>
            {copied ? "Copied" : "Copy link"}
          </Button>
        )}
      </CopyButton>
    </Group>
  );
}
