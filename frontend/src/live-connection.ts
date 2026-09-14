// Each connection owns its listeners; retired sockets can never publish stale data.
export function connectLive(options: {
  message: (data: string) => void;
  state: (state: "connecting" | "live" | "offline") => void;
  // HTTP can distinguish an expired Access session from an opaque WS handshake failure.
  recover: (manual: boolean) => Promise<boolean | void> | boolean | void;
  refresh?: () => void;
  interval: () => number;
}) {
  let socket: WebSocket | null = null;
  let reconnectTimer = 0;
  let stopped = false;
  let blocked = false;
  let failures = 0;
  let lastMessage = Date.now();
  let lastCheck = Date.now();
  let lastResume = -Infinity;
  let received = false;
  let recovery: Promise<void> | null = null;
  const available = () => !stopped && !blocked && !document.hidden && navigator.onLine;

  function retire() {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = 0;
    const old = socket;
    socket = null;
    old?.close();
  }
  function schedule(code = 1006) {
    retire();
    options.state("offline");
    if (!available()) return;
    const delay = code === 4403 ? 30_000 : Math.min(30_000, 1200 * 2 ** Math.min(failures++, 5));
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = 0;
      void probe(false);
    }, delay);
  }
  function connect() {
    if (!available() || socket) return;
    lastMessage = Date.now();
    received = false;
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const current = new WebSocket(`${protocol}://${location.host}/ws/cluster`);
    socket = current;
    const active = () => !stopped && socket === current;
    options.state("connecting");
    current.addEventListener("message", event => {
      if (!active() || !available()) return;
      try {
        options.message(event.data);
        lastMessage = Date.now();
        received = true;
        failures = 0;
        options.state("live");
      } catch {
        schedule();
      }
    });
    current.addEventListener("close", event => {
      if (active()) schedule(event.code);
    });
    current.addEventListener("error", () => {
      if (active()) schedule();
    });
  }
  function probe(manual: boolean) {
    if (recovery) return recovery;
    // Single flight across visibility, online, focus and watchdog callbacks.
    recovery = Promise.resolve().then(() => options.recover(manual)).then(result => {
      if (stopped) return;
      if (result === false) {
        blocked = true;
        retire();
        options.state("offline");
      } else connect();
    }).catch(() => {
      if (!stopped && !socket) schedule();
    }).finally(() => { recovery = null; });
    return recovery;
  }
  function resume(manual: boolean) {
    if (manual) blocked = false;
    if (!available() || recovery) return;
    const now = Date.now();
    if (!manual && now - lastResume < 1000) return;
    lastResume = lastCheck = now;
    // A manual data refresh must not interrupt a healthy socket/slow handshake.
    if (!manual) retire();
    connect();
    options.refresh?.();
    void probe(manual);
  }
  const recover = () => resume(false);
  function visibility() {
    if (document.hidden) retire();
    else recover();
  }
  function offline() {
    retire();
    options.state("offline");
  }
  function check() {
    const now = Date.now();
    const slept = now - lastCheck > 10_000;
    lastCheck = now;
    if (!available() || recovery) return;
    if (slept) { recover(); return; }
    if (reconnectTimer) return;
    // A tunnel handshake has its own deadline, independent of telemetry cadence.
    const timeout = received ? Math.max(15_000, options.interval() * 3000) : 30_000;
    if (socket && now - lastMessage > timeout) schedule();
    else if (!socket) void probe(false);
  }
  const timer = window.setInterval(check, 3000);
  document.addEventListener("visibilitychange", visibility);
  window.addEventListener("online", recover);
  window.addEventListener("offline", offline);
  window.addEventListener("focus", check);
  window.addEventListener("pageshow", check);
  connect();
  return {
    recover: () => resume(true),
    dispose() {
      stopped = true;
      retire();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener("online", recover);
      window.removeEventListener("offline", offline);
      window.removeEventListener("focus", check);
      window.removeEventListener("pageshow", check);
    },
  };
}
