import { Center, Loader, Menu } from "@mantine/core";
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppFrame, AuthCard, notifyError, SignInForm, ZmProvider } from "@zm/ui";
import { useState } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes, useNavigate } from "react-router";
import { ClientPage, ClientsList, NewClient } from "./Clients";
import { currentOwner, signIn, signOut } from "./session";

/** `/login`: platform owners only (#22). */
function Login() {
  const navigate = useNavigate();
  const queries = useQueryClient();
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
          queries.clear(); // e.g. a "signed out" answer cached by an earlier visit
          navigate("/");
        }}
      />
    </AuthCard>
  );
}

/** Everything behind sign-in (silently from the refresh cookie after a reload), in the app frame. */
function SignedIn() {
  const navigate = useNavigate();
  const queries = useQueryClient();
  const owner = useQuery({ queryKey: ["owner"], queryFn: currentOwner });
  if (owner.data === null) return <Navigate to="/login" replace />;
  if (!owner.data) {
    return (
      <Center mih="100vh">
        <Loader aria-label="Signing in" />
      </Center>
    );
  }
  return (
    <AppFrame
      clientName="Z-Matrix owner console"
      nav={[{ key: "clients", label: "Clients" }]}
      activeKey="clients"
      onNavigate={() => navigate("/")}
      user={{ name: owner.data.name, email: owner.data.email }}
      userMenu={
        <Menu.Item
          onClick={async () => {
            await signOut();
            queries.clear();
            navigate("/login");
          }}
        >
          Sign out
        </Menu.Item>
      }
    >
      <Outlet />
    </AppFrame>
  );
}

/** The console's routes, inside a router (BrowserRouter in the app, MemoryRouter in tests). */
export function ConsoleApp() {
  const [queries] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }));
  return (
    <QueryClientProvider client={queries}>
      <ZmProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<SignedIn />}>
            <Route path="/" element={<ClientsList />} />
            <Route path="/clients/new" element={<NewClient />} />
            <Route path="/clients/:id" element={<ClientPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </ZmProvider>
    </QueryClientProvider>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <ConsoleApp />
    </BrowserRouter>
  );
}
