import React, { useEffect, useState } from 'react'
import { setApiKey, getKeyStatus } from '../utils/api'

interface Props {
  onStatusChange?: (configured: boolean) => void
}

export default function ApiKeySettings({ onStatusChange }: Props) {
  const [open, setOpen] = useState(false)
  const [key, setKey] = useState('')
  const [configured, setConfigured] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    getKeyStatus().then((status) => {
      setConfigured(status)
      onStatusChange?.(status)
    })
  }, [])

  const handleSave = async () => {
    if (!key.trim()) return
    setSaving(true)
    setError('')
    try {
      await setApiKey(key.trim())
      setConfigured(true)
      setKey('')
      onStatusChange?.(true)
    } catch (e: any) {
      setError(e.message ?? 'Failed to save key')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={styles.wrapper}>
      <button style={styles.toggle} onClick={() => setOpen(!open)}>
        {open ? '\u25BC' : '\u25B6'} API Key Settings
        {configured && <span style={styles.check}> ✓</span>}
      </button>

      {open && (
        <div style={styles.body}>
          <p style={styles.hint}>
            {configured
              ? 'An API key is configured. Enter a new key below to replace it.'
              : 'No API key configured. Provide an Anthropic API key for prompt-driven dashboards.'}
          </p>
          <div style={styles.row}>
            <input
              type="password"
              style={styles.input}
              placeholder={configured ? '\u25CF\u25CF\u25CF\u25CF\u25CF\u25CF\u25CF\u25CF' : 'sk-ant-...'}
              value={key}
              onChange={(e) => setKey(e.target.value)}
            />
            <button
              style={styles.saveBtn}
              onClick={handleSave}
              disabled={saving || !key.trim()}
            >
              {saving ? 'Saving...' : 'Save'}
            </button>
          </div>
          {error && <div style={styles.error}>{error}</div>}
        </div>
      )}
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  wrapper: {
    marginBottom: 12,
    border: '1px solid #e0e0e0',
    borderRadius: 6,
    overflow: 'hidden',
  },
  toggle: {
    width: '100%',
    padding: '8px 12px',
    background: '#fafafa',
    border: 'none',
    textAlign: 'left',
    cursor: 'pointer',
    fontSize: 13,
    fontWeight: 600,
    color: '#555',
  },
  check: {
    color: '#16a34a',
    fontWeight: 700,
  },
  body: {
    padding: '8px 12px 12px',
  },
  hint: {
    fontSize: 12,
    color: '#777',
    margin: '0 0 8px',
  },
  row: {
    display: 'flex',
    gap: 8,
  },
  input: {
    flex: 1,
    padding: '6px 10px',
    border: '1px solid #ccc',
    borderRadius: 4,
    fontSize: 13,
  },
  saveBtn: {
    padding: '6px 16px',
    backgroundColor: '#4E79A7',
    color: 'white',
    border: 'none',
    borderRadius: 4,
    cursor: 'pointer',
    fontWeight: 600,
    fontSize: 13,
  },
  error: {
    marginTop: 6,
    fontSize: 12,
    color: '#dc2626',
  },
}
