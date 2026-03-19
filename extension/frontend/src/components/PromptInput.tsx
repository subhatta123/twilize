import React from 'react'

interface Props {
  prompt: string
  onChange: (value: string) => void
}

export default function PromptInput({ prompt, onChange }: Props) {
  return (
    <div style={styles.container}>
      <label style={styles.label}>Describe your ideal dashboard</label>
      <textarea
        style={styles.textarea}
        value={prompt}
        onChange={e => onChange(e.target.value)}
        placeholder="E.g., Show me a sales dashboard with a line chart showing revenue over time, a bar chart of sales by category, and a map of sales by region..."
        rows={4}
      />
      <div style={styles.hint}>
        Leave empty for automatic suggestions based on your data shape.
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: { marginBottom: 16 },
  label: { display: 'block', fontWeight: 600, marginBottom: 6, fontSize: 14 },
  textarea: {
    width: '100%',
    padding: 12,
    border: '1px solid #ddd',
    borderRadius: 6,
    fontSize: 14,
    fontFamily: 'inherit',
    resize: 'vertical',
    boxSizing: 'border-box',
  },
  hint: { fontSize: 12, color: '#888', marginTop: 4 },
}
