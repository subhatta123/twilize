import React from 'react'
import type { FieldInfo } from '../utils/types'

interface Props {
  fields: FieldInfo[]
  rowCount: number
  loading: boolean
  error: string
  onContinue: () => void
  onReload: () => void
}

export default function DataPreview({ fields, rowCount, loading, error, onContinue, onReload }: Props) {
  if (loading) {
    return <div style={styles.loading}>Loading data from Tableau...</div>
  }

  return (
    <div>
      {error && <div style={styles.warning}>{error}</div>}

      <div style={styles.summary}>
        <strong>{fields.length}</strong> fields | <strong>{rowCount.toLocaleString()}</strong> rows
      </div>

      <table style={styles.table}>
        <thead>
          <tr>
            <th style={styles.th}>Field</th>
            <th style={styles.th}>Type</th>
            <th style={styles.th}>Cardinality</th>
            <th style={styles.th}>Sample</th>
          </tr>
        </thead>
        <tbody>
          {fields.map(f => (
            <tr key={f.name}>
              <td style={styles.td}>{f.name}</td>
              <td style={styles.td}>
                <span style={{ ...styles.badge, backgroundColor: typeColor(f.datatype) }}>
                  {f.datatype}
                </span>
              </td>
              <td style={styles.td}>{f.cardinality}</td>
              <td style={{ ...styles.td, ...styles.sample }}>
                {f.sample_values.slice(0, 3).join(', ')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div style={styles.actions}>
        <button style={styles.secondaryBtn} onClick={onReload}>Reload</button>
        <button
          style={styles.primaryBtn}
          onClick={onContinue}
          disabled={fields.length === 0}
        >
          Continue
        </button>
      </div>
    </div>
  )
}

function typeColor(datatype: string): string {
  switch (datatype) {
    case 'float': case 'int': case 'real': return '#e8f5e9'
    case 'date': case 'datetime': case 'date-time': return '#e3f2fd'
    case 'bool': return '#fff3e0'
    default: return '#f3e5f5'
  }
}

const styles: Record<string, React.CSSProperties> = {
  loading: { textAlign: 'center', padding: 40, color: '#666' },
  warning: { padding: 8, backgroundColor: '#fff8e1', borderRadius: 6, marginBottom: 12, fontSize: 13 },
  summary: { marginBottom: 12, fontSize: 14, color: '#555' },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
  th: { textAlign: 'left', padding: '8px 12px', borderBottom: '2px solid #e0e0e0', color: '#666' },
  td: { padding: '6px 12px', borderBottom: '1px solid #f0f0f0' },
  sample: { color: '#888', fontSize: 12, maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  badge: { padding: '2px 6px', borderRadius: 4, fontSize: 11, fontWeight: 500 },
  actions: { display: 'flex', gap: 8, marginTop: 16, justifyContent: 'flex-end' },
  primaryBtn: { padding: '10px 20px', backgroundColor: '#4E79A7', color: 'white', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 600 },
  secondaryBtn: { padding: '10px 20px', backgroundColor: '#f0f0f0', color: '#333', border: '1px solid #ddd', borderRadius: 6, cursor: 'pointer' },
}
