import assert from 'node:assert/strict';
import test from 'node:test';
import {locateText, matchPdfSelection} from '../src/lib/pdfAnchors.ts';
test('aligns whitespace and ligatures while keeping original offsets',()=>{
  const text='A ﬁne\n  model'; const match=locateText(text,'A fine model');
  assert.equal(text.slice(match.start,match.end),text);
});
test('never changes math symbols or chooses a repeated match',()=>{
  assert.equal(locateText('x+y','x-y'),null);
  assert.equal(locateText('test test','test'),null);
});
test('matches a PDF quote only within its page and unique block',()=>{
  const blocks=[{id:'p1',page:1,type:'paragraph',text:'A fine model.'},{id:'p2',page:2,type:'paragraph',text:'A fine model.'}];
  assert.equal(matchPdfSelection(blocks,2,'fine model').start.blockId,'p2');
  assert.equal(matchPdfSelection([...blocks,{...blocks[0],id:'p3'}],1,'fine model'),null);
});
