import type {Block, TextAnchor} from './document';

// Ignore only layout differences; never silently match a different word or symbol.
export function normalizedText(text: string) {
  let value = ''; const offsets: number[] = [];
  for (let i = 0; i < text.length; i++) {
    const character = text[i].normalize('NFKC');
    for (const part of character) if (!/[\s\u00ad]/.test(part)) {value += part; offsets.push(i);}
  }
  return {value, offsets};
}
export function locateText(text: string, quote: string): {start: number; end: number} | null {
  const source = normalizedText(text); const needle = normalizedText(quote).value;
  if (!needle) return null;
  const at = source.value.indexOf(needle);
  if (at < 0 || source.value.indexOf(needle, at + 1) >= 0) return null;
  return {start: source.offsets[at], end: source.offsets[at + needle.length - 1] + 1};
}
export function matchPdfSelection(blocks: Block[], page: number, quote: string): TextAnchor | null {
  const matches = blocks.filter(block => block.page === page).flatMap(block => {
    const text = block.type === 'list' ? (block.items || []).join('') : block.text || block.caption || '';
    const match = locateText(text, quote);
    return match ? [{block, text, match}] : [];
  });
  if (matches.length !== 1) return null;
  const {block, text, match} = matches[0];
  return {start: {blockId: block.id, offset: match.start}, end: {blockId: block.id, offset: match.end}, quote: text.slice(match.start, match.end), prefix: text.slice(Math.max(0, match.start - 32), match.start), suffix: text.slice(match.end, match.end + 32)};
}
