/** 简历上传 UI 的真实浏览器验证：上传、轮询、选择与分析请求载荷。 */
import { chromium } from 'playwright'

const browser = await chromium.launch({ headless: true })
const page = await browser.newPage()
const consoleErrors = []
let taskPayload = null

page.on('console', (message) => {
  if (message.type() === 'error') consoleErrors.push(message.text())
})
page.on('request', (request) => {
  if (request.url().includes('/api/tasks') && request.method() === 'POST') {
    taskPayload = JSON.parse(request.postData() ?? '{}')
  }
})

try {
  await page.goto('http://127.0.0.1:5175/', { waitUntil: 'networkidle' })
  await page.locator('input[type="file"]').setInputFiles({
    name: 'browser-verification.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('姓名：测试候选人\n技能：Python、FastAPI、React\n经验：三年后端开发经验\n', 'utf8'),
  })
  const resumeButton = page.locator('.resume-item:not([disabled])').filter({ hasText: 'browser-verification.txt' }).last()
  await resumeButton.waitFor({ timeout: 10_000 })
  await resumeButton.click()
  await page.getByLabel('职位描述').fill('招聘后端工程师，要求熟悉 Python、FastAPI 与 React，并具备三年以上接口设计经验。')
  await page.getByRole('button', { name: '开始分析' }).click()
  await page.getByRole('button', { name: '核可' }).waitFor({ timeout: 15_000 })

  if (!taskPayload?.resume_id) throw new Error('分析请求未携带 resume_id')
  if (consoleErrors.length) throw new Error(`浏览器控制台错误: ${consoleErrors.join(' | ')}`)
  console.log(JSON.stringify({ passed: true, taskPayload }, null, 2))
} finally {
  await browser.close()
}