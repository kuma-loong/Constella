import { useEffect, useRef, useState } from "preact/hooks";
import { applyDocumentTheme, readThemeMode } from "../theme";
import { AccountBindingForm } from "./AccountBindingForm";
import { labRequest } from "./api";
import type { LabUser } from "./types";

export function OnboardingDialog({ user, onUserChange }: { user: LabUser; onUserChange: (user: LabUser) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function skipBinding() {
    setBusy(true); setError("");
    try {
      const result = await labRequest<{ user: LabUser }>("/api/lab/me/readonly", { method: "POST" });
      window.history.replaceState(null, "", "/overview");
      onUserChange(result.user);
    } catch {
      setError("Could not switch to read-only access. Refresh the page and try again.");
    } finally { setBusy(false); }
  }
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const colorScheme = window.matchMedia("(prefers-color-scheme: dark)");
    const applyTheme = () => applyDocumentTheme(readThemeMode(), colorScheme.matches);
    applyTheme();
    colorScheme.addEventListener("change", applyTheme);
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) dialog.showModal();
    return () => {
      colorScheme.removeEventListener("change", applyTheme);
      if (dialog?.open) dialog.close();
    };
  }, []);

  return <dialog
    ref={dialogRef}
    class="lab-onboarding-dialog"
    aria-labelledby="onboardingTitle"
    onCancel={(event) => event.preventDefault()}
  >
    <header class="lab-onboarding-head">
      <img src="/logo.svg?v=20260825" alt="" />
      <div>
        <p class="lab-eyebrow">Constella Lab</p>
        <h1 id="onboardingTitle">Finish setting up your account</h1>
        <p>Connect a Linux account to see your personal activity, or continue with read-only access.</p>
      </div>
    </header>
    <AccountBindingForm user={user} onUserChange={onUserChange} mode="onboarding" />
    <footer class="lab-onboarding-foot">
      <span>Signed in as <strong>{user.email}</strong>.</span>
      {user.role === "member" && <div><p>No server account? Browse monitoring pages without a personal dashboard.</p><button class="lab-button" disabled={busy} onClick={() => void skipBinding()}>{busy ? "Please wait…" : "Continue with read-only access"}</button>{error && <p role="alert">{error}</p>}</div>}
    </footer>
  </dialog>;
}
