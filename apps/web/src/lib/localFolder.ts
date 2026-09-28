import type {Annotation, DocumentModel} from './document';

export type FolderDocument = {
  id: string; filename: string; savedAt: number; document: DocumentModel;
  serverId?: string; sourcePath?: string[];
};
export type FolderPdf = {id: string; filename: string; savedAt: number; sourcePath: string[]};
export type FolderEntry = FolderDocument | FolderPdf;

type IndexRecord = Omit<FolderDocument, 'document'> & {title: string; pageCount: number};
type FolderIndex = {version: 1; records: IndexRecord[]; hidden: string[]};
type PermissionHandle = FileSystemDirectoryHandle & {
  queryPermission: (options: {mode: 'readwrite'}) => Promise<PermissionState>;
  requestPermission: (options: {mode: 'readwrite'}) => Promise<PermissionState>;
};
type PickerWindow = Window & {showDirectoryPicker?: (options?: {mode: 'readwrite'}) => Promise<FileSystemDirectoryHandle>};

let activeFolder: FileSystemDirectoryHandle | null = null;
const annotationWrites = new Map<string, Promise<void>>();
const DB_NAME = 'paperlight-folder-setting';
const FOLDER_KEY = 'selected-folder';
const META_NAME = '.paperlight';
const emptyIndex = (): FolderIndex => ({version: 1, records: [], hidden: []});
const pathKey = (path: string[]) => path.join('/');

function settingDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore('settings');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function folderSetting(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest): Promise<unknown> {
  const db = await settingDb();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction('settings', mode);
    const request = action(transaction.objectStore('settings'));
    let result: unknown;
    request.onsuccess = () => {result = request.result;};
    request.onerror = () => reject(request.error);
    transaction.oncomplete = () => {db.close(); resolve(result);};
    transaction.onerror = () => {db.close(); reject(transaction.error);};
  });
}

export function supportsLocalFolder(): boolean {
  return typeof window !== 'undefined' && typeof (window as PickerWindow).showDirectoryPicker === 'function';
}

export function currentFolderName(): string | null {return activeFolder?.name || null;}
export function hasLocalFolder(): boolean {return activeFolder !== null;}

export async function restoreLocalFolder(): Promise<{name: string; authorized: boolean} | null> {
  if (!supportsLocalFolder()) return null;
  const handle = await folderSetting('readonly', store => store.get(FOLDER_KEY)) as PermissionHandle | undefined;
  if (!handle) return null;
  const authorized = await handle.queryPermission({mode: 'readwrite'}) === 'granted';
  activeFolder = authorized ? handle : null;
  return {name: handle.name, authorized};
}

export async function authorizeLocalFolder(): Promise<boolean> {
  const handle = await folderSetting('readonly', store => store.get(FOLDER_KEY)) as PermissionHandle | undefined;
  if (!handle) return false;
  const granted = await handle.requestPermission({mode: 'readwrite'}) === 'granted';
  activeFolder = granted ? handle : null;
  return granted;
}

export async function chooseLocalFolder(): Promise<string> {
  const picker = (window as PickerWindow).showDirectoryPicker;
  if (!picker) throw new Error('此浏览器不支持持续读取本地文件夹');
  const handle = await picker({mode: 'readwrite'});
  activeFolder = handle;
  await folderSetting('readwrite', store => store.put(handle, FOLDER_KEY));
  return handle.name;
}

function folder(): FileSystemDirectoryHandle {
  if (!activeFolder) throw new Error('请先选择并授权本地文件夹');
  return activeFolder;
}

async function metadata(create = false): Promise<FileSystemDirectoryHandle> {
  return folder().getDirectoryHandle(META_NAME, {create});
}

async function readJson<T>(dir: FileSystemDirectoryHandle, name: string): Promise<T | null> {
  try {
    const file = await (await dir.getFileHandle(name)).getFile();
    return JSON.parse(await file.text()) as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === 'NotFoundError') return null;
    throw error;
  }
}

