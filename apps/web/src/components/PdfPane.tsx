'use client';
import {useEffect, useRef, useState, type RefObject} from 'react';
import {Minus, Plus} from 'lucide-react';
import type {PDFDocumentProxy} from 'pdfjs-dist';
import type {Annotation, Box, DocumentModel} from '@/lib/document';
import {allBlocks, isTextAnchor} from '@/lib/document';
import {locateText, matchPdfSelection} from '@/lib/pdfAnchors';

type PdfRectangle = {page: number; bbox: Box};
type Props = {paper: DocumentModel; serverId: string; annotations: Annotation[]; scrollRef: RefObject<HTMLDivElement | null>; style: 'highlight' | 'underline' | 'area' | null; color: string; noteEnabled: boolean; onAnnotation: (record: Annotation) => void; onNotice: (text: string) => void};
function relativeRect(rect: DOMRect, surface: DOMRect): Box {
  const x = Math.max(0, (rect.left - surface.left) / surface.width), y = Math.max(0, (rect.top - surface.top) / surface.height);
  return {x, y, width: Math.min(1-x, rect.width/surface.width), height: Math.min(1-y, rect.height/surface.height)};
}
function quoteRange(root: HTMLElement, quote: string): Range | null {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); const nodes: Text[] = [];
  let node; let text = '';
  while ((node = walker.nextNode())) {nodes.push(node as Text); text += node.textContent || '';}
  const match = locateText(text, quote); if (!match) return null;
  const range = document.createRange(); let offset = 0; let started = false;
  for (const current of nodes) {const end = offset + current.length;
    if (!started && match.start < end) {range.setStart(current, match.start-offset); started = true;}
    if (started && match.end <= end) {range.setEnd(current, match.end-offset); return range;}
    offset = end;
  }
  return null;
}
function PdfPage({pdf, pageNumber, width, paper, annotations, root}: {pdf: PDFDocumentProxy; pageNumber: number; width: number; paper: DocumentModel; annotations: Annotation[]; root: RefObject<HTMLDivElement | null>}) {
  const host = useRef<HTMLDivElement>(null), canvas = useRef<HTMLCanvasElement>(null), layer = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false), [rendered, setRendered] = useState(0), [height, setHeight] = useState(width * 1.414), [error, setError] = useState('');
  const [marks, setMarks] = useState<Array<{id: string; bbox: Box; color: string; underline: boolean}>>([]);
  useEffect(() => {const observer = new IntersectionObserver(entries => setVisible(entries.some(entry=>entry.isIntersecting)), {root: root.current, rootMargin: '900px'}); if(host.current) observer.observe(host.current); return ()=>observer.disconnect();}, [root]);
  useEffect(() => {
    if (!visible || !canvas.current || !layer.current) return;
    let cancelled = false; let cancel: (()=>void) | undefined;
    void (async () => {
      const library = await import('pdfjs-dist'); const page = await pdf.getPage(pageNumber);
      if(cancelled || !canvas.current || !layer.current) return;
      const viewport = page.getViewport({scale:width/page.getViewport({scale:1}).width}); setHeight(viewport.height);
      const ratio = Math.min(devicePixelRatio || 1, 2); const element = canvas.current;
      element.width = Math.round(viewport.width*ratio); element.height = Math.round(viewport.height*ratio);
      element.style.width = `${viewport.width}px`; element.style.height = `${viewport.height}px`;
      const task = page.render({canvas:element, viewport, transform:[ratio,0,0,ratio,0,0]}); cancel = ()=>task.cancel(); await task.promise;
      if(cancelled || !layer.current) return;
      layer.current.replaceChildren(); layer.current.style.setProperty('--total-scale-factor', String(viewport.scale));
      const textLayer = new library.TextLayer({textContentSource:await page.getTextContent(), container:layer.current, viewport}); cancel = ()=>textLayer.cancel(); await textLayer.render();
      if(!cancelled) {setRendered(value=>value+1);setError('');}
    })().catch(error=>{if(!cancelled && error?.name!=='RenderingCancelledException') setError('本页加载失败，请重新打开论文。');});
    return ()=>{cancelled=true;cancel?.();};
  }, [pdf,pageNumber,width,visible]);
  useEffect(()=>{
    if(!host.current || !layer.current || !rendered) return;
    const surface = host.current.getBoundingClientRect(); const result: typeof marks = [];
    for(const record of annotations) {
      const underline = record.style === 'underline' || record.type === 'underline';
      const saved = record.anchor.pdfRects?.filter(rect=>rect.page===pageNumber);
      if(saved?.length) {for(const rect of saved) result.push({id:record.id,bbox:rect.bbox,color:record.color,underline});continue;}
      if(!isTextAnchor(record.anchor)) {
        if(record.anchor.page===pageNumber && record.anchor.space==='page') result.push({id:record.id,bbox:record.anchor.bbox,color:record.color,underline:false});
        continue;
      }
      const anchor = record.anchor;
      const block = allBlocks(paper).find(block=>block.id===anchor.start.blockId);
      if(block?.page !== pageNumber) continue;
      const range = quoteRange(layer.current,record.anchor.quote); if(!range) continue;
      for(const rect of Array.from(range.getClientRects())) if(rect.width>0 && rect.height>0) result.push({id:record.id,bbox:relativeRect(rect,surface),color:record.color,underline});
    }
    setMarks(result);
  }, [annotations,rendered,width,height,paper,pageNumber]);
  return <div ref={host} className="pdf-page" data-pdf-page={pageNumber} style={{width,height}}><canvas ref={canvas} aria-label={`PDF 第 ${pageNumber} 页`} /><div ref={layer} className="textLayer" /><div className="pdf-marks" aria-hidden="true">{marks.map((mark,index)=><span key={`${mark.id}-${index}`} style={{left:`${mark.bbox.x*100}%`,top:`${mark.bbox.y*100}%`,width:`${mark.bbox.width*100}%`,height:`${mark.bbox.height*100}%`,background:mark.underline?'transparent':`${mark.color}55`,borderBottom:mark.underline?`2px solid ${mark.color}`:undefined}} />)}</div>{error&&<p className="pdf-error">{error}</p>}<small className="pdf-page-number">{pageNumber}</small></div>;
}
export default function PdfPane(props: Props) {
  const {paper,serverId,annotations,scrollRef} = props;
  const [pdf,setPdf] = useState<PDFDocumentProxy | null>(null), [error,setError] = useState(''), [fit,setFit] = useState(600), [zoom,setZoom] = useState(1);
  const area = useRef<{x:number;y:number;page:HTMLElement} | null>(null);
  useEffect(()=>{let active=true;let destroy:(()=>void)|undefined;
    setPdf(null);setError('');
    void import('pdfjs-dist').then(library=>{library.GlobalWorkerOptions.workerSrc='/pdf.worker.min.mjs';const task=library.getDocument({url:`/api/parser/api/documents/${serverId}/original.pdf`,disableAutoFetch:true,disableStream:true,rangeChunkSize:131072,cMapUrl:'/pdf-cmaps/',cMapPacked:true,standardFontDataUrl:'/pdf-fonts/'});destroy=()=>void task.destroy();return task.promise;}).then(document=>{if(active)setPdf(document);}).catch(()=>{if(active)setError('原 PDF 暂时无法加载，网页仍可阅读。');});
    return ()=>{active=false;destroy?.();};
  },[serverId]);
  useEffect(()=>{const element=scrollRef.current;if(!element)return;const observer=new ResizeObserver(()=>setFit(Math.max(240,element.clientWidth-32)));observer.observe(element);return ()=>observer.disconnect();},[scrollRef]);
  useEffect(()=>{const element=scrollRef.current;if(!element)return;const wheel=(event:WheelEvent)=>{if(!event.ctrlKey)return;event.preventDefault();setZoom(value=>Math.max(.6,Math.min(2.5,value*Math.exp(-event.deltaY*.002))));};element.addEventListener('wheel',wheel,{passive:false});return ()=>element.removeEventListener('wheel',wheel);},[scrollRef]);
  const saveSelection = () => {
    if(!props.style || props.style==='area') return;
    const selection=window.getSelection();if(!selection?.rangeCount || selection.isCollapsed)return;
    const range=selection.getRangeAt(0);const node=range.startContainer instanceof Element?range.startContainer:range.startContainer.parentElement;
    const page=node?.closest<HTMLElement>('[data-pdf-page]');if(!page || !scrollRef.current?.contains(page))return;
    const end=range.endContainer instanceof Element?range.endContainer:range.endContainer.parentElement;
    if(end?.closest('[data-pdf-page]')!==page){props.onNotice('请分页面选择 PDF 文字。');return;}
    const pageNumber=Number(page.dataset.pdfPage), quote=selection.toString().trim();if(!quote)return;
    const pdfRects: PdfRectangle[] = Array.from(range.getClientRects()).filter(rect=>rect.width>0&&rect.height>0).slice(0,200).map(rect=>({page:pageNumber,bbox:relativeRect(rect,page.getBoundingClientRect())}));
    const anchor=matchPdfSelection(allBlocks(paper),pageNumber,quote);
    if(!anchor){
      const first = pdfRects[0];const geometry = paper.pages?.find(item=>item.number===pageNumber);
      const block = allBlocks(paper).filter(item=>item.page===pageNumber).sort((a,b)=>Math.abs((a.bbox?.y||0)/(geometry?.height||1)-(first?.bbox.y||0))-Math.abs((b.bbox?.y||0)/(geometry?.height||1)-(first?.bbox.y||0)))[0];
      if(first) {props.onAnnotation({id:crypto.randomUUID(),documentId:paper.id,type:'area',noteEnabled:props.noteEnabled,color:props.color,anchor:{blockId:block?.id || `pdf-page-${pageNumber}`,page:pageNumber,bbox:first.bbox,space:'page',pdfRects,pdfOnly:true},createdAt:Date.now()});selection.removeAllRanges();props.onNotice('已保存 PDF 高亮；这段文字无法可靠匹配到网页，暂仅显示在原文中。');}
      else props.onNotice('这一页没有可关联的正文区域，无法保存标注。');
      return;
    }
    props.onAnnotation({id:crypto.randomUUID(),documentId:paper.id,type:props.noteEnabled?'note':props.style,style:props.style,noteEnabled:props.noteEnabled,color:props.color,anchor:{...anchor,pdfRects},createdAt:Date.now()});selection.removeAllRanges();
  };
  return <section className="pdf-pane" aria-label="PDF 原文"><div className="pdf-pane-toolbar"><strong>PDF 原文</strong><span>点击并选择文字即可标注</span><button aria-label="缩小 PDF" onClick={()=>setZoom(value=>Math.max(.6,value-.1))}><Minus size={15}/></button><button onClick={()=>setZoom(1)} title="适合宽度">{Math.round(zoom*100)}%</button><button aria-label="放大 PDF" onClick={()=>setZoom(value=>Math.min(2.5,value+.1))}><Plus size={15}/></button></div><div ref={scrollRef} className="pdf-scroll" onMouseUp={saveSelection} onPointerDown={event=>{if(props.style!=='area')return;const page=(event.target as Element).closest<HTMLElement>('[data-pdf-page]');if(page)area.current={x:event.clientX,y:event.clientY,page};}} onPointerUp={event=>{
    const start=area.current;area.current=null;if(!start)return;const surface=start.page.getBoundingClientRect();
    const box=relativeRect(new DOMRect(Math.min(start.x,event.clientX),Math.min(start.y,event.clientY),Math.abs(event.clientX-start.x),Math.abs(event.clientY-start.y)),surface);if(box.width<.005||box.height<.005)return;
    const page=Number(start.page.dataset.pdfPage);const geometry=paper.pages?.find(item=>item.number===page);
    const blocks=allBlocks(paper).filter(block=>block.page===page);const block=blocks.sort((a,b)=>Math.abs((a.bbox?.y||0)/(geometry?.height||1)-box.y)-Math.abs((b.bbox?.y||0)/(geometry?.height||1)-box.y))[0];
    props.onAnnotation({id:crypto.randomUUID(),documentId:paper.id,type:'area',noteEnabled:props.noteEnabled,color:props.color,anchor:{blockId:block?.id || `pdf-page-${page}`,pdfOnly:!block,page,bbox:box,space:'page',pdfRects:[{page,bbox:box}]},createdAt:Date.now()});
  }} onPointerCancel={()=>{area.current=null;}} style={{touchAction:props.style==='area'?'none':undefined,userSelect:props.style==='area'?'none':undefined}}>{error?<p className="pdf-error">{error}</p>:pdf?Array.from({length:pdf.numPages},(_,index)=><PdfPage key={index} pdf={pdf} pageNumber={index+1} width={fit*zoom} paper={paper} annotations={annotations} root={scrollRef}/>):<p className="pdf-loading">正在加载原 PDF…</p>}</div></section>;
}
