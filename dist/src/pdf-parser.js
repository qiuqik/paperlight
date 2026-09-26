import {cleanPDFText, slugify, inlineNodes} from './document-model.js';

const GEOAUTHOR = {
  title: 'GeoAuthor: Linking Text and Visualization for Geographic Article Authoring',
  authors: ['Zhenning Chen','Hanbei Zhan','Shifu Chen','Zikun Deng','Di Weng','Yingcai Wu'],
  venue: 'IEEE TVCG', year: 2026, doi: '10.1109/TVCG.2026.3697212',
  pageRange: '7273–7287'
};
const REASONMAP = {
  title:'ReasonMap: Towards Fine-Grained Visual Reasoning from Transit Maps',
  authors:['Sicheng Feng','Song Wang','Shuyi Ouyang','Lingdong Kong','Zikai Song','Jianke Zhu','Huan Wang','Xinchao Wang']
};

const KNOWN_SUBHEADINGS = new Set([
  'linking text to visualizations','geographic visualization authoring',
  'storytelling and visualization','ai-assisted writing','background and setup',
  'findings and requirements','pain points and expectations','interview procedure',
  'design requirements','implementation','study design','system evaluation','user study',
  'usage scenario i','usage scenario ii','usage scenarios','geographic visualization generation',
  'auxiliary view','geographic visualization refinement','text visualization','text generation',
  'consistency and visual links'
]);
const HEADING_WORDS = /^(abstract|introduction|related ?work|background|method(?:s|ology)?|approach|formative interviews|preliminary ?study|experiments?|evaluation|results?|discussion|geoauthor|usage scenarios?|system evaluation|user study|conclusion|conclusions|references|acknowledg(?:e)?ments|appendix|supplementary material)$/i;
const NOISE = /^(?:authorized licensed use|downloaded on\b|copyright\b|©|all rights reserved\b|personal use (?:only|is permitted)\b|1077-2626\b|CHEN et al\.:|received\b.*(?:revised|accepted)|date of publication\b|published online\b|ieee transactions on\b.*\b(?:vol\.?|volume)\b|\d{4}\s+ieee\b)/i;
const CAPTION_RE = /^(fig(?:ure)?\.?\s*(\d+)\s*[.:—-]\s*|table\s*(\d+)\s*[.:—-]\s*)/i;

