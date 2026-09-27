import type {Metadata} from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Paperlight',
  description: '结构化论文阅读器',
};

export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
