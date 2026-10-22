// /dev/ui (dev server only, never in production builds): every @zm/ui extra, @zm/forms on real compiled forms
// and a sample of Mantine inputs, with a preset switcher; the top bar's appearance menu switches scheme and accent.
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Code,
  Group,
  NumberInput,
  Paper,
  Radio,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  TextInput,
  Textarea,
  Title,
} from "@mantine/core";
import { ZmForm } from "@zm/forms";
import { sampleApproval, sampleDdChecklist, sampleLicences } from "@zm/forms/samples";
import {
  AppFrame,
  DataTable,
  notifyError,
  Num,
  PageHeader,
  PRESETS,
  SignInForm,
  StageTracker,
  StatusPill,
  ZmProvider,
} from "@zm/ui";
import { sampleColumns as columns, sampleNav as nav, sampleSites as sites, sampleStages as stages } from "@zm/ui/samples";
import { useState, type ReactNode } from "react";

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
  const [submitted, setSubmitted] = useState("nothing yet");
  const onSubmit = async (values: Record<string, unknown>) => setSubmitted(JSON.stringify(values));
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

          <Section title="Sign-in form (AuthCard frames it on /, /login and the console's /login)">
            <Paper maw={400}>
              <SignInForm onSubmit={() => notifyError("Preview only: nothing was sent.")} />
            </Paper>
          </Section>

          <Section title="Numbers">
            <Group gap="xl">
              <Num value={1234567} />
              <Num value={4250000} currency />
              <Num value={98.456} decimals={2} />
              <Num value={null} />
            </Group>
          </Section>

          <Section title="Forms (@zm/forms): Legal DD checklist, QSR licences, a Legal L3 approval">
            <Stack gap="xl">
              <ZmForm
                schema={sampleDdChecklist}
                layout={sampleDdChecklist["x-zm-layout"] ?? []}
                caseLabel="Site"
                caseData={{ loi_file: { label: "Signed LOI", value: { name: "loi-mx-0001.pdf" } } }}
                onSubmit={onSubmit}
              />
              <ZmForm
                schema={sampleLicences}
                layout={sampleLicences["x-zm-layout"] ?? []}
                caseLabel="Store"
                caseData={{ store_name: { label: "Store name", value: "Indiranagar" } }}
                onSubmit={onSubmit}
              />
              <ZmForm
                schema={sampleApproval}
                layout={[]}
                caseLabel="Store"
                caseData={{ store_name: { label: "Store name", value: "Indiranagar" } }}
                onSubmit={onSubmit}
              />
              <Code block>Submitted: {submitted}</Code>
            </Stack>
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
