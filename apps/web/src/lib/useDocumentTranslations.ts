'use client';
import {useCallback, useEffect, useMemo, useRef, useState} from 'react';

export type TranslationItem = {status: 'pending' | 'translating' | 'ready' | 'failed'; text: string; expanded: boolean; error?: string};
export type TranslationState = {revision?: number; items: Record<string, TranslationItem>; targetIds: string[]; total: number; ready: number; failed: number; processing: boolean; bulkRequested: boolean; available: boolean};
export function useDocumentTranslations(documentId?: string) {
  const [saved, setSaved] = useState<{id: string; data: TranslationState} | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const currentId = useRef(documentId);
  currentId.current = documentId;
  const mutation = useRef(false);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const state = saved && saved.id === documentId ? saved.data : null;
  const targets = useMemo(() => new Set(state?.targetIds || []), [state?.targetIds]);

  useEffect(() => {
    if (!documentId) return;
    let active = true;
    const abort = new AbortController();
    setError('');
    const load = async () => {
      try {
        const response = await fetch(`/api/parser/api/documents/${documentId}/translations`, {cache: 'no-store', signal: abort.signal});
        if (!response.ok) throw new Error('翻译记录读取失败，请重新打开论文。');
        const data = await response.json() as TranslationState;
        if (active) {
          if (!mutation.current) setSaved(previous => previous?.id === documentId && (previous.data.revision || 0) > (data.revision || 0) ? previous : {id: documentId, data});
          setError('');
          if (data.processing || mutation.current) pollTimer.current = setTimeout(load, 1500);
        }
      } catch (failure) {
        if (active) {setError(failure instanceof Error ? failure.message : '翻译记录读取失败'); pollTimer.current = setTimeout(load, 5000);}
      }
    };
    void load();
    return () => {active = false; abort.abort(); clearTimeout(pollTimer.current);};
  }, [documentId, state?.processing]);

  const send = useCallback(async (method: 'POST' | 'PATCH', body: object) => {
    if (!documentId || mutation.current) return;
    mutation.current = true;
    setBusy(true);
    setError('');
    try {
      const response = await fetch(`/api/parser/api/documents/${documentId}/translations`, {
        method, headers: {'content-type': 'application/json'}, body: JSON.stringify(body),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '翻译请求失败，请稍后重试。');
      if (currentId.current === documentId) setSaved({id: documentId, data: data as TranslationState});
    } catch (failure) {
      if (currentId.current === documentId) setError(failure instanceof Error ? failure.message : '翻译请求失败');
    } finally {
      mutation.current = false;
      setBusy(false);
    }
  }, [documentId]);
  return {state, targets, busy, error, enabled: !!documentId,
    translate: (targetId: string) => send('POST', {targetId}),
    translateAll: () => send('POST', {all: true}),
    setExpanded: (expanded: boolean, targetId?: string) => send('PATCH', {expanded, ...(targetId ? {targetId} : {})}),
  };
}
export type TranslationController = ReturnType<typeof useDocumentTranslations>;