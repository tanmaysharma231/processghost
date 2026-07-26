'use client'

import { RiGhostLine, RiFlaskLine } from 'react-icons/ri'

interface HeaderSectionProps {
  demoMode: boolean
}

export default function HeaderSection({ demoMode }: HeaderSectionProps) {
  return (
    <header className="w-full border-b border-border bg-card">
      <div className="mx-auto max-w-3xl px-6 py-8 flex items-start justify-between">
        <div>
          <div className="flex items-center gap-3 mb-2">
            <RiGhostLine className="h-8 w-8 text-foreground" />
            <h1 className="font-serif text-3xl font-bold tracking-tight text-foreground">
              ProcessGhost
            </h1>
          </div>
          <p className="text-muted-foreground text-sm leading-relaxed max-w-lg" style={{ lineHeight: '1.7' }}>
            Surface the knowledge experts forgot to write down — even what they forgot to say.
          </p>
        </div>
        {demoMode && (
          <div className="flex items-center gap-1.5 bg-foreground text-background px-3 py-1.5 text-xs font-medium tracking-wide mt-1">
            <RiFlaskLine className="h-3.5 w-3.5" />
            Demo Mode
          </div>
        )}
      </div>
    </header>
  )
}
