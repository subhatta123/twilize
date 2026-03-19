import type { FieldInfo } from './types'

declare const tableau: {
  extensions: {
    initializeAsync: () => Promise<void>
    dashboardContent: {
      dashboard: {
        worksheets: Array<{
          name: string
          getUnderlyingTablesAsync: () => Promise<
            Array<{ id: string; caption: string }>
          >
          getUnderlyingTableDataAsync: (
            tableId: string,
            options: { maxRows: number }
          ) => Promise<{
            columns: Array<{ fieldName: string; dataType: string }>
            data: Array<Array<{ value: unknown }>>
            totalRowCount: number
          }>
        }>
      }
    }
  }
}

export async function initTableauExtension(): Promise<void> {
  if (typeof tableau === 'undefined') {
    console.warn('Tableau Extensions API not available (running outside Tableau)')
    return
  }
  await tableau.extensions.initializeAsync()
}

export async function extractTableauData(): Promise<{
  fields: FieldInfo[]
  dataRows: unknown[][]
  rowCount: number
}> {
  if (typeof tableau === 'undefined') {
    return { fields: [], dataRows: [], rowCount: 0 }
  }

  const dashboard = tableau.extensions.dashboardContent.dashboard
  if (dashboard.worksheets.length === 0) {
    return { fields: [], dataRows: [], rowCount: 0 }
  }

  const worksheet = dashboard.worksheets[0]
  const tables = await worksheet.getUnderlyingTablesAsync()
  if (tables.length === 0) {
    return { fields: [], dataRows: [], rowCount: 0 }
  }

  const tableData = await worksheet.getUnderlyingTableDataAsync(tables[0].id, {
    maxRows: 10000,
  })

  const uniqueValues: Map<string, Set<string>> = new Map()
  const sampleValues: Map<string, string[]> = new Map()

  for (const col of tableData.columns) {
    uniqueValues.set(col.fieldName, new Set())
    sampleValues.set(col.fieldName, [])
  }

  const dataRows: unknown[][] = []
  for (const row of tableData.data) {
    const rowValues: unknown[] = []
    for (let i = 0; i < row.length; i++) {
      const val = row[i].value
      rowValues.push(val)

      const fieldName = tableData.columns[i].fieldName
      const strVal = String(val)
      uniqueValues.get(fieldName)!.add(strVal)
      const samples = sampleValues.get(fieldName)!
      if (samples.length < 5) {
        samples.push(strVal)
      }
    }
    dataRows.push(rowValues)
  }

  const fields: FieldInfo[] = tableData.columns.map(col => ({
    name: col.fieldName,
    datatype: col.dataType,
    role: '',
    cardinality: uniqueValues.get(col.fieldName)?.size ?? 0,
    sample_values: sampleValues.get(col.fieldName) ?? [],
  }))

  return {
    fields,
    dataRows,
    rowCount: tableData.totalRowCount,
  }
}
