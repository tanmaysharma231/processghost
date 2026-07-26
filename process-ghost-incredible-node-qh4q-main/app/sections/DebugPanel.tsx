'use client'

import React, { useRef, useEffect } from 'react'
import { FiX, FiCopy, FiTerminal } from 'react-icons/fi'
import { copyToClipboard } from '@/lib/clipboard'

export interface DebugLogEntry {
  timestamp: string
  type: 'agent_start' | 'agent_input' | 'agent_output' | 'agent_complete' | 'handoff' | 'error' | 'info' | 'loop'
  agentName?: string
  message: string
  payload?: string
  tokenUsage?: number
  iteration?: number
}

interface DebugPanelProps {
  open: boolean
  onToggle: () => void
  logs: DebugLogEntry[]
}

function truncate(str: string, max: number): { text: string; truncated: boolean } {
  if (str.length <= max) return { text: str, truncated: false }
  return { text: str.slice(0, max), truncated: true }
}

function LogLine({ entry }: { entry: DebugLogEntry }) {
  const [expanded, setExpanded] = React.useState(false)

  const colorMap: Record<string, string> = {
    agent_start: 'text-green-400',
    agent_input: 'text-gray-400',
    agent_output: 'text-cyan-400',
    agent_complete: 'text-green-300',
    handoff: 'text-yellow-400',
    error: 'text-red-400',
    info: 'text-gray-500',
    loop: 'text-purple-400',
  }

  const prefixMap: Record<string, string> = {
    agent_start: '[START]',
    agent_input: '[INPUT]',
    agent_output: '[OUTPUT]',
    agent_complete: '[DONE]',
    handoff: '[HANDOFF]',
    error: '[ERROR]',
    info: '[INFO]',
    loop: '[LOOP]',
  }

  const color = colorMap[entry.type] || 'text-gray-400'
  const prefix = prefixMap[entry.type] || '[LOG]'

  const payloadTrunc = entry.payload ? truncate(entry.payload, 500) : null

  return (
    <div className="font-mono text-xs leading-relaxed">
      {entry.type === 'handoff' && (
        <div className="text-yellow-500 my-1 select-none">
          {'─'.repeat(60)}
        </div>
      )}
      <div className="flex gap-2">
        <span className="text-gray-600 shrink-0 select-none">{entry.timestamp}</span>
        <span className={`${color} shrink-0 font-bold select-none`}>{prefix}</span>
        {entry.agentName && (
          <span className="text-white shrink-0">[{entry.agentName}]</span>
        )}
        <span className={color}>{entry.message}</span>
        {entry.tokenUsage != null && (
          <span className="text-gray-500 shrink-0">({entry.tokenUsage} tokens)</span>
        )}
        {entry.iteration != null && (
          <span className="text-purple-400 shrink-0">Pass {entry.iteration}</span>
        )}
      </div>
      {payloadTrunc && (
        <div className="ml-[7.5rem] mt-0.5">
          <pre className="text-gray-500 whitespace-pre-wrap break-all text-[10px] leading-snug">
            {expanded ? entry.payload : payloadTrunc.text}
            {payloadTrunc.truncated && !expanded && '...'}
          </pre>
          {payloadTrunc.truncated && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-cyan-600 hover:text-cyan-400 text-[10px] mt-0.5 underline"
            >
              {expanded ? 'Collapse' : 'Expand full payload'}
            </button>
          )}
        </div>
      )}
      {entry.type === 'handoff' && (
        <div className="text-yellow-500 my-1 select-none">
          {'─'.repeat(60)}
        </div>
      )}
    </div>
  )
}

export default function DebugPanel({ open, onToggle, logs }: DebugPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (open && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [open, logs.length])

  const handleCopyLogs = () => {
    const text = logs.map(l => {
      let line = `${l.timestamp} ${l.type.toUpperCase()}`
      if (l.agentName) line += ` [${l.agentName}]`
      line += ` ${l.message}`
      if (l.payload) line += `\n  Payload: ${l.payload}`
      if (l.tokenUsage != null) line += ` (${l.tokenUsage} tokens)`
      if (l.type === 'handoff') line = `\n${'─'.repeat(60)}\n${line}\n${'─'.repeat(60)}`
      return line
    }).join('\n')
    copyToClipboard(text)
  }

  return (
    <>
      {/* Toggle Button - always visible */}
      <button
        onClick={onToggle}
        className={`fixed top-4 right-4 z-50 flex items-center gap-1.5 px-3 py-1.5 text-xs font-mono font-medium transition-colors ${
          open
            ? 'bg-green-500 text-black'
            : 'bg-gray-900 text-green-400 border border-green-800 hover:bg-gray-800'
        }`}
      >
        <FiTerminal className="h-3.5 w-3.5" />
        Debug
        {logs.length > 0 && (
          <span className={`ml-1 px-1.5 py-0.5 text-[10px] ${open ? 'bg-black/20 text-black' : 'bg-green-900 text-green-300'}`}>
            {logs.length}
          </span>
        )}
      </button>

      {/* Panel */}
      <div
        className={`fixed top-0 right-0 h-full z-40 transition-transform duration-300 ease-in-out ${
          open ? 'translate-x-0' : 'translate-x-full'
        }`}
        style={{ width: '480px' }}
      >
        <div className="h-full bg-black border-l border-green-900 flex flex-col shadow-2xl">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-green-900/50 bg-gray-950">
            <div className="flex items-center gap-2">
              <FiTerminal className="h-4 w-4 text-green-400" />
              <span className="font-mono text-sm text-green-400 font-bold">ProcessGhost Debug Console</span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleCopyLogs}
                className="flex items-center gap-1 px-2 py-1 text-[10px] font-mono text-green-500 border border-green-800 hover:bg-green-900/30 transition-colors"
              >
                <FiCopy className="h-3 w-3" />
                Copy Logs
              </button>
              <button
                onClick={onToggle}
                className="p-1 text-gray-500 hover:text-green-400 transition-colors"
              >
                <FiX className="h-4 w-4" />
              </button>
            </div>
          </div>

          {/* Log content */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-1">
            {logs.length === 0 ? (
              <div className="text-gray-600 font-mono text-xs">
                <p>{'>'} Waiting for pipeline execution...</p>
                <p className="animate-pulse">_</p>
              </div>
            ) : (
              logs.map((entry, i) => <LogLine key={i} entry={entry} />)
            )}
          </div>

          {/* Footer */}
          <div className="px-4 py-2 border-t border-green-900/50 bg-gray-950">
            <span className="font-mono text-[10px] text-gray-600">
              {logs.length} event{logs.length !== 1 ? 's' : ''} logged
            </span>
          </div>
        </div>
      </div>
    </>
  )
}
