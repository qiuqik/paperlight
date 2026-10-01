'use client';

import {useCallback, useEffect, useRef, useState} from 'react';
import {BookOpen, ChevronRight, Clock3, Grid2X2, List, Plus, Search, Settings2, Star, Trash2} from 'lucide-react';
import ReadingActivity from './ReadingActivity';
import ConfirmDialog from './ConfirmDialog';
import WorkspaceFrame from './WorkspaceFrame';
import type {Account} from './AppShell';
import type {PublicationInfo} from '@/lib/document';

type Paper = {documentId: string; title: string; authors: string[]; pageCount: number; status: string; parseSource?: string; arxivId?: string; arxivVersion?: number; createdAt: number; lastOpenedAt?: number; annotationCount: number; progress: number; favorite: boolean; previewSrc?: string; publication?: PublicationInfo};
type Section = 'all' | 'recent' | 'favorites';
type UploadState = {documentId: string; status: string; stage?: string; progress?: number; error?: string};
const stages: Record<string, string> = {queued: '等待解析', loading_parser: '准备解析器', fetching_arxiv_html: '获取 arXiv 官方 HTML', fetching_arxiv_pdf: '获取固定版本 PDF', extracting_structure: '提取正文', recognizing_scanned_pages: '识别扫描页', linking_references: '整理参考文献', normalizing_document: '整理图表和公式'};
const dateLabel = (timestamp?: number) => timestamp ? new Date(timestamp * 1000).toLocaleDateString('zh-CN') : '尚未阅读';

