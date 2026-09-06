import { useEffect, useRef } from "preact/hooks";
import { applyDocumentTheme, readThemeMode } from "../theme";
import { AccountBindingForm } from "./AccountBindingForm";
import type { LabUser } from "./types";

export function OnboardingDialog({ user, onUserChange }: { user: LabUser; onUserChange: (user: LabUser) => void }) {
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
        <p>Connect at least one Linux account so Constella can attribute GPU work to you.</p>
      </div>
    </header>
    <AccountBindingForm user={user} onUserChange={onUserChange} mode="onboarding" />
    <footer class="lab-onboarding-foot">
      Signed in as <strong>{user.email}</strong>. Setup finishes only after an account is connected.
    </footer>
  </dialog>;
}
