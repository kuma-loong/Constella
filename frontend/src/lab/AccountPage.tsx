import { useEffect, useState } from "preact/hooks";
import { LabApiError, labRequest } from "./api";
import { AccountBindingForm } from "./AccountBindingForm";
import { labRoleLabel, type LabUser } from "./types";

export function AccountPage({ user, onUserChange }: { user: LabUser; onUserChange: (user: LabUser) => void }) {
  const [message, setMessage] = useState("");
  const [displayName, setDisplayName] = useState(user.display_name || "");
  const [profileState, setProfileState] = useState<"ready" | "saving">("ready");
  const [profileMessage, setProfileMessage] = useState("");

  useEffect(() => {
    setDisplayName(user.display_name || "");
  }, [user.display_name]);

  async function saveProfile(event: SubmitEvent) {
    event.preventDefault();
    setProfileState("saving");
    setProfileMessage("");
    try {
      const payload = await labRequest<{ user: LabUser }>("/api/lab/me", {
        method: "PATCH",
        body: JSON.stringify({ display_name: displayName.trim() || null }),
      });
      onUserChange(payload.user);
      setProfileMessage("Display name updated.");
    } catch (error) {
      setProfileMessage(formatError(error));
    } finally {
      setProfileState("ready");
    }
  }

  async function endBinding(bindingId: string) {
    if (!window.confirm("Disconnect this node account? Historical ownership records will be retained.")) {
      return;
    }
    try {
      await labRequest(`/api/lab/account-bindings/${encodeURIComponent(bindingId)}`, { method: "DELETE" });
      const payload = await labRequest<{ user: LabUser }>("/api/lab/me");
      onUserChange(payload.user);
      setMessage("Node account disconnected.");
    } catch (error) {
      setMessage(formatError(error));
    }
  }

  return (
    <div class="lab-stack">
      <header class="lab-page-head">
        <div>
          <a class="lab-eyebrow" href="/profile">Back to your activity</a>
          <h2>Account settings</h2>
          <p>Choose how you appear in Constella and connect your GPU node accounts.</p>
        </div>
        <span class="lab-role-label">{labRoleLabel(user.role)}</span>
      </header>

      <section class="lab-panel" aria-labelledby="profileTitle">
        <div class="lab-panel-head">
          <div>
            <h3 id="profileTitle">Display name</h3>
            <p>This name appears in the header and member management. It does not change any Linux username.</p>
          </div>
        </div>
        <form class="lab-profile-form" onSubmit={saveProfile}>
          <label>
            <span>Name</span>
            <input
              value={displayName}
              maxLength={80}
              autoComplete="name"
              placeholder="How others should see you"
              onInput={(event) => {
                setDisplayName(event.currentTarget.value);
                setProfileMessage("");
              }}
            />
          </label>
          <button
            class="lab-button is-primary"
            type="submit"
            disabled={profileState === "saving" || displayName.trim() === (user.display_name || "")}
          >
            {profileState === "saving" ? "Saving..." : "Save name"}
          </button>
          {profileMessage ? <span class="lab-profile-message" role="status">{profileMessage}</span> : null}
        </form>
      </section>

      <section class="lab-panel" aria-labelledby="currentBindingsTitle">
        <div class="lab-panel-head">
          <div>
            <h3 id="currentBindingsTitle">Connected node accounts</h3>
            <p>Task ownership is matched by node and numeric UID.</p>
          </div>
        </div>
        {user.bindings.filter((binding) => !binding.valid_to).length ? (
          <div class="lab-table-scroll">
            <table class="lab-table">
              <thead><tr><th>Node</th><th>Linux user</th><th>UID</th><th>Verification</th><th>Since</th><th><span class="sr-only">Action</span></th></tr></thead>
              <tbody>
                {user.bindings.filter((binding) => !binding.valid_to).map((binding) => (
                  <tr key={binding.id}>
                    <td>{binding.node_id}</td><td><code>{binding.unix_username}</code></td><td>{binding.unix_uid}</td>
                    <td><span class="lab-status-label">{assuranceLabel(binding.assurance)}</span></td>
                    <td>{formatDate(binding.valid_from)}</td>
                    <td class="lab-action-cell"><button class="lab-button is-quiet" type="button" onClick={() => void endBinding(binding.id)}>Disconnect</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <div class="lab-empty">No node accounts connected.</div>}
        {message ? <div class={`lab-notice ${message.includes("disconnected") ? "is-success" : "is-warning"}`} role="status">{message}</div> : null}
      </section>

      <AccountBindingForm user={user} onUserChange={onUserChange} />
    </div>
  );
}

function assuranceLabel(value: string) {
  if (value === "admin_verified") return "Admin verified";
  if (value === "node_verified") return "Node verified";
  return "Not verified";
}

function formatDate(timestamp: number) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(timestamp * 1000);
}

function formatError(error: unknown) {
  if (error instanceof LabApiError) {
    return `${error.message.replaceAll("_", " ")}${error.requestId ? ` / request ${error.requestId}` : ""}`;
  }
  return "The request could not be completed.";
}
