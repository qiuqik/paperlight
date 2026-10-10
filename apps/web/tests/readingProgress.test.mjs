import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import {JSDOM} from 'jsdom';
function setup(remote = {}) {
  const dom = new JSDOM('<article><div class="paper-content"><p data-block-id="first"></p><p data-block-id="second"></p></div></article>', {url: 'http://localhost'});
  const root = dom.window.document.querySelector('article');
  let before = 1000;
  Object.defineProperties(root, {scrollHeight: {get: () => before + 1000}, clientHeight: {value: 500}});
  root.getBoundingClientRect = () => ({top: 0});
  const blocks = root.querySelectorAll('p');
  blocks[0].getBoundingClientRect = () => ({top: -root.scrollTop, height: before, bottom: before-root.scrollTop});
  blocks[1].getBoundingClientRect = () => ({top: before-root.scrollTop, height: 500, bottom: before+500-root.scrollTop});
  const writes = [];
  const exports = {};
  const code = ts.transpileModule(fs.readFileSync(new URL('../src/lib/readingProgress.ts', import.meta.url), 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
  vm.runInNewContext(code, {exports, window: dom.window, document: dom.window.document, localStorage: dom.window.localStorage, setTimeout, clearTimeout, Date, fetch: async (_, options) => {if (options.method === 'PUT') writes.push(JSON.parse(options.body)); return {ok: true, json: async () => remote};}});
  const start = () => exports.trackReadingPosition(root, 'alice', 'doc', () => {}, () => assert.fail('write failed'));
  return {root, dom, writes, start, merge: exports.mergeLocalReadingProgress, reflow: () => {before += 400;}};
}
test('immediate reload restores precise last scroll before debounce fires', async () => {
  const s=setup(); const first=s.start(); await first.ready;
  s.root.scrollTop=1123.45; s.root.dispatchEvent(new s.dom.window.Event('scroll'));
  first.dispose(); s.root.scrollTop=0;
  const next=s.start(); await next.ready;
  assert.ok(Math.abs(s.root.scrollTop-1123.45)<0.01);
  assert.ok(s.writes.some(w => w.blockId==='second' && w.recordedAt));
  next.dispose(); s.dom.window.close();
});
test('translation or image reflow preserves the same position within a paragraph', async () => {
  const s=setup({scroll_progress:75,block_id:'second',block_offset:0.3,updated_at:1});
  const tracker=s.start(); await tracker.ready;
  assert.equal(s.root.scrollTop,1126);
  s.reflow(); tracker.layoutChanged();
  assert.equal(s.root.scrollTop,1526);
  tracker.dispose(); s.dom.window.close();
});
test('newer server position takes precedence over stale local position', async () => {
  const s=setup({scroll_progress:75,block_id:'second',block_offset:0.3,updated_at:100});
  s.dom.window.localStorage.setItem('paperlight:position:alice:doc',JSON.stringify({percent:10,blockOffset:0,recordedAt:1}));
  const tracker=s.start(); await tracker.ready;
  assert.equal(s.root.scrollTop,1126); tracker.dispose(); s.dom.window.close();
});

test('library immediately shows the latest scroll even before its server write completes', async () => {
  const s=setup({scroll_progress:10,updated_at:1});
  const tracker=s.start();await tracker.ready;
  s.root.scrollTop=1123.45;s.root.dispatchEvent(new s.dom.window.Event('scroll'));
  const stale=[{documentId:'doc',progress:10,progressUpdatedAt:1,title:'Paper'}];
  const latest=s.merge(stale,'alice');
  assert.ok(Math.abs(latest[0].progress-1123.45/1500*100)<0.01);
  assert.equal(latest[0].title,'Paper');assert.equal(stale[0].progress,10);
  assert.equal(s.merge(stale,'bob')[0].progress,10);
  const newer=[{documentId:'doc',progress:90,progressUpdatedAt:Date.now()/1000+1}];
  assert.equal(s.merge(newer,'alice')[0].progress,90);
  tracker.dispose();s.dom.window.close();
});
test('reflow percentage stays consistent between the reader, local list and saved record', async () => {
  const s=setup({scroll_progress:75,block_id:'second',block_offset:0.3,updated_at:1});
  const tracker=s.start();await tracker.ready;s.reflow();tracker.layoutChanged();
  const expected=s.root.scrollTop/(s.root.scrollHeight-s.root.clientHeight)*100;
  const latest=s.merge([{documentId:'doc',progress:75,progressUpdatedAt:1}],'alice');
  assert.ok(Math.abs(latest[0].progress-expected)<0.01);
  tracker.dispose();await Promise.resolve();
  assert.ok(s.writes.some(w=>Math.abs(w.percent-expected)<0.01));s.dom.window.close();
});
test('malformed local records do not replace server progress',()=>{
  const s=setup();s.dom.window.localStorage.setItem('paperlight:position:alice:doc',JSON.stringify({percent:150,recordedAt:Date.now()}));
  assert.equal(s.merge([{documentId:'doc',progress:25}],'alice')[0].progress,25);
  s.dom.window.localStorage.setItem('paperlight:position:alice:doc','broken JSON');
  assert.equal(s.merge([{documentId:'doc',progress:25}],'alice')[0].progress,25);s.dom.window.close();
});

test('leaving the reader does not replace the last position with detached DOM geometry', async () => {
  const s=setup();const tracker=s.start();await tracker.ready;
  s.root.scrollTop=600;s.root.dispatchEvent(new s.dom.window.Event('scroll'));
  s.root.remove();s.root.scrollTop=0;tracker.dispose();await Promise.resolve();
  assert.equal(s.merge([{documentId:'doc',progress:0}],'alice')[0].progress,40);
  assert.ok(s.writes.some(w=>w.percent===40));assert.ok(!s.writes.some(w=>w.percent===0));s.dom.window.close();
});
