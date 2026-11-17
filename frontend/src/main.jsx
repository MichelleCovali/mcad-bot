import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.jsx";
import "./styles.css";

const AdminWorkspace = React.lazy(() => import("./components/AdminWorkspace.jsx").then((module) => ({ default: module.AdminWorkspace })));
const configuredAdminHost = import.meta.env.VITE_ADMIN_HOST?.trim();
let adminHost;
try {
  if (configuredAdminHost) adminHost = new URL(configuredAdminHost.includes("://") ? configuredAdminHost : `https://${configuredAdminHost}`).hostname;
} catch { /* An invalid optional hostname leaves /admin available. */ }
const adminRoute = /^\/admin(?:\/|$)/.test(window.location.pathname)
  || window.location.hostname === adminHost;

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    {adminRoute ? <React.Suspense fallback={<main className="workspace-loading" role="status">Opening MCAD workspace…</main>}><AdminWorkspace /></React.Suspense> : <App />}
  </React.StrictMode>,
);
