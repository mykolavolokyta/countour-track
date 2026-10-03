import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = {title: 'Contour · Мій портфель', description: 'Особистий облік криптоактивів і готівки', icons: {icon:'/favicon.svg'}};
export default function RootLayout({children}: Readonly<{children: React.ReactNode}>) {
  return <html lang="uk"><body>{children}</body></html>;
}