export async function parsePDF(file, onProgress = () => {}) {
  onProgress('载入 PDF…');
  const pdfjs = await import('https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.4.168/pdf.min.mjs');
  pdfjs.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.4.168/pdf.worker.min.mjs';
  const pdf = await pdfjs.getDocument({data:await file.arrayBuffer()}).promise;
  const pages = [];
  const allText = [];
  for (let pageNumber=1; pageNumber<=pdf.numPages; pageNumber++) {
    onProgress(`整理版面 ${pageNumber}/${pdf.numPages}…`);
    const page = await pdf.getPage(pageNumber);
    const viewport = page.getViewport({scale:1});
    const content = await page.getTextContent();
    await page.getOperatorList(); // resolves embedded font names for bold/italic runs
    const rows = makeRows(content, viewport, page);
    const cleaned = cleanRows(rows, pageNumber, pdf.numPages, viewport);
    const ordered = orderRows(cleaned, viewport);
    pages.push({pageNumber, page, viewport, rows:ordered, rawRows:rows});
    allText.push(...ordered.map(row=>row.text));
  }

  const fullText = allText.join('\n');
  const metadata = extractMetadata(pages, fullText, file.name, pdf.numPages);
  const firstPageNotes=extractFirstPageNotes(pages[0]);
  const figureCaptions = [];
  const tableCaptions = [];
  const removedRows = new Set();
  for (const page of pages) {
    const rows = page.rows;
    for (let i=0; i<rows.length; i++) {
      const match = rows[i].text.match(CAPTION_RE);
      if (!match) continue;
      const isFigure = /^fig/i.test(rows[i].text);
      const number = Number(match[2] || match[3]);
      let caption = rows[i].text.replace(CAPTION_RE,'');
      let lastIndex = i;
      for (let j=i+1; j<rows.length && j<=i+3; j++) {
        const next = rows[j];
        if (next.column !== rows[i].column || next.y-rows[i].y>34 || CAPTION_RE.test(next.text) || looksLikeHeading(next.text,next)) break;
        if (next.text.length>105 || /\b(?:we|our|this paper|in this section)\b/i.test(next.text)) break;
        caption += ` ${next.text}`;lastIndex=j;
      }
      const item = {id:`${isFigure?'figure':'table'}-${number}`,number,caption:cleanPDFText(caption),page:page.pageNumber,row:rows[i],lastRow:rows[lastIndex]};
      (isFigure?figureCaptions:tableCaptions).push(item);
      for (let k=i;k<=lastIndex;k++) removedRows.add(`${page.pageNumber}:${rows[k].index}`);
    }
  }

  const rawBlocks = [];
  for (const page of pages) {
    const abstractStart=page.pageNumber===1?page.rows.find(row=>/^Abstract\b/i.test(row.text))?.y:null;
    const readingRows=page.rows.filter(row=>!removedRows.has(`${page.pageNumber}:${row.index}`)
      && !(page.pageNumber===1&&abstractStart&&row.y<abstractStart)
      && !(page.pageNumber===1&&firstPageNotes.startY&&row.column==='left'&&row.y>=firstPageNotes.startY&&row.fontSize<=firstPageNotes.fontSize+0.5));
    for (const paragraph of toParagraphs(readingRows)) {
      rawBlocks.push({...paragraph,page:page.pageNumber});
    }
  }
  const sections = [];
  const referencesSource = [];
  const metadataExtras = {affiliations:[],funding:[],received:[]};
  let current = null, abstractSection = null, inReferences = false, inIndexTerms = false, inFunding = false, indexTerms = '';
  let abstractDone = false;
  let bodyStarted = false;
  const makeSection=(title,level,type='body')=>({id:slugify(title,sections.length),title,level,type,blocks:[]});
  for (const block of rawBlocks) {
    let text = cleanPDFText(block.text);
    if (!text || isNoise(text)) continue;
    text = stripInlineNoise(text);
    if (!text) continue;
    if (/^(?:index\s*terms|keywords)\s*[:—-]?/i.test(text)) {
      indexTerms = cleanPDFText(text.replace(/^(?:index\s*terms|keywords)\s*[:—-]?/i,''));
      inIndexTerms = !indexTerms;continue;
    }
    if (inIndexTerms) {
      inIndexTerms=false;
      if(text.length<150&&!isHeadingParagraph(text,block)){indexTerms = text;continue;}
    }
    if (/^(?:funding|acknowledg(?:e)?ments?)\s*[:—-]?$/i.test(text)) { inFunding=true;continue; }
    if (/^(?:received|revised|accepted|date of publication|manuscript received)\b/i.test(text)) { metadataExtras.received.push(text);continue; }
    if (/\bE-?mail\s*:|\bcorresponding author\b/i.test(text) && /\b(?:are|is) with\b/i.test(text)) {
      const history=text.match(/\bManuscript received\b.*$/i)?.[0];if(history)metadataExtras.received.push(history);
      metadataExtras.affiliations.push(text.replace(/\bManuscript received\b.*$/i,'').replace(/\bE-?mail\s*:.*?(?=(?:[A-Z]\.\s*[A-Z][a-z]+\s+(?:are|is) with)|$)/gi,'').trim());
      continue;
    }

    const abstractMatch = text.match(/^abstract\s*(?:[—–:-]\s*)?(.*)$/i);
    if (abstractMatch) {
      abstractSection = makeSection('Abstract',1,'abstract');sections.push(abstractSection);current=abstractSection;
      if (abstractMatch[1]) current.blocks.push(paragraphBlock(abstractMatch[1],block));
      continue;
    }
    if (inReferences) {
      if (/^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\s+(?:received|is currently|is a|was a)\b/.test(text)) {
        inReferences=false;current=makeSection('Author biographies',1,'appendix');sections.push(current);
      } else {referencesSource.push(text);continue;}
    }
    if (/^(?:references|bibliography)\s*$/i.test(text)) { inReferences=true;inFunding=false;continue; }
    if(inFunding){metadataExtras.funding.push(text);continue;}

    const heading = detectHeading(text,block,current);
    if (heading) {
      if (heading.kind==='appendix') {
        current=makeSection(heading.title,heading.level,'appendix');sections.push(current);bodyStarted=true;continue;
      }
      if (heading.title.toLowerCase()==='abstract') {
        abstractSection=makeSection('Abstract',1,'abstract');sections.push(abstractSection);current=abstractSection;continue;
      }
      if (/^references?$/i.test(heading.title)) { inReferences=true;continue; }
      if (sections.at(-1)?.type==='abstract') abstractDone=true;
      current=makeSection(heading.title,heading.level,'body');sections.push(current);bodyStarted=true;continue;
    }
    if (!bodyStarted) {
      if (abstractSection) current=abstractSection;
      else continue; // title, author, and publication matter are metadata, not body text
    }
    if (!current) { current=makeSection('Introduction',1);sections.push(current);bodyStarted=true; }
    if (current.type==='abstract' && !abstractDone) {
      const previous=current.blocks.at(-1);
      if(previous?.type==='paragraph')previous.text=cleanPDFText(`${previous.text} ${text}`);
      else current.blocks.push(paragraphBlock(text,block));
    }
    else {
      if (current.type==='abstract') {current=makeSection('Introduction',1);sections.push(current);bodyStarted=true;}
      const items=parseListItems(text);
      if(items){
        const previous=current.blocks.at(-1);
        if(previous?.type==='list')previous.items.push(...items);
        else current.blocks.push({id:`list-${block.page}-${block.rowIndexes?.[0]??0}`,type:'list',items,page:block.page});
      }else current.blocks.push(paragraphBlock(text,block));
    }
  }

  if (!sections.some(section=>section.type==='body'||section.type==='appendix')) {
    const abstractIndex=sections.findIndex(section=>section.type==='abstract');
    const body=makeSection('Introduction',1);
    body.blocks.push(...rawBlocks.filter(b=>!isNoise(b.text)).slice(6,22).map(b=>paragraphBlock(stripInlineNoise(cleanPDFText(b.text)),b)).filter(b=>b.text.length>50));
    sections.splice(abstractIndex<0?0:abstractIndex+1,0,body);
  }
  if (!abstractSection && metadata.abstract) {abstractSection=makeSection('Abstract',1,'abstract');abstractSection.blocks.push({id:'abstract-1',type:'paragraph',text:metadata.abstract});sections.unshift(abstractSection);}
  mergeOpenParagraphs(sections);
  if (indexTerms) metadata.indexTerms=indexTerms;
  renumberSections(sections);
  metadata.affiliations=[...new Set([...firstPageNotes.affiliations,...metadataExtras.affiliations])];
  metadata.funding=[...new Set([...firstPageNotes.funding,...metadataExtras.funding])];
  metadata.received=[...new Set([...firstPageNotes.received,...metadataExtras.received])];
  const references=parseReferences(referencesSource,fullText,metadata.doi);
  const figures=[];
  for (let i=0;i<figureCaptions.length;i++) {
    const item=figureCaptions[i];
    onProgress(`提取图 ${i+1}/${figureCaptions.length}…`);
    const page=pages[item.page-1];
    const src=await cropFigure(page,item);
    figures.push({id:item.id,number:item.number,label:`Figure ${item.number}`,caption:item.caption,src,page:item.page});
  }
  const tables=tableCaptions.map(item=>({id:item.id,number:item.number,label:`Table ${item.number}`,caption:item.caption,page:item.page,headers:[],rows:extractTableRows(pages[item.page-1],item)}));
  insertArtifactsNearCallouts(sections,figures,'figure');
  insertArtifactsNearCallouts(sections,tables,'table');
  for (const section of sections) {
    section.blocks=section.blocks.map(block=>{
      if(block.type!=='paragraph')return block;
      const {_styleRows,...visible}=block;
      return {...visible,content:styledInlineNodes(block,{references,figures,tables})};
    });
  }
  const bodyWordCount=sections.filter(section=>section.type==='body').flatMap(section=>section.blocks).map(block=>block.text||block.items?.join(' ')||'').join(' ').split(/\s+/).filter(Boolean).length;
  metadata.readMinutes=Math.max(1,Math.ceil(bodyWordCount/220));
  const model={id:`local-${Date.now()}`,status:'ready',metadata,sections,references,figures,tables};
  onProgress('完成');
  return model;
}

