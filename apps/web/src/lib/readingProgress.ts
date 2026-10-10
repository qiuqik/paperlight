type Position = {percent: number; blockId?: string; blockOffset: number; recordedAt: number};
export function mergeLocalReadingProgress<T extends {documentId: string; progress: number; progressUpdatedAt?: number}>(records: T[], userId: string): T[] {
  return records.map(record => {
    try {
      const saved = JSON.parse(localStorage.getItem('paperlight:position:' + userId + ':' + record.documentId) || 'null') as Position | null;
      if (saved && Number.isFinite(saved.percent) && saved.percent >= 0 && saved.percent <= 100 && Number.isFinite(saved.recordedAt) && saved.recordedAt > (record.progressUpdatedAt || 0) * 1000) {
        return {...record, progress: saved.percent, progressUpdatedAt: saved.recordedAt / 1000};
      }
    } catch {}
    return record;
  });
}
export function trackReadingPosition(root: HTMLElement, userId: string, documentId: string, onProgress: (value: number) => void, onError: () => void) {
  const key = 'paperlight:position:' + userId + ':' + documentId;
  const url = '/api/parser/api/documents/' + documentId + '/progress';
  let disposed = false, restored = false, correcting = false;
  let position: Position | undefined, pending: Position | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let chain = Promise.resolve();
  const blocks = () => Array.from(root.querySelectorAll<HTMLElement>('[data-block-id]')).filter(el => el.getBoundingClientRect().height > 0);
  const capture = (): Position => {
    const top = root.getBoundingClientRect().top + 24;
    const block = root.scrollTop > 0 ? blocks().find(el => el.getBoundingClientRect().bottom > top) : undefined;
    const bounds = block?.getBoundingClientRect();
    return {percent: Math.max(0, Math.min(100, root.scrollTop / Math.max(1, root.scrollHeight - root.clientHeight) * 100)),
      blockId: block?.dataset.blockId, blockOffset: bounds ? (top - bounds.top) / bounds.height : 0, recordedAt: Date.now()};
  };
  const apply = () => {
    if (!position || disposed) return;
    const block = blocks().find(el => el.dataset.blockId === position!.blockId);
    correcting = true;
    const behavior = root.style.scrollBehavior;
    root.style.scrollBehavior = 'auto';
    if (block) {const b = block.getBoundingClientRect(); root.scrollTop += b.top + b.height * position.blockOffset - root.getBoundingClientRect().top - 24;}
    else root.scrollTop = Math.max(0, root.scrollHeight - root.clientHeight) * position.percent / 100;
    root.style.scrollBehavior = behavior;
    const percent = capture().percent;
    if (Math.abs(percent - position.percent) >= 0.001) {
      position = {...position, percent, recordedAt: Date.now()};
      pending = position;
      try {localStorage.setItem(key, JSON.stringify(position));} catch {}
      clearTimeout(timer); timer = setTimeout(() => flush(), 400);
    }
    onProgress(Math.round(percent));
    correcting = false;
  };
  const write = async (value: Position) => {
    try {const response = await fetch(url, {method: 'PUT', headers: {'content-type': 'application/json'}, body: JSON.stringify(value), keepalive: true}); if (!response.ok) throw new Error();}
    catch {if (!disposed) onError();}
  };
  const flush = (leaving = false) => {
    clearTimeout(timer);
    if (!pending) return;
    const value = pending; pending = undefined;
    if (leaving) void write(value); else chain = chain.then(() => write(value));
  };
  const scroll = () => {
    if (!restored || correcting || !root.isConnected || root.clientHeight <= 0) return;
    const next = capture();
    if (position && next.blockId === position.blockId && Math.abs(next.blockOffset-position.blockOffset) < 0.00001 && Math.abs(next.percent-position.percent) < 0.001) return;
    position = next; pending = next;
    try {localStorage.setItem(key, JSON.stringify(next));} catch {}
    onProgress(Math.round(next.percent));
    clearTimeout(timer); timer = setTimeout(() => flush(), 400);
  };
  const leave = () => {scroll(); flush(true);};
  const visibility = () => {if (document.hidden) leave();};
  root.addEventListener('scroll', scroll, {passive: true});
  window.addEventListener('pagehide', leave);
  document.addEventListener('visibilitychange', visibility);
  const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(() => {if (restored) apply();});
  observer?.observe(root);
  const content = root.querySelector('.paper-content'); if (content) observer?.observe(content);
  const ready = (async () => {
    let local: Position | undefined;
    try {local = JSON.parse(localStorage.getItem(key) || 'null') || undefined;} catch {}
    let remote: Position | undefined;
    try {const response = await fetch(url, {cache: 'no-store'}); if (response.ok) {const entry = await response.json(); if (typeof entry.scroll_progress === 'number') remote = {percent: entry.scroll_progress, blockId: entry.block_id || undefined, blockOffset: entry.block_offset || 0, recordedAt: (entry.updated_at || 0)*1000};}} catch {}
    if (disposed) return;
    position = local && (!remote || local.recordedAt > remote.recordedAt) ? local : remote;
    apply(); restored = true;
    // Re-send a locally saved position when reload interrupted its server write.
    if (position === local && local) {pending = local; flush();}
    if (!position) position = capture();
    void document.fonts?.ready.then(() => {if (!disposed) apply();});
  })();
  return {ready, layoutChanged: apply, dispose: () => {leave(); disposed = true; clearTimeout(timer); observer?.disconnect(); root.removeEventListener('scroll', scroll); window.removeEventListener('pagehide', leave); document.removeEventListener('visibilitychange', visibility);}};
}
