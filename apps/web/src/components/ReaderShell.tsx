'use client';
import {useCallback, useEffect, useRef, useState} from 'react';
import {BookOpen, ChevronLeft, ChevronRight, Clock3, FilePlus2, Highlighter, Image, List, Maximize2, Palette, Scan, Settings2, StickyNote, Table2, Underline, X} from 'lucide-react';
import DocumentRenderer from './DocumentRenderer';
import demo from '@/data/demo.json';
import type {Annotation, AreaAnchor, DocumentModel} from '@/lib/document';
import {allBlocks, DOCUMENT_MODEL_VERSION, isTextAnchor, resolveAssetSources} from '@/lib/document';
import {captureAnchor, renderTextHighlights, resolveAnchor} from '@/lib/anchors';
import {deleteAnnotation, fingerprint, getDocument, getProgress, listAnnotations, listDocuments, saveAnnotation, saveDocument, saveProgress, type SavedDocument} from '@/lib/storage';
import {useAnnotationUI, useLayout, usePreferences, type RightPanel, type Tool} from '@/lib/stores';

const SAMPLE = resolveAssetSources(demo as DocumentModel);
const COLORS = ['#f8d86a', '#ef7474', '#74b9e8', '#83cfa7', '#b9a1e6', '#b8bec6'];
const PANELS: Array<{id: RightPanel; label: string}> = [{id: 'references', label: '参考文献'}, {id: 'figures', label: '图片'}, {id: 'tables', label: '表格'}, {id: 'notes', label: '笔记'}];
const TOOLS: Array<{id: Tool; label: string; Icon: typeof Highlighter}> = [
  {id: 'highlight', label: '高亮', Icon: Highlighter}, {id: 'underline', label: '下划线', Icon: Underline},
  {id: 'area', label: '区域选择', Icon: Scan}, {id: 'note', label: '笔记', Icon: StickyNote},
];

type ParserState = {documentId: string; status: string; stage?: string; progress?: number; document?: DocumentModel; error?: string};
type ServerRecord = {documentId: string; title: string; pageCount: number; status: string};
type LibraryRecord = {id: string; name: string; folder: string; size: number};
const PARSE_STAGE_LABELS: Record<string, string> = {
  queued: '等待解析', loading_parser: '准备解析器', extracting_structure: '提取正文结构',
  recognizing_scanned_pages: '识别扫描页面', linking_references: '关联参考文献',
  normalizing_document: '整理图表与公式', ready: '解析完成', failed: '解析失败',
};
function annotationTime(item: Annotation): number {
  const value = item.updatedAt || item.createdAt || 0;
  return value < 1e11 ? value * 1000 : value;
}
const pause = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
async function waitForDocument(state: ParserState, onProgress: (message: string) => void): Promise<ParserState & {document: DocumentModel}> {
  for (let tries = 0; tries < 360 && state.status === 'processing'; tries++) {
    onProgress(`${PARSE_STAGE_LABELS[state.stage || ''] || '正在解析'} · ${Math.round((state.progress || 0) * 100)}%`);
    await pause(2000);
    const response = await fetch(`/api/parser/api/documents/${state.documentId}`);
    if (!response.ok) throw new Error(`状态查询失败 (${response.status})`);
    state = await response.json() as ParserState;
  }
  if (!state.document) throw new Error(state.error || '解析未完成');
  return state as ParserState & {document: DocumentModel};
}

