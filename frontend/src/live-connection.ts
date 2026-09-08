// Each connection owns its listeners; retired sockets can never publish stale data.
export function connectLive(options: {
  message: (data: string) => void;
  state: (state: "connecting" | "live" | "offline") => void;
  recover: (manual: boolean) => void;
  interval: () => number;
}) {
  let socket: WebSocket | null = null;
  let reconnectTimer = 0;
  let stopped = false;
  let failures = 0;
  let lastMessage = Date.now();
  let lastCheck = Date.now();

  function retire() {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = 0;
    const old = socket;
    socket = null;
    old?.close();
  }
  function connect() {
    retire();
    if (stopped || document.hidden || !navigator.onLine) return;
    lastMessage = Date.now();
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const current = new WebSocket(`${protocol}://${location.host}/ws/cluster`);
    socket = current;
    const active = () => !stopped && socket === current;
    options.state("connecting");
    current.addEventListener("message", event => {
      if (!active()) return;
      try {
        options.message(event.data);
        lastMessage = Date.now();
        failures = 0;
        options.state("live");
      } catch {
        current.close();
      }
    });
    current.addEventListener("close", event => {
      if (!active()) return;
      socket = null;
      options.state("offline");
      if (event.code === 4401 || event.code === 4403) options.recover(false);
      const delay = event.code === 4403 ? 30_000 : Math.min(30_000, 1200 * 2 ** Math.min(failures++, 5));
      reconnectTimer = window.setTimeout(connect, delay);
    });
    current.addEventListener("error", () => {
      if (active()) options.state("offline");
    });
  }
  function resume(manual: boolean) {
    if (stopped || document.hidden || !navigator.onLine) return;
    connect();
    options.recover(manual);
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
    const stale = now - lastMessage > Math.max(15_000, options.interval() * 3000);
    if (now - lastCheck > 10_000 || (!reconnectTimer && (stale || !socket))) recover();
    lastCheck = now;
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
