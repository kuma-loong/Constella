export type LabRole = "viewer" | "member" | "admin";
export type LabUserStatus = "active" | "disabled" | "pending_identity_review";

export type LabBinding = {
  id: string;
  user_id: string;
  user_email?: string;
  user_display_name?: string | null;
  node_id: string;
  unix_uid: number;
  unix_gid?: number | null;
  unix_username: string;
  assurance: "self_claimed" | "admin_verified" | "node_verified";
  status: "active" | "revoked" | "reassigned";
  valid_from: number;
  valid_to?: number | null;
};

export type LabUser = {
  id: string;
  email: string;
  display_name?: string | null;
  role: LabRole;
  status: LabUserStatus;
  created_at: number;
  updated_at: number;
  last_login_at: number;
  active_binding_count?: number;
  bindings: LabBinding[];
};

export type BindingNode = {
  node_id: string;
  hostname: string;
  status: string;
  connected: boolean;
  binding_supported: boolean;
};

export type AccountResult = {
  node_id: string;
  bindable: boolean;
  canonical_username?: string;
  uid?: number;
  gid?: number;
  error?: string;
};

export type AuditEvent = {
  id: number;
  occurred_at: number;
  actor_email?: string | null;
  action: string;
  target_type: string;
  target_id?: string | null;
  request_id: string;
  details: Record<string, unknown>;
};
