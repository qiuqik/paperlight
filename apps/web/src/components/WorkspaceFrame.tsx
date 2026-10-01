'use client';

import {BookOpen, ChevronLeft, KeyRound, Settings2, Shield, UserRound} from 'lucide-react';
import {usePathname} from 'next/navigation';
import AccountMenu from './AccountMenu';
import type {Account} from './AppShell';

export function AccountNavigation({active}: {active: 'profile' | 'settings' | 'password'}) {
  return <nav className="workspace-sidebar" aria-label="账号导航">
    <a className={active === 'profile' ? 'selected' : ''} href="/profile"><UserRound size={18} />个人资料</a>
    <a className={active === 'settings' ? 'selected' : ''} href="/settings"><Settings2 size={18} />阅读设置</a>
    <a className={active === 'password' ? 'selected' : ''} href="/profile#password"><KeyRound size={18} />修改密码</a>
    <a className="sidebar-bottom" href="/"><ChevronLeft size={18} />返回论文库</a>
  </nav>;
}

export default function WorkspaceFrame({user, onLogout, sidebar, children, search}: {user: Account; onLogout: () => Promise<void>; sidebar: React.ReactNode; children: React.ReactNode; search?: React.ReactNode}) {
  const path = usePathname();
  return <div className="workspace-app"><header className="workspace-header">{path !== '/' && <a className="workspace-back" href="/" aria-label="返回论文库" title="返回论文库"><ChevronLeft size={20}/></a>}<a className="library-brand" href="/"><BookOpen size={21} />Paperlight</a>{search}<AccountMenu user={user} onLogout={onLogout} /></header><div className="workspace-layout">{sidebar}<main className="workspace-main">{children}</main></div></div>;
}

export function AdminNavigation() {
  return <nav className="workspace-sidebar" aria-label="管理导航"><a className="selected" href="/admin"><Shield size={18} />用户管理</a><a href="/settings"><Settings2 size={18} />阅读设置</a><a className="sidebar-bottom" href="/"><ChevronLeft size={18} />返回论文库</a></nav>;
}
