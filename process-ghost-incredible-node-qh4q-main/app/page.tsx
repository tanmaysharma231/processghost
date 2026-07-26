'use client'

import React, { useState, useCallback, useRef } from 'react'
import { callAIAgent, uploadFiles } from '@/lib/aiAgent'
import HeaderSection from './sections/HeaderSection'
import HowToUse from './sections/HowToUse'
import UploadSection from './sections/UploadSection'
import ProgressTracker, { type NodeStatus } from './sections/ProgressTracker'
import OutputPanel from './sections/OutputPanel'
import DebugPanel, { type DebugLogEntry } from './sections/DebugPanel'
import { Card, CardContent } from '@/components/ui/card'
import {
  DEMO_TRANSCRIPT_RESULT,
  DEMO_SOP_RESULT,
  DEMO_JUDGE_RESULT,
  generateDemoDebugLogs,
} from './demoData'

const AGENTS = {
  transcription: '69ec5b0720c378a25b45e8d2',
  extraction: '69ec5b33603f5bad913c0dbd',
  gapDetection: '69ec5b3307db2da1f5e0c20c',
  visualFiller: '69ec5b349291081b210b36ea',
  synthesis: '69ec5b5bf7d0c95f8d9bf0b8',
  judge: '69ec5b5b9291081b210b36f8',
}

const AGENT_INFO = [
  { id: AGENTS.transcription, name: 'Transcription Agent', purpose: 'Extracts structured transcript from video' },
  { id: AGENTS.extraction, name: 'Process Extraction Agent', purpose: 'Identifies explicit process steps' },
  { id: AGENTS.gapDetection, name: 'Gap Detection Agent', purpose: 'Finds expertise blindness gaps' },
  { id: AGENTS.visualFiller, name: 'Visual Gap Filler Agent', purpose: 'Recovers unverbalized screen actions' },
  { id: AGENTS.synthesis, name: 'Documentation Synthesis Agent', purpose: 'Produces annotated SOP document' },
  { id: AGENTS.judge, name: 'SOP Judge Agent', purpose: 'Audits SOP for completeness' },
]

const AGENT_NAME_MAP: Record<string, string> = {
  [AGENTS.transcription]: 'Transcription Agent',
  [AGENTS.extraction]: 'Process Extraction Agent',
  [AGENTS.gapDetection]: 'Gap Detection Agent',
  [AGENTS.visualFiller]: 'Visual Gap Filler Agent',
  [AGENTS.synthesis]: 'Documentation Synthesis Agent',
  [AGENTS.judge]: 'SOP Judge Agent',
}

const MAX_JUDGE_PASSES = 3

const THEME_VARS = {
  '--background': '0 0% 98%',
  '--foreground': '0 0% 8%',
  '--card': '0 0% 100%',
  '--card-foreground': '0 0% 8%',
  '--primary': '0 0% 8%',
  '--primary-foreground': '0 0% 98%',
  '--secondary': '0 0% 94%',
  '--secondary-foreground': '0 0% 12%',
  '--accent': '0 80% 45%',
  '--accent-foreground': '0 0% 98%',
  '--muted': '0 0% 92%',
  '--muted-foreground': '0 0% 40%',
  '--border': '0 0% 85%',
  '--radius': '0rem',
} as React.CSSProperties

const DEMO_CONTEXT = {
  fileDescription: 'ProcessGhost_Bills_Tracker.xlsx — a company bills tracker with three sheets: "Master Bills" (reference list with Bill IDs and clean vendor names), "Transactions - March" (raw garbled bank transaction strings with Bill IDs and actualized amounts), and "Monthly Actuals" (where actual amounts are entered month by month, with a SUMIF formula at the bottom).',
  audioDescription: 'english_hackathon_test.mp4 — an expert explaining how to enter monthly actuals from bank transactions into the bills tracker. The expert walks through the process in ~20 seconds, skipping nearly every critical detail.',
  expertSkips: [
    '"master files" — never specifies this is a separate sheet called "Master Bills"',
    '"monthly actions" — the actual sheet is "Monthly Actuals", mispronounced',
    'Matching logic never explained — how to identify which row corresponds to which transaction (Bill ID)',
    '"transactions" — never says this is a sheet called "Transactions - March"',
    '"Amazon Web Services" — but actual data is garbled "AMZN WEB SVCS IN *AWS53871"',
    '"I paste it here" — no column specified (it\'s Column F, Actual)',
    '"total will get updated automatically" — never mentions Status must be "Paid" for SUMIF, or formula row protection',
  ],
}

