'use client';

import {readWorkspaceCache, writeWorkspaceCache, clearWorkspaceCache} from '@/lib/workspaceCache';
import {useEffect, useState} from 'react';
import LoginScreen from './LoginScreen';
import ReaderShell from './ReaderShell';
import Administration from './Administration';
import LibraryShell from './LibraryShell';
import ProfileShell from './ProfileShell';
import SettingsShell from './SettingsShell';

export type Account = {id: string; username: string; display_name: string; role: 'admin' | 'user'; created_at: number};

export default function AppShell({initialId, administration = false, view = 'library'}: {initialId?: string; administration?: boolean; view?: 'library' | 'profile' | 'settings'}) {
  const [user, setUser] = useState<Account | null>(() => readWorkspaceCache<Account>('account') || null);
  const [loading, setLoading] = useState(() => !readWorkspaceCache<Account>('account'));
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    void fetch('/api/parser/api/auth/me', {cache: 'no-store'}).then(async response => {
      if (!active) return;
      if (response.ok) {const account = await response.json() as Account; writeWorkspaceCache('account', account); setUser(account);}
      else if (response.status === 401 || response.status === 403) {clearWorkspaceCache(); setUser(null);}
    }).catch(() => {if (active) setError('连接服务器失败');})
      .finally(() => {if (active) setLoading(false);});
    return () => {active = false;};
  }, []);

  const logout = async () => {
    await fetch('/api/parser/api/auth/logout', {method: 'POST'});
    clearWorkspaceCache();
    setUser(null);
    history.replaceState({}, '', '/');
  };

  if (loading) return <main className="app-loading"><p>正在连接 Paperlight…</p></main>;
  if (!user) return <LoginScreen onLogin={account => {clearWorkspaceCache(); writeWorkspaceCache('account', account); setUser(account); window.scrollTo(0,0);}} initialError={error} />;
  if (administration) return user.role === 'admin'
    ? <Administration user={user} onLogout={logout} />
    : <main className="app-loading"><p>只有管理员可以访问此页面。</p><a href="/">返回阅读器</a></main>;
  if (initialId) return <ReaderShell initialId={initialId} user={user} onLogout={logout} />;
  if (view === 'profile') return <ProfileShell user={user} onUserChange={setUser} onLogout={logout} />;
  if (view === 'settings') return <SettingsShell user={user} onLogout={logout} />;
  return <LibraryShell user={user} onLogout={logout} />;
}
