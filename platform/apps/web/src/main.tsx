import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";

// /dev/ui and /dev/layouts exist only on the dev server: in production builds this branch and the
// pages' chunks are dropped.
const Dev = !import.meta.env.DEV
  ? null
  : window.location.pathname.startsWith("/dev/ui")
    ? lazy(() => import("./dev/DevUi"))
    : window.location.pathname.startsWith("/dev/layouts")
      ? lazy(() => import("./dev/DevLayouts"))
      : null;

const root = document.getElementById("root");
if (!root) throw new Error("#root missing");
createRoot(root).render(
  <StrictMode>
    {Dev ? (
      <Suspense fallback={null}>
        <Dev />
      </Suspense>
    ) : (
      <App />
    )}
  </StrictMode>,
);
