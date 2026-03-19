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
            data: Array<Array<{ value: unknown; formattedValue: string }>>
            totalRowCount: number
          }>
          getSummaryDataAsync: (
            options?: { maxRows: number }
          ) => Promise<{
            columns: Array<{ fieldName: string; dataType: string }>
            data: Array<Array<{ value: unknown; formattedValue: string }>>
            totalRowCount: number
          }>
        }>
      }
    }
  }
}

export async function initTableauExtension(): Promise<void> {
  if (typeof tableau === 'undefined') {
    throw new Error('Tableau Extensions API not available')
  }
  await tableau.extensions.initializeAsync()
}

export async function extractTableauData(): Promise<{
  fields: FieldInfo[]
  dataRows: unknown[][]
  rowCount: number
}> {
  if (typeof tableau === 'undefined') {
    throw new Error('Tableau Extensions API not available')
  }

  const dashboard = tableau.extensions.dashboardContent.dashboard
  console.log(`[cwtwb] Found ${dashboard.worksheets.length} worksheet(s) on dashboard`)

  if (dashboard.worksheets.length === 0) {
    throw new Error(
      'No worksheets found on this dashboard. ' +
      'Add at least one worksheet to the dashboard before using the extension.'
    )
  }

  // Try each worksheet until we find one with data
  for (const worksheet of dashboard.worksheets) {
    console.log(`[cwtwb] Trying worksheet: ${worksheet.name}`)

    try {
      // First try getUnderlyingTablesAsync (full data access)
      const tables = await worksheet.getUnderlyingTablesAsync()
      console.log(`[cwtwb] Worksheet "${worksheet.name}" has ${tables.length} table(s)`)

      if (tables.length > 0) {
        const tableData = await worksheet.getUnderlyingTableDataAsync(
          tables[0].id,
          { maxRows: 10000 }
        )

        console.log(
          `[cwtwb] Got ${tableData.data.length} rows, ` +
          `${tableData.columns.length} columns from "${worksheet.name}"`
        )

        if (tableData.data.length > 0) {
          return processTableData(tableData)
        }
      }

      // Fallback: try getSummaryDataAsync (aggregated view data)
      try {
        const summaryData = await worksheet.getSummaryDataAsync({ maxRows: 10000 })
        console.log(
          `[cwtwb] Summary data: ${summaryData.data.length} rows from "${worksheet.name}"`
        )
        if (summaryData.data.length > 0) {
          return processTableData(summaryData)
        }
      } catch (summaryErr) {
        console.log(`[cwtwb] getSummaryDataAsync not available for "${worksheet.name}"`)
      }
    } catch (err) {
      console.warn(`[cwtwb] Error reading "${worksheet.name}":`, err)
    }
  }

  throw new Error(
    'No data found in any worksheet. Make sure worksheets on this dashboard ' +
    'have fields on shelves (drag a field to Rows/Columns first).'
  )
}

function processTableData(tableData: {
  columns: Array<{ fieldName: string; dataType: string }>
  data: Array<Array<{ value: unknown; formattedValue: string }>>
  totalRowCount: number
}): {
  fields: FieldInfo[]
  dataRows: unknown[][]
  rowCount: number
} {
  const uniqueValues: Map<string, Set<string>> = new Map()
  const sampleValues: Map<string, string[]> = new Map()

  for (const col of tableData.columns) {
    uniqueValues.set(col.fieldName, new Set())
    sampleValues.set(col.fieldName, [])
  }

  const dataRows: unknown[][] = []
  for (const row of tableData.data) {
    const rowValues: unknown[] = []
    for (let i = 0; i < row.length && i < tableData.columns.length; i++) {
      const cell = row[i]
      // Use formattedValue for dates (preserves readable format),
      // native value for numbers
      const col = tableData.columns[i]
      let val: unknown

      if (col.dataType === 'date' || col.dataType === 'date-time' || col.dataType === 'datetime') {
        // Use formattedValue for dates to preserve format for CSV
        val = cell.formattedValue || String(cell.value)
      } else {
        val = cell.value
      }

      rowValues.push(val)

      const strVal = String(val)
      uniqueValues.get(col.fieldName)!.add(strVal)
      const samples = sampleValues.get(col.fieldName)!
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

  console.log(`[cwtwb] Processed ${dataRows.length} rows, ${fields.length} fields`)
  console.log(`[cwtwb] Fields:`, fields.map(f => `${f.name}(${f.datatype})`).join(', '))

  return {
    fields,
    dataRows,
    rowCount: tableData.totalRowCount,
  }
}
