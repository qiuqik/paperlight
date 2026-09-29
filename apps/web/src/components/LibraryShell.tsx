'use client';

import {useCallback, useEffect, useRef, useState} from 'react';
import {BookOpen, Clock3, FilePlus2, Search, Star, Trash2} from 'lucide-react';
import AccountMenu from './AccountMenu';
import type {Account} from './AppShell';

type Paper = {documentId: string; title: string; authors: string[]; pageCount: number; status: string; parseSource?: string; arxivId?: string; arxivVersion?: number; createdAt: number; lastOpenedAt?: number; annotationCount: number; progress: number; favorite: boolean};
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
  const [arxivUrl, setArxivUrl] = useState('');
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
  const importArxiv = async () => {
    if (!arxivUrl.trim()) {setMessage('请粘贴 arXiv 论文链接'); return;}
    setUploading(true);
    setMessage('正在确认 arXiv 版本…');
    try {
      await finishImport(await fetch('/api/parser/api/documents/arxiv', {method: 'POST', headers: {'content-type': 'application/json'},
        body: JSON.stringify({url: arxivUrl.trim()})}));
    } catch (error) {setMessage(error instanceof Error ? error.message : 'arXiv 导入失败');}
    finally {setUploading(false);}
  };

  const toggleFavorite = async (paper: Paper) => {
    const response = await fetch(`/api/parser/api/documents/${paper.documentId}/favorite`, {method: 'PATCH', headers: {'content-type': 'application/json'}, body: JSON.stringify({favorite: !paper.favorite})});
    if (!response.ok) {setMessage('收藏状态保存失败'); return;}
    setPapers(current => current.map(item => item.documentId === paper.documentId ? {...item, favorite: !paper.favorite} : item));
  };
  const deletePaper = async (paper: Paper) => {
    if (!window.confirm(`删除《${paper.title}》及其笔记？此操作无法撤销。`)) return;
    const response = await fetch(`/api/parser/api/documents/${paper.documentId}`, {method: 'DELETE'});
    if (!response.ok) {setMessage('删除论文失败'); return;}
    setPapers(current => current.filter(item => item.documentId !== paper.documentId));
  };

  const recent = [...papers].filter(item => item.lastOpenedAt && item.status === 'ready').sort((a, b) => (b.lastOpenedAt || 0) - (a.lastOpenedAt || 0));
  const source = section === 'favorites' ? papers.filter(item => item.favorite) : section === 'recent' ? recent : papers;
  const filtered = source.filter(item => `${item.title} ${item.authors.join(' ')}`.toLowerCase().includes(query.trim().toLowerCase()));
  const open = (paper: Paper) => {
    if (paper.status !== 'ready') {setMessage(paper.status === 'failed' ? '这篇论文解析失败，请重新导入。' : '这篇论文仍在解析，请稍后刷新页面。'); return;}
    window.location.assign(`/reader/${paper.documentId}`);
  };
  return <div className="library-app">
    <header className="library-header"><a className="library-brand" href="/"><BookOpen size={22} />Paperlight</a><div className="library-header-actions"><AccountMenu user={user} onLogout={onLogout} /></div></header>
    <div className="library-layout"><nav className="library-sidebar" aria-label="论文库导航"><button className={section === 'all' ? 'selected' : ''} onClick={() => setSection('all')}><BookOpen size={18} />我的论文</button><button className={section === 'recent' ? 'selected' : ''} onClick={() => setSection('recent')}><Clock3 size={18} />最近阅读</button><button className={section === 'favorites' ? 'selected' : ''} onClick={() => setSection('favorites')}><Star size={18} />收藏</button></nav>
      <main className="library-main"><div className="library-welcome"><div><span className="library-eyebrow">你的论文库</span><h1>你好，{user.display_name}</h1><p>上传 PDF，或粘贴 arXiv 论文链接。</p></div><div className="library-import-panel"><button className="library-import" disabled={uploading} onClick={() => picker.current?.click()}><FilePlus2 size={18} />上传 PDF</button><input className="library-arxiv-input" type="url" aria-label="arXiv 论文链接" placeholder="https://arxiv.org/abs/2603.17965v1" value={arxivUrl} disabled={uploading} onChange={event => setArxivUrl(event.target.value)} onKeyDown={event => {if (event.key === 'Enter') void importArxiv();}} /><button className="library-import-secondary" disabled={uploading} onClick={() => void importArxiv()}>导入 arXiv</button></div></div>
        {message && <div className="library-message" role="status">{message}<button onClick={() => setMessage('')} aria-label="关闭提示">×</button></div>}
        {section === 'all' && !query && <section className="library-recent"><div className="library-section-heading"><h2>最近阅读</h2>{recent.length > 3 && <button onClick={() => setSection('recent')}>查看全部</button>}</div>{recent.length ? <div className="library-recent-grid">{recent.slice(0, 3).map(item => <button className="library-recent-card" key={item.documentId} onClick={() => open(item)}><span className="paper-cover"><BookOpen size={25} /></span><strong>{item.title}</strong><small>{item.authors.slice(0, 2).join(', ') || '未知作者'}</small><span className="paper-progress"><i style={{width: `${item.progress || 0}%`}} /></span><small>{Math.round(item.progress || 0)}% 已读 · {dateLabel(item.lastOpenedAt)}</small></button>)}</div> : <p className="library-empty">打开论文后，最近阅读会显示在这里。</p>}</section>}
        <section className="library-papers"><div className="library-section-heading"><div><h2>{section === 'all' ? '我的论文' : section === 'recent' ? '最近阅读' : '收藏'}</h2><small>{filtered.length} 篇论文</small></div><label className="library-search"><Search size={17} /><input aria-label="搜索论文" placeholder="搜索标题或作者" value={query} onChange={event => setQuery(event.target.value)} /></label></div>
          {loading ? <p className="library-empty">正在加载论文…</p> : filtered.length ? <div className="library-paper-list">{filtered.map(item => <div className="library-paper-row" key={item.documentId}><button className="library-paper-open" onClick={() => open(item)}><span className="paper-cover mini"><BookOpen size={20} /></span><span className="paper-details"><strong>{item.title}</strong><small>{item.authors.slice(0, 3).join(', ') || '未知作者'}</small><small>{item.status === 'ready' ? `${item.arxivId ? `${item.arxivId}v${item.arxivVersion} · ${item.parseSource === 'arxiv_html' ? '官方 HTML' : 'PDF'}` : `${item.pageCount} 页`} · ${item.annotationCount} 条标注 · ${Math.round(item.progress || 0)}% 已读 · ${dateLabel(item.lastOpenedAt)}` : item.status === 'failed' ? '解析失败' : '正在解析'}</small></span></button><div className="library-paper-actions"><button title={item.favorite ? '取消收藏' : '收藏'} aria-label={item.favorite ? `取消收藏 ${item.title}` : `收藏 ${item.title}`} onClick={() => void toggleFavorite(item)}><Star size={18} fill={item.favorite ? 'currentColor' : 'none'} /></button><button title="删除论文" aria-label={`删除 ${item.title}`} onClick={() => void deletePaper(item)}><Trash2 size={17} /></button></div></div>)}</div> : <p className="library-empty">{query ? '没有找到匹配的论文。' : section === 'favorites' ? '还没有收藏的论文。' : section === 'recent' ? '还没有最近阅读的论文。' : '还没有论文，上传 PDF 或导入 arXiv 链接开始阅读。'}</p>}
        </section></main></div><input ref={picker} hidden type="file" accept="application/pdf,.pdf" onChange={event => void importPdf(event.target.files?.[0])} />
  </div>;
}
