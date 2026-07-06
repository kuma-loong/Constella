export const DISPLAY_TIME_ZONE = "Asia/Shanghai";

export function fmtGiB(mib: number) {
  if (!Number.isFinite(mib)) {
    return "n/a";
  }
  return `${(mib / 1024).toFixed(mib >= 10240 ? 1 : 2)} GiB`;
}

export function fmtPct(value: number) {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  return `${value.toFixed(value % 1 ? 1 : 0)}%`;
}

export function fmtNumber(value: number) {
  if (!Number.isFinite(value)) {
    return "n/a";
  }
  return value.toLocaleString(undefined, { maximumFractionDigits: value >= 10 ? 1 : 2 });
}

export function formatBucket(seconds: number) {
  if (seconds < 60) {
    return `${seconds}s`;
  }
  if (seconds < 3600) {
    return `${Math.round(seconds / 60)}m`;
  }
  return `${Math.round(seconds / 3600)}h`;
}

export function formatTime(epochSeconds: number) {
  if (!Number.isFinite(epochSeconds)) {
    return "n/a";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: DISPLAY_TIME_ZONE,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(epochSeconds * 1000));
}

export function fmtDuration(seconds: number | null) {
  if (seconds === null || !Number.isFinite(seconds)) {
    return "n/a";
  }
  if (seconds < 60) {
    return `${Math.max(0, Math.floor(seconds))}s`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ${Math.floor(seconds % 60)}s`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours}h ${minutes % 60}m`;
  }
  const days = Math.floor(hours / 24);
  return `${days}d ${hours % 24}h`;
}

export function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (char) => {
    const map: Record<string, string> = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    };
    return map[char] || char;
  });
}

export function escapeAttr(value: string) {
  return escapeHtml(value).replace(/\n/g, " ");
}
