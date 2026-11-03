import { Anchor, Button, PasswordInput, Stack, TextInput } from "@mantine/core";
import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { AuthCard, notifyError, SignInForm, ZmProvider } from "@zm/ui";
import { useState } from "react";
import { BrowserRouter, Link, Navigate, Outlet, Route, Routes, useNavigate, useParams } from "react-router";
import { acceptInvite, findWorkspace, savedWorkspace, signIn, type Workspace } from "./session";
import { Shell } from "./Shell";

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
  const queries = useQueryClient();
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
          queries.clear(); // e.g. a "signed out" answer cached by an earlier visit to /app
          navigate("/app");
        }}
      />
      <Anchor component={Link} to="/" size="sm">
        Use a different workspace
      </Anchor>
    </AuthCard>
  );
}

/** `/invite/:token`: the emailed invite (S2-23). Setting a password signs the person in. */
function AcceptInvite() {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const queries = useQueryClient();
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  return (
    <AuthCard title="Set your password" description="You'll sign in with your work email and this password.">
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setPending(true);
          const done = await acceptInvite(token, password);
          setPending(false);
          if (!done.ok) return notifyError(done.message);
          queries.clear();
          navigate("/app");
        }}
      >
        <Stack>
          <PasswordInput
            label="New password"
            description="At least 12 characters."
            autoComplete="new-password"
            required
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.currentTarget.value)}
          />
          <Button type="submit" loading={pending} fullWidth>
            Set password
          </Button>
        </Stack>
      </form>
    </AuthCard>
  );
}

/** The client app's routes, inside a router (BrowserRouter in the app, MemoryRouter in tests). */
export function ClientApp() {
  const [workspace, setWorkspace] = useState(savedWorkspace);
  const [queries] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }));
  return (
    <QueryClientProvider client={queries}>
      <Routes>
        {/* Signed out: the workspace's look. Signed in, the shell wears the release's look and the user's own. */}
        <Route
          element={
            <ZmProvider client={workspace ?? undefined}>
              <Outlet />
            </ZmProvider>
          }
        >
          <Route path="/" element={<Landing onWorkspace={setWorkspace} />} />
          <Route path="/login" element={<Login workspace={workspace} />} />
          <Route path="/invite/:token" element={<AcceptInvite />} />
        </Route>
        <Route path="/app/*" element={<Shell workspace={workspace} />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </QueryClientProvider>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <ClientApp />
    </BrowserRouter>
  );
}
