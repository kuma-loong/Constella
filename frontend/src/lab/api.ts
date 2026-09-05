export const LAB_HEADERS = {
  "X-Requested-With": "XMLHttpRequest",
};

export class LabApiError extends Error {
  status: number;
  requestId?: string;
  payload: unknown;

  constructor(status: number, payload: unknown, requestId?: string) {
    super(errorCode(payload) || `Request failed: ${status}`);
    this.status = status;
    this.payload = payload;
    this.requestId = requestId;
  }
}

export async function labRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const mutating = options.method && options.method !== "GET";
  const response = await fetch(path, {
    cache: "no-store",
    ...options,
    headers: {
      ...LAB_HEADERS,
      ...(mutating ? { "X-Constella-Request": "same-origin" } : {}),
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  const payload = await response.json().catch(() => null);
  if (response.status === 401) {
    window.location.reload();
    throw new LabApiError(response.status, payload, response.headers.get("x-request-id") || undefined);
  }
  if (!response.ok) {
    throw new LabApiError(response.status, payload, response.headers.get("x-request-id") || undefined);
  }
  return payload as T;
}

function errorCode(payload: unknown): string | undefined {
  if (!payload || typeof payload !== "object") {
    return undefined;
  }
  const record = payload as Record<string, unknown>;
  if (typeof record.error === "string") {
    return record.error;
  }
  if (record.detail && typeof record.detail === "object") {
    const code = (record.detail as Record<string, unknown>).code;
    return typeof code === "string" ? code : undefined;
  }
  return undefined;
}
