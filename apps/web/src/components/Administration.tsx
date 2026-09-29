'use client';

import {useCallback, useEffect, useState} from 'react';
import type {Account} from './AppShell';
import AccountMenu from './AccountMenu';

type UserRow = Account & {disabled: number; document_count: number; last_login_at: number | null};
type Paper = {documentId: string; title: string; pageCount: number; status: string};

export default function Administration({user, onLogout}: {user: Account; onLogout: () => Promise<void>}) {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [selected, setSelected] = useState<UserRow | null>(null);
  const [documents, setDocuments] = useState<Paper[]>([]);
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newRole, setNewRole] = useState<'user' | 'admin'>('user');
  const [displayName, setDisplayName] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState('');

  const loadUsers = useCallback(async () => {
    const response = await fetch('/api/parser/api/admin/users', {cache: 'no-store'});
    if (!response.ok) throw new Error('无法读取用户列表');
    setUsers(await response.json() as UserRow[]);
  }, []);
  useEffect(() => {void loadUsers().catch(error => setMessage(error.message));}, [loadUsers]);

  const selectUser = async (item: UserRow) => {
    setSelected(item); setUsername(item.username); setDisplayName(item.display_name); setPassword(''); setMessage('');
    const response = await fetch(`/api/parser/api/documents?ownerId=${encodeURIComponent(item.id)}`, {cache: 'no-store'});
    if (response.ok) setDocuments(await response.json() as Paper[]);
    else setMessage('无法读取该用户的论文');
  };

  const createUser = async (event: React.FormEvent) => {
    event.preventDefault(); setMessage('');
    const response = await fetch('/api/parser/api/admin/users', {method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({username: newUsername, password: newPassword, role: newRole})});
    if (!response.ok) {setMessage(response.status === 409 ? '用户名已存在' : '创建用户失败：密码至少 4 位'); return;}
    setNewUsername(''); setNewPassword(''); setNewRole('user');
    await loadUsers(); setMessage('用户已创建');
  };

  const saveUser = async () => {
    if (!selected) return;
    setMessage('');
    const changes: Record<string, unknown> = {username, displayName};
    if (password) changes.password = password;
    const response = await fetch(`/api/parser/api/admin/users/${selected.id}`, {method: 'PATCH', headers: {'content-type': 'application/json'},
      body: JSON.stringify(changes)});
    if (!response.ok) {setMessage(response.status === 409 ? '用户名已存在' : '保存用户失败'); return;}
    setSelected(await response.json() as UserRow); setPassword(''); await loadUsers(); setMessage('已保存');
  };

  const setDisabled = async (disabled: boolean) => {
    if (!selected || selected.id === user.id) return;
    const response = await fetch(`/api/parser/api/admin/users/${selected.id}`, {method: 'PATCH', headers: {'content-type': 'application/json'},
      body: JSON.stringify({disabled})});
    if (!response.ok) {setMessage('更新账户状态失败'); return;}
    setSelected({...selected, disabled: Number(disabled)}); await loadUsers();
  };

  const deleteUser = async () => {
    if (!selected || selected.id === user.id || !window.confirm(`彻底删除 ${selected.username} 及其所有论文和笔记？此操作无法撤销。`)) return;
    const response = await fetch(`/api/parser/api/admin/users/${selected.id}`, {method: 'DELETE'});
    if (!response.ok) {setMessage('删除用户失败，请检查是否有论文仍在解析'); return;}
    setSelected(null); setDocuments([]); await loadUsers(); setMessage('账户及数据已删除');
  };

  const deleteDocument = async (item: Paper) => {
    if (!selected || !window.confirm(`删除 ${selected.username} 的论文「${item.title}」及其笔记？`)) return;
    const response = await fetch(`/api/parser/api/documents/${item.documentId}`, {method: 'DELETE'});
    if (!response.ok) {setMessage('删除论文失败，可能仍在解析'); return;}
    setDocuments(current => current.filter(record => record.documentId !== item.documentId));
    await loadUsers(); setMessage('论文已删除');
  };

  return <main className="admin-page"><header><a href="/">Paperlight · 我的论文</a><span>管理后台</span><AccountMenu user={user} onLogout={onLogout} /></header>
    <div className="admin-content"><section><h1>用户管理</h1><div className="admin-users">{users.map(item => <button key={item.id} className={selected?.id === item.id ? 'selected' : ''} onClick={() => void selectUser(item)}>
      <strong>{item.username}</strong><span>{item.role === 'admin' ? '管理员' : '用户'} · {item.document_count} 篇{item.disabled ? ' · 已停用' : ''}</span>
    </button>)}</div><form className="admin-create" onSubmit={event => void createUser(event)}><h2>创建用户</h2>
      <input aria-label="新用户名" placeholder="用户名" value={newUsername} onChange={event => setNewUsername(event.target.value)} required />
      <input aria-label="初始密码" type="password" placeholder="初始密码（至少 4 位，可用纯数字）" value={newPassword} onChange={event => setNewPassword(event.target.value)} required minLength={4} />
      <select aria-label="角色" value={newRole} onChange={event => setNewRole(event.target.value as 'admin' | 'user')}><option value="user">普通用户</option><option value="admin">管理员</option></select>
      <button type="submit">创建</button></form></section>
    <section>{selected ? <><h2>{selected.username}</h2><div className="admin-edit"><label>用户名<input value={username} onChange={event => setUsername(event.target.value)} /></label>
      <label>显示名称<input value={displayName} onChange={event => setDisplayName(event.target.value)} /></label>
      <label>重置密码<input type="password" value={password} minLength={4} onChange={event => setPassword(event.target.value)} placeholder="留空则不修改；至少 4 位" /></label>
      <button onClick={() => void saveUser()}>保存修改</button>{selected.id !== user.id && <><button onClick={() => void setDisabled(!selected.disabled)}>{selected.disabled ? '启用账户' : '停用账户'}</button><button className="danger" onClick={() => void deleteUser()}>删除账户及数据</button></>}</div>
      <h3>论文</h3><div className="admin-documents">{documents.length ? documents.map(item => <div className="admin-document-row" key={item.documentId}><a href={`/reader/${item.documentId}`}>{item.title}<small>{item.pageCount} 页 · {item.status}</small></a><button aria-label={`删除 ${item.title}`} onClick={() => void deleteDocument(item)}>删除</button></div>) : <p>暂无论文</p>}</div></> : <p>选择用户，查看账户与论文。</p>}</section></div>
    {message && <p className="admin-message" role="status">{message}</p>}
  </main>;
}
