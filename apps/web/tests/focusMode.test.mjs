import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';
import {createRequire} from 'node:module';
import ts from 'typescript';
import React, {act} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {JSDOM} from 'jsdom';

const require = createRequire(import.meta.url);
function reader() {
  const modules = new Map();
  const source = fs.readFileSync(new URL('../src/components/ReaderShell.tsx', import.meta.url), 'utf8');
  const ast = ts.createSourceFile('ReaderShell.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const stateNames = [];
  const visit = node => {
    if (ts.isVariableDeclaration(node) && node.initializer && ts.isCallExpression(node.initializer) && node.initializer.expression.getText(ast) === 'useState') stateNames.push(node.name.elements[0].name.text);
    ts.forEachChild(node, visit);
  };
  visit(ast);
  let hookIndex = 0;
  const stateOverrides = {serverId: 'test-document', splitView: true};
  const hookReact = {...React, useState: initial => {
    const name = stateNames[hookIndex++];
    return React.useState(Object.hasOwn(stateOverrides, name) ? stateOverrides[name] : initial);
  }};
  const stub = (name, text = '') => ({__esModule: true, default: () => React.createElement('div', {'data-test-component': name}, text)});
  const mockRequire = name => {
    if (name === 'react') return hookReact;
    if (name === 'next/link') return {__esModule: true, default: ({children, ...props}) => React.createElement('a', props, children)};
    if (name === '@/data/demo.json') return JSON.parse(fs.readFileSync(new URL('../src/data/demo.json', import.meta.url), 'utf8'));
    if (name === '@/lib/stores') return Object.fromEntries(Object.entries(load('lib/stores.ts')).map(([key, store]) => [key, Object.assign(selector => selector ? selector(store.getState()) : store.getState(), {getState: store.getState})]));
    if (name === '@/lib/document') return load('lib/document.ts');
    if (name === './FocusModeTools') return load('components/FocusModeTools.tsx');
    if (name === './DocumentRenderer') return stub('document', 'English source and 中文译文');
    if (name === './PdfPane') return stub('pdf');
    if (name === './PaneDivider') return stub('divider');
    if (name === './AccountMenu') return stub('account');
    if (name === './ConfirmDialog') return stub('confirm');
    if (name === './ParagraphTranslation') return {TranslationToolbar: () => React.createElement('button', null, '一键翻译')};
    if (name === '@/lib/useDocumentTranslations') return {useDocumentTranslations: () => ({state: null, error: ''})};
    if (name === '@/lib/useReadingProgress') return {useReadingProgress: () => {}};
    if (name === '@/lib/useReadingActivity') return {useReadingActivity: () => {}};
    if (name === '@/lib/anchors') return {};
    return require(name);
  };
  const load = path => {
    if (modules.has(path)) return modules.get(path);
    const code = ts.transpileModule(fs.readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8'), {
      compilerOptions: {module: ts.ModuleKind.CommonJS, esModuleInterop: true, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022}
    }).outputText;
    const exports = {};
    vm.runInNewContext(code, {exports, require: mockRequire, setTimeout, clearTimeout, console, document: globalThis.document});
    modules.set(path, exports);
    return exports;
  };
  const stores = load('lib/stores.ts');
  const Reader = load('components/ReaderShell.tsx').default;
  const render = () => {
    hookIndex = 0;
    return new JSDOM(renderToStaticMarkup(React.createElement(Reader, {initialId: 'test-document', user: {id: 'user', role: 'user'}, onLogout: async () => {}}))).window.document;
  };
  return {stores, render, stateOverrides, FocusModeTools: load('components/FocusModeTools.tsx').default};
}

