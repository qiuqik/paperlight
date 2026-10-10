'use client';
import {Fragment, useEffect, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import katex from 'katex';
import type {Annotation, Block, DocumentModel, InlineNode, PageGeometry, Section} from '@/lib/document';
import {isTextAnchor} from '@/lib/document';
import PaperIdentity from './PaperIdentity';
import {ParagraphTranslation, TranslationContext} from './ParagraphTranslation';
import type {TranslationController} from '@/lib/useDocumentTranslations';

function renderedLatex(latex: string, displayMode: boolean) {
  let expression = latex.trim();
  if (expression.startsWith('\\(') && expression.endsWith('\\)')) expression = expression.slice(2, -2).trim();
  else if (expression.startsWith('\\[') && expression.endsWith('\\]')) expression = expression.slice(2, -2).trim();
  else if (expression.startsWith('$$') && expression.endsWith('$$')) expression = expression.slice(2, -2).trim();
  else if (expression.startsWith('$') && expression.endsWith('$')) expression = expression.slice(1, -1).trim();
  // The reader positions equation numbers itself. A model may transcribe the
  // printed number as \tag{...}; KaTeX's own tag then overlaps the equation.
  if (displayMode) expression = expression.replace(/(?:\\tag\*?\s*\{[^{}]*\}\s*)+$/, '').trim();
  try { return katex.renderToString(expression, {displayMode, throwOnError: true, trust: false, output: 'htmlAndMathml'}); }
  catch { return undefined; }
}

function MathExpression({latex, mathml, text, src, displayMode = false}: {latex?: string; mathml?: string; text?: string; src?: string; displayMode?: boolean}) {
  if (latex) {
    const html = renderedLatex(latex, displayMode);
    if (html) return <span className="math-expression" aria-label={text || latex} dangerouslySetInnerHTML={{__html: html}} />;
  }
  if (mathml) return <span className="math-expression" aria-label={text || latex} dangerouslySetInnerHTML={{__html: mathml}} />;
  if (src) return <img className={displayMode ? 'formula-crop' : 'inline-formula-crop'} src={src} alt={text || '原 PDF 公式'} />;
  return <span className="math-fallback">{text || '公式未能识别'}</span>;
}

function InlineFormula({node}: {node: InlineNode}) {
  const hasRenderedMath = !!(node.latex && renderedLatex(node.latex, false) || node.mathml);
  const expression = <MathExpression latex={node.latex} mathml={node.mathml} src={node.src} text={node.text || node.display} />;
  return <span className="embedded-equation">{node.src && !hasRenderedMath ? <ZoomableImage src={node.src} label="行内公式" className="inline-image-open">{expression}</ZoomableImage> : expression}{node.number != null && <span className="equation-number">({node.number})</span>}</span>;
}

function Inline({nodes, onReference}: {nodes: InlineNode[]; onReference: (id: string) => void}) {
  return <>{nodes.map((node, index) => {
    const label = node.display || node.text || '';
    if (node.type === 'inlineEquation') return <InlineFormula node={node} key={index} />;
    if (node.type === 'citation') return <button className="inline-link" key={index} onClick={() => node.referenceIds?.[0] && onReference(node.referenceIds[0])}>{label}</button>;
    if (node.type === 'figureLink' || node.type === 'tableLink') return <button className="inline-link" key={index} onClick={() => document.getElementById(node.figureId || node.tableId || '')?.scrollIntoView({behavior: 'smooth'})}>{label}</button>;
    if (node.type === 'link' && /^https?:\/\//.test(node.href || '')) return <a key={index} href={node.href} target="_blank" rel="noreferrer">{label}</a>;
    if (node.type === 'link' && node.href?.startsWith('#h-')) return <a key={index} href={node.href}>{label}</a>;
    let content: React.ReactNode = node.type === 'text' ? <PlainText text={label} onReference={onReference} /> : label;
    if (node.bold) content = <strong>{content}</strong>;
    if (node.italic) content = <em>{content}</em>;
    if (node.type === 'superscript') content = <sup>{content}</sup>;
    if (node.type === 'subscript') content = <sub>{content}</sub>;
    return <span key={index}>{content}</span>;
  })}</>;
}
function PlainText({text, onReference}: {text: string; onReference: (id: string) => void}) {
  return <>{text.split(/(\[\d+(?:\s*[,–-]\s*\d+)*\]|\b(?:Fig(?:ure)?\.?|Table)\s*\d+(?:\.\d+)*\b)/gi).map((part, index) => {
    const citation = part.match(/^\[(\d+)\]$/);
    if (citation) return <button className="inline-link" key={index} onClick={() => onReference(citation[1])}>{part}</button>;
    const figure = part.match(/^(?:Fig(?:ure)?\.?)\s*(\d+(?:\.\d+)*)$/i);
    if (figure) return <button className="inline-link" key={index} onClick={() => (document.getElementById(`figure-${figure[1].replaceAll('.', '-')}`) || document.querySelector(`[data-figure-number="${figure[1]}"]`))?.scrollIntoView({behavior: 'smooth'})}>{part}</button>;
    const table = part.match(/^Table\s*(\d+(?:\.\d+)*)$/i);
    if (table) return <button className="inline-link" key={index} onClick={() => document.getElementById(`table-${table[1].replaceAll('.', '-')}`)?.scrollIntoView({behavior: 'smooth'})}>{part}</button>;
    return <span key={index}>{part}</span>;
  })}</>;
}

function AreaMarks({block, annotations, pages, surface = 'block'}: {block: Block; annotations: Annotation[]; pages: PageGeometry[]; surface?: 'block' | 'image'}) {
  return <>{annotations.filter(annotation => annotation.type === 'area' && !isTextAnchor(annotation.anchor) && annotation.anchor.blockId === block.id && (annotation.anchor.surface === 'image') === (surface === 'image')).map(annotation => {
    const anchor = annotation.anchor;
    if (isTextAnchor(anchor) || anchor.pdfOnly) return null;
    let {bbox} = anchor;
    const page = pages.find(item => item.number === anchor.page);
    if (anchor.space === 'page' && page && block.bbox) bbox = {
      x: (bbox.x * page.width - block.bbox.x) / block.bbox.width,
      y: (bbox.y * page.height - block.bbox.y) / block.bbox.height,
      width: bbox.width * page.width / block.bbox.width,
      height: bbox.height * page.height / block.bbox.height,
    };
    return <span key={annotation.id} className="area-mark" style={{left: `${bbox.x * 100}%`, top: `${bbox.y * 100}%`, width: `${bbox.width * 100}%`, height: `${bbox.height * 100}%`, borderColor: annotation.color, backgroundColor: `${annotation.color}33`}} />;
  })}</>;
}
function ZoomableImage({src, label, className, children}: {src: string; label: string; className: string; children: React.ReactNode}) {
  const [preview, setPreview] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [imageSize, setImageSize] = useState<{width: number; height: number} | null>(null);
  const lightboxRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!preview) return;
    const close = (event: KeyboardEvent) => {if (event.key === 'Escape') setPreview(false);};
    const wheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      setZoom(value => Math.min(8, Math.max(.5, value * Math.exp(-event.deltaY * .008))));
    };
    window.addEventListener('keydown', close);
    lightboxRef.current?.addEventListener('wheel', wheel, {passive: false});
    const element = lightboxRef.current;
    return () => {window.removeEventListener('keydown', close); element?.removeEventListener('wheel', wheel);};
  }, [preview]);
  const fit = imageSize && typeof window !== 'undefined' ? Math.min(1, (window.innerWidth - 72) / imageSize.width, (window.innerHeight - 120) / imageSize.height) : 1;
  return <><button type="button" className={className} aria-label={`放大${label}`} onClick={() => {setZoom(1); setImageSize(null); setPreview(true);}}>{children}</button>{preview && createPortal(<div ref={lightboxRef} className="figure-lightbox" role="dialog" aria-modal="true" aria-label={`${label}图片预览`} onMouseDown={event => {if (event.target === event.currentTarget) setPreview(false);}}><div className="figure-lightbox-toolbar"><span>{label} · Ctrl + 滚轮或触控板缩放</span><button type="button" onClick={() => setZoom(value => Math.max(.5, value / 1.25))} aria-label="缩小">−</button><button type="button" onClick={() => setZoom(value => Math.min(8, value * 1.25))} aria-label="放大">＋</button><span>{Math.round(zoom * 100)}%</span><button type="button" onClick={() => setPreview(false)} aria-label="关闭">×</button></div><div className="figure-lightbox-scroll" onClick={() => setPreview(false)}><img src={src} alt={label} onLoad={event => setImageSize({width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight})} style={{width: imageSize ? `${Math.round(imageSize.width * fit * zoom)}px` : 'auto', maxWidth: imageSize ? 'none' : '100%'}} /></div></div>, document.body)}</>;
}
function AreaImage({block, annotations, pages, alt, className}: {block: Block; annotations: Annotation[]; pages: PageGeometry[]; alt: string; className?: string}) {
  const content = <><img className={className} src={block.src} alt={alt} draggable={false} /><AreaMarks block={block} annotations={annotations} pages={pages} surface="image" /></>;
  return block.src ? <ZoomableImage src={block.src} label={alt} className="area-surface figure-open">{content}</ZoomableImage> : <span className="area-surface">{content}</span>;
}
function TableGrid({block, onReference}: {block: Block; onReference: (id: string) => void}) {
  if (!block.tableRows?.length) return <div className="table-scroll"><table><thead><tr>{(block.headers || []).map((cell, index) => <th key={index}>{typeof cell === 'string' ? cell : cell.text}</th>)}</tr></thead><tbody>{(block.rows || []).map((row, index) => <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex}>{typeof cell === 'string' ? cell : cell.text}</td>)}</tr>)}</tbody></table></div>;
  const renderRows = (group: 'head' | 'body' | 'foot') => block.tableRows?.filter(row => row.group === group).map((row, index) => <tr key={`${group}-${index}`}>{row.cells.map((cell, cellIndex) => {
    const Cell = cell.header ? 'th' : 'td';
    return <Cell key={cellIndex} colSpan={cell.colSpan} rowSpan={cell.rowSpan} className={`scholarly-cell align-${cell.align} top-${cell.topRule} bottom-${cell.bottomRule}`}>{cell.content?.length ? <Inline nodes={cell.content} onReference={onReference} /> : cell.text}</Cell>;
  })}</tr>);
  return <div className="table-scroll scholarly-table-scroll"><table className="scholarly-table">{block.tableRows.some(row => row.group === 'head') && <thead>{renderRows('head')}</thead>}<tbody>{renderRows('body')}</tbody>{block.tableRows.some(row => row.group === 'foot') && <tfoot>{renderRows('foot')}</tfoot>}</table></div>;
}
function EquationBlock({block, annotations, pages}: {block: Block; annotations: Annotation[]; pages: PageGeometry[]}) {
  const latex = block.latex || '';
  return <figure data-block-id={block.id} data-page={block.page} className={`paper-equation area-target${block.number != null ? ' numbered-equation' : ''}`}>
    {block.src && !((latex && renderedLatex(latex, true)) || block.mathml) ? <ZoomableImage src={block.src} label={`公式${block.number ? ` ${block.number}` : ''}`} className="formula-image-open"><MathExpression latex={latex} mathml={block.mathml} src={block.src} text={block.text} displayMode /></ZoomableImage> : <MathExpression latex={latex} mathml={block.mathml} src={block.src} text={block.text} displayMode />}
    {block.number != null && <span className="equation-number" aria-label={`公式编号 ${block.number}`}>({block.number})</span>}
    <AreaMarks block={block} annotations={annotations} pages={pages} />
  </figure>;
}
function BlockView({block, annotations, pages, onReference, pdfUrl, prompt = false, inlineParagraph = false}: {block: Block; annotations: Annotation[]; pages: PageGeometry[]; onReference: (id: string) => void; pdfUrl?: string; prompt?: boolean; inlineParagraph?: boolean}) {
  const common = {'data-block-id': block.id, 'data-page': block.page};
  const Paragraph = inlineParagraph ? 'span' : 'p';
  const originalPage = pdfUrl ? `${pdfUrl}#page=${block.page || 1}` : undefined;
  switch (block.type) {
    case 'paragraph': return <Paragraph {...common} className={`area-target${prompt && /^(?:[A-Z][A-Z\s()&-]{5,}|Step \d+\s*[—–-])/.test(block.text || '') ? ' prompt-label' : ''}`}>{block.content?.length ? <Inline nodes={block.content} onReference={onReference} /> : <PlainText text={block.text || ''} onReference={onReference} />}{block.source === 'pdf_original_paragraph' && originalPage && <a className="formula-original" href={originalPage} target="_blank" rel="noreferrer">查看原文</a>}<AreaMarks block={block} annotations={annotations} pages={pages} /></Paragraph>;
    case 'figure': return <figure {...common} id={block.id} data-figure-number={block.label?.replace(/^Figure\s+/i, '') || block.number} className="paper-figure area-target" onDragStart={event => event.preventDefault()}>{block.src && <AreaImage block={block} annotations={annotations} pages={pages} alt={block.caption || block.label || '论文插图'} />}{block.source === 'arxiv_html_missing_visual' && <div className="source-notice">arXiv HTML 未提供这张图像。{block.sourceUrl && <a href={block.sourceUrl} target="_blank" rel="noreferrer">查看该版本的原 PDF</a>}</div>}<AreaMarks block={block} annotations={annotations} pages={pages} /><figcaption><strong>{block.label || `Figure ${block.number}`}</strong> {block.captionContent?.length ? <Inline nodes={block.captionContent} onReference={onReference} /> : <PlainText text={block.caption || ''} onReference={onReference} />}</figcaption></figure>;
    case 'table': return <figure {...common} id={block.id} className="paper-figure area-target scholarly-table-figure" onDragStart={event => event.preventDefault()}><figcaption><strong>{block.label || `Table ${block.number}`}</strong> {block.captionContent?.length ? <Inline nodes={block.captionContent} onReference={onReference} /> : <PlainText text={block.caption || ''} onReference={onReference} />}</figcaption>{block.src ? <AreaImage block={block} annotations={annotations} pages={pages} alt={block.caption || '论文表格'} /> : <TableGrid block={block} onReference={onReference} />}<AreaMarks block={block} annotations={annotations} pages={pages} /></figure>;
    case 'equation': return <EquationBlock block={block} annotations={annotations} pages={pages} />;
    case 'list': {const List = block.listOrdered ? 'ol' : 'ul'; return <div {...common} className="area-target"><List>{(block.items || []).map((item, index) => <li key={index}>{block.listContent?.[index]?.length ? <Inline nodes={block.listContent[index]} onReference={onReference} /> : item}</li>)}</List><AreaMarks block={block} annotations={annotations} pages={pages} /></div>;}
    case 'quote': return <blockquote {...common} className="area-target">{block.text}<AreaMarks block={block} annotations={annotations} pages={pages} /></blockquote>;
    case 'heading': return <h3 {...common} className="area-target">{block.content?.length ? <Inline nodes={block.content} onReference={onReference} /> : block.text}<AreaMarks block={block} annotations={annotations} pages={pages} /></h3>;
    case 'code': return block.src ? <figure {...common} className="area-target"><AreaImage block={block} annotations={annotations} pages={pages} alt={block.text || '代码'} /><AreaMarks block={block} annotations={annotations} pages={pages} /></figure> : <pre {...common} className="area-target">{block.text}<AreaMarks block={block} annotations={annotations} pages={pages} /></pre>;
    case 'footnote': return <aside {...common} className="footnote area-target">{block.text}<AreaMarks block={block} annotations={annotations} pages={pages} /></aside>;
    default: return <p {...common} className="area-target">{block.text}<AreaMarks block={block} annotations={annotations} pages={pages} /></p>;
  }
}

