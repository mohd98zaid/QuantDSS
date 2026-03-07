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
    <html lang="en" className="dark">
      <body className="bg-surface-950 text-white min-h-screen">
        {children}
      </body>
    </html>
  )
}