async function writeJson(dir: FileSystemDirectoryHandle, name: string, value: unknown): Promise<void> {
  const writer = await (await dir.getFileHandle(name, {create: true})).createWritable();
  try {await writer.write(JSON.stringify(value)); await writer.close();}
  catch (error) {await writer.abort(); throw error;}
}

async function readIndex(): Promise<FolderIndex> {
  try {return await readJson<FolderIndex>(await metadata(), 'index.json') || emptyIndex();}
  catch (error) {if (error instanceof DOMException && error.name === 'NotFoundError') return emptyIndex(); throw error;}
}

async function subdirectory(name: 'models' | 'annotations', create = false): Promise<FileSystemDirectoryHandle> {
  return (await metadata(create)).getDirectoryHandle(name, {create});
}

async function readModel(id: string): Promise<DocumentModel | null> {
  try {return await readJson<DocumentModel>(await subdirectory('models'), `${id}.json`);}
  catch (error) {if (error instanceof DOMException && error.name === 'NotFoundError') return null; throw error;}
}

async function fileAt(path: string[]): Promise<FileSystemFileHandle> {
  let dir = folder();
  for (const segment of path.slice(0, -1)) dir = await dir.getDirectoryHandle(segment);
  return dir.getFileHandle(path[path.length - 1]);
}

export async function openFolderPdf(path: string[]): Promise<File> {
  return (await fileAt(path)).getFile();
}

async function scanPdfs(dir: FileSystemDirectoryHandle, prefix: string[] = [], depth = 0): Promise<FolderPdf[]> {
  const result: FolderPdf[] = [];
  const entries = dir as unknown as AsyncIterable<[string, FileSystemHandle]>;
  for await (const [name, handle] of entries) {
    if (handle.kind === 'file' && /\.pdf$/i.test(name)) {
      const path = [...prefix, name];
      const file = await (handle as FileSystemFileHandle).getFile();
      result.push({id: `folder:${pathKey(path)}`, filename: name, savedAt: file.lastModified, sourcePath: path});
    } else if (handle.kind === 'directory' && name !== META_NAME && depth < 5) {
      result.push(...await scanPdfs(handle as FileSystemDirectoryHandle, [...prefix, name], depth + 1));
    }
  }
  return result;
}

export async function listFolderEntries(): Promise<FolderEntry[]> {
  const index = await readIndex();
  const parsed: FolderDocument[] = [];
  for (const record of index.records) {
    const document = await readModel(record.id);
    if (document) parsed.push({id: record.id, filename: record.filename, savedAt: record.savedAt,
      serverId: record.serverId, sourcePath: record.sourcePath, document});
  }
  const recordedPaths = new Set(parsed.filter(item => item.sourcePath).map(item => pathKey(item.sourcePath!)));
  const hidden = new Set(index.hidden);
  const unparsed = (await scanPdfs(folder())).filter(item => !recordedPaths.has(pathKey(item.sourcePath)) && !hidden.has(pathKey(item.sourcePath)));
  return [...parsed, ...unparsed].sort((a, b) => b.savedAt - a.savedAt);
}

export async function getFolderDocument(id: string): Promise<FolderDocument | null> {
  const record = (await readIndex()).records.find(item => item.id === id);
  if (!record) return null;
  const document = await readModel(id);
  return document ? {id, filename: record.filename, savedAt: record.savedAt, serverId: record.serverId, sourcePath: record.sourcePath, document} : null;
}