const DEMO_PROMPTS = {
  transcription: `Transcribe the following audio walkthrough recording: demo_walkthrough.mp3

DEMO CONTEXT — This is a real preloaded demo scenario:
The audio is an expert explaining in Hinglish (Hindi-English mix) how to add a new March bill entry to a company bills tracker spreadsheet (ProcessGhost_Bills_Tracker.xlsx).

The spreadsheet has two sheets:
- "Master Bills" — 8 recurring vendors (AWS, Slack, Google Workspace, Zoom, HubSpot, Jira, Figma, Notion)
- "Monthly Actuals" — Jan/Feb complete, March partially filled with 5 pending rows

The expert walks through: opening the file, navigating to the right area, adding vendor name, entering the amount, setting the date, marking status as paid. The expert speaks casually, mixing Hindi and English, and skips several steps that feel obvious to them.

Generate a structured transcript capturing all spoken content, noting the Hinglish language, and timestamping key moments.`,

  extraction: (transcriptData: any) => `Extract explicit process steps from this transcript of a walkthrough for adding a March bill entry to a company bills tracker spreadsheet.

Transcript: ${safeStringify(transcriptData)}

Context: The process involves adding a new row to the "Monthly Actuals" sheet for a March vendor payment in ProcessGhost_Bills_Tracker.xlsx. Extract every step the expert explicitly mentions.`,

  gapDetection: (transcriptData: any, stepsData: any, prevFindings?: any[]) => {
    return capMessage(`Detect expertise blindness gaps in this walkthrough for adding a bill entry to a company spreadsheet tracker.

Transcript: ${safeStringify(transcriptData)}
Extracted Steps: ${safeStringify(stepsData)}
${prevFindings?.length ? `Previous judge findings to address: ${safeStringify(prevFindings)}` : ''}

CRITICAL CONTEXT — The expert SKIPS these things (known gaps to detect):
1. Which sheet to navigate to — expert jumps to "Monthly Actuals" without saying the sheet name
2. How to find the correct month's rows — expert scrolls to March section without explaining the layout structure
3. Status must be typed EXACTLY as "Paid" (case-sensitive) for the SUMIF summary formula to work correctly
4. The bottom summary row contains a SUMIF formula — new entries must go ABOVE it, never overwrite it
5. The Variance column (Actual vs Budget) calculates automatically via formula and should not be manually edited

These are classic expertise blindness patterns: the expert assumes the viewer knows the spreadsheet layout, formula dependencies, and exact text requirements. Surface ALL of these gaps.`)
  },

  visualFiller: (gapsData: any) => capMessage(`Analyze the spreadsheet screen for visual gap recovery. The file is ProcessGhost_Bills_Tracker.xlsx viewed in full frame.

Gaps detected: ${safeStringify(gapsData)}

Context: This is a company bills tracker. The screen shows:
- Tab bar at bottom with "Master Bills" and "Monthly Actuals" sheets
- Monthly Actuals sheet has sections grouped by month (January, February, March)
- Each section has columns: Vendor, Amount, Date, Status, Variance
- A SUMIF summary row at the bottom of each month section
- The Variance column contains formulas (=Budget-Actual)

Recover any unverbalized screen actions the expert performed but did not describe, especially: sheet tab clicks, scrolling behavior, cell selection patterns, and formula-protected cells visible on screen.`),

  synthesis: (tone: string, stepsData: any, gapsData: any, visualData: any) =>
    capMessage(`Synthesize a comprehensive SOP document for "How to Add a Monthly Bill Entry in ProcessGhost Bills Tracker" with tone: ${tone}.

Process Steps: ${safeStringify(stepsData)}
Detected Gaps: ${safeStringify(gapsData)}
Visual Fills: ${safeStringify(visualData)}

This SOP should guide a new employee through adding a March vendor payment to the company bills tracker spreadsheet. Include all recovered gaps as annotated steps — especially the critical warnings about the SUMIF formula row and case-sensitive "Paid" status.`),

  judge: (sopData: any) => capMessage(`Review this draft SOP for adding a bill entry to a company spreadsheet tracker. Evaluate as a senior process auditor:

${safeStringify(sopData)}

Key verification points:
- Does the SOP mention which sheet tab to navigate to?
- Does it explain how to locate the correct month section?
- Does it warn about the case-sensitive "Paid" status for SUMIF?
- Does it warn about NOT overwriting the summary formula row?
- Does it note the auto-calculating Variance column?
- Is the step sequence logical for a first-time user?`),
}

