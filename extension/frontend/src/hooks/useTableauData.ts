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
      // In development outside Tableau, provide demo fields
      if (typeof tableau === 'undefined') {
        setFields([
          { name: 'Category', datatype: 'string', role: 'dimension', cardinality: 3, sample_values: ['Furniture', 'Technology', 'Office Supplies'] },
          { name: 'Region', datatype: 'string', role: 'dimension', cardinality: 4, sample_values: ['East', 'West', 'Central', 'South'] },
          { name: 'Sales', datatype: 'float', role: 'measure', cardinality: 500, sample_values: ['100.5', '250.3', '75.0'] },
          { name: 'Profit', datatype: 'float', role: 'measure', cardinality: 400, sample_values: ['20.5', '50.3', '15.0'] },
          { name: 'Order Date', datatype: 'date', role: 'dimension', cardinality: 365, sample_values: ['2024-01-15', '2024-02-20'] },
        ])
        setRowCount(1000)
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
