import { useEffect, useMemo, useState } from "preact/hooks";
import { LabApiError, labRequest } from "./api";
import { labRoleLabel, type AccountResult, type BindingNode, type LabUser } from "./types";

type AccountDraft = { selected: boolean; username: string };

export function AccountPage({ user, onUserChange }: { user: LabUser; onUserChange: (user: LabUser) => void }) {
  const canBind = user.role === "member" || user.role === "admin";
  const [nodes, setNodes] = useState<BindingNode[]>([]);
  const [drafts, setDrafts] = useState<Record<string, AccountDraft>>({});
  const [sharedUsername, setSharedUsername] = useState("");
  const [results, setResults] = useState<AccountResult[] | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "previewing" | "binding" | "error">("loading");
  const [message, setMessage] = useState("");
  const [displayName, setDisplayName] = useState(user.display_name || "");
  const [profileState, setProfileState] = useState<"ready" | "saving">("ready");
  const [profileMessage, setProfileMessage] = useState("");

  useEffect(() => {
    if (canBind) {
      void loadNodes();
    } else {
      setState("ready");
    }
  }, [canBind]);

  useEffect(() => {
    setDisplayName(user.display_name || "");
  }, [user.display_name]);

  const activeNodeIds = useMemo(
    () => new Set(user.bindings.filter((binding) => !binding.valid_to).map((binding) => binding.node_id)),
    [user.bindings],
  );
  const selectedAccounts = nodes
    .filter((node) => drafts[node.node_id]?.selected)
    .map((node) => ({ node_id: node.node_id, username: drafts[node.node_id]?.username.trim() || "" }));

  async function loadNodes() {
    setState("loading");
    try {
      const payload = await labRequest<{ nodes: BindingNode[] }>("/api/lab/account-binding-nodes");
      setNodes(payload.nodes);
      setDrafts((previous) => {
        const next = { ...previous };
        for (const node of payload.nodes) {
          next[node.node_id] ||= { selected: false, username: "" };
        }
        return next;
      });
      setState("ready");
    } catch (error) {
      setMessage(formatError(error));
      setState("error");
    }
  }

  function updateDraft(nodeId: string, update: Partial<AccountDraft>) {
    setDrafts((previous) => ({
      ...previous,
      [nodeId]: { ...previous[nodeId], ...update },
    }));
    setResults(null);
    setMessage("");
  }

  function fillSelected() {
    const username = sharedUsername.trim();
    if (!username) {
      return;
    }
    setDrafts((previous) =>
      Object.fromEntries(
        Object.entries(previous).map(([nodeId, draft]) => [
          nodeId,
          draft.selected ? { ...draft, username } : draft,
        ]),
      ),
    );
    setResults(null);
  }

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

  async function preview() {
    if (!selectedAccounts.length || selectedAccounts.some((account) => !account.username)) {
      setMessage("Select at least one available node and enter every Linux username.");
      return;
    }
    setState("previewing");
    setMessage("");
    try {
      const payload = await labRequest<{ results: AccountResult[] }>("/api/lab/account-lookups/batch", {
        method: "POST",
        body: JSON.stringify({ accounts: selectedAccounts }),
      });
      setResults(payload.results);
      setState("ready");
    } catch (error) {
      setMessage(formatError(error));
      setState("ready");
    }
  }

  async function bind() {
    if (!results?.length || results.some((result) => !result.bindable)) {
      return;
    }
    setState("binding");
    setMessage("");
    try {
      await labRequest("/api/lab/account-bindings/batch", {
        method: "POST",
        body: JSON.stringify({ accounts: selectedAccounts }),
      });
      const payload = await labRequest<{ user: LabUser }>("/api/lab/me");
      onUserChange(payload.user);
      setResults(null);
      setDrafts((previous) =>
        Object.fromEntries(Object.entries(previous).map(([key, draft]) => [key, { ...draft, selected: false }])),
      );
      setMessage("Node accounts connected.");
      setState("ready");
    } catch (error) {
      setMessage(formatError(error));
      setState("ready");
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
      await loadNodes();
    } catch (error) {
      setMessage(formatError(error));
    }
  }

  return (
    <div class="lab-stack">
      <header class="lab-page-head">
        <div>
          <p class="lab-eyebrow">Profile</p>
          <h2>Your access</h2>
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
        ) : <div class="lab-empty">No node accounts connected yet. Select one or more nodes below.</div>}
      </section>

      {!canBind ? <section class="lab-panel" aria-labelledby="newBindingsTitle">
        <div class="lab-panel-head"><div><h3 id="newBindingsTitle">Connect node accounts</h3><p>Your view-only role does not permit Linux account connections.</p></div></div>
        <div class="lab-empty">Ask a Lab administrator to grant member access before claiming a node account.</div>
      </section> : <section class="lab-panel" aria-labelledby="newBindingsTitle">
        <div class="lab-panel-head">
          <div><h3 id="newBindingsTitle">Connect node accounts</h3><p>Constella checks every selected node before connecting the accounts together.</p></div>
          <button class="lab-button is-quiet" type="button" onClick={() => void loadNodes()} disabled={state === "loading"}>Refresh nodes</button>
        </div>

        <div class="lab-fill-row">
          <label><span>Username for selected nodes</span><input value={sharedUsername} onInput={(event) => setSharedUsername(event.currentTarget.value)} placeholder="alice" autoComplete="off" /></label>
          <button class="lab-button is-quiet" type="button" onClick={fillSelected}>Fill selected</button>
        </div>

        {state === "loading" ? <div class="lab-empty">Loading node capabilities...</div> : nodes.length ? (
          <div class="lab-table-scroll">
            <table class="lab-table lab-binding-table">
              <thead><tr><th>Select</th><th>Node</th><th>Status</th><th>Linux username</th><th>Lookup</th></tr></thead>
              <tbody>{nodes.map((node) => {
                const unavailable = !node.connected || !node.binding_supported || activeNodeIds.has(node.node_id);
                const result = results?.find((item) => item.node_id === node.node_id);
                return <tr key={node.node_id} class={unavailable ? "is-disabled" : ""}>
                  <td><input aria-label={`Select ${node.node_id}`} type="checkbox" checked={drafts[node.node_id]?.selected || false} disabled={unavailable} onChange={(event) => updateDraft(node.node_id, { selected: event.currentTarget.checked })} /></td>
                  <td><strong>{node.node_id}</strong><small>{node.hostname}</small></td>
                  <td>{activeNodeIds.has(node.node_id) ? "already bound" : !node.connected ? "offline" : !node.binding_supported ? "agent upgrade required" : node.status}</td>
                  <td><label class="sr-only" for={`username-${node.node_id}`}>Linux username on {node.node_id}</label><input id={`username-${node.node_id}`} value={drafts[node.node_id]?.username || ""} disabled={unavailable || !drafts[node.node_id]?.selected} onInput={(event) => updateDraft(node.node_id, { username: event.currentTarget.value })} autoComplete="off" /></td>
                  <td>{state === "previewing" && drafts[node.node_id]?.selected ? "checking" : result?.bindable ? <span class="lab-result is-ok">{result.canonical_username} / UID {result.uid}</span> : result ? <span class="lab-result is-error">{resultError(result.error)}</span> : "not checked"}</td>
                </tr>;
              })}</tbody>
            </table>
          </div>
        ) : <div class="lab-empty">No monitored nodes are available yet.</div>}

        {message ? <div class={`lab-notice ${message.includes("connected") ? "is-success" : "is-warning"}`} role="status">{message}</div> : null}
        {results?.length && results.every((result) => result.bindable) ? (
          <div class="lab-confirm">
            <div><strong>Confirm account connections</strong><p>The accounts exist, but ownership has not been proven. They are only used for task attribution and personal statistics.</p></div>
            <button class="lab-button is-primary" type="button" disabled={state === "binding"} onClick={() => void bind()}>{state === "binding" ? "Connecting..." : `Connect ${results.length} account${results.length === 1 ? "" : "s"}`}</button>
          </div>
        ) : null}
        <div class="lab-panel-actions"><button class="lab-button is-primary" type="button" disabled={state === "previewing" || state === "binding" || !selectedAccounts.length} onClick={() => void preview()}>{state === "previewing" ? "Checking nodes..." : "Check selected accounts"}</button></div>
      </section>}
    </div>
  );
}

function resultError(error?: string) {
  if (error === "node_offline") return "node offline";
  if (error === "account_lookup_unsupported") return "agent upgrade required";
  if (error === "account_lookup_unavailable") return "lookup unavailable";
  return "account unavailable";
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
