'use client'

import { FiCheck, FiAlertCircle, FiRefreshCw, FiDownload } from 'react-icons/fi'
import { Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'

export type NodeStatus = 'pending' | 'active' | 'complete' | 'looping' | 'error' | 'waiting'

interface ProgressTrackerProps {
  statuses: NodeStatus[]
  judgePassCount: number
  maxJudgePasses: number
  visible: boolean
  transcriptData: any
  awaitingJudgeDecision: boolean
  onRunAnotherPass: () => void
}

const NODE_LABELS = [
  'Transcription',
  'Extraction',
  'Gap Detection',
  'Visual Analysis',
  'Synthesis',
  'Judge',
]

export default function ProgressTracker({
  statuses,
  judgePassCount,
  maxJudgePasses,
  visible,
  transcriptData,
  awaitingJudgeDecision,
  onRunAnotherPass,
}: ProgressTrackerProps) {
  if (!visible) return null

  const handleDownloadTranscript = () => {
    if (!transcriptData) return
    const text = typeof transcriptData === 'string'
      ? transcriptData
      : JSON.stringify(transcriptData, null, 2)
    const blob = new Blob([text], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
    a.href = url
    a.download = `transcript_${ts}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  const renderIcon = (status: NodeStatus) => {
    switch (status) {
      case 'complete':
        return <FiCheck className="h-4 w-4 text-background" />
      case 'active':
        return <Loader2 className="h-4 w-4 text-background animate-spin" />
      case 'looping':
        return <FiRefreshCw className="h-4 w-4 text-background animate-spin" />
      case 'waiting':
        return <span className="h-2.5 w-2.5 rounded-full bg-yellow-400 animate-pulse" />
      case 'error':
        return <FiAlertCircle className="h-4 w-4 text-background" />
      default:
        return <span className="h-2 w-2 rounded-full bg-muted-foreground" />
    }
  }

  const bgForStatus = (status: NodeStatus) => {
    switch (status) {
      case 'complete': return 'bg-foreground'
      case 'active': return 'bg-foreground'
      case 'looping': return 'bg-foreground'
      case 'waiting': return 'bg-yellow-100 border border-yellow-400'
      case 'error': return 'bg-red-600'
      default: return 'bg-muted border border-border'
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        {NODE_LABELS.map((label, i) => (
          <div key={label} className="flex items-center flex-1 last:flex-none">
            <div className="flex flex-col items-center gap-1.5">
              <div className={`h-8 w-8 flex items-center justify-center ${bgForStatus(statuses[i] ?? 'pending')}`}>
                {renderIcon(statuses[i] ?? 'pending')}
              </div>
              <span className="text-xs text-muted-foreground whitespace-nowrap">{label}</span>
              {/* Transcript download button next to Transcription node */}
              {i === 0 && (statuses[0] ?? 'pending') === 'complete' && transcriptData && (
                <button
                  onClick={handleDownloadTranscript}
                  className="flex items-center gap-1 px-2 py-0.5 text-[10px] text-muted-foreground hover:text-foreground border border-border hover:border-foreground/40 transition-colors mt-0.5"
                >
                  <FiDownload className="h-2.5 w-2.5" />
                  Transcript
                </button>
              )}
            </div>
            {i < NODE_LABELS.length - 1 && (
              <div className={`flex-1 h-px mx-2 mt-[-18px] ${(statuses[i] ?? 'pending') === 'complete' ? 'bg-foreground' : 'bg-border'}`} />
            )}
          </div>
        ))}
      </div>

      {/* Judge pass counter and Run Another Pass button */}
      {judgePassCount > 0 && (
        <div className="flex items-center justify-center gap-3 text-xs text-muted-foreground">
          <div className="border-t border-dashed border-border w-12" />
          <span className="font-mono">
            Pass {judgePassCount} of {maxJudgePasses} used
          </span>
          <div className="border-t border-dashed border-border w-12" />
        </div>
      )}

      {awaitingJudgeDecision && (
        <div className="flex items-center justify-center pt-1">
          <Button
            variant="outline"
            size="sm"
            onClick={onRunAnotherPass}
            disabled={judgePassCount >= maxJudgePasses}
            className="gap-2 font-mono text-xs"
          >
            <FiRefreshCw className="h-3.5 w-3.5" />
            {judgePassCount >= maxJudgePasses
              ? 'Maximum passes reached'
              : `Run Another Pass (${maxJudgePasses - judgePassCount} remaining)`}
          </Button>
        </div>
      )}
    </div>
  )
}
