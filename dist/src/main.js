import {escapeHTML, inlineNodes} from './document-model.js';
import {parsePDF} from './pdf-parser.js';

const $=selector=>document.querySelector(selector);
const article=$('#article'),toc=$('#toc'),viewer=$('#viewer');
const demoDocumentPromise=fetch(new URL('./mockDocument.json',import.meta.url)).then(response=>{if(!response.ok)throw new Error('Could not load sample document');return response.json()});

let currentDocument=null, sectionObserver=null, paragraphObserver=null, activeSection='abstract';
let darkMode=false;

function inlineHTML(nodes){return (nodes||[]).map(node=>{
  if(node.type==='citation')return `<button class="cite" type="button" data-ref="${escapeHTML(node.referenceIds?.[0]||'')}" aria-label="查看引用 ${escapeHTML(node.display||'')}" ${node.unresolved?'data-unresolved="true"':''}>${escapeHTML(node.display||'citation')}</button>`;
  if(node.type==='figureLink')return `<button class="figure-link" type="button" data-figure-link="${escapeHTML(node.figureId)}">${escapeHTML(node.display)}</button>`;
  if(node.type==='tableLink')return `<button class="table-link" type="button" data-table-link="${escapeHTML(node.tableId)}">${escapeHTML(node.display)}</button>`;
  if(node.type==='link'&&/^https?:\/\//i.test(node.href||''))return `<a href="${escapeHTML(node.href)}" target="_blank" rel="noopener">${escapeHTML(node.display||node.text||node.href)}</a>`;
  if(node.type==='inlineEquation')return `<span class="inline-equation" data-tex="${escapeHTML(node.text||'')}">${escapeHTML(node.text||'')}</span>`;
  if(node.type==='superscript')return `<sup>${escapeHTML(node.text||'')}</sup>`;
  if(node.type==='subscript')return `<sub>${escapeHTML(node.text||'')}</sub>`;
  let text=escapeHTML(node.text||'');if(node.italic)text=`<em>${text}</em>`;if(node.bold)text=`<strong>${text}</strong>`;return text;
}).join('');}

function renderCell(cell,tag='td'){const value=typeof cell==='string'?{text:cell}:cell||{};return `<${tag} class="${value.bold?'best':''} ${value.underline?'underlined':''}"${value.rowSpan>1?` rowspan="${Number(value.rowSpan)}"`:''}${value.colSpan>1?` colspan="${Number(value.colSpan)}"`:''}>${value.italic?`<em>${escapeHTML(value.text)}</em>`:escapeHTML(value.text)}</${tag}>`;}
function tableHTML(table,compact=false){
  const rawHeaders=table.headers||[];
  const headerRows=rawHeaders.length&&Array.isArray(rawHeaders[0])?rawHeaders:[rawHeaders];
  const head=rawHeaders.length?`<thead>${headerRows.map(row=>`<tr>${row.map(h=>renderCell(h,'th')).join('')}</tr>`).join('')}</thead>`:'';
  const body=(table.rows||[]).map(row=>`<tr>${row.map(renderCell).join('')}</tr>`).join('');
  return `<figure class="table-card" id="${escapeHTML(table.id)}"><figcaption><b>${escapeHTML(table.label||`Table ${table.number}`)}.</b> ${escapeHTML(table.caption||'')}</figcaption><div class="table-scroll"><table>${head}<tbody>${body}</tbody></table></div>${compact?'':`<button type="button" class="table-open" data-table-open="${escapeHTML(table.id)}">展开表格 ↗</button>`}</figure>`;
}
function blockHTML(block){
  if(block.type==='paragraph'){
    const rawContent=block.content||inlineNodes(block.text,{references:currentDocument.references,figures:currentDocument.figures,tables:currentDocument.tables});
    const content=rawContent.flatMap(node=>{
      if(node.type!=='text')return [node];
      return inlineNodes(node.text,{references:currentDocument.references,figures:currentDocument.figures,tables:currentDocument.tables}).map(part=>{
        if(part.type!=='citation')return part.type==='text'?{...part,bold:node.bold,italic:node.italic}:part;
        const ref=currentDocument.references.find(item=>String(item.number)===String(part.referenceIds?.[0])||String(item.id)===String(part.referenceIds?.[0]));
        return {...part,referenceIds:ref?[String(ref.id)]:part.referenceIds,unresolved:!ref};
      });
    });
    const refs=(content||[]).filter(n=>n.type==='citation').flatMap(n=>n.referenceIds||[]);
    let html=inlineHTML(content);
    if(!block.content){const lead=block.text.match(/^([A-Z][A-Za-z /-]{1,34}:)(?=\s)/);if(lead)html=html.replace(escapeHTML(lead[1]),`<strong class="label">${escapeHTML(lead[1])}</strong>`);}
    if(!block.content){if(block.bold)html=`<strong>${html}</strong>`;else if(block.italic)html=`<em>${html}</em>`;}
    return `<p data-reader-paragraph="true" data-citations="${escapeHTML(refs.join(','))}" ${block.page?`data-page="${block.page}"`:''}>${html}</p>`;
  }
  if(block.type==='figure')return `<figure class="figure-card" id="${escapeHTML(block.id)}"><button type="button" data-figure-open="${escapeHTML(block.id)}" aria-label="放大 ${escapeHTML(block.label||'figure')}">${block.src?`<img src="${block.src}" alt="${escapeHTML(block.caption||block.label||'论文插图')}" loading="lazy">`:`<div class="empty-note">图像未能从 PDF 页面生成</div>`}</button><figcaption><b>${escapeHTML(block.label||`Figure ${block.number}`)}.</b> ${escapeHTML(block.caption||'')}</figcaption></figure>`;
  if(block.type==='table')return tableHTML(block);
  if(block.type==='list')return `<ul>${(block.items||[]).map(item=>`<li>${inlineHTML(inlineNodes(item,{references:currentDocument.references,figures:currentDocument.figures,tables:currentDocument.tables}))}</li>`).join('')}</ul>`;
  if(block.type==='equation')return `<div class="equation" role="math" data-tex="${escapeHTML(block.text||'')}">${escapeHTML(block.text||'')}</div>`;
  if(block.type==='footnote')return `<aside class="footnote" role="note">${escapeHTML(block.text||'')}</aside>`;
  if(block.type==='requirement')return `<aside class="requirement">${inlineHTML(inlineNodes(block.text,{references:currentDocument.references,figures:currentDocument.figures,tables:currentDocument.tables}))}</aside>`;
  if(block.type==='quote')return `<blockquote>${escapeHTML(block.text||'')}</blockquote>`;
  return '';
}

function renderDocument(doc){
  currentDocument=doc;
  const m=doc.metadata||{},sections=doc.sections||[],refs=doc.references||[];
  const abstract=sections.find(section=>section.type==='abstract');
  const bodySections=sections.filter(section=>section.type!=='abstract');
  const authorLabel=(m.authors||[]).join(' · ');
  const venueYear=[m.venue,m.year].filter(Boolean).join(' · ');
  const doi=m.doi?`<a class="paper-doi" href="https://doi.org/${encodeURI(m.doi)}" target="_blank" rel="noopener">DOI ${escapeHTML(m.doi)} ↗</a>`:'';
  const pageLabel=m.pageRange?`${escapeHTML(m.pageRange)} · ${escapeHTML(m.pageCount)} pages`:m.pageCount?`${escapeHTML(m.pageCount)} pages`:'';
  const notice=m.notice?`<div class="notice">${escapeHTML(m.notice)}</div>`:'';
  const abstractHTML=abstract?`<section class="abstract" id="abstract" data-section="abstract"><strong>ABSTRACT</strong>${abstract.blocks.map(blockHTML).join('')}</section>`:'';
  const indexTerms=m.indexTerms?`<p class="index-terms"><strong>Index Terms—</strong>${escapeHTML(m.indexTerms)}</p>`:'';
  const affiliations=(m.affiliations||[]).filter(Boolean);
  const received=(m.received||[]).filter(Boolean);
  const funding=(m.funding||[]).filter(Boolean);
  const appendixHTML=sections.filter(section=>section.type==='appendix').map(section=>`<details class="paper-extra" id="${escapeHTML(section.id)}"><summary>${escapeHTML(section.title)}</summary>${section.blocks.map(blockHTML).join('')}</details>`).join('');
  article.innerHTML=`<div class="paper-meta"><span class="dot"></span><span>${escapeHTML(doc.id==='paperlight-demo'?'Sample paper':'Imported PDF')}</span><span>·</span><span class="tag">${m.readMinutes?`${escapeHTML(m.readMinutes)} min read`:'PDF'}</span>${pageLabel?`<span>·</span><span>${pageLabel}</span>`:''}${venueYear?`<span>·</span><span>${escapeHTML(venueYear)}</span>`:''}${doi?`<span>·</span>${doi}`:''}</div><h1>${escapeHTML(m.title||'Untitled paper')}</h1>${authorLabel?`<div class="authors">${escapeHTML(authorLabel)}</div>`:''}${affiliations.length?`<details class="paper-extra"><summary>Author affiliations</summary><ul>${affiliations.map(value=>`<li>${escapeHTML(value)}</li>`).join('')}</ul></details>`:''}${received.length?`<details class="paper-extra"><summary>Publication history</summary>${received.map(value=>`<div>${escapeHTML(value)}</div>`).join('')}</details>`:''}${notice}${abstractHTML}${indexTerms}${bodySections.filter(s=>s.type!=='appendix').map(section=>{const heading=section.level===1?'h2':section.level===2?'h3':'h4';return `<section id="${escapeHTML(section.id)}" data-section="${escapeHTML(section.id)}"><${heading}>${escapeHTML(section.title)}</${heading}>${section.blocks.map(blockHTML).join('')}</section>`}).join('')}${appendixHTML}${funding.length?`<details class="paper-extra"><summary>Funding and acknowledgements</summary>${funding.map(value=>`<div>${escapeHTML(value)}</div>`).join('')}</details>`:''}`;
  $('#mobileDocTitle').textContent=m.title||'';
  renderContents(sections);renderReferencePanel(refs);renderAssetPanels(doc.figures||[],doc.tables||[]);activateTabs();observeReader();bindArticleInteractions();
  renderEquations();
  $('#topProgress').innerHTML=`<strong id="topPercent">0%</strong> · <span id="topSection">0 / 0</span>`;
  $('#refCount').textContent=refs.length?`(${refs.length})`:'';
  updateProgress();
}

async function renderEquations(){
  const elements=[...article.querySelectorAll('.equation[data-tex],.inline-equation[data-tex]')];if(!elements.length)return;
  try{const module=await import('https://cdn.jsdelivr.net/npm/katex@0.18.9/dist/katex.mjs');for(const element of elements){const tex=element.dataset.tex.replace(/^\$\$?\s*|\s*\$\$?$/g,'');module.default.render(tex,element,{displayMode:element.classList.contains('equation'),throwOnError:false,strict:'ignore'});}}
  catch(error){console.warn('KaTeX could not be loaded; showing extracted formula text.',error);}
}

function renderContents(sections){
  toc.innerHTML=sections.map(section=>`<button type="button" class="level-${Math.min(section.level||1,3)}" data-target="${escapeHTML(section.id)}" aria-current="false">${escapeHTML(section.title)}</button>`).join('');
  toc.querySelectorAll('[data-target]').forEach(button=>button.addEventListener('click',()=>{document.getElementById(button.dataset.target)?.scrollIntoView({behavior:'smooth',block:'start'});$('#leftRail').classList.remove('mobile-open')}));
}

function renderReferencePanel(references){
  $('#references').innerHTML=references.length?references.map(ref=>`<article class="ref" id="ref-${escapeHTML(ref.id)}" data-reference="${escapeHTML(ref.id)}"><a href="#ref-${escapeHTML(ref.id)}" data-ref-jump="${escapeHTML(ref.id)}">[${escapeHTML(ref.number??ref.id)}] ${escapeHTML(ref.authors||'')}</a><div style="color:var(--ink);margin-top:3px">${escapeHTML(ref.title||'')}</div><span>${escapeHTML([ref.venue,ref.year].filter(Boolean).join(' · '))}</span>${ref.doi?`<a class="paper-doi" href="https://doi.org/${encodeURI(ref.doi)}" target="_blank" rel="noopener">DOI ↗</a>`:(/^https?:\/\//i.test(ref.url||'')?`<a class="paper-doi" href="${escapeHTML(ref.url)}" target="_blank" rel="noopener">来源 ↗</a>`:'')}</article>`).join(''):'<div class="empty-note">没有识别到参考文献。</div>';
  $('#references').querySelectorAll('[data-ref-jump]').forEach(link=>link.addEventListener('click',event=>{event.preventDefault();jumpToReference(link.dataset.refJump)}));
}
function renderAssetPanels(figures,tables){
  $('#figures').innerHTML=figures.length?`<div class="asset-nav">${figures.map(figure=>`<button type="button" data-asset-target="${escapeHTML(figure.id)}">Fig. ${escapeHTML(figure.number)} · ${escapeHTML(figure.caption||'').slice(0,68)}</button>`).join('')}</div>`:'<div class="empty-note">没有识别到图。</div>';
  $('#tables').innerHTML=tables.length?`<div class="asset-nav">${tables.map(table=>`<button type="button" data-asset-target="${escapeHTML(table.id)}">Table ${escapeHTML(table.number)} · ${escapeHTML(table.caption||'').slice(0,68)}</button>`).join('')}</div>`:'<div class="empty-note">没有识别到表格。</div>';
  document.querySelectorAll('[data-asset-target]').forEach(button=>button.addEventListener('click',()=>document.getElementById(button.dataset.assetTarget)?.scrollIntoView({behavior:'smooth',block:'center'})));
}
function activateTabs(){
  document.querySelectorAll('.side-tabs [data-tab]').forEach(button=>button.addEventListener('click',()=>{
    document.querySelectorAll('.side-tabs [data-tab]').forEach(tab=>{const active=tab===button;tab.classList.toggle('active',active);tab.setAttribute('aria-selected',String(active));});
    document.querySelectorAll('.side-content').forEach(panel=>panel.classList.toggle('active',panel.id===button.dataset.tab));
  }));
}
function observeReader(){
  if(sectionObserver)sectionObserver.disconnect();if(paragraphObserver)paragraphObserver.disconnect();
  const sections=[...article.querySelectorAll('[data-section]')];
  if('IntersectionObserver'in window){
    sectionObserver=new IntersectionObserver(entries=>{const visible=entries.filter(e=>e.isIntersecting).sort((a,b)=>b.intersectionRatio-a.intersectionRatio)[0];if(visible){activeSection=visible.target.id;setActiveToc(activeSection);updateProgress();}},{rootMargin:'-16% 0px -68% 0px',threshold:[0,.1,.3,.6]});sections.forEach(section=>sectionObserver.observe(section));
    paragraphObserver=new IntersectionObserver(entries=>{const visible=entries.filter(e=>e.isIntersecting).sort((a,b)=>b.intersectionRatio-a.intersectionRatio)[0];if(visible)highlightReferences((visible.target.dataset.citations||'').split(',').filter(Boolean));},{rootMargin:'-25% 0px -65% 0px',threshold:[0,.2,.6]});article.querySelectorAll('[data-reader-paragraph]').forEach(p=>paragraphObserver.observe(p));
  }
}
function setActiveToc(id){toc.querySelectorAll('[data-target]').forEach(button=>{const active=button.dataset.target===id;button.classList.toggle('active',active);button.setAttribute('aria-current',active?'location':'false')});}
function updateProgress(){
  const sections=[...article.querySelectorAll('[data-section]')];let index=sections.findIndex(section=>section.id===activeSection);if(index<0)index=0;
  const percent=sections.length?Math.round((index+1)/sections.length*100):0;
  $('#topPercent').textContent=`${percent}%`;$('#topSection').textContent=`${Math.min(index+1,sections.length)} / ${sections.length}`;
}
function highlightReferences(ids){document.querySelectorAll('[data-reference]').forEach(element=>element.classList.toggle('current',ids.includes(element.dataset.reference)));}
function jumpToReference(id){
  document.querySelector('.side-tabs [data-tab="references"]').click();
  const target=document.querySelector(`[data-reference="${CSS.escape(String(id))}"]`);
  if(target){target.scrollIntoView({behavior:'smooth',block:'center'});target.classList.remove('flash');void target.offsetWidth;target.classList.add('flash');}
  if(innerWidth<=1120)$('#rightRail').classList.add('mobile-open');
}
function openViewer(content,caption){$('#viewerContent').innerHTML=content;$('#viewerCaption').textContent=caption||'';viewer.classList.add('open');$('#viewerClose').focus();}
function positionCard(trigger,event){
  const card=$('#citationCard');card.style.display='block';
  const rect=trigger.getBoundingClientRect(),x=event?.clientX||rect.left,y=event?.clientY||rect.bottom;
  card.style.left=`${Math.max(8,Math.min(x+12,innerWidth-290))}px`;card.style.top=`${Math.max(8,Math.min(y+14,innerHeight-220))}px`;
}
function showReferenceCard(button,event){
  const ref=currentDocument.references.find(item=>String(item.id)===button.dataset.ref);if(!ref)return;
  $('#cardTitle').textContent=ref.title||'Reference';$('#cardMeta').textContent=`${ref.authors||''}${ref.venue?` · ${ref.venue}`:''}${ref.year?` · ${ref.year}`:''}`;
  $('#cardPreview').textContent=ref.preview||'';$('#cardPreview').style.display=ref.preview?'block':'none';$('.card-action').textContent='查看参考文献 →';positionCard(button,event);
}
function showArtifactCard(button,event,artifact,kind){
  if(!artifact)return;
  $('#cardTitle').textContent=`${artifact.label||`${kind==='figure'?'Figure':'Table'} ${artifact.number}`}. ${artifact.caption||''}`;
  $('#cardMeta').textContent=`${kind==='figure'?'Figure':'Table'} · PDF page ${artifact.page||'—'}`;
  const preview=$('#cardPreview');
  if(kind==='figure'&&artifact.src)preview.innerHTML=`<img src="${artifact.src}" alt="">`;
  else if(kind==='table')preview.innerHTML=tableHTML({...artifact,id:`preview-${artifact.id}`},true);
  else preview.textContent='';
  preview.style.display=preview.innerHTML?'block':'none';$('.card-action').textContent=`查看${kind==='figure'?'图片':'表格'} →`;positionCard(button,event);
}
function bindArticleInteractions(){
  article.querySelectorAll('[data-figure-open]').forEach(button=>button.addEventListener('click',()=>{const figure=currentDocument.figures.find(item=>item.id===button.dataset.figureOpen);if(figure)openViewer(figure.src?`<img src="${figure.src}" alt="${escapeHTML(figure.caption)}">`:'<div class="empty-note">无法预览该图片</div>',`${figure.label}. ${figure.caption}`)}));
  article.querySelectorAll('[data-table-open]').forEach(button=>button.addEventListener('click',()=>{const table=currentDocument.tables.find(item=>item.id===button.dataset.tableOpen);if(table)openViewer(tableHTML(table,true),`${table.label}. ${table.caption}`)}));
  article.querySelectorAll('[data-figure-link]').forEach(button=>{const artifact=currentDocument.figures.find(item=>item.id===button.dataset.figureLink);button.addEventListener('mouseenter',event=>showArtifactCard(button,event,artifact,'figure'));button.addEventListener('focus',event=>showArtifactCard(button,event,artifact,'figure'));button.addEventListener('mouseleave',()=>$('#citationCard').style.display='none');button.addEventListener('blur',()=>$('#citationCard').style.display='none');button.addEventListener('click',()=>document.getElementById(button.dataset.figureLink)?.scrollIntoView({behavior:'smooth',block:'center'}));});
  article.querySelectorAll('[data-table-link]').forEach(button=>{const artifact=currentDocument.tables.find(item=>item.id===button.dataset.tableLink);button.addEventListener('mouseenter',event=>showArtifactCard(button,event,artifact,'table'));button.addEventListener('focus',event=>showArtifactCard(button,event,artifact,'table'));button.addEventListener('mouseleave',()=>$('#citationCard').style.display='none');button.addEventListener('blur',()=>$('#citationCard').style.display='none');button.addEventListener('click',()=>document.getElementById(button.dataset.tableLink)?.scrollIntoView({behavior:'smooth',block:'center'}));});
  article.querySelectorAll('.cite').forEach(button=>{
    button.addEventListener('mouseenter',event=>showReferenceCard(button,event));button.addEventListener('focus',event=>showReferenceCard(button,event));button.addEventListener('mouseleave',()=>$('#citationCard').style.display='none');button.addEventListener('blur',()=>$('#citationCard').style.display='none');button.addEventListener('click',()=>jumpToReference(button.dataset.ref));
  });
}

$('#settingsButton').addEventListener('click',()=>$('#settings').classList.add('open'));
$('#closeSettings').addEventListener('click',()=>$('#settings').classList.remove('open'));
$('#settings').addEventListener('click',event=>{if(event.target===$('#settings'))$('#settings').classList.remove('open')});
document.addEventListener('keydown',event=>{if(event.key==='Escape'){if($('#settings').classList.contains('open'))$('#settings').classList.remove('open');if(viewer.classList.contains('open'))viewer.classList.remove('open');}});
$('#fontSize').addEventListener('input',event=>document.documentElement.style.setProperty('--body-size',`${event.target.value}px`));
$('#contentWidth').addEventListener('change',event=>document.documentElement.style.setProperty('--reading-width',event.target.value));
const apiInput=$('#parserApiUrl');
const isLocalHost=['localhost','127.0.0.1'].includes(location.hostname);
try{apiInput.value=localStorage.getItem('paperlight-parser-api')||(isLocalHost?'http://127.0.0.1:8000':'');}catch{apiInput.value=isLocalHost?'http://127.0.0.1:8000':'';}
apiInput.addEventListener('change',()=>{const value=apiInput.value.trim().replace(/\/+$/,'');try{if(value)localStorage.setItem('paperlight-parser-api',value);else localStorage.removeItem('paperlight-parser-api');}catch{}apiInput.value=value;});
$('#themeButton').addEventListener('click',()=>{darkMode=!darkMode;document.documentElement.style.setProperty('--paper',darkMode?'#202b29':'#fffdfa');document.documentElement.style.setProperty('--ink',darkMode?'#e8f0ed':'#172522');document.documentElement.style.setProperty('--wash',darkMode?'#273532':'#f4f8f6');document.documentElement.style.setProperty('--line',darkMode?'#40504c':'#dfe8e4');document.documentElement.style.setProperty('--muted',darkMode?'#a9bab5':'#6c7d79');document.body.style.background=darkMode?'#15201e':'#edf3f0';document.querySelectorAll('.reading p').forEach(p=>p.style.color=darkMode?'#d3dfdb':'#293c37');});
$('#tocToggle').addEventListener('click',()=>$('#leftRail').classList.toggle('mobile-open'));
$('#refsToggle').addEventListener('click',()=>$('#rightRail').classList.toggle('mobile-open'));
$('#viewerClose').addEventListener('click',()=>viewer.classList.remove('open'));
viewer.addEventListener('click',event=>{if(event.target===viewer)viewer.classList.remove('open')});

const fileInput=$('#pdfInput'),importButton=$('#importButton');
importButton.addEventListener('click',()=>fileInput.click());
function wait(ms){return new Promise(resolve=>setTimeout(resolve,ms));}
async function parseWithAPI(file,apiBase,onProgress){
  const form=new FormData();form.append('file',file,file.name);
  const response=await fetch(`${apiBase}/api/documents`,{method:'POST',body:form});
  if(!response.ok)throw new Error(`Parser API returned ${response.status}`);
  let state=await response.json();
  for(let attempt=0;attempt<2400;attempt++){
    if(state.status==='ready'&&state.document){
      const doc=state.document;
      const fixAssets=value=>{if(value&&typeof value.src==='string'&&value.src.startsWith('/'))value.src=new URL(value.src,apiBase).href;};
      for(const section of doc.sections||[])for(const block of section.blocks||[])fixAssets(block);
      for(const item of [...(doc.figures||[]),...(doc.tables||[])])fixAssets(item);
      return doc;
    }
    if(state.status==='failed')throw new Error(state.error||'Parser API could not process this PDF.');
    onProgress(`服务器解析 · ${state.stage||'processing'} · ${Math.round((state.progress||0)*100)}%`);
    await wait(900);
    const next=await fetch(`${apiBase}/api/documents/${encodeURIComponent(state.documentId)}`);
    if(!next.ok)throw new Error(`Parser status returned ${next.status}`);
    state=await next.json();
  }
  throw new Error('Parser API timed out.');
}
fileInput.addEventListener('change',async()=>{
  const file=fileInput.files?.[0];if(!file)return;
  if(file.type!=='application/pdf'&&!file.name.toLowerCase().endsWith('.pdf')){alert('请选择 PDF 文件。');return;}
  importButton.disabled=true;importButton.textContent='准备中…';
  let apiFailure='';
  try{
    const apiBase=apiInput.value.trim().replace(/\/+$/,'');
    let doc;
    if(apiBase){
      try{doc=await parseWithAPI(file,apiBase,label=>{importButton.textContent=label;});}
      catch(error){apiFailure=error?.message||'Parser API unavailable';console.warn('Parser API unavailable; switching to in-browser extraction.',error);}
    }
    if(!doc)doc=await parsePDF(file,label=>{importButton.textContent=label;});
    if(apiFailure)doc.metadata.notice=[doc.metadata.notice,`服务器解析不可用，已切换到浏览器本地解析（${apiFailure}）。本地模式对复杂版式、扫描页和表格的识别能力有限。`].filter(Boolean).join(' ');
    if(!doc.sections.some(section=>section.type==='body')&&!doc.metadata.abstract)doc.metadata.notice='没有提取到可阅读文本。该文件可能是扫描 PDF，需要 OCR 后端才能识别。';
    if(!doc.figures.length&&!doc.tables.length)doc.metadata.notice=[doc.metadata.notice,'未识别到独立图表；原文和章节仍可继续阅读。'].filter(Boolean).join(' ');
    renderDocument(doc);importButton.textContent='更换 PDF';
  }catch(error){console.error('PDF import failed',error);alert(`PDF 读取失败：${error?.message||'未知错误'}`);importButton.textContent='导入 PDF';}
  finally{importButton.disabled=false;fileInput.value='';}
});

demoDocumentPromise.then(renderDocument).catch(error=>{console.error(error);article.innerHTML='<div class="empty-note">示例文档暂时无法加载，请刷新页面。</div>';});
