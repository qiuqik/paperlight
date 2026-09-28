'use client';

import {useEffect, useRef, useState} from 'react';
import {ChevronDown, LogOut, Settings2, Shield, UserRound} from 'lucide-react';
import type {Account} from './AppShell';

export default function AccountMenu({user, onLogout}: {user: Account; onLogout: () => Promise<void>}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {if (!root.current?.contains(event.target as Node)) setOpen(false);};
    const escape = (event: KeyboardEvent) => {if (event.key === 'Escape') setOpen(false);};
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape);};
  }, [open]);
  return <div className="account-menu" ref={root}>
    <button className="account-trigger" type="button" aria-expanded={open} aria-label="账号菜单" onClick={() => setOpen(value => !value)}><span className="account-avatar">{user.display_name.slice(0, 1).toUpperCase()}</span><span>{user.display_name}</span><ChevronDown size={15} /></button>
    {open && <div className="account-popover" role="menu"><div className="account-summary"><strong>{user.display_name}</strong><small>@{user.username}</small></div>
      <a role="menuitem" href="/profile"><UserRound size={16} />个人资料</a>
      <a role="menuitem" href="/settings"><Settings2 size={16} />阅读设置</a>
      <a role="menuitem" href="/profile#password"><UserRound size={16} />修改密码</a>
      {user.role === 'admin' && <a role="menuitem" href="/admin"><Shield size={16} />管理后台</a>}
      <button role="menuitem" type="button" onClick={() => void onLogout()}><LogOut size={16} />退出登录</button>
    </div>}
  </div>;
}
