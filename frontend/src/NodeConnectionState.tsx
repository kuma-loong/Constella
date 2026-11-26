import { fmtDuration, formatTime } from "./format";
import type { NodeSnapshot } from "./types";

export function lastSampleAge(node: NodeSnapshot, now = Date.now() / 1000) {
  return Number.isFinite(node.sampled_at) && node.sampled_at > 0
    ? `${fmtDuration(Math.max(0, now - node.sampled_at))} ago`
    : "unavailable";
}

export function NodeStatusSeal({ status }: { status: "offline" | "error" }) {
  const label = status.toUpperCase();
  return (
    <span class="node-status-seal" aria-hidden="true">
      {["descending", "ascending"].map(direction => (
        <span class={`node-status-tape is-${direction}`} key={direction}>
          <span class="node-status-tape-labels">
            {Array.from({ length: 10 }, (_, index) => <span key={index}>{label}</span>)}
          </span>
        </span>
      ))}
    </span>
  );
}

export function NodeConnectionNotice({ node, now }: { node: NodeSnapshot | null; now?: number }) {
  if (!node || (node.status !== "offline" && node.status !== "stale")) return null;
  const offline = node.status === "offline";
  const sampledAt = Number.isFinite(node.sampled_at) && node.sampled_at > 0 ? node.sampled_at : null;
  return (
    <aside class={`node-connection-notice is-${node.status}`} role="status" aria-label="Node connection status">
      <div class="node-connection-state">
        <i data-lucide="alert-triangle" aria-hidden="true" />
        <strong>{offline ? "OFFLINE" : "DELAYED"}</strong>
      </div>
      <div class="node-connection-copy">
        <strong>{offline ? "Live readings unavailable" : "Waiting for fresh telemetry"}</strong>
        <p>{offline
          ? "The watchdog checks periodically and attempts recovery. If the node remains offline, contact an administrator."
          : "Updates are overdue. The last readings remain visible while waiting for the agent."}</p>
      </div>
      <div class="node-connection-time">
        <span>Last sample · Asia/Shanghai</span>
        <strong>{sampledAt === null ? "Unavailable" : formatTime(sampledAt)}</strong>
        <small>{lastSampleAge(node, now)}</small>
      </div>
    </aside>
  );
}
