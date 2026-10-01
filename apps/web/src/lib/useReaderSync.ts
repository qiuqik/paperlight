'use client';
import {useEffect, type RefObject} from 'react';
import type {DocumentModel} from './document';
import {allBlocks} from './document';
export function useReaderSync(article: RefObject<HTMLElement | null>, pdf: RefObject<HTMLDivElement | null>, paper: DocumentModel, enabled: boolean) {
  useEffect(()=>{
    const html=article.current, original=pdf.current;if(!enabled||!html||!original)return;
    let lockedUntil=0;let frame=0;const blocks=allBlocks(paper);const blockMap=new Map(blocks.map(block=>[block.id,block]));
    const sync=(source:'html'|'pdf')=>{if(performance.now()<lockedUntil)return;cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{
      if(source==='html') {
        const top=html.getBoundingClientRect().top+30;
        const elements=Array.from(html.querySelectorAll<HTMLElement>('[data-block-id]'));
        const element=elements.find(element=>element.getBoundingClientRect().bottom>top);const block=element&&blockMap.get(element.dataset.blockId!);
        const page=original.querySelector<HTMLElement>(`[data-pdf-page="${block?.page || 1}"]`);if(!page)return;
        const size=paper.pages?.find(item=>item.number===block?.page);const fraction=size&&block?.bbox?block.bbox.y/size.height:0;
        lockedUntil=performance.now()+200;original.scrollTop+=page.getBoundingClientRect().top-original.getBoundingClientRect().top+fraction*page.clientHeight-25;
      } else {
        const top=original.getBoundingClientRect().top+30;
        const page=Array.from(original.querySelectorAll<HTMLElement>('[data-pdf-page]')).find(page=>page.getBoundingClientRect().bottom>top);if(!page)return;
        const number=Number(page.dataset.pdfPage);const size=paper.pages?.find(item=>item.number===number);const fraction=Math.max(0,(top-page.getBoundingClientRect().top)/page.clientHeight);
        const candidates=blocks.filter(block=>block.page===number);
        const block=candidates.sort((a,b)=>Math.abs((a.bbox?.y||0)/(size?.height||1)-fraction)-Math.abs((b.bbox?.y||0)/(size?.height||1)-fraction))[0];
        const element=block&&Array.from(html.querySelectorAll<HTMLElement>('[data-block-id]')).find(element=>element.dataset.blockId===block.id);if(!element)return;
        lockedUntil=performance.now()+200;html.scrollTop+=element.getBoundingClientRect().top-html.getBoundingClientRect().top-30;
      }
    });};
    const initialPages = new MutationObserver(() => {lockedUntil=0;sync('html');});
    initialPages.observe(original,{childList:true});
    const htmlScroll=()=>sync('html'),pdfScroll=()=>sync('pdf');html.addEventListener('scroll',htmlScroll,{passive:true});original.addEventListener('scroll',pdfScroll,{passive:true});
    return ()=>{initialPages.disconnect();cancelAnimationFrame(frame);html.removeEventListener('scroll',htmlScroll);original.removeEventListener('scroll',pdfScroll);};
  },[article,pdf,paper,enabled]);
}
