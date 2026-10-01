// Ephemeral tab memory only; private data is never persisted in browser storage.
const entries = new Map<string, {value: unknown; expires: number}>();
export function readWorkspaceCache<T>(key: string): T | undefined {
  const entry = entries.get(key);
  if (!entry || entry.expires < Date.now()) return undefined;
  return entry.value as T;
}
export function writeWorkspaceCache<T>(key: string, value: T) {
  entries.set(key, {value, expires: Date.now() + 5 * 60_000});
}
export function clearWorkspaceCache() { entries.clear(); }
