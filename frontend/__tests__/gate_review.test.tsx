import '@testing-library/jest-dom'
import { render, screen } from '@testing-library/react'
import GateReview from '@/components/GateReview'

const mockFlaggedFields = [
  {
    field_name: 'agi',
    extracted_value: 207840,
    confidence: 0.67,
    reason: 'AGI was extracted with 67% confidence, below the required 85% threshold.',
    field_classification: 'hard_required',
  },
  {
    field_name: 'filing_status',
    extracted_value: 'married_filing_jointly',
    confidence: 0.55,
    reason: 'Filing status confidence is low. Please confirm the extracted value.',
    field_classification: 'hard_required',
  },
]

describe('GateReview', () => {
  it('renders flagged field cards with confirm buttons', () => {
    render(
      <GateReview
        flaggedFields={mockFlaggedFields}
        contradictions={[]}
        onConfirmField={jest.fn()}
        onResolveContradiction={jest.fn()}
      />
    )

    // Both field names should appear (as human-readable labels)
    expect(screen.getAllByText(/Agi/i).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/Filing Status/i).length).toBeGreaterThanOrEqual(1)

    // Each card should have a Confirm button
    const confirmButtons = screen.getAllByText(/Confirm/i)
    expect(confirmButtons.length).toBeGreaterThanOrEqual(2)

    // Each card should have an input for entering correct value
    const inputs = screen.getAllByPlaceholderText(/Enter correct value/i)
    expect(inputs.length).toBe(2)
  })

  it('shows all-clear message when no items need review', () => {
    render(
      <GateReview
        flaggedFields={[]}
        contradictions={[]}
        onConfirmField={jest.fn()}
        onResolveContradiction={jest.fn()}
      />
    )
    expect(screen.getByText(/All items reviewed/i)).toBeInTheDocument()
  })
})