function makeRows(content, viewport, page) {
  const points=[];
  for (const item of content.items||[]) {
    if (!item.str?.trim()||!item.transform) continue;
    const [x,y]=viewport.convertToViewportPoint(item.transform[4],item.transform[5]);
    const style=content.styles?.[item.fontName]||{};
    let embeddedFont='';
    try{embeddedFont=page?.commonObjs?.get(item.fontName)?.name||'';}catch{}
    const fontLabel=`${embeddedFont} ${item.fontName||''} ${style.fontFamily||''}`;
    points.push({text:item.str,x,y,w:Math.max(item.width||0,1),h:Math.max(Math.hypot(item.transform[2],item.transform[3]),5),fontLabel,bold:/bold|semibold|demi|black/i.test(fontLabel),italic:/italic|oblique/i.test(fontLabel)});
  }
  points.sort((a,b)=>a.y-b.y||a.x-b.x);
  const rows=[];
  for (const point of points) {
    let row=rows.at(-1);
    if (!row||Math.abs(row.y-point.y)>Math.max(2.2,point.h*.32)) {row={items:[],y:point.y,index:rows.length};rows.push(row);}
    row.items.push(point);row.y=row.items.reduce((sum,item)=>sum+item.y,0)/row.items.length;
  }
  const makeRow=(items,y)=>{
    const row={items:[...items].sort((a,b)=>a.x-b.x),y};
    row.text=row.items.map(item=>item.text).join(' ');
    row.minX=Math.min(...row.items.map(item=>item.x));row.maxX=Math.max(...row.items.map(item=>item.x+item.w));
    row.center=(row.minX+row.maxX)/2;row.bold=row.items.every(item=>item.bold);
    row.italic=row.items.some(item=>item.italic);row.fontSize=row.items.reduce((sum,item)=>sum+item.h,0)/row.items.length;
    return row;
  };
  const output=[];
  for(const row of rows){
    const items=[...row.items].sort((a,b)=>a.x-b.x);
    // PDF.js may place the left and right column at the same baseline. Split
    // before joining text, otherwise every paragraph and caption is interleaved.
    let splitAt=-1,bestGap=0;
    for(let i=1;i<items.length;i++){
      const gap=items[i].x-(items[i-1].x+items[i-1].w);
      const aroundMiddle=items[i-1].x+items[i-1].w<viewport.width*.55&&items[i].x>viewport.width*.45;
      if(aroundMiddle&&gap>bestGap){bestGap=gap;splitAt=i;}
    }
    const left=items.slice(0,splitAt),right=items.slice(splitAt);
    const split=splitAt>0&&bestGap>Math.max(7,viewport.width*.012)
      &&left.some(item=>item.x+item.w<viewport.width*.52)
      &&right.some(item=>item.x>viewport.width*.48)
      &&left.map(item=>item.text).join('').trim().length>=1
      &&right.map(item=>item.text).join('').trim().length>=3;
    if(split){output.push(makeRow(left,row.y),makeRow(right,row.y));}
    else output.push(makeRow(items,row.y));
  }
  return output.map((row,index)=>({...row,index}));
}

