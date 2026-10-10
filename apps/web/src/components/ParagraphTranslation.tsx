'use client';
import {createContext, useContext} from 'react';
import {ChevronDown, ChevronUp, Languages, LoaderCircle, RotateCcw} from 'lucide-react';
import type {TranslationController} from '@/lib/useDocumentTranslations';
import styles from './ParagraphTranslation.module.css';

export const TranslationContext = createContext<TranslationController | null>(null);

export function ParagraphTranslation({targetId}: {targetId: string}) {
  const controller = useContext(TranslationContext);
  if (!controller?.targets.has(targetId)) return null;
  const item = controller.state?.items[targetId];
  const pending = item?.status === 'pending' || item?.status === 'translating';
  const ready = item?.status === 'ready';
  const Icon = pending ? LoaderCircle : ready ? item.expanded ? ChevronUp : ChevronDown : Languages;
  const label = pending ? '翻译中…' : ready ? item.expanded ? '收起译文' : '展开译文' : item?.status === 'failed' ? '重试翻译' : '翻译';
  const translatedId = `translation-${targetId}`;
  return <div className={`${styles.unit} notranslate`} translate="no" data-translation-ui>
    <div className={styles.actions}><button type="button" title={label} aria-label={label} aria-expanded={ready ? item.expanded : undefined}
      aria-controls={ready ? translatedId : undefined} disabled={pending || controller.busy}
      onClick={() => void (ready ? controller.setExpanded(!item.expanded, targetId) : controller.translate(targetId))}>
      <Icon size={14} className={pending ? styles.spinning : undefined} /><span>{label}</span>
    </button></div>
    {item?.status === 'failed' && <div className={styles.error} role="status">{item.error}</div>}
    {ready && <div id={translatedId} className={styles.text} lang="zh-CN" hidden={!item.expanded}>{item.text}</div>}
  </div>;
}

export function TranslationToolbar({controller}: {controller: TranslationController}) {
  const {state, busy} = controller;
  return <div className={styles.toolbar}>
    {!state?.bulkRequested ? <button type="button" disabled={!state || !state.total || busy || !controller.enabled}
      title="用 DeepSeek 翻译全部标题和文本段落" aria-label="一键翻译" onClick={() => void controller.translateAll()}>
      <Languages size={17} /><span>一键翻译</span>
    </button> : <>
      <button type="button" title="全部展开译文" aria-label="全部展开译文" disabled={busy} onClick={() => void controller.setExpanded(true)}><ChevronDown size={16} /><span>全部展开</span></button>
      <button type="button" title="全部收起译文" aria-label="全部收起译文" disabled={busy} onClick={() => void controller.setExpanded(false)}><ChevronUp size={16} /><span>全部收起</span></button>
      {!state.processing && state.ready < state.total && <button type="button" title="继续翻译尚未完成的段落" aria-label="翻译未完成段落" disabled={busy} onClick={() => void controller.translateAll()}><RotateCcw size={16} /><span>翻译剩余</span></button>}
    </>}
    {state?.processing && <span className={styles.progress} role="status"><LoaderCircle size={13} className={styles.spinning} />{state.ready}/{state.total}</span>}
  </div>;
}