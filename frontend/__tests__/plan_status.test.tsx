import '@testing-library/jest-dom'
import { render, screen } from '@testing-library/react'
import PlanStatus from '@/components/PlanStatus'

describe('PlanStatus', () => {
  it('shows completed steps as complete and pending steps as pending', () => {
    render(
      <PlanStatus
        planStatus="step_2_complete"
        stepOutputs={{ step_1: {}, step_2: {} }}
      />
    )

    const step1 = screen.getByTestId('step-indicator-step_1')
    const step2 = screen.getByTestId('step-indicator-step_2')
    const step3 = screen.getByTestId('step-indicator-step_3')

    expect(step1).toHaveAttribute('data-status', 'complete')
    expect(step2).toHaveAttribute('data-status', 'complete')
    expect(step3).toHaveAttribute('data-status', 'pending')
  })

  it('shows running step with running indicator', () => {
    render(
      <PlanStatus
        planStatus="step_2_running"
        stepOutputs={{ step_1: {} }}
      />
    )
    const step2 = screen.getByTestId('step-indicator-step_2')
    expect(step2).toHaveAttribute('data-status', 'running')
  })

  it('shows all 4 step indicators', () => {
    render(
      <PlanStatus
        planStatus="complete"
        stepOutputs={{ step_1: {}, step_2: {}, step_3: {}, step_4: {} }}
      />
    )
    expect(screen.getByTestId('step-indicator-step_1')).toBeInTheDocument()
    expect(screen.getByTestId('step-indicator-step_2')).toBeInTheDocument()
    expect(screen.getByTestId('step-indicator-step_3')).toBeInTheDocument()
    expect(screen.getByTestId('step-indicator-step_4')).toBeInTheDocument()
  })
})
