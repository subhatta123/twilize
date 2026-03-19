import React, { useState } from 'react'
import DataPreview from './components/DataPreview'
import PromptInput from './components/PromptInput'
import ImageUpload from './components/ImageUpload'
import SuggestionPreview from './components/SuggestionPreview'
import ProgressIndicator from './components/ProgressIndicator'
import DownloadPanel from './components/DownloadPanel'
import { useTableauData } from './hooks/useTableauData'
import { useGeneration } from './hooks/useGeneration'
import type { DashboardPlan } from './utils/types'

type Step = 'data' | 'prompt' | 'preview' | 'generating' | 'download'

export default function App() {
  const [step, setStep] = useState<Step>('data')
  const [prompt, setPrompt] = useState('')
  const [imageBase64, setImageBase64] = useState('')
  const [plan, setPlan] = useState<DashboardPlan | null>(null)

  const { fields, dataRows, rowCount, loading: dataLoading, error: dataError, reload } = useTableauData()
  const { generate, suggesting, generating, downloadUrl, error: genError } = useGeneration()

  const handleSuggest = async () => {
    setStep('preview')
    const suggested = await generate.suggest(fields, rowCount, prompt, imageBase64)
    if (suggested) {
      setPlan(suggested)
    }
  }

  const handleGenerate = async () => {
    if (!plan) return
    setStep('generating')
    const url = await generate.build(fields, dataRows, plan)
    if (url) {
      setStep('download')
    }
  }

  const handleRegenerate = () => {
    setPlan(null)
    setStep('prompt')
  }

  return (
    <div style={styles.container}>
      <h1 style={styles.title}>cwtwb Dashboard Builder</h1>

      {step === 'data' && (
        <DataPreview
          fields={fields}
          rowCount={rowCount}
          loading={dataLoading}
          error={dataError}
          onContinue={() => setStep('prompt')}
          onReload={reload}
        />
      )}

      {step === 'prompt' && (
        <div>
          <PromptInput
            prompt={prompt}
            onChange={setPrompt}
          />
          <ImageUpload
            onImageCapture={setImageBase64}
            hasImage={!!imageBase64}
          />
          <div style={styles.actions}>
            <button style={styles.secondaryBtn} onClick={() => setStep('data')}>
              Back
            </button>
            <button style={styles.primaryBtn} onClick={handleSuggest}>
              Suggest Dashboard
            </button>
          </div>
        </div>
      )}

      {step === 'preview' && (
        <SuggestionPreview
          plan={plan}
          loading={suggesting}
          onAccept={handleGenerate}
          onRegenerate={handleRegenerate}
          onBack={() => setStep('prompt')}
        />
      )}

      {step === 'generating' && (
        <ProgressIndicator
          generating={generating}
          error={genError}
        />
      )}

      {step === 'download' && (
        <DownloadPanel
          downloadUrl={downloadUrl}
          onNewDashboard={handleRegenerate}
        />
      )}

      {genError && step !== 'generating' && (
        <div style={styles.error}>{genError}</div>
      )}
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    maxWidth: 600,
    margin: '0 auto',
    padding: 16,
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  },
  title: {
    fontSize: 20,
    fontWeight: 600,
    marginBottom: 16,
    color: '#333',
  },
  actions: {
    display: 'flex',
    gap: 8,
    marginTop: 16,
  },
  primaryBtn: {
    padding: '10px 20px',
    backgroundColor: '#4E79A7',
    color: 'white',
    border: 'none',
    borderRadius: 6,
    cursor: 'pointer',
    fontWeight: 600,
  },
  secondaryBtn: {
    padding: '10px 20px',
    backgroundColor: '#f0f0f0',
    color: '#333',
    border: '1px solid #ddd',
    borderRadius: 6,
    cursor: 'pointer',
  },
  error: {
    marginTop: 12,
    padding: 12,
    backgroundColor: '#fef2f2',
    color: '#dc2626',
    borderRadius: 6,
    fontSize: 14,
  },
}
