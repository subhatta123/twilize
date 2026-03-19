import React, { useCallback } from 'react'

interface Props {
  onImageCapture: (base64: string) => void
  hasImage: boolean
}

export default function ImageUpload({ onImageCapture, hasImage }: Props) {
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    const file = e.dataTransfer.files[0]
    if (file && file.type.startsWith('image/')) {
      readFile(file, onImageCapture)
    }
  }, [onImageCapture])

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      readFile(file, onImageCapture)
    }
  }, [onImageCapture])

  return (
    <div style={styles.container}>
      <label style={styles.label}>Reference dashboard image (optional)</label>
      <div
        style={{
          ...styles.dropZone,
          ...(hasImage ? styles.hasImage : {}),
        }}
        onDrop={handleDrop}
        onDragOver={e => e.preventDefault()}
      >
        {hasImage ? (
          <div>
            Image uploaded
            <button
              style={styles.clearBtn}
              onClick={() => onImageCapture('')}
            >
              Clear
            </button>
          </div>
        ) : (
          <div>
            <div>Drag & drop a reference dashboard image</div>
            <div style={styles.orText}>or</div>
            <label style={styles.browseBtn}>
              Browse
              <input
                type="file"
                accept="image/*"
                style={{ display: 'none' }}
                onChange={handleFileSelect}
              />
            </label>
          </div>
        )}
      </div>
    </div>
  )
}

function readFile(file: File, callback: (base64: string) => void) {
  const reader = new FileReader()
  reader.onload = () => {
    const result = reader.result as string
    // Strip data URL prefix to get pure base64
    const base64 = result.split(',')[1] || result
    callback(base64)
  }
  reader.readAsDataURL(file)
}

const styles: Record<string, React.CSSProperties> = {
  container: { marginBottom: 16 },
  label: { display: 'block', fontWeight: 600, marginBottom: 6, fontSize: 14 },
  dropZone: {
    border: '2px dashed #ccc',
    borderRadius: 8,
    padding: 24,
    textAlign: 'center',
    color: '#888',
    fontSize: 14,
    cursor: 'pointer',
  },
  hasImage: {
    borderColor: '#4E79A7',
    backgroundColor: '#f0f7ff',
    color: '#4E79A7',
  },
  orText: { margin: '8px 0', fontSize: 12, color: '#aaa' },
  browseBtn: {
    padding: '6px 16px',
    backgroundColor: '#f0f0f0',
    borderRadius: 4,
    cursor: 'pointer',
    fontSize: 13,
  },
  clearBtn: {
    marginLeft: 12,
    padding: '4px 12px',
    backgroundColor: 'transparent',
    border: '1px solid #4E79A7',
    borderRadius: 4,
    color: '#4E79A7',
    cursor: 'pointer',
    fontSize: 12,
  },
}
