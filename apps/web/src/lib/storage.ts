import type {Annotation, DocumentModel} from './document';
import {deleteFolderAnnotation, getFolderDocument, hasLocalFolder, listFolderAnnotations, saveFolderAnnotation, saveFolderDocument} from './localFolder';

export type SavedDocument = {id: string; document: DocumentModel; filename: string; savedAt: number; pdf?: Blob; serverId?: string; sourcePath?: string[]};
export type ReadingProgress = {id: string; percent: number; blockId?: string; blockOffset?: number; updatedAt: number};
const DB_NAME = 'paperlight-v2';
const DB_VERSION = 3;

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
      if (!db.objectStoreNames.contains('readingProgress')) db.createObjectStore('readingProgress', {keyPath: 'id'});
      if (!db.objectStoreNames.contains('recent')) db.createObjectStore('recent', {keyPath: 'id'});
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function request<T>(storeName: 'documents' | 'annotations' | 'readingProgress' | 'recent', mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
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

const legacyDocuments = () => request<SavedDocument[]>('documents', 'readonly', store => store.getAll());
const legacyAnnotations = () => request<Annotation[]>('annotations', 'readonly', store => store.getAll());

export async function saveDocument(entry: SavedDocument): Promise<void> {
  if (hasLocalFolder()) await saveFolderDocument(entry, entry.pdf, entry.sourcePath);
}
export async function getDocument(id: string): Promise<SavedDocument | undefined> {
  if (hasLocalFolder()) {
    const entry = await getFolderDocument(id);
    if (entry) return entry;
  }
  return request<SavedDocument | undefined>('documents', 'readonly', store => store.get(id));
}
export async function listDocuments(): Promise<SavedDocument[]> {
  return legacyDocuments();
}
export async function saveAnnotation(entry: Annotation): Promise<void> {
  if (hasLocalFolder()) await saveFolderAnnotation(entry);
}
export async function deleteAnnotation(id: string, documentId?: string): Promise<void> {
  if (hasLocalFolder() && documentId) await deleteFolderAnnotation(id, documentId);
  await request('annotations', 'readwrite', store => store.delete(id));
}
export async function listAnnotations(documentId: string): Promise<Annotation[]> {
  const legacy = await request<Annotation[]>('annotations', 'readonly', store => store.index('documentId').getAll(documentId));
  if (!hasLocalFolder()) return legacy;
  const folder = await listFolderAnnotations(documentId);
  return [...folder, ...legacy.filter(item => !folder.some(saved => saved.id === item.id))];
}

export async function migrateBrowserDataToFolder(): Promise<number> {
  if (!hasLocalFolder()) throw new Error('请先选择本地文件夹');
  const documents = await legacyDocuments();
  const annotations = await legacyAnnotations();
  for (const entry of documents) await saveFolderDocument(entry, entry.pdf);
  for (const annotation of annotations) await saveFolderAnnotation(annotation);
  await request('documents', 'readwrite', store => store.clear());
  await request('annotations', 'readwrite', store => store.clear());
  return documents.length;
}

export async function removeBrowserHistory(id: string): Promise<void> {
  const annotations = await request<Annotation[]>('annotations', 'readonly', store => store.index('documentId').getAll(id));
  for (const item of annotations) await request('annotations', 'readwrite', store => store.delete(item.id));
  await Promise.all([
    request('documents', 'readwrite', store => store.delete(id)),
    request('recent', 'readwrite', store => store.delete(id)),
    request('readingProgress', 'readwrite', store => store.delete(id)),
  ]);
}

export async function recordBrowserVisit(id: string, title: string, serverId?: string): Promise<void> {
  await request('recent', 'readwrite', store => store.put({id, title, serverId, visitedAt: Date.now()}));
}
export const saveProgress = (entry: ReadingProgress) => request('readingProgress', 'readwrite', store => store.put(entry));
export const getProgress = (id: string) => request<ReadingProgress | undefined>('readingProgress', 'readonly', store => store.get(id));

export async function fingerprint(file: File): Promise<string> {
  const bytes = await file.arrayBuffer();
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('');
}