test('focus mode removes the normal header and PDF and keeps one standalone annotation toolbar', () => {
  const {stores, render} = reader();
  stores.useLayout.getState().set({focus: true, leftOpen: true, rightOpen: true});
  const dom = render();
  assert.equal(dom.querySelector('.reader-topbar'), null);
  assert.equal(dom.querySelector('[data-test-component="pdf"]'), null);
  assert.equal(dom.querySelector('[data-test-component="divider"]'), null);
  assert.equal(dom.querySelector('.side-rail'), null);
  assert.equal(dom.querySelector('.docked-tools'), null);
  assert.ok(dom.querySelector('.left-panel').hidden);
  assert.ok(dom.querySelector('.right-panel').hidden);
  assert.equal(dom.querySelectorAll('[aria-label="标注工具"]').length, 1);
  assert.ok(dom.querySelector('.focus-note-tools [aria-label="高亮"]'));
  assert.ok(dom.querySelector('.focus-note-tools [aria-label="退出专注模式"]'));
  assert.ok(dom.querySelector('article').textContent.includes('中文译文'));
});

test('focus toolbar stays at the top for every saved dock preference and restores the normal PDF layout on exit', () => {
  const {stores, render} = reader();
  for (const toolbarDock of ['top', 'bottom', 'left', 'right']) {
    stores.usePreferences.getState().set({toolbarDock});
    stores.useLayout.getState().set({focus: true});
    assert.ok(render().querySelector('.focus-note-tools [aria-label="标注工具"]'));
    assert.equal(render().querySelector('.docked-tools'), null);
  }
  stores.useLayout.getState().set({focus: false});
  const dom = render();
  assert.ok(dom.querySelector('.reader-topbar'));
  assert.ok(dom.querySelector('[data-test-component="pdf"]'));
  assert.ok(dom.querySelector('[data-test-component="divider"]'));
  assert.ok(dom.querySelector('.docked-right'));
  assert.equal(dom.querySelector('.focus-note-tools'), null);
});

test('focus-mode notes open a small editor without reopening either sidebar', () => {
  const {stores, render, stateOverrides} = reader();
  const note = {id: 'test-note', type: 'note', anchor: {start: {blockId: 'p1'}, end: {blockId: 'p1'}, quote: '选中的文字'}, note: '保存的笔记', color: '#ef7474'};
  stateOverrides.annotations = [note]; stateOverrides.focusNoteId = note.id;
  stores.useLayout.getState().set({focus: true});
  const dom = render();
  assert.equal(dom.querySelector('.focus-note-editor textarea').textContent, note.note);
  assert.ok(dom.querySelector('.focus-note-editor [aria-label="完成笔记"]'));
  assert.ok(dom.querySelector('.right-panel').hidden);
});
test('Escape completes a focus note first and then exits focus mode', async () => {
  const dom = new JSDOM('<div id="app"></div>', {url: 'http://localhost'});
  const previousWindow = globalThis.window, previousDocument = globalThis.document;
  globalThis.window = dom.window; globalThis.document = dom.window.document;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const {createRoot} = await import('react-dom/client');
  const root = createRoot(dom.window.document.querySelector('#app'));
  try {
    const {FocusModeTools} = reader();
    const calls = [];
    const props = {onExit: () => calls.push('exit'), onCloseNote: () => calls.push('close'), onChangeNote: () => {}, onSaveNote: text => calls.push(text)};
    await act(async () => root.render(React.createElement(FocusModeTools, {...props, note: {id: 'n', note: '已写好的笔记'}}, '标注工具')));
    assert.equal(dom.window.document.activeElement.tagName, 'TEXTAREA');
    await act(async () => dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', {key: 'Escape', bubbles: true})));
    assert.deepEqual(calls, ['已写好的笔记', 'close']);
    await act(async () => root.render(React.createElement(FocusModeTools, props, '标注工具')));
    await act(async () => dom.window.document.dispatchEvent(new dom.window.KeyboardEvent('keydown', {key: 'Escape', bubbles: true})));
    assert.equal(calls.at(-1), 'exit');
  } finally {
    await act(async () => root.unmount()); dom.window.close();
    globalThis.window = previousWindow; globalThis.document = previousDocument;
    delete globalThis.IS_REACT_ACT_ENVIRONMENT;
  }
});