import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import {JSDOM} from 'jsdom';
function setup() {
 const dom=new JSDOM('',{url:'http://localhost'}); let now=0, hidden=false, focused=true, cleanup;
 Object.defineProperty(dom.window.document,'hidden',{get:()=>hidden});dom.window.document.hasFocus=()=>focused;
 const intervals=[]; const writes=[]; const exports={};
 const code=ts.transpileModule(fs.readFileSync(new URL('../src/lib/useReadingActivity.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 vm.runInNewContext(code,{exports,require:()=>({useEffect:fn=>{cleanup=fn();}}),window:dom.window,document:dom.window.document,localStorage:dom.window.localStorage,performance:{now:()=>now},crypto:{randomUUID:()=> '11111111-1111-1111-1111-111111111111'},setInterval:fn=>{intervals.push(fn);return intervals.length;},clearInterval:()=>{},fetch:async (_,options)=>{writes.push(JSON.parse(options.body));return {ok:true};}});
 exports.useReadingActivity('doc','alice');
 return {dom,writes,advance:ms=>{now+=ms;},tick:()=>intervals[0](),hide:()=>{hidden=true;dom.window.document.dispatchEvent(new dom.window.Event('visibilitychange'));},close:()=>{cleanup();dom.window.close();},blur:()=>{focused=false;dom.window.dispatchEvent(new dom.window.Event('blur'));}};
}
test('per-document reading time includes the final seconds before refresh',async()=>{
 const s=setup();s.advance(3200);s.dom.window.dispatchEvent(new s.dom.window.Event('pagehide'));await Promise.resolve();
 assert.equal(s.writes[0].seconds,3);assert.equal(s.writes[0].documentId,'doc');s.close();
});
test('time in a hidden or unfocused reader is excluded',async()=>{
 const s=setup();s.advance(5000);s.tick();s.hide();await Promise.resolve();
 assert.equal(s.writes[0].seconds,5);s.advance(5000);s.tick();s.close();await Promise.resolve();assert.equal(s.writes.length,1);
 const other=setup();other.advance(3000);other.blur();other.advance(5000);other.tick();other.close();await Promise.resolve();assert.equal(other.writes[0].seconds,3);
});