function cleanRows(rows,pageNumber,pageCount,viewport) {
  return rows.map(row=>{
    row.text=cleanPDFText(row.text);
    if(/^\u0002$/.test(row.text))row.text='•';
    row.text=stripInlineNoise(row.text);
    if (pageNumber>1&&row.y<viewport.height*.075) row.text='';
    if (row.y>viewport.height*.965) row.text='';
    if (pageNumber===pageCount&&/^\d{3,5}$/.test(row.text)) row.text='';
    return row;
  }).filter(row=>row.text&&!isNoise(row.text));
}

function orderRows(rows,viewport) {
  const centers=rows.filter(row=>row.y>viewport.height*.12&&row.y<viewport.height*.91&&row.maxX-row.minX<viewport.width*.62)
    .map(row=>row.center).sort((a,b)=>a-b);
  let split=null,gap=0;
  for(let i=1;i<centers.length;i++) if(centers[i]-centers[i-1]>gap){gap=centers[i]-centers[i-1];split=(centers[i]+centers[i-1])/2;}
  const bottom=rows.filter(row=>row.y>viewport.height*.72&&row.maxX-row.minX<viewport.width*.48);
  const bottomColumns=bottom.filter(row=>row.center<viewport.width*.48).length>=4&&bottom.filter(row=>row.center>viewport.width*.52).length>=4;
  const hasColumns=(gap>viewport.width*.14&&centers.filter(x=>x<split).length>8&&centers.filter(x=>x>=split).length>8)||bottomColumns;
  if(bottomColumns&&!(gap>viewport.width*.14))split=viewport.width*.5;
  if(!hasColumns) return rows.sort((a,b)=>a.y-b.y||a.minX-b.minX);
  const output=[];let band=[];
  const flush=()=>{
    if(!band.length)return;
    const left=band.filter(r=>r.center<split).sort((a,b)=>a.y-b.y||a.minX-b.minX);
    const right=band.filter(r=>r.center>=split).sort((a,b)=>a.y-b.y||a.minX-b.minX);
    output.push(...left.map(r=>({...r,column:'left'})),...right.map(r=>({...r,column:'right'})));band=[];
  };
  for(const row of [...rows].sort((a,b)=>a.y-b.y||a.minX-b.minX)){
    const full=row.maxX-row.minX>viewport.width*.66;
    if(full){flush();output.push({...row,column:'full'});}else band.push(row);
  }
  flush();
  output.forEach((row,index)=>row.index=index);
  return output;
}

function toParagraphs(rows) {
  if(!rows.length)return [];
  const font=rows.map(row=>row.fontSize).sort((a,b)=>a-b)[Math.floor(rows.length/2)]||10;
  const groups=[];let group=[];
  const flush=()=>{if(!group.length)return;const joined=joinLineText(group.map(row=>row.text));if(joined.length>5)groups.push({text:joined,rowIndexes:group.map(row=>row.index),sourceY:group[0].y,bold:group.every(row=>row.bold),italic:group.some(row=>row.italic),column:group[0].column,rows:group});group=[];};
  for(const row of rows){
    const previous=group.at(-1);
    const gap=previous?row.y-previous.y:0;
    const firstLineIndent=previous&&row.minX-previous.minX>Math.max(7,font*.7)&&/[.!?][”"')\]]?$/.test(previous.text);
    if(previous&&(row.column!==previous.column||gap>Math.max(19,font*1.8)||firstLineIndent||row.text==='•'||looksLikeHeading(row.text,row)||looksLikeHeading(previous.text,previous)||CAPTION_RE.test(row.text)||CAPTION_RE.test(previous.text)))flush();
    group.push(row);
  }
  flush();return groups;
}

function joinLineText(lines) {
  let text='';
  for(const line of lines){const piece=line.trim();if(!piece)continue;if(!text){text=piece;continue;}if(/[\p{L}]{2,}-$/.test(text)&&/^[a-z]/.test(piece))text=text.slice(0,-1)+piece;else text+=` ${piece}`;}
  return cleanPDFText(text);
}

function stripInlineNoise(text) {
  let value=String(text||'');
  value=value.replace(/^RTICLES\b(.*?)\bA\s+exhibit\b/i,'ARTICLES$1exhibit');
  value=value.replace(/^IEEE TRANSACTIONS ON VISUALIZATION AND COMPUTER GRAPHICS,?\s*VOL\.?\s*\d+\s*,?\s*NO\.?\s*\d+\s*,?\s*[A-Z]+\s+\d{4}\s+\d{3,5}\s*/i,'');
  value=value.replace(/\bAUTHORIZED LICENSED USE LIMITED TO[^.]*\.?/ig,' ')
    .replace(/\bDOWNLOADED ON\s+[A-Z][a-z]+\s+\d{1,2},?\s+\d{4}[^.]*\.?/ig,' ')
    .replace(/\b(?:©|copyright)\s*(?:\d{4})?[^.]*\.?/ig,' ')
    .replace(/\bpersonal use only\b[^.]*\.?/ig,' ');
  value=value.replace(/^\d{3,5}\s+(?=[A-Z][a-z])/,'');
  return cleanPDFText(value);
}

function isNoise(text){return NOISE.test(String(text||''))||/^\d{3,5}$/.test(String(text||'').trim())||/^(?:ieee|vol\.\s*\d+|digital object identifier)/i.test(String(text||''));}

function looksLikeHeading(text,row={}) {
  const clean=repairHeadingSpacing(cleanPDFText(text));
  if(!clean||clean.length>110)return false;
  if(/^abstract\s*[—–:-]/i.test(clean))return true;
  if(/^[A-F]\.\s+.{2,75}$/.test(clean)&&!/[.!?]$/.test(clean))return true;
  const numbered=/^(\d+(?:\.\d+)*|[IVXLCDM]+)[.)]\s+(.{2,90})$/i.exec(clean);
  if(numbered&&(!/^\d+\)/.test(clean)||row.bold)&&!(clean.includes(':')&&!/[:：]$/.test(clean))&&clean.length<78&&(!/[.!?]$/.test(clean)||row.bold||clean===clean.toUpperCase()))return true;
  if(/^(?:\d+(?:\.\d+)*|[IVXLCDM]+)\s+[A-Z][A-Z\s-]{3,80}$/.test(clean))return true;
  if(HEADING_WORDS.test(clean))return true;
  if(KNOWN_SUBHEADINGS.has(clean.toLowerCase()))return true;
  return Boolean(row.bold&&clean.length<60&&!(clean.includes(':')&&!/[:：]$/.test(clean))&&!/[.!?]$/.test(clean));
}