/**
 * Safely stringify data for agent prompts, truncating if too large.
 * Lyzr API has a body size limit — keep each field under ~15KB
 * so combined messages stay well under the API limit.
 */
function safeStringify(data: any, maxLen = 15000): string {
  try {
    const str = JSON.stringify(data)
    if (str.length <= maxLen) return str
    // Try to extract just the key fields to stay under limit
    if (typeof data === 'object' && data !== null) {
      const summary: Record<string, any> = {}
      for (const key of Object.keys(data)) {
        const val = data[key]
        if (Array.isArray(val)) {
          summary[key] = val.slice(0, 10) // keep first 10 items
        } else if (typeof val === 'string' && val.length > 2000) {
          summary[key] = val.slice(0, 2000) + '... [truncated]'
        } else {
          summary[key] = val
        }
      }
      const summaryStr = JSON.stringify(summary)
      if (summaryStr.length <= maxLen) return summaryStr
    }
    return str.slice(0, maxLen) + '... [truncated]'
  } catch {
    return String(data).slice(0, maxLen)
  }
}

/**
 * Cap the total message length to prevent Lyzr API body parsing errors.
 */
function capMessage(msg: string, maxLen = 45000): string {
  if (msg.length <= maxLen) return msg
  return msg.slice(0, maxLen) + '\n\n[Message truncated to fit API limits]'
}

function parseAgentResult(result: any): any {
  try {
    const resp = result?.response
    if (!resp) return null
    const r = resp?.result
    if (r && typeof r === 'object' && Object.keys(r).length > 0) return r
    if (typeof resp === 'string') {
      const parsed = JSON.parse(resp)
      return parsed?.result ?? parsed
    }
    return resp
  } catch {
    return result?.response ?? null
  }
}

