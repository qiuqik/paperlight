'use client';
import {useState} from 'react';
import katex from 'katex';
import type {Annotation, Block, DocumentModel, InlineNode, PageGeometry, Section} from '@/lib/document';
import {isTextAnchor} from '@/lib/document';

function MathExpression({latex, mathml, text, src, displayMode = false, preferOriginal = false}: {latex?: string; mathml?: string; text?: string; src?: string; displayMode?: boolean; preferOriginal?: boolean}) {
  if (src) return <img className={displayMode ? 'formula-crop' : 'inline-formula-crop'} src={src} alt={text || '原 PDF 公式'} />;
  if (latex && !preferOriginal) {
    try {
      const html = katex.renderToString(latex, {displayMode, throwOnError: true, trust: false, output: 'htmlAndMathml'});
      return <span className="math-expression" aria-label={text || latex} dangerouslySetInnerHTML={{__html: html}} />;
    } catch { /* Show the extracted text when the LaTeX cannot be parsed. */ }
  }
  if (mathml) return <span className="math-expression" aria-label={text || latex} dangerouslySetInnerHTML={{__html: mathml}} />;
  return <span className="math-fallback">{text || '公式未能识别'}</span>;
}

function Inline({nodes, onReference, pdfUrl}: {nodes: InlineNode[]; onReference: (id: string) => void; pdfUrl?: string}) {
  return <>{nodes.map((node, index) => {
    const label = node.display || node.text || '';
    if (node.type === 'inlineEquation') return <span className="embedded-equation" key={index}><MathExpression latex={node.latex} mathml={node.mathml} src={node.src} text={node.text || node.display} preferOriginal={!!pdfUrl} />{pdfUrl && <a className="formula-original" href={pdfUrl} target="_blank" rel="noreferrer" title="查看原 PDF 中的公式">原文</a>}{node.number != null && <span className="equation-number">({node.number})</span>}</span>;
    if (node.type === 'citation') return <button className="inline-link" key={index} onClick={() => node.referenceIds?.[0] && onReference(node.referenceIds[0])}>{label}</button>;
    if (node.type === 'figureLink' || node.type === 'tableLink') return <button className="inline-link" key={index} onClick={() => document.getElementById(node.figureId || node.tableId || '')?.scrollIntoView({behavior: 'smooth'})}>{label}</button>;
    if (node.type === 'link' && /^https?:\/\//.test(node.href || '')) return <a key={index} href={node.href} target="_blank" rel="noreferrer">{label}</a>;
    if (node.type === 'link' && node.href?.startsWith('#h-')) return <a key={index} href={node.href}>{label}</a>;
    let content: React.ReactNode = label;
    if (node.bold) content = <strong>{content}</strong>;
    if (node.italic) content = <em>{content}</em>;
    if (node.type === 'superscript') content = <sup>{content}</sup>;
    if (node.type === 'subscript') content = <sub>{content}</sub>;
    return <span key={index}>{content}</span>;
  })}</>;
}
function PlainText({text, onReference}: {text: string; onReference: (id: string) => void}) {
  return <>{text.split(/(\[\d+(?:\s*[,–-]\s*\d+)*\]|\b(?:Fig(?:ure)?\.?|Table)\s+\d+\b)/gi).map((part, index) => {
    const citation = part.match(/^\[(\d+)\]$/);
    if (citation) return <button className="inline-link" key={index} onClick={() => onReference(citation[1])}>{part}</button>;
    const figure = part.match(/^(?:Fig(?:ure)?\.?)\s+(\d+)$/i);
    if (figure) return <button className="inline-link" key={index} onClick={() => document.getElementById(`figure-${figure[1]}`)?.scrollIntoView({behavior: 'smooth'})}>{part}</button>;
    const table = part.match(/^Table\s+(\d+)$/i);
    if (table) return <button className="inline-link" key={index} onClick={() => document.getElementById(`table-${table[1]}`)?.scrollIntoView({behavior: 'smooth'})}>{part}</button>;
    return <span key={index}>{part}</span>;
  })}</>;
}

function AreaMarks({block, annotations, pages, surface = 'block'}: {block: Block; annotations: Annotation[]; pages: PageGeometry[]; surface?: 'block' | 'image'}) {
  return <>{annotations.filter(annotation => annotation.type === 'area' && !isTextAnchor(annotation.anchor) && annotation.anchor.blockId === block.id && (annotation.anchor.surface === 'image') === (surface === 'image')).map(annotation => {
    const anchor = annotation.anchor;
    if (isTextAnchor(anchor)) return null;
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
function AreaImage({block, annotations, pages, alt, className}: {block: Block; annotations: Annotation[]; pages: PageGeometry[]; alt: string; className?: string}) {
  return <span className="area-surface"><img className={className} src={block.src} alt={alt} draggable={false} /><AreaMarks block={block} annotations={annotations} pages={pages} surface="image" /></span>;
}
function TableGrid({block, onReference}: {block: Block; onReference: (id: string) => void}) {
  if (!block.tableRows?.length) return <div className="table-scroll"><table><thead><tr>{(block.headers || []).map((cell, index) => <th key={index}>{typeof cell === 'string' ? cell : cell.text}</th>)}</tr></thead><tbody>{(block.rows || []).map((row, index) => <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex}>{typeof cell === 'string' ? cell : cell.text}</td>)}</tr>)}</tbody></table></div>;
  const renderRows = (group: 'head' | 'body' | 'foot') => block.tableRows?.filter(row => row.group === group).map((row, index) => <tr key={`${group}-${index}`}>{row.cells.map((cell, cellIndex) => {
    const Cell = cell.header ? 'th' : 'td';
    return <Cell key={cellIndex} colSpan={cell.colSpan} rowSpan={cell.rowSpan} className={`scholarly-cell align-${cell.align} top-${cell.topRule} bottom-${cell.bottomRule}`}>{cell.content?.length ? <Inline nodes={cell.content} onReference={onReference} /> : cell.text}</Cell>;
  })}</tr>);
  return <div className="table-scroll scholarly-table-scroll"><table className="scholarly-table">{block.tableRows.some(row => row.group === 'head') && <thead>{renderRows('head')}</thead>}<tbody>{renderRows('body')}</tbody>{block.tableRows.some(row => row.group === 'foot') && <tfoot>{renderRows('foot')}</tfoot>}</table></div>;
}
function EquationBlock({block, annotations, pages, pdfUrl, documentId}: {block: Block; annotations: Annotation[]; pages: PageGeometry[]; pdfUrl?: string; documentId: string}) {
  const [latex, setLatex] = useState(block.latex || '');
  const [draft, setDraft] = useState(block.latex || '');
  const [editing, setEditing] = useState(false);
  const [message, setMessage] = useState('');
  const save = async () => {
    try {
      const response = await fetch(`/api/parser/api/documents/${encodeURIComponent(documentId)}/formulas/${encodeURIComponent(block.id)}`, {
        method: 'PATCH', headers: {'content-type': 'application/json'}, body: JSON.stringify({revised: draft}),
      });
      if (!response.ok) throw new Error(`保存失败 (${response.status})`);
      setLatex(draft.trim());
      setEditing(false);
      setMessage('转写已保存；阅读视图仍显示原 PDF 公式。');
    } catch (error) {setMessage(error instanceof Error ? error.message : '保存失败');}
  };
  return <figure data-block-id={block.id} data-page={block.page} className={`paper-equation area-target${block.number != null ? ' numbered-equation' : ''}`}>
    <MathExpression latex={latex} mathml={block.mathml} src={block.src} text={block.text} displayMode preferOriginal={!!pdfUrl} />
    {pdfUrl && <a className="formula-original" href={`${pdfUrl}#page=${block.page || 1}`} target="_blank" rel="noreferrer">查看原文</a>}
    {latex && <button className="formula-copy" type="button" title={pdfUrl ? '辅助转写，可能与原公式不一致' : '原始 TeX'} onClick={() => void navigator.clipboard.writeText(latex)}>复制 TeX</button>}
    {pdfUrl && <button className="formula-copy" type="button" onClick={() => {setDraft(latex); setEditing(value => !value); setMessage('');}}>修订转写</button>}
    {block.number != null && <span className="equation-number" aria-label={`公式编号 ${block.number}`}>({block.number})</span>}
    {editing && <div className="formula-revision"><label htmlFor={`formula-${block.id}`}>原图的 LaTeX 转写</label><textarea id={`formula-${block.id}`} value={draft} onChange={event => setDraft(event.target.value)} rows={3} /><button type="button" onClick={() => void save()}>保存转写</button></div>}
    {message && <p className="formula-revision-message" role="status">{message}</p>}
    <AreaMarks block={block} annotations={annotations} pages={pages} />
  </figure>;
}
function BlockView({block, annotations, pages, onReference, pdfUrl, documentId, prompt = false}: {block: Block; annotations: Annotation[]; pages: PageGeometry[]; onReference: (id: string) => void; pdfUrl?: string; documentId: string; prompt?: boolean}) {
  const common = {'data-block-id': block.id, 'data-page': block.page};
  const originalPage = pdfUrl ? `${pdfUrl}#page=${block.page || 1}` : undefined;
  switch (block.type) {
    case 'paragraph': return block.source === 'pdf_original_paragraph' && block.src
      ? <figure {...common} className="original-paragraph area-target"><AreaImage block={block} annotations={annotations} pages={pages} alt={block.text || '包含行内公式的原 PDF 段落'} /><figcaption>原 PDF 段落 · 行内公式以原文呈现{originalPage && <a href={originalPage} target="_blank" rel="noreferrer">查看原页</a>}<details><summary>查看提取文本</summary><PlainText text={block.text || ''} onReference={onReference} /></details></figcaption></figure>
      : <p {...common} className={`area-target${prompt && /^(?:[A-Z][A-Z\s()&-]{5,}|Step \d+\s*[—–-])/.test(block.text || '') ? ' prompt-label' : ''}`}>{block.content?.length ? <Inline nodes={block.content} onReference={onReference} pdfUrl={originalPage} /> : <PlainText text={block.text || ''} onReference={onReference} />}<AreaMarks block={block} annotations={annotations} pages={pages} /></p>;
    case 'figure': return <figure {...common} id={block.id} className="paper-figure area-target" onDragStart={event => event.preventDefault()}>{block.src && <AreaImage block={block} annotations={annotations} pages={pages} alt={block.caption || block.label || '论文插图'} />}{block.source === 'arxiv_html_missing_visual' && <div className="source-notice">arXiv HTML 未提供这张图像。{block.sourceUrl && <a href={block.sourceUrl} target="_blank" rel="noreferrer">查看该版本的原 PDF</a>}</div>}<AreaMarks block={block} annotations={annotations} pages={pages} /><figcaption><strong>{block.label || `Figure ${block.number}`}</strong> {block.captionContent?.length ? <Inline nodes={block.captionContent} onReference={onReference} /> : <PlainText text={block.caption || ''} onReference={onReference} />}</figcaption></figure>;
    case 'table': return <figure {...common} id={block.id} className="paper-figure area-target scholarly-table-figure" onDragStart={event => event.preventDefault()}><figcaption><strong>{block.label || `Table ${block.number}`}</strong> {block.captionContent?.length ? <Inline nodes={block.captionContent} onReference={onReference} /> : <PlainText text={block.caption || ''} onReference={onReference} />}</figcaption>{block.src ? <AreaImage block={block} annotations={annotations} pages={pages} alt={block.caption || '论文表格'} /> : <TableGrid block={block} onReference={onReference} />}<AreaMarks block={block} annotations={annotations} pages={pages} /></figure>;
    case 'equation': return <EquationBlock block={block} annotations={annotations} pages={pages} pdfUrl={pdfUrl} documentId={documentId} />;
    case 'list': {const List = block.listOrdered ? 'ol' : 'ul'; return <div {...common} className="area-target"><List>{(block.items || []).map((item, index) => <li key={index}>{block.listContent?.[index]?.length ? <Inline nodes={block.listContent[index]} onReference={onReference} pdfUrl={originalPage} /> : item}</li>)}</List><AreaMarks block={block} annotations={annotations} pages={pages} /></div>;}
    case 'quote': return <blockquote {...common} className="area-target">{block.text}<AreaMarks block={block} annotations={annotations} pages={pages} /></blockquote>;
    case 'heading': return <h3 {...common} className="area-target">{block.content?.length ? <Inline nodes={block.content} onReference={onReference} /> : block.text}<AreaMarks block={block} annotations={annotations} pages={pages} /></h3>;
    case 'code': return block.src ? <figure {...common} className="area-target"><AreaImage block={block} annotations={annotations} pages={pages} alt={block.text || '代码'} /><AreaMarks block={block} annotations={annotations} pages={pages} /></figure> : <pre {...common} className="area-target">{block.text}<AreaMarks block={block} annotations={annotations} pages={pages} /></pre>;
    case 'footnote': return <aside {...common} className="footnote area-target">{block.text}<AreaMarks block={block} annotations={annotations} pages={pages} /></aside>;
    default: return <p {...common} className="area-target">{block.text}<AreaMarks block={block} annotations={annotations} pages={pages} /></p>;
  }
}

function SectionView({section, annotations, pages, onReference, pdfUrl, documentId, arxivHtml}: {section: Section; annotations: Annotation[]; pages: PageGeometry[]; onReference: (id: string) => void; pdfUrl?: string; documentId: string; arxivHtml: boolean}) {
  const Heading = section.level === 1 ? 'h2' : section.level === 2 ? 'h3' : 'h4';
  const prompt = section.presentation === 'prompt' && (!arxivHtml || /^(?:\d+(?:\.\d+)*\.?\s*)?Prompts?$/i.test(section.title.trim()));
  return <section id={section.id} className={section.type === 'abstract' ? 'abstract-section' : prompt ? 'prompt-section' : ''}>{section.blocks.filter(block => block.beforeHeading).map(block => <BlockView key={block.id} block={block} annotations={annotations} pages={pages} onReference={onReference} pdfUrl={pdfUrl} documentId={documentId} prompt={prompt} />)}<Heading>{section.title}</Heading>{section.blocks.filter(block => !block.beforeHeading).map(block => <BlockView key={block.id} block={block} annotations={annotations} pages={pages} onReference={onReference} pdfUrl={pdfUrl} documentId={documentId} prompt={prompt} />)}</section>;
}

export default function DocumentRenderer({document: paper, annotations, onReference}: {document: DocumentModel; annotations: Annotation[]; onReference: (id: string) => void}) {
  const pdfUrl = paper.source && paper.source !== 'arxiv_html' ? `/api/parser/api/documents/${paper.id}/original.pdf` : undefined;
  return <>
    <header className="paper-header"><div className="eyebrow">{paper.arxivId ? `ARXIV · ${paper.arxivId}v${paper.arxivVersion} · ${paper.source === 'arxiv_html' ? '官方 HTML' : 'PDF 原文'}` : `PAPERLIGHT · ${paper.metadata.pageCount || '—'} PAGES`}</div><h1>{paper.metadata.title}</h1><p className="authors">{paper.metadata.authors.join(' · ')}</p>{paper.metadata.venue && <p className="venue">{paper.metadata.venue} {paper.metadata.year || ''}</p>}{paper.fallbackReason && <p className="source-notice">官方 HTML 不完整，已使用固定版本的 PDF。</p>}</header>
    {paper.sections.map(section => <SectionView key={section.id} section={section} annotations={annotations} pages={paper.pages || []} onReference={onReference} pdfUrl={pdfUrl} documentId={paper.id} arxivHtml={paper.source === 'arxiv_html'} />)}
  </>;
}
