import { useEffect, useState } from "preact/hooks";
import { LabApiError, labRequest } from "./api";
import type { AuditEvent, LabBinding, LabRole, LabUser, LabUserStatus } from "./types";

type AdminData = { users: LabUser[]; bindings: LabBinding[]; events: AuditEvent[] };

export function AdminPage({ currentUser, onUserChange }: { currentUser: LabUser; onUserChange: (user: LabUser) => void }) {
  const [data, setData] = useState<AdminData | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [message, setMessage] = useState("");
  const [reassign, setReassign] = useState({ user_id: "", node_id: "", username: "", reason: "" });
  const [migration, setMigration] = useState({ user_id: "", pending_user_id: "", reason: "" });

  useEffect(() => {
    void load();
  }, []);

  async function load() {
    setState("loading");
    try {
      const [users, bindings, audit] = await Promise.all([
        labRequest<{ users: LabUser[] }>("/api/lab/admin/users"),
        labRequest<{ bindings: LabBinding[] }>("/api/lab/admin/bindings"),
        labRequest<{ events: AuditEvent[] }>("/api/lab/admin/audit-events?limit=100"),
      ]);
      setData({ users: users.users, bindings: bindings.bindings, events: audit.events });
      setState("ready");
    } catch (error) {
      setMessage(formatError(error));
      setState("error");
    }
  }

  async function updateUser(user: LabUser, role: LabRole, status: LabUserStatus) {
    const risk = role === "admin" && user.role !== "admin"
      ? "Grant administrator access to this user?"
      : user.role === "admin" && role !== "admin"
        ? "Remove administrator access from this user?"
        : status === "disabled" && user.status !== "disabled"
          ? "Disable this user and end all current bindings?"
          : null;
    if (risk && !window.confirm(risk)) return;
    try {
      const payload = await labRequest<{ user: LabUser }>(`/api/lab/admin/users/${user.id}`, {
        method: "PATCH",
        body: JSON.stringify({ role, status }),
      });
      if (user.id === currentUser.id) onUserChange(payload.user);
      setMessage("User access updated.");
      await load();
    } catch (error) {
      setMessage(formatError(error));
    }
  }

  async function verify(bindingId: string) {
    try {
      await labRequest(`/api/lab/admin/bindings/${bindingId}/verify`, { method: "POST" });
      setMessage("Binding marked as admin verified.");
      await load();
    } catch (error) {
      setMessage(formatError(error));
    }
  }

  async function submitReassignment(event: SubmitEvent) {
    event.preventDefault();
    if (!window.confirm("Transfer this node account binding? Existing conflicting bindings will be ended.")) return;
    try {
      await labRequest("/api/lab/admin/bindings/reassign", {
        method: "POST",
        body: JSON.stringify(reassign),
      });
      setReassign({ user_id: "", node_id: "", username: "", reason: "" });
      setMessage("Binding reassigned.");
      await load();
    } catch (error) {
      setMessage(formatError(error));
    }
  }

  async function submitMigration(event: SubmitEvent) {
    event.preventDefault();
    if (!window.confirm("Move the reviewed Access identity onto the selected existing user?")) return;
    try {
      await labRequest(`/api/lab/admin/users/${migration.user_id}/migrate-identity`, {
        method: "POST",
        body: JSON.stringify({ pending_user_id: migration.pending_user_id, reason: migration.reason }),
      });
      setMigration({ user_id: "", pending_user_id: "", reason: "" });
      setMessage("Access identity migrated.");
      await load();
    } catch (error) {
      setMessage(formatError(error));
    }
  }

  if (state === "loading") return <div class="lab-empty lab-page-state">Loading users and audit records...</div>;
  if (state === "error" || !data) return <div class="lab-empty lab-page-state"><p>{message || "Admin data is unavailable."}</p><button class="lab-button" type="button" onClick={() => void load()}>Retry</button></div>;

  const activeUsers = data.users.filter((user) => user.status === "active");
  const pendingUsers = data.users.filter((user) => user.status === "pending_identity_review");

  return <div class="lab-stack">
    <header class="lab-page-head"><div><p class="lab-eyebrow">Lab management</p><h2>People and access</h2><p>Manage member access, correct bindings, and review security-sensitive changes.</p></div><button class="lab-button is-quiet" type="button" onClick={() => void load()}>Refresh</button></header>
    {message ? <div class="lab-notice" role="status">{message}</div> : null}

    <section class="lab-panel" aria-labelledby="labUsersTitle">
      <div class="lab-panel-head"><div><h3 id="labUsersTitle">Users</h3><p>{activeUsers.length} active / {pendingUsers.length} pending review</p></div></div>
      <div class="lab-table-scroll"><table class="lab-table"><thead><tr><th>User</th><th>Role</th><th>Status</th><th>Bindings</th><th>Last login</th><th>Action</th></tr></thead><tbody>
        {data.users.map((user) => <UserRow key={user.id} user={user} onSave={updateUser} />)}
      </tbody></table></div>
    </section>

    <section class="lab-panel" aria-labelledby="bindingsTitle">
      <div class="lab-panel-head"><div><h3 id="bindingsTitle">Current bindings</h3><p>Active node and UID ownership records.</p></div></div>
      {data.bindings.length ? <div class="lab-table-scroll"><table class="lab-table"><thead><tr><th>User</th><th>Node</th><th>Linux user</th><th>UID</th><th>Assurance</th><th>Action</th></tr></thead><tbody>
        {data.bindings.map((binding) => <tr key={binding.id}><td>{binding.user_display_name || binding.user_email}</td><td>{binding.node_id}</td><td><code>{binding.unix_username}</code></td><td>{binding.unix_uid}</td><td>{binding.assurance.replaceAll("_", " ")}</td><td>{binding.assurance === "self_claimed" ? <button class="lab-button is-quiet" type="button" onClick={() => void verify(binding.id)}>Verify</button> : "verified"}</td></tr>)}
      </tbody></table></div> : <div class="lab-empty">No active account bindings.</div>}

      <form class="lab-admin-form" onSubmit={submitReassignment}>
        <div class="lab-form-copy"><h4>Reassign a binding</h4><p>This ends any conflicting current binding and records the reason.</p></div>
        <label><span>Target user</span><select required value={reassign.user_id} onChange={(event) => setReassign({ ...reassign, user_id: event.currentTarget.value })}><option value="">Select user</option>{activeUsers.map((user) => <option value={user.id}>{user.display_name || user.email}</option>)}</select></label>
        <label><span>Node ID</span><input required value={reassign.node_id} onInput={(event) => setReassign({ ...reassign, node_id: event.currentTarget.value })} /></label>
        <label><span>Linux username</span><input required autoComplete="off" value={reassign.username} onInput={(event) => setReassign({ ...reassign, username: event.currentTarget.value })} /></label>
        <label class="lab-form-wide"><span>Reason</span><input required value={reassign.reason} onInput={(event) => setReassign({ ...reassign, reason: event.currentTarget.value })} /></label>
        <button class="lab-button is-danger" type="submit">Reassign</button>
      </form>
    </section>

    <section class="lab-panel" aria-labelledby="identityTitle">
      <div class="lab-panel-head"><div><h3 id="identityTitle">Identity review</h3><p>Resolve a changed Access subject only after confirming it is the same person.</p></div></div>
      {pendingUsers.length ? <form class="lab-admin-form" onSubmit={submitMigration}>
        <label><span>Existing user</span><select required value={migration.user_id} onChange={(event) => setMigration({ ...migration, user_id: event.currentTarget.value })}><option value="">Select user</option>{data.users.filter((user) => user.status !== "pending_identity_review").map((user) => <option value={user.id}>{user.display_name || user.email}</option>)}</select></label>
        <label><span>Pending identity</span><select required value={migration.pending_user_id} onChange={(event) => setMigration({ ...migration, pending_user_id: event.currentTarget.value })}><option value="">Select identity</option>{pendingUsers.map((user) => <option value={user.id}>{user.email}</option>)}</select></label>
        <label class="lab-form-wide"><span>Review reason</span><input required value={migration.reason} onInput={(event) => setMigration({ ...migration, reason: event.currentTarget.value })} /></label>
        <button class="lab-button is-danger" type="submit">Migrate identity</button>
      </form> : <div class="lab-empty">No identities are waiting for review.</div>}
    </section>

    <section class="lab-panel" aria-labelledby="auditTitle">
      <div class="lab-panel-head"><div><h3 id="auditTitle">Audit events</h3><p>Newest 100 immutable application events.</p></div></div>
      <div class="lab-table-scroll"><table class="lab-table"><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Target</th><th>Details</th><th>Request</th></tr></thead><tbody>
        {data.events.map((event) => <tr key={event.id}><td>{formatDate(event.occurred_at)}</td><td>{event.actor_email || "system"}</td><td><code>{event.action}</code></td><td>{event.target_type}</td><td class="lab-details-cell">{formatDetails(event.details)}</td><td><code>{event.request_id.slice(0, 8)}</code></td></tr>)}
      </tbody></table></div>
    </section>
  </div>;
}

