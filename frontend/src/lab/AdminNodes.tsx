import { useEffect, useRef, useState } from "preact/hooks";
import { LabApiError, labRequest } from "./api";

type ControlNode = {
  node_id: string; hostname: string; status: string; connected: boolean;
  restart_supported: boolean; manager_host: boolean; agent_version: string | null;
  agent_instance_id: string | null;
};
type Restart = { instance: string | null; deadline: number; state: "sending" | "waiting" | "done" | "unconfirmed" | "failed" };

export function AdminNodes() {
  const [nodes, setNodes] = useState<ControlNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [restarts, setRestarts] = useState<Record<string, Restart>>({});
  const [cooldowns, setCooldowns] = useState<Record<string, number>>({});
  const [now, setNow] = useState(Date.now());
  const mounted = useRef(true);
  const request = useRef<AbortController | null>(null);

  async function load() {
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    try {
      const data = await labRequest<{ nodes: ControlNode[] }>("/api/lab/admin/nodes", { signal: controller.signal });
      if (!mounted.current || controller.signal.aborted) return;
      setNodes(data.nodes);
      setError("");
      setRestarts(previous => Object.fromEntries(Object.entries(previous).map(([id, restart]) => {
        if (restart.state !== "waiting" && restart.state !== "unconfirmed") return [id, restart];
        const node = data.nodes.find(node => node.node_id === id);
        const reconnected = node?.connected && node.agent_instance_id && node.agent_instance_id !== restart.instance;
        return [id, { ...restart, state: reconnected ? "done" : Date.now() >= restart.deadline ? "unconfirmed" : "waiting" }];
      })));
    } catch (caught) {
      if (mounted.current && !controller.signal.aborted) setError("Could not refresh node status. Retry to check connections.");
    } finally {
      if (mounted.current && !controller.signal.aborted) setLoading(false);
    }
  }

  const waiting = Object.values(restarts).some(restart => restart.state === "waiting");
  const cooling = Object.values(cooldowns).some(deadline => deadline > now);
  useEffect(() => {
    mounted.current = true;
    void load();
    return () => { mounted.current = false; request.current?.abort(); };
  }, []);
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(timer);
  }, [waiting]);
  useEffect(() => {
    if (!cooling) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [cooling]);

  async function restart(node: ControlNode) {
    if (!window.confirm(`Restart the Constella agent on ${node.node_id}? Telemetry will pause briefly. GPU jobs and the manager continue running.`)) return;
    setRestarts(previous => ({ ...previous, [node.node_id]: { instance: node.agent_instance_id, deadline: Date.now() + 30_000, state: "sending" } }));
    setCooldowns(previous => ({ ...previous, [node.node_id]: Date.now() + 60_000 }));
    setNow(Date.now());
    try {
      await labRequest(`/api/lab/admin/nodes/${encodeURIComponent(node.node_id)}/restart`, { method: "POST" });
      if (mounted.current) setRestarts(previous => ({ ...previous, [node.node_id]: { ...previous[node.node_id], state: "waiting" } }));
    } catch (caught) {
      if (!mounted.current) return;
      const code = caught instanceof LabApiError ? caught.message : "";
      const state = code === "agent_restart_rejected" || code === "agent_restart_unsupported" || code === "node_offline" || code === "agent_restart_cooldown" ? "failed" : "unconfirmed";
      setRestarts(previous => ({ ...previous, [node.node_id]: { ...previous[node.node_id], state } }));
      setError(code === "agent_restart_cooldown" ? "This agent is cooling down. Wait one minute before retrying." : state === "unconfirmed" ? "Restart outcome is unconfirmed. Refresh node status before retrying." : "The agent restart was rejected. Refresh node status before retrying.");
    }
  }

  return <section class="lab-panel" aria-labelledby="agentControlTitle">
    <div class="lab-panel-head"><div><h3 id="agentControlTitle">Node agents</h3><p>Restart telemetry collection, including on the manager host. GPU jobs continue running.</p></div><button class="lab-button is-quiet" disabled={loading} onClick={() => void load()}>Refresh nodes</button></div>
    {error && <div class="lab-notice" role="alert">{error}</div>}
    <div class="lab-node-controls" aria-busy={loading}>
      {nodes.map(node => {
        const state = restarts[node.node_id]?.state;
        const busy = state === "sending" || state === "waiting";
        const cooling = (cooldowns[node.node_id] || 0) > now;
        const detail = state === "sending" ? "Sending restart request…" : state === "waiting" ? "Waiting for the agent to reconnect…" : state === "done" ? "Agent restarted and reconnected" : state === "unconfirmed" ? "Restart unconfirmed · refresh to check" : state === "failed" ? "Restart request rejected" : !node.connected ? "Agent offline" : !node.restart_supported ? "Agent upgrade required for restart" : `Agent ${node.agent_version || "unknown version"}`;
        return <div class="lab-node-control" key={node.node_id}>
          <div class="lab-node-identity"><strong>{node.node_id}</strong><small>{node.hostname}</small></div>
          <span class="lab-status-label">{node.status}</span>
          {node.manager_host && <span class="lab-status-label">Manager host</span>}
          <span class="lab-node-control-detail" role="status">{detail}</span>
          <button class="lab-button" disabled={busy || cooling || !node.connected || !node.restart_supported} onClick={() => void restart(node)}>{busy ? "Restarting…" : cooling ? "Cooling down" : "Restart agent"}</button>
        </div>;
      })}
      {!nodes.length && <div class="lab-empty">{loading ? "Loading node agents…" : "No node agents have connected yet."}</div>}
    </div>
  </section>;
}
