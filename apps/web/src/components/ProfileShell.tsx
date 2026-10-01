'use client';

import {useState} from 'react';
import WorkspaceFrame, {AccountNavigation} from './WorkspaceFrame';
import type {Account} from './AppShell';

export default function ProfileShell({user, onUserChange, onLogout}: {user: Account; onUserChange: (user: Account) => void; onLogout: () => Promise<void>}) {
  const [name, setName] = useState(user.display_name);
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const saveProfile = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setMessage('');
    try {
      const response = await fetch('/api/parser/api/profile', {method: 'PATCH', headers: {'content-type': 'application/json'}, body: JSON.stringify({displayName: name})});
      if (!response.ok) throw new Error('保存个人资料失败');
      onUserChange(await response.json() as Account); setMessage('个人资料已保存');
    } catch (error) {setMessage(error instanceof Error ? error.message : '保存失败');}
    finally {setBusy(false);}
  };
  const savePassword = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setMessage('');
    try {
      const response = await fetch('/api/parser/api/profile/password', {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({currentPassword, newPassword})});
      if (!response.ok) throw new Error(response.status === 401 ? '当前密码不正确' : '修改密码失败；新密码至少需要 4 位');
      setCurrentPassword(''); setNewPassword('');
      window.alert('密码已修改，请重新登录。');
      await onLogout();
    } catch (error) {setMessage(error instanceof Error ? error.message : '修改密码失败');}
    finally {setBusy(false);}
  };
  return <WorkspaceFrame user={user} onLogout={onLogout} sidebar={<AccountNavigation active="profile" />}>
    <div className="profile-page"><h1>个人资料</h1>
      <form className="profile-card" onSubmit={event => void saveProfile(event)}><div className="profile-identity"><span className="account-avatar large">{user.display_name.slice(0, 1).toUpperCase()}</span><div><strong>{user.display_name}</strong><p>@{user.username}</p></div></div><h2>基本信息</h2><dl className="profile-facts"><div><dt>用户名</dt><dd>{user.username}</dd></div><div><dt><label htmlFor="display-name">显示名称</label></dt><dd><input id="display-name" value={name} maxLength={80} onChange={event => setName(event.target.value)} required /></dd></div><div><dt>角色</dt><dd>{user.role === 'admin' ? '管理员' : '普通用户'}</dd></div><div><dt>加入时间</dt><dd>{new Date(user.created_at * 1000).toLocaleDateString('zh-CN')}</dd></div></dl><button className="primary-button" disabled={busy} type="submit">保存资料</button></form>
      <form id="password" className="account-panel account-form" onSubmit={event => void savePassword(event)}><h2>修改密码</h2><label>当前密码<input type="password" autoComplete="current-password" value={currentPassword} onChange={event => setCurrentPassword(event.target.value)} required /></label><label>新密码<input type="password" autoComplete="new-password" minLength={4} value={newPassword} onChange={event => setNewPassword(event.target.value)} required /></label><p>至少 4 位，可以使用纯数字。修改后需要重新登录。</p><button disabled={busy} type="submit">修改密码</button></form>
      {message && <p role="status" className="account-message">{message}</p>}</div></WorkspaceFrame>;
}
