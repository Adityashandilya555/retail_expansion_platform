import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";

// /dev/ui exists only on the dev server: in production builds this branch and its chunk are dropped.
const DevUi = import.meta.env.DEV ? lazy(() => import("./dev/DevUi")) : null;
const showDevUi = DevUi !== null && window.location.pathname.startsWith("/dev/ui");

const root = document.getElementById("root");
if (!root) throw new Error("#root missing");
createRoot(root).render(
  <StrictMode>
    {showDevUi && DevUi ? (
      <Suspense fallback={null}>
        <DevUi />
      </Suspense>
    ) : (
      <App />
    )}
  </StrictMode>,
);
