import assert from 'node:assert/strict';
import test from 'node:test';

import {refreshSavedDocument} from '../src/lib/documentCache.ts';

const saved = {
  id: 'fingerprint', serverId: 'server-id', filename: 'paper.pdf', savedAt: 1,
  pdf: new Blob(['pdf']),
  document: {id: 'server-id', modelVersion: 3, fingerprint: 'fingerprint', metadata: {title: 'Paper', authors: [], pageCount: 1}, sections: [], figures: [], tables: [], references: []},
};

test('refreshes a cached model from its server copy while preserving the PDF blob', async () => {
  const fresh = {...saved.document, sections: [{id: 'body', blocks: [{id: 'figure-1', order: 0}]}]};
  const fetcher = async (url, options) => {
    assert.equal(url, '/api/parser/api/documents/server-id');
    assert.equal(options.cache, 'no-store');
    return {ok: true, json: async () => ({status: 'ready', document: fresh})};
  };

  const result = await refreshSavedDocument(saved, fetcher);

  assert.equal(result.document, fresh);
  assert.equal(result.pdf, saved.pdf);
  assert.equal(result.id, saved.id);
});

test('opens the local copy when the server cannot be reached', async () => {
  const result = await refreshSavedDocument(saved, async () => {throw new Error('offline');});
  assert.equal(result, saved);
});

test('does not replace a cached paper with a different server document', async () => {
  const different = {...saved.document, fingerprint: 'other'};
  const result = await refreshSavedDocument(saved, async () => ({ok: true, json: async () => ({status: 'ready', document: different})}));
  assert.equal(result, saved);
});
