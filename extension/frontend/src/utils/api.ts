import type { DashboardPlan, FieldInfo } from './types'

const BASE_URL = '/api'

/**
 * Decoded shape of the `X-Twilize-Manifest` response header on
 * `/api/generate`. Matches the dict returned by
 * `extension/backend/pipeline.py::generate_workbook`. All fields are
 * optional so older backend versions (which don't set the header) don't
 * break older frontends.
 */
export interface GenerationManifest {
  output_path?: string
  dashboards?: unknown[]
  filters?: {
    count?: number
    scope?: string | null
    fields?: string[]
    clickable?: boolean
    min_height_px?: number
    global_scope_applied?: boolean
    filter_groups?: Record<string, number>
    per_dashboard?: unknown[]
  }
  global_filter_groups?: Record<string, number>
  required_charts_requested?: number
  required_charts_fulfilled?: number
  style_reference?: { path?: string; extracted?: unknown } | null
  warnings?: string[]
  job_id?: string
}

export interface GenerationResult {
  downloadUrl: string
  manifest: GenerationManifest | null
  jobId: string | null
}

export async function suggestDashboard(
  fields: FieldInfo[],
  rowCount: number,
  prompt: string,
  imageBase64: string,
  maxCharts: number = 5,
  sampleRows: unknown[][] = [],
  requiredCharts: unknown[] = [],
): Promise<DashboardPlan> {
  const response = await fetch(`${BASE_URL}/suggest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      fields: fields.map(f => ({
        name: f.name,
        datatype: f.datatype,
        role: f.role,
        cardinality: f.cardinality,
        sample_values: f.sample_values,
        null_count: f.null_count ?? 0,
      })),
      prompt,
      row_count: rowCount,
      max_charts: maxCharts,
      image_base64: imageBase64,
      sample_rows: sampleRows,
      required_charts: requiredCharts,
    }),
  })

  if (!response.ok) {
    throw new Error(`Suggestion failed: ${response.statusText}`)
  }

  return response.json()
}

/**
 * Base64-decode the `X-Twilize-Manifest` response header.
 * Returns null on any decode failure so callers can gracefully fall back
 * to the old "file-only" behavior.
 */
function decodeManifestHeader(raw: string | null): GenerationManifest | null {
  if (!raw) return null
  try {
    const json = atob(raw)
    return JSON.parse(json) as GenerationManifest
  } catch {
    return null
  }
}

export async function generateWorkbook(
  fields: FieldInfo[],
  dataRows: unknown[][],
  plan: DashboardPlan,
  opts: {
    referenceImageBase64?: string
    requiredCharts?: unknown[]
  } = {},
): Promise<GenerationResult> {
  const response = await fetch(`${BASE_URL}/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      fields: fields.map(f => ({
        name: f.name,
        datatype: f.datatype,
        role: f.role,
        cardinality: f.cardinality,
        null_count: f.null_count ?? 0,
      })),
      data_rows: dataRows,
      plan,
      reference_image_base64: opts.referenceImageBase64 ?? '',
      required_charts: opts.requiredCharts ?? [],
    }),
  })

  if (!response.ok) {
    throw new Error(`Generation failed: ${response.statusText}`)
  }

  const manifest = decodeManifestHeader(response.headers.get('X-Twilize-Manifest'))
  const jobId = response.headers.get('X-Twilize-Job-Id')
  const blob = await response.blob()
  return {
    downloadUrl: URL.createObjectURL(blob),
    manifest,
    jobId,
  }
}

export async function checkHealth(): Promise<boolean> {
  try {
    const response = await fetch(`${BASE_URL}/health`)
    return response.ok
  } catch {
    return false
  }
}

export async function setApiKey(key: string): Promise<void> {
  const response = await fetch(`${BASE_URL}/set-key`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: key }),
  })
  if (!response.ok) {
    throw new Error(`Failed to set API key: ${response.statusText}`)
  }
}

export async function getKeyStatus(): Promise<boolean> {
  try {
    const response = await fetch(`${BASE_URL}/key-status`)
    if (!response.ok) return false
    const data = await response.json()
    return data.configured === true
  } catch {
    return false
  }
}
