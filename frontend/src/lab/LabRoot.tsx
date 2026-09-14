import { useEffect, useMemo, useState } from "preact/hooks";
import App from "../App";
import { AUTHENTICATION_REQUIRED, requestAuthentication } from "../requests";
import type { AppExtension, AppRoute, ExtensionRoute } from "../app-extension";
import { Icon } from "../components";
import { LAB_HEADERS, LabApiError, labRequest } from "./api";
import { ProfilePage } from "./ProfilePage";
import { AccountPage } from "./AccountPage";
import { AdminPage } from "./AdminPage";
import { OnboardingDialog } from "./OnboardingDialog";
import { labRoleLabel, type LabUser } from "./types";

export function LabRoot() {
  const [authenticationRequired, setAuthenticationRequired] = useState(false);
  const [user, setUser] = useState<LabUser | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    document.documentElement.dataset.edition = "lab";
    const expired = () => setAuthenticationRequired(true);
    window.addEventListener(AUTHENTICATION_REQUIRED, expired);
    void loadUser();
    return () => {
      window.removeEventListener(AUTHENTICATION_REQUIRED, expired);
      delete document.documentElement.dataset.edition;
    };
  }, []);

  async function loadUser() {
    try {
      const payload = await labRequest<{ user: LabUser }>("/api/lab/me");
      const canonicalPath = payload.user.role === "viewer" && ["/profile", "/profile/settings", "/account", "/management", "/admin"].includes(window.location.pathname) ? "/overview" : canonicalLabPath(window.location.pathname, payload.user.role === "admin");
      if (canonicalPath && canonicalPath !== window.location.pathname) {
        window.history.replaceState(null, "", canonicalPath);
      }
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
    const canManageLab = user.role === "admin";
    return {
      parseRoute: (pathname) => parseRoute(pathname, canManageLab),
      isPath: (pathname) => ["/profile", "/profile/settings", "/management", "/account", "/admin"].includes(pathname),
      renderNavigation: (route) => user.role === "viewer" ? null : <LabNavigation route={route} canManageLab={canManageLab} />,
      renderHeaderActions: () => <UserMenu user={user} />,
      renderPage: (route, snapshot) => route.key === "management" && canManageLab
        ? <AdminPage currentUser={user} onUserChange={setUser} />
        : user.role === "viewer" ? <p>Read-only access does not include a personal dashboard.</p>
        : route.key === "account" ? <AccountPage user={user} onUserChange={setUser} />
        : <ProfilePage user={user} snapshot={snapshot} />,
      canManageSettings: canManageLab,
      showNodeProcessDetails: false,
      requestHeaders: LAB_HEADERS,
      onAuthenticationRequired: requestAuthentication,
    };
  }, [user]);

  if (authenticationRequired) {
    return <main class="lab-auth-state" id="mainContent">
      <p class="lab-eyebrow">Constella Lab</p>
      <h1>Sign in again</h1>
      <p>Your session needs verification. Reconnect to continue.</p>
      <button class="lab-button is-primary" type="button" onClick={() => window.location.reload()}>Reconnect</button>
    </main>;
  }

  if (!user) {
    return <main class="lab-auth-state" id="mainContent">
      <img src="/logo.svg?v=20260825" alt="" />
      <p class="lab-eyebrow">Constella Lab</p>
      <h1>{error ? "Access unavailable" : "Loading your identity"}</h1>
      <p>{error || "Verifying the Cloudflare Access session..."}</p>
      {error ? <button class="lab-button is-primary" type="button" onClick={() => void loadUser()}>Retry</button> : <span class="lab-loading-line" />}
    </main>;
  }

  const needsOnboarding = user.onboarding_completed_at == null && user.role !== "viewer";
  if (needsOnboarding) {
    return <OnboardingDialog user={user} onUserChange={setUser} />;
  }
  return <App extension={extension} />;
}

function parseRoute(pathname: string, canManageLab: boolean): ExtensionRoute | null {
  if (pathname === "/profile/settings" || pathname === "/account") return { kind: "extension", key: "account" };
  if (pathname === "/profile") {
    return { kind: "extension", key: "profile" };
  }
  if (pathname === "/management" || pathname === "/admin") {
    return { kind: "extension", key: canManageLab ? "management" : "profile" };
  }
  return null;
}

function canonicalLabPath(pathname: string, canManageLab: boolean): string | null {
  if (pathname === "/account") return "/profile/settings";
  if (pathname === "/admin") return canManageLab ? "/management" : "/profile";
  if (pathname === "/management" && !canManageLab) return "/profile";
  return null;
}

function LabNavigation({ route, canManageLab }: { route: AppRoute; canManageLab: boolean }) {
  return <>
    <a class={`nav-link ${route.kind === "extension" && ["profile", "account"].includes(route.key) ? "is-active" : ""}`} aria-current={route.kind === "extension" && ["profile", "account"].includes(route.key) ? "page" : undefined} href="/profile"><Icon name="users" /><span>Profile</span></a>
    {canManageLab ? <a class={`nav-link ${route.kind === "extension" && route.key === "management" ? "is-active" : ""}`} aria-current={route.kind === "extension" && route.key === "management" ? "page" : undefined} href="/management"><Icon name="database" /><span>Lab management</span></a> : null}
  </>;
}

function UserMenu({ user }: { user: LabUser }) {
  const label = user.display_name || user.email.split("@")[0];
  return <details class="lab-user-menu">
    <summary aria-label={`Signed in as ${user.email}`} title={user.email}><span class="lab-user-avatar">{label.slice(0, 2).toUpperCase()}</span><span class="lab-user-summary"><strong>{label}</strong><small>{labRoleLabel(user.role)}</small></span></summary>
    <div class="lab-user-popover"><strong>{user.display_name || user.email}</strong>{user.display_name ? <span>{user.email}</span> : null}<span class="lab-status-label">{labRoleLabel(user.role)}</span>{user.role !== "viewer" && <a href="/profile">Profile</a>}{user.role === "admin" ? <a href="/management">Lab management</a> : null}<a href="/cdn-cgi/access/logout">Sign out</a></div>
  </details>;
}
