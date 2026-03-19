import { useState, useMemo } from 'react'
import type { DashboardPlan, FieldInfo } from '../utils/types'
import { suggestDashboard, generateWorkbook } from '../utils/api'

export function useGeneration() {
  const [suggesting, setSuggesting] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [downloadUrl, setDownloadUrl] = useState('')
  const [error, setError] = useState('')

  const generate = useMemo(() => ({
    suggest: async (
      fields: FieldInfo[],
      rowCount: number,
      prompt: string,
      imageBase64: string,
    ): Promise<DashboardPlan | null> => {
      setSuggesting(true)
      setError('')
      try {
        const plan = await suggestDashboard(fields, rowCount, prompt, imageBase64)
        return plan
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Suggestion failed')
        return null
      } finally {
        setSuggesting(false)
      }
    },

    build: async (
      fields: FieldInfo[],
      dataRows: unknown[][],
      plan: DashboardPlan,
    ): Promise<string | null> => {
      setGenerating(true)
      setError('')
      try {
        const url = await generateWorkbook(fields, dataRows, plan)
        setDownloadUrl(url)
        return url
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Generation failed')
        return null
      } finally {
        setGenerating(false)
      }
    },
  }), [])

  return { generate, suggesting, generating, downloadUrl, error }
}
