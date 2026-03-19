import { useState, useEffect, useCallback } from 'react'
import type { FieldInfo } from '../utils/types'
import { initTableauExtension, extractTableauData } from '../utils/tableauApi'

export function useTableauData() {
  const [fields, setFields] = useState<FieldInfo[]>([])
  const [dataRows, setDataRows] = useState<unknown[][]>([])
  const [rowCount, setRowCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadData = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      await initTableauExtension()
      const result = await extractTableauData()
      setFields(result.fields)
      setDataRows(result.dataRows)
      setRowCount(result.rowCount)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to load data'
      setError(msg)
      // In development outside Tableau, provide demo fields and data
      if (typeof tableau === 'undefined') {
        setFields([
          { name: 'Category', datatype: 'string', role: 'dimension', cardinality: 3, sample_values: ['Furniture', 'Technology', 'Office Supplies'] },
          { name: 'Region', datatype: 'string', role: 'dimension', cardinality: 4, sample_values: ['East', 'West', 'Central', 'South'] },
          { name: 'Sales', datatype: 'float', role: 'measure', cardinality: 500, sample_values: ['100.5', '250.3', '75.0'] },
          { name: 'Profit', datatype: 'float', role: 'measure', cardinality: 400, sample_values: ['20.5', '50.3', '15.0'] },
          { name: 'Order Date', datatype: 'date', role: 'dimension', cardinality: 365, sample_values: ['2024-01-15', '2024-02-20'] },
        ])
        // Provide demo data rows so generated workbooks have actual data
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
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadData()
  }, [loadData])

  return { fields, dataRows, rowCount, loading, error, reload: loadData }
}

declare const tableau: unknown
