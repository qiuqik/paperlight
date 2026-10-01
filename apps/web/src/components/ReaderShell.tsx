'use client';
import Link from 'next/link';
import {useCallback, useEffect, useLayoutEffect, useRef, useState} from 'react';
import {ArrowLeft, FileText, BookOpen, ChevronLeft, ChevronRight, Clock3, Highlighter, Image, List, Maximize2, Minus, Plus, Scan, Settings2, StickyNote, Table2, Underline, X} from 'lucide-react';
import DocumentRenderer from './DocumentRenderer';
import PdfPane from './PdfPane';
import PaneDivider from './PaneDivider';
import ConfirmDialog from './ConfirmDialog';
import AccountMenu from './AccountMenu';
import type {Account} from './AppShell';
import demo from '@/data/demo.json';
import type {Annotation, AreaAnchor, DocumentModel, Reference} from '@/lib/document';
import {allBlocks, isTextAnchor, resolveAssetSources} from '@/lib/document';
import {captureAnchor, renderTextHighlights, resolveAnchor} from '@/lib/anchors';
import {useReadingActivity} from '@/lib/useReadingActivity';
import {useAnnotationUI, useLayout, usePreferences, type MarkStyle, type RightPanel} from '@/lib/stores';

const SAMPLE = resolveAssetSources(demo as DocumentModel);
const COLORS = ['#f8d86a', '#ef7474', '#74b9e8', '#83cfa7', '#b9a1e6', '#b8bec6'];
const PANELS: Array<{id: RightPanel; label: string}> = [{id: 'references', label: '参考文献'}, {id: 'figures', label: '图片'}, {id: 'tables', label: '表格'}, {id: 'notes', label: '笔记'}];
const MARK_STYLES: Array<{id: MarkStyle; label: string; Icon: typeof Highlighter}> = [
  {id: 'highlight', label: '高亮', Icon: Highlighter}, {id: 'underline', label: '下划线', Icon: Underline},
  {id: 'area', label: '区域选择', Icon: Scan},
];

type ParserState = {documentId: string; status: string; stage?: string; progress?: number; document?: DocumentModel; error?: string; ownerId?: string; ownerUsername?: string; formulaStatus?: 'processing' | 'ready' | 'failed'};
type ServerRecord = {documentId: string; title: string; pageCount: number; status: string; createdAt: number; lastOpenedAt?: number};
type LibraryRecord = {id: string; name: string; folder: string; size: number};
const PARSE_STAGE_LABELS: Record<string, string> = {
  queued: '等待解析', loading_parser: '准备解析器', extracting_structure: '提取正文结构',
  recognizing_scanned_pages: '识别扫描页面', linking_references: '关联参考文献',
  normalizing_document: '整理图表与公式', ready: '解析完成', failed: '解析失败',
};
const pause = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
const distinctReferencePreview = (reference: Reference) => reference.preview && reference.preview.replace(/\s+/g, ' ').trim() !== reference.title.replace(/\s+/g, ' ').trim() ? reference.preview : '';
const referenceDetails = (reference: Reference) => [reference.venue && !reference.title.includes(reference.venue) ? reference.venue : '', reference.year && !reference.title.includes(String(reference.year)) ? String(reference.year) : ''].filter(Boolean).join(' · ');
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

