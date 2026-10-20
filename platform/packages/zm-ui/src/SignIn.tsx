import { Button, Center, Paper, PasswordInput, Stack, Text, TextInput, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useState, type ReactNode } from "react";

/** The centred card each sign-in step sits in, in the client app and the owner console (#22). */
export function AuthCard({ title, description, children }: { title: ReactNode; description?: ReactNode; children: ReactNode }) {
  return (
    <Center component="main" mih="100vh" p="md">
      <Paper withBorder radius="md" p="xl" w="100%" maw={400}>
        <Stack gap="lg">
          <Stack gap={4}>
            <Text size="xs" fw={600} c="dimmed" tt="uppercase">
              Z-Matrix
            </Text>
            <Title order={1} size="h3">
              {title}
            </Title>
            {description && (
              <Text size="sm" c="dimmed">
                {description}
              </Text>
            )}
          </Stack>
          {children}
        </Stack>
      </Paper>
    </Center>
  );
}

/** Email + password, sent with Enter or the button; the caller reports a failure with `notifyError`.
 * Email takes focus on arrival, so after the route change a keyboard user starts in the right field. */
export function SignInForm({
  pending = false,
  submitLabel = "Sign in",
  onSubmit,
}: {
  pending?: boolean;
  submitLabel?: string;
  onSubmit: (values: { email: string; password: string }) => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({ email: email.trim(), password });
      }}
    >
      <Stack>
        <TextInput
          label="Email"
          type="email"
          autoComplete="username"
          required
          autoFocus
          value={email}
          onChange={(e) => setEmail(e.currentTarget.value)}
        />
        <PasswordInput
          label="Password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.currentTarget.value)}
        />
        <Button type="submit" loading={pending} fullWidth>
          {submitLabel}
        </Button>
      </Stack>
    </form>
  );
}

/** An error the person can act on, as a Mantine notification (inside ZmProvider). */
export function notifyError(message: string) {
  notifications.show({ color: "red", title: "Couldn't continue", message });
}
