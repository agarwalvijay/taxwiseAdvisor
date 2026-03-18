'use client'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

interface IncomeRow {
  year: number
  estimated_income: number
  notes: string
}

interface IncomeTableProps {
  initialRows?: IncomeRow[]
  onSave: (rows: IncomeRow[], ssStartAge?: number, ssBenefit?: number) => void
  loading?: boolean
}

const currentYear = new Date().getFullYear()

export default function IncomeTable({ initialRows, onSave, loading }: IncomeTableProps) {
  const [rows, setRows] = useState<IncomeRow[]>(
    initialRows ?? [
      { year: currentYear, estimated_income: 0, notes: '' },
      { year: currentYear + 1, estimated_income: 0, notes: '' },
      { year: currentYear + 2, estimated_income: 0, notes: '' },
    ]
  )
  const [ssStartAge, setSsStartAge] = useState<string>('')
  const [ssBenefit, setSsBenefit] = useState<string>('')
  const [error, setError] = useState<string | null>(null)

  function addRow() {
    const lastYear = rows.length > 0 ? rows[rows.length - 1].year : currentYear - 1
    setRows([...rows, { year: lastYear + 1, estimated_income: 0, notes: '' }])
  }

  function updateRow(index: number, field: keyof IncomeRow, value: string) {
    setRows((prev) => {
      const next = [...prev]
      if (field === 'year') next[index] = { ...next[index], year: parseInt(value) || currentYear }
      else if (field === 'estimated_income') next[index] = { ...next[index], estimated_income: parseFloat(value) || 0 }
      else next[index] = { ...next[index], notes: value }
      return next
    })
  }

  function removeRow(index: number) {
    setRows((prev) => prev.filter((_, i) => i !== index))
  }

  function handleSave() {
    if (rows.length < 3) {
      setError('At least 3 years of income projections are required.')
      return
    }
    setError(null)
    const ss = ssStartAge ? parseInt(ssStartAge) : undefined
    const benefit = ssBenefit ? parseFloat(ssBenefit) : undefined
    onSave(rows, ss, benefit)
  }

  return (
    <div className="space-y-4">
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="bg-[#1F4E79] text-white">
              <th className="px-3 py-2 text-left">Year</th>
              <th className="px-3 py-2 text-left">Estimated Income ($)</th>
              <th className="px-3 py-2 text-left">Notes (optional)</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className={i % 2 === 0 ? 'bg-white' : 'bg-slate-50'}>
                <td className="px-2 py-1">
                  <Input
                    type="number"
                    className="w-24 h-8"
                    value={row.year}
                    onChange={(e) => updateRow(i, 'year', e.target.value)}
                  />
                </td>
                <td className="px-2 py-1">
                  <Input
                    type="number"
                    className="w-40 h-8"
                    value={row.estimated_income}
                    onChange={(e) => updateRow(i, 'estimated_income', e.target.value)}
                  />
                </td>
                <td className="px-2 py-1">
                  <Input
                    className="w-48 h-8"
                    value={row.notes}
                    onChange={(e) => updateRow(i, 'notes', e.target.value)}
                  />
                </td>
                <td className="px-2 py-1">
                  <button
                    onClick={() => removeRow(i)}
                    className="text-gray-400 hover:text-red-500 text-xs"
                    title="Remove row"
                  >
                    ✕
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Button variant="outline" size="sm" onClick={addRow}>+ Add Year</Button>

      {error && (
        <p className="text-red-600 text-sm font-medium bg-red-50 border border-red-200 rounded px-3 py-2">
          {error}
        </p>
      )}

      <div className="border-t pt-4 space-y-3">
        <p className="text-sm font-medium text-gray-700">Social Security (optional)</p>
        <div className="flex gap-4 flex-wrap">
          <div>
            <label className="text-xs text-gray-500 block mb-1">SS Start Age (62–70)</label>
            <Input
              type="number"
              className="w-28 h-8"
              placeholder="70"
              min={62}
              max={70}
              value={ssStartAge}
              onChange={(e) => setSsStartAge(e.target.value)}
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">Monthly Benefit ($)</label>
            <Input
              type="number"
              className="w-36 h-8"
              placeholder="3520"
              value={ssBenefit}
              onChange={(e) => setSsBenefit(e.target.value)}
            />
          </div>
        </div>
      </div>

      <Button onClick={handleSave} disabled={loading} className="bg-[#1F4E79] hover:bg-[#1a4068] text-white">
        {loading ? 'Saving...' : 'Save & Continue'}
      </Button>
    </div>
  )
}
