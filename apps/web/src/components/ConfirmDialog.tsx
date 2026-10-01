'use client';
import {useEffect, useRef, useState} from 'react';

export default function ConfirmDialog({title, message, onCancel, onConfirm}: {title: string; message: string; onCancel: () => void; onConfirm: () => Promise<void>}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {const element = dialog.current; element?.showModal(); return () => element?.close();}, []);
  return <dialog ref={dialog} className="confirm-dialog" onCancel={event => {event.preventDefault(); if (!busy) onCancel();}} aria-labelledby="confirmation-title">
    <h2 id="confirmation-title">{title}</h2><p>{message}</p>{error && <p role="alert">{error}</p>}
    <div className="confirm-actions"><button autoFocus disabled={busy} onClick={onCancel}>取消</button><button className="danger-button" disabled={busy} onClick={() => {setBusy(true); void onConfirm().catch(() => setError('操作失败，请重试。')).finally(() => setBusy(false));}}>{busy ? '正在删除…' : '删除论文'}</button></div>
  </dialog>;
}
