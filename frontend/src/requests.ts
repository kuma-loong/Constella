export class RequestError extends Error {
  constructor(public status: number, public payload?: unknown, public requestId?: string) {
    super(`Request failed (${status})`);
  }
}

export async function fetchJson<T>(url: string, options: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  options.signal?.addEventListener("abort", abort, { once: true });
  if (options.signal?.aborted) controller.abort();
  const timer = window.setTimeout(abort, 15_000);
  try {
    const response = await fetch(url, { cache: "no-store", ...options, signal: controller.signal });
    if (!response.ok) throw new RequestError(
      response.status, await response.json().catch(() => null), response.headers?.get("x-request-id") || undefined,
    );
    return await response.json() as T;
  } finally {
    window.clearTimeout(timer);
    options.signal?.removeEventListener("abort", abort);
  }
}