export default function ReaderShell({initialId, user, onLogout}: {initialId?: string; user: Account; onLogout: () => Promise<void>}) {
  const [paper, setPaper] = useState<DocumentModel>(SAMPLE);
  const [localId, setLocalId] = useState(SAMPLE.id);
  const [serverId, setServerId] = useState<string | undefined>();
  useReadingActivity(serverId, user.id);
  const [viewingOwner, setViewingOwner] = useState('');
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [importStatus, setImportStatus] = useState('');
  const [serverHistory, setServerHistory] = useState<ServerRecord[]>([]);
  const [serverLibrary, setServerLibrary] = useState<LibraryRecord[]>([]);
  const [settingsReady, setSettingsReady] = useState(false);
  const [noteFocusId, setNoteFocusId] = useState<string | null>(null);
  const [targetReferenceId, setTargetReferenceId] = useState<string | null>(null);
  const [openNoteEditors, setOpenNoteEditors] = useState<Set<string>>(() => new Set());
  const [progress, setProgress] = useState(0);
  const [colorOpen, setColorOpen] = useState(false);
  const colorControlRef = useRef<HTMLDivElement>(null);
  const articleRef = useRef<HTMLElement>(null);
  const pdfRef = useRef<HTMLDivElement>(null);
  const [splitView, setSplitView] = useState(false);
  const [splitWidth, setSplitWidth] = useState<number | null>(null);
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const readingAnchorRef = useRef<{blockId: string; blockOffset: number} | null>(null);
  const areaStart = useRef<{block: HTMLElement; surface: HTMLElement; x: number; y: number} | null>(null);
  const noteSyncTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const pendingCreates = useRef(new Map<string, Promise<void>>());
  const serverNoteWrites = useRef(new Map<string, Promise<void>>());
  const prefs = usePreferences();
  const layout = useLayout();
  const setLayout = useLayout(state => state.set);
  const annotationUI = useAnnotationUI();
  const figuresInOrder = [...paper.figures].sort((first, second) => (first.order ?? Infinity) - (second.order ?? Infinity));
  const tablesInOrder = [...paper.tables].sort((first, second) => (first.order ?? Infinity) - (second.order ?? Infinity));

  useEffect(() => {
    let active = true;
    usePreferences.getState().reset();
    useLayout.getState().set({leftOpen: false, rightOpen: false, focus: false});
    void fetch('/api/parser/api/settings', {cache: 'no-store'}).then(async response => {
      if (response.ok && active) prefs.set(await response.json());
    }).catch(() => {if (active) setImportStatus('阅读设置暂时无法同步');})
      .finally(() => {if (active) setSettingsReady(true);});
    return () => {active = false;};
  }, [user.id]);
  useEffect(() => {
    if (!settingsReady) return;
    const timer = setTimeout(() => {
      const {fontFamily, fontSize, lineHeight, contentWidth, theme, toolbarDock, activeColor, customApp, customPaper, customText, customAccent} = usePreferences.getState();
      void fetch('/api/parser/api/settings', {method: 'PUT', headers: {'content-type': 'application/json'},
        body: JSON.stringify({fontFamily, fontSize, lineHeight, contentWidth, theme, toolbarDock, activeColor, customApp, customPaper, customText, customAccent})})
        .then(response => {if (!response.ok) setImportStatus('阅读设置未能同步到服务器');})
        .catch(() => setImportStatus('阅读设置未能同步到服务器'));
    }, 500);
    return () => clearTimeout(timer);
  }, [settingsReady, prefs.fontFamily, prefs.fontSize, prefs.lineHeight, prefs.contentWidth, prefs.theme, prefs.toolbarDock, prefs.activeColor, prefs.customApp, prefs.customPaper, prefs.customText, prefs.customAccent]);
  useEffect(() => {
    if (!colorOpen) return;
    const closeOutside = (event: PointerEvent) => {
      if (!colorControlRef.current?.contains(event.target as Node)) setColorOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setColorOpen(false);
    };
    document.addEventListener('pointerdown', closeOutside, true);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOutside, true);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [colorOpen]);
  useEffect(() => {
    if (!targetReferenceId || !layout.rightOpen || layout.rightPanel !== 'references') return;
    const frame = requestAnimationFrame(() => {
      document.getElementById(`ref-${targetReferenceId}`)?.scrollIntoView({behavior: 'smooth', block: 'center'});
      setTargetReferenceId(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [targetReferenceId, layout.rightOpen, layout.rightPanel]);
  useEffect(() => {
    if (!articleRef.current) return;
    return renderTextHighlights(articleRef.current, annotations);
  }, [paper, annotations]);
  useEffect(() => {
    const root = articleRef.current;
    if (!root || !serverId) return;
    let cancelled = false;
    let restored = false;
    let frame = 0;
    let lastStored = '';
    let writeTimer: ReturnType<typeof setTimeout> | undefined;
    let pending: {percent: number; blockId?: string; blockOffset: number} | undefined;
    const flush = () => {
      if (!pending) return;
      const value = pending;
      pending = undefined;
      void fetch(`/api/parser/api/documents/${serverId}/progress`, {method: 'PUT', headers: {'content-type': 'application/json'}, body: JSON.stringify(value)})
        .then(response => {if (!response.ok) setImportStatus('阅读进度未能同步到服务器');})
        .catch(() => setImportStatus('阅读进度未能同步到服务器'));
    };
    const restore = async () => {
      let saved: number | undefined;
      let savedBlockId: string | undefined;
      let savedBlockOffset = 0;
      try {
        const response = await fetch(`/api/parser/api/documents/${serverId}/progress`, {cache: 'no-store'});
        if (response.ok) {
          const entry = await response.json() as {scroll_progress?: number; block_id?: string; block_offset?: number};
          saved = entry.scroll_progress;
          savedBlockId = entry.block_id;
          savedBlockOffset = entry.block_offset || 0;
        }
      } catch {}
      if (cancelled) return;
      saved = Number.isFinite(saved) ? Math.max(0, Math.min(100, saved!)) : 0;
      frame = requestAnimationFrame(() => {
        const block = savedBlockId && Array.from(root.querySelectorAll<HTMLElement>('[data-block-id]')).find(element => element.dataset.blockId === savedBlockId);
        if (block) {
          const fraction = Number.isFinite(savedBlockOffset) ? Math.max(0, Math.min(1, savedBlockOffset)) : 0;
          root.scrollTop += block.getBoundingClientRect().top + block.getBoundingClientRect().height * fraction - root.getBoundingClientRect().top - 24;
          readingAnchorRef.current = {blockId: savedBlockId!, blockOffset: fraction};
        } else {
          root.scrollTop = (root.scrollHeight - root.clientHeight) * saved / 100;
          const top = root.getBoundingClientRect().top + 24;
          const visible = Array.from(root.querySelectorAll<HTMLElement>('[data-block-id]')).find(element => element.getBoundingClientRect().bottom > top);
          const bounds = visible?.getBoundingClientRect();
          readingAnchorRef.current = visible?.dataset.blockId ? {blockId: visible.dataset.blockId, blockOffset: bounds?.height ? Math.max(0, Math.min(1, (top - bounds.top) / bounds.height)) : 0} : null;
        }
        const height = root.scrollHeight - root.clientHeight;
        const actual = height > 0 ? Math.round(root.scrollTop / height * 100) : 0;
        setProgress(actual);
        lastStored = `${actual}:${savedBlockId || ''}:${Math.round(savedBlockOffset * 20)}`;
        restored = true;
      });
    };
    const onScroll = () => {
      if (!restored) return;
      const height = root.scrollHeight - root.clientHeight;
      const next = height > 0 ? Math.round(root.scrollTop / height * 100) : 0;
      setProgress(next);
      const top = root.getBoundingClientRect().top + 24;
      const block = Array.from(root.querySelectorAll<HTMLElement>('[data-block-id]')).find(element => element.getBoundingClientRect().bottom > top);
      const blockId = block?.dataset.blockId;
      const bounds = block?.getBoundingClientRect();
      const blockOffset = bounds?.height ? Math.max(0, Math.min(1, (top - bounds.top) / bounds.height)) : 0;
      readingAnchorRef.current = blockId ? {blockId, blockOffset} : null;
      const marker = `${next}:${blockId || ''}:${Math.round(blockOffset * 20)}`;
      if (marker === lastStored) return;
      lastStored = marker;
      pending = {percent: next, blockId, blockOffset};
      if (writeTimer) clearTimeout(writeTimer);
      writeTimer = setTimeout(flush, 400);
    };
    root.addEventListener('scroll', onScroll, {passive: true});
    void restore();
    return () => {cancelled = true; cancelAnimationFrame(frame); if (writeTimer) clearTimeout(writeTimer); flush(); root.removeEventListener('scroll', onScroll);};
  }, [serverId]);

  useLayoutEffect(() => {
    const root = articleRef.current;
    const anchor = readingAnchorRef.current;
    if (!root || !anchor) return;
    const block = Array.from(root.querySelectorAll<HTMLElement>('[data-block-id]')).find(element => element.dataset.blockId === anchor.blockId);
    if (!block) return;
    const bounds = block.getBoundingClientRect();
    root.scrollTop += bounds.top + bounds.height * anchor.blockOffset - root.getBoundingClientRect().top - 24;
  }, [paper, prefs.fontFamily, prefs.fontSize, prefs.lineHeight, prefs.contentWidth, layout.focus, layout.leftOpen, layout.rightOpen]);

  useEffect(() => {
    if (!serverId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const check = async () => {
      try {
        const response = await fetch(`/api/parser/api/documents/${serverId}`, {cache: 'no-store'});
        if (!response.ok || cancelled) return;
        const state = await response.json() as ParserState;
        if (cancelled) return;
        if (state.formulaStatus === 'processing') {
          setImportStatus(current => current || '正文已可阅读，公式正在后台排版…');
          timer = setTimeout(check, 5000);
        }
        else if (state.formulaStatus === 'ready' && state.document) {
          setPaper(current => current.id === state.documentId ? resolveAssetSources(state.document!, serverId) : current);
          setImportStatus(current => current === '正文已可阅读，公式正在后台排版…' ? '' : current);
        } else if (state.formulaStatus === 'failed') {
          setImportStatus(current => current === '正文已可阅读，公式正在后台排版…' ? '公式识别未完成，已保留原始文本' : current);
        }
      } catch {if (!cancelled) timer = setTimeout(check, 10000);}
    };
    void check();
    return () => {cancelled = true; if (timer) clearTimeout(timer);};
  }, [serverId]);

  const openDocument = useCallback(async (document: DocumentModel, id: string, ownerId?: string, ownerUsername?: string) => {
    const response = await fetch(`/api/parser/api/documents/${id}/annotations`, {cache: 'no-store'});
    if (!response.ok) throw new Error('无法读取此论文的笔记');
    const saved = (await response.json() as Array<Annotation & {blockId?: string; start?: number; end?: number; quote?: string; mode?: string}>).map(item => {
      if (item.anchor) return item;
      const anchor = {start: {blockId: item.blockId || '', offset: item.start || 0}, end: {blockId: item.blockId || '', offset: item.end || 0}, quote: item.quote || '', prefix: '', suffix: ''};
      return {...item, documentId: id, type: item.note ? 'note' : item.mode === 'underline' ? 'underline' : 'highlight', anchor} as Annotation;
    });
    readingAnchorRef.current = null;
    setProgress(0); setPaper(resolveAssetSources(document, id)); setLocalId(id); setServerId(id);
    setViewingOwner(ownerId && ownerId !== user.id ? ownerUsername || '其他用户' : '');
    setAnnotations(saved); setLayout({historyOpen: false});
    try {history.replaceState({}, '', `/reader/${encodeURIComponent(id)}`);} catch {}
  }, [setLayout, user.id]);

  useEffect(() => {
    if (!initialId) return;
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(`/api/parser/api/documents/${encodeURIComponent(initialId)}`);
        if (!response.ok) throw new Error('无法访问这篇论文');
        const state = await response.json() as ParserState;
        if (state.document && !cancelled) await openDocument(state.document, initialId, state.ownerId, state.ownerUsername);
      } catch (error) {if (!cancelled) setImportStatus(error instanceof Error ? error.message : '无法打开论文');}
    })();
    return () => {cancelled = true;};
  }, [initialId, openDocument]);

  const addAnnotation = useCallback(async (record: Annotation) => {
    if (!serverId) return;
    setAnnotations(current => [...current, record]);
    if (record.type === 'note' || record.noteEnabled) {setLayout({rightPanel: 'notes', rightOpen: true}); setNoteFocusId(record.id);}
    const pending = (async () => {
      try {
        const response = await fetch(`/api/parser/api/documents/${serverId}/annotations`, {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(record)});
        if (!response.ok) throw new Error(`保存批注失败 (${response.status})`);
      } catch {
        setAnnotations(current => current.filter(item => item.id !== record.id));
        setImportStatus('批注未能保存到服务器，请重试');
      }
    })();
    pendingCreates.current.set(record.id, pending);
    try {await pending;} finally {pendingCreates.current.delete(record.id);}
  }, [serverId, setLayout]);

  const syncNote = (annotationId: string, note: string, remote: string) => {
    const writes = serverNoteWrites.current;
    const previous = writes.get(annotationId);
    const next = (previous?.catch(() => {}) ?? Promise.resolve()).then(async () => {
      await pendingCreates.current.get(annotationId);
      const response = await fetch(`/api/parser/api/documents/${remote}/annotations/${annotationId}`, {method: 'PATCH', headers: {'content-type': 'application/json'}, body: JSON.stringify({note})});
      if (!response.ok) throw new Error('Server note sync failed');
    });
    writes.set(annotationId, next);
    const clear = () => {if (writes.get(annotationId) === next) writes.delete(annotationId);};
    void next.then(clear, () => {clear(); setImportStatus('笔记未能同步到服务器');});
  };
  const updateNote = (record: Annotation, note: string) => {
    const changed = {...record, note, updatedAt: Date.now()};
    setAnnotations(current => current.map(item => item.id === record.id ? {...item, note, updatedAt: changed.updatedAt} : item));
    const prior = noteSyncTimers.current.get(record.id);
    if (prior) clearTimeout(prior);
    if (serverId) noteSyncTimers.current.set(record.id, setTimeout(() => {
      syncNote(record.id, note, serverId);
      noteSyncTimers.current.delete(record.id);
    }, 400));
  };
  const removeAnnotation = async (record: Annotation) => {
    const timer = noteSyncTimers.current.get(record.id);
    if (timer) {clearTimeout(timer); noteSyncTimers.current.delete(record.id);}
    setAnnotations(current => current.filter(item => item.id !== record.id));
    try {
      await pendingCreates.current.get(record.id);
      await serverNoteWrites.current.get(record.id)?.catch(() => {});
      if (serverId) {
        const response = await fetch(`/api/parser/api/documents/${serverId}/annotations/${record.id}`, {method: 'DELETE'});
        if (!response.ok && response.status !== 404) throw new Error(`Delete failed: ${response.status}`);
      }
    } catch {
      setAnnotations(current => current.some(item => item.id === record.id) ? current : [...current, record]);
      setImportStatus('删除未同步到服务器，请重试');
      return;
    }
  };
  const onTextSelection = () => {
    if (!annotationUI.markStyle || annotationUI.markStyle === 'area' || !articleRef.current || !serverId) return;
    const anchor = captureAnchor(articleRef.current);
    if (!anchor) return;
    const record: Annotation = {id: crypto.randomUUID(), documentId: localId, type: annotationUI.noteEnabled ? 'note' : annotationUI.markStyle, style: annotationUI.markStyle, noteEnabled: annotationUI.noteEnabled, color: prefs.activeColor, anchor, note: '', createdAt: Date.now()};
    void addAnnotation(record);
    window.getSelection()?.removeAllRanges();
  };
  const onAreaStart = (event: React.PointerEvent<HTMLElement>) => {
    if (annotationUI.markStyle !== 'area' || !serverId) return;
    const block = (event.target as Element).closest<HTMLElement>('.area-target[data-block-id]');
    if (!block) return;
    const imageSurface = block.querySelector<HTMLElement>('.area-surface');
    if (imageSurface && !imageSurface.contains(event.target as Node)) return;
    const surface = imageSurface || block;
    const rect = surface.getBoundingClientRect();
    areaStart.current = {block, surface, x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))};
    block.setPointerCapture(event.pointerId);
  };
  const onAreaEnd = (event: React.PointerEvent<HTMLElement>) => {
    const start = areaStart.current;
    areaStart.current = null;
    if (!start) return;
    const rect = start.surface.getBoundingClientRect();
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
    const anchor: AreaAnchor = {blockId, page, bbox: pageBox, space: block?.bbox && pageSize ? 'page' : 'block', ...(start.surface !== start.block ? {surface: 'image' as const} : {})};
    void addAnnotation({id: crypto.randomUUID(), documentId: localId, type: 'area', noteEnabled: annotationUI.noteEnabled, color: prefs.activeColor, anchor, createdAt: Date.now()});
  };

  const openHistory = async () => {
    layout.set({historyOpen: true});
    try {
      const response = await fetch('/api/parser/api/documents', {cache: 'no-store'});
      if (!response.ok) throw new Error('无法读取我的论文');
      setServerHistory(await response.json() as ServerRecord[]);
      if (user.role === 'admin') {
        const library = await fetch('/api/parser/api/library', {cache: 'no-store'});
        setServerLibrary(library.ok ? await library.json() as LibraryRecord[] : []);
      }
    } catch (error) {setImportStatus(error instanceof Error ? error.message : '无法读取我的论文');}
  };
  useEffect(() => {if (!initialId) void openHistory();}, [initialId, user.id]);

  const deleteServerDocument = async (id: string) => {
    const response = await fetch(`/api/parser/api/documents/${id}`, {method: 'DELETE'});
    if (!response.ok) throw new Error('删除论文失败');
    setDeleteId(null);
    setServerHistory(current => current.filter(item => item.documentId !== id));
    if (serverId === id) {setPaper(SAMPLE); setServerId(undefined); setLocalId(SAMPLE.id); setViewingOwner(''); setAnnotations([]); history.replaceState({}, '', '/');}
  };
  const openServer = async (id: string) => {
    const response = await fetch(`/api/parser/api/documents/${id}`);
    if (!response.ok) throw new Error('服务器文档无法打开');
    const state = await response.json() as ParserState;
    if (!state.document) throw new Error('文档尚未完成解析');
    await openDocument(state.document, id, state.ownerId, state.ownerUsername);
  };
  const openLibrary = async (id: string, filename: string) => {
    setImportStatus('正在打开服务器文件…');
    try {
      const response = await fetch(`/api/parser/api/library/${id}/open`, {method: 'POST'});
      if (!response.ok) throw new Error(`服务器文件无法打开 (${response.status})`);
      const state = await waitForDocument(await response.json() as ParserState, setImportStatus);
      await openDocument(state.document, state.documentId, state.ownerId, state.ownerUsername);
      setImportStatus('');
    } catch (error) {setImportStatus(error instanceof Error ? error.message : '打开服务器文件失败');}
  };
  const jumpToAnnotation = (record: Annotation) => {
    const root = articleRef.current;
    if (!root) return;
    if (!isTextAnchor(record.anchor) && record.anchor.pdfOnly) {
      setSplitView(true);
      const anchor = record.anchor;
      requestAnimationFrame(() => pdfRef.current?.querySelector(`[data-pdf-page="${anchor.page}"]`)?.scrollIntoView({block: 'start'}));
      return;
    }
    if (isTextAnchor(record.anchor)) {
      const range = resolveAnchor(root, record.anchor);
      range?.startContainer.parentElement?.scrollIntoView({behavior: 'smooth', block: 'center'});
    } else root.querySelector(`[data-block-id="${record.anchor.blockId}"]`)?.scrollIntoView({behavior: 'smooth', block: 'center'});
  };

  const navigateContent = (id: string) => {
    document.getElementById(id)?.scrollIntoView({behavior: 'smooth', block: 'center'});
    if (!splitView) return;
    const block = allBlocks(paper).find(item => item.id === id) || paper.sections.find(section => section.id === id)?.blocks[0];
    if (!block?.page || !pdfRef.current) return;
    const page = pdfRef.current.querySelector<HTMLElement>(`[data-pdf-page="${block.page}"]`);
    if (page) pdfRef.current.scrollTo({top: page.offsetTop - pdfRef.current.offsetTop, behavior: 'smooth'});
  };

  const openReference = (id: string) => {
    const reference = paper.references.find(item => item.id === id || String(item.number) === id);
    if (!reference) return;
    layout.set({rightPanel: 'references', rightOpen: true});
    setTargetReferenceId(reference.id);
  };

  const toolbar = <div className="annotation-tools" role="toolbar" aria-label="标注工具">
    <div className="tool-group" role="group" aria-label="划线样式">{MARK_STYLES.map(({id, label, Icon}) => <button key={id} type="button" className={annotationUI.markStyle === id ? 'active' : ''} aria-label={label} title={label} aria-pressed={annotationUI.markStyle === id} onClick={() => annotationUI.setStyle(id)}><Icon size={18} /></button>)}</div>
    <div className="tool-group" role="group" aria-label="笔记"><button type="button" className={annotationUI.noteEnabled ? 'active' : ''} aria-label="添加笔记" title="选择后添加笔记" aria-pressed={annotationUI.noteEnabled} onClick={() => annotationUI.setNoteEnabled(!annotationUI.noteEnabled)}><StickyNote size={18} /></button></div>
    <div ref={colorControlRef} className="tool-group color-control" role="group" aria-label="颜色"><button type="button" className="gradient-trigger" aria-label="选择标注颜色" title="标注颜色" aria-expanded={colorOpen} onClick={() => setColorOpen(!colorOpen)}><span className="gradient-swatch" /><span className="selected-color" style={{background: prefs.activeColor}} /></button>{colorOpen && <div className="color-popover"><div className="color-presets">{COLORS.map(color => <button key={color} type="button" title={color} aria-label={`颜色 ${color}`} style={{background: color}} onClick={() => {prefs.set({activeColor: color}); setColorOpen(false);}} />)}<label className="gradient-picker" title="自定义颜色"><input type="color" aria-label="自定义颜色" value={prefs.activeColor} onChange={event => prefs.set({activeColor: event.target.value})} /></label></div></div>}</div>
  </div>;

  return <div className={`reader-app theme-${prefs.theme} dock-${prefs.toolbarDock} ${layout.focus ? 'focus-mode' : ''} ${annotationUI.markStyle === 'area' ? 'area-mode' : ''}`} style={{'--reader-font': prefs.fontFamily, '--reader-size': `${prefs.fontSize}px`, '--reader-leading': prefs.lineHeight, '--reader-width': `${prefs.contentWidth}px`,
      '--split-width': splitWidth === null ? 'var(--reader-width)' : `${splitWidth}%`, '--custom-app': prefs.customApp, '--custom-paper': prefs.customPaper, '--custom-text': prefs.customText, '--custom-accent': prefs.customAccent} as React.CSSProperties}>
    <header className="reader-topbar"><Link className="brand reader-brand" href="/"><BookOpen size={20} /><strong>Paperlight</strong></Link><Link className="library-link" href="/" aria-label="返回论文库" title="返回论文库"><ArrowLeft size={19} /></Link>{viewingOwner && <span className="owner-context">正在查看 {viewingOwner} 的论文</span>}<span className="top-title" title={paper.metadata.title}>{paper.metadata.title}</span>{prefs.toolbarDock === 'top' && toolbar}<div className="top-actions"><span className="read-time"><Clock3 size={15} /> {paper.metadata.readMinutes || '—'} min</span><button title="显示 PDF" aria-label="显示 PDF" aria-pressed={splitView} disabled={!serverId} onClick={() => setSplitView(value => !value)}><FileText size={18} /></button><button title="减小字号" aria-label="减小字号" onClick={() => prefs.set({fontSize: Math.max(14, prefs.fontSize - 1)})}><Minus size={16} /></button><button title="增大字号" aria-label="增大字号" onClick={() => prefs.set({fontSize: Math.min(26, prefs.fontSize + 1)})}><Plus size={16} /></button><button title="阅读设置" aria-label="阅读设置" onClick={() => layout.set({settingsOpen: true})}><Settings2 size={17} /></button><button title="专注模式" aria-label="专注模式" aria-pressed={layout.focus} onClick={() => layout.set({focus: !layout.focus, leftOpen: layout.focus, rightOpen: layout.focus})}><Maximize2 size={18} /></button><AccountMenu user={user} onLogout={onLogout} /></div></header>
    {importStatus && <div className="status-banner" role="status">{importStatus}<button aria-label="关闭提示" onClick={() => setImportStatus('')}><X size={14} /></button></div>}
    <div className="reader-grid"><aside className={`left-panel ${layout.leftOpen ? 'open' : ''}`}><div className="panel-heading"><span>目录</span><button title="收起目录" onClick={() => layout.set({leftOpen: false})}><ChevronLeft size={16} /></button></div><nav>{paper.sections.map(section => <button key={section.id} className={`toc-item level-${section.level}`} onClick={() => navigateContent(section.id)}>{section.title}</button>)}</nav></aside>
      {(!layout.leftOpen || layout.focus) && <div className="side-rail"><button title="目录" aria-label="目录" aria-pressed={layout.leftOpen} onClick={() => layout.set({leftOpen: !layout.leftOpen})}><List size={19} /></button></div>}
      <main className={`reading-column ${splitView && serverId ? 'split-reading' : ''}`}>{splitView && serverId && <PdfPane paper={paper} serverId={serverId} annotations={annotations} scrollRef={pdfRef} style={annotationUI.markStyle} color={prefs.activeColor} noteEnabled={annotationUI.noteEnabled} onAnnotation={record => void addAnnotation(record)} onNotice={setImportStatus} />}{splitView && serverId && <PaneDivider rightPercent={splitWidth} onChange={setSplitWidth} />}{prefs.toolbarDock !== 'top' && <div className={`docked-tools docked-${prefs.toolbarDock}`}>{toolbar}</div>}<article ref={articleRef} className="reader-scroll" onMouseUp={onTextSelection} onPointerDown={onAreaStart} onPointerUp={onAreaEnd} onPointerCancel={() => {areaStart.current = null;}}><div className="paper-content"><DocumentRenderer document={paper} annotations={annotations} onReference={openReference} /></div></article><div className="reading-progress"><span style={{width: `${progress}%`}} /></div></main>
      {(!layout.rightOpen || layout.focus) && <div className="side-rail right-side">{PANELS.map(panel => {const Icon = panel.id === 'references' ? BookOpen : panel.id === 'figures' ? Image : panel.id === 'tables' ? Table2 : StickyNote; return <button key={panel.id} title={panel.label} aria-label={panel.label} aria-pressed={layout.rightOpen && layout.rightPanel === panel.id} onClick={() => layout.set({rightOpen: !(layout.rightOpen && layout.rightPanel === panel.id), rightPanel: panel.id})}><Icon size={18} /></button>;})}</div>}
      <aside className={`right-panel ${layout.rightOpen ? 'open' : ''}`}><div className="panel-tabs">{PANELS.map(panel => <button key={panel.id} className={layout.rightPanel === panel.id ? 'active' : ''} onClick={() => layout.set({rightPanel: panel.id})}>{panel.label}</button>)}<button className="panel-collapse" title="收起面板" aria-label="收起面板" onClick={() => layout.set({rightOpen: false})}><ChevronRight size={16} /></button></div><div className="panel-list">
        {layout.rightPanel === 'references' && (paper.references.length ? paper.references.map(ref => <div className="reference-card" id={`ref-${ref.id}`} key={ref.id}><small>[{ref.number}] {ref.authors}</small><strong>{ref.title}</strong>{referenceDetails(ref) && <span>{referenceDetails(ref)}</span>}{distinctReferencePreview(ref) && <p>{distinctReferencePreview(ref)}</p>}</div>) : <p className="empty-panel">暂无参考文献</p>)}
        {layout.rightPanel === 'figures' && (figuresInOrder.length ? figuresInOrder.map(item => <button className="asset-card" key={item.id} onClick={() => navigateContent(item.id)}>{item.src && <img src={item.src} alt="" />}<strong>{item.label || `Figure ${item.number}`}</strong><span>{item.caption}</span></button>) : <p className="empty-panel">暂无图片</p>)}
        {layout.rightPanel === 'tables' && (tablesInOrder.length ? tablesInOrder.map(item => <button className="asset-card" key={item.id} onClick={() => navigateContent(item.id)}><strong>{item.label || `Table ${item.number}`}</strong><span>{item.caption}</span></button>) : <p className="empty-panel">暂无表格</p>)}
        {layout.rightPanel === 'notes' && (annotations.length ? annotations.map(item => <div className="note-card" key={item.id} style={{borderColor: item.color, background: `${item.color}18`}}><button className="note-quote" onClick={() => jumpToAnnotation(item)}>{isTextAnchor(item.anchor) ? `“${item.anchor.quote}”` : `第 ${item.anchor.page} 页区域`}</button>{(item.type === 'note' || item.noteEnabled || !!item.note || openNoteEditors.has(item.id)) && <textarea aria-label="笔记内容" placeholder="输入笔记…" value={item.note || ''} autoFocus={noteFocusId === item.id} onFocus={() => setNoteFocusId(null)} onChange={event => void updateNote(item, event.target.value)} onBlur={event => {const timer = noteSyncTimers.current.get(item.id); if (timer && serverId) {clearTimeout(timer); noteSyncTimers.current.delete(item.id); syncNote(item.id, event.currentTarget.value, serverId);}}} />}<div className="note-footer"><span>{item.type === 'note' ? item.style === 'underline' ? '下划线 · 笔记' : '高亮 · 笔记' : item.type === 'area' ? item.noteEnabled ? '区域 · 笔记' : '区域' : item.type === 'underline' ? '下划线' : '高亮'}</span>{item.type !== 'note' && !item.noteEnabled && !item.note && !openNoteEditors.has(item.id) && <button onClick={() => {setOpenNoteEditors(current => new Set(current).add(item.id)); setNoteFocusId(item.id);}}>添加笔记</button>}<button onClick={() => void removeAnnotation(item)}>删除</button></div></div>) : <p className="empty-panel">选择文字后，笔记和标注会出现在这里。</p>)}
      </div></aside></div>
    {layout.settingsOpen && <div className="drawer-backdrop" onClick={() => layout.set({settingsOpen: false})}><aside className="settings-drawer" onClick={event => event.stopPropagation()}><div className="drawer-title"><h2>阅读设置</h2><button aria-label="关闭设置" onClick={() => layout.set({settingsOpen: false})}><X size={20} /></button></div><label>字体<select value={prefs.fontFamily} onChange={event => prefs.set({fontFamily: event.target.value})}><option value="Georgia, serif">Georgia</option><option value="Arial, sans-serif">Arial</option><option value="'Times New Roman', serif">Times New Roman</option></select></label><label>字号 <b>{prefs.fontSize}px</b><input type="range" min="14" max="26" value={prefs.fontSize} onChange={event => prefs.set({fontSize: Number(event.target.value)})} /></label><label>行距 <b>{prefs.lineHeight.toFixed(1)}</b><input type="range" min="1.2" max="2.2" step="0.1" value={prefs.lineHeight} onChange={event => prefs.set({lineHeight: Number(event.target.value)})} /></label><label>阅读宽度 <b>{prefs.contentWidth}px</b><input type="range" min="600" max="1200" step="20" value={prefs.contentWidth} onChange={event => {setSplitWidth(null); prefs.set({contentWidth: Number(event.target.value)});}} /></label><label>主题<select value={prefs.theme} onChange={event => prefs.set({theme: event.target.value as typeof prefs.theme})}><option value="paper">纸张</option><option value="warm">暖色</option><option value="dark">深色</option><option value="custom">自定义</option></select></label>{prefs.theme === 'custom' && <div className="custom-theme-colors">{([['customApp', '界面背景'], ['customPaper', '纸张背景'], ['customText', '正文文字'], ['customAccent', '强调色']] as const).map(([key, label]) => <label key={key}>{label}<input type="color" value={prefs[key]} onChange={event => prefs.set({[key]: event.target.value})} /></label>)}</div>}<label>工具栏位置<select value={prefs.toolbarDock} onChange={event => prefs.set({toolbarDock: event.target.value as typeof prefs.toolbarDock})}><option value="top">顶部</option><option value="bottom">底部</option><option value="left">左侧</option><option value="right">右侧</option></select></label><p className="settings-sync">设置会自动保存到你的账户，在其他设备登录后恢复。</p></aside></div>}
    {deleteId && <ConfirmDialog title="删除论文？" message="论文和笔记将被删除，此操作无法撤销。" onCancel={() => setDeleteId(null)} onConfirm={() => deleteServerDocument(deleteId)} />}
    {layout.historyOpen && <div className="dialog-backdrop" onClick={() => layout.set({historyOpen: false})}><section className="history-dialog" onClick={event => event.stopPropagation()}>
      <div className="drawer-title"><h2>我的论文</h2><button aria-label="关闭我的论文" onClick={() => layout.set({historyOpen: false})}><X size={20} /></button></div>
      <div className="history-list"><h3>最近阅读</h3>
        {serverHistory.filter(item => item.lastOpenedAt).sort((a, b) => (b.lastOpenedAt || 0) - (a.lastOpenedAt || 0)).slice(0, 5).map(item => <button key={item.documentId} onClick={() => void openServer(item.documentId).catch(error => setImportStatus(error.message))}><strong>{item.title}</strong><small>{item.pageCount} 页</small></button>)}
        <h3>全部论文</h3>{serverHistory.length ? serverHistory.map(item => <div className="history-row" key={item.documentId}><button className="history-open" onClick={() => void openServer(item.documentId).catch(error => setImportStatus(error.message))}><strong>{item.title}</strong><small>{item.pageCount} 页 · {item.status}</small></button><button className="history-remove" aria-label={`删除 ${item.title}`} onClick={() => setDeleteId(item.documentId)}>删除</button></div>) : <p className="empty-panel">还没有论文，点击右上角导入 PDF。</p>}
        {user.role === 'admin' && <><h3>服务器文件库</h3>{serverLibrary.map(item => <button key={item.id} onClick={() => void openLibrary(item.id, item.name)}><strong>{item.name}</strong><small>{item.folder === '.' ? '文件库根目录' : item.folder} · {(item.size / 1024 / 1024).toFixed(1)} MB</small></button>)}</>}
      </div></section></div>}
  </div>;
}
