// Keep decoded node responses across range switches; never extend their data age on a hit.
export function createNodeHistoryCache<T extends { generated_at?: number }>(ttlMs = 60_000, limit = 8) {
  const entries = new Map<string, { value: T; expiresAt: number }>();
  return {
    get(key: string): T | null {
      const entry = entries.get(key);
      if (!entry) return null;
      if (Date.now() >= entry.expiresAt) {
        entries.delete(key);
        return null;
      }
      entries.delete(key);
      entries.set(key, entry);
      return entry.value;
    },
    set(key: string, value: T, requestedAt: number) {
      const generatedAt = value.generated_at == null ? requestedAt : value.generated_at * 1000;
      entries.delete(key);
      entries.set(key, { value, expiresAt: Math.min(requestedAt, generatedAt) + ttlMs });
      while (entries.size > limit) entries.delete(entries.keys().next().value!);
    },
    delete: (key: string) => entries.delete(key),
    clear: () => entries.clear(),
  };
}
