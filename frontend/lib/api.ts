import type { GateStatus } from '@/types'

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers })

  if (!res.ok) {
    let errorMessage = `HTTP ${res.status}`
    try {
      const errorData = (await res.json()) as Record<string, unknown>
      const detail = errorData.detail
      if (typeof detail === 'object' && detail !== null) {
        const detailObj = detail as Record<string, unknown>
        errorMessage = (detailObj.blocking_reason as string) ?? JSON.stringify(detail)
      } else if (typeof detail === 'string') {
        errorMessage = detail
      }
    } catch {
      // ignore parse error
    }
    throw new Error(errorMessage)
  }

  if (res.status === 204 || res.headers.get('content-length') === '0') {
    return undefined as unknown as T
  }
  return res.json() as Promise<T>
}

export const clientsApi = {
  create: (name: string, advisorId: string, advisorName?: string, advisorEmail?: string) =>
    apiFetch<{ client_id: string; name: string }>('/api/clients', {
      method: 'POST',
      body: JSON.stringify({
        name,
        advisor_id: advisorId,
        advisor_name: advisorName ?? 'Advisor',
        advisor_email: advisorEmail ?? 'advisor@taxwise.app',
      }),
    }),

  list: (advisorId: string) =>
    apiFetch<Array<{
      client_id: string
      name: string
      document_count: number
      plan_status: string
      created_at: string
    }>>(`/api/clients?advisor_id=${encodeURIComponent(advisorId)}`),

  get: (clientId: string) =>
    apiFetch<{ client_id: string; name: string; advisor_id: string }>(
      `/api/clients/${clientId}`
    ),
}

export const documentsApi = {
  upload: async (clientId: string, file: File): Promise<unknown> => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('client_id', clientId)
    const res = await fetch(`${API_BASE}/api/documents/upload`, {
      method: 'POST',
      body: formData,
    })
    if (!res.ok) {
      const err = (await res.json()) as Record<string, unknown>
      throw new Error((err.detail as string) ?? 'Upload failed')
    }
    return res.json()
  },

  list: (clientId: string) =>
    apiFetch<Array<{
      document_id: string
      filename: string
      document_type: string
      classification_status: string
      classification_confidence?: number
      institution?: string
      tax_year?: number
    }>>(`/api/documents/${clientId}`),

  delete: (documentId: string) =>
    apiFetch<void>(`/api/documents/${documentId}`, { method: 'DELETE' }),

  getExtraction: (documentId: string) =>
    apiFetch<{
      document_id: string
      filename: string
      document_type: string
      raw_extraction?: {
        document_type: string
        institution?: string
        fields: Record<string, { value: unknown; confidence: number; inferred?: boolean; note?: string }>
        extraction_notes?: string[]
        overall_confidence?: number
      }
    }>(`/api/documents/${documentId}/extraction`),
}

export const snapshotsApi = {
  getGateStatus: (clientId: string) =>
    apiFetch<GateStatus>(`/api/snapshots/${clientId}/gate-status`),

  confirmField: (clientId: string, fieldPath: string, confirmedValue: unknown, originalExtracted?: unknown) =>
    apiFetch<unknown>(`/api/snapshots/${clientId}/confirm-field`, {
      method: 'POST',
      body: JSON.stringify({
        field_path: fieldPath,
        confirmed_value: confirmedValue,
        original_extracted: originalExtracted,
      }),
    }),

  resolveContradiction: (clientId: string, contradictionId: string, resolution: string, resolvedValue?: unknown) =>
    apiFetch<unknown>(`/api/snapshots/${clientId}/resolve-contradiction`, {
      method: 'POST',
      body: JSON.stringify({
        contradiction_id: contradictionId,
        resolution,
        resolved_value: resolvedValue,
      }),
    }),

  saveIncomeProjections: (
    clientId: string,
    projections: Array<{ year: number; estimated_income: number; notes?: string }>,
    ssStartAge?: number,
    ssBenefit?: number
  ) =>
    apiFetch<unknown>(`/api/snapshots/${clientId}/income-projections`, {
      method: 'POST',
      body: JSON.stringify({
        projections,
        social_security_start_age: ssStartAge ?? null,
        social_security_monthly_benefit: ssBenefit ?? null,
      }),
    }),

  getIncomeProjections: (clientId: string) =>
    apiFetch<{
      projections?: Array<{ year: number; estimated_income: number; notes?: string }>
      social_security?: { start_age?: number; monthly_benefit_estimate?: number }
    } | null>(`/api/snapshots/${clientId}/income-projections`),

  assemble: (clientId: string) =>
    apiFetch<unknown>(`/api/snapshots/${clientId}/assemble`, { method: 'POST' }),
}

export const plansApi = {
  generate: (clientId: string) =>
    apiFetch<{ status: string; message: string }>(
      `/api/plans/${clientId}/generate`,
      { method: 'POST' }
    ),

  getLatest: (clientId: string) =>
    apiFetch<{
      plan_id: string
      status: string
      step_outputs: Record<string, unknown>
      created_at?: string
    }>(`/api/plans/${clientId}/latest`),
}

export const reportsApi = {
  generate: (planId: string, advisorName: string, clientName: string) =>
    apiFetch<{
      report_id: string
      plan_id: string
      created_at?: string
      download_url: string
      filename: string
    }>(`/api/reports/${planId}/generate`, {
      method: 'POST',
      body: JSON.stringify({ advisor_name: advisorName, client_name: clientName }),
    }),

  list: (planId: string) =>
    apiFetch<Array<{
      report_id: string
      plan_id: string
      created_at?: string
      download_url: string
    }>>(`/api/reports/${planId}/list`),

  downloadUrl: (reportId: string) => `${API_BASE}/api/reports/${reportId}/download`,
}