async function copyPdf(source: Blob, filename: string, id: string): Promise<string[]> {
  const base = filename.replace(/[\\/]/g, '_').replace(/\.pdf$/i, '') || 'paper';
  let sourceHash: string | null = null;
  for (let attempt = 0; attempt < 100; attempt++) {
    const name = attempt === 0 ? `${base}.pdf` : `${base}-${id.slice(0, 8)}${attempt === 1 ? '' : `-${attempt}`}.pdf`;
    try {
      const existing = await (await folder().getFileHandle(name)).getFile();
      if (existing.size !== source.size) continue;
      sourceHash ??= Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await source.arrayBuffer())), byte => byte.toString(16).padStart(2, '0')).join('');
      const existingHash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await existing.arrayBuffer())), byte => byte.toString(16).padStart(2, '0')).join('');
      if (sourceHash === existingHash) return [name];
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'NotFoundError')) throw error;
      const writer = await (await folder().getFileHandle(name, {create: true})).createWritable();
      try {await writer.write(source); await writer.close();}
      catch (writeError) {await writer.abort(); throw writeError;}
      return [name];
    }
  }
  throw new Error('无法为 PDF 找到可用的本地文件名');
}

export async function saveFolderDocument(entry: FolderDocument, pdf?: Blob, sourcePath?: string[]): Promise<void> {
  if (!/^[a-f0-9]{32,64}$/.test(entry.id)) throw new Error('Invalid document ID');
  const index = await readIndex();
  const previous = index.records.find(item => item.id === entry.id);
  const path = sourcePath || entry.sourcePath || previous?.sourcePath || (pdf ? await copyPdf(pdf, entry.filename, entry.id) : undefined);
  await writeJson(await subdirectory('models', true), `${entry.id}.json`, entry.document);
  const record: IndexRecord = {id: entry.id, filename: entry.filename, savedAt: entry.savedAt, serverId: entry.serverId,
    sourcePath: path, title: entry.document.metadata.title, pageCount: entry.document.metadata.pageCount};
  index.records = [...index.records.filter(item => item.id !== entry.id), record];
  if (path) index.hidden = index.hidden.filter(item => item !== pathKey(path));
  await writeJson(await metadata(true), 'index.json', index);
}

export async function removeFolderEntry(entry: FolderEntry): Promise<void> {
  const index = await readIndex();
  index.records = index.records.filter(item => item.id !== entry.id);
  if (entry.sourcePath && !index.hidden.includes(pathKey(entry.sourcePath))) index.hidden.push(pathKey(entry.sourcePath));
  await writeJson(await metadata(true), 'index.json', index);
  if ('document' in entry) {
    for (const directory of ['models', 'annotations'] as const) {
      try {await (await subdirectory(directory)).removeEntry(`${entry.id}.json`);}
      catch (error) {if (!(error instanceof DOMException && error.name === 'NotFoundError')) throw error;}
    }
  }
}

export async function listFolderAnnotations(documentId: string): Promise<Annotation[]> {
  try {return await readJson<Annotation[]>(await subdirectory('annotations'), `${documentId}.json`) || [];}
  catch (error) {if (error instanceof DOMException && error.name === 'NotFoundError') return []; throw error;}
}

export async function saveFolderAnnotation(annotation: Annotation): Promise<void> {
  return queueAnnotationWrite(annotation.documentId, async () => {
    const records = await listFolderAnnotations(annotation.documentId);
    await writeJson(await subdirectory('annotations', true), `${annotation.documentId}.json`,
      [...records.filter(item => item.id !== annotation.id), annotation]);
  });
}

export async function deleteFolderAnnotation(id: string, documentId: string): Promise<void> {
  return queueAnnotationWrite(documentId, async () => {
    const records = await listFolderAnnotations(documentId);
    await writeJson(await subdirectory('annotations', true), `${documentId}.json`, records.filter(item => item.id !== id));
  });
}

function queueAnnotationWrite(documentId: string, action: () => Promise<void>): Promise<void> {
  const previous = annotationWrites.get(documentId);
  const next = (previous?.catch(() => {}) ?? Promise.resolve()).then(action);
  annotationWrites.set(documentId, next);
  const clear = () => {if (annotationWrites.get(documentId) === next) annotationWrites.delete(documentId);};
  void next.then(clear, clear);
  return next;
}
