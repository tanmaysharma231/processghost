'use client'

import React, { useState } from 'react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { FiAlertTriangle, FiMonitor, FiSearch, FiChevronDown, FiCopy, FiDownload, FiRefreshCw } from 'react-icons/fi'
import { copyToClipboard } from '@/lib/clipboard'

interface SopStep {
  step_number?: number
  instruction?: string
  annotation_type?: string
  evidence_quote?: string
  bridging_explanation?: string
  source?: string
  added_in_pass?: number
}

interface SopSection {
  section_number?: number
  section_title?: string
  steps?: SopStep[]
}

interface GapReportEntry {
  gap_id?: number
  type?: string
  related_step?: number
  description?: string
  resolution?: string
  timestamp?: string
}

interface SopMetadata {
  total_original_steps?: number
  total_gaps_found?: number
  total_visual_recoveries?: number
  output_tone?: string
}

interface JudgeEvaluation {
  logical_sequencing?: string
  implicit_assumptions?: string
  prerequisite_completeness?: string
  decision_criteria?: string
  edge_cases?: string
}

interface JudgeFinding {
  finding_id?: number
  severity?: string
  related_step?: number
  issue?: string
  recommendation?: string
}

interface JudgeResult {
  verdict?: string
  confidence_score?: number
  evaluation?: JudgeEvaluation
  findings?: JudgeFinding[]
  iteration_notes?: string
  approval_conditions?: string
}

interface OutputPanelProps {
  sopTitle: string
  sopSections: SopSection[]
  gapReport: GapReportEntry[]
  metadata: SopMetadata | null
  judgeResults: JudgeResult[]
  artifactUrl: string
  onNewWalkthrough: () => void
}

function getAnnotationStyle(type?: string) {
  const t = (type ?? '').toLowerCase()
  if (t.includes('verbal') || t.includes('verbal_gap')) {
    return { borderColor: 'hsl(0 80% 45%)', icon: <FiAlertTriangle className="h-4 w-4 shrink-0" style={{ color: 'hsl(0, 80%, 45%)' }} />, label: 'Verbal Gap' }
  }
  if (t.includes('visual') || t.includes('visual_recovery')) {
    return { borderColor: 'hsl(0 0% 8%)', icon: <FiMonitor className="h-4 w-4 shrink-0 text-foreground" />, label: 'Visual Recovery' }
  }
  if (t.includes('judge')) {
    return { borderColor: 'hsl(220 60% 50%)', icon: <FiSearch className="h-4 w-4 shrink-0" style={{ color: 'hsl(220, 60%, 50%)' }} />, label: 'Judge-Flagged' }
  }
  return { borderColor: 'transparent', icon: null, label: '' }
}

function buildMarkdownText(title: string, sections: SopSection[], gapReport: GapReportEntry[]) {
  let md = `# ${title}\n\n`
  for (const section of sections) {
    md += `## ${section?.section_title ?? 'Section'}\n\n`
    const steps = Array.isArray(section?.steps) ? section.steps : []
    for (const step of steps) {
      md += `${step?.step_number ?? '-'}. ${step?.instruction ?? ''}\n`
      if (step?.evidence_quote) md += `   > ${step.evidence_quote}\n`
      if (step?.bridging_explanation) md += `   _${step.bridging_explanation}_\n`
      md += '\n'
    }
  }
  if (gapReport.length > 0) {
    md += `## Gap Report\n\n`
    for (const g of gapReport) {
      md += `- **Gap ${g?.gap_id ?? '?'}** (Step ${g?.related_step ?? '?'}, ${g?.type ?? 'Unknown'}): ${g?.description ?? ''}\n`
      if (g?.resolution) md += `  Resolution: ${g.resolution}\n`
    }
  }
  return md
}

