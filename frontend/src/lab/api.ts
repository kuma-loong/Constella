import { fetchJson, RequestError } from "../requests";

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
  try {
    return await fetchJson<T>(path, {
      ...options,
      headers: {
        ...LAB_HEADERS,
        ...(mutating ? { "X-Constella-Request": "same-origin" } : {}),
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
  } catch (error) {
    if (error instanceof RequestError) {
      throw new LabApiError(error.status, error.payload, error.requestId);
    }
    throw error;
  }
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
