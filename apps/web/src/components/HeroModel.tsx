'use client';

import {createElement, useEffect, useRef, useState} from 'react';
import type {ModelViewerElement} from '@google/model-viewer';
import styles from './LoginScreen.module.css';

export default function HeroModel() {
  const viewer = useRef<ModelViewerElement>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');

  useEffect(() => {
    let active = true;
    const element = viewer.current;
    if (!element) return;
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const syncMotion = () => {
      element.autoRotate = !reducedMotion.matches;
      if (reducedMotion.matches) element.pause();
      else element.play();
    };
    const onInteract = () => {element.autoRotate = false;};
    const onReset = (event: MouseEvent) => {
      event.preventDefault();
      if (!element.loaded) return;
      element.cameraOrbit = '25deg 65deg auto';
      element.cameraTarget = 'auto auto auto';
      element.fieldOfView = '30deg';
      // Dragging changes camera goals without changing these properties.
      // Force their re-evaluation even when the declared value is unchanged.
      element.requestUpdate('cameraOrbit');
      element.requestUpdate('cameraTarget');
      element.requestUpdate('fieldOfView');
      element.resetTurntableRotation(0);
      element.jumpCameraToGoal();
      syncMotion();
    };
    const onLoad = () => {if (active) {setState('ready'); syncMotion();}};
    const onError = () => {if (active) setState('error');};
    element.addEventListener('load', onLoad);
    element.addEventListener('pointerdown', onInteract);
    element.addEventListener('wheel', onInteract, {passive: true});
    element.addEventListener('contextmenu', onReset);
    element.addEventListener('error', onError);
    reducedMotion.addEventListener('change', syncMotion);
    void import('@google/model-viewer').then(() => {
      if (active) syncMotion();
    }).catch(onError);
    return () => {
      active = false;
      element.removeEventListener('load', onLoad);
      element.removeEventListener('pointerdown', onInteract);
      element.removeEventListener('wheel', onInteract);
      element.removeEventListener('contextmenu', onReset);
      element.removeEventListener('error', onError);
      reducedMotion.removeEventListener('change', syncMotion);
      element.pause?.();
    };
  }, []);

  return <div className={styles.modelScene}>
    {createElement('model-viewer', {
      ref: viewer,
      className: styles.modelViewer,
      src: '/medieval__fantasy__book.glb',
      alt: '一本展开的奇幻书籍，书页上呈现立体场景',
      'camera-controls': '',
      'disable-pan': '',
      'touch-action': 'pan-y',
      'camera-orbit': '25deg 65deg auto',
      'field-of-view': '30deg',
      'rotation-per-second': '8deg',
      'auto-rotate-delay': '1000',
      'shadow-intensity': '1',
      'shadow-softness': '0.9',
      exposure: '1.1',
      loading: 'eager',
      reveal: 'auto',
      'interaction-prompt': 'none',
      title: '拖动旋转 · 滚轮缩放 · 右键还原',
    })}
    {state !== 'ready' ? <span className={styles.modelStatus} role="status">
      {state === 'error' ? '每一页，都有新的可能。' : '正在打开这本书…'}
    </span> : null}
    {state === 'ready' ? <span className={styles.modelHint}>拖动旋转 · 滚轮缩放 · 右键还原</span> : null}
    <a className={styles.modelCredit} href="https://sketchfab.com/3d-models/medieval--fantasy--book-17b2d17980f8489781de5d3abc930c94" target="_blank" rel="noreferrer">Medieval Fantasy Book · HiQ3D · CC BY 4.0</a>
  </div>;
}
