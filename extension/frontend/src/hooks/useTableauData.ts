import { useState, useEffect, useCallback } from 'react'
import type { FieldInfo } from '../utils/types'
import { initTableauExtension, extractTableauData } from '../utils/tableauApi'

declare const tableau: unknown

/**
 * Check if we are truly inside a Tableau extension host.
 * The Extensions API script defines `tableau` globally even in a browser,
 * so we need a deeper check.
 */
function isInsideTableau(): boolean {
  try {
    // Inside Tableau, the parent window is the Tableau host
    // Outside Tableau (standalone browser), we're the top window
    // The Extensions API script sets tableau.extensions but dashboardContent
    // is only available after initializeAsync succeeds
    if (typeof tableau === 'undefined' || !tableau) return false
    const t = tableau as any
    if (!t.extensions) return false
    // If we're in an iframe (Tableau loads extensions in iframes), likely inside Tableau
    if (window.self !== window.top) return true
    // Check if initializeAsync exists (it always does with the script loaded)
    // but dashboardContent won't exist until init succeeds
    // Best heuristic: check if we're in an iframe
    return false
  } catch {
    // Cross-origin iframe access throws — that means we're in Tableau's iframe
    return true
  }
}

export function useTableauData() {
  const [fields, setFields] = useState<FieldInfo[]>([])
  const [dataRows, setDataRows] = useState<unknown[][]>([])
  const [rowCount, setRowCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadData = useCallback(async () => {
    setLoading(true)
    setError('')

    const inTableau = isInsideTableau()
    console.log('[cwtwb] isInsideTableau:', inTableau)

    if (!inTableau) {
      // Not inside Tableau — use demo data
      setFields([
        { name: 'Category', datatype: 'string', role: 'dimension', cardinality: 3, sample_values: ['Furniture', 'Technology', 'Office Supplies'] },
        { name: 'Region', datatype: 'string', role: 'dimension', cardinality: 4, sample_values: ['East', 'West', 'Central', 'South'] },
        { name: 'Sales', datatype: 'float', role: 'measure', cardinality: 500, sample_values: ['100.5', '250.3', '75.0'] },
        { name: 'Profit', datatype: 'float', role: 'measure', cardinality: 400, sample_values: ['20.5', '50.3', '15.0'] },
        { name: 'Order Date', datatype: 'date', role: 'dimension', cardinality: 365, sample_values: ['2024-01-15', '2024-02-20'] },
      ])
      setDataRows([
        ['Furniture', 'East', 1200.5, 300.2, '2024-01-15'],
        ['Technology', 'West', 3500.0, 800.0, '2024-01-20'],
        ['Office Supplies', 'Central', 450.3, 120.1, '2024-02-10'],
        ['Furniture', 'South', 980.0, 210.5, '2024-02-15'],
        ['Technology', 'East', 2100.7, 550.3, '2024-03-01'],
        ['Office Supplies', 'West', 670.0, 180.0, '2024-03-10'],
        ['Furniture', 'Central', 1550.0, 380.0, '2024-03-20'],
        ['Technology', 'South', 4200.0, 1100.5, '2024-04-05'],
        ['Office Supplies', 'East', 320.0, 85.0, '2024-04-15'],
        ['Furniture', 'West', 890.0, 195.3, '2024-05-01'],
        ['Technology', 'Central', 2800.0, 720.0, '2024-05-15'],
        ['Office Supplies', 'South', 510.5, 140.2, '2024-06-01'],
        ['Furniture', 'East', 1750.0, 420.0, '2024-06-15'],
        ['Technology', 'West', 3100.0, 850.0, '2024-07-01'],
        ['Office Supplies', 'Central', 390.0, 95.5, '2024-07-15'],
        ['Furniture', 'South', 1100.0, 280.0, '2024-08-01'],
        ['Technology', 'East', 2650.0, 680.0, '2024-08-15'],
        ['Office Supplies', 'West', 580.0, 155.0, '2024-09-01'],
        ['Furniture', 'Central', 1350.0, 340.0, '2024-09-15'],
        ['Technology', 'South', 3800.0, 950.0, '2024-10-01'],
      ])
      setRowCount(20)
      setError('Running outside Tableau — using demo data')
      setLoading(false)
      return
    }

    // Inside Tableau — try to read real data
    try {
      await initTableauExtension()
      console.log('[cwtwb] initializeAsync succeeded')
      const result = await extractTableauData()
      console.log('[cwtwb] extractTableauData got', result.fields.length, 'fields,', result.dataRows.length, 'rows')
      setFields(result.fields)
      setDataRows(result.dataRows)
      setRowCount(result.rowCount)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load data from Tableau'
      console.error('[cwtwb] Tableau data extraction failed:', msg)
      setError(msg)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadData()
  }, [loadData])

  return { fields, dataRows, rowCount, loading, error, reload: loadData }
}
