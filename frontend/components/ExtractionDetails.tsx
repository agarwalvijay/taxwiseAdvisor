'use client'
import { useEffect, useState } from 'react'
import { documentsApi } from '@/lib/api'
import { Badge } from '@/components/ui/badge'

// ── Types ──────────────────────────────────────────────────────────────────

interface FieldData {
  value: unknown
  confidence: number
  inferred?: boolean
  note?: string
}

interface RawExtraction {
  document_type: string
  institution?: string
  fields: Record<string, FieldData>
  extraction_notes?: string[]
  overall_confidence?: number
}

interface ExtractionResponse {
  document_id: string
  filename: string
  document_type: string
  raw_extraction?: RawExtraction
}

interface Doc {
  document_id: string
  filename: string
  document_type: string
  classification_status: string
}

interface Props {
  documents: Doc[]
}

// ── Helpers ────────────────────────────────────────────────────────────────

const FIELD_LABELS: Record<string, string> = {
  is_consolidated: 'Consolidated Statement',
  account_type: 'Account Type',
  institution: 'Institution',
  account_value: 'Account Value',
  total_roth_balance: 'Total Roth Balance',
  total_pretax_retirement_balance: 'Total Pre-Tax Retirement Balance',
  total_hsa_balance: 'Total HSA Balance',
  sub_accounts: 'Sub-Accounts',
  employer_name: 'Employer Name',
  statement_date: 'Statement Date',
  roth_sub_account_balance: 'Roth Sub-Account Balance',
  ytd_employee_contributions: 'YTD Employee Contributions',
  non_deductible_basis: 'Non-Deductible Basis',
  holdings_summary: 'Holdings Summary',
  filing_status: 'Filing Status',
  agi: 'Adjusted Gross Income',
  taxable_income: 'Taxable Income',
  state_of_residence: 'State of Residence',
  tax_year: 'Tax Year',
  total_account_value: 'Total Account Value',
  cash_balance: 'Cash Balance',
  holdings: 'Holdings',
  cost_basis_total: 'Total Cost Basis',
}

function label(key: string) {
  return FIELD_LABELS[key] ?? key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  if (value >= 0.90) return <Badge className="bg-green-100 text-green-800 border-green-200">{pct}%</Badge>
  if (value >= 0.70) return <Badge className="bg-yellow-100 text-yellow-800 border-yellow-200">{pct}%</Badge>
  return <Badge className="bg-red-100 text-red-800 border-red-200">{pct}%</Badge>
}

function formatValue(key: string, val: unknown): string {
  if (val === null || val === undefined) return '—'
  if (typeof val === 'boolean') return val ? 'Yes' : 'No'
  if (typeof val === 'number') {
    // Dollar amounts
    if (
      key.includes('balance') || key.includes('value') || key.includes('agi') ||
      key.includes('income') || key.includes('tax') || key.includes('basis') ||
      key.includes('contribution') || key.includes('rmd') || key.includes('benefit')
    ) {
      return '$' + val.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })
    }
    return String(val)
  }
  if (typeof val === 'string') return val
  if (Array.isArray(val)) return `${val.length} item(s)` // handled separately
  if (typeof val === 'object') return JSON.stringify(val)
  return String(val)
}

// ── Sub-account table ──────────────────────────────────────────────────────

