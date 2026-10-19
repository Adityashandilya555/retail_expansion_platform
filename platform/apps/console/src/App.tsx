import { Center, Stack, Text, Title } from "@mantine/core";
import { AppearanceMenu, ZmProvider } from "@zm/ui";

export function App() {
  return (
    <ZmProvider>
      <Center mih="100vh">
        <Stack align="center" gap="xs">
          <Title order={1}>Z-Matrix — owner console</Title>
          <Text c="dimmed">Client management arrives with #29.</Text>
          <AppearanceMenu />
        </Stack>
      </Center>
    </ZmProvider>
  );
}
