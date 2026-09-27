import {escapeHTML, inlineNodes} from './document-model.js';
import {parsePDF} from './pdf-parser.js';

const $=selector=>document.querySelector(selector);
const article=$('#article'),toc=$('#toc'),viewer=$('#viewer');
const demoDocumentPromise=fetch(new URL('./mockDocument.json',import.meta.url)).then(response=>{if(!response.ok)throw new Error('Could not load sample document');return response.json()});

let currentDocument=null, sectionObserver=null, paragraphObserver=null, activeSection='abstract';
let cardHideTimer=null, cardAction=null;
let currentAnnotations=[],pendingSelection=null,annotationLoadToken=0;
let preferences={fontSize:17,width:'770px',font:'serif',theme:'light',customPaper:'#fffdfa',customInk:'#172522',expanded:false,toolbarDock:'top',annotationMode:'highlight',annotationColor:'#ffe59a',annotationNote:false};
try{preferences={...preferences,...JSON.parse(localStorage.getItem('paperlight-preferences')||'{}')};}catch{}
let profileId='';try{profileId=localStorage.getItem('paperlight-profile-id')||'';}catch{}
function randomId(){const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);return Array.from(bytes,byte=>byte.toString(16).padStart(2,'0')).join('');}
if(!/^[a-f0-9]{32}$/.test(profileId)){profileId=randomId();try{localStorage.setItem('paperlight-profile-id',profileId);}catch{}}

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
  if(table.src)return `<figure class="table-card table-image-card" id="${escapeHTML(table.id)}">${compact?'':`<figcaption><b>${escapeHTML(table.label||`Table ${table.number}`)}.</b> ${escapeHTML(table.caption||'')}</figcaption>`}<div class="table-scroll"><img src="${escapeHTML(table.src)}" alt="${escapeHTML(table.label||`Table ${table.number}`)}" loading="lazy"></div>${compact?'':`<button type="button" class="table-open" data-table-open="${escapeHTML(table.id)}">展开表格 ↗</button>`}</figure>`;
  const rawHeaders=table.headers||[];
  const headerRows=rawHeaders.length&&Array.isArray(rawHeaders[0])?rawHeaders:[rawHeaders];
  const head=rawHeaders.length?`<thead>${headerRows.map(row=>`<tr>${row.map(h=>renderCell(h,'th')).join('')}</tr>`).join('')}</thead>`:'';
  const body=(table.rows||[]).map(row=>`<tr>${row.map(cell=>renderCell(cell)).join('')}</tr>`).join('');
  return `<figure class="table-card" id="${escapeHTML(table.id)}">${compact?'':`<figcaption><b>${escapeHTML(table.label||`Table ${table.number}`)}.</b> ${escapeHTML(table.caption||'')}</figcaption>`}<div class="table-scroll"><table>${head}<tbody>${body}</tbody></table></div>${compact?'':`<button type="button" class="table-open" data-table-open="${escapeHTML(table.id)}">展开表格 ↗</button>`}</figure>`;
}
function blockHTML(block){
  if(block.type==='paragraph'){
    if(block.src)return `<div class="math-facsimile"><img src="${escapeHTML(block.src)}" alt="原 PDF 中的数学段落" loading="lazy"><details><summary>查看可复制文本</summary><p data-reader-paragraph="true" data-block-id="${escapeHTML(block.id)}">${escapeHTML(block.text)}</p></details></div>`;
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
    return `<p data-reader-paragraph="true" data-block-id="${escapeHTML(block.id)}" data-citations="${escapeHTML(refs.join(','))}" ${block.page?`data-page="${block.page}"`:''}>${html}</p>`;
  }
  if(block.type==='figure')return `<figure class="figure-card" id="${escapeHTML(block.id)}"><button type="button" data-figure-open="${escapeHTML(block.id)}" aria-label="放大 ${escapeHTML(block.label||'figure')}">${block.src?`<img src="${block.src}" alt="${escapeHTML(block.caption||block.label||'论文插图')}" loading="lazy">`:`<div class="empty-note">图像未能从 PDF 页面生成</div>`}</button><figcaption><b>${escapeHTML(block.label||`Figure ${block.number}`)}.</b> ${escapeHTML(block.caption||'')}</figcaption></figure>`;
  if(block.type==='table')return tableHTML(block);
  if(block.type==='code')return block.src?`<figure class="code-card"><button type="button" data-code-open="${escapeHTML(block.id)}" aria-label="放大代码"><img src="${escapeHTML(block.src)}" alt="${escapeHTML(block.text)}" loading="lazy"></button></figure>`:`<pre class="code-block"><code>${escapeHTML(block.text)}</code></pre>`;
  if(block.type==='list')return (block.items||[]).map(item=>{const cleaned=item.replace(/^\s*(?:[•●▪·]|[-–]\s)\s*/, '');const html=inlineHTML(inlineNodes(cleaned,{references:currentDocument.references,figures:currentDocument.figures,tables:currentDocument.tables}));return /^\d+[.)]\s|^R\d+\s*[:.:—-]|^[a-z]/.test(cleaned)?`<p class="list-prose">${html}</p>`:`<ul><li>${html}</li></ul>`}).join('');
  if(block.type==='equation')return block.src?`<figure class="equation-image"><img src="${escapeHTML(block.src)}" alt="${escapeHTML(block.text||'公式')}" loading="lazy"></figure>`:`<div class="equation" role="math" data-tex="${escapeHTML(block.text||'')}">${escapeHTML(block.text||'')}</div>`;
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
  const supplementary=(m.supplementary||[]).filter(Boolean);
  const authorNotes=(m.authorNotes||[]).filter(Boolean);
  const authorNotesHTML=authorNotes.length?`<details class="paper-extra"><summary>Author notes</summary>${authorNotes.map(value=>`<div>${escapeHTML(value)}</div>`).join('')}</details>`:'';
  const supplementaryHTML=supplementary.length?`<details class="paper-extra"><summary>Supplementary material</summary>${supplementary.map(value=>`<div>${escapeHTML(value)}</div>`).join('')}</details>`:'';
  const appendixHTML=sections.filter(section=>section.type==='appendix').map(section=>`<details class="paper-extra" id="${escapeHTML(section.id)}"><summary>${escapeHTML(section.title)}</summary>${section.blocks.map(blockHTML).join('')}</details>`).join('');
  article.innerHTML=`<div class="paper-meta"><span class="dot"></span><span>${escapeHTML(doc.id==='paperlight-demo'?'Sample paper':'Imported PDF')}</span><span>·</span><span class="tag">${m.readMinutes?`${escapeHTML(m.readMinutes)} min read`:'PDF'}</span>${pageLabel?`<span>·</span><span>${pageLabel}</span>`:''}${venueYear?`<span>·</span><span>${escapeHTML(venueYear)}</span>`:''}${doi?`<span>·</span>${doi}`:''}</div><h1>${escapeHTML(m.title||'Untitled paper')}</h1>${authorLabel?`<div class="authors">${escapeHTML(authorLabel)}</div>`:''}${affiliations.length?`<details class="paper-extra"><summary>Author affiliations</summary><ul>${affiliations.map(value=>`<li>${escapeHTML(value)}</li>`).join('')}</ul></details>`:''}${authorNotesHTML}${received.length?`<details class="paper-extra"><summary>Publication history</summary>${received.map(value=>`<div>${escapeHTML(value)}</div>`).join('')}</details>`:''}${supplementaryHTML}${notice}${abstractHTML}${indexTerms}${bodySections.filter(s=>s.type!=='appendix').map(section=>{const heading=section.level===1?'h2':section.level===2?'h3':'h4';return `<section id="${escapeHTML(section.id)}" data-section="${escapeHTML(section.id)}"><${heading}>${escapeHTML(section.title)}</${heading}>${section.blocks.map(blockHTML).join('')}</section>`}).join('')}${appendixHTML}${funding.length?`<details class="paper-extra"><summary>Funding and acknowledgements</summary>${funding.map(value=>`<div>${escapeHTML(value)}</div>`).join('')}</details>`:''}`;
  const firstBodySection=article.querySelector('section[data-section]:not(.abstract)');
  for(const asset of bodySections.flatMap(section=>section.blocks||[]).filter(block=>block.beforeHeading&&block.type==='figure')){
    const figure=article.querySelector(`#${CSS.escape(asset.id)}`);
    if(figure&&firstBodySection)firstBodySection.before(figure);
  }
  $('#mobileDocTitle').textContent=m.title||'';
  renderContents(sections);renderReferencePanel(refs);renderAssetPanels(doc.figures||[],doc.tables||[]);activateTabs();observeReader();bindArticleInteractions();
  renderEquations();
  loadAnnotations(doc);
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
  $('#references').innerHTML=references.length?references.map(ref=>{const title=ref.title||ref.preview||'';const venue=ref.venue&&!title.includes(ref.venue)?ref.venue:'';const year=ref.year&&!title.includes(String(ref.year))?ref.year:'';return `<article class="ref" id="ref-${escapeHTML(ref.id)}" data-reference="${escapeHTML(ref.id)}"><a href="#ref-${escapeHTML(ref.id)}" data-ref-jump="${escapeHTML(ref.id)}">[${escapeHTML(ref.number??ref.id)}] ${escapeHTML(ref.authors||'')}</a><div style="color:var(--ink);margin-top:3px">${escapeHTML(title)}</div>${venue||year?`<span>${escapeHTML([venue,year].filter(Boolean).join(' · '))}</span>`:''}${ref.doi?`<a class="paper-doi" href="https://doi.org/${encodeURI(ref.doi)}" target="_blank" rel="noopener">DOI ↗</a>`:(/^https?:\/\//i.test(ref.url||'')?`<a class="paper-doi" href="${escapeHTML(ref.url)}" target="_blank" rel="noopener">来源 ↗</a>`:'')}</article>`}).join(''):'<div class="empty-note">没有识别到参考文献。</div>';
  $('#references').querySelectorAll('[data-ref-jump]').forEach(link=>link.addEventListener('click',event=>{event.preventDefault();jumpToReference(link.dataset.refJump)}));
}
function renderAssetPanels(figures,tables){
  $('#figures').innerHTML=figures.length?`<div class="asset-nav">${figures.map(figure=>`<button type="button" data-asset-target="${escapeHTML(figure.id)}">${escapeHTML(figure.label||'Illustration')} · ${escapeHTML(figure.caption||'').slice(0,68)}</button>`).join('')}</div>`:'<div class="empty-note">没有识别到图。</div>';
  $('#tables').innerHTML=tables.length?`<div class="asset-nav">${tables.map(table=>`<button type="button" data-asset-target="${escapeHTML(table.id)}">${escapeHTML(table.label||'Tabular content')} · ${escapeHTML(table.caption||'').slice(0,68)}</button>`).join('')}</div>`:'<div class="empty-note">没有识别到表格。</div>';
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
  clearTimeout(cardHideTimer);
  const card=$('#citationCard');card.style.display='block';
  const rect=trigger.getBoundingClientRect(),x=event?.clientX||rect.left,y=event?.clientY||rect.bottom;
  card.style.left=`${Math.max(8,Math.min(x+12,innerWidth-290))}px`;card.style.top=`${Math.max(8,Math.min(y+14,innerHeight-220))}px`;
}
function hideCardSoon(){clearTimeout(cardHideTimer);cardHideTimer=setTimeout(()=>$('#citationCard').style.display='none',220);}
$('#citationCard').addEventListener('mouseenter',()=>clearTimeout(cardHideTimer));
$('#citationCard').addEventListener('mouseleave',hideCardSoon);
$('#cardAction').addEventListener('click',()=>{cardAction?.();$('#citationCard').style.display='none';});
function showReferenceCard(button,event){
  const ref=currentDocument.references.find(item=>String(item.id)===button.dataset.ref);if(!ref)return;
  $('#cardTitle').textContent=ref.title||'Reference';$('#cardMeta').textContent=`${ref.authors||''}${ref.venue?` · ${ref.venue}`:''}${ref.year?` · ${ref.year}`:''}`;
  const preview=$('#cardPreview');preview.textContent=ref.preview||'';
  const target=ref.url||((ref.doi||'').trim()?`https://doi.org/${encodeURI(ref.doi.trim())}`:'');
  if(/^https?:\/\//i.test(target)){const link=document.createElement('a');link.href=target;link.target='_blank';link.rel='noopener noreferrer';link.textContent='打开原文或 DOI ↗';preview.append(link);}
  preview.style.display=preview.textContent?'block':'none';$('#cardAction').textContent='查看参考文献 →';cardAction=()=>jumpToReference(ref.id);positionCard(button,event);
}
function showArtifactCard(button,event,artifact,kind){
  if(!artifact)return;
  $('#cardTitle').textContent=`${artifact.label||`${kind==='figure'?'Figure':'Table'} ${artifact.number}`}. ${artifact.caption||''}`;
  $('#cardMeta').textContent=`${kind==='figure'?'Figure':'Table'} · PDF page ${artifact.page||'—'}`;
  const preview=$('#cardPreview');
  if(kind==='figure'&&artifact.src)preview.innerHTML=`<img src="${artifact.src}" alt="">`;
  else if(kind==='table')preview.innerHTML=tableHTML({...artifact,id:`preview-${artifact.id}`},true);
  else preview.textContent='';
  preview.style.display=preview.innerHTML?'block':'none';$('#cardAction').textContent=`查看${kind==='figure'?'图片':'表格'} →`;cardAction=()=>document.getElementById(artifact.id)?.scrollIntoView({behavior:'smooth',block:'center'});positionCard(button,event);
}
function bindArticleInteractions(){
  article.querySelectorAll('[data-code-open]').forEach(button=>button.addEventListener('click',()=>{const block=[...currentDocument.sections.flatMap(section=>section.blocks||[])].find(item=>item.id===button.dataset.codeOpen);if(block?.src)openViewer(`<img src="${escapeHTML(block.src)}" alt="${escapeHTML(block.text)}">`,'原文代码');}));
  article.querySelectorAll('[data-figure-open]').forEach(button=>button.addEventListener('click',()=>{const figure=currentDocument.figures.find(item=>item.id===button.dataset.figureOpen);if(figure)openViewer(figure.src?`<img src="${figure.src}" alt="${escapeHTML(figure.caption)}">`:'<div class="empty-note">无法预览该图片</div>',`${figure.label}. ${figure.caption}`)}));
  article.querySelectorAll('[data-table-open]').forEach(button=>button.addEventListener('click',()=>{const table=currentDocument.tables.find(item=>item.id===button.dataset.tableOpen);if(table)openViewer(tableHTML(table,true),`${table.label}. ${table.caption}`)}));
  article.querySelectorAll('[data-figure-link]').forEach(button=>{const artifact=currentDocument.figures.find(item=>item.id===button.dataset.figureLink);button.addEventListener('mouseenter',event=>showArtifactCard(button,event,artifact,'figure'));button.addEventListener('focus',event=>showArtifactCard(button,event,artifact,'figure'));button.addEventListener('mouseleave',hideCardSoon);button.addEventListener('blur',hideCardSoon);button.addEventListener('click',()=>document.getElementById(button.dataset.figureLink)?.scrollIntoView({behavior:'smooth',block:'center'}));});
  article.querySelectorAll('[data-table-link]').forEach(button=>{const artifact=currentDocument.tables.find(item=>item.id===button.dataset.tableLink);button.addEventListener('mouseenter',event=>showArtifactCard(button,event,artifact,'table'));button.addEventListener('focus',event=>showArtifactCard(button,event,artifact,'table'));button.addEventListener('mouseleave',hideCardSoon);button.addEventListener('blur',hideCardSoon);button.addEventListener('click',()=>document.getElementById(button.dataset.tableLink)?.scrollIntoView({behavior:'smooth',block:'center'}));});
  article.querySelectorAll('.cite').forEach(button=>{
    button.addEventListener('mouseenter',event=>showReferenceCard(button,event));button.addEventListener('focus',event=>showReferenceCard(button,event));button.addEventListener('mouseleave',hideCardSoon);button.addEventListener('blur',hideCardSoon);button.addEventListener('click',()=>jumpToReference(button.dataset.ref));
  });
}

$('#settingsButton').addEventListener('click',()=>$('#settings').classList.add('open'));
$('#closeSettings').addEventListener('click',()=>$('#settings').classList.remove('open'));
$('#settings').addEventListener('click',event=>{if(event.target===$('#settings'))$('#settings').classList.remove('open')});
document.addEventListener('keydown',event=>{if(event.key==='Escape'){if($('#settings').classList.contains('open'))$('#settings').classList.remove('open');if(viewer.classList.contains('open'))viewer.classList.remove('open');}});
let preferenceSaveTimer;
function applyPreferences(){
  const p=preferences,root=document.documentElement;
  root.dataset.theme=p.theme;
  root.style.setProperty('--body-size',`${Math.max(14,Math.min(26,Number(p.fontSize)||17))}px`);
  root.style.setProperty('--reading-width',p.width);
  root.style.setProperty('--reader-font',p.font==='sans'?'Inter,ui-sans-serif,system-ui,sans-serif':p.font==='mono'?'ui-monospace,Consolas,monospace':'Georgia,"Noto Serif SC",serif');
  const theme=p.theme==='dark'?{paper:'#202b29',ink:'#e8f0ed',wash:'#273532',line:'#40504c',muted:'#a9bab5',body:'#15201e'}:p.theme==='sepia'?{paper:'#fbf3e4',ink:'#3c3023',wash:'#f6ead6',line:'#daccb6',muted:'#75685a',body:'#ece2d1'}:p.theme==='custom'?{paper:p.customPaper,ink:p.customInk,wash:p.customPaper,line:'#c8d2cd',muted:p.customInk,body:p.customPaper}:{paper:'#fffdfa',ink:'#172522',wash:'#f4f8f6',line:'#dfe8e4',muted:'#6c7d79',body:'#edf3f0'};
  for(const key of ['paper','ink','wash','line','muted'])root.style.setProperty(`--${key}`,theme[key]);document.body.style.background=theme.body;
  root.style.setProperty('--paragraph-ink',theme.ink);
  $('.shell').classList.toggle('reader-expanded',!!p.expanded);
  $('#noteToolbar').dataset.dock=p.toolbarDock||'top';
  for(const [id,value] of [['fontSize',p.fontSize],['contentWidth',p.width],['readerFont',p.font],['readerTheme',p.theme],['customPaper',p.customPaper],['customInk',p.customInk],['toolbarDock',p.toolbarDock],['annotationColor',p.annotationColor]])$("#"+id).value=value;
  $('#annotationNote').checked=!!p.annotationNote;$('#profileId').value=profileId;
  document.querySelectorAll('[data-annotation-mode]').forEach(button=>button.classList.toggle('active',button.dataset.annotationMode===p.annotationMode));
  $('#expandReader').setAttribute('aria-label',p.expanded?'恢复三栏布局':'放大阅读区');
}
function savePreferences(){try{localStorage.setItem('paperlight-preferences',JSON.stringify(preferences));}catch{}clearTimeout(preferenceSaveTimer);preferenceSaveTimer=setTimeout(async()=>{if(!apiBase())return;try{await fetch(`${apiBase()}/api/preferences/${profileId}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(preferences)});}catch(error){console.warn('Settings sync failed',error);}},500);}
for(const [id,key,eventName] of [['fontSize','fontSize','input'],['contentWidth','width','change'],['readerFont','font','change'],['readerTheme','theme','change'],['customPaper','customPaper','input'],['customInk','customInk','input'],['toolbarDock','toolbarDock','change'],['annotationColor','annotationColor','input'],['annotationNote','annotationNote','change']]){$('#'+id).addEventListener(eventName,event=>{preferences[key]=key==='annotationNote'?event.target.checked:event.target.value;if(key==='customPaper'||key==='customInk')preferences.theme='custom';applyPreferences();savePreferences();});}
$('#annotationPreset').addEventListener('change',event=>{preferences.annotationColor=event.target.value;applyPreferences();savePreferences();});
document.querySelectorAll('[data-annotation-mode]').forEach(button=>button.addEventListener('click',()=>{preferences.annotationMode=button.dataset.annotationMode;applyPreferences();savePreferences();}));
$('#expandReader').addEventListener('click',()=>{preferences.expanded=!preferences.expanded;applyPreferences();savePreferences();});
document.querySelectorAll('[data-open-panel]').forEach(button=>button.addEventListener('click',()=>{const tab=button.dataset.openPanel;if(tab==='toc'){$('#leftRail').classList.toggle('expanded-open');$('#rightRail').classList.remove('expanded-open');}else{$('#rightRail').classList.add('expanded-open');$('#leftRail').classList.remove('expanded-open');document.querySelector(`.side-tabs [data-tab="${tab}"]`)?.click();}}));
const apiInput=$('#parserApiUrl');
const isLocalHost=['localhost','127.0.0.1'].includes(location.hostname);
const defaultApiUrl=isLocalHost?'http://127.0.0.1:8000':location.hostname.endsWith('.ts.net')?`https://${location.hostname}:8443`:'';
try{apiInput.value=localStorage.getItem('paperlight-parser-api')||defaultApiUrl;}catch{apiInput.value=defaultApiUrl;}
apiInput.addEventListener('change',()=>{const value=apiInput.value.trim().replace(/\/+$/,'');try{if(value)localStorage.setItem('paperlight-parser-api',value);else localStorage.removeItem('paperlight-parser-api');}catch{}apiInput.value=value;});
$('#loadProfile').addEventListener('click',async()=>{const requested=$('#profileId').value.trim().toLowerCase();if(!/^[a-f0-9]{32}$/.test(requested)){alert('请输入 32 位档案码。');return;}try{const response=await fetch(`${apiBase()}/api/preferences/${requested}`);if(!response.ok)throw new Error(`HTTP ${response.status}`);const loaded=await response.json();if(!Object.keys(loaded).length)throw new Error('服务器上没有这个档案码');profileId=requested;localStorage.setItem('paperlight-profile-id',profileId);preferences={...preferences,...loaded};applyPreferences();savePreferences();$('#profileStatus').textContent='已同步';}catch(error){$('#profileStatus').textContent=`读取失败：${error.message}`;}});
applyPreferences();
$('#tocToggle').addEventListener('click',()=>$('#leftRail').classList.toggle('mobile-open'));
$('#refsToggle').addEventListener('click',()=>$('#rightRail').classList.toggle('mobile-open'));
$('#viewerClose').addEventListener('click',()=>viewer.classList.remove('open'));
viewer.addEventListener('click',event=>{if(event.target===viewer)viewer.classList.remove('open')});

function apiBase(){return apiInput.value.trim().replace(/\/+$/,'');}
function prepareDocument(doc,base){
  const fix=value=>{if(value&&typeof value.src==='string'&&value.src.startsWith('/'))value.src=new URL(value.src,base).href;};
  for(const section of doc.sections||[])for(const block of section.blocks||[])fix(block);
  for(const item of [...(doc.figures||[]),...(doc.tables||[])])fix(item);
  return doc;
}
async function loadAnnotations(doc){
  const token=++annotationLoadToken;
  currentAnnotations=[];renderNotes();
  if(!/^[a-f0-9]{32}$/.test(doc.id)||!apiBase()){
    try{currentAnnotations=JSON.parse(localStorage.getItem(`paperlight-notes-${doc.id}`)||'[]');}catch{currentAnnotations=[];}
    applyHighlights();renderNotes();return;
  }
  try{
    const response=await fetch(`${apiBase()}/api/documents/${doc.id}/annotations`);
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const records=await response.json();
    if(token!==annotationLoadToken||currentDocument!==doc)return;
    currentAnnotations=records;applyHighlights();renderNotes();
  }catch(error){console.warn('Could not load notes',error);$('#notes').textContent='笔记暂时无法加载。';}
}
function markText(record){
  const paragraph=article.querySelector(`[data-block-id="${CSS.escape(record.blockId)}"]`);
  if(!paragraph)return;
  const full=paragraph.textContent;
  let start=record.start,end=record.end;
  if(full.slice(start,end)!==record.quote){start=full.indexOf(record.quote);end=start+record.quote.length;}
  if(start<0||end<=start)return;
  const walker=document.createTreeWalker(paragraph,NodeFilter.SHOW_TEXT);
  const parts=[];let cursor=0,node;
  while((node=walker.nextNode())){const finish=cursor+node.textContent.length;if(finish>start&&cursor<end)parts.push({node,from:Math.max(0,start-cursor),to:Math.min(node.textContent.length,end-cursor)});cursor=finish;}
  for(const part of parts.reverse()){
    const selected=part.node.splitText(part.from);
    selected.splitText(part.to-part.from);
    const mark=document.createElement('mark');mark.className='reader-highlight';mark.dataset.annotationId=record.id;mark.dataset.mode=record.mode||'highlight';mark.style.setProperty('--annotation-color',record.color||'#ffe59a');
    selected.parentNode.replaceChild(mark,selected);mark.appendChild(selected);
  }
}
function applyHighlights(){for(const record of currentAnnotations)markText(record);}
function renderNotes(){
  const panel=$('#notes');
  panel.innerHTML=currentAnnotations.length?currentAnnotations.map(record=>`<div class="note-item" data-note-color style="--note-color:${escapeHTML(record.color||'#ffe59a')}"><button type="button" data-note-jump="${escapeHTML(record.id)}">“${escapeHTML(record.quote.slice(0,100))}”</button>${record.note?`<small>${escapeHTML(record.note)}</small>`:''}<button type="button" class="delete-note" data-note-delete="${escapeHTML(record.id)}">删除</button></div>`).join(''):'<div class="empty-note">选中正文文字，可高亮、划线或添加笔记。</div>';
  panel.querySelectorAll('[data-note-jump]').forEach(button=>button.addEventListener('click',()=>article.querySelector(`[data-annotation-id="${CSS.escape(button.dataset.noteJump)}"]`)?.scrollIntoView({behavior:'smooth',block:'center'})));
  panel.querySelectorAll('[data-note-delete]').forEach(button=>button.addEventListener('click',async()=>{
    if(/^[a-f0-9]{32}$/.test(currentDocument.id)&&apiBase()){
      const response=await fetch(`${apiBase()}/api/documents/${currentDocument.id}/annotations/${button.dataset.noteDelete}`,{method:'DELETE'});
      if(!response.ok){alert('删除笔记失败，请重试。');return;}
    }else{currentAnnotations=currentAnnotations.filter(record=>record.id!==button.dataset.noteDelete);localStorage.setItem(`paperlight-notes-${currentDocument.id}`,JSON.stringify(currentAnnotations));}
    renderDocument(currentDocument);
  }));
}
article.addEventListener('mouseup',()=>{
  const selection=window.getSelection();if(!selection||selection.isCollapsed||!currentDocument)return;
  const range=selection.getRangeAt(0);
  const first=range.startContainer.parentElement?.closest('p[data-block-id]');
  const last=range.endContainer.parentElement?.closest('p[data-block-id]');
  if(!first||first!==last)return;
  const before=document.createRange();before.selectNodeContents(first);before.setEnd(range.startContainer,range.startOffset);
  const start=before.toString().length,quote=selection.toString();
  if(!quote.trim()||quote.length>3000)return;
  pendingSelection={blockId:first.dataset.blockId,start,end:start+quote.length,quote};
  const rect=range.getBoundingClientRect(),toolbar=$('#annotationToolbar');
  toolbar.style.left=`${Math.max(8,Math.min(rect.left,innerWidth-170))}px`;
  toolbar.style.top=`${Math.max(8,rect.top-42)}px`;
  toolbar.classList.add('open');
});
document.addEventListener('mousedown',event=>{if(!event.target.closest('#annotationToolbar')&&!event.target.closest('#article'))$('#annotationToolbar').classList.remove('open');});
async function saveSelection(note=''){
  if(!pendingSelection)return;
  const record={...pendingSelection,note,mode:preferences.annotationMode,color:preferences.annotationColor};
  if(/^[a-f0-9]{32}$/.test(currentDocument.id)&&apiBase()){
    const response=await fetch(`${apiBase()}/api/documents/${currentDocument.id}/annotations`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(record)});
    if(!response.ok){alert('保存高亮或笔记失败，请重试。');return;}
  }else{currentAnnotations.push({...record,id:randomId(),createdAt:Date.now()/1000});try{localStorage.setItem(`paperlight-notes-${currentDocument.id}`,JSON.stringify(currentAnnotations));}catch{alert('浏览器空间不足，笔记未保存。');return;}}
  $('#annotationToolbar').classList.remove('open');window.getSelection()?.removeAllRanges();
  renderDocument(currentDocument);
  document.querySelector('.side-tabs [data-tab="notes"]')?.click();
}
$('#highlightSelection').addEventListener('click',()=>{if(preferences.annotationNote){const note=prompt('为这段文字添加笔记');if(note!==null)saveSelection(note);}else saveSelection();});
$('#noteSelection').addEventListener('click',()=>{const note=prompt('为这段文字添加笔记');if(note!==null)saveSelection(note);});

function localDatabase(){return new Promise((resolve,reject)=>{const request=indexedDB.open('paperlight-local',1);request.onupgradeneeded=()=>request.result.createObjectStore('documents',{keyPath:'id'});request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});}
async function localTransaction(mode,operation){const db=await localDatabase();return new Promise((resolve,reject)=>{const tx=db.transaction('documents',mode),request=operation(tx.objectStore('documents'));request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);tx.oncomplete=()=>db.close();});}
async function saveLocalDocument(file,doc){try{await localTransaction('readwrite',store=>store.put({id:doc.id,title:doc.metadata?.title||file.name,pageCount:doc.metadata?.pageCount||0,file,document:doc,savedAt:Date.now()}));return true;}catch(error){console.warn('Could not save local PDF history',error);return false;}}
async function openHistory(source='local'){
  const dialog=$('#historyDialog'),list=$('#historyList');dialog.classList.add('open');list.textContent='正在读取历史记录…';
  document.querySelectorAll('[data-history-source]').forEach(button=>button.classList.toggle('active',button.dataset.historySource===source));
  $('#historyTip').textContent=source==='local'?'此浏览器保存导入的 PDF。其他电脑各自有独立的本地记录。':'本机服务器保存的论文可通过 Tailscale 从其他电脑读取。';
  try{
    const records=source==='local'?(await localTransaction('readonly',store=>store.getAll())).sort((a,b)=>b.savedAt-a.savedAt):await (async()=>{if(!apiBase())throw new Error('请先设置 Parser API URL');const response=await fetch(`${apiBase()}/api/documents`);if(!response.ok)throw new Error(`HTTP ${response.status}`);return response.json();})();
    list.innerHTML=records.length?records.map(record=>`<button type="button" class="history-item" data-history-id="${escapeHTML(source==='local'?record.id:record.documentId)}"><strong>${escapeHTML(record.title)}</strong><small>${source==='local'?'此浏览器 PDF':'服务器'} · ${escapeHTML(record.pageCount||'—')} 页${source==='server'?` · ${escapeHTML(record.annotationCount||0)} 条笔记`:''}</small></button>`).join(''):'<div class="empty-note">这里还没有论文。导入 PDF 后会出现在此浏览器记录中。</div>';
    list.querySelectorAll('[data-history-id]').forEach(button=>button.addEventListener('click',async()=>{button.disabled=true;button.querySelector('small').textContent='正在打开…';try{let doc;if(source==='local'){const entry=await localTransaction('readonly',store=>store.get(button.dataset.historyId));if(!entry)throw new Error('本地文件不存在');doc=entry.document;if(/^[a-f0-9]{32}$/.test(doc.id)&&apiBase()){try{const health=await fetch(`${apiBase()}/health`,{signal:AbortSignal.timeout(2500)});if(!health.ok)throw new Error('offline');}catch{doc=await parsePDF(entry.file,()=>{});}}}else{const result=await fetch(`${apiBase()}/api/documents/${button.dataset.historyId}`);if(!result.ok)throw new Error(`HTTP ${result.status}`);const state=await result.json();if(!state.document)throw new Error('论文尚未解析完成');doc=prepareDocument(state.document,apiBase());}renderDocument(doc);dialog.classList.remove('open');}catch(error){alert(`打开失败：${error.message}`);button.disabled=false;}}));
  }catch(error){list.textContent=`历史记录暂时无法读取：${error.message}`;}
}
$('#historyButton').addEventListener('click',()=>openHistory('local'));
document.querySelectorAll('[data-history-source]').forEach(button=>button.addEventListener('click',()=>openHistory(button.dataset.historySource)));
$('#closeHistory').addEventListener('click',()=>$('#historyDialog').classList.remove('open'));
$('#historyDialog').addEventListener('click',event=>{if(event.target.id==='historyDialog')event.target.classList.remove('open');});

const fileInput=$('#pdfInput'),importButton=$('#importButton');
importButton.addEventListener('click',()=>fileInput.click());
function wait(ms){return new Promise(resolve=>setTimeout(resolve,ms));}
async function parseWithAPI(file,apiBase,onProgress){
  let currentStage='',stageStarted=Date.now();
  const form=new FormData();form.append('file',file,file.name);
  const response=await fetch(`${apiBase}/api/documents`,{method:'POST',body:form});
  if(!response.ok)throw new Error(`Parser API returned ${response.status}`);
  let state=await response.json();
  for(let attempt=0;attempt<2400;attempt++){
    if(state.status==='ready'&&state.document){
      return prepareDocument(state.document,apiBase);
    }
    if(state.status==='failed')throw new Error(state.error||'Parser API could not process this PDF.');
    if(state.stage!==currentStage){currentStage=state.stage;stageStarted=Date.now();}
    const seconds=(Date.now()-stageStarted)/1000;
    const stageLabels={queued:'等待开始',loading_parser:'准备解析器',extracting_structure:'识别文字与版面',recognizing_scanned_pages:'识别扫描页',linking_references:'整理参考文献',normalizing_document:'生成阅读页面'};
    const base=Math.round((state.progress||0)*100);
    const estimate=state.stage==='extracting_structure'?Math.min(70,Math.round(15+55*(1-Math.exp(-seconds/19)))):state.stage==='recognizing_scanned_pages'?Math.min(72,Math.round(48+24*(1-Math.exp(-seconds/25)))):state.stage==='linking_references'?Math.min(88,Math.round(74+14*(1-Math.exp(-seconds/18)))):base;
    onProgress(`${stageLabels[state.stage]||'正在解析'} · 约${Math.max(base,estimate)}%`);
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
    renderDocument(doc);if(!await saveLocalDocument(file,doc))alert('浏览器空间不足，本地历史未能保存；服务器历史仍可使用。');importButton.textContent='更换 PDF';
  }catch(error){console.error('PDF import failed',error);alert(`PDF 读取失败：${error?.message||'未知错误'}`);importButton.textContent='导入 PDF';}
  finally{importButton.disabled=false;fileInput.value='';}
});

window.__paperlightReady=true;
demoDocumentPromise.then(doc=>{if(!currentDocument)renderDocument(doc);}).catch(error=>{console.error(error);if(!currentDocument)article.innerHTML='<div class="empty-note">示例文档暂时无法加载，请刷新页面。</div>';});