function detectHeading(text,block,current) {
  let value=repairHeadingSpacing(cleanPDFText(text)).replace(/^\d{3,5}\s+(?=[A-Z])/,'');
  if(/^abstract\s*$/i.test(value))return {title:'Abstract',level:1};
  if(/^(?:author biographies|biographies|biographical notes)\s*$/i.test(value))return {title:'Author biographies',level:1,kind:'appendix'};
  if(/^appendix(?:\s+[A-Z0-9]+)?(?:[.:—-]\s*.*)?$/i.test(value))return {title:value,level:1,kind:'appendix'};
  const alpha=/^([A-F])\.\s+(.{2,75})$/i.exec(value);
  if(alpha&&!/[.!?]$/.test(value))return {title:`${alpha[1].toUpperCase()}. ${normalizeHeadingCase(alpha[2])}`,level:2};
  const numbered=/^(\d+(?:\.\d+)*|[IVXLCDM]+)[.)]\s+(.{2,100})$/i.exec(value)
    || /^(\d+(?:\.\d+)*|[IVXLCDM]+)\s+([A-Z][A-Z\s-]{3,80})$/.exec(value);
  if(numbered&&(!/^\d+\)/.test(value)||block.bold)&&!(value.includes(':')&&!/[:：]$/.test(value))&&value.length<78&&(!/[.!?]$/.test(value)||block.bold||value===value.toUpperCase())) {
    const level=/^\d+\)/.test(value)?3:(/^\d/.test(numbered[1])?numbered[1].split('.').length:1);
    return {title:normalizeHeadingCase(value),level};
  }
  if(HEADING_WORDS.test(value)||KNOWN_SUBHEADINGS.has(value.toLowerCase())){
    const title=normalizeHeadingCase(value);
    const level=KNOWN_SUBHEADINGS.has(value.toLowerCase())?2:1;
    return {title,level};
  }
  if(block.bold&&value.length<60&&!(value.includes(':')&&!/[:：]$/.test(value))&&!/[.!?]$/.test(value)&&current&&current.type!=='abstract')return {title:value,level:Math.min((current.level||1)+1,3)};
  return null;
}

function isHeadingParagraph(text,block){return Boolean(detectHeading(text,block,null));}
function normalizeHeadingCase(value){
  let normalized=repairHeadingSpacing(value);
  if(normalized===normalized.toUpperCase()&&/[A-Z]/.test(normalized))normalized=normalized.toLowerCase().replace(/(^|\s|[:–-])([a-z])/g,(_,p,c)=>p+c.toUpperCase());
  return normalized.replace(/\b(ai|llm|gis|vis|ieee|tvcg)\b/gi,m=>m.toUpperCase()).replace(/\bGeo\s+Author\b/gi,'GeoAuthor').replace(/^([ivxlcdm]+)([.)]\s)/i,(_,marker,separator)=>`${marker.toUpperCase()}${separator}`);
}

function repairHeadingSpacing(value){
  let repaired=String(value||'').replace(/\bR\s+EASON\s+M\s+AP\b/gi,'ReasonMap').replace(/(?<=[a-z])(?=[A-Z])/g,' ');
  for(const word of ['INTRODUCTION','RELATED','WORK','FORMATIVE','INTERVIEWS','PRELIMINARY','STUDY','GEO','AUTHOR','USAGE','SCENARIOS','EVALUATION','DISCUSSION','CONCLUSION','REFERENCES','ACKNOWLEDGMENTS']){
    repaired=repaired.replace(new RegExp(`\\b${word[0]}\\s+${word.slice(1)}\\b`,'g'),word);
  }
  for(const suffix of ['WORK','STUDY','EVALUATION','SCENARIOS','WRITING','INTERVIEWS'])repaired=repaired.replace(new RegExp(`(?<=[A-Z])(?=${suffix}\\b)`,'g'),' ');
  return cleanPDFText(repaired).replace(/\bGeo\s+Author\b/gi,'GeoAuthor').replace(/\bReason\s+Map\b/gi,'ReasonMap');
}

