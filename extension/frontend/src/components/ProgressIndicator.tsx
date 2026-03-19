import React from 'react'

interface Props {
  generating: boolean
  error: string
}

export default function ProgressIndicator({ generating, error }: Props) {
  if (error) {
    return (
      <div style={styles.container}>
        <div style={styles.errorIcon}>!</div>
        <div style={styles.errorText}>{error}</div>
      </div>
    )
  }

  return (
    <div style={styles.container}>
      <div style={styles.spinner} />
      <div style={styles.text}>
        {generating ? 'Building your dashboard...' : 'Preparing...'}
      </div>
      <div style={styles.subtext}>
        Creating Hyper extract, configuring charts, and building layout
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    textAlign: 'center',
    padding: 48,
  },
  spinner: {
    width: 40,
    height: 40,
    border: '4px solid #e0e0e0',
    borderTop: '4px solid #4E79A7',
    borderRadius: '50%',
    animation: 'spin 1s linear infinite',
    margin: '0 auto 16px',
  },
  text: {
    fontSize: 16,
    fontWeight: 600,
    color: '#333',
    marginBottom: 8,
  },
  subtext: {
    fontSize: 13,
    color: '#888',
  },
  errorIcon: {
    width: 40,
    height: 40,
    borderRadius: '50%',
    backgroundColor: '#fef2f2',
    color: '#dc2626',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 20,
    fontWeight: 700,
    margin: '0 auto 16px',
  },
  errorText: {
    color: '#dc2626',
    fontSize: 14,
  },
}
