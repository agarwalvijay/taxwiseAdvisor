import '@testing-library/jest-dom'
import { render, screen, fireEvent } from '@testing-library/react'
import IncomeTable from '@/components/IncomeTable'

describe('IncomeTable', () => {
  it('shows validation error when fewer than 3 years entered', () => {
    const onSave = jest.fn()
    render(<IncomeTable onSave={onSave} />)

    // The initial render has 2 rows — clicking Save should show error
    const saveButton = screen.getByText(/Save & Continue/i)
    fireEvent.click(saveButton)

    expect(screen.getByText(/At least 3 years/i)).toBeInTheDocument()
    expect(onSave).not.toHaveBeenCalled()
  })

  it('calls onSave when 3 or more years are present', () => {
    const onSave = jest.fn()
    render(<IncomeTable onSave={onSave} />)

    // Add a third row
    const addButton = screen.getByText(/\+ Add Year/i)
    fireEvent.click(addButton)

    // Now save
    const saveButton = screen.getByText(/Save & Continue/i)
    fireEvent.click(saveButton)

    expect(onSave).toHaveBeenCalled()
    expect(screen.queryByText(/At least 3 years/i)).not.toBeInTheDocument()
  })
})
