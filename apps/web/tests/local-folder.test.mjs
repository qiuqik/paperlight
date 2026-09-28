import assert from 'node:assert/strict';
import {test} from 'node:test';
import {chooseLocalFolder, deleteFolderAnnotation, listFolderAnnotations, listFolderEntries, openFolderPdf, removeFolderEntry, restoreLocalFolder, saveFolderAnnotation, saveFolderDocument} from '../src/lib/localFolder.ts';

const missing = () => new DOMException('Missing', 'NotFoundError');

class MemoryFile {
  kind = 'file';
  bytes;
  constructor(name, bytes) {this.name = name; this.bytes = bytes;}
  async getFile() {return new File([this.bytes], this.name, {type: 'application/pdf', lastModified: 123});}
  async createWritable() {
    let next;
    return {
      write: async value => {next = value instanceof Blob ? new Uint8Array(await value.arrayBuffer()) : new TextEncoder().encode(value);},
      close: async () => {this.bytes = next;},
      abort: async () => {},
    };
  }
}

class MemoryDirectory {
  kind = 'directory';
  entries = new Map();
  constructor(name) {this.name = name;}
  async getDirectoryHandle(name, {create = false} = {}) {
    if (!this.entries.has(name) && create) this.entries.set(name, new MemoryDirectory(name));
    const value = this.entries.get(name);
    if (value?.kind !== 'directory') throw missing();
    return value;
  }
  async getFileHandle(name, {create = false} = {}) {
    if (!this.entries.has(name) && create) this.entries.set(name, new MemoryFile(name, new Uint8Array()));
    const value = this.entries.get(name);
    if (value?.kind !== 'file') throw missing();
    return value;
  }
  async removeEntry(name) {if (!this.entries.delete(name)) throw missing();}
  async *[Symbol.asyncIterator]() {yield* this.entries;}
  async queryPermission() {return 'granted';}
  async requestPermission() {return 'granted';}
}

function installSettingsDb() {
  const settings = new Map();
  globalThis.indexedDB = {open: () => {
    const openRequest = {};
    queueMicrotask(() => {
      openRequest.result = {
        createObjectStore() {}, close() {},
        transaction() {
          const transaction = {objectStore: () => ({
            get(key) {return operation(() => settings.get(key));},
            put(value, key) {return operation(() => settings.set(key, value));},
          })};
          function operation(run) {
            const request = {};
            queueMicrotask(() => {request.result = run(); request.onsuccess?.(); queueMicrotask(() => transaction.oncomplete?.());});
            return request;
          }
          return transaction;
        },
      };
      openRequest.onupgradeneeded?.();
      openRequest.onsuccess?.();
    });
    return openRequest;
  }};
}

test('folder history survives reopening, preserves PDFs when removed, and serializes note writes', async () => {
  installSettingsDb();
  const root = new MemoryDirectory('Papers');
  root.entries.set('paper.pdf', new MemoryFile('paper.pdf', new TextEncoder().encode('original')));
  globalThis.window = {showDirectoryPicker: async () => root};
  assert.equal(await chooseLocalFolder(), 'Papers');
  const id = 'a'.repeat(64);
  const document = {metadata: {title: 'Paper', pageCount: 2}};
  await saveFolderDocument({id, filename: 'paper.pdf', savedAt: 1, document}, new Blob(['different']));
  const records = await listFolderEntries();
  const parsed = records.find(item => item.id === id);
  assert.ok(parsed);
  assert.notDeepEqual(parsed.sourcePath, ['paper.pdf']);
  assert.equal(await (await openFolderPdf(['paper.pdf'])).text(), 'original');
  assert.equal(await restoreLocalFolder().then(info => info?.authorized), true);

  const first = {id: '1', documentId: id, note: 'first'};
  const second = {id: '2', documentId: id, note: 'second'};
  await Promise.all([saveFolderAnnotation(first), saveFolderAnnotation(second)]);
  assert.deepEqual((await listFolderAnnotations(id)).map(item => item.id), ['1', '2']);
  await deleteFolderAnnotation('1', id);
  assert.deepEqual((await listFolderAnnotations(id)).map(item => item.id), ['2']);

  await removeFolderEntry(parsed);
  assert.equal((await listFolderEntries()).some(item => item.id === id), false);
  assert.equal(await (await openFolderPdf(parsed.sourcePath)).text(), 'different');
  assert.equal(await (await openFolderPdf(['paper.pdf'])).text(), 'original');
});