function UserRow({ user, onSave }: { user: LabUser; onSave: (user: LabUser, role: LabRole, status: LabUserStatus) => Promise<void> }) {
  const [role, setRole] = useState<LabRole>(user.role);
  const [status, setStatus] = useState<LabUserStatus>(user.status);
  const changed = role !== user.role || status !== user.status;
  return <tr><td><strong>{user.display_name || user.email}</strong>{user.display_name ? <small>{user.email}</small> : null}</td><td><label class="sr-only" for={`role-${user.id}`}>Role for {user.email}</label><select id={`role-${user.id}`} value={role} onChange={(event) => setRole(event.currentTarget.value as LabRole)}><option value="viewer">View only</option><option value="member">Member</option><option value="admin">Lab administrator</option></select></td><td><label class="sr-only" for={`status-${user.id}`}>Status for {user.email}</label><select id={`status-${user.id}`} value={status} onChange={(event) => setStatus(event.currentTarget.value as LabUserStatus)}><option value="active">Active</option><option value="disabled">Disabled</option><option value="pending_identity_review">Pending review</option></select></td><td>{user.active_binding_count || 0}</td><td>{formatDate(user.last_login_at)}</td><td><button class="lab-button is-quiet" type="button" disabled={!changed} onClick={() => void onSave(user, role, status)}>Save</button></td></tr>;
}

function formatDate(timestamp: number) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "short", timeStyle: "short" }).format(timestamp * 1000);
}

function formatDetails(details: Record<string, unknown>) {
  return Object.entries(details).map(([key, value]) => `${key}: ${String(value)}`).join(" / ") || "none";
}

function formatError(error: unknown) {
  if (error instanceof LabApiError) return `${error.message.replaceAll("_", " ")}${error.requestId ? ` / request ${error.requestId}` : ""}`;
  return "The admin request could not be completed.";
}
