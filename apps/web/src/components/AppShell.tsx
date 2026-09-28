'use client';

import {useEffect, useState} from 'react';
import {Eye, EyeOff} from 'lucide-react';
import ReaderShell from './ReaderShell';
import Administration from './Administration';
import LibraryShell from './LibraryShell';
import ProfileShell from './ProfileShell';
import SettingsShell from './SettingsShell';

export type Account = {id: string; username: string; display_name: string; role: 'admin' | 'user'; created_at: number};

export default function AppShell({initialId, administration = false, view = 'library'}: {initialId?: string; administration?: boolean; view?: 'library' | 'profile' | 'settings'}) {
  const [user, setUser] = useState<Account | null>(null);
  const [loading, setLoading] = useState(true);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    void fetch('/api/parser/api/auth/me', {cache: 'no-store'}).then(async response => {
      if (active && response.ok) setUser(await response.json() as Account);
    }).catch(() => {if (active) setError('连接服务器失败');})
      .finally(() => {if (active) setLoading(false);});
    return () => {active = false;};
  }, []);

  const login = async (event: React.FormEvent) => {
    event.preventDefault();
    setError('');
    try {
      const response = await fetch('/api/parser/api/auth/login', {method: 'POST', headers: {'content-type': 'application/json'},
        body: JSON.stringify({username, password})});
      if (!response.ok) throw new Error(response.status === 401 ? '用户名或密码错误' : response.status === 403 ? '登录请求被拦截，请刷新页面后重试' : `登录失败 (${response.status})`);
      setPassword('');
      setUser(await response.json() as Account);
    } catch (cause) {setError(cause instanceof Error ? cause.message : '登录失败');}
  };

  const logout = async () => {
    await fetch('/api/parser/api/auth/logout', {method: 'POST'});
    setUser(null);
    setPassword('');
    history.replaceState({}, '', '/');
  };

  if (loading) return <main className="auth-page"><p>正在连接 Paperlight…</p></main>;
  if (!user) return <main className="auth-page"><form className="auth-card" onSubmit={event => void login(event)}>
    <h1>Paperlight</h1><p>登录后继续阅读你的论文与笔记</p>
    <label>用户名<input autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} required /></label>
    <label>密码<span className="auth-password-control"><input type={showPassword ? 'text' : 'password'} autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} required /><button type="button" className="auth-password-toggle" title={showPassword ? '隐藏密码' : '显示密码'} aria-label={showPassword ? '隐藏密码' : '显示密码'} aria-pressed={showPassword} onClick={() => setShowPassword(value => !value)}>{showPassword ? <EyeOff size={18} /> : <Eye size={18} />}</button></span></label>
    {error && <span className="auth-error" role="alert">{error}</span>}
    <button type="submit">登录</button>
  </form></main>;
  if (administration) return user.role === 'admin'
    ? <Administration user={user} onLogout={logout} />
    : <main className="auth-page"><p>只有管理员可以访问此页面。</p><a href="/">返回阅读器</a></main>;
  if (initialId) return <ReaderShell initialId={initialId} user={user} onLogout={logout} />;
  if (view === 'profile') return <ProfileShell user={user} onUserChange={setUser} onLogout={logout} />;
  if (view === 'settings') return <SettingsShell user={user} onLogout={logout} />;
  return <LibraryShell user={user} onLogout={logout} />;
}
