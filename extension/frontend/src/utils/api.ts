import type { DashboardPlan, FieldInfo } from './types'

const BASE_URL = '/api'

export async function suggestDashboard(
  fields: FieldInfo[],
  rowCount: number,
  prompt: string,
  imageBase64: string,
  maxCharts: number = 5,
  sampleRows: unknown[][] = [],
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
    }),
  })

  if (!response.ok) {
    throw new Error(`Suggestion failed: ${response.statusText}`)
  }

  return response.json()
}

export async function generateWorkbook(
  fields: FieldInfo[],
  dataRows: unknown[][],
  plan: DashboardPlan,
): Promise<string> {
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
    }),
  })

  if (!response.ok) {
    throw new Error(`Generation failed: ${response.statusText}`)
  }

  const blob = await response.blob()
  return URL.createObjectURL(blob)
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
