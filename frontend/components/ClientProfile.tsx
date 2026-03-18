'use client'
import { useState, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { snapshotsApi } from '@/lib/api'

const FILING_STATUS_OPTIONS = [
  { value: 'single', label: 'Single' },
  { value: 'married_filing_jointly', label: 'Married Filing Jointly' },
  { value: 'married_filing_separately', label: 'Married Filing Separately' },
  { value: 'head_of_household', label: 'Head of Household' },
  { value: 'qualifying_widow', label: 'Qualifying Widow(er)' },
]

interface ClientProfileProps {
  clientId: string
  existingConfirmations: Record<string, { confirmed_value: unknown }> // advisor_confirmations
  onSaved: () => void
}

export default function ClientProfile({ clientId, existingConfirmations, onSaved }: ClientProfileProps) {
  const get = (field: string) => {
    const c = existingConfirmations[field]
    return c != null ? String(c.confirmed_value) : ''
  }

  const [age, setAge] = useState(get('personal.age'))
  const [retirementAge, setRetirementAge] = useState(get('personal.retirement_target_age'))
  const [filingStatus, setFilingStatus] = useState(get('personal.filing_status'))
  const [state, setState] = useState(get('personal.state'))
  const [agi, setAgi] = useState(get('income.current_year_agi'))
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Sync form fields when confirmations load from the server (async on mount)
  useEffect(() => {
    const c = existingConfirmations
    if (Object.keys(c).length === 0) return
    if (c['personal.age']) setAge(String(c['personal.age'].confirmed_value))
    if (c['personal.retirement_target_age']) setRetirementAge(String(c['personal.retirement_target_age'].confirmed_value))
    if (c['personal.filing_status']) setFilingStatus(String(c['personal.filing_status'].confirmed_value))
    if (c['personal.state']) setState(String(c['personal.state'].confirmed_value))
    if (c['income.current_year_agi']) setAgi(String(c['income.current_year_agi'].confirmed_value))
    const allPresent = ['personal.age', 'personal.retirement_target_age', 'personal.filing_status', 'personal.state', 'income.current_year_agi']
      .every((f) => f in c)
    if (allPresent) setSaved(true)
  }, [existingConfirmations])

  const allFilled = age.trim() && retirementAge.trim() && filingStatus && state.trim() && agi.trim()

  async function handleSave() {
    setSaving(true)
    setError(null)
    try {
      // Use dot-notation paths that match the snapshot assembler's _get() lookups
      const entries: Array<[string, unknown]> = [
        ['personal.age', Number(age)],
        ['personal.retirement_target_age', Number(retirementAge)],
        ['personal.filing_status', filingStatus],
        ['personal.state', state.trim().toUpperCase().slice(0, 2)],
        ['income.current_year_agi', Number(agi)],
      ]
      for (const [field, value] of entries) {
        await snapshotsApi.confirmField(clientId, field, value, null)
      }
      setSaved(true)
      onSaved()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to save profile')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 space-y-4">
      <div>
        <p className="font-medium text-blue-900 text-sm">Client Profile</p>
        <p className="text-xs text-blue-700 mt-0.5">
          These fields are required but not extractable from financial documents — enter them once here.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="text-xs font-medium text-gray-700 block mb-1">Client Age</label>
          <Input
            type="number"
            min={18}
            max={100}
            placeholder="e.g. 52"
            value={age}
            onChange={(e) => { setAge(e.target.value); setSaved(false) }}
          />
        </div>
        <div>
          <label className="text-xs font-medium text-gray-700 block mb-1">Retirement Target Age</label>
          <Input
            type="number"
            min={50}
            max={80}
            placeholder="e.g. 65"
            value={retirementAge}
            onChange={(e) => { setRetirementAge(e.target.value); setSaved(false) }}
          />
        </div>
        <div>
          <label className="text-xs font-medium text-gray-700 block mb-1">Filing Status</label>
          <select
            className="w-full h-9 rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm"
            value={filingStatus}
            onChange={(e) => { setFilingStatus(e.target.value); setSaved(false) }}
          >
            <option value="">Select...</option>
            {FILING_STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs font-medium text-gray-700 block mb-1">State of Residence (2-letter)</label>
          <Input
            maxLength={2}
            placeholder="e.g. CA"
            value={state}
            onChange={(e) => { setState(e.target.value); setSaved(false) }}
          />
        </div>
        <div className="col-span-2">
          <label className="text-xs font-medium text-gray-700 block mb-1">
            Adjusted Gross Income — AGI
            <span className="text-gray-400 font-normal ml-1">(from last tax return, or best estimate)</span>
          </label>
          <Input
            type="number"
            placeholder="e.g. 185000"
            value={agi}
            onChange={(e) => { setAgi(e.target.value); setSaved(false) }}
          />
        </div>
      </div>

      {error && <p className="text-xs text-red-600">{error}</p>}

      <div className="flex items-center gap-3">
        <Button
          size="sm"
          disabled={!allFilled || saving}
          onClick={handleSave}
          className="bg-[#1F4E79] hover:bg-[#1a4068] text-white"
        >
          {saving ? 'Saving...' : 'Save Profile'}
        </Button>
        {saved && <span className="text-xs text-green-700 font-medium">✓ Profile saved</span>}
      </div>
    </div>
  )
}
