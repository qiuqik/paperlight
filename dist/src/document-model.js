/**
 * The reader's normalized document contract. Parser-specific output should be
 * adapted here so the rendering layer only handles Paperlight document JSON.
 * @typedef {{type:'text',text:string,bold?:boolean,italic?:boolean}|{type:'citation',referenceIds:string[],display:string}|{type:'link',href:string,display?:string}|{type:'figureLink',figureId:string,display:string}|{type:'tableLink',tableId:string,display:string}|{type:'inlineEquation'|'superscript'|'subscript',text:string}} InlineNode
 * @typedef {{id:string,title:string,level:number,type?:string,blocks:Array<object>}} Section
 * @typedef {{id:string,number:number,authors:string,title:string,venue?:string,year?:number,doi?:string,preview?:string}} Reference
 * @typedef {{id:string,status:string,metadata:object,sections:Section[],references:Reference[],figures:object[],tables:object[]}} DocumentModel
 */

export const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({
  '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
}[c]));

export function slugify(value, index = 0) {
  const slug = String(value || 'section').toLowerCase()
    .replace(/[^a-z0-9\u3400-\u9fff]+/g, '-').replace(/^-|-$/g, '').slice(0, 42);
  return `${slug || 'section'}-${index}`;
}

export function cleanPDFText(value) {
  return String(value ?? '')
    .replace(/[\u00ad\u034f\u200b-\u200f\ufeff\ufffe\ufffd]/g, '')
    .replace(/[ﬁ]/g, 'fi').replace(/[ﬂ]/g, 'fl').replace(/[ﬀ]/g, 'ff')
    .replace(/[ﬃ]/g, 'ffi').replace(/[ﬄ]/g, 'ffl')
    .replace(/([\p{L}]{2,})-\s+([a-z])/gu, '$1$2')
    .replace(/\s+/g, ' ').trim();
}

export function inlineNodes(text, {references = [], figures = [], tables = []} = {}) {
  const label=String(text??'').match(/^(Background|Participants|Interview Procedure|Analysis|Benchmark|Implementation|Requirement|Finding|Method|Result):(?=\s)/i);
  if(label)return [{type:'text',text:label[0],bold:true},...inlineNodes(String(text).slice(label[0].length),{references,figures,tables})];
  const knownRefs = new Set(references.map(ref => String(ref.id)));
  const figureByNumber = new Map(figures.map(f => [String(f.number), String(f.id)]));
  const tableByNumber = new Map(tables.map(t => [String(t.number), String(t.id)]));
  const pattern = /(\[(?:\d+(?:\s*[,–-]\s*\d+)*)\])|\b((?:Fig(?:ure)?\.?\s*\d+)|(?:Table\s*\d+))\b/gi;
  const nodes = [];
  let cursor = 0;
  for (const match of String(text ?? '').matchAll(pattern)) {
    const start = match.index;
    if (start > cursor) nodes.push({type:'text', text:String(text).slice(cursor, start)});
    if (match[1]) {
      const nums = expandCitationNumbers(match[1]);
      for (const number of nums) {
        nodes.push({type:'citation', referenceIds:[String(number)], display:`[${number}]`, unresolved:!knownRefs.has(String(number))});
      }
    } else {
      const display = match[2];
      const figureMatch = display.match(/^(?:Fig(?:ure)?\.?)\s*(\d+)$/i);
      const tableMatch = display.match(/^Table\s*(\d+)$/i);
      if (figureMatch && figureByNumber.has(figureMatch[1])) nodes.push({type:'figureLink', figureId:figureByNumber.get(figureMatch[1]), display});
      else if (tableMatch && tableByNumber.has(tableMatch[1])) nodes.push({type:'tableLink', tableId:tableByNumber.get(tableMatch[1]), display});
      else nodes.push({type:'text', text:display});
    }
    cursor = start + match[0].length;
  }
  if (cursor < String(text ?? '').length) nodes.push({type:'text', text:String(text).slice(cursor)});
  return nodes.length ? nodes : [{type:'text', text:String(text ?? '')}];
}

function expandCitationNumbers(label) {
  const body = label.slice(1, -1).replace(/\s/g, '');
  const values = new Set();
  for (const part of body.split(',')) {
    const range = part.match(/^(\d+)[–-](\d+)$/);
    if (range) {
      const from = Number(range[1]), to = Number(range[2]);
      for (let n = from; n <= Math.min(to, from + 30); n++) values.add(n);
    } else if (/^\d+$/.test(part)) values.add(Number(part));
  }
  return [...values].sort((a,b)=>a-b);
}

export function richLabelHTML(text) {
  const safe = escapeHTML(text);
  return safe.replace(/^([A-Z][A-Za-z /-]{1,35}:)(?=\s)/, '<strong class="label">$1</strong>');
}
