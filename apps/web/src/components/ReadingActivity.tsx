'use client';
import {readWorkspaceCache, writeWorkspaceCache} from '@/lib/workspaceCache';
import {useEffect, useState} from 'react';
import {Clock3, ChartColumn, ArrowUpRight} from 'lucide-react';
import {localDay} from '@/lib/useReadingActivity';
import styles from './ReadingActivity.module.css';

type Summary = {totalSeconds: number; activeDays: number; days: Array<{date: string; seconds: number}>};
const duration = (seconds: number) => seconds < 60 ? `${Math.floor(seconds)} 秒` : seconds < 3600 ? `${Math.floor(seconds/60)} 分钟` : `${(seconds/3600).toFixed(1)} 小时`;
export default function ReadingActivity({userId}: {userId: string}) {
  const [data,setData] = useState<Summary | null>(() => readWorkspaceCache<Summary>(`activity:${userId}:${localDay()}`) || null), [error,setError] = useState(false), [selected,setSelected] = useState<string | null>(null);
  useEffect(() => {let active=true;void fetch(`/api/parser/api/activity?today=${localDay()}`,{cache:'no-store'}).then(async response=>{if(!response.ok)throw new Error();const result=await response.json() as Summary;if(active){writeWorkspaceCache(`activity:${userId}:${localDay()}`,result);setData(result);}}).catch(()=>{if(active)setError(true);});return()=>{active=false;};},[userId]);
  const days = data?.days || [], today = days.at(-1)?.seconds || 0, week = days.slice(-7).reduce((sum,day)=>sum+day.seconds,0);
  const [range,setRange] = useState(30);
  const [hovered,setHovered] = useState<string | null>(null);
  const visible = days.slice(-range);
  const total = visible.reduce((sum,day)=>sum+day.seconds,0);
  const maximum = Math.max(60,...visible.map(day=>day.seconds));
  const activeDate = hovered || selected || visible.at(-1)?.date;
  const chosen = visible.find(day=>day.date===activeDate);

  return <section className={styles.panel} aria-label="阅读时间">
    <div className={styles.heading}><div><span className={styles.eyebrow}>YOUR READING RHYTHM</span><h2>每一页，都算数。</h2></div><span className={styles.period}><ChartColumn size={15} />阅读概览</span></div>
    <div className={styles.body}><div className={styles.metrics}>
      <div><span><Clock3 size={13} />今天阅读</span><strong>{data ? duration(today) : '—'}</strong></div>
      <div><span>最近 7 天</span><strong>{data ? duration(week) : '—'}</strong></div>
      <div><span>累计阅读</span><strong>{data ? duration(data.totalSeconds) : '—'}</strong></div>
      <div><span>阅读天数</span><strong>{data ? data.activeDays : '—'}<small> 天</small></strong></div>
    </div></div>
    <div className={styles.chartHeading}><div><span>阅读时长</span><strong>{data ? duration(total) : '—'}</strong><small>{range === 84 ? '近 12 周' : `近 ${range} 天`}累计</small></div><div className={styles.range} aria-label="阅读统计范围">{[{days:7,label:'近 7 天'},{days:30,label:'近 30 天'},{days:84,label:'近 12 周'}].map(option=><button key={option.days} aria-pressed={range===option.days} onClick={()=>{setRange(option.days);setSelected(null);setHovered(null);}}>{option.label}</button>)}</div></div>
    <div className={styles.chartScroll}><div className={styles.chart} data-range={range} role="group" aria-label="每日阅读时长柱状图">
      <div className={styles.gridLines} aria-hidden="true"><span/><span/><span/></div>
      {visible.map((day,index)=><div className={styles.column} key={day.date}>
        <div className={styles.barArea}>
          {activeDate===day.date && <div className={styles.tooltip} role="status"><span>{day.date}</span><strong>阅读 {duration(day.seconds)}</strong></div>}
          <button className={`${styles.bar} ${activeDate===day.date?styles.selected:''}`} data-empty={day.seconds===0} style={{height:day.seconds ? `${Math.max(1,day.seconds/maximum*100)}%` : '2px'}} aria-label={`${day.date}，阅读 ${duration(day.seconds)}`} aria-pressed={selected===day.date} onMouseEnter={()=>setHovered(day.date)} onMouseLeave={()=>setHovered(null)} onFocus={()=>setHovered(day.date)} onBlur={()=>setHovered(null)} onClick={()=>setSelected(current=>current===day.date?null:day.date)} />
        </div>
        <span className={styles.date}>{range===7 ? day.date.slice(5) : range===30 ? Number(day.date.slice(-2)) : (index%7===0 || index===visible.length-1) ? day.date.slice(5) : ''}</span>
      </div>)}
      {!data && <p className={styles.chartEmpty}>{error ? '暂时无法加载阅读记录' : '正在加载阅读记录…'}</p>}
    </div></div>
    <p className={styles.caption}>{error ? '阅读记录暂时无法加载，请刷新重试。' : chosen ? `${chosen.date} · 阅读 ${duration(chosen.seconds)}` : data?.totalSeconds ? '留一点时间，读一篇好论文。' : '从今天开始，留下你的阅读足迹。'}<ArrowUpRight size={14}/></p>
  </section>;
}
