import { Button, Center, Loader, Text } from "@mantine/core";
import { AuthCard, notifyError, SignInForm, ZmProvider } from "@zm/ui";
import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useNavigate } from "react-router";
import { loadMe, refresh, signedIn, signIn, signOut, type OwnerMe } from "./session";

/** `/login`: platform owners only (#22). */
function Login() {
  const navigate = useNavigate();
  const [pending, setPending] = useState(false);
  return (
    <AuthCard title="Owner console" description="For Z-Matrix platform owners.">
      <SignInForm
        pending={pending}
        onSubmit={async ({ email, password }) => {
          setPending(true);
          const error = await signIn(email, password);
          setPending(false);
          if (error) return notifyError(error);
          navigate("/");
        }}
      />
    </AuthCard>
  );
}

/** `/`: signed in (silently from the refresh cookie after a reload). Clients and publishing come with #29. */
function Home() {
  const navigate = useNavigate();
  const [owner, setOwner] = useState<OwnerMe | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    void (async () => {
      const ok = signedIn() || (await refresh());
      const found = ok ? await loadMe() : null;
      if (live) setOwner(found);
    })();
    return () => {
      live = false;
    };
  }, []);
  if (owner === null) return <Navigate to="/login" replace />;
  if (owner === undefined) {
    return (
      <Center mih="100vh">
        <Loader aria-label="Signing in" />
      </Center>
    );
  }
  return (
    <AuthCard title={`Signed in as ${owner.name}`} description={owner.email}>
      <Text size="sm" c="dimmed">
        Clients, provisioning and publishing arrive with #29.
      </Text>
      <Button
        variant="default"
        onClick={async () => {
          await signOut();
          navigate("/login");
        }}
      >
        Sign out
      </Button>
    </AuthCard>
  );
}

/** The console's routes, inside a router (BrowserRouter in the app, MemoryRouter in tests). */
export function ConsoleApp() {
  return (
    <ZmProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<Home />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </ZmProvider>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <ConsoleApp />
    </BrowserRouter>
  );
}