export default function ReaderShell({initialId}: {initialId?: string}) {
  const [paper, setPaper] = useState<DocumentModel>(SAMPLE);
  const [localId, setLocalId] = useState(SAMPLE.id);
  const [serverId, setServerId] = useState<string | undefined>();
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [importStatus, setImportStatus] = useState('');
  const [historySource, setHistorySource] = useState<'local' | 'server'>('local');
  const [localHistory, setLocalHistory] = useState<SavedDocument[]>([]);
  const [serverHistory, setServerHistory] = useState<ServerRecord[]>([]);
  const [serverLibrary, setServerLibrary] = useState<LibraryRecord[]>([]);
  const [cachePdf, setCachePdf] = useState(false);
  const [noteFocusId, setNoteFocusId] = useState<string | null>(null);
  const [openNoteEditors, setOpenNoteEditors] = useState<Set<string>>(() => new Set());
  const [progress, setProgress] = useState(0);
  const [colorOpen, setColorOpen] = useState(false);
  const articleRef = useRef<HTMLElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const areaStart = useRef<{block: HTMLElement; x: number; y: number} | null>(null);
  const noteSyncTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const prefs = usePreferences();
  const layout = useLayout();
  const setLayout = useLayout(state => state.set);
  const annotationUI = useAnnotationUI();

  useEffect(() => {void usePreferences.persist.rehydrate();}, []);
  useEffect(() => {
    if (initialId) return;
    void listAnnotations(SAMPLE.id).then(setAnnotations).catch(() => {});
  }, [initialId]);
  useEffect(() => {
    if (!articleRef.current) return;
    return renderTextHighlights(articleRef.current, annotations);
  }, [paper, annotations]);
  useEffect(() => {
    const root = articleRef.current;
    if (!root) return;
    let cancelled = false;
    let restored = false;
    let frame = 0;
    let lastStored = -1;
    const legacyKey = `paperlight-progress-${localId}`;
    const restore = async () => {
      let saved: number | undefined;
      try {saved = (await getProgress(localId))?.percent;} catch {}
      if (saved === undefined) {
        try {saved = Number(localStorage.getItem(legacyKey)) || 0;} catch {saved = 0;}
        if (!cancelled) void saveProgress({id: localId, percent: saved, updatedAt: Date.now()}).catch(() => {});
      }
      if (cancelled) return;
      saved = Math.max(0, Math.min(100, saved));
      frame = requestAnimationFrame(() => {
        root.scrollTop = (root.scrollHeight - root.clientHeight) * saved / 100;
        setProgress(saved);
        lastStored = saved;
        restored = true;
      });
    };
    const onScroll = () => {
      if (!restored) return;
      const height = root.scrollHeight - root.clientHeight;
      const next = height > 0 ? Math.round(root.scrollTop / height * 100) : 0;
      setProgress(next);
      if (next === lastStored) return;
      lastStored = next;
      const top = root.getBoundingClientRect().top + 24;
      const blockId = Array.from(root.querySelectorAll<HTMLElement>('[data-block-id]')).find(element => element.getBoundingClientRect().bottom > top)?.dataset.blockId;
      void saveProgress({id: localId, percent: next, blockId, updatedAt: Date.now()}).catch(() => {
        try {localStorage.setItem(legacyKey, String(next));} catch {}
      });
    };
    root.addEventListener('scroll', onScroll, {passive: true});
    void restore();
    return () => {cancelled = true; cancelAnimationFrame(frame); root.removeEventListener('scroll', onScroll);};
  }, [paper, localId]);

  const openDocument = useCallback(async (document: DocumentModel, id: string, remote?: string) => {
    const documentKey = document.fingerprint && /^[a-f0-9]{64}$/.test(document.fingerprint) ? document.fingerprint : id;
    let saved: Annotation[] = [];
    try {saved = await listAnnotations(documentKey);} catch {}
    if (documentKey !== id) {
      try {
        const legacy = await listAnnotations(id);
        const merged = new Map(saved.map(item => [item.id, item]));
        for (const item of legacy) {
          const current = merged.get(item.id);
          if (!current || annotationTime(item) > annotationTime(current)) {
            const moved = {...item, documentId: documentKey};
            await saveAnnotation(moved);
            merged.set(item.id, moved);
          }
        }
        saved = [...merged.values()];
        const [currentProgress, legacyProgress] = await Promise.all([getProgress(documentKey), getProgress(id)]);
        if (legacyProgress && (!currentProgress || legacyProgress.updatedAt > currentProgress.updatedAt)) {
          await saveProgress({...legacyProgress, id: documentKey});
        }
      } catch {}
    }
    setProgress(0); setPaper(resolveAssetSources(document, remote)); setLocalId(documentKey); setServerId(remote);
    if (remote) {
      try {
        const response = await fetch(`/api/parser/api/documents/${remote}/annotations`);
        if (response.ok) {
          const remoteAnnotations = (await response.json() as Array<Annotation & {blockId?: string; start?: number; end?: number; quote?: string; mode?: string}>).map(item => {
            if (item.anchor) return item;
            const anchor = {start: {blockId: item.blockId || '', offset: item.start || 0}, end: {blockId: item.blockId || '', offset: item.end || 0}, quote: item.quote || '', prefix: '', suffix: ''};
            return {...item, type: item.note ? 'note' : item.mode === 'underline' ? 'underline' : 'highlight', anchor} as Annotation;
          });
          const merged = new Map(saved.map(item => [item.id, item]));
          for (const item of remoteAnnotations) {
            if (!item.anchor) continue;
            const current = merged.get(item.id);
            const preserveUnsyncedNote = !!current?.note && !item.note && !item.updatedAt;
            if (!current || (!preserveUnsyncedNote && annotationTime(item) > annotationTime(current))) {
              const incoming = {...item, documentId: documentKey};
              await saveAnnotation(incoming);
              merged.set(item.id, incoming);
            }
          }
          saved = [...merged.values()];
        }
      } catch {}
    }
    setAnnotations(saved); setLayout({historyOpen: false});
    try {history.replaceState({}, '', id === SAMPLE.id ? '/' : `/reader/${encodeURIComponent(id)}`);} catch {}
  }, [setLayout]);

  useEffect(() => {
    if (!initialId) return;
    let cancelled = false;
    (async () => {
      try {
        const saved = await getDocument(initialId);
        if (saved && !cancelled) {await openDocument(saved.document, saved.id, saved.serverId); return;}
        const response = await fetch(`/api/parser/api/documents/${encodeURIComponent(initialId)}`);
        if (!response.ok) return;
        const state = await response.json() as ParserState;
        if (state.document && !cancelled) await openDocument(state.document, initialId, initialId);
      } catch {}
    })();
    return () => {cancelled = true;};
  }, [initialId, openDocument]);

  const addAnnotation = useCallback(async (record: Annotation) => {
    setAnnotations(current => [...current, record]);
    try {await saveAnnotation(record);} catch {setImportStatus('批注未能保存到浏览器');}
    if (serverId) {
      try {const response = await fetch(`/api/parser/api/documents/${serverId}/annotations`, {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(record)}); if (!response.ok) setImportStatus('批注仅保存在此浏览器，服务器同步失败');} catch {setImportStatus('批注仅保存在此浏览器，服务器同步失败');}
    }
    if (record.type === 'note') {setLayout({rightPanel: 'notes', rightOpen: true}); setNoteFocusId(record.id);}
  }, [serverId, setLayout]);

  const syncNote = (annotationId: string, note: string, remote: string) => {
    void fetch(`/api/parser/api/documents/${remote}/annotations/${annotationId}`, {method: 'PATCH', headers: {'content-type': 'application/json'}, body: JSON.stringify({note})})
      .then(response => {if (!response.ok) throw new Error('Server note sync failed');})
      .catch(() => setImportStatus('笔记仅保存在此浏览器，服务器同步失败'));
  };
  const updateNote = async (record: Annotation, note: string) => {
    const changed = {...record, note, updatedAt: Date.now()};
    setAnnotations(current => current.map(item => item.id === record.id ? changed : item));
    try {await saveAnnotation(changed);} catch {setImportStatus('笔记未能保存到浏览器');}
    const prior = noteSyncTimers.current.get(record.id);
    if (prior) clearTimeout(prior);
    if (serverId) noteSyncTimers.current.set(record.id, setTimeout(() => {
      syncNote(record.id, note, serverId);
      noteSyncTimers.current.delete(record.id);
    }, 400));
  };
  const removeAnnotation = async (record: Annotation) => {
    setAnnotations(current => current.filter(item => item.id !== record.id));
    await deleteAnnotation(record.id);
    if (serverId) await fetch(`/api/parser/api/documents/${serverId}/annotations/${record.id}`, {method: 'DELETE'}).catch(() => {});
  };
  const onTextSelection = () => {
    if (annotationUI.activeTool === 'none' || annotationUI.activeTool === 'area' || !articleRef.current) return;
    const anchor = captureAnchor(articleRef.current);
    if (!anchor) return;
    const record: Annotation = {id: crypto.randomUUID(), documentId: localId, type: annotationUI.activeTool, color: prefs.activeColor, anchor, note: '', createdAt: Date.now()};
    void addAnnotation(record);
    window.getSelection()?.removeAllRanges();
  };
  const onAreaStart = (event: React.PointerEvent<HTMLElement>) => {
    if (annotationUI.activeTool !== 'area') return;
    const block = (event.target as Element).closest<HTMLElement>('.area-target[data-block-id]');
    if (!block) return;
    const rect = block.getBoundingClientRect();
    areaStart.current = {block, x: (event.clientX - rect.left) / rect.width, y: (event.clientY - rect.top) / rect.height};
    block.setPointerCapture(event.pointerId);
  };
  const onAreaEnd = (event: React.PointerEvent<HTMLElement>) => {
    const start = areaStart.current;
    areaStart.current = null;
    if (!start) return;
    const rect = start.block.getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const y = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
    const bbox = {x: Math.min(start.x, x), y: Math.min(start.y, y), width: Math.abs(x - start.x), height: Math.abs(y - start.y)};
    if (bbox.width < 0.015 || bbox.height < 0.015) return;
    const blockId = start.block.dataset.blockId!;
    const block = allBlocks(paper).find(item => item.id === blockId);
    const page = Number(start.block.dataset.page) || 1;
    const pageSize = paper.pages?.find(item => item.number === page);
    const pageBox = block?.bbox && pageSize ? {
      x: (block.bbox.x + bbox.x * block.bbox.width) / pageSize.width,
      y: (block.bbox.y + bbox.y * block.bbox.height) / pageSize.height,
      width: bbox.width * block.bbox.width / pageSize.width,
      height: bbox.height * block.bbox.height / pageSize.height,
    } : bbox;
    const anchor: AreaAnchor = {blockId, page, bbox: pageBox, space: block?.bbox && pageSize ? 'page' : 'block'};
    void addAnnotation({id: crypto.randomUUID(), documentId: localId, type: 'area', color: prefs.activeColor, anchor, createdAt: Date.now()});
  };

  const importPdf = async (file?: File) => {
    if (!file) return;
    setImportStatus('正在检查本地记录…');
    try {
      const hash = await fingerprint(file);
      const cached = await getDocument(hash);
      if (cached?.document.modelVersion === DOCUMENT_MODEL_VERSION) {await openDocument(cached.document, hash, cached.serverId); setImportStatus(''); return;}
      const body = new FormData(); body.set('file', file);
      setImportStatus('正在上传 PDF…');
      const response = await fetch('/api/parser/api/documents', {method: 'POST', body});
      if (!response.ok) throw new Error(`上传失败 (${response.status})`);
      const state = await waitForDocument(await response.json() as ParserState, setImportStatus);
      await saveDocument({id: hash, document: state.document, filename: file.name, savedAt: Date.now(), pdf: cachePdf ? file : undefined, serverId: state.documentId});
      await openDocument(state.document, hash, state.documentId);
      setImportStatus('');
    } catch (error) {setImportStatus(error instanceof Error ? error.message : '导入失败');}
    if (fileRef.current) fileRef.current.value = '';
  };

  const openHistory = async (source: 'local' | 'server') => {
    setHistorySource(source); layout.set({historyOpen: true});
    if (source === 'local') {try {setLocalHistory((await listDocuments()).sort((a, b) => b.savedAt - a.savedAt));} catch {setLocalHistory([]);}}
    else {
      const [documents, library] = await Promise.allSettled([fetch('/api/parser/api/documents'), fetch('/api/parser/api/library')]);
      setServerHistory(documents.status === 'fulfilled' && documents.value.ok ? await documents.value.json() : []);
      setServerLibrary(library.status === 'fulfilled' && library.value.ok ? await library.value.json() : []);
    }
  };
  const openServer = async (id: string) => {
    const response = await fetch(`/api/parser/api/documents/${id}`);
    if (!response.ok) throw new Error('服务器文档无法打开');
    const state = await response.json() as ParserState;
    if (!state.document) throw new Error('文档尚未完成解析');
    await openDocument(state.document, id, id);
  };
  const openLibrary = async (id: string, filename: string) => {
    setImportStatus('正在打开服务器文件…');
    try {
      const response = await fetch(`/api/parser/api/library/${id}/open`, {method: 'POST'});
      if (!response.ok) throw new Error(`服务器文件无法打开 (${response.status})`);
      const state = await waitForDocument(await response.json() as ParserState, setImportStatus);
      const localKey = state.document.fingerprint || state.documentId;
      await saveDocument({id: localKey, document: state.document, filename, savedAt: Date.now(), serverId: state.documentId});
      await openDocument(state.document, localKey, state.documentId);
      setImportStatus('');
    } catch (error) {setImportStatus(error instanceof Error ? error.message : '打开服务器文件失败');}
  };
  const jumpToAnnotation = (record: Annotation) => {
    const root = articleRef.current;
    if (!root) return;
    if (isTextAnchor(record.anchor)) {
      const range = resolveAnchor(root, record.anchor);
      range?.startContainer.parentElement?.scrollIntoView({behavior: 'smooth', block: 'center'});
    } else root.querySelector(`[data-block-id="${record.anchor.blockId}"]`)?.scrollIntoView({behavior: 'smooth', block: 'center'});
  };

  const toolbar = <div className="annotation-tools" role="toolbar" aria-label="标注工具">
    {TOOLS.map(({id, label, Icon}) => <button key={id} type="button" className={annotationUI.activeTool === id ? 'active' : ''} aria-label={label} title={label} aria-pressed={annotationUI.activeTool === id} onClick={() => annotationUI.setTool(annotationUI.activeTool === id ? 'none' : id)}><Icon size={18} /></button>)}
    <div className="color-control"><button type="button" aria-label="选择标注颜色" title="标注颜色" onClick={() => setColorOpen(!colorOpen)}><span className="color-dot" style={{background: prefs.activeColor}} /><Palette size={14} /></button>{colorOpen && <div className="color-popover">{COLORS.map(color => <button key={color} type="button" title={color} aria-label={`颜色 ${color}`} style={{background: color}} onClick={() => {prefs.set({activeColor: color}); setColorOpen(false);}} />)}<input type="color" aria-label="自定义颜色" value={prefs.activeColor} onChange={event => prefs.set({activeColor: event.target.value})} /></div>}</div>
  </div>;

  return <div className={`reader-app theme-${prefs.theme} dock-${prefs.toolbarDock} ${layout.focus ? 'focus-mode' : ''} ${annotationUI.activeTool === 'area' ? 'area-mode' : ''}`} style={{'--reader-font': prefs.fontFamily, '--reader-size': `${prefs.fontSize}px`, '--reader-leading': prefs.lineHeight, '--reader-width': `${prefs.contentWidth}px`, '--custom-app': prefs.customApp, '--custom-paper': prefs.customPaper, '--custom-text': prefs.customText, '--custom-accent': prefs.customAccent} as React.CSSProperties}>
    <header className="reader-topbar"><div className="brand"><BookOpen size={20} /><strong>Paperlight</strong></div><span className="top-title" title={paper.metadata.title}>{paper.metadata.title}</span>{prefs.toolbarDock === 'top' && toolbar}<div className="top-actions"><span className="read-time"><Clock3 size={15} /> {paper.metadata.readMinutes || '—'} min</span><button title="专注模式" aria-label="专注模式" aria-pressed={layout.focus} onClick={() => layout.set({focus: !layout.focus, leftOpen: layout.focus, rightOpen: layout.focus})}><Maximize2 size={18} /></button><button title="设置" aria-label="设置" onClick={() => layout.set({settingsOpen: true})}><Settings2 size={18} /></button><button title="历史记录" aria-label="历史记录" onClick={() => void openHistory('local')}><Clock3 size={18} /></button><button className="import-button" onClick={() => fileRef.current?.click()}><FilePlus2 size={16} /> 导入</button></div></header>
    {importStatus && <div className="status-banner" role="status">{importStatus}<button aria-label="关闭提示" onClick={() => setImportStatus('')}><X size={14} /></button></div>}
    <div className="reader-grid"><aside className={`left-panel ${layout.leftOpen ? 'open' : ''}`}><div className="panel-heading"><span>目录</span><button title="收起目录" onClick={() => layout.set({leftOpen: false})}><ChevronLeft size={16} /></button></div><nav>{paper.sections.map(section => <button key={section.id} className={`toc-item level-${section.level}`} onClick={() => document.getElementById(section.id)?.scrollIntoView({behavior: 'smooth'})}>{section.title}</button>)}</nav></aside>
      {(!layout.leftOpen || layout.focus) && <div className="side-rail"><button title="目录" aria-label="目录" aria-pressed={layout.leftOpen} onClick={() => layout.set({leftOpen: !layout.leftOpen})}><List size={19} /></button></div>}
      <main className="reading-column">{prefs.toolbarDock !== 'top' && <div className={`docked-tools docked-${prefs.toolbarDock}`}>{toolbar}</div>}<article ref={articleRef} className="reader-scroll" onMouseUp={onTextSelection} onPointerDown={onAreaStart} onPointerUp={onAreaEnd}><div className="paper-content"><DocumentRenderer document={paper} annotations={annotations} /></div></article><div className="reading-progress"><span style={{width: `${progress}%`}} /></div></main>
      {(!layout.rightOpen || layout.focus) && <div className="side-rail right-side">{PANELS.map(panel => {const Icon = panel.id === 'references' ? BookOpen : panel.id === 'figures' ? Image : panel.id === 'tables' ? Table2 : StickyNote; return <button key={panel.id} title={panel.label} aria-label={panel.label} aria-pressed={layout.rightOpen && layout.rightPanel === panel.id} onClick={() => layout.set({rightOpen: !(layout.rightOpen && layout.rightPanel === panel.id), rightPanel: panel.id})}><Icon size={18} /></button>;})}</div>}
      <aside className={`right-panel ${layout.rightOpen ? 'open' : ''}`}><div className="panel-heading"><span>{PANELS.find(item => item.id === layout.rightPanel)?.label}</span><button title="收起面板" onClick={() => layout.set({rightOpen: false})}><ChevronRight size={16} /></button></div><div className="panel-tabs">{PANELS.map(panel => <button key={panel.id} className={layout.rightPanel === panel.id ? 'active' : ''} onClick={() => layout.set({rightPanel: panel.id})}>{panel.label}</button>)}</div><div className="panel-list">
        {layout.rightPanel === 'references' && (paper.references.length ? paper.references.map(ref => <div className="reference-card" id={`ref-${ref.id}`} key={ref.id}><small>[{ref.number}] {ref.authors}</small><strong>{ref.title}</strong><span>{ref.venue} {ref.year}</span>{ref.preview && <p>{ref.preview}</p>}</div>) : <p className="empty-panel">暂无参考文献</p>)}
        {layout.rightPanel === 'figures' && (paper.figures.length ? paper.figures.map(item => <button className="asset-card" key={item.id} onClick={() => document.getElementById(item.id)?.scrollIntoView({behavior: 'smooth', block: 'center'})}>{item.src && <img src={item.src} alt="" />}<strong>{item.label || `Figure ${item.number}`}</strong><span>{item.caption}</span></button>) : <p className="empty-panel">暂无图片</p>)}
        {layout.rightPanel === 'tables' && (paper.tables.length ? paper.tables.map(item => <button className="asset-card" key={item.id} onClick={() => document.getElementById(item.id)?.scrollIntoView({behavior: 'smooth', block: 'center'})}><strong>{item.label || `Table ${item.number}`}</strong><span>{item.caption}</span></button>) : <p className="empty-panel">暂无表格</p>)}
        {layout.rightPanel === 'notes' && (annotations.length ? annotations.map(item => <div className="note-card" key={item.id} style={{borderColor: item.color, background: `${item.color}18`}}><button className="note-quote" onClick={() => jumpToAnnotation(item)}>{isTextAnchor(item.anchor) ? `“${item.anchor.quote}”` : `第 ${item.anchor.page} 页区域`}</button>{(item.type === 'note' || !!item.note || openNoteEditors.has(item.id)) && <textarea aria-label="笔记内容" placeholder="输入笔记…" value={item.note || ''} autoFocus={noteFocusId === item.id} onFocus={() => setNoteFocusId(null)} onChange={event => void updateNote(item, event.target.value)} onBlur={event => {const timer = noteSyncTimers.current.get(item.id); if (timer && serverId) {clearTimeout(timer); noteSyncTimers.current.delete(item.id); syncNote(item.id, event.currentTarget.value, serverId);}}} />}<div className="note-footer"><span>{item.type}</span>{item.type !== 'note' && !item.note && !openNoteEditors.has(item.id) && <button onClick={() => {setOpenNoteEditors(current => new Set(current).add(item.id)); setNoteFocusId(item.id);}}>添加笔记</button>}<button onClick={() => void removeAnnotation(item)}>删除</button></div></div>) : <p className="empty-panel">选择文字后，笔记和标注会出现在这里。</p>)}
      </div></aside></div>
    {layout.settingsOpen && <div className="drawer-backdrop" onClick={() => layout.set({settingsOpen: false})}><aside className="settings-drawer" onClick={event => event.stopPropagation()}><div className="drawer-title"><h2>阅读设置</h2><button aria-label="关闭设置" onClick={() => layout.set({settingsOpen: false})}><X size={20} /></button></div><label>字体<select value={prefs.fontFamily} onChange={event => prefs.set({fontFamily: event.target.value})}><option value="Georgia, serif">Georgia</option><option value="Arial, sans-serif">Arial</option><option value="'Times New Roman', serif">Times New Roman</option></select></label><label>字号 <b>{prefs.fontSize}px</b><input type="range" min="14" max="26" value={prefs.fontSize} onChange={event => prefs.set({fontSize: Number(event.target.value)})} /></label><label>行距 <b>{prefs.lineHeight.toFixed(1)}</b><input type="range" min="1.2" max="2.2" step="0.1" value={prefs.lineHeight} onChange={event => prefs.set({lineHeight: Number(event.target.value)})} /></label><label>阅读宽度 <b>{prefs.contentWidth}px</b><input type="range" min="600" max="1200" step="20" value={prefs.contentWidth} onChange={event => prefs.set({contentWidth: Number(event.target.value)})} /></label><label>主题<select value={prefs.theme} onChange={event => prefs.set({theme: event.target.value as typeof prefs.theme})}><option value="paper">纸张</option><option value="warm">暖色</option><option value="dark">深色</option><option value="custom">自定义</option></select></label>{prefs.theme === 'custom' && <div className="custom-theme-colors">{([['customApp', '界面背景'], ['customPaper', '纸张背景'], ['customText', '正文文字'], ['customAccent', '强调色']] as const).map(([key, label]) => <label key={key}>{label}<input type="color" value={prefs[key]} onChange={event => prefs.set({[key]: event.target.value})} /></label>)}</div>}<label>工具栏位置<select value={prefs.toolbarDock} onChange={event => prefs.set({toolbarDock: event.target.value as typeof prefs.toolbarDock})}><option value="top">顶部</option><option value="bottom">底部</option><option value="left">左侧</option><option value="right">右侧</option></select></label><label className="check-row"><input type="checkbox" checked={cachePdf} onChange={event => setCachePdf(event.target.checked)} />新导入时在此浏览器缓存 PDF</label></aside></div>}
    {layout.historyOpen && <div className="dialog-backdrop" onClick={() => layout.set({historyOpen: false})}><section className="history-dialog" onClick={event => event.stopPropagation()}><div className="drawer-title"><h2>历史记录</h2><button aria-label="关闭历史记录" onClick={() => layout.set({historyOpen: false})}><X size={20} /></button></div><div className="history-tabs"><button className={historySource === 'local' ? 'active' : ''} onClick={() => void openHistory('local')}>此浏览器</button><button className={historySource === 'server' ? 'active' : ''} onClick={() => void openHistory('server')}>服务器</button></div><div className="history-list">{historySource === 'local' ? (localHistory.length ? localHistory.map(item => <button key={item.id} onClick={() => void openDocument(item.document, item.id, item.serverId)}><strong>{item.document.metadata.title}</strong><small>{item.filename} · {item.document.metadata.pageCount} 页</small></button>) : <p className="empty-panel">此浏览器还没有导入的论文。</p>) : <>
      <h3>已解析文档</h3>{serverHistory.length ? serverHistory.map(item => <button key={item.documentId} onClick={() => void openServer(item.documentId).catch(error => setImportStatus(error.message))}><strong>{item.title}</strong><small>{item.pageCount} 页 · {item.status}</small></button>) : <p className="empty-panel">暂无已解析文档。</p>}
      <h3>服务器文件库</h3>{serverLibrary.length ? serverLibrary.map(item => <button key={item.id} onClick={() => void openLibrary(item.id, item.name)}><strong>{item.name}</strong><small>{item.folder === '.' ? '文件库根目录' : item.folder} · {(item.size / 1024 / 1024).toFixed(1)} MB</small></button>) : <p className="empty-panel">文件库为空，或解析服务未启动。</p>}
    </>}</div></section></div>}
    <input ref={fileRef} type="file" accept="application/pdf,.pdf" hidden onChange={event => void importPdf(event.target.files?.[0])} />
  </div>;
}
