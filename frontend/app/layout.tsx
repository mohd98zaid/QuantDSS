import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'QuantDSS — Trading Decision Support',
  description: 'Personal quant trading decision support system for NSE equities',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className="dark scroll-smooth">
      <body className="text-white min-h-screen font-sans selection:bg-blue-500/30">
        {children}
      </body>
    </html>
  )
}
