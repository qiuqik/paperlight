import type {Annotation, DocumentModel} from './document';

export type SavedDocument = {id: string; document: DocumentModel; filename: string; savedAt: number; pdf?: Blob; serverId?: string};
const DB_NAME = 'paperlight-v2';
const DB_VERSION = 1;

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains('documents')) db.createObjectStore('documents', {keyPath: 'id'});
      if (!db.objectStoreNames.contains('annotations')) {
        const store = db.createObjectStore('annotations', {keyPath: 'id'});
        store.createIndex('documentId', 'documentId');
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function request<T>(storeName: 'documents' | 'annotations', mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, mode);
    const operation = run(tx.objectStore(storeName));
    let result: T;
    operation.onsuccess = () => {result = operation.result;};
    operation.onerror = () => reject(operation.error);
    tx.oncomplete = () => {db.close(); resolve(result);};
    tx.onerror = () => {db.close(); reject(tx.error);};
  });
}

export const saveDocument = (entry: SavedDocument) => request('documents', 'readwrite', store => store.put(entry));
export const getDocument = (id: string) => request<SavedDocument | undefined>('documents', 'readonly', store => store.get(id));
export const listDocuments = () => request<SavedDocument[]>('documents', 'readonly', store => store.getAll());
export const saveAnnotation = (entry: Annotation) => request('annotations', 'readwrite', store => store.put(entry));
export const deleteAnnotation = (id: string) => request('annotations', 'readwrite', store => store.delete(id));
export const listAnnotations = (documentId: string) => request<Annotation[]>('annotations', 'readonly', store => store.index('documentId').getAll(documentId));

export async function fingerprint(file: File): Promise<string> {
  const bytes = await file.arrayBuffer();
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('');
}
