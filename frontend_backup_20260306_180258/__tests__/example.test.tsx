import { render, screen } from '@testing-library/react'

describe('Basic Rendering', () => {
  it('renders a simple div', () => {
    render(<div data-testid="test-div">Hello Jest</div>)
    const element = screen.getByTestId('test-div')
    expect(element).toBeInTheDocument()
    expect(element).toHaveTextContent('Hello Jest')
  })
})
