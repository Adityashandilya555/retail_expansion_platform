import { Anchor, Button, Center, Loader, Stack, Text, TextInput } from "@mantine/core";
import { AuthCard, notifyError, SignInForm, ZmProvider } from "@zm/ui";
import { useEffect, useState } from "react";
import { BrowserRouter, Link, Navigate, Route, Routes, useNavigate } from "react-router";
import { findWorkspace, loadMe, refresh, savedWorkspace, signedIn, signIn, signOut, type Me, type Workspace } from "./session";

/** `/`: the workspace code. */
function Landing({ onWorkspace }: { onWorkspace: (w: Workspace) => void }) {
  const navigate = useNavigate();
  const [code, setCode] = useState("");
  const [pending, setPending] = useState(false);
  return (
    <AuthCard title="Sign in to your workspace" description="Enter the workspace code your organisation gave you.">
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setPending(true);
          const found = await findWorkspace(code.trim());
          setPending(false);
          if (!found.ok) return notifyError(found.message);
          onWorkspace(found.value);
          navigate("/login");
        }}
      >
        <Stack>
          <TextInput
            label="Workspace code"
            required
            autoFocus
            autoComplete="organization"
            value={code}
            onChange={(e) => setCode(e.currentTarget.value)}
          />
          <Button type="submit" loading={pending} fullWidth>
            Continue
          </Button>
        </Stack>
      </form>
    </AuthCard>
  );
}

/** `/login`: email and password, on the client's look. */
function Login({ workspace }: { workspace: Workspace | null }) {
  const navigate = useNavigate();
  const [pending, setPending] = useState(false);
  if (!workspace) return <Navigate to="/" replace />;
  return (
    <AuthCard title={workspace.client_name} description="Sign in with your work email.">
      <SignInForm
        pending={pending}
        onSubmit={async ({ email, password }) => {
          setPending(true);
          const done = await signIn(workspace.code, email, password);
          setPending(false);
          if (!done.ok) return notifyError(done.message);
          navigate("/app");
        }}
      />
      <Anchor component={Link} to="/" size="sm">
        Use a different workspace
      </Anchor>
    </AuthCard>
  );
}

/** `/app`: signed in (silently from the refresh cookie after a reload). The site screens come with #27. */
function Home() {
  const navigate = useNavigate();
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    void (async () => {
      const ok = signedIn() || (await refresh());
      const found = ok ? await loadMe() : null;
      if (live) setMe(found);
    })();
    return () => {
      live = false;
    };
  }, []);
  if (me === null) return <Navigate to="/" replace />;
  if (me === undefined) {
    return (
      <Center mih="100vh">
        <Loader aria-label="Signing in" />
      </Center>
    );
  }
  const levels = me.memberships.map((m) => `${m.department} L${m.level}`).join(", ");
  return (
    <AuthCard title={`Signed in as ${me.name}`} description={`${me.client.name} · ${levels}`}>
      <Text size="sm" c="dimmed">
        The site screens arrive with #27.
      </Text>
      <Button
        variant="default"
        onClick={async () => {
          await signOut();
          navigate("/");
        }}
      >
        Sign out
      </Button>
    </AuthCard>
  );
}

/** The client app's routes, inside a router (BrowserRouter in the app, MemoryRouter in tests). */
export function ClientApp() {
  const [workspace, setWorkspace] = useState(savedWorkspace);
  return (
    <ZmProvider client={workspace ? { theme: workspace.theme, accent: workspace.accent } : undefined}>
      <Routes>
        <Route path="/" element={<Landing onWorkspace={setWorkspace} />} />
        <Route path="/login" element={<Login workspace={workspace} />} />
        <Route path="/app/*" element={<Home />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </ZmProvider>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <ClientApp />
    </BrowserRouter>
  );
}