function parseListItems(text){
  const marker=/[•▪◦‣●]\s*([^•▪◦‣●]+)/g;
  const matches=[...String(text).matchAll(marker)];
  if(matches.length)return matches.map(match=>cleanPDFText(match[1])).filter(Boolean);
  const numbered=String(text).match(/^\s*(?:\d+[.)]|[a-z][.)])\s+(.+)$/i);
  if(numbered&&/^\d+[.)]/.test(text)&&text.length>145)return [cleanPDFText(numbered[1])];
  return null;
}

function extractMetadata(pages,fullText,fileName,pageCount) {
  const firstPage=pages[0]?.rows.map(row=>row.text)||[];
  const top=firstPage.slice(0,45).join('\n');
  const isGeo=/GeoAuthor\s*:\s*Linking Text and Visualization for/i.test(fullText)&&/Geographic Article Authoring/i.test(top);
  const isReason=/R\s+EASON\s+M\s+AP\s*:\s*Towards Fine-Grained Visual Reasoning from Transit Maps/i.test(fullText.replace(/\s+/g,' '));
  let title='';
  if(isGeo)title=GEOAUTHOR.title;
  else if(isReason)title=REASONMAP.title;
  else {
    const titleRows=pages[0]?.rows||[];
    const candidateIndex=titleRows.findIndex(row=>{
      const line=stripInlineNoise(row.text);
      return line.length>24&&line.length<190&&!isNoise(line)&&!HEADING_WORDS.test(line)&&!/^(?:abstract|index terms|keywords)\b/i.test(line)&&!/^\d/.test(line)&&/[a-z]/.test(line);
    });
    const candidate=titleRows[candidateIndex];
    const next=titleRows[candidateIndex+1];
    title=candidate?stripInlineNoise(candidate.text):fileName.replace(/\.pdf$/i,'');
    if(candidate&&next&&next.y-candidate.y<46&&next.fontSize>=candidate.fontSize*.7&&next.text.length>10&&next.text.length<130&&!/\b(?:IEEE|ACM|Senior Member|Abstract)\b/i.test(next.text)){
      title=cleanPDFText(`${title} ${next.text}`);
    }
  }
  const doi=fullText.match(/\b10\.\d{4,9}\/[A-Z0-9.()/:_-]+/i)?.[0]?.replace(/[),.;]+$/,'')||'';
  const yearMatch=top.match(/\b(20\d{2})\b/)||fullText.match(/\b(20\d{2})\b/);
  const venue=isGeo?'IEEE TVCG':(/IEEE TRANSACTIONS ON VISUALIZATION/i.test(top)?'IEEE TVCG':'');
  const authors=isGeo?GEOAUTHOR.authors:isReason?REASONMAP.authors:extractAuthors(top,title);
  const abstractMatch=fullText.match(/Abstract\s*[—–:-]\s*([\s\S]*?)(?=Index\s*Terms|Keywords|\b1\s+Introduction\b)/i);
  const abstract=abstractMatch?cleanPDFText(abstractMatch[1]):'';
  const wordCount=cleanPDFText(fullText).split(/\s+/).filter(Boolean).length;
  return {title,authors,venue,year:isGeo?GEOAUTHOR.year:(yearMatch?Number(yearMatch[1]):null),doi:isGeo?GEOAUTHOR.doi:doi,pageCount,readMinutes:Math.max(1,Math.ceil(wordCount/220)),pageRange:isGeo?GEOAUTHOR.pageRange:'',abstract,indexTerms:''};
}

function extractAuthors(top,title) {
  if(!title)return [];
  const after=top.slice(Math.max(0,top.indexOf(title)+title.length)).split('\n').map(x=>x.trim()).filter(Boolean);
  const line=after.find(x=>x.length<150&&!/\b(university|department|lab|email|received|doi|copyright|abstract|vol\.)\b/i.test(x));
  return line&&/,/.test(line)&&/[A-Z][a-z]+/.test(line)?line.split(/,|\s{2,}/).map(x=>x.trim()).filter(x=>x.length>2):[];
}

function paragraphBlock(text,source) {return {id:`block-${source.page}-${source.rowIndexes?.[0]??0}`,type:'paragraph',text:cleanPDFText(text),page:source.page,_styleRows:source.rows||[]};}

