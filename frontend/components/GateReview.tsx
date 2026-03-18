'use client'
import { useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'

interface FlaggedFieldItem {
  field_name: string
  extracted_value: unknown
  confidence?: number
  reason: string
  field_classification: string
}

interface ContradictionItem {
  contradiction_id: string
  description: string
  field_a?: string
  value_a?: unknown
  field_b?: string
  value_b?: unknown
  suggested_resolution: string
  resolved: boolean
}

interface GateReviewProps {
  flaggedFields: FlaggedFieldItem[]
  contradictions: ContradictionItem[]
  onConfirmField: (fieldName: string, value: unknown) => void
  onResolveContradiction: (id: string, resolution: string, value?: unknown) => void
}

function toDisplayLabel(fieldName: string): string {
  return fieldName
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

export default function GateReview({
  flaggedFields,
  contradictions,
  onConfirmField,
  onResolveContradiction,
}: GateReviewProps) {
  const [overrideValues, setOverrideValues] = useState<Record<string, string>>({})
  const [contradictionResolutions, setContradictionResolutions] = useState<Record<string, string>>({})
  const [customValues, setCustomValues] = useState<Record<string, string>>({})

  const unresolvedCount =
    flaggedFields.filter((f) => f.field_classification === 'hard_required').length +
    contradictions.filter((c) => !c.resolved).length

  return (
    <div className="space-y-4">
      {unresolvedCount > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
          <p className="text-amber-800 font-medium">
            {unresolvedCount} item{unresolvedCount !== 1 ? 's' : ''} require your review before the plan can be generated
          </p>
        </div>
      )}

      {flaggedFields.map((field) => (
        <Card key={field.field_name} className="border-amber-200">
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              <span className="text-amber-500">⚠</span>
              {toDisplayLabel(field.field_name)}
              {field.field_classification === 'hard_required' && (
                <Badge variant="destructive" className="text-xs">Required</Badge>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {field.extracted_value !== null && field.extracted_value !== undefined ? (
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <span className="text-gray-500">Extracted value: </span>
                  <span className="font-medium">{String(field.extracted_value)}</span>
                </div>
                {field.confidence !== undefined && (
                  <div>
                    <span className="text-gray-500">Confidence: </span>
                    <span className={`font-medium ${field.confidence < 0.75 ? 'text-red-600' : 'text-amber-600'}`}>
                      {Math.round(field.confidence * 100)}%
                    </span>
                  </div>
                )}
              </div>
            ) : (
              <p className="text-xs text-gray-500 italic">Not found in uploaded documents — upload a Form 1040 or enter below.</p>
            )}
            <p className="text-sm text-gray-600 bg-gray-50 rounded p-2">{field.reason}</p>
            <div className="flex items-center gap-3 flex-wrap">
              <Input
                data-testid={`override-input-${field.field_name}`}
                placeholder="Enter correct value"
                className="w-48"
                value={overrideValues[field.field_name] ?? ''}
                onChange={(e) =>
                  setOverrideValues((prev) => ({ ...prev, [field.field_name]: e.target.value }))
                }
              />
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  const val = overrideValues[field.field_name]
                  if (val) onConfirmField(field.field_name, val)
                }}
              >
                Submit Correction
              </Button>
              {field.extracted_value !== null && field.extracted_value !== undefined && (
                <Button
                  size="sm"
                  data-testid={`confirm-btn-${field.field_name}`}
                  onClick={() => onConfirmField(field.field_name, field.extracted_value)}
                >
                  Confirm {String(field.extracted_value)}
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
      ))}

      {contradictions.filter((c) => !c.resolved).map((contradiction) => (
        <Card key={contradiction.contradiction_id} className="border-red-200">
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              <span className="text-red-500">⚡</span>
              Contradiction: {contradiction.description.slice(0, 60)}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-gray-700">{contradiction.description}</p>
            {contradiction.suggested_resolution && (
              <p className="text-sm text-blue-700 bg-blue-50 rounded p-2">
                Suggestion: {contradiction.suggested_resolution}
              </p>
            )}
            <div className="space-y-2">
              <p className="text-sm font-medium text-gray-700">Which value is correct?</p>
              {contradiction.value_a !== undefined && (
                <label className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="radio"
                    name={`contradiction-${contradiction.contradiction_id}`}
                    value="use_a"
                    onChange={() =>
                      setContradictionResolutions((prev) => ({
                        ...prev,
                        [contradiction.contradiction_id]: 'use_a',
                      }))
                    }
                  />
                  Use {contradiction.field_a ?? 'Value A'}: {String(contradiction.value_a)}
                </label>
              )}
              {contradiction.value_b !== undefined && (
                <label className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="radio"
                    name={`contradiction-${contradiction.contradiction_id}`}
                    value="use_b"
                    onChange={() =>
                      setContradictionResolutions((prev) => ({
                        ...prev,
                        [contradiction.contradiction_id]: 'use_b',
                      }))
                    }
                  />
                  Use {contradiction.field_b ?? 'Value B'}: {String(contradiction.value_b)}
                </label>
              )}
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="radio"
                  name={`contradiction-${contradiction.contradiction_id}`}
                  value="custom"
                  onChange={() =>
                    setContradictionResolutions((prev) => ({
                      ...prev,
                      [contradiction.contradiction_id]: 'custom',
                    }))
                  }
                />
                Enter different value:
                <Input
                  placeholder="Custom value"
                  className="w-40 h-7 text-sm"
                  value={customValues[contradiction.contradiction_id] ?? ''}
                  onChange={(e) =>
                    setCustomValues((prev) => ({
                      ...prev,
                      [contradiction.contradiction_id]: e.target.value,
                    }))
                  }
                />
              </label>
            </div>
            <Button
              size="sm"
              onClick={() => {
                const resolution = contradictionResolutions[contradiction.contradiction_id] ?? 'acknowledged'
                const customVal = customValues[contradiction.contradiction_id]
                onResolveContradiction(contradiction.contradiction_id, resolution, customVal ?? undefined)
              }}
            >
              Resolve
            </Button>
          </CardContent>
        </Card>
      ))}

      {flaggedFields.length === 0 && contradictions.filter((c) => !c.resolved).length === 0 && (
        <div className="bg-green-50 border border-green-200 rounded-lg p-4">
          <p className="text-green-800 font-medium">✓ All items reviewed — ready to continue</p>
        </div>
      )}
    </div>
  )
}
