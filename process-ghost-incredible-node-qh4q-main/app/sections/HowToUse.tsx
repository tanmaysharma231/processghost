'use client'

import React from 'react'
import { RiFlaskLine, RiPlayCircleLine, RiFileTextLine, RiBugLine, RiArrowRightLine } from 'react-icons/ri'

const STEPS = [
  {
    number: '01',
    icon: RiFlaskLine,
    title: 'Load Sample Data',
    description: 'Click "Try with Sample Data" to load a pre-built demo scenario — a walkthrough video and spreadsheet are included so you can test instantly without uploading anything.',
  },
  {
    number: '02',
    icon: RiPlayCircleLine,
    title: 'Hit Generate',
    description: 'Press "Generate SOP" to kick off the 6-agent pipeline. Watch each agent activate in sequence — transcription, extraction, gap detection, visual fill, synthesis, and judge.',
  },
  {
    number: '03',
    icon: RiFileTextLine,
    title: 'Review the Output',
    description: 'Once complete, you get a full SOP document with annotated steps, gap reports showing what the expert skipped, and a judge verdict with confidence score. If gaps remain, you can run another pass.',
  },
  {
    number: '04',
    icon: RiBugLine,
    title: 'Check the Debug Panel',
    description: 'Open the Debug panel (bottom-right) to see every agent call in real time — inputs, outputs, handoffs, and errors. Useful for understanding how data flows between agents.',
  },
]

export default function HowToUse() {
  return (
    <div className="border border-border bg-card p-6">
      <p className="text-xs font-semibold tracking-widest uppercase text-muted-foreground mb-1">Quick Start</p>
      <h2 className="text-lg font-semibold text-foreground mb-6">How to use ProcessGhost</h2>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {STEPS.map((step, i) => (
          <div key={step.number} className="relative flex gap-3 p-4 border border-border bg-background">
            <div className="shrink-0 flex flex-col items-center">
              <span className="text-[10px] font-bold text-muted-foreground/50 mb-1">{step.number}</span>
              <step.icon className="h-5 w-5 text-foreground" />
            </div>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-foreground leading-tight mb-1">{step.title}</p>
              <p className="text-xs text-muted-foreground leading-relaxed">{step.description}</p>
            </div>
            {i < STEPS.length - 1 && (
              <RiArrowRightLine className="absolute -right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground/30 hidden sm:block" />
            )}
          </div>
        ))}
      </div>

      <div className="mt-4 pt-4 border-t border-border flex items-start gap-2">
        <RiFlaskLine className="h-4 w-4 text-muted-foreground shrink-0 mt-0.5" />
        <p className="text-xs text-muted-foreground leading-relaxed">
          <span className="font-medium text-foreground">Tip:</span> The sample data runs a fully hardcoded demo with instant results — no API calls are made. Upload your own video to run the real pipeline with live agent processing.
        </p>
      </div>
    </div>
  )
}
