import React from 'react'
import type { DashboardPlan } from '../utils/types'

interface Props {
  plan: DashboardPlan | null
  loading: boolean
  onAccept: () => void
  onRegenerate: () => void
  onBack: () => void
}

const CHART_ICONS: Record<string, string> = {
  Line: '\u2014',
  Bar: '\u2588',
  Scatterplot: '\u2022',
  Pie: '\u25CB',
  Heatmap: '\u2593',
  Map: '\u25CA',
  Text: 'T',
  'Tree Map': '\u25A0',
  Area: '\u25B3',
}

export default function SuggestionPreview({ plan, loading, onAccept, onRegenerate, onBack }: Props) {
  if (loading || !plan) {
    return <div style={styles.loading}>Analyzing data and generating suggestions...</div>
  }

  const warning = (plan as any)._warning as string | undefined
  const engine = (plan as any)._engine as string | undefined

  return (
    <div>
      <h2 style={styles.planTitle}>{plan.title}</h2>
      <div style={styles.layoutBadge}>
        Layout: {plan.layout}
        {engine && <span style={{ marginLeft: 8, color: engine === 'llm' ? '#16a34a' : '#d97706' }}>
          ({engine === 'llm' ? 'AI-powered' : 'Rule-based'})
        </span>}
      </div>
      {warning && (
        <div style={styles.warning}>{warning}</div>
      )}

      <div style={styles.chartList}>
        {plan.charts.map((chart, i) => (
          <div key={i} style={styles.chartCard}>
            <div style={styles.chartHeader}>
              <span style={styles.chartIcon}>
                {CHART_ICONS[chart.chart_type] || '?'}
              </span>
              <div>
                <div style={styles.chartTitle}>{chart.title}</div>
                <div style={styles.chartType}>{chart.chart_type}</div>
              </div>
            </div>
            <div style={styles.shelves}>
              {chart.shelves.map((s, j) => (
                <span key={j} style={styles.shelfBadge}>
                  {s.shelf}: {s.aggregation ? `${s.aggregation}(${s.field_name})` : s.field_name}
                </span>
              ))}
            </div>
            {chart.reason && (
              <div style={styles.reason}>{chart.reason}</div>
            )}
          </div>
        ))}
      </div>

      <div style={styles.actions}>
        <button style={styles.secondaryBtn} onClick={onBack}>Back</button>
        <button style={styles.secondaryBtn} onClick={onRegenerate}>Regenerate</button>
        <button style={styles.primaryBtn} onClick={onAccept}>
          Generate Dashboard
        </button>
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  loading: { textAlign: 'center', padding: 40, color: '#666' },
  planTitle: { fontSize: 18, fontWeight: 600, marginBottom: 4 },
  layoutBadge: { fontSize: 12, color: '#888', marginBottom: 16 },
  chartList: { display: 'flex', flexDirection: 'column', gap: 10 },
  chartCard: {
    border: '1px solid #e0e0e0',
    borderRadius: 8,
    padding: 12,
    backgroundColor: '#fafafa',
  },
  chartHeader: { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 },
  chartIcon: { fontSize: 20, width: 32, textAlign: 'center', color: '#4E79A7' },
  chartTitle: { fontWeight: 600, fontSize: 14 },
  chartType: { fontSize: 12, color: '#888' },
  shelves: { display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 6 },
  shelfBadge: {
    padding: '2px 8px',
    backgroundColor: '#e8f0fe',
    borderRadius: 4,
    fontSize: 11,
    color: '#3367d6',
  },
  reason: { fontSize: 12, color: '#666', fontStyle: 'italic' },
  warning: {
    padding: '8px 12px',
    backgroundColor: '#fffbeb',
    border: '1px solid #f59e0b',
    borderRadius: 6,
    color: '#92400e',
    fontSize: 12,
    marginBottom: 12,
  },
  actions: { display: 'flex', gap: 8, marginTop: 16, justifyContent: 'flex-end' },
  primaryBtn: { padding: '10px 20px', backgroundColor: '#4E79A7', color: 'white', border: 'none', borderRadius: 6, cursor: 'pointer', fontWeight: 600 },
  secondaryBtn: { padding: '10px 20px', backgroundColor: '#f0f0f0', color: '#333', border: '1px solid #ddd', borderRadius: 6, cursor: 'pointer' },
}
