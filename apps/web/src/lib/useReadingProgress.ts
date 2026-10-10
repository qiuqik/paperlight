'use client';
import {useEffect, useLayoutEffect, useRef, type RefObject} from 'react';
import {trackReadingPosition} from './readingProgress';
export function useReadingProgress(root: RefObject<HTMLElement | null>, userId: string, documentId: string | undefined, ready: boolean, layoutKey: unknown[], onProgress: (value: number) => void, onError: () => void) {
  const controller = useRef<ReturnType<typeof trackReadingPosition> | null>(null);
  const callbacks = useRef({onProgress, onError}); callbacks.current = {onProgress, onError};
  useEffect(() => {
    if (!root.current || !documentId || !ready) return;
    const current = trackReadingPosition(root.current, userId, documentId, value => callbacks.current.onProgress(value), () => callbacks.current.onError());
    controller.current = current;
    return () => {current.dispose(); controller.current = null;};
  }, [root, userId, documentId, ready]);
  useLayoutEffect(() => {controller.current?.layoutChanged();}, layoutKey);
}
