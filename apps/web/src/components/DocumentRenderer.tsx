'use client';
import type {Annotation, Block, DocumentModel, InlineNode, PageGeometry, Section} from '@/lib/document';
import {isTextAnchor} from '@/lib/document';

function Inline({nodes, onReference}: {nodes: InlineNode[]; onReference: (id: string) => void}) {
  return <>{nodes.map((node, index) => {
    const label = node.display || node.text || '';
    if (node.type === 'citation') return <button className="inline-link" key={index} onClick={() => node.referenceIds?.[0] && onReference(node.referenceIds[0])}>{label}</button>;
    if (node.type === 'figureLink' || node.type === 'tableLink') return <button className="inline-link" key={index} onClick={() => document.getElementById(node.figureId || node.tableId || '')?.scrollIntoView({behavior: 'smooth'})}>{label}</button>;
    if (node.type === 'link' && /^https?:\/\//.test(node.href || '')) return <a key={index} href={node.href} target="_blank" rel="noreferrer">{label}</a>;
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

function AreaMarks({block, annotations, pages}: {block: Block; annotations: Annotation[]; pages: PageGeometry[]}) {
  return <>{annotations.filter(annotation => annotation.type === 'area' && !isTextAnchor(annotation.anchor) && annotation.anchor.blockId === block.id).map(annotation => {
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
function BlockView({block, annotations, pages, onReference}: {block: Block; annotations: Annotation[]; pages: PageGeometry[]; onReference: (id: string) => void}) {
  const common = {'data-block-id': block.id, 'data-page': block.page};
  switch (block.type) {
    case 'paragraph': return <p {...common} className="area-target">{block.src ? <><img className="formula-crop" src={block.src} alt="原 PDF 段落" /><span className="sr-only">{block.text}</span></> : block.content?.length ? <Inline nodes={block.content} onReference={onReference} /> : <PlainText text={block.text || ''} onReference={onReference} />}<AreaMarks block={block} annotations={annotations} pages={pages} /></p>;
    case 'figure': return <figure {...common} id={block.id} className="paper-figure area-target" onDragStart={event => event.preventDefault()}>{block.src && <img src={block.src} alt={block.caption || block.label || '论文插图'} draggable={false} />}<AreaMarks block={block} annotations={annotations} pages={pages} /><figcaption><strong>{block.label || `Figure ${block.number}`}</strong> {block.caption}</figcaption></figure>;
    case 'table': return <figure {...common} id={block.id} className="paper-figure area-target" onDragStart={event => event.preventDefault()}><figcaption><strong>{block.label || `Table ${block.number}`}</strong> {block.caption}</figcaption>{block.src ? <img src={block.src} alt={block.caption || '论文表格'} draggable={false} /> : <div className="table-scroll"><table><thead><tr>{(block.headers || []).map((cell, index) => <th key={index}>{typeof cell === 'string' ? cell : cell.text}</th>)}</tr></thead><tbody>{(block.rows || []).map((row, index) => <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex}>{typeof cell === 'string' ? cell : cell.text}</td>)}</tr>)}</tbody></table></div>}<AreaMarks block={block} annotations={annotations} pages={pages} /></figure>;
    case 'equation': return <figure {...common} className={`paper-equation area-target${block.number != null ? ' numbered-equation' : ''}`} onDragStart={event => event.preventDefault()}>{block.src ? <img src={block.src} alt={block.text || '原 PDF 公式'} draggable={false} /> : <code>{block.text}</code>}{block.number != null && <span className="equation-number" aria-label={`公式编号 ${block.number}`}>({block.number})</span>}<AreaMarks block={block} annotations={annotations} pages={pages} /></figure>;
    case 'list': return <div {...common} className="area-target"><ul>{(block.items || []).map((item, index) => <li key={index}>{item}</li>)}</ul><AreaMarks block={block} annotations={annotations} pages={pages} /></div>;
    case 'quote': return <blockquote {...common} className="area-target">{block.text}<AreaMarks block={block} annotations={annotations} pages={pages} /></blockquote>;
    case 'code': return block.src ? <figure {...common} className="area-target"><img src={block.src} alt={block.text || '代码'} /><AreaMarks block={block} annotations={annotations} pages={pages} /></figure> : <pre {...common} className="area-target">{block.text}<AreaMarks block={block} annotations={annotations} pages={pages} /></pre>;
    case 'footnote': return <aside {...common} className="footnote area-target">{block.text}<AreaMarks block={block} annotations={annotations} pages={pages} /></aside>;
    default: return <p {...common} className="area-target">{block.text}<AreaMarks block={block} annotations={annotations} pages={pages} /></p>;
  }
}

function SectionView({section, annotations, pages, onReference}: {section: Section; annotations: Annotation[]; pages: PageGeometry[]; onReference: (id: string) => void}) {
  const Heading = section.level === 1 ? 'h2' : section.level === 2 ? 'h3' : 'h4';
  return <section id={section.id} className={section.type === 'abstract' ? 'abstract-section' : ''}>{section.blocks.filter(block => block.beforeHeading).map(block => <BlockView key={block.id} block={block} annotations={annotations} pages={pages} onReference={onReference} />)}<Heading>{section.title}</Heading>{section.blocks.filter(block => !block.beforeHeading).map(block => <BlockView key={block.id} block={block} annotations={annotations} pages={pages} onReference={onReference} />)}</section>;
}

export default function DocumentRenderer({document: paper, annotations, onReference}: {document: DocumentModel; annotations: Annotation[]; onReference: (id: string) => void}) {
  return <>
    <header className="paper-header"><div className="eyebrow">PAPERLIGHT · {paper.metadata.pageCount || '—'} PAGES</div><h1>{paper.metadata.title}</h1><p className="authors">{paper.metadata.authors.join(' · ')}</p>{paper.metadata.venue && <p className="venue">{paper.metadata.venue} {paper.metadata.year || ''}</p>}</header>
    {paper.sections.map(section => <SectionView key={section.id} section={section} annotations={annotations} pages={paper.pages || []} onReference={onReference} />)}
  </>;
}
