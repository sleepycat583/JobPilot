/** App 入口组件的测试基础设施冒烟用例。 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import App from './App'

describe('App', () => {
  it('renders the task composer', () => {
    render(<App />)

    expect(screen.getByRole('heading', { name: 'Agent Progress Console' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '启动异步任务' })).toBeEnabled()
  })
})