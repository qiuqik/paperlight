import type {Metadata} from 'next';
import 'katex/dist/katex.min.css';
import './globals.css';
import './workspace.css';
import './library.css';
import './account-pages.css';
import './administration.css';
import './reader-chrome.css';
import './workspace-theme.css';

export const metadata: Metadata = {
  title: 'Paperlight',
  icons: {icon: '/icon.svg?v=monochrome'},
  description: '结构化论文阅读器',
};

export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
