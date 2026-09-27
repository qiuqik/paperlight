export function normalizeQuote(value: string): string {
  return value.replace(/\s+/g, ' ').trim();
}

export function contextAt(text: string, offset: number, context: string, side: 'before' | 'after'): boolean {
  if (!context) return true;
  return side === 'before'
    ? text.slice(Math.max(0, offset - context.length), offset) === context
    : text.slice(offset, offset + context.length) === context;
}

function occurrences(text: string, value: string): number[] {
  if (!value) return [];
  const offsets: number[] = [];
  for (let at = text.indexOf(value); at !== -1; at = text.indexOf(value, at + 1)) offsets.push(at);
  return offsets;
}

export function findQuoteOffsets(text: string, quote: string, prefix: string, suffix: string): {start: number; end: number} | null {
  const positions = occurrences(text, quote);
  const contextual = positions.filter(at => contextAt(text, at, prefix, 'before')
    && contextAt(text, at + quote.length, suffix, 'after'));
  const chosen = contextual.length === 1 ? contextual[0] : positions.length === 1 ? positions[0] : null;
  return chosen === null ? null : {start: chosen, end: chosen + quote.length};
}

export function contextOffsets(text: string, context: string, side: 'before' | 'after', near: number): number[] {
  const offsets = occurrences(text, context).map(at => side === 'before' ? at + context.length : at);
  return offsets.sort((a, b) => Math.abs(a - near) - Math.abs(b - near)).slice(0, 20);
}