function getTimestamp(): string {
  return new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { hasError: boolean; error: string }
> {
  constructor(props: { children: React.ReactNode }) {
    super(props)
    this.state = { hasError: false, error: '' }
  }
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error: error.message }
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen flex items-center justify-center bg-background text-foreground">
          <div className="text-center p-8 max-w-md">
            <h2 className="text-xl font-semibold mb-2">Something went wrong</h2>
            <p className="text-muted-foreground mb-4 text-sm">{this.state.error}</p>
            <button onClick={() => this.setState({ hasError: false, error: '' })} className="px-4 py-2 bg-primary text-primary-foreground text-sm">Try again</button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

export default function Page() {
  const [file, setFile] = useState<File | null>(null)
  const [tone, setTone] = useState('step-by-step')
  const [cropEnabled, setCropEnabled] = useState(false)
  const [cropRegion, setCropRegion] = useState({ x: 0, y: 0, w: 100, h: 100 })
  const [demoMode, setDemoMode] = useState(false)
  const [demoLoading, setDemoLoading] = useState(false)

  const [isRunning, setIsRunning] = useState(false)
  const [nodeStatuses, setNodeStatuses] = useState<NodeStatus[]>(Array(6).fill('pending'))
  const [activeAgentId, setActiveAgentId] = useState<string | null>(null)
  const [pipelineError, setPipelineError] = useState<string | null>(null)
  const [showProgress, setShowProgress] = useState(false)

  const [sopTitle, setSopTitle] = useState('')
  const [sopSections, setSopSections] = useState<any[]>([])
  const [gapReport, setGapReport] = useState<any[]>([])
  const [metadata, setMetadata] = useState<any>(null)
  const [judgeResults, setJudgeResults] = useState<any[]>([])
  const [artifactUrl, setArtifactUrl] = useState('')
  const [isComplete, setIsComplete] = useState(false)

  // Transcript data for download
  const [transcriptData, setTranscriptData] = useState<any>(null)

  // Judge pass control: user-driven, not automatic
  const [judgePassCount, setJudgePassCount] = useState(0)
  const [awaitingJudgeDecision, setAwaitingJudgeDecision] = useState(false)

  // Pipeline intermediate data refs (persist across renders for "Run Another Pass")
  const pipelineDataRef = useRef<{
    transcriptData: any
    stepsData: any
    prevJudgeFindings: any[]
  }>({ transcriptData: null, stepsData: null, prevJudgeFindings: [] })

  // Debug panel
  const [debugOpen, setDebugOpen] = useState(false)
  const [debugLogs, setDebugLogs] = useState<DebugLogEntry[]>([])

  const addLog = useCallback((entry: Omit<DebugLogEntry, 'timestamp'>) => {
    setDebugLogs(prev => [...prev, { ...entry, timestamp: getTimestamp() }])
  }, [])

  const updateNode = useCallback((idx: number, status: NodeStatus) => {
    setNodeStatuses(prev => { const n = [...prev]; n[idx] = status; return n })
  }, [])

  const loadDemoData = useCallback(() => {
    setDemoLoading(true)
    const demoBlob = new Blob(['demo-walkthrough-video'], { type: 'video/mp4' })
    const demoFile = new File([demoBlob], 'english_hackathon_test.mp4 + ProcessGhost_Bills_Tracker.xlsx', { type: 'video/mp4' })
    setFile(demoFile)
    setDemoMode(true)
    setCropEnabled(false)
    setCropRegion({ x: 0, y: 0, w: 100, h: 100 })
    setTone('step-by-step')
    setDemoLoading(false)
  }, [])

  // Run the gap-detection -> visual filler -> synthesis -> judge loop (one pass)
  const runJudgePass = useCallback(async (passNumber: number) => {
    const { transcriptData: tData, stepsData, prevJudgeFindings } = pipelineDataRef.current
    setIsRunning(true)
    setAwaitingJudgeDecision(false)
    setPipelineError(null)

    try {
      addLog({ type: 'loop', message: `Starting judge pass ${passNumber}`, iteration: passNumber })

      // Step 3: Gap Detection
      updateNode(2, 'active')
      setActiveAgentId(AGENTS.gapDetection)
      addLog({ type: 'agent_start', agentName: 'Gap Detection Agent', message: `Running (Pass ${passNumber})` })
      const gapMsg = demoMode
        ? DEMO_PROMPTS.gapDetection(tData, stepsData, prevJudgeFindings.length > 0 ? prevJudgeFindings : undefined)
        : capMessage(prevJudgeFindings.length > 0
          ? `Detect expertise blindness gaps. Transcript: ${safeStringify(tData)} Steps: ${safeStringify(stepsData)} Judge findings: ${safeStringify(prevJudgeFindings)}`
          : `Detect expertise blindness gaps. Transcript: ${safeStringify(tData)} Steps: ${safeStringify(stepsData)}`)
      addLog({ type: 'agent_input', agentName: 'Gap Detection Agent', message: 'Input payload', payload: gapMsg })
      const r3 = await callAIAgent(gapMsg, AGENTS.gapDetection)
      if (!r3.success) throw new Error(r3.error ?? 'Gap detection failed')
      const gapsData = parseAgentResult(r3)
      addLog({ type: 'agent_output', agentName: 'Gap Detection Agent', message: 'Output received', payload: JSON.stringify(gapsData) })
      addLog({ type: 'agent_complete', agentName: 'Gap Detection Agent', message: 'Complete' })
      updateNode(2, 'complete')

      // Handoff
      addLog({ type: 'handoff', message: 'HANDOFF: Gap Detection Agent complete, passing to Visual Gap Filler Agent' })

      // Step 4: Visual Gap Filler
      updateNode(3, 'active')
      setActiveAgentId(AGENTS.visualFiller)
      addLog({ type: 'agent_start', agentName: 'Visual Gap Filler Agent', message: 'Running' })
      const visualMsg = demoMode
        ? DEMO_PROMPTS.visualFiller(gapsData)
        : (() => {
            const cropInfo = cropEnabled ? ` Crop: x=${cropRegion.x}% y=${cropRegion.y}% w=${cropRegion.w}% h=${cropRegion.h}%` : ' Full frame.'
            return capMessage(`Analyze gap regions for visual screen actions.${cropInfo} Gaps: ${safeStringify(gapsData)}`)
          })()
      addLog({ type: 'agent_input', agentName: 'Visual Gap Filler Agent', message: 'Input payload', payload: visualMsg })
      const r4 = await callAIAgent(visualMsg, AGENTS.visualFiller)
      if (!r4.success) throw new Error(r4.error ?? 'Visual analysis failed')
      const visualData = parseAgentResult(r4)
      addLog({ type: 'agent_output', agentName: 'Visual Gap Filler Agent', message: 'Output received', payload: JSON.stringify(visualData) })
      addLog({ type: 'agent_complete', agentName: 'Visual Gap Filler Agent', message: 'Complete' })
      updateNode(3, 'complete')

      // Handoff
      addLog({ type: 'handoff', message: 'HANDOFF: Visual Gap Filler Agent complete, passing to Documentation Synthesis Agent' })

      // Step 5: Synthesis
      updateNode(4, 'active')
      setActiveAgentId(AGENTS.synthesis)
      addLog({ type: 'agent_start', agentName: 'Documentation Synthesis Agent', message: 'Running' })
      const synthesisMsg = demoMode
        ? DEMO_PROMPTS.synthesis(tone, stepsData, gapsData, visualData)
        : capMessage(`Synthesize an SOP document with tone: ${tone}. Steps: ${safeStringify(stepsData)} Gaps: ${safeStringify(gapsData)} Visual Fills: ${safeStringify(visualData)}`)
      addLog({ type: 'agent_input', agentName: 'Documentation Synthesis Agent', message: 'Input payload', payload: synthesisMsg })
      const r5 = await callAIAgent(synthesisMsg, AGENTS.synthesis)
      if (!r5.success) throw new Error(r5.error ?? 'Synthesis failed')
      const sopData = parseAgentResult(r5)
      setSopTitle(sopData?.sop_title ?? '')
      setSopSections(Array.isArray(sopData?.sop_sections) ? sopData.sop_sections : [])
      setGapReport(Array.isArray(sopData?.gap_report) ? sopData.gap_report : [])
      setMetadata(sopData?.metadata ?? null)
      const files = Array.isArray(r5?.module_outputs?.artifact_files) ? r5.module_outputs!.artifact_files : []
      if (files.length > 0 && files[0]?.file_url) setArtifactUrl(files[0].file_url)
      addLog({ type: 'agent_output', agentName: 'Documentation Synthesis Agent', message: 'Output received', payload: JSON.stringify(sopData) })
      addLog({ type: 'agent_complete', agentName: 'Documentation Synthesis Agent', message: 'Complete' })
      updateNode(4, 'complete')

      // Handoff
      addLog({ type: 'handoff', message: 'HANDOFF: Documentation Synthesis Agent complete, passing to SOP Judge Agent' })

      // Step 6: Judge
      updateNode(5, 'active')
      setActiveAgentId(AGENTS.judge)
      addLog({ type: 'agent_start', agentName: 'SOP Judge Agent', message: `Running (Pass ${passNumber})` })
      const judgeMsg = demoMode
        ? DEMO_PROMPTS.judge(sopData)
        : capMessage(`Review this draft SOP as a senior process auditor: ${safeStringify(sopData)}`)
      addLog({ type: 'agent_input', agentName: 'SOP Judge Agent', message: 'Input payload', payload: judgeMsg })
      const r6 = await callAIAgent(judgeMsg, AGENTS.judge)
      if (!r6.success) throw new Error(r6.error ?? 'Judge evaluation failed')
      const judgeData = parseAgentResult(r6)
      setJudgeResults(prev => [...prev, judgeData])
      addLog({ type: 'agent_output', agentName: 'SOP Judge Agent', message: `Verdict: ${judgeData?.verdict ?? 'Unknown'}, Confidence: ${Math.round((judgeData?.confidence_score ?? 0) * 100)}%`, payload: JSON.stringify(judgeData) })
      addLog({ type: 'agent_complete', agentName: 'SOP Judge Agent', message: 'Complete' })

      setJudgePassCount(passNumber)

      if (judgeData?.verdict === 'GAPS_REMAINING' && passNumber < MAX_JUDGE_PASSES) {
        // Store findings for next potential pass
        pipelineDataRef.current.prevJudgeFindings = Array.isArray(judgeData?.findings) ? judgeData.findings : []
        updateNode(5, 'waiting')
        setAwaitingJudgeDecision(true)
        addLog({ type: 'info', message: `Judge found gaps. User may trigger another pass (${MAX_JUDGE_PASSES - passNumber} remaining).` })
        // Reset nodes 2-4 to pending for potential next pass
        updateNode(2, 'pending')
        updateNode(3, 'pending')
        updateNode(4, 'pending')
      } else {
        updateNode(5, 'complete')
        setIsComplete(true)
        addLog({ type: 'info', message: judgeData?.verdict === 'APPROVED' ? 'SOP approved by judge.' : `Pipeline complete after ${passNumber} pass(es). Final verdict: ${judgeData?.verdict ?? 'Unknown'}` })
      }
    } catch (err: any) {
      setPipelineError(err?.message ?? 'Pipeline failed')
      addLog({ type: 'error', message: err?.message ?? 'Pipeline failed' })
    } finally {
      setIsRunning(false)
      setActiveAgentId(null)
    }
  }, [demoMode, tone, cropEnabled, cropRegion, updateNode, addLog])

  /**
   * Instant demo pipeline — hardcoded output, no agent calls.
   * Animates node statuses sequentially with short delays for visual feedback,
   * then displays the full hardcoded SOP output.
   */
  const runDemoPipeline = useCallback(async () => {
    setIsRunning(true)
    setShowProgress(true)
    setPipelineError(null)
    setIsComplete(false)
    setJudgeResults([])
    setJudgePassCount(0)
    setAwaitingJudgeDecision(false)
    setNodeStatuses(Array(6).fill('pending'))
    setTranscriptData(null)
    setDebugLogs([])
    pipelineDataRef.current = { transcriptData: null, stepsData: null, prevJudgeFindings: [] }

    // Generate all debug logs with timestamps
    const demoLogs = generateDemoDebugLogs()

    const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

    // Animate through 6 nodes with short delays
    const nodeDelays = [300, 350, 400, 450, 500, 400]
    const logBatches = [
      // Agent 1: Transcription (indices 0-6)
      { start: 0, end: 7 },
      // Agent 2: Extraction (indices 7-11)
      { start: 7, end: 12 },
      // Agent 3: Gap Detection (indices 12-17)
      { start: 12, end: 18 },
      // Agent 4: Visual Gap Filler (indices 18-23)
      { start: 18, end: 24 },
      // Agent 5: Synthesis (indices 24-29)
      { start: 24, end: 30 },
      // Agent 6: Judge + final (indices 30-36)
      { start: 30, end: demoLogs.length },
    ]

    for (let i = 0; i < 6; i++) {
      updateNode(i, 'active')
      setActiveAgentId(AGENT_INFO[i].id)

      // Add this agent's batch of logs
      const batch = logBatches[i]
      if (batch) {
        setDebugLogs(prev => [...prev, ...demoLogs.slice(batch.start, batch.end)])
      }

      await delay(nodeDelays[i])
      updateNode(i, 'complete')
    }

    // Set transcript data
    setTranscriptData(DEMO_TRANSCRIPT_RESULT)
    pipelineDataRef.current.transcriptData = DEMO_TRANSCRIPT_RESULT

    // Set SOP output
    setSopTitle(DEMO_SOP_RESULT.sop_title)
    setSopSections(DEMO_SOP_RESULT.sop_sections)
    setGapReport(DEMO_SOP_RESULT.gap_report)
    setMetadata(DEMO_SOP_RESULT.metadata)

    // Set judge result
    setJudgeResults([DEMO_JUDGE_RESULT])
    setJudgePassCount(1)

    // Mark complete
    setIsComplete(true)
    setActiveAgentId(null)
    setIsRunning(false)
  }, [updateNode])

  const runPipeline = useCallback(async () => {
    if (!file && !demoMode) return

    // Instant hardcoded path for demo mode
    if (demoMode) {
      await runDemoPipeline()
      return
    }

    setIsRunning(true)
    setShowProgress(true)
    setPipelineError(null)
    setIsComplete(false)
    setJudgeResults([])
    setJudgePassCount(0)
    setAwaitingJudgeDecision(false)
    setNodeStatuses(Array(6).fill('pending'))
    setTranscriptData(null)
    setDebugLogs([])
    pipelineDataRef.current = { transcriptData: null, stepsData: null, prevJudgeFindings: [] }

    addLog({ type: 'info', message: 'Pipeline started' })

    try {
      // Upload file first
      let uploadedAssetIds: string[] = []
      if (file) {
        addLog({ type: 'info', message: `Uploading file: ${file.name}` })
        const uploadResult = await uploadFiles(file)
        if (!uploadResult.success || uploadResult.asset_ids.length === 0) {
          throw new Error(uploadResult.error ?? 'File upload failed. Please try again.')
        }
        uploadedAssetIds = uploadResult.asset_ids
        addLog({ type: 'info', message: `File uploaded successfully. Asset IDs: ${uploadedAssetIds.join(', ')}` })
      }

      // Step 1: Transcription
      updateNode(0, 'active')
      setActiveAgentId(AGENTS.transcription)
      addLog({ type: 'agent_start', agentName: 'Transcription Agent', message: 'Running' })
      const transcriptionMsg = `Transcribe the following video recording: ${file!.name}. The video is a screen recording of a process walkthrough. Please generate a structured transcript.`
      addLog({ type: 'agent_input', agentName: 'Transcription Agent', message: 'Input payload', payload: transcriptionMsg })
      const r1 = await callAIAgent(transcriptionMsg, AGENTS.transcription, uploadedAssetIds.length > 0 ? { assets: uploadedAssetIds } : undefined)
      if (!r1.success) throw new Error(r1.error ?? 'Transcription failed')
      const tData = parseAgentResult(r1)
      setTranscriptData(tData)
      pipelineDataRef.current.transcriptData = tData
      addLog({ type: 'agent_output', agentName: 'Transcription Agent', message: 'Transcript received', payload: JSON.stringify(tData) })
      addLog({ type: 'agent_complete', agentName: 'Transcription Agent', message: 'Complete' })
      updateNode(0, 'complete')

      // Handoff
      addLog({ type: 'handoff', message: 'HANDOFF: Transcription Agent complete, passing to Process Extraction Agent' })

      // Step 2: Extraction
      updateNode(1, 'active')
      setActiveAgentId(AGENTS.extraction)
      addLog({ type: 'agent_start', agentName: 'Process Extraction Agent', message: 'Running' })
      const extractionMsg = capMessage(`Extract process steps from this transcript: ${safeStringify(tData)}`)
      addLog({ type: 'agent_input', agentName: 'Process Extraction Agent', message: 'Input payload', payload: extractionMsg })
      const r2 = await callAIAgent(extractionMsg, AGENTS.extraction)
      if (!r2.success) throw new Error(r2.error ?? 'Extraction failed')
      const sData = parseAgentResult(r2)
      pipelineDataRef.current.stepsData = sData
      addLog({ type: 'agent_output', agentName: 'Process Extraction Agent', message: 'Steps extracted', payload: JSON.stringify(sData) })
      addLog({ type: 'agent_complete', agentName: 'Process Extraction Agent', message: 'Complete' })
      updateNode(1, 'complete')

      // Handoff
      addLog({ type: 'handoff', message: 'HANDOFF: Process Extraction Agent complete, passing to Gap Detection Agent' })

      // Run first judge pass (gap detection -> visual filler -> synthesis -> judge)
      setIsRunning(false) // runJudgePass will set it back
      await runJudgePass(1)
    } catch (err: any) {
      setPipelineError(err?.message ?? 'Pipeline failed')
      addLog({ type: 'error', message: err?.message ?? 'Pipeline failed' })
      setIsRunning(false)
      setActiveAgentId(null)
    }
  }, [file, demoMode, tone, cropEnabled, cropRegion, updateNode, addLog, runJudgePass, runDemoPipeline])

  const handleRunAnotherPass = useCallback(() => {
    const nextPass = judgePassCount + 1
    if (nextPass > MAX_JUDGE_PASSES) return
    runJudgePass(nextPass)
  }, [judgePassCount, runJudgePass])

  const handleNewWalkthrough = useCallback(() => {
    setFile(null)
    setTone('step-by-step')
    setCropEnabled(false)
    setCropRegion({ x: 0, y: 0, w: 100, h: 100 })
    setIsRunning(false)
    setNodeStatuses(Array(6).fill('pending'))
    setActiveAgentId(null)
    setPipelineError(null)
    setShowProgress(false)
    setSopTitle('')
    setSopSections([])
    setGapReport([])
    setMetadata(null)
    setJudgeResults([])
    setArtifactUrl('')
    setIsComplete(false)
    setDemoMode(false)
    setTranscriptData(null)
    setJudgePassCount(0)
    setAwaitingJudgeDecision(false)
    setDebugLogs([])
    pipelineDataRef.current = { transcriptData: null, stepsData: null, prevJudgeFindings: [] }
  }, [])

  return (
    <ErrorBoundary>
      <div style={THEME_VARS} className="min-h-screen bg-background text-foreground">
        <HeaderSection demoMode={demoMode} />

        {/* Debug Panel */}
        <DebugPanel
          open={debugOpen}
          onToggle={() => setDebugOpen(prev => !prev)}
          logs={debugLogs}
        />

        <main className="mx-auto max-w-3xl px-6 py-8 space-y-8">
          {!showProgress && !isComplete && !awaitingJudgeDecision && (
            <HowToUse />
          )}

          {!isComplete && !awaitingJudgeDecision && (
            <UploadSection
              file={file}
              onFileChange={setFile}
              tone={tone}
              onToneChange={setTone}
              cropEnabled={cropEnabled}
              onCropEnabledChange={setCropEnabled}
              cropRegion={cropRegion}
              onCropRegionChange={setCropRegion}
              onGenerate={runPipeline}
              isRunning={isRunning}
              demoMode={demoMode}
              onLoadDemo={loadDemoData}
              demoLoading={demoLoading}
            />
          )}

          <ProgressTracker
            statuses={nodeStatuses}
            judgePassCount={judgePassCount}
            maxJudgePasses={MAX_JUDGE_PASSES}
            visible={showProgress}
            transcriptData={transcriptData}
            awaitingJudgeDecision={awaitingJudgeDecision}
            onRunAnotherPass={handleRunAnotherPass}
          />

          {pipelineError && (
            <Card className="border border-red-300 shadow-none">
              <CardContent className="p-4 text-sm text-red-700">{pipelineError}</CardContent>
            </Card>
          )}

          {/* Show output when complete OR when awaiting judge decision (so user can see current SOP) */}
          {(isComplete || awaitingJudgeDecision) && (
            <OutputPanel
              sopTitle={sopTitle}
              sopSections={sopSections}
              gapReport={gapReport}
              metadata={metadata}
              judgeResults={judgeResults}
              artifactUrl={artifactUrl}
              onNewWalkthrough={handleNewWalkthrough}
            />
          )}

          <Card className="border border-border shadow-none">
            <CardContent className="p-4">
              <p className="text-xs font-medium text-muted-foreground mb-3">Pipeline Agents</p>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                {AGENT_INFO.map((a) => (
                  <div key={a.id} className="flex items-start gap-2">
                    <div className={`mt-1.5 h-2 w-2 shrink-0 ${activeAgentId === a.id ? 'bg-foreground animate-pulse' : 'bg-muted-foreground/30'}`} />
                    <div>
                      <p className="text-xs font-medium text-foreground leading-tight">{a.name}</p>
                      <p className="text-xs text-muted-foreground leading-tight">{a.purpose}</p>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </main>
      </div>
    </ErrorBoundary>
  )
}
