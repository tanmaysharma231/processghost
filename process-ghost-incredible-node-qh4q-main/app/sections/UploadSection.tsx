'use client'

import React, { useRef } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Card, CardContent } from '@/components/ui/card'
import { RiUploadCloud2Line, RiFilmLine, RiCloseLine, RiFlaskLine } from 'react-icons/ri'
import { Loader2 } from 'lucide-react'

interface UploadSectionProps {
  file: File | null
  onFileChange: (f: File | null) => void
  tone: string
  onToneChange: (t: string) => void
  cropEnabled: boolean
  onCropEnabledChange: (v: boolean) => void
  cropRegion: { x: number; y: number; w: number; h: number }
  onCropRegionChange: (r: { x: number; y: number; w: number; h: number }) => void
  onGenerate: () => void
  isRunning: boolean
  demoMode: boolean
  onLoadDemo: () => void
  demoLoading: boolean
}

export default function UploadSection({
  file, onFileChange, tone, onToneChange,
  cropEnabled, onCropEnabledChange,
  cropRegion, onCropRegionChange,
  onGenerate, isRunning,
  demoMode, onLoadDemo, demoLoading,
}: UploadSectionProps) {
  const inputRef = useRef<HTMLInputElement>(null)

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const f = e.dataTransfer.files?.[0]
    if (f && /\.(mp4|webm|mov|mp3|wav|xlsx|xls)$/i.test(f.name)) onFileChange(f)
  }

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) onFileChange(f)
  }

  const formatSize = (bytes: number) => {
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  return (
    <div className="space-y-6">
      {!file ? (
        <div className="relative">
          {demoMode && (
            <div className="absolute top-3 right-3 z-10 flex items-center gap-1.5 bg-foreground text-background px-2.5 py-1 text-xs font-medium tracking-wide">
              <RiFlaskLine className="h-3 w-3" />
              Demo Mode
            </div>
          )}
          <div
            onDrop={handleDrop}
            onDragOver={(e) => e.preventDefault()}
            onClick={() => inputRef.current?.click()}
            className="border-2 border-dashed border-border bg-card px-6 pt-12 pb-6 flex flex-col items-center justify-center cursor-pointer transition-colors hover:border-foreground/40"
          >
            <RiUploadCloud2Line className="h-10 w-10 text-muted-foreground mb-3" />
            <p className="text-sm font-medium text-foreground mb-1">Drop your video file here or click to browse</p>
            <p className="text-xs text-muted-foreground mb-6">Accepts .mp4, .webm, .mov (screen recordings, walkthroughs)</p>
            <input
              ref={inputRef}
              type="file"
              accept=".mp4,.webm,.mov,video/mp4,video/webm,video/quicktime"
              className="hidden"
              onChange={handleFileInput}
            />
          </div>
          <div className="border-2 border-dashed border-border border-t-0 bg-card px-6 py-4 flex items-center justify-center">
            <div className="flex items-center gap-3 w-full">
              <div className="flex-1 h-px bg-border" />
              <span className="text-xs text-muted-foreground font-medium">or</span>
              <div className="flex-1 h-px bg-border" />
            </div>
          </div>
          <Button
            variant="outline"
            className="w-full h-12 border-2 border-dashed border-border border-t-0 bg-card hover:bg-secondary/50 font-medium text-sm gap-2"
            onClick={(e) => { e.stopPropagation(); onLoadDemo() }}
            disabled={demoLoading || isRunning}
          >
            {demoLoading ? (
              <span className="flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" /> Loading demo data...</span>
            ) : (
              <>
                <RiFlaskLine className="h-4 w-4" />
                Try with Sample Data
              </>
            )}
          </Button>
        </div>
      ) : (
        <Card className="border border-border shadow-none relative">
          {demoMode && (
            <div className="absolute top-3 right-3 z-10 flex items-center gap-1.5 bg-foreground text-background px-2.5 py-1 text-xs font-medium tracking-wide">
              <RiFlaskLine className="h-3 w-3" />
              Demo Mode
            </div>
          )}
          <CardContent className="p-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <RiFilmLine className="h-6 w-6 text-foreground" />
                <div>
                  <p className="text-sm font-medium text-foreground">{file.name}</p>
                  <p className="text-xs text-muted-foreground">{formatSize(file.size)}</p>
                </div>
              </div>
              <Button variant="ghost" size="sm" onClick={() => onFileChange(null)} disabled={isRunning}>
                <RiCloseLine className="h-4 w-4" />
              </Button>
            </div>
            {demoMode && (
              <div className="mt-3 pt-3 border-t border-border">
                <p className="text-xs text-muted-foreground">
                  Preloaded: <span className="font-medium text-foreground">english_hackathon_test.mp4</span> + <span className="font-medium text-foreground">ProcessGhost_Bills_Tracker.xlsx</span>
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Label className="text-xs font-medium text-foreground">Crop Region</Label>
            <div className="flex items-center gap-2">
              <Label htmlFor="full-frame" className="text-xs text-muted-foreground">Full Frame</Label>
              <Switch id="full-frame" checked={!cropEnabled} onCheckedChange={(v) => onCropEnabledChange(!v)} />
            </div>
          </div>
          {cropEnabled && (
            <div className="grid grid-cols-4 gap-2">
              {(['x', 'y', 'w', 'h'] as const).map((key) => (
                <div key={key}>
                  <Label className="text-xs text-muted-foreground uppercase">{key === 'w' ? 'W%' : key === 'h' ? 'H%' : `${key.toUpperCase()}%`}</Label>
                  <Input
                    type="number"
                    min={0}
                    max={100}
                    value={cropRegion[key]}
                    onChange={(e) => onCropRegionChange({ ...cropRegion, [key]: Number(e.target.value) })}
                    className="h-8 text-xs"
                  />
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="space-y-3">
          <Label className="text-xs font-medium text-foreground">Output Tone</Label>
          <Select value={tone} onValueChange={onToneChange}>
            <SelectTrigger className="h-9">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="step-by-step">Step-by-Step Instructional</SelectItem>
              <SelectItem value="formal">Formal / Corporate</SelectItem>
              <SelectItem value="wiki">Conversational Wiki</SelectItem>
              <SelectItem value="bulleted">Bulleted / Scannable</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <Button
        className="w-full h-11 font-medium"
        disabled={(!file && !demoMode) || isRunning}
        onClick={onGenerate}
      >
        {isRunning ? (
          <span className="flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" /> Generating SOP...</span>
        ) : (
          'Generate SOP'
        )}
      </Button>
    </div>
  )
}