function SubAccountTable({ accounts }: { accounts: unknown[] }) {
  if (!accounts.length) return <p className="text-xs text-gray-400 italic">Empty</p>
  const rows = accounts as Record<string, unknown>[]
  return (
    <table className="w-full text-xs border-collapse mt-1">
      <thead>
        <tr className="bg-[#1F4E79] text-white">
          <th className="px-2 py-1 text-left">Account Name</th>
          <th className="px-2 py-1 text-left">Account #</th>
          <th className="px-2 py-1 text-left">Type</th>
          <th className="px-2 py-1 text-right">Ending Value</th>
          <th className="px-2 py-1 text-center">Conf.</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className={i % 2 === 0 ? 'bg-white' : 'bg-slate-50'}>
            <td className="px-2 py-1">{String(r.account_name ?? '—')}</td>
            <td className="px-2 py-1 font-mono">{String(r.account_number ?? '—')}</td>
            <td className="px-2 py-1">{String(r.account_type ?? '—').replace(/_/g, ' ')}</td>
            <td className="px-2 py-1 text-right">
              {typeof r.ending_value === 'number'
                ? '$' + r.ending_value.toLocaleString('en-US', { minimumFractionDigits: 0 })
                : '—'}
            </td>
            <td className="px-2 py-1 text-center">
              {typeof r.confidence === 'number' ? `${Math.round(r.confidence * 100)}%` : '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ── Holdings table ─────────────────────────────────────────────────────────

function HoldingsTable({ holdings }: { holdings: unknown[] }) {
  if (!holdings.length) return <p className="text-xs text-gray-400 italic">No holdings</p>
  const rows = holdings as Record<string, unknown>[]
  return (
    <table className="w-full text-xs border-collapse mt-1">
      <thead>
        <tr className="bg-[#1F4E79] text-white">
          <th className="px-2 py-1 text-left">Symbol</th>
          <th className="px-2 py-1 text-left">Description</th>
          <th className="px-2 py-1 text-right">Qty</th>
          <th className="px-2 py-1 text-right">Market Value</th>
          <th className="px-2 py-1 text-right">Unrealized G/L</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((h, i) => (
          <tr key={i} className={i % 2 === 0 ? 'bg-white' : 'bg-slate-50'}>
            <td className="px-2 py-1 font-mono font-bold">{String(h.symbol ?? '—')}</td>
            <td className="px-2 py-1">{String(h.description ?? '—')}</td>
            <td className="px-2 py-1 text-right">{h.quantity != null ? String(h.quantity) : '—'}</td>
            <td className="px-2 py-1 text-right">
              {typeof h.market_value === 'number' ? '$' + h.market_value.toLocaleString('en-US') : '—'}
            </td>
            <td className="px-2 py-1 text-right">
              {typeof h.unrealized_gain_loss === 'number'
                ? (h.unrealized_gain_loss >= 0 ? '+' : '') + '$' + h.unrealized_gain_loss.toLocaleString('en-US')
                : '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ── Single document panel ──────────────────────────────────────────────────

function DocExtractionPanel({ doc }: { doc: Doc }) {
  const [open, setOpen] = useState(false)
  const [data, setData] = useState<ExtractionResponse | null>(null)
  const [loading, setLoading] = useState(false)

  async function load() {
    if (data || loading) return
    setLoading(true)
    try {
      const result = await documentsApi.getExtraction(doc.document_id)
      setData(result)
    } finally {
      setLoading(false)
    }
  }

  function toggle() {
    if (!open) load()
    setOpen(o => !o)
  }

  const fields = data?.raw_extraction?.fields ?? {}
  const notes = data?.raw_extraction?.extraction_notes ?? []
  const confidence = data?.raw_extraction?.overall_confidence

  // Separate scalar fields from array fields, skip nulls
  const scalarFields = Object.entries(fields).filter(
    ([k, v]) => v.value !== null && v.value !== undefined && !Array.isArray(v.value) && k !== 'institution'
  )
  const arrayFields = Object.entries(fields).filter(
    ([, v]) => Array.isArray(v.value) && (v.value as unknown[]).length > 0
  )

  return (
    <div className="border rounded-lg overflow-hidden">
      <button
        onClick={toggle}
        className="w-full flex items-center justify-between px-4 py-3 bg-slate-50 hover:bg-slate-100 text-left"
      >
        <div className="flex items-center gap-3">
          <span className="font-medium text-sm text-gray-900">{doc.filename}</span>
          <span className="text-xs text-gray-500 bg-gray-200 px-2 py-0.5 rounded">
            {doc.document_type?.replace(/_/g, ' ')}
          </span>
          {confidence != null && (
            <ConfidenceBadge value={confidence} />
          )}
        </div>
        <span className="text-gray-400 text-sm">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="px-4 py-3 space-y-4">
          {loading && <p className="text-sm text-gray-400">Loading extraction...</p>}

          {!loading && data && (
            <>
              {/* Scalar fields table */}
              {scalarFields.length > 0 && (
                <table className="w-full text-sm border-collapse">
                  <thead>
                    <tr className="bg-[#1F4E79] text-white">
                      <th className="px-3 py-2 text-left w-1/3">Field</th>
                      <th className="px-3 py-2 text-left">Extracted Value</th>
                      <th className="px-3 py-2 text-center w-20">Confidence</th>
                      <th className="px-3 py-2 text-left">Note</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scalarFields.map(([k, v], i) => (
                      <tr key={k} className={i % 2 === 0 ? 'bg-white' : 'bg-slate-50'}>
                        <td className="px-3 py-2 font-medium text-gray-700">{label(k)}</td>
                        <td className="px-3 py-2 font-mono text-gray-900">
                          {formatValue(k, v.value)}
                          {v.inferred && (
                            <span className="ml-2 text-xs text-blue-500 italic">inferred</span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-center">
                          <ConfidenceBadge value={v.confidence} />
                        </td>
                        <td className="px-3 py-2 text-xs text-gray-500">{v.note ?? ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              {/* Array fields */}
              {arrayFields.map(([k, v]) => (
                <div key={k}>
                  <p className="text-sm font-semibold text-gray-700 mb-1">{label(k)}</p>
                  {k === 'sub_accounts'
                    ? <SubAccountTable accounts={v.value as unknown[]} />
                    : (k === 'holdings' || k === 'holdings_summary')
                    ? <HoldingsTable holdings={v.value as unknown[]} />
                    : (
                      <pre className="text-xs bg-gray-50 p-2 rounded overflow-auto">
                        {JSON.stringify(v.value, null, 2)}
                      </pre>
                    )
                  }
                </div>
              ))}

              {/* Extraction notes */}
              {notes.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
                    Extraction Notes
                  </p>
                  <ul className="space-y-1">
                    {notes.map((n, i) => (
                      <li key={i} className="text-xs text-gray-600 bg-blue-50 border border-blue-100 rounded px-3 py-1">
                        {n}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {scalarFields.length === 0 && arrayFields.length === 0 && (
                <p className="text-sm text-gray-400 italic">No fields extracted from this document.</p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────

export default function ExtractionDetails({ documents }: Props) {
  const classified = documents.filter(d => d.classification_status !== 'rejected' && d.document_type)

  if (!classified.length) return null

  return (
    <div className="space-y-3">
      <p className="text-sm font-medium text-gray-700">Extracted Data by Document</p>
      <p className="text-xs text-gray-500">
        Click a document to review what was extracted. Confidence scores below 90% are highlighted.
      </p>
      {classified.map(doc => (
        <DocExtractionPanel key={doc.document_id} doc={doc} />
      ))}
    </div>
  )
}
