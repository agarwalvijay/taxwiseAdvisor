'use client'
import { useEffect, useRef, useState, useCallback } from 'react'
import { useUser } from '@clerk/nextjs'
import Header from '@/components/Header'
import GateReview from '@/components/GateReview'
import ExtractionDetails from '@/components/ExtractionDetails'
import ClientProfile from '@/components/ClientProfile'
import IncomeTable from '@/components/IncomeTable'
import PlanStatus from '@/components/PlanStatus'
import { clientsApi, documentsApi, snapshotsApi, plansApi, reportsApi } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type {
  GateStatus,
  PlanSynthesizerOutput,
} from '@/types'

interface ClientPageProps {
  params: { client_id: string }
}

type StepStatus = 'pending' | 'active' | 'warning' | 'complete'

interface UploadedDoc {
  document_id: string
  filename: string
  document_type: string
  classification_status: string
  classification_confidence?: number
  institution?: string
  tax_year?: number
}

interface PlanData {
  plan_id: string
  status: string
  step_outputs: Record<string, unknown>
}

const STEP_LABELS = [
  'Upload Documents',
  'Review Extractions',
  'Income Projections',
  'Generate Plan',
  'Download Report',
]

export default function ClientWorkspacePage({ params }: ClientPageProps) {
  const { client_id } = params
  const { user } = useUser()

  const [currentStep, setCurrentStep] = useState(1)
  const [clientName, setClientName] = useState('')
  const [documents, setDocuments] = useState<UploadedDoc[]>([])
  const [gateStatus, setGateStatus] = useState<GateStatus | null>(null)
  const [existingProjections, setExistingProjections] = useState<Array<{ year: number; estimated_income: number; notes?: string }> | null>(null)
  const [plan, setPlan] = useState<PlanData | null>(null)
  const [uploading, setUploading] = useState(false)
  const [assembling, setAssembling] = useState(false)
  const [savingProjections, setSavingProjections] = useState(false)
  const [generatingPlan, setGeneratingPlan] = useState(false)
  const [generatingReport, setGeneratingReport] = useState(false)
  const [advisorName, setAdvisorName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Load initial data
  useEffect(() => {
    clientsApi.get(client_id).then((c) => setClientName(c.name)).catch(() => {})
    documentsApi.list(client_id).then(setDocuments).catch(() => {})
    snapshotsApi.getGateStatus(client_id).then(setGateStatus).catch(() => {})
    snapshotsApi.getIncomeProjections(client_id).then((data) => {
      if (data?.projections && data.projections.length > 0) setExistingProjections(data.projections)
    }).catch(() => {})
    plansApi.getLatest(client_id).then((p) => setPlan(p as PlanData)).catch(() => {})
  }, [client_id])

  useEffect(() => {
    if (user) setAdvisorName(user.fullName ?? '')
  }, [user])

  // Poll plan status when generating
  useEffect(() => {
    if (generatingPlan || plan?.status?.includes('running') || plan?.status?.includes('generating')) {
      pollRef.current = setInterval(async () => {
        try {
          const updated = await plansApi.getLatest(client_id)
          setPlan(updated as PlanData)
          if (updated.status === 'complete' || updated.status === 'failed') {
            setGeneratingPlan(false)
            if (pollRef.current) clearInterval(pollRef.current)
          }
        } catch {
          // ignore
        }
      }, 3000)
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [client_id, generatingPlan, plan?.status])

  const refreshGate = useCallback(() => {
    snapshotsApi.getGateStatus(client_id).then(setGateStatus).catch(() => {})
  }, [client_id])

  // Derive step statuses from gate
  function getStepStatus(step: number): StepStatus {
    const gate = gateStatus
    if (step === 1) {
      return documents.length > 0 && documents.some((d) => d.classification_status !== 'rejected')
        ? 'complete' : 'active'
    }
    if (step === 2) {
      if (!gate) return 'pending'
      if (gate.extraction_gate === 'passed' && gate.validation_gate === 'passed') return 'complete'
      if (gate.flagged_fields.length > 0 || gate.contradictions.some((c) => !c.resolved)) return 'warning'
      return 'active'
    }
    if (step === 3) {
      if (!gate) return 'pending'
      return gate.income_table_gate === 'passed' ? 'complete' : 'active'
    }
    if (step === 4) {
      if (!plan) return 'pending'
      return plan.status === 'complete' ? 'complete' : 'active'
    }
    if (step === 5) {
      return plan?.status === 'complete' ? 'active' : 'pending'
    }
    return 'pending'
  }

  function stepIcon(step: number): string {
    const s = getStepStatus(step)
    if (s === 'complete') return '✓'
    if (s === 'warning') return '⚠'
    if (s === 'active' && (step === 4) && generatingPlan) return '⟳'
    return '○'
  }

  function stepColor(step: number): string {
    const s = getStepStatus(step)
    if (s === 'complete') return 'text-green-600'
    if (s === 'warning') return 'text-amber-500'
    if (s === 'active') return 'text-[#1F4E79]'
    return 'text-gray-400'
  }

  // Upload handler
  async function handleFileUpload(file: File) {
    setUploading(true)
    setError(null)
    try {
      await documentsApi.upload(client_id, file)
      const updated = await documentsApi.list(client_id)
      setDocuments(updated)
      refreshGate()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  async function handleConfirmField(fieldName: string, value: unknown) {
    try {
      await snapshotsApi.confirmField(client_id, fieldName, value, value)
      refreshGate()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to confirm field')
    }
  }

  async function handleResolveContradiction(id: string, resolution: string, value?: unknown) {
    try {
      await snapshotsApi.resolveContradiction(client_id, id, resolution, value)
      refreshGate()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to resolve contradiction')
    }
  }

  async function handleMoveToStep3() {
    setAssembling(true)
    setError(null)
    try {
      await snapshotsApi.assemble(client_id)
      refreshGate()
      setCurrentStep(3)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to assemble snapshot')
    } finally {
      setAssembling(false)
    }
  }

  async function handleSaveProjections(
    rows: Array<{ year: number; estimated_income: number; notes?: string }>,
    ssStartAge?: number,
    ssBenefit?: number
  ) {
    setSavingProjections(true)
    setError(null)
    try {
      await snapshotsApi.saveIncomeProjections(client_id, rows, ssStartAge, ssBenefit)
      setExistingProjections(rows)
      refreshGate()
      setCurrentStep(4)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to save projections')
    } finally {
      setSavingProjections(false)
    }
  }

  async function handleGeneratePlan() {
    setGeneratingPlan(true)
    setError(null)
    try {
      await plansApi.generate(client_id)
      // Don't call getLatest immediately — the background task may not have committed yet.
      // The polling useEffect will discover the plan once it's in the DB.
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to generate plan')
      setGeneratingPlan(false)
    }
  }

  async function handleGenerateReport() {
    if (!plan) return
    setGeneratingReport(true)
    setError(null)
    try {
      const report = await reportsApi.generate(plan.plan_id, advisorName || 'Advisor', clientName)
      window.open(reportsApi.downloadUrl(report.report_id), '_blank')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to generate report')
    } finally {
      setGeneratingReport(false)
    }
  }

  const step4 = plan?.step_outputs?.step_4 as PlanSynthesizerOutput | undefined

  return (
    <div className="min-h-screen bg-slate-50">
      <Header />
      <main className="flex max-w-6xl mx-auto px-4 py-6 gap-6">
        {/* Sidebar */}
        <aside className="w-52 flex-shrink-0">
          <div className="bg-white rounded-lg shadow-sm p-4 space-y-1">
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">
              {clientName || 'Client'}
            </p>
            {STEP_LABELS.map((label, i) => {
              const step = i + 1
              const isActive = currentStep === step
              return (
                <button
                  key={step}
                  onClick={() => setCurrentStep(step)}
                  className={`w-full text-left px-3 py-2 rounded text-sm flex items-center gap-2 transition-colors ${
                    isActive
                      ? 'bg-[#1F4E79] text-white'
                      : 'text-gray-700 hover:bg-slate-100'
                  }`}
                >
                  <span className={isActive ? 'text-white' : stepColor(step)}>
                    {stepIcon(step)}
                  </span>
                  {label}
                </button>
              )
            })}
          </div>
        </aside>

        {/* Content */}
        <div className="flex-1 space-y-4">
          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
              {error}
              <button className="ml-2 underline" onClick={() => setError(null)}>Dismiss</button>
            </div>
          )}

          {/* Step 1: Upload */}
          {currentStep === 1 && (
            <Card>
              <CardHeader>
                <CardTitle>Step 1 — Upload Documents</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <ClientProfile
                  clientId={client_id}
                  existingConfirmations={
                    (gateStatus?.advisor_confirmations as Record<string, { confirmed_value: unknown }>) ?? {}
                  }
                  onSaved={refreshGate}
                />
                <div
                  className="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center cursor-pointer hover:border-[#1F4E79] transition-colors"
                  onClick={() => fileInputRef.current?.click()}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => {
                    e.preventDefault()
                    const file = e.dataTransfer.files[0]
                    if (file && file.type === 'application/pdf') handleFileUpload(file)
                  }}
                >
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf"
                    className="hidden"
                    onChange={(e) => {
                      const file = e.target.files?.[0]
                      if (file) handleFileUpload(file)
                    }}
                  />
                  <p className="text-gray-500">
                    {uploading ? 'Uploading...' : 'Drag & drop a PDF here, or click to browse'}
                  </p>
                  <p className="text-xs text-gray-400 mt-1">
                    Accepts: Form 1040, Brokerage Statements, IRA/401k Statements
                  </p>
                </div>

                {documents.length > 0 && (
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-slate-100 text-gray-600">
                        <th className="px-3 py-2 text-left">Filename</th>
                        <th className="px-3 py-2 text-left">Type</th>
                        <th className="px-3 py-2 text-left">Year</th>
                        <th className="px-3 py-2 text-left">Status</th>
                        <th className="px-3 py-2"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {documents.map((doc, i) => (
                        <tr key={doc.document_id} className={i % 2 === 0 ? 'bg-white' : 'bg-slate-50'}>
                          <td className="px-3 py-2 text-gray-900">{doc.filename}</td>
                          <td className="px-3 py-2 text-gray-600">{doc.document_type ?? '—'}</td>
                          <td className="px-3 py-2 text-gray-600">{doc.tax_year ?? '—'}</td>
                          <td className="px-3 py-2">
                            {doc.classification_status === 'rejected' ? (
                              <Badge variant="destructive">Rejected</Badge>
                            ) : doc.classification_status === 'extracted' || doc.classification_status === 'gate_passed' ? (
                              <Badge className="bg-green-500 text-white">
                                ✓ {doc.document_type ?? 'Classified'}
                                {doc.classification_confidence
                                  ? ` ${Math.round(doc.classification_confidence * 100)}%`
                                  : ''}
                              </Badge>
                            ) : (
                              <Badge variant="secondary">{doc.classification_status}</Badge>
                            )}
                          </td>
                          <td className="px-3 py-2">
                            <button
                              className="text-gray-400 hover:text-red-500 text-xs"
                              title="Delete document"
                              onClick={async () => {
                                if (!confirm(`Delete "${doc.filename}"?`)) return
                                await documentsApi.delete(doc.document_id)
                                setDocuments((prev) => prev.filter((d) => d.document_id !== doc.document_id))
                              }}
                            >
                              ✕
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}

                <Button
                  disabled={!documents.some((d) => d.classification_status !== 'rejected')}
                  onClick={() => setCurrentStep(2)}
                  className="bg-[#1F4E79] hover:bg-[#1a4068] text-white"
                >
                  Next: Review Extractions →
                </Button>
              </CardContent>
            </Card>
          )}

          {/* Step 2: Review */}
          {currentStep === 2 && gateStatus && (
            <Card>
              <CardHeader>
                <CardTitle>Step 2 — Review Extractions</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <GateReview
                  flaggedFields={gateStatus.flagged_fields}
                  contradictions={gateStatus.contradictions}
                  onConfirmField={handleConfirmField}
                  onResolveContradiction={handleResolveContradiction}
                />

                <ExtractionDetails documents={documents} />

                <Button
                  disabled={
                    assembling ||
                    gateStatus.flagged_fields.some((f) => f.field_classification === 'hard_required') ||
                    gateStatus.contradictions.some((c) => !c.resolved)
                  }
                  onClick={handleMoveToStep3}
                  className="bg-[#1F4E79] hover:bg-[#1a4068] text-white"
                >
                  {assembling ? 'Assembling snapshot...' : 'Next: Income Projections →'}
                </Button>
              </CardContent>
            </Card>
          )}

          {/* Step 3: Income Projections */}
          {currentStep === 3 && (
            <Card>
              <CardHeader>
                <CardTitle>Step 3 — Income Projections</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-gray-600 mb-4">
                  Enter at least 3 years of estimated income. Include years of low income (sabbaticals,
                  early retirement) — these are the ideal Roth conversion windows.
                </p>
                <IncomeTable
                  key={existingProjections ? 'loaded' : 'empty'}
                  initialRows={existingProjections ?? undefined}
                  onSave={handleSaveProjections}
                  loading={savingProjections}
                />
              </CardContent>
            </Card>
          )}

          {/* Step 4: Generate Plan */}
          {currentStep === 4 && (
            <Card>
              <CardHeader>
                <CardTitle>Step 4 — Generate Tax Optimization Plan</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {!plan && !generatingPlan && (
                  <div className="space-y-4">
                    <div className="bg-green-50 border border-green-200 rounded-lg p-4 space-y-1 text-sm">
                      <p className="font-medium text-green-800">✓ All information verified</p>
                      <p className="text-green-700">Documents: {documents.length} uploaded and reviewed</p>
                      <p className="text-green-700">Client: {clientName}</p>
                    </div>
                    <Button
                      onClick={handleGeneratePlan}
                      className="bg-[#1F4E79] hover:bg-[#1a4068] text-base px-8 py-3"
                    >
                      Generate Tax Optimization Plan
                    </Button>
                  </div>
                )}

                {(generatingPlan || (plan && plan.status !== 'complete' && plan.status !== 'failed')) && (
                  <div className="space-y-4">
                    <p className="text-gray-700 font-medium">Analyzing your client&apos;s tax situation...</p>
                    <PlanStatus
                      planStatus={plan?.status ?? 'generating'}
                      stepOutputs={plan?.step_outputs ?? {}}
                    />
                  </div>
                )}

                {plan?.status === 'complete' && step4 && (
                  <div className="space-y-4">
                    <div className="bg-green-50 border border-green-200 rounded-lg p-4">
                      <p className="font-medium text-green-800 mb-2">✓ Plan complete</p>
                      <p className="text-sm text-gray-700">{step4.executive_summary}</p>
                    </div>

                    <div>
                      <h3 className="font-semibold text-gray-900 mb-2">Priority Actions</h3>
                      <div className="space-y-2">
                        {step4.priority_actions.slice(0, 3).map((action) => (
                          <div key={action.priority} className="bg-white border rounded-lg p-3 text-sm">
                            <div className="flex items-start gap-3">
                              <span className="bg-[#1F4E79] text-white rounded-full w-6 h-6 flex items-center justify-center text-xs flex-shrink-0">
                                {action.priority}
                              </span>
                              <div>
                                <p className="font-medium text-gray-900">{action.action}</p>
                                <p className="text-gray-600 mt-0.5">{action.rationale}</p>
                                <p className="text-green-700 mt-0.5 font-medium">{action.estimated_benefit}</p>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>

                    <div className="flex gap-3">
                      <Button onClick={() => setCurrentStep(5)} className="bg-[#1F4E79] hover:bg-[#1a4068] text-white">
                        Next: Download Report →
                      </Button>
                      <Button
                        variant="outline"
                        onClick={() => { setPlan(null); handleGeneratePlan() }}
                      >
                        Regenerate Plan
                      </Button>
                    </div>
                  </div>
                )}

                {plan?.status === 'failed' && (
                  <div className="bg-red-50 border border-red-200 rounded-lg p-4">
                    <p className="text-red-700">Plan generation failed. Please try again.</p>
                    <Button
                      variant="outline"
                      className="mt-3"
                      onClick={handleGeneratePlan}
                    >
                      Retry
                    </Button>
                  </div>
                )}
              </CardContent>
            </Card>
          )}

          {/* Step 5: Download Report */}
          {currentStep === 5 && plan?.status === 'complete' && step4 && (
            <Card>
              <CardHeader>
                <CardTitle>Step 5 — Download Report</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="text-sm font-medium text-gray-700 block mb-1">Advisor Name</label>
                    <Input
                      value={advisorName}
                      onChange={(e) => setAdvisorName(e.target.value)}
                      placeholder="Your name"
                    />
                  </div>
                  <div>
                    <label className="text-sm font-medium text-gray-700 block mb-1">Client Name</label>
                    <Input value={clientName} disabled />
                  </div>
                </div>

                <div className="bg-gray-50 border rounded-lg p-4 space-y-2">
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Preview</p>
                  <p className="text-sm font-medium text-gray-900">Executive Summary</p>
                  <p className="text-sm text-gray-700">{step4.executive_summary}</p>
                  {step4.priority_actions[0] && (
                    <>
                      <p className="text-sm font-medium text-gray-900 pt-2">Top Recommendation</p>
                      <p className="text-sm text-gray-700">#{step4.priority_actions[0].priority}: {step4.priority_actions[0].action}</p>
                    </>
                  )}
                </div>

                <Button
                  onClick={handleGenerateReport}
                  disabled={generatingReport || !advisorName.trim()}
                  className="bg-[#1F4E79] hover:bg-[#1a4068] text-base px-6"
                >
                  {generatingReport ? 'Generating PDF...' : 'Generate & Download PDF Report'}
                </Button>
              </CardContent>
            </Card>
          )}

          {currentStep === 5 && plan?.status !== 'complete' && (
            <Card>
              <CardContent className="py-8 text-center text-gray-500">
                <p>Complete the plan generation step first.</p>
                <Button variant="outline" className="mt-4" onClick={() => setCurrentStep(4)}>
                  Go to Generate Plan
                </Button>
              </CardContent>
            </Card>
          )}
        </div>
      </main>
    </div>
  )
}
