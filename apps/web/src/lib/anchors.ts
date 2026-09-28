import type {Annotation, TextAnchor} from './document';
import {isTextAnchor} from './document';
import {contextAt, contextOffsets, findQuoteOffsets, normalizeQuote} from './quoteAnchor';

function blockFor(node: Node | null, root: HTMLElement): HTMLElement | null {
  const element = node instanceof Element ? node : node?.parentElement;
  const block = element?.closest<HTMLElement>('[data-block-id]') || null;
  return block && root.contains(block) ? block : null;
}

function textOffset(block: HTMLElement, node: Node, offset: number): number {
  const range = document.createRange();
  range.selectNodeContents(block);
  range.setEnd(node, offset);
  return range.toString().length;
}

export function captureAnchor(root: HTMLElement): TextAnchor | null {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
  const range = selection.getRangeAt(0);
  const startBlock = blockFor(range.startContainer, root);
  const endBlock = blockFor(range.endContainer, root);
  if (!startBlock || !endBlock) return null;
  const quote = selection.toString().trim();
  if (!quote) return null;
  const start = textOffset(startBlock, range.startContainer, range.startOffset);
  const end = textOffset(endBlock, range.endContainer, range.endOffset);
  return {
    start: {blockId: startBlock.dataset.blockId!, offset: start},
    end: {blockId: endBlock.dataset.blockId!, offset: end},
    quote, prefix: (startBlock.textContent || '').slice(Math.max(0, start - 32), start),
    suffix: (endBlock.textContent || '').slice(end, end + 32),
  };
}

function pointAtOffset(block: HTMLElement, offset: number): {node: Node; offset: number} | null {
  const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node) {
    const length = node.textContent?.length || 0;
    if (offset <= length) return {node, offset};
    offset -= length;
    node = walker.nextNode();
  }
  return null;
}

function rangeAt(startBlock: HTMLElement, startOffset: number, endBlock: HTMLElement, endOffset: number): Range | null {
  const start = pointAtOffset(startBlock, startOffset);
  const end = pointAtOffset(endBlock, endOffset);
  if (!start || !end) return null;
  const range = document.createRange();
  try {range.setStart(start.node, start.offset); range.setEnd(end.node, end.offset);} catch {return null;}
  return range;
}

function sameQuote(range: Range, quote: string): boolean {
  return normalizeQuote(range.toString()) === normalizeQuote(quote);
}

export function resolveAnchor(root: HTMLElement, anchor: TextAnchor): Range | null {
  const startBlock = Array.from(root.querySelectorAll<HTMLElement>('[data-block-id]')).find(element => element.dataset.blockId === anchor.start.blockId);
  const endBlock = Array.from(root.querySelectorAll<HTMLElement>('[data-block-id]')).find(element => element.dataset.blockId === anchor.end.blockId);
  if (!startBlock || !endBlock) return null;
  const startText = startBlock.textContent || '';
  const endText = endBlock.textContent || '';
  const original = rangeAt(startBlock, anchor.start.offset, endBlock, anchor.end.offset);
  if (original && (!anchor.quote || sameQuote(original, anchor.quote))
    && contextAt(startText, anchor.start.offset, anchor.prefix, 'before')
    && contextAt(endText, anchor.end.offset, anchor.suffix, 'after')) return original;
  if (!anchor.quote) return null;

  if (startBlock === endBlock) {
    const match = findQuoteOffsets(startText, anchor.quote, anchor.prefix, anchor.suffix);
    if (match) {
      const recovered = rangeAt(startBlock, match.start, endBlock, match.end);
      if (recovered && sameQuote(recovered, anchor.quote)) return recovered;
    }
  }

  const starts = anchor.prefix ? contextOffsets(startText, anchor.prefix, 'before', anchor.start.offset) : [0];
  const ends = anchor.suffix ? contextOffsets(endText, anchor.suffix, 'after', anchor.end.offset) : [endText.length];
  const recovered: Range[] = [];
  for (const start of starts) for (const end of ends) {
    const range = rangeAt(startBlock, start, endBlock, end);
    if (range && sameQuote(range, anchor.quote)) recovered.push(range);
  }
  return recovered.length === 1 ? recovered[0] : null;
}

type HighlightRegistry = {set: (key: string, value: unknown) => void; delete: (key: string) => void};
export function renderTextHighlights(root: HTMLElement, annotations: Annotation[]): () => void {
  const registry = (CSS as unknown as {highlights?: HighlightRegistry}).highlights;
  const HighlightClass = (window as unknown as {Highlight?: new (range: Range) => unknown}).Highlight;
  if (!registry || !HighlightClass) return () => {};
  const style = document.createElement('style');
  const keys: string[] = [];
  for (const annotation of annotations) {
    if (!isTextAnchor(annotation.anchor)) continue;
    const range = resolveAnchor(root, annotation.anchor);
    if (!range) continue;
    const key = `paperlight-${annotation.id.replace(/[^a-zA-Z0-9-]/g, '')}`;
    const color = /^#[0-9a-fA-F]{6}$/.test(annotation.color) ? annotation.color : '#f8d86a';
    registry.set(key, new HighlightClass(range));
    style.textContent += (annotation.style || annotation.type) === 'underline'
      ? `::highlight(${key}){text-decoration:underline;text-decoration-color:${color};text-decoration-thickness:2px;background:transparent}`
      : `::highlight(${key}){background:${color}80;color:inherit}`;
    keys.push(key);
  }
  document.head.append(style);
  return () => {keys.forEach(key => registry.delete(key)); style.remove();};
}
