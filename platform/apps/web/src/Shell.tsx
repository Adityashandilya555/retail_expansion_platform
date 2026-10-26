// The signed-in client app (#27): one shell; the release from `GET /app/ui` decides the nav and
// pages, already filtered to this user's departments by the API.
import { Alert, Anchor, Button, Center, Loader, Menu, Stack, Text, Title } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BlocksApi, PageRenderer, defaultCasePage } from "@zm/blocks";
import { AppFrame, ZmProvider } from "@zm/ui";
import { LogOut } from "lucide-react";
import { Link, Navigate, Route, Routes, useNavigate, useParams } from "react-router";
import { api, currentUser, loadUi, signOut, type AppUi, type Workspace } from "./session";

function NotFound() {
  return (
    <Stack align="flex-start" gap="xs">
      <Title order={1}>Page not found</Title>
      <Text c="dimmed">This page doesn't exist, or it isn't shared with your department.</Text>
      <Anchor component={Link} to="/app">
        Go to your home page
      </Anchor>
    </Stack>
  );
}

/** `/app` (the first nav item's page) and `/app/:page`. Case pages only open on a case. */
function PageRoute({ ui, pageKey }: { ui: AppUi; pageKey: string | undefined }) {
  const page = ui.pages.find((p) => p.key === pageKey && p.layout !== "case");
  return page ? <PageRenderer key={page.key} page={page} ui={ui} /> : <NotFound />;
}

/** `MX-{seq:4}` → /^MX-\d{4,}$/: the shell finds a case's flow from its code. */
function codePattern(pattern: string): RegExp {
  const parts = pattern.split(/\{seq:(\d)\}/);
  const source = parts.map((part, i) => (i % 2 ? `\\d{${part},}` : part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  return new RegExp(`^${source.join("")}$`);
}

/** `/app/cases/:code`: the release's `case` page for the case's flow, or the built-in one. */
function CaseRoute({ ui }: { ui: AppUi }) {
  const { code = "" } = useParams();
  // ponytail: first flow whose pattern matches; read the flow from GET /app/cases/{code} (#91) if two flows share a pattern
  const flow = ui.flows.find((f) => codePattern(f.case_code).test(code));
  if (!flow) return <NotFound />;
  const page = ui.pages.find((p) => p.layout === "case" && p.flow === flow.key) ?? defaultCasePage(flow.key);
  return <PageRenderer key={code} page={page} ui={ui} />;
}

function Screen({ label }: { label: string }) {
  return (
    <Center mih="100vh">
      <Loader aria-label={label} />
    </Center>
  );
}

export function Shell({ workspace }: { workspace: Workspace | null }) {
  const navigate = useNavigate();
  const queries = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: currentUser });
  const ui = useQuery({ queryKey: ["ui"], queryFn: loadUi, enabled: Boolean(me.data) });
  const section = useParams()["*"]?.split("/")[0] || undefined;

  if (me.data === null) return <Navigate to="/" replace />;
  const look = ui.data?.client ?? workspace ?? undefined;
  let body;
  if (me.isError) {
    body = (
      <Center mih="100vh">
        <Stack align="center">
          <Text>Can't reach Z-Matrix right now.</Text>
          <Button onClick={() => void me.refetch()}>Retry</Button>
        </Stack>
      </Center>
    );
  } else if (!me.data || ui.isPending) {
    body = <Screen label="Loading your workspace" />;
  } else {
    const release = ui.data;
    const home = release?.nav[0]?.page;
    body = (
      <AppFrame
        clientName={release?.client.name ?? me.data.client.name}
        nav={(release?.nav ?? []).map((n) => ({ key: n.page, label: n.label }))}
        activeKey={section ?? home}
        onNavigate={(key) => navigate(`/app/${key}`)}
        user={{ name: me.data.name, email: me.data.email }}
        userMenu={
          <Menu.Item
            leftSection={<LogOut size={14} strokeWidth={1.5} aria-hidden />}
            onClick={async () => {
              await signOut();
              queries.clear(); // the next person in this tab must not see this one's data
              navigate("/");
            }}
          >
            Sign out
          </Menu.Item>
        }
      >
        {ui.isError ? (
          <Alert color="red" title="Couldn't load this workspace.">
            <Button mt="sm" variant="default" onClick={() => void ui.refetch()}>
              Retry
            </Button>
          </Alert>
        ) : !release ? (
          <Text c="dimmed">Nothing has been published for this workspace yet.</Text>
        ) : (
          <BlocksApi value={api}>
            <Routes>
              <Route index element={<PageRoute ui={release} pageKey={home} />} />
              <Route path="cases/:code" element={<CaseRoute ui={release} />} />
              <Route path=":page" element={<PageRoute ui={release} pageKey={section} />} />
              <Route path="*" element={<NotFound />} />
            </Routes>
          </BlocksApi>
        )}
      </AppFrame>
    );
  }
  return (
    <ZmProvider client={look} userId={me.data?.id}>
      {body}
    </ZmProvider>
  );
}