function SectionView({section, annotations, pages, onReference, pdfUrl, arxivHtml}: {section: Section; annotations: Annotation[]; pages: PageGeometry[]; onReference: (id: string) => void; pdfUrl?: string; arxivHtml: boolean}) {
  const Heading = section.level === 1 ? 'h2' : section.level === 2 ? 'h3' : 'h4';
  const prompt = section.presentation === 'prompt' && (!arxivHtml || /^(?:\d+(?:\.\d+)*\.?\s*)?Prompts?$/i.test(section.title.trim()));
  const renderBlocks = (blocks: Block[]) => {
    const groups: Block[][] = [];
    for (const block of blocks) {
      const group = groups.at(-1);
      if (!prompt && block.type === 'paragraph' && block.continuesPrevious && group?.at(-1)?.type === 'paragraph') group.push(block);
      else groups.push([block]);
    }
    return groups.map(group => <Fragment key={group[0].id}>{group.length === 1
      ? <BlockView key={group[0].id} block={group[0]} annotations={annotations} pages={pages} onReference={onReference} pdfUrl={pdfUrl} prompt={prompt} />
      : <p key={group[0].id} className="continued-paragraph">{group.map((block, index) => <span key={block.id}>{index > 0 && ' '}<BlockView block={block} annotations={annotations} pages={pages} onReference={onReference} pdfUrl={pdfUrl} inlineParagraph /></span>)}</p>}<ParagraphTranslation targetId={`block:${group[0].id}`} /></Fragment>);
  };
  return <section id={section.id} className={section.type === 'abstract' ? 'abstract-section' : prompt ? 'prompt-section' : ''}>{renderBlocks(section.blocks.filter(block => block.beforeHeading))}{(!section.tocHidden || prompt) && <Fragment><Heading>{section.title}</Heading><ParagraphTranslation targetId={`section:${section.id}`} /></Fragment>}{renderBlocks(section.blocks.filter(block => !block.beforeHeading))}</section>;
}

export default function DocumentRenderer({document: paper, annotations, onReference, translations}: {document: DocumentModel; annotations: Annotation[]; onReference: (id: string) => void; translations: TranslationController}) {
  const pdfUrl = paper.source && paper.source !== 'arxiv_html' ? `/api/parser/api/documents/${paper.id}/original.pdf` : undefined;
  return <TranslationContext.Provider value={translations}>
    <header className="paper-header"><div className="eyebrow">{paper.arxivId ? `ARXIV · ${paper.arxivId}v${paper.arxivVersion} · ${paper.source === 'arxiv_html' ? '官方 HTML' : 'PDF 原文'}` : `PAPERLIGHT · ${paper.metadata.pageCount || '—'} PAGES`}</div><h1>{paper.metadata.title}</h1><ParagraphTranslation targetId="title" /><PaperIdentity key={paper.id} paper={paper} />{paper.fallbackReason && <p className="source-notice">官方 HTML 不完整，已使用固定版本的 PDF。</p>}</header>
    {paper.sections.map(section => <SectionView key={section.id} section={section} annotations={annotations} pages={paper.pages || []} onReference={onReference} pdfUrl={pdfUrl} arxivHtml={paper.source === 'arxiv_html'} />)}
  </TranslationContext.Provider>;
}
