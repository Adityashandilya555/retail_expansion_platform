import { Center, Stack, Text, Title } from "@mantine/core";
import { AppearanceMenu, ZmProvider } from "@zm/ui";

export function App() {
  return (
    <ZmProvider>
      <Center mih="100vh">
        <Stack align="center" gap="xs">
          <Title order={1}>Z-Matrix — client app</Title>
          <Text c="dimmed">Sign-in and the site screens arrive with #22 and #27.</Text>
          <AppearanceMenu />
        </Stack>
      </Center>
    </ZmProvider>
  );
}