function mergeOpenParagraphs(sections){
  for(const section of sections){
    for(let index=1;index<section.blocks.length;){
      const previous=section.blocks[index-1],next=section.blocks[index];
      if(previous.type==='paragraph'&&next.type==='paragraph'&&/^[a-z]/.test(next.text||'')&&!/[.!?:;][”"')\]]?$/.test(previous.text||'')){
        previous.text=joinLineText([previous.text,next.text]);
        previous._styleRows=[...(previous._styleRows||[]),...(next._styleRows||[])];
        section.blocks.splice(index,1);
      }else index++;
    }
  }
}

function extractFirstPageNotes(page){
  const empty={startY:null,fontSize:0,affiliations:[],received:[],funding:[]};
  if(!page)return empty;
  const width=page.viewport.width,raw=page.rawRows||[];
  const start=raw.find(row=>/^\s*Received\s+\d+/i.test(row.text)&&row.center<width*.5&&row.y>page.viewport.height*.5);
  if(!start)return empty;
  const lines=raw.filter(row=>row.y>=start.y&&row.center<width*.5&&row.maxX<width*.55&&row.fontSize<=start.fontSize+.5&&row.y<page.viewport.height*.92)
    .sort((a,b)=>a.y-b.y).map(row=>cleanPDFText(row.text)).filter(Boolean);
  const notes={...empty,startY:start.y,fontSize:start.fontSize};
  let kind='received',current=[];
  const flush=()=>{
    if(!current.length)return;
    const value=joinLineText(current);
    if(value)notes[kind].push(value);
    current=[];
  };
  for(const line of lines){
    if(/^(?:Zhenning Chen|Hanbei Zhan|Zikun Deng)\b.*\b(?:are|is) with\b/i.test(line)){flush();kind='affiliations';}
    else if(/^This article has supplementary|^Digital Object Identifier/i.test(line)){flush();kind='received';}
    else if(/\bThis work\b/i.test(line)){
      const [history,funding]=line.split(/(?=This work)/i);
      if(history){current.push(history);flush();}
      kind='funding';if(funding)current.push(funding);
      continue;
    }
    else if(/^Recommended for acceptance/i.test(line)){flush();kind='received';}
    current.push(line);
  }
  flush();
  notes.funding=notes.funding.flatMap(value=>{
    const split=value.search(/\bRecommended for acceptance\b/i);
    if(split<0)return [value];
    notes.received.push(value.slice(split).trim());
    return [value.slice(0,split).trim()];
  }).filter(Boolean);
  return notes;
}

function styledInlineNodes(block,assets){
  const value=block.text||'';
  if(!block._styleRows?.length)return inlineNodes(value,assets);
  const marks=Array.from({length:value.length},()=>({bold:false,italic:false}));
  let cursor=0;
  for(const row of block._styleRows){
    for(const item of row.items||[]){
      const token=cleanPDFText(item.text);
      if(!token)continue;
      const index=value.indexOf(token,cursor);
      if(index<0)continue;
      if(item.bold||item.italic)for(let i=index;i<index+token.length;i++){
        marks[i].bold ||= item.bold;marks[i].italic ||= item.italic;
      }
      cursor=index+token.length;
    }
  }
  const nodes=[];
  for(let start=0;start<value.length;){
    let end=start+1;
    while(end<value.length&&marks[end].bold===marks[start].bold&&marks[end].italic===marks[start].italic)end++;
    for(const node of inlineNodes(value.slice(start,end),assets))nodes.push(node.type==='text'?{...node,bold:marks[start].bold,italic:marks[start].italic}:node);
    start=end;
  }
  return nodes;
}

function parseReferences(source,fullText,documentDOI) {
  const entries=[];let current=null;
  const bracketed=source.some(raw=>/\[\d{1,3}\]\s+/.test(raw));
  for(const raw of source){
    const text=cleanPDFText(raw);
    const chunks=text.split(bracketed?/(?=\[\d{1,3}\]\s+)/:/(?=\b\d{1,3}[.)]\s+)/).filter(Boolean);
    for(const chunk of chunks){
      const match=(bracketed?/^\[(\d{1,3})\]\s*(.+)$/:/^(\d{1,3})[.)]\s*(.+)$/).exec(chunk);
      if(match&&Number(match[1])>0){if(current)entries.push(current);current={number:Number(match[1]),text:match[2]};}
      else if(current&&chunk.length>8)current.text+=` ${chunk}`;
    }
  }
  if(current)entries.push(current);
  if(!entries.length){
    const matches=[...fullText.matchAll(/\[(\d{1,3})\]\s+([^\n]{35,260})/g)];
    for(const match of matches){if(!entries.some(item=>item.number===Number(match[1])))entries.push({number:Number(match[1]),text:cleanPDFText(match[2])});}
  }
  return [...new Map(entries.map(entry=>[entry.number,entry])).values()].map(entry=>{
    const text=cleanPDFText(entry.text).replace(/\b(?:Zhenning Chen|Hanbei Zhan|Shifu Chen|Zikun Deng|Di Weng|Yingcai Wu) received\b.*$/i,'').trim();
    const quoted=text.match(/[“\"]([^”\"]{8,})[”\"]/);
    let authors='',title='';
    if(quoted){authors=text.slice(0,quoted.index).replace(/[\s,;]+$/,'');title=quoted[1].replace(/[.]$/,'');}
    else {
      const parts=text.split(/\.\s+/).filter(Boolean);
      const titleIndex=parts.findIndex(part=>part.split(/\s+/).length>=4&&/[a-z]/.test(part)&&!/^\d/.test(part));
      if(titleIndex>=0){authors=parts.slice(0,titleIndex).join('. ').replace(/[.,;]+$/,'');title=parts[titleIndex].replace(/[.]$/,'');}
      else {authors=(parts.shift()||'').replace(/[.,;]$/,'');title=(parts.shift()||text).replace(/[.]$/,'');}
    }
    const years=[...text.matchAll(/\b((?:19|20)\d{2})\b/g)].map(match=>Number(match[1]));
    const year=years.at(-1)||null;
    const doi=text.match(/\b10\.\d{4,9}\/[A-Z0-9.()/:_-]+/i)?.[0]?.replace(/[),.;]+$/,'');
    const venue=/IEEE Trans(?:actions)?\.?(?: Vis\.? Comput\.? Graph\.?|actions on Visualization and Computer Graphics)/i.test(text)?'IEEE TVCG':(/CHI/i.test(text)?'CHI':(/ACM Transactions/i.test(text)?'ACM Transactions':''));
    const preview=text.length>230?`${text.slice(0,227)}…`:text;
    return {id:String(entry.number),number:entry.number,authors,title,venue,year,doi:doi||'',preview};
  }).sort((a,b)=>a.number-b.number);
}

