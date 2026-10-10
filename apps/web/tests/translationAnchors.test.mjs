import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import {JSDOM} from 'jsdom';

// Compile the actual TypeScript modules without changing production import paths.
function fixture() {
  const dom = new JSDOM(`<article><p data-block-id="p1">First English paragraph.</p><div data-translation-ui><button>收起译文</button><div data-block-id="translation:block:p1" data-translation-block>第一段中文😀译文。</div></div><h2>Section heading</h2><p data-block-id="p2">Second English paragraph.</p><div data-translation-ui><button>收起译文</button><div data-block-id="translation:block:p2" data-translation-block>第二段中文译文。</div></div></article>`);
  const registry = new Map();
  dom.window.Highlight = class {constructor(...ranges) {this.ranges = ranges;}};
  const context = {window: dom.window, document: dom.window.document, Node: dom.window.Node,
    Element: dom.window.Element, NodeFilter: dom.window.NodeFilter, CSS: {highlights: registry}};
  const modules = new Map();
  const load = name => {
    if (modules.has(name)) return modules.get(name);
    const source = fs.readFileSync(new URL(`../src/lib/${name}.ts`, import.meta.url), 'utf8');
    const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
    const exports = {};
    vm.runInNewContext(code, {...context, exports, require: path => load(path.replace('./', ''))});
    modules.set(name, exports);
    return exports;
  };
  const anchors = load('anchors');
  const root = dom.window.document.querySelector('article');
  const block = id => [...root.querySelectorAll('[data-block-id]')].find(node => node.dataset.blockId === id);
  const select = (from, start, to, end) => {
    const range = dom.window.document.createRange();
    range.setStart(block(from).firstChild, start); range.setEnd(block(to).firstChild, end);
    const selection = dom.window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    return anchors.captureAnchor(root);
  };
  return {dom, anchors, root, block, select, registry};
}

test('Chinese selection retains UTF-16 offsets and restores after reopening the document', () => {
  const current = fixture();
  const id = 'translation:block:p1';
  const text = current.block(id).textContent;
  const anchor = current.select(id, 0, id, text.length);
  assert.equal(anchor.quote, text);
  assert.equal(anchor.end.offset, text.length);
  const reopened = fixture();
  assert.equal(reopened.anchors.resolveAnchor(reopened.root, JSON.parse(JSON.stringify(anchor))).toString(), text);
});

test('cross-paragraph Chinese highlights exclude English paragraphs and controls', () => {
  const {anchors, root, select, block, registry} = fixture();
  const anchor = select('translation:block:p1', 2, 'translation:block:p2', 5);
  assert.equal(anchor.quote, block('translation:block:p1').textContent.slice(2)+block('translation:block:p2').textContent.slice(0, 5));
  assert.ok(anchors.resolveAnchor(root, anchor));
  const cleanup = anchors.renderTextHighlights(root, [{id: 'chinese-note', type: 'note', style: 'underline', color: '#ef7474', anchor}]);
  const ranges = registry.get('paperlight-chinese-note').ranges;
  assert.equal(ranges.length, 2);
  assert.equal(ranges.map(range => range.toString()).join(''), anchor.quote);
  assert.ok(root.ownerDocument.head.textContent.includes('text-decoration:underline'));
  cleanup(); assert.equal(registry.size, 0);
});

test('cross-paragraph English highlights remain separate from translations', () => {
  const {anchors, root, select, registry} = fixture();
  const anchor = select('p1', 0, 'p2', 6);
  assert.ok(anchor.quote.includes('English'));
  assert.ok(!anchor.quote.includes('译文'));
  assert.ok(anchors.resolveAnchor(root, anchor));
  anchors.renderTextHighlights(root, [{id: 'english-note', type: 'highlight', color: '#ef7474', anchor}]);
  assert.ok(registry.get('paperlight-english-note').ranges.every(range => !range.toString().includes('中文')));
});

test('mixed source and translation selections cannot create confused anchors', () => {
  const {select} = fixture();
  assert.equal(select('p1', 0, 'translation:block:p1', 4), null);
});

test('translation annotations restore after hiding and expanding the cached translation', () => {
  const {anchors, root, select, block, registry} = fixture();
  const id = 'translation:block:p1';
  const anchor = select(id, 0, id, 4);
  block(id).hidden = true;
  block(id).hidden = false;
  anchors.renderTextHighlights(root, [{id: 'restored', type: 'highlight', color: '#ef7474', anchor}]);
  assert.equal(registry.get('paperlight-restored').ranges[0].toString(), '第一段中');
});