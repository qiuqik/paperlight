'use client';
import {useEffect, useRef, useState} from 'react';
import type {PublicationInfo} from '@/lib/document';
import styles from './PaperTagsEditor.module.css';

export type PaperTags = {venue: string; publishDate: string; institutions: string[]; other: string[]};
export function publicationTags(publication?: PublicationInfo): PaperTags {
  return {venue: publication?.venue_short || publication?.venue || '', publishDate: publication?.publish_time ? String(publication.publish_time).replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3') : '', institutions: publication?.institutions || [], other: []};
}
export function PaperLabels({tags}: {tags: PaperTags}) {
  return <span className="library-publication-tags" title={[tags.venue,tags.publishDate,...tags.institutions,...tags.other].filter(Boolean).join(' · ')}>{tags.venue && <span>{tags.venue}</span>}{tags.publishDate && <time>{tags.publishDate}</time>}{tags.institutions.map(value=><span key={`institution:${value}`}>{value}</span>)}{tags.other.map(value=><span key={`other:${value}`}>{value}</span>)}</span>;
}
export default function PaperTagsEditor({title,tags,publication,onCancel,onSave}: {title:string;tags:PaperTags;publication?:PublicationInfo;onCancel:()=>void;onSave:(tags:PaperTags)=>Promise<void>}) {
  const dialog=useRef<HTMLDialogElement>(null);
  const [venue,setVenue]=useState(tags.venue),[publishDate,setDate]=useState(tags.publishDate),[institutions,setInstitutions]=useState(tags.institutions.join('\n')),[other,setOther]=useState(tags.other.join('\n')),[busy,setBusy]=useState(false),[error,setError]=useState('');
  useEffect(()=>{const element=dialog.current;element?.showModal();return()=>element?.close();},[]);
  const reset=()=>{const source=publicationTags(publication);setVenue(source.venue);setDate(source.publishDate);setInstitutions(source.institutions.join('\n'));};
  const lines=(text:string)=>Array.from(new Set(text.split('\n').map(value=>value.trim()).filter(Boolean)));
  return <dialog ref={dialog} className={styles.dialog} aria-labelledby="paper-tags-title" onCancel={event=>{event.preventDefault();if(!busy)onCancel();}}>
    <form onSubmit={event=>{event.preventDefault();setBusy(true);setError('');void onSave({venue:venue.trim(),publishDate,institutions:lines(institutions),other:lines(other)}).catch(cause=>setError(cause instanceof Error?cause.message:'保存失败，请重试')).finally(()=>setBusy(false));}}>
      <h2 id="paper-tags-title">编辑标签</h2><p className={styles.title}>{title}</p><p className={styles.hint}>使用 DeepSeek 返回的信息，或填写你自己的标签。留空即可移除。</p>
      <fieldset disabled={busy}><label>期刊 / 会议<input value={venue} maxLength={120} placeholder="例如 TVCG2026" onChange={event=>setVenue(event.target.value)}/></label><label>发表日期<input type="date" value={publishDate} onChange={event=>setDate(event.target.value)}/></label><label>机构<textarea value={institutions} placeholder="每行一个机构" rows={3} onChange={event=>setInstitutions(event.target.value)}/></label><label>其他标签<textarea value={other} placeholder="每行一个标签，例如 可视化、待精读" rows={3} onChange={event=>setOther(event.target.value)}/></label></fieldset>
      {publication && <button className={styles.reset} type="button" disabled={busy} onClick={reset}>填入 DeepSeek 返回的信息</button>}{error&&<p role="alert" className={styles.error}>{error}</p>}
      <div className={styles.actions}><button type="button" disabled={busy} onClick={onCancel}>取消</button><button className={styles.save} disabled={busy} type="submit">{busy?'正在保存…':'保存标签'}</button></div>
    </form>
  </dialog>;
}
