import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = {
  title: 'Contour',
  description: 'Особистий облік криптоактивів і готівки',
  icons: {
    icon: '/favicon.svg',
    apple: [{ url: '/apple-icon.png', sizes: '180x180', type: 'image/png' }],
  },
};
export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return <html lang="uk"><body>{children}</body></html>;
}