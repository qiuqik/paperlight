'use client';
import {useEffect} from 'react';

export const localDay = (date = new Date()) => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;

export function useReadingActivity(documentId: string | undefined, userId: string) {
  useEffect(() => {
    if (!documentId) return;
    let last = performance.now(), lastInput = last, seconds = 0, day = localDay(), sending = false, active = true;
    const queue: Array<{eventId: string; documentId: string; day: string; seconds: number}> = [];
    const flush = async () => {
      if (seconds >= 1) {queue.push({eventId:crypto.randomUUID(),documentId,day,seconds:Math.min(60,Math.floor(seconds))}); seconds %= 1;}
      if (sending) return;
      sending = true;
      try {while (queue.length && active) {const response = await fetch('/api/parser/api/activity', {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(queue[0]),keepalive:true}); if (!response.ok) break; queue.shift();}}
      catch {} finally {sending = false;}
    };
    const tick = () => {
      const now = performance.now(), elapsed = (now-last)/1000; last = now;
      if (day !== localDay()) {void flush(); day = localDay();}
      if (!document.hidden && document.hasFocus() && now-lastInput < 120000 && elapsed < 10) seconds += elapsed;
      if (seconds >= 30) void flush();
    };
    const input = () => {lastInput = performance.now();};
    const visibility = () => {last = performance.now(); if (document.hidden) void flush(); else input();};
    const leave = () => {void flush();};
    const timer = setInterval(tick, 5000);
    const retry = setInterval(() => {if (queue.length) void flush();}, 30000);
    for (const event of ['pointerdown','pointermove','keydown','wheel','touchstart']) window.addEventListener(event,input,{passive:true});
    window.addEventListener('pagehide',leave); document.addEventListener('visibilitychange',visibility);
    return () => {clearInterval(timer);clearInterval(retry);void flush();active=false;for (const event of ['pointerdown','pointermove','keydown','wheel','touchstart']) window.removeEventListener(event,input);window.removeEventListener('pagehide',leave);document.removeEventListener('visibilitychange',visibility);};
  }, [documentId,userId]);
}
