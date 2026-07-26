import { NextRequest, NextResponse } from 'next/server'

// Allow large video uploads and longer processing time
export const maxDuration = 120 // 2 minutes for large video uploads

const LYZR_UPLOAD_URL = `${process.env.LYZR_AGENT_BASE_URL || 'https://agent-prod.studio.lyzr.ai'}/v3/assets/upload`
const LYZR_API_KEY = process.env.LYZR_API_KEY || ''

export async function POST(request: NextRequest) {
  try {
    if (!LYZR_API_KEY) {
      return NextResponse.json(
        {
          success: false,
          asset_ids: [],
          files: [],
          total_files: 0,
          successful_uploads: 0,
          failed_uploads: 0,
          message: 'LYZR_API_KEY not configured',
          timestamp: new Date().toISOString(),
          error: 'LYZR_API_KEY not configured on server',
        },
        { status: 500 }
      )
    }

    const formData = await request.formData()
    const files = formData.getAll('files')

    if (files.length === 0) {
      return NextResponse.json(
        {
          success: false,
          asset_ids: [],
          files: [],
          total_files: 0,
          successful_uploads: 0,
          failed_uploads: 0,
          message: 'No files provided',
          timestamp: new Date().toISOString(),
          error: 'No files provided',
        },
        { status: 400 }
      )
    }

    // Forward the request to Lyzr API
    // Convert File objects to Buffer-backed Blobs to ensure proper serialization
    const uploadFormData = new FormData()
    for (const file of files) {
      if (file instanceof File) {
        const arrayBuffer = await file.arrayBuffer()
        const blob = new Blob([arrayBuffer], { type: file.type || 'application/octet-stream' })
        uploadFormData.append('files', blob, file.name)
      }
    }

    const response = await fetch(LYZR_UPLOAD_URL, {
      method: 'POST',
      headers: {
        'x-api-key': LYZR_API_KEY,
      },
      body: uploadFormData,
    })

    if (response.ok) {
      const data = await response.json()

      const uploadedFiles = (data.results || []).map((r: any) => ({
        asset_id: r.asset_id || '',
        file_name: r.file_name || '',
        success: r.success ?? true,
        error: r.error,
      }))

      const assetIds = uploadedFiles
        .filter((f: any) => f.success && f.asset_id)
        .map((f: any) => f.asset_id)

      return NextResponse.json({
        success: true,
        asset_ids: assetIds,
        files: uploadedFiles,
        total_files: data.total_files || files.length,
        successful_uploads: data.successful_uploads || assetIds.length,
        failed_uploads: data.failed_uploads || 0,
        message: `Successfully uploaded ${assetIds.length} file(s)`,
        timestamp: new Date().toISOString(),
      })
    } else {
      const errorText = await response.text()
      console.error('Upload API error:', response.status, errorText)

      // Parse error detail from Lyzr API response
      let errorMsg = `Upload failed with status ${response.status}`
      try {
        const errorData = JSON.parse(errorText)
        errorMsg = errorData?.detail || errorData?.error || errorData?.message || errorMsg
      } catch {
        // Use raw text if not JSON
        if (errorText) errorMsg = errorText
      }

      return NextResponse.json(
        {
          success: false,
          asset_ids: [],
          files: [],
          total_files: files.length,
          successful_uploads: 0,
          failed_uploads: files.length,
          message: errorMsg,
          timestamp: new Date().toISOString(),
          error: errorMsg,
        },
        { status: response.status }
      )
    }
  } catch (error) {
    console.error('File upload error:', error)

    return NextResponse.json(
      {
        success: false,
        asset_ids: [],
        files: [],
        total_files: 0,
        successful_uploads: 0,
        failed_uploads: 0,
        message: 'Server error during upload',
        timestamp: new Date().toISOString(),
        error: error instanceof Error ? error.message : String(error),
      },
      { status: 500 }
    )
  }
}
