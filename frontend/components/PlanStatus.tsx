'use client'

interface PlanStatusProps {
  planStatus: string
  stepOutputs: Record<string, unknown>
}

const STEPS = [
  { key: 'step_1', label: 'Tax trajectory analyzed' },
  { key: 'step_2', label: 'Optimizing Roth conversions' },
  { key: 'step_3', label: 'Tax-loss harvesting analysis' },
  { key: 'step_4', label: 'Synthesizing recommendations' },
]

const RUNNING_STEP_MAP: Record<string, string> = {
  step_1_running: 'step_1',
  step_2_running: 'step_2',
  step_3_running: 'step_3',
  step_4_running: 'step_4',
}

export default function PlanStatus({ planStatus, stepOutputs }: PlanStatusProps) {
  const isComplete = planStatus === 'complete'
  const isFailed = planStatus === 'failed'
  const runningStep = RUNNING_STEP_MAP[planStatus]

  return (
    <div className="space-y-3">
      {STEPS.map((step) => {
        const isDone = step.key in stepOutputs
        const isRunning = runningStep === step.key
        const label = isRunning ? `${step.label}...` : step.label

        let indicator: string
        let textClass: string
        if (isDone) {
          indicator = '✓'
          textClass = 'text-green-700'
        } else if (isRunning) {
          indicator = '⟳'
          textClass = 'text-blue-600'
        } else {
          indicator = '○'
          textClass = 'text-gray-400'
        }

        return (
          <div
            key={step.key}
            data-testid={`step-indicator-${step.key}`}
            data-status={isDone ? 'complete' : isRunning ? 'running' : 'pending'}
            className="flex items-center gap-3"
          >
            <span className={`text-lg font-bold ${textClass}`}>{indicator}</span>
            <span className={`text-sm ${textClass}`}>{label}</span>
          </div>
        )
      })}

      {isFailed && (
        <p className="text-red-600 text-sm mt-2">
          Plan generation failed. Please check the gate status and try again.
        </p>
      )}

      {!isComplete && !isFailed && (
        <p className="text-gray-500 text-sm mt-2">This usually takes 60–90 seconds</p>
      )}
    </div>
  )
}
