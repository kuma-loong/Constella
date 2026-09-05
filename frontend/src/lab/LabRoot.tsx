import { useEffect, useMemo, useState } from "preact/hooks";
import App from "../App";
import type { AppExtension, AppRoute, ExtensionRoute } from "../app-extension";
import { Icon } from "../components";
import { LAB_HEADERS, LabApiError, labRequest } from "./api";
import { AccountPage } from "./AccountPage";
import { AdminPage } from "./AdminPage";
import type { LabUser } from "./types";

export function LabRoot() {
  const [user, setUser] = useState<LabUser | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    document.documentElement.dataset.edition = "lab";
    void loadUser();
    return () => {
      delete document.documentElement.dataset.edition;
    };
  }, []);

  async function loadUser() {
    try {
      const payload = await labRequest<{ user: LabUser }>("/api/lab/me");
      setUser(payload.user);
      setError(null);
    } catch (caught) {
      if (caught instanceof LabApiError && caught.status === 403) {
        setError("Your Lab account is disabled or waiting for identity review. Contact an administrator.");
      } else {
        setError("Constella Lab could not load your signed-in identity.");
      }
    }
  }

  const extension = useMemo<AppExtension | undefined>(() => {
    if (!user) return undefined;
    return {
      parseRoute,
      isPath: (pathname) => pathname === "/account" || pathname === "/admin",
      renderNavigation: (route) => <LabNavigation route={route} admin={user.role === "admin"} />,
      renderHeaderActions: () => <UserMenu user={user} />,
      renderPage: (route) => route.key === "admin" && user.role === "admin"
        ? <AdminPage currentUser={user} onUserChange={setUser} />
        : <AccountPage user={user} onUserChange={setUser} />,
      canManageSettings: user.role === "admin",
      requestHeaders: LAB_HEADERS,
      onAuthenticationRequired: () => window.location.reload(),
    };
  }, [user]);

  if (!user) {
    return <main class="lab-auth-state" id="mainContent">
      <img src="/logo.svg?v=20260825" alt="" />
      <p class="lab-eyebrow">Constella Lab</p>
      <h1>{error ? "Access unavailable" : "Loading your identity"}</h1>
      <p>{error || "Verifying the Cloudflare Access session..."}</p>
      {error ? <button class="lab-button is-primary" type="button" onClick={() => void loadUser()}>Retry</button> : <span class="lab-loading-line" />}
    </main>;
  }

  return <App extension={extension} />;
}

function parseRoute(pathname: string): ExtensionRoute | null {
  if (pathname === "/account") return { kind: "extension", key: "account" };
  if (pathname === "/admin") return { kind: "extension", key: "admin" };
  return null;
}

function LabNavigation({ route, admin }: { route: AppRoute; admin: boolean }) {
  return <>
    <a class={`nav-link ${route.kind === "extension" && route.key === "account" ? "is-active" : ""}`} aria-current={route.kind === "extension" && route.key === "account" ? "page" : undefined} href="/account"><Icon name="users" /><span>My account</span></a>
    {admin ? <a class={`nav-link ${route.kind === "extension" && route.key === "admin" ? "is-active" : ""}`} aria-current={route.kind === "extension" && route.key === "admin" ? "page" : undefined} href="/admin"><Icon name="database" /><span>Admin</span></a> : null}
  </>;
}

function UserMenu({ user }: { user: LabUser }) {
  const label = user.display_name || user.email.split("@")[0];
  return <details class="lab-user-menu">
    <summary aria-label={`Signed in as ${user.email}`} title={user.email}><span class="lab-user-avatar">{label.slice(0, 2).toUpperCase()}</span><span class="lab-user-summary"><strong>{label}</strong><small>{user.role}</small></span></summary>
    <div class="lab-user-popover"><strong>{user.display_name || user.email}</strong>{user.display_name ? <span>{user.email}</span> : null}<span class="lab-status-label">{user.role}</span><a href="/account">My account</a>{user.role === "admin" ? <a href="/admin">Administration</a> : null}<a href="/cdn-cgi/access/logout">Sign out</a></div>
  </details>;
}
