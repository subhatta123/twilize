import type { DashboardPlan, FieldInfo } from './types'

const BASE_URL = '/api'

export async function suggestDashboard(
  fields: FieldInfo[],
  rowCount: number,
  prompt: string,
  imageBase64: string,
  maxCharts: number = 6,
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
      })),
      prompt,
      row_count: rowCount,
      max_charts: maxCharts,
      image_base64: imageBase64,
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
