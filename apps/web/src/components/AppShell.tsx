'use client';

import {useEffect, useState} from 'react';
import {BookOpen, Eye, EyeOff, LockKeyhole, UserRound} from 'lucide-react';
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
  if (!user) return <main className="auth-page"><div className="auth-scene"><section className="auth-introduction"><BookOpen size={42} strokeWidth={1.7} /><h1>Paperlight</h1><h2>更好的学术论文阅读体验</h2><p>Read. Annotate. Organize.<br />Understand.</p><div className="book-illustration" aria-hidden="true"><span className="book-leaf leaf-one" /><span className="book-leaf leaf-two" /><div className="book-page page-left"><i /><i /><i /><i /><i /></div><div className="book-page page-right"><i /><i /><i /><i /><i /></div></div></section><form className="auth-card" onSubmit={event => void login(event)}>
    <div className="auth-tabs"><span>登录</span><small>欢迎回来</small></div><h2>继续你的阅读</h2><p>登录后打开论文与笔记</p>
    <label className="auth-field"><span className="sr-only">用户名</span><UserRound size={17} /><input placeholder="用户名" autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} required /></label>
    <label className="auth-field auth-password-control"><span className="sr-only">密码</span><LockKeyhole size={17} /><input placeholder="密码" type={showPassword ? 'text' : 'password'} autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} required /><button type="button" className="auth-password-toggle" title={showPassword ? '隐藏密码' : '显示密码'} aria-label={showPassword ? '隐藏密码' : '显示密码'} aria-pressed={showPassword} onClick={() => setShowPassword(value => !value)}>{showPassword ? <EyeOff size={18} /> : <Eye size={18} />}</button></label>
    {error && <span className="auth-error" role="alert">{error}</span>}
    <button type="submit">登录</button>
    <small className="auth-footnote">账号由管理员创建，如需帮助请联系管理员。</small>
  </form></div></main>;
  if (administration) return user.role === 'admin'
    ? <Administration user={user} onLogout={logout} />
    : <main className="auth-page"><p>只有管理员可以访问此页面。</p><a href="/">返回阅读器</a></main>;
  if (initialId) return <ReaderShell initialId={initialId} user={user} onLogout={logout} />;
  if (view === 'profile') return <ProfileShell user={user} onUserChange={setUser} onLogout={logout} />;
  if (view === 'settings') return <SettingsShell user={user} onLogout={logout} />;
  return <LibraryShell user={user} onLogout={logout} />;
}
