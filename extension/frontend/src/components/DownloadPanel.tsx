import React from 'react'

interface Props {
  downloadUrl: string
  onNewDashboard: () => void
}

export default function DownloadPanel({ downloadUrl, onNewDashboard }: Props) {
  return (
    <div style={styles.container}>
      <div style={styles.checkmark}>&#10003;</div>
      <h2 style={styles.title}>Dashboard Ready!</h2>
      <p style={styles.description}>
        Your Tableau workbook has been generated successfully.
      </p>

      <div style={styles.actions}>
        <a
          href={downloadUrl}
          download="dashboard.twbx"
          style={styles.downloadBtn}
        >
          Download .twbx
        </a>
        <button style={styles.newBtn} onClick={onNewDashboard}>
          Create Another
        </button>
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    textAlign: 'center',
    padding: 48,
  },
  checkmark: {
    width: 48,
    height: 48,
    borderRadius: '50%',
    backgroundColor: '#e8f5e9',
    color: '#2e7d32',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 24,
    margin: '0 auto 16px',
  },
  title: {
    fontSize: 20,
    fontWeight: 600,
    color: '#333',
    marginBottom: 8,
  },
  description: {
    fontSize: 14,
    color: '#666',
    marginBottom: 24,
  },
  actions: {
    display: 'flex',
    gap: 12,
    justifyContent: 'center',
  },
  downloadBtn: {
    padding: '12px 24px',
    backgroundColor: '#4E79A7',
    color: 'white',
    textDecoration: 'none',
    borderRadius: 6,
    fontWeight: 600,
    fontSize: 14,
  },
  newBtn: {
    padding: '12px 24px',
    backgroundColor: '#f0f0f0',
    color: '#333',
    border: '1px solid #ddd',
    borderRadius: 6,
    cursor: 'pointer',
    fontSize: 14,
  },
}