export default function LibraryShell({user, onLogout}: {user: Account; onLogout: () => Promise<void>}) {
  const [papers, setPapers] = useState<Paper[]>([]);
  const [section, setSection] = useState<Section>('all');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState('');
  const [uploading, setUploading] = useState(false);
  const [sort, setSort] = useState('recent');
  const [view, setView] = useState<'list' | 'grid'>('list');
  const [deleteTarget, setDeleteTarget] = useState<Paper | null>(null);
  const picker = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    const response = await fetch('/api/parser/api/documents', {cache: 'no-store'});
    if (!response.ok) throw new Error('无法读取我的论文');
    setPapers(await response.json() as Paper[]);
  }, []);
  useEffect(() => {void refresh().catch(error => setMessage(error.message)).finally(() => setLoading(false));}, [refresh]);


  const finishImport = async (response: Response) => {
    if (!response.ok) {
      const body = await response.json().catch(() => ({})) as {detail?: string};
      throw new Error(body.detail || `导入失败 (${response.status})`);
    }
    let state = await response.json() as UploadState;
    for (let tries = 0; tries < 360 && state.status === 'processing'; tries++) {
      setMessage(`${stages[state.stage || ''] || '正在解析'} · ${Math.round((state.progress || 0) * 100)}%`);
      await new Promise(resolve => setTimeout(resolve, 2000));
      const poll = await fetch(`/api/parser/api/documents/${state.documentId}`, {cache: 'no-store'});
      if (!poll.ok) throw new Error('状态查询失败');
      state = await poll.json() as UploadState;
    }
    await refresh();
    if (state.status !== 'ready') throw new Error(state.error || '解析未完成');
    window.location.assign(`/reader/${state.documentId}`);
  };
  const importPdf = async (file?: File) => {
    if (!file) return;
    setUploading(true);
    setMessage('正在上传 PDF…');
    try {
      const body = new FormData(); body.set('file', file);
      await finishImport(await fetch('/api/parser/api/documents', {method: 'POST', body}));
    } catch (error) {setMessage(error instanceof Error ? error.message : '上传失败');}
    finally {setUploading(false); if (picker.current) picker.current.value = '';}
  };
  const toggleFavorite = async (paper: Paper) => {
    const response = await fetch(`/api/parser/api/documents/${paper.documentId}/favorite`, {method: 'PATCH', headers: {'content-type': 'application/json'}, body: JSON.stringify({favorite: !paper.favorite})});
    if (!response.ok) {setMessage('收藏状态保存失败'); return;}
    setPapers(current => current.map(item => item.documentId === paper.documentId ? {...item, favorite: !paper.favorite} : item));
  };
  const deletePaper = async (paper: Paper) => {
    const response = await fetch(`/api/parser/api/documents/${paper.documentId}`, {method: 'DELETE'});
    if (!response.ok) throw new Error('删除论文失败');
    setDeleteTarget(null);
    setPapers(current => current.filter(item => item.documentId !== paper.documentId));
  };

  const recent = [...papers].filter(item => item.lastOpenedAt && item.status === 'ready').sort((a, b) => (b.lastOpenedAt || 0) - (a.lastOpenedAt || 0));
  const source = section === 'favorites' ? papers.filter(item => item.favorite) : section === 'recent' ? recent : papers;
  const filtered = source.filter(item => `${item.title} ${item.authors.join(' ')}`.toLowerCase().includes(query.trim().toLowerCase())).sort((a, b) => sort === 'title' ? a.title.localeCompare(b.title) : sort === 'added' ? b.createdAt - a.createdAt : (b.lastOpenedAt || b.createdAt) - (a.lastOpenedAt || a.createdAt));
  const open = (paper: Paper) => {
    if (paper.status !== 'ready') {setMessage(paper.status === 'failed' ? '这篇论文解析失败，请重新导入。' : '这篇论文仍在解析，请稍后刷新页面。'); return;}
    window.location.assign(`/reader/${paper.documentId}`);
  };
  const hour = new Date().getHours();
  const greeting = hour < 12 ? '早上好' : hour < 18 ? '下午好' : '晚上好';
  const search = <label className="workspace-search"><Search size={16} /><input aria-label="搜索论文" placeholder="搜索论文标题、作者…" value={query} onChange={event => setQuery(event.target.value)} /></label>;
  const sidebar = <nav className="workspace-sidebar" aria-label="论文库导航"><button className={section === 'all' ? 'selected' : ''} onClick={() => setSection('all')}><BookOpen size={18} />我的论文</button><button className={section === 'recent' ? 'selected' : ''} onClick={() => setSection('recent')}><Clock3 size={18} />最近阅读</button><button className={section === 'favorites' ? 'selected' : ''} onClick={() => setSection('favorites')}><Star size={18} />收藏</button><a className="sidebar-bottom" href="/settings"><Settings2 size={18} />阅读设置</a></nav>;
  return <WorkspaceFrame user={user} onLogout={onLogout} sidebar={sidebar} search={search}>
    <div className="library-welcome"><div><h1>{greeting}，{user.display_name}</h1><p>继续探索你的研究世界。</p></div><button className="primary-button" disabled={uploading} onClick={() => picker.current?.click()}><Plus size={17} />导入 PDF</button></div>
    {section === 'all' && !query && <ReadingActivity userId={user.id} />}
    {message && <div className="library-message" role="status">{message}<button onClick={() => setMessage('')} aria-label="关闭提示">×</button></div>}
    {section === 'all' && !query && <section className="library-recent"><div className="library-section-heading"><h2>最近阅读</h2>{recent.length > 3 && <button onClick={() => setSection('recent')}>查看全部</button>}</div>{recent.length ? <div className="library-recent-grid">{recent.slice(0, 3).map(item => <button className="library-recent-card" key={item.documentId} onClick={() => open(item)}><span className="paper-cover">{item.previewSrc ? <img src={item.previewSrc?.startsWith('/api/documents/') ? `/api/parser${item.previewSrc}` : item.previewSrc} alt="" /> : <BookOpen size={30} />}</span><strong title={item.title}>{item.title}</strong><small>{item.authors.slice(0, 1).join('') || '作者待确认'}{item.authors.length > 1 ? ' et al.' : ''}</small><span className="paper-progress"><i style={{width: `${item.progress || 0}%`}} /></span><span className="recent-card-footer"><small>{Math.round(item.progress || 0)}% · {item.pageCount} 页<br />{dateLabel(item.lastOpenedAt)}</small><ChevronRight size={17} /></span></button>)}</div> : <p className="library-empty">打开论文后，最近阅读会显示在这里。</p>}</section>}
    <section className="library-papers"><div className="library-section-heading"><div><h2>{section === 'all' ? '我的论文' : section === 'recent' ? '最近阅读' : '收藏'}</h2><small>{filtered.length} 篇</small></div><div className="library-view-controls"><select aria-label="论文排序" value={sort} onChange={event => setSort(event.target.value)}><option value="recent">最近打开</option><option value="added">添加时间</option><option value="title">标题</option></select><button aria-label="列表视图" aria-pressed={view === 'list'} onClick={() => setView('list')}><List size={17} /></button><button aria-label="网格视图" aria-pressed={view === 'grid'} onClick={() => setView('grid')}><Grid2X2 size={17} /></button></div></div>
      {loading ? <p className="library-empty">正在加载论文…</p> : filtered.length ? <div className={`paper-library ${view}`}><div className="paper-table-heading"><span>标题</span><span>作者</span><span>添加时间</span><span>进度</span><span /></div>{filtered.map(item => <div className="paper-library-row" key={item.documentId}><button className="paper-title-button" onClick={() => open(item)} title={item.title}><BookOpen size={18} /><span className="paper-title-copy"><strong>{item.title}</strong>{item.publication && <span className="library-publication-tags">{item.publication.venue_short && <span title={item.publication.venue || undefined}>{item.publication.venue_short}</span>}{item.publication.publish_time && <time>{String(item.publication.publish_time).replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3')}</time>}{item.publication.institutions?.map(institution => <span key={institution} title={institution}>{institution}</span>)}{item.publication.publication_status === 'preprint' && <span>预印本</span>}</span>}</span></button><span className="paper-row-authors" title={item.authors.join(', ')}>{item.authors[0] || '待确认'}{item.authors.length > 1 ? ' et al.' : ''}</span><span className="paper-row-date">{dateLabel(item.createdAt)}</span><span className="paper-row-progress">{item.status === 'ready' ? `${Math.round(item.progress || 0)}%` : item.status === 'failed' ? '失败' : '解析中'}<small>{item.pageCount} 页 · {item.annotationCount} 条标注</small></span><div className="library-paper-actions"><button title={item.favorite ? '取消收藏' : '收藏'} aria-label={item.favorite ? `取消收藏 ${item.title}` : `收藏 ${item.title}`} onClick={() => void toggleFavorite(item)}><Star size={16} fill={item.favorite ? 'currentColor' : 'none'} /></button><button title="删除论文" aria-label={`删除 ${item.title}`} onClick={() => setDeleteTarget(item)}><Trash2 size={16} /></button></div></div>)}</div> : <p className="library-empty">{query ? '没有找到匹配的论文。' : '这里还没有论文，导入 PDF 开始阅读。'}</p>}
    </section>{deleteTarget && <ConfirmDialog title="删除论文？" message={`《${deleteTarget.title}》及其笔记将被删除，此操作无法撤销。`} onCancel={() => setDeleteTarget(null)} onConfirm={() => deletePaper(deleteTarget)} />}<input ref={picker} hidden type="file" accept="application/pdf,.pdf" onChange={event => void importPdf(event.target.files?.[0])} />
  </WorkspaceFrame>;
}
