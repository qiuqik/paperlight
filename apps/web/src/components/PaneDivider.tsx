'use client';
import {useRef} from 'react';

export default function PaneDivider({rightPercent, onChange}: {rightPercent: number | null; onChange: (value: number) => void}) {
  const dragging = useRef(false);
  const change = (element: HTMLElement, x: number) => {const bounds = element.parentElement!.getBoundingClientRect(); onChange(Math.max(25, Math.min(75, (bounds.right - x) / bounds.width * 100)));};
  return <div className="pane-divider" role="separator" aria-label="调整 PDF 与网页宽度" aria-orientation="vertical" aria-valuemin={25} aria-valuemax={75} aria-valuenow={rightPercent === null ? undefined : Math.round(rightPercent)} tabIndex={0}
    onPointerDown={event => {dragging.current = true; event.currentTarget.setPointerCapture(event.pointerId); event.preventDefault();}}
    onPointerMove={event => {if (dragging.current) change(event.currentTarget, event.clientX);}}
    onPointerUp={event => {dragging.current = false; event.currentTarget.releasePointerCapture(event.pointerId);}}
    onPointerCancel={() => {dragging.current = false;}}
    onKeyDown={event => {if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {event.preventDefault(); onChange(Math.max(25, Math.min(75, (rightPercent ?? ((event.currentTarget.parentElement?.querySelector<HTMLElement>('.reader-scroll')?.clientWidth || 0) / (event.currentTarget.parentElement?.clientWidth || 1) * 100)) + (event.key === 'ArrowLeft' ? 2 : -2))));}}}><span /></div>;
}
