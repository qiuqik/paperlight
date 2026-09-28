'use client';

import {useEffect, useState} from 'react';
import {BookOpen} from 'lucide-react';
import AccountMenu from './AccountMenu';
import type {Account} from './AppShell';
import {usePreferences} from '@/lib/stores';

export default function SettingsShell({user, onLogout}: {user: Account; onLogout: () => Promise<void>}) {
  const prefs = usePreferences();
  const [ready, setReady] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => {
    let active = true;
    usePreferences.getState().reset();
    void fetch('/api/parser/api/settings', {cache: 'no-store'}).then(async response => {
      if (!response.ok) throw new Error('无法读取阅读设置');
      const settings = await response.json();
      if (active) prefs.set(settings);
    }).catch(error => {if (active) setMessage(error.message);}).finally(() => {if (active) setReady(true);});
    return () => {active = false;};
  }, [user.id]);
  useEffect(() => {
    if (!ready) return;
    const timer = setTimeout(() => {
      const {fontFamily, fontSize, lineHeight, contentWidth, theme, toolbarDock, activeColor, customApp, customPaper, customText, customAccent} = usePreferences.getState();
      void fetch('/api/parser/api/settings', {method: 'PUT', headers: {'content-type': 'application/json'}, body: JSON.stringify({fontFamily, fontSize, lineHeight, contentWidth, theme, toolbarDock, activeColor, customApp, customPaper, customText, customAccent})})
        .then(response => {if (!response.ok) setMessage('阅读设置保存失败'); else setMessage('阅读设置已自动保存');})
        .catch(() => setMessage('阅读设置保存失败'));
    }, 500);
    return () => clearTimeout(timer);
  }, [ready, prefs.fontFamily, prefs.fontSize, prefs.lineHeight, prefs.contentWidth, prefs.theme, prefs.toolbarDock, prefs.activeColor, prefs.customApp, prefs.customPaper, prefs.customText, prefs.customAccent]);
  return <div className="library-app"><header className="library-header"><a className="library-brand" href="/"><BookOpen size={22} />Paperlight</a><AccountMenu user={user} onLogout={onLogout} /></header>
    <main className="account-page"><a className="account-back" href="/">← 返回我的论文</a><h1>阅读设置</h1><div className="account-panel account-form"><label>字体<select value={prefs.fontFamily} onChange={event => prefs.set({fontFamily: event.target.value})}><option value="Georgia, serif">Georgia</option><option value="Arial, sans-serif">Arial</option><option value="'Times New Roman', serif">Times New Roman</option></select></label><label>字号 · {prefs.fontSize}px<input type="range" min="14" max="26" value={prefs.fontSize} onChange={event => prefs.set({fontSize: Number(event.target.value)})} /></label><label>行距 · {prefs.lineHeight.toFixed(1)}<input type="range" min="1.2" max="2.2" step="0.1" value={prefs.lineHeight} onChange={event => prefs.set({lineHeight: Number(event.target.value)})} /></label><label>阅读宽度 · {prefs.contentWidth}px<input type="range" min="600" max="1200" step="20" value={prefs.contentWidth} onChange={event => prefs.set({contentWidth: Number(event.target.value)})} /></label><label>主题<select value={prefs.theme} onChange={event => prefs.set({theme: event.target.value as typeof prefs.theme})}><option value="paper">纸张</option><option value="warm">暖色</option><option value="dark">深色</option><option value="custom">自定义</option></select></label>{prefs.theme === 'custom' && <div className="account-color-grid">{([['customApp', '界面背景'], ['customPaper', '纸张背景'], ['customText', '正文文字'], ['customAccent', '强调色']] as const).map(([key, label]) => <label key={key}>{label}<input type="color" value={prefs[key]} onChange={event => prefs.set({[key]: event.target.value})} /></label>)}</div>}<label>工具栏位置<select value={prefs.toolbarDock} onChange={event => prefs.set({toolbarDock: event.target.value as typeof prefs.toolbarDock})}><option value="top">顶部</option><option value="bottom">底部</option><option value="left">左侧</option><option value="right">右侧</option></select></label><p>设置会自动保存到账号，并在其他设备登录后恢复。</p>{message && <p role="status" className="account-message">{message}</p>}</div></main></div>;
}
