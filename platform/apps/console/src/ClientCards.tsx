// The owner dashboard (#47): per client, its accounts, cases in progress, active release and the
// health of its Operaton and database. Counts and status only, never a client's business data.
import { Badge, Box, Button, Card, Group, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import type { components } from "@zm/api-client";
import { Notice, Num, TONE_COLORS } from "@zm/ui";
import type { ReactNode } from "react";
import { CLIENT_STATUS, Status } from "./Clients";
import { api, must } from "./session";

type ClientOut = components["schemas"]["ClientOut"];
type ClientOverview = components["schemas"]["ClientOverview"];
type Probe = components["schemas"]["Probe"];

/** Keep equal to CONSOLE_POLL in apps/api/src/zm_api/clients.py: the API keeps its answer 30 s;
 * the registry read and a client probe take at most 3 + 10 s, so a down engine is red here within
 * 30 + 15 + 3 + 10 = 58 s (#47). */
const POLL_MS = 15_000;

function Health({ label, probe }: { label: string; probe: Probe }) {
  return (
    <Badge variant="dot" color={probe.up ? TONE_COLORS.success : TONE_COLORS.error}>
      {probe.up ? `${label} up · ${probe.ms} ms` : `${label} down`}
    </Badge>
  );
}

function Row({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div>
      <Text component="dt" size="sm" c="dimmed">
        {term}
      </Text>
      <Text component="dd" m={0}>
        {children}
      </Text>
    </div>
  );
}

function ClientCard({ name, c }: { name: string; c: ClientOverview }) {
  return (
    <Card withBorder component="section" aria-label={name}>
      <Stack gap="sm">
        <Group justify="space-between" wrap="nowrap">
          <Title order={2} size="h4">
            {name}
          </Title>
          <Status of={CLIENT_STATUS} status={c.status} />
        </Group>
        {c.health ? (
          <>
            <Stack component="dl" gap="xs" m={0}>
              <Row term="Users">
                {c.users ? (
                  <>
                    <Num value={c.users.active} /> active · <Num value={c.users.invited} /> invited · <Num value={c.users.deactivated} /> deactivated
                  </>
                ) : (
                  "—"
                )}
              </Row>
              <Row term="Cases in progress">
                {c.cases_in_progress === null
                  ? "—"
                  : c.cases_in_progress.length === 0
                    ? "No flows published yet"
                    : c.cases_in_progress.map((f, i) => (
                        <span key={f.flow}>
                          {i > 0 && " · "}
                          {f.flow} <Num value={f.count} />
                        </span>
                      ))}
              </Row>
              <Row term="Last release">
                {c.last_release ? `v${c.last_release.version} · ${new Date(c.last_release.published_at).toLocaleString()}` : "Nothing published yet"}
              </Row>
            </Stack>
            <Group gap="xs">
              <Health label="Operaton" probe={c.health.operaton} />
              <Health label="Database" probe={c.health.db} />
            </Group>
          </>
        ) : (
          <Text size="sm" c="dimmed">
            Counts and health show once it's ready.
          </Text>
        )}
      </Stack>
    </Card>
  );
}

/** A card per client; `clients` (from `GET /owner/clients`) names them. */
export function ClientCards({ clients }: { clients: ClientOut[] }) {
  const overview = useQuery({
    queryKey: ["clients", "overview"],
    queryFn: () => must(api.GET("/owner/clients/overview")),
    refetchInterval: POLL_MS,
  });
  if (overview.isPending) return null;
  const names = new Map(clients.map((c) => [c.id, c.name]));
  return (
    <Box mb="xl">
      {overview.isError && overview.data && (
        <Box mb="md">
          <Notice tone="warning" title={`Couldn't refresh — showing data from ${new Date(overview.dataUpdatedAt).toLocaleTimeString()}.`}>
            <Button mt="sm" variant="default" onClick={() => void overview.refetch()}>
              Retry
            </Button>
          </Notice>
        </Box>
      )}
      {overview.data ? (
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
          {overview.data.map((c) => (
            <ClientCard key={c.id} name={names.get(c.id) ?? c.slug} c={c} />
          ))}
        </SimpleGrid>
      ) : (
        <Notice tone="error" title="Couldn't load the overview.">
          <Button mt="sm" variant="default" onClick={() => void overview.refetch()}>
            Retry
          </Button>
        </Notice>
      )}
    </Box>
  );
}
