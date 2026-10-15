// /dev/ui (dev server only, never in production builds): every @zm/ui extra plus a sample of Mantine
// inputs, with a preset switcher; the top bar's appearance menu switches scheme and accent.
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  NumberInput,
  Paper,
  Radio,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Textarea,
  Title,
} from "@mantine/core";
import { AppFrame, DataTable, Num, PageHeader, PRESETS, StageTracker, StatusPill, ZmProvider, type Column, type NavItem, type Stage } from "@zm/ui";
import { ClipboardCheck, Inbox, MapPin, Scale } from "lucide-react";
import { useState, type ReactNode } from "react";

const nav: NavItem[] = [
  { key: "home", label: "My work", icon: Inbox, badge: 3 },
  { key: "sites", label: "Sites", icon: MapPin },
  { key: "legal", label: "Legal", icon: Scale, badge: 1 },
  { key: "design", label: "Design", icon: ClipboardCheck },
];

const stages: Stage[] = [
  { key: "bd", label: "BD", state: "done" },
  { key: "legal", label: "Legal", state: "open" },
  { key: "finance", label: "Finance", state: "open" },
  { key: "design", label: "Design", state: "queued" },
  { key: "launch", label: "Launch", state: "queued" },
];

type Site = { code: string; name: string; city: string; days: number; capex: number; state: Stage["state"] };
const sites: Site[] = [
  { code: "MX-0001", name: "Koramangala 5th Block", city: "Bengaluru", days: 4, capex: 4250000, state: "open" },
  { code: "MX-0002", name: "Baner Road", city: "Pune", days: 11, capex: 3180000, state: "queued" },
  { code: "MX-0003", name: "Anna Nagar", city: "Chennai", days: 2, capex: 5120000, state: "done" },
];
const columns: Column<Site>[] = [
  { key: "code", header: "Code", sortValue: (s) => s.code, render: (s) => <Text ff="monospace" size="sm">{s.code}</Text> },
  { key: "name", header: "Site", sortValue: (s) => s.name },
  { key: "city", header: "City" },
  { key: "state", header: "Status", render: (s) => <StatusPill tone={s.state} /> },
  { key: "days", header: "Days in stage", align: "right", sortValue: (s) => s.days, render: (s) => <Num value={s.days} /> },
  { key: "capex", header: "Capex", align: "right", sortValue: (s) => s.capex, render: (s) => <Num value={s.capex} currency /> },
];

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Paper withBorder p="md">
      <Title order={2} size="h4" mb="sm">
        {title}
      </Title>
      {children}
    </Paper>
  );
}

export default function DevUi() {
  const [preset, setPreset] = useState("quartz");
  const [tableState, setTableState] = useState("rows");
  return (
    <ZmProvider client={{ theme: preset }}>
      <AppFrame clientName="Matrix Retail (preview)" nav={nav} activeKey="sites" user={{ name: "Priya Rao", email: "priya@example.com" }}>
        <PageHeader
          title="/dev/ui"
          description="Every @zm/ui extra and a sample of Mantine inputs. Switch scheme and accent in the top bar."
          actions={
            <Select
              aria-label="Preset"
              data={Object.values(PRESETS).map((p) => ({ value: p.key, label: p.label }))}
              value={preset}
              onChange={(v) => setPreset(v ?? "quartz")}
              allowDeselect={false}
              w={160}
            />
          }
        />
        <Stack gap="lg">
          <Section title="Status pills">
            <Group gap="xs">
              <StatusPill tone="done" />
              <StatusPill tone="open" />
              <StatusPill tone="queued" />
              <StatusPill tone="closed" />
              <StatusPill tone="warning" />
              <StatusPill tone="open">In Legal</StatusPill>
            </Group>
          </Section>

          <Section title="Stage tracker">
            <Stack gap="sm">
              <StageTracker stages={stages} label="Stages of MX-0001" />
              <StageTracker stages={stages} compact label="Stages of MX-0001 (compact)" />
            </Stack>
          </Section>

          <Section title="Data table">
            <SegmentedControl
              aria-label="Table state"
              mb="sm"
              value={tableState}
              onChange={setTableState}
              data={["rows", "loading", "empty", "error"]}
            />
            <DataTable
              caption="Sites"
              columns={columns}
              rows={tableState === "rows" ? sites : []}
              rowKey={(s) => s.code}
              onRowClick={() => {}}
              loading={tableState === "loading"}
              error={tableState === "error" ? "Couldn't load sites" : null}
              onRetry={() => setTableState("rows")}
              emptyText="No sites yet"
            />
          </Section>

          <Section title="Numbers">
            <Group gap="xl">
              <Num value={1234567} />
              <Num value={4250000} currency />
              <Num value={98.456} decimals={2} />
              <Num value={null} />
            </Group>
          </Section>

          <Section title="Mantine inputs (used directly, styled by the theme)">
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <TextInput label="Site name" placeholder="Koramangala 5th Block" required />
              <NumberInput label="Monthly rent" prefix="₹" thousandSeparator="," />
              <Select label="Store model" data={["Flagship", "Express", "Kiosk"]} />
              <Textarea label="Notes" autosize minRows={2} />
              <Radio.Group label="Title clear?" withAsterisk>
                <Group mt="xs">
                  <Radio value="yes" label="Yes" />
                  <Radio value="no" label="No" />
                </Group>
              </Radio.Group>
              <Checkbox label="Fire NOC received" mt="lg" />
            </SimpleGrid>
            <Group mt="md">
              <Button>Approve</Button>
              <Button variant="light">Save draft</Button>
              <Button variant="outline">Send back</Button>
              <Button color="red" variant="light">
                Reject
              </Button>
              <Button variant="subtle">Cancel</Button>
              <Badge>Badge</Badge>
            </Group>
            <Alert mt="md" title="Heads up">
              Alerts and notifications follow the accent too.
            </Alert>
          </Section>
        </Stack>
      </AppFrame>
    </ZmProvider>
  );
}
