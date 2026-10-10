'use client';
import {useEffect, type ReactNode} from 'react';
import {Minimize2, X} from 'lucide-react';
import type {Annotation} from '@/lib/document';

type Props = {children: ReactNode; note?: Annotation; onExit: () => void; onCloseNote: () => void; onChangeNote: (text: string) => void; onSaveNote: (text: string) => void};
export default function FocusModeTools({children, note, onExit, onCloseNote, onChangeNote, onSaveNote}: Props) {
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented || event.isComposing) return;
      event.preventDefault();
      if (note) {onSaveNote(note.note || ''); onCloseNote();} else onExit();
    };
    document.addEventListener('keydown', escape);
    return () => document.removeEventListener('keydown', escape);
  }, [note, onExit, onCloseNote, onSaveNote]);
  return <>
    <div className="focus-note-tools" role="region" aria-label="专注模式笔记工具">
      {children}
      <button type="button" className="focus-exit" aria-label="退出专注模式" title="退出专注模式（Esc）" onClick={onExit}><Minimize2 size={18} /></button>
    </div>
    {note && <section className="focus-note-editor" role="dialog" aria-label="编辑笔记">
      <div className="focus-note-heading"><strong>笔记</strong><button type="button" title="完成笔记" aria-label="完成笔记" onClick={() => {onSaveNote(note.note || ''); onCloseNote();}}><X size={18} /></button></div>
      <textarea key={note.id} autoFocus aria-label="笔记内容" placeholder="输入笔记…" value={note.note || ''} onChange={event => onChangeNote(event.target.value)} onBlur={event => onSaveNote(event.target.value)} />
    </section>}
  </>;
}