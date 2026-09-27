import type {Annotation, TextAnchor} from './document';
import {isTextAnchor} from './document';

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

export function resolveAnchor(root: HTMLElement, anchor: TextAnchor): Range | null {
  const startBlock = Array.from(root.querySelectorAll<HTMLElement>('[data-block-id]')).find(element => element.dataset.blockId === anchor.start.blockId);
  const endBlock = Array.from(root.querySelectorAll<HTMLElement>('[data-block-id]')).find(element => element.dataset.blockId === anchor.end.blockId);
  if (!startBlock || !endBlock) return null;
  const start = pointAtOffset(startBlock, anchor.start.offset);
  const end = pointAtOffset(endBlock, anchor.end.offset);
  if (!start || !end) return null;
  const range = document.createRange();
  try {range.setStart(start.node, start.offset); range.setEnd(end.node, end.offset);} catch {return null;}
  return range;
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
    style.textContent += annotation.type === 'underline'
      ? `::highlight(${key}){text-decoration:underline;text-decoration-color:${color};text-decoration-thickness:2px;background:transparent}`
      : `::highlight(${key}){background:${color}80;color:inherit}`;
    keys.push(key);
  }
  document.head.append(style);
  return () => {keys.forEach(key => registry.delete(key)); style.remove();};
}