export default function OutputPanel({
  sopTitle, sopSections, gapReport, metadata, judgeResults, artifactUrl, onNewWalkthrough,
}: OutputPanelProps) {
  const [reviewOpen, setReviewOpen] = useState(false)

  const safeSections = Array.isArray(sopSections) ? sopSections : []
  const safeGapReport = Array.isArray(gapReport) ? gapReport : []
  const safeJudgeResults = Array.isArray(judgeResults) ? judgeResults : []

  const mdText = buildMarkdownText(sopTitle || 'SOP Document', safeSections, safeGapReport)

  const handleCopy = () => {
    copyToClipboard(mdText)
  }

  const handleDownload = () => {
    const blob = new Blob([mdText], { type: 'text/markdown' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${(sopTitle || 'SOP').replace(/\s+/g, '_')}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="font-serif text-xl font-bold text-foreground">{sopTitle || 'Generated SOP'}</h2>
        {metadata && (
          <div className="flex gap-2">
            <Badge variant="secondary" className="text-xs">{metadata?.total_original_steps ?? 0} steps</Badge>
            <Badge variant="secondary" className="text-xs">{metadata?.total_gaps_found ?? 0} gaps</Badge>
            <Badge variant="secondary" className="text-xs">{metadata?.total_visual_recoveries ?? 0} visual fills</Badge>
          </div>
        )}
      </div>

      <div className="flex gap-2">
        <Button variant="outline" size="sm" onClick={handleCopy}>
          <FiCopy className="h-3.5 w-3.5 mr-1.5" /> Copy to Clipboard
        </Button>
        <Button variant="outline" size="sm" onClick={handleDownload}>
          <FiDownload className="h-3.5 w-3.5 mr-1.5" /> Download Markdown
        </Button>
        {artifactUrl && (
          <a href={artifactUrl} target="_blank" rel="noopener noreferrer">
            <Button variant="outline" size="sm">
              <FiDownload className="h-3.5 w-3.5 mr-1.5" /> Download File
            </Button>
          </a>
        )}
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={onNewWalkthrough}>
          <FiRefreshCw className="h-3.5 w-3.5 mr-1.5" /> New Walkthrough
        </Button>
      </div>

      <Tabs defaultValue="sop">
        <TabsList>
          <TabsTrigger value="sop">Clean SOP</TabsTrigger>
          <TabsTrigger value="gaps">Gap Report ({safeGapReport.length})</TabsTrigger>
        </TabsList>

        <TabsContent value="sop">
          <ScrollArea className="max-h-[600px]">
            <div className="space-y-8 pr-4">
              {safeSections.map((section, si) => (
                <div key={si} className="space-y-3">
                  <h3 className="font-serif text-base font-semibold text-foreground border-b border-border pb-2">
                    {section?.section_number != null ? `${section.section_number}. ` : ''}{section?.section_title ?? 'Section'}
                  </h3>
                  <div className="space-y-3">
                    {Array.isArray(section?.steps) && section.steps.map((step, sti) => {
                      const ann = getAnnotationStyle(step?.annotation_type)
                      return (
                        <Card key={sti} className="border shadow-none" style={{ borderLeftWidth: ann.icon ? '3px' : '1px', borderLeftColor: ann.icon ? ann.borderColor : undefined }}>
                          <CardContent className="p-4 space-y-2">
                            <div className="flex items-start gap-2">
                              <span className="font-mono text-xs text-muted-foreground mt-0.5 w-6 shrink-0">{step?.step_number ?? sti + 1}.</span>
                              <div className="flex-1 space-y-2">
                                <p className="text-sm leading-relaxed">{step?.instruction ?? ''}</p>
                                {step?.evidence_quote && (
                                  <blockquote className="border-l-2 border-border pl-3 font-mono text-xs text-muted-foreground italic">
                                    {step.evidence_quote}
                                  </blockquote>
                                )}
                                {step?.bridging_explanation && (
                                  <div className="flex items-start gap-1.5 text-xs">
                                    {ann.icon}
                                    <span className="text-muted-foreground">{step.bridging_explanation}</span>
                                  </div>
                                )}
                                <div className="flex items-center gap-2 flex-wrap">
                                  {ann.label && <Badge variant="outline" className="text-xs">{ann.label}</Badge>}
                                  {step?.source && <Badge variant="secondary" className="text-xs">Source: {step.source}</Badge>}
                                  {(step?.added_in_pass ?? 0) > 1 && <Badge variant="secondary" className="text-xs">Added in Pass {step?.added_in_pass}</Badge>}
                                </div>
                              </div>
                            </div>
                          </CardContent>
                        </Card>
                      )
                    })}
                  </div>
                </div>
              ))}
            </div>
          </ScrollArea>
        </TabsContent>

        <TabsContent value="gaps">
          <ScrollArea className="max-h-[600px]">
            <div className="space-y-3 pr-4">
              {safeGapReport.length === 0 && (
                <p className="text-sm text-muted-foreground py-8 text-center">No gaps recorded.</p>
              )}
              {safeGapReport.map((g, gi) => {
                const gType = (g?.type ?? '').toLowerCase()
                const isVerbal = gType.includes('verbal')
                const isVisual = gType.includes('visual')
                const isJudge = gType.includes('judge')
                return (
                  <Card key={gi} className="border shadow-none">
                    <CardContent className="p-4 space-y-2">
                      <div className="flex items-center gap-2">
                        <Badge variant="outline" className="text-xs">Step {g?.related_step ?? '?'}</Badge>
                        <Badge variant="secondary" className="text-xs flex items-center gap-1">
                          {isVerbal && <FiAlertTriangle className="h-3 w-3" />}
                          {isVisual && <FiMonitor className="h-3 w-3" />}
                          {isJudge && <FiSearch className="h-3 w-3" />}
                          {g?.type ?? 'Unknown'}
                        </Badge>
                        {g?.timestamp && <span className="text-xs text-muted-foreground ml-auto">{g.timestamp}</span>}
                      </div>
                      <p className="text-sm text-foreground">{g?.description ?? ''}</p>
                      {g?.resolution && (
                        <p className="text-xs text-muted-foreground"><span className="font-medium">Resolution:</span> {g.resolution}</p>
                      )}
                    </CardContent>
                  </Card>
                )
              })}
            </div>
          </ScrollArea>
        </TabsContent>
      </Tabs>

      {safeJudgeResults.length > 0 && (
        <Collapsible open={reviewOpen} onOpenChange={setReviewOpen}>
          <CollapsibleTrigger asChild>
            <Button variant="ghost" className="w-full justify-between text-sm text-muted-foreground">
              Review Log ({safeJudgeResults.length} iteration{safeJudgeResults.length > 1 ? 's' : ''})
              <FiChevronDown className={`h-4 w-4 transition-transform ${reviewOpen ? 'rotate-180' : ''}`} />
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="space-y-4 pt-2">
              {safeJudgeResults.map((judge, ji) => (
                <Card key={ji} className="border shadow-none">
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm flex items-center justify-between">
                      <span>Iteration {ji + 1}</span>
                      <div className="flex items-center gap-2">
                        <Badge variant={judge?.verdict === 'APPROVED' ? 'default' : 'secondary'} className="text-xs">
                          {judge?.verdict ?? 'Unknown'}
                        </Badge>
                        {judge?.confidence_score != null && (
                          <span className="text-xs text-muted-foreground">{Math.round((judge.confidence_score ?? 0) * 100)}% confidence</span>
                        )}
                      </div>
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3 text-xs">
                    {judge?.evaluation && (
                      <div className="grid grid-cols-1 gap-1.5">
                        {Object.entries(judge.evaluation).map(([k, v]) => (
                          <div key={k} className="flex gap-2">
                            <span className="text-muted-foreground font-medium capitalize whitespace-nowrap">{k.replace(/_/g, ' ')}:</span>
                            <span className="text-foreground">{String(v ?? '')}</span>
                          </div>
                        ))}
                      </div>
                    )}
                    {Array.isArray(judge?.findings) && judge.findings.length > 0 && (
                      <div className="space-y-2 pt-1">
                        <p className="font-medium text-muted-foreground">Findings:</p>
                        {judge.findings.map((f, fi) => (
                          <div key={fi} className="border border-border p-2 space-y-1">
                            <div className="flex items-center gap-2">
                              <Badge variant="outline" className="text-xs">Step {f?.related_step ?? '?'}</Badge>
                              <Badge variant="secondary" className="text-xs">{f?.severity ?? 'Unknown'}</Badge>
                            </div>
                            <p className="text-foreground">{f?.issue ?? ''}</p>
                            {f?.recommendation && <p className="text-muted-foreground">Rec: {f.recommendation}</p>}
                          </div>
                        ))}
                      </div>
                    )}
                    {judge?.iteration_notes && <p className="text-muted-foreground">{judge.iteration_notes}</p>}
                    {judge?.approval_conditions && <p className="text-muted-foreground"><span className="font-medium">Conditions:</span> {judge.approval_conditions}</p>}
                  </CardContent>
                </Card>
              ))}
            </div>
          </CollapsibleContent>
        </Collapsible>
      )}
    </div>
  )
}
