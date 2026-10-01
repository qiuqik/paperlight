'use client';
import {useEffect, useState} from 'react';
import {Clock3, CalendarDays, ArrowUpRight} from 'lucide-react';
import {localDay} from '@/lib/useReadingActivity';
import styles from './ReadingActivity.module.css';

type Summary = {totalSeconds: number; activeDays: number; days: Array<{date: string; seconds: number}>};
const duration = (seconds: number) => seconds < 60 ? `${Math.floor(seconds)} 秒` : seconds < 3600 ? `${Math.floor(seconds/60)} 分钟` : `${(seconds/3600).toFixed(1)} 小时`;
export default function ReadingActivity({userId}: {userId: string}) {
  const [data,setData] = useState<Summary | null>(null), [error,setError] = useState(false), [selected,setSelected] = useState<string | null>(null);
  useEffect(() => {let active=true;void fetch(`/api/parser/api/activity?today=${localDay()}`,{cache:'no-store'}).then(async response=>{if(!response.ok)throw new Error();const result=await response.json() as Summary;if(active)setData(result);}).catch(()=>{if(active)setError(true);});return()=>{active=false;};},[userId]);
  const days = data?.days || [], today = days.at(-1)?.seconds || 0, week = days.slice(-7).reduce((sum,day)=>sum+day.seconds,0);
  const chosen = days.find(day=>day.date===selected);
  const padding = days.length ? (new Date(`${days[0].date}T12:00:00`).getDay()+6)%7 : 0;
  return <section className={styles.panel} aria-label="阅读时间">
    <div className={styles.heading}><div><span className={styles.eyebrow}>YOUR READING RHYTHM</span><h2>每一页，都算数。</h2></div><span className={styles.period}><CalendarDays size={14} />近 12 周</span></div>
    <div className={styles.body}><div className={styles.metrics}>
      <div><span><Clock3 size={13} />今天阅读</span><strong>{data ? duration(today) : '—'}</strong></div>
      <div><span>最近 7 天</span><strong>{data ? duration(week) : '—'}</strong></div>
      <div><span>累计阅读</span><strong>{data ? duration(data.totalSeconds) : '—'}</strong></div>
      <div><span>阅读天数</span><strong>{data ? data.activeDays : '—'}<small> 天</small></strong></div>
    </div><div className={styles.calendar}><div className={styles.weekdays}><span>一</span><span>三</span><span>五</span></div><div className={styles.cells} aria-label="每日阅读时长">
      {Array.from({length:padding},(_,index)=><span key={`padding-${index}`} aria-hidden="true"/>)}{days.map(day=><button key={day.date} className={selected===day.date?styles.selected:''} data-level={day.seconds===0?0:day.seconds<300?1:day.seconds<1200?2:day.seconds<3600?3:4} aria-label={`${day.date}，阅读 ${duration(day.seconds)}`} title={`${day.date} · ${duration(day.seconds)}`} onClick={()=>setSelected(day.date)} />)}
    </div><div className={styles.legend}><span>{days[0]?.date.slice(5)} — {days.at(-1)?.date.slice(5)}</span><span>少 <i data-level="0"/><i data-level="1"/><i data-level="2"/><i data-level="3"/><i data-level="4"/> 多</span></div></div></div>
    <p className={styles.caption}>{error ? '阅读记录暂时无法加载，请刷新重试。' : chosen ? `${chosen.date} · 阅读 ${duration(chosen.seconds)}` : data?.totalSeconds ? '留一点时间，读一篇好论文。' : '从今天开始，留下你的阅读足迹。'}<ArrowUpRight size={14}/></p>
  </section>;
}
