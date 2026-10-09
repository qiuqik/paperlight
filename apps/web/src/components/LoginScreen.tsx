'use client';

import {useState, type FormEvent} from 'react';
import {ArrowRight, BookOpen, Eye, EyeOff} from 'lucide-react';
import type {Account} from './AppShell';
import HeroModel from './HeroModel';
import styles from './LoginScreen.module.css';

export default function LoginScreen({onLogin, initialError}: {onLogin: (user: Account) => void; initialError?: string}) {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [visible, setVisible] = useState(false);
  const [error, setError] = useState(initialError || '');
  const [busy, setBusy] = useState(false);
  const registering = mode === 'register';

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    if (registering && password !== confirmation) {
      setError('两次输入的密码不一致。');
      return;
    }
    setBusy(true);
    try {
      const response = await fetch(`/api/parser/api/auth/${mode}`, {
        method: 'POST', headers: {'content-type': 'application/json'},
        body: JSON.stringify({username: username.trim(), password, ...(registering ? {confirmPassword: confirmation} : {})}),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => null) as {detail?: unknown} | null;
        throw new Error(response.status === 401 ? '用户名或密码错误' : response.status === 403 ? '请刷新页面后重试'
          : typeof payload?.detail === 'string' ? payload.detail : `暂时无法${registering ? '注册' : '登录'}，请稍后重试。`);
      }
      const account = await response.json() as Account;
      setPassword(''); setConfirmation(''); onLogin(account);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '连接失败，请稍后重试。');
    } finally {setBusy(false);}
  };

  const switchMode = () => {
    setMode(registering ? 'login' : 'register');
    setPassword(''); setConfirmation(''); setVisible(false); setError('');
  };

  return <main className={styles.page}>
    <section className={styles.hero}>
      <div className={styles.halo} aria-hidden="true"/>
      <HeroModel/>
      <a href="/" className={styles.brand}><BookOpen size={24}/>Paperlight<span>READING, REIMAGINED.</span></a>
      <div className={styles.heroCopy}><span className={styles.kicker}>A LITTLE SPACE FOR BIG IDEAS</span><h1>让阅读，<br/>慢下来。</h1><p>论文、思考与灵感，在这里相遇。</p></div>
      <div className={styles.heroFooter}><span>READ. THINK. CONNECT.</span><span>01 — YOUR QUIET CORNER</span></div>
    </section>
    <section className={styles.entry}>
      <form className={styles.form} onSubmit={event => void submit(event)}>
        <span className={styles.kicker}>{registering ? 'YOUR NEXT CHAPTER' : 'WELCOME BACK'}</span>
        <h2>{registering ? '开启你的探索' : '继续你的探索'}</h2>
        <p>{registering ? '创建账号，拥有自己的论文库与阅读笔记。' : '登录 Paperlight，打开属于你的论文与笔记。'}</p>
        <label htmlFor="auth-username">用户名<input id="auth-username" name="username" autoComplete="username" placeholder={registering ? '3–40 个字符' : '输入用户名'} value={username} onChange={event => setUsername(event.target.value)} minLength={registering ? 3 : undefined} maxLength={40} required disabled={busy}/></label>
        <label htmlFor="auth-password">密码<div className={styles.password}>
          <input id="auth-password" name="password" autoComplete={registering ? 'new-password' : 'current-password'} placeholder={registering ? '至少 4 个字符' : '输入密码'} type={visible ? 'text' : 'password'} value={password} onChange={event => setPassword(event.target.value)} minLength={registering ? 4 : undefined} maxLength={1024} required disabled={busy}/>
          <button type="button" aria-label={visible ? '隐藏密码' : '显示密码'} aria-pressed={visible} disabled={busy} onClick={() => setVisible(value => !value)}>{visible ? <EyeOff size={18}/> : <Eye size={18}/>}</button>
        </div></label>
        {registering ? <label htmlFor="auth-confirmation">确认密码<input id="auth-confirmation" name="confirmPassword" autoComplete="new-password" placeholder="再次输入密码" type={visible ? 'text' : 'password'} value={confirmation} onChange={event => setConfirmation(event.target.value)} minLength={4} maxLength={1024} required disabled={busy}/></label> : null}
        {error ? <p className={styles.error} role="alert">{error}</p> : null}
        <button className={styles.submit} type="submit" disabled={busy}>{busy ? (registering ? '正在创建账号…' : '正在登录…') : (registering ? '注册并进入论文库' : '进入论文库')}<ArrowRight size={18}/></button>
        <div className={styles.modeSwitch}>{registering ? '已经有账号？' : '还没有账号？'}<button type="button" disabled={busy} onClick={switchMode}>{registering ? '返回登录' : '注册账号'}</button></div>
      </form>
      <div className={styles.entryFooter}>一个只属于阅读的空间。<span>Paperlight © {new Date().getFullYear()}</span></div>
    </section>
  </main>;
}
