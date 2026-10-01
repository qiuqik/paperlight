'use client';

import {useEffect, useState} from 'react';
import {ExternalLink, RefreshCw} from 'lucide-react';
import type {DocumentModel, PublicationInfo} from '@/lib/document';

type Lookup = {status: 'missing' | 'processing' | 'ready' | 'failed'; available?: boolean; information?: PublicationInfo};
const statusLabels = {published: '已发表', accepted: '已接收', preprint: '预印本', unknown: '发表状态待确认'};

export default function PaperIdentity({paper}: {paper: DocumentModel}) {
  const [info, setInfo] = useState(paper.metadata.publication);
  const [status, setStatus] = useState<Lookup['status']>(info ? 'ready' : 'missing');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!/^[a-f0-9]{32}$/.test(paper.id) || !paper.source) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let polls = 0;
    const endpoint = `/api/parser/api/documents/${paper.id}/publication`;
    const read = async (start = false) => {
      try {
        let response = await fetch(endpoint, {signal: controller.signal, cache: 'no-store'});
        if (!response.ok) return;
        let state = await response.json() as Lookup;
        if ((state.status === 'missing' && state.available) || start && state.status === 'failed' || state.status === 'processing' && polls === 0) {
          response = await fetch(endpoint, {method: 'POST', signal: controller.signal});
          if (!response.ok) return;
          state = await response.json() as Lookup;
        }
        if (controller.signal.aborted) return;
        setStatus(state.status);
        if (state.status === 'ready' && state.information) setInfo(state.information);
        if (state.status === 'processing' && polls++ < 65) timer = setTimeout(() => void read(), 2000);
      } catch {if (!controller.signal.aborted) setStatus('failed');}
    };
    void read(attempt > 0);
    return () => {controller.abort(); clearTimeout(timer);};
  }, [paper.id, attempt]);
  const authors = info?.authors.length ? info.authors : paper.metadata.authors;
  const institutions = info ? info.institutions : paper.metadata.affiliations || [];
  const date = info?.publish_time ? String(info.publish_time).replace(/^(\d{4})(\d{2})(\d{2})$/, '$1/$2/$3') : '';
  return <div className="paper-authors"><p className="authors">{authors.join(' · ')}</p>{institutions.length > 0 && <p className="author-detail">{institutions.join(' · ')}</p>}
    {info && <div className="publication-line"><span className={`publication-badge ${info.publication_status}`}>{statusLabels[info.publication_status]}</span>{info.venue && <span>{info.venue}</span>}{date && <time>{date}</time>}{info.publication_source_url && <a href={info.publication_source_url} target="_blank" rel="noreferrer" title="论文信息来源">来源<ExternalLink size={11} /></a>}</div>}
    {info?.keywords.length ? <div className="paper-keywords">{info.keywords.slice(0, 5).map(keyword => <span key={keyword}>{keyword}</span>)}</div> : null}
    {status === 'processing' && <p className="publication-status" role="status">正在核实论文信息…</p>}{status === 'failed' && <button type="button" className="publication-retry" onClick={() => setAttempt(value => value + 1)}><RefreshCw size={12} />重新查询论文信息</button>}
  </div>;
}
