'use client';
import {useState} from 'react';
import {ArrowRight, BookOpen, Eye, EyeOff} from 'lucide-react';
import type {Account} from './AppShell';
import ReadingScene from './ReadingScene';
import styles from './LoginScreen.module.css';

export default function LoginScreen({onLogin, initialError}: {onLogin: (user: Account) => void; initialError?: string}) {
  const [username,setUsername]=useState(''),[password,setPassword]=useState(''),[visible,setVisible]=useState(false),[error,setError]=useState(initialError||''),[busy,setBusy]=useState(false);
  const login = async (event: React.FormEvent) => {
    event.preventDefault();setError('');setBusy(true);
    try {const response=await fetch('/api/parser/api/auth/login',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({username,password})});if(!response.ok)throw new Error(response.status===401?'用户名或密码错误':response.status===403?'请刷新页面后重新登录':`登录失败 (${response.status})`);setPassword('');onLogin(await response.json() as Account);}
    catch(cause){setError(cause instanceof Error?cause.message:'登录失败');}finally{setBusy(false);}
  };
  return <main className={styles.page}><section className={styles.hero}><a href="/" className={styles.brand}><BookOpen size={24}/>Paperlight<span>READING, REIMAGINED.</span></a><div className={styles.heroCopy}><span className={styles.kicker}>A LITTLE SPACE FOR BIG IDEAS</span><h1>让阅读，<br/>慢下来。</h1><p>论文、思考与灵感，在这里相遇。</p></div><ReadingScene/><div className={styles.heroFooter}><span>READ. THINK. CONNECT.</span><span>01 — YOUR QUIET CORNER</span></div></section>
    <section className={styles.entry}><form className={styles.form} onSubmit={event=>void login(event)}><span className={styles.kicker}>WELCOME BACK</span><h2>继续你的探索</h2><p>登录 Paperlight，打开属于你的论文与笔记。</p><label>用户名<input autoComplete="username" placeholder="输入用户名" value={username} onChange={event=>setUsername(event.target.value)} required disabled={busy}/></label><label>密码<div className={styles.password}><input autoComplete="current-password" placeholder="输入密码" type={visible?'text':'password'} value={password} onChange={event=>setPassword(event.target.value)} required disabled={busy}/><button type="button" aria-label={visible?'隐藏密码':'显示密码'} aria-pressed={visible} onClick={()=>setVisible(value=>!value)}>{visible?<EyeOff size={18}/>:<Eye size={18}/>}</button></div></label>{error&&<p className={styles.error} role="alert">{error}</p>}<button className={styles.submit} type="submit" disabled={busy}>{busy?'正在登录…':'进入论文库'}<ArrowRight size={18}/></button><small>账号由管理员创建</small></form><div className={styles.entryFooter}>一个只属于阅读的空间。<span>Paperlight © {new Date().getFullYear()}</span></div></section>
  </main>;
}
