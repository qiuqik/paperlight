import type {DocumentModel} from './document';
import type {SavedDocument} from './storage';

type ReadyDocument = {status: string; document?: DocumentModel};

export async function refreshSavedDocument(entry: SavedDocument, fetcher: typeof fetch = fetch): Promise<SavedDocument> {
  if (!entry.serverId) return entry;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetcher(`/api/parser/api/documents/${encodeURIComponent(entry.serverId)}`,
      {cache: 'no-store', signal: controller.signal});
    if (!response.ok) return entry;
    const state = await response.json() as ReadyDocument;
    const fresh = state.document;
    if (state.status !== 'ready' || !fresh || fresh.id !== entry.serverId
      || (fresh.modelVersion || 0) < (entry.document.modelVersion || 0)
      || (fresh.fingerprint && entry.document.fingerprint && fresh.fingerprint !== entry.document.fingerprint)) return entry;
    return JSON.stringify(fresh) === JSON.stringify(entry.document) ? entry : {...entry, document: fresh};
  } catch {
    return entry;
  } finally {
    clearTimeout(timeout);
  }
}