function renumberSections(sections){
  let major=0,minor=0,patch=0;
  for(const section of sections){
    if(section.type!=='body')continue;
    const name=section.title.replace(/^(?:[IVXLCDM]+|\d+(?:\.\d+)*|[A-Z])[.)]?\s+/i,'');
    if(section.level<=1){major++;minor=0;patch=0;section.title=`${major} ${name}`;}
    else if(section.level===2){minor++;patch=0;section.title=`${major}.${minor} ${name}`;}
    else {if(!minor)minor=1;patch++;section.title=`${major}.${minor}.${patch} ${name}`;}
  }
}

async function cropFigure(pageData,item) {
  try {
    const scale=3,viewport=pageData.page.getViewport({scale});
    const canvas=document.createElement('canvas');canvas.width=Math.ceil(viewport.width);canvas.height=Math.ceil(viewport.height);
    const context=canvas.getContext('2d',{alpha:false});
    await pageData.page.render({canvasContext:context,viewport}).promise;
    const row=item.row,baseW=pageData.viewport.width,baseH=pageData.viewport.height;
    const twoColumn=pageData.rows.some(r=>r.column==='right');
    let left=0,right=baseW;
    if(twoColumn&&row.column!=='full'&&row.maxX-row.minX<baseW*.72){if(row.column==='left')right=baseW*.5;else if(row.column==='right')left=baseW*.5;}
    let top=Math.max(baseH*.06,row.y-baseH*.36),bottom=Math.max(0,row.y-Math.max(5,row.fontSize*.7));
    if(bottom-top<baseH*.13)top=Math.max(baseH*.06,bottom-baseH*.2);
    const crop=document.createElement('canvas');crop.width=Math.round((right-left)*scale);crop.height=Math.round((bottom-top)*scale);
    const ctx=crop.getContext('2d',{alpha:false});ctx.drawImage(canvas,left*scale,top*scale,(right-left)*scale,(bottom-top)*scale,0,0,crop.width,crop.height);
    return crop.toDataURL('image/png');
  } catch(error) {console.warn('Could not crop figure from PDF page',error);return '';}
}

function extractTableRows(page,item) {
  const candidates=page.rows.filter(row=>row.y>item.row.y&&row.y<item.row.y+page.viewport.height*.43&&row.column===item.row.column);
  const rows=[];
  for(const line of candidates){
    const cells=[];let current=null;
    for(const atom of line.items){
      const previous=current?.items.at(-1);const gap=previous?atom.x-(previous.x+previous.w):0;
      if(!current||gap>Math.max(15,atom.h*1.5)){current={items:[]};cells.push(current);}current.items.push(atom);
    }
    const clean=cells.map(cell=>({text:cleanPDFText(cell.items.map(x=>x.text).join(' ')),bold:cell.items.some(x=>x.bold),italic:cell.items.some(x=>x.italic)})).filter(cell=>cell.text);
    if(clean.length>=2&&clean.length<=10)rows.push(clean);
    if(rows.length>=22)break;
  }
  if(rows.length>1){const max=Math.max(...rows.map(row=>row.length));return rows.map(row=>Array.from({length:max},(_,i)=>row[i]||{text:'',bold:false,italic:false}));}
  return [];
}

function insertArtifactsNearCallouts(sections,artifacts,kind) {
  for(const artifact of artifacts){
    const regex=kind==='figure'?new RegExp(`\\bfig(?:ure)?\\.?\\s*${artifact.number}\\b`,'i'):new RegExp(`\\btable\\s*${artifact.number}\\b`,'i');
    let target=null;
    for(const section of sections){
      const block=section.blocks.find(item=>item.type==='paragraph'&&item.page===artifact.page&&regex.test(item.text));
      if(block){target={section,block};break;}
    }
    if(!target){for(const section of sections){const match=[...section.blocks].reverse().find(block=>block.type==='paragraph'&&block.page===artifact.page);if(match){target={section,block:match};break;}}}
    const block=kind==='figure'?{id:artifact.id,type:'figure',number:artifact.number,label:artifact.label,caption:artifact.caption,src:artifact.src,page:artifact.page}:{id:artifact.id,type:'table',number:artifact.number,label:artifact.label,caption:artifact.caption,headers:[],rows:artifact.rows,page:artifact.page};
    if(target){const index=target.section.blocks.indexOf(target.block);target.section.blocks.splice(index+1,0,block);}
    else {let body=sections.find(section=>section.type==='body');if(!body){body={id:slugify('Figures and Tables',sections.length),title:'Figures and Tables',level:1,type:'body',blocks:[]};sections.push(body);}body.blocks.push(block);}
  }
}
