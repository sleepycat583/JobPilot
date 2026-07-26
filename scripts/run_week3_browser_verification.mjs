/** 简历匹配浏览器验收：真实 HTTP、审核恢复与结果展示。 */
import { spawn } from 'node:child_process'
import { chromium } from 'playwright'

const processes = []
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function start(command, args, cwd = process.cwd()) {
  const child = spawn(command, args, { cwd, shell: process.platform === 'win32', stdio: 'pipe' })
  processes.push(child)
  child.stdout.on('data', (chunk) => process.stdout.write(`[${command}] ${chunk}`))
  child.stderr.on('data', (chunk) => process.stderr.write(`[${command}] ${chunk}`))
  return child
}

async function waitForUrl(url) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      if ((await fetch(url)).ok) return
    } catch {}
    await wait(200)
  }
  throw new Error(`服务未在规定时间启动: ${url}`)
}

try {
  start('python', ['-m', 'uvicorn', 'scripts.verification_server:app', '--port', '8011'])
  start('npx', ['vite', '--config', 'vite.verification.config.ts', '--host', '127.0.0.1', '--port', '5175'], 'frontend')
  await waitForUrl('http://127.0.0.1:8011/docs')
  await waitForUrl('http://127.0.0.1:5175')

  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage()
  const requests = []
  page.on('request', (request) => {
    if (request.url().includes('/api/') || request.url().includes('/v1/')) {
      requests.push({ method: request.method(), url: request.url(), headers: request.headers() })
    }
  })
  page.on('response', async (response) => {
    if (response.url().includes('/api/sessions/') && response.headers()['content-type']?.includes('text/event-stream')) {
      // 浏览器 EventSource 内容不直接暴露给 Playwright；网络请求和 UI 状态是此处证据。
    }
  })

  await page.goto('http://127.0.0.1:5175')
  await page.locator('input[type="file"]').setInputFiles({
    name: 'verification-resume.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('Java 后端工程师，负责 Spring Boot 服务与 API 设计。'),
  })
  const resumeButton = page.locator('.resume-item:not([disabled])').filter({ hasText: 'verification-resume' }).first()
  await resumeButton.waitFor({ timeout: 15_000 })
  await resumeButton.click()
  await page.getByLabel('职位描述').fill('后端工程师岗位，要求熟悉 Java、Spring Boot，并具备三年以上接口设计经验。')
  await page.getByRole('button', { name: '开始匹配' }).click()
  await page.getByRole('heading', { name: '审核 JD 解析结果' }).waitFor({ timeout: 15_000 })
  await page.getByRole('button', { name: '核可' }).click()
  await page.getByRole('heading', { name: /简历匹配结果 .*分/ }).waitFor({ timeout: 15_000 })
  await page.getByRole('heading', { name: '审核简历匹配结果' }).waitFor({ timeout: 15_000 })

  const taskPayload = await page.evaluate(() => document.querySelector('.thread-footer')?.textContent ?? null)
  const matchResult = {
    taskPosts: requests.filter((item) => item.method === 'POST' && item.url.includes('/api/tasks')).length,
    resumePosts: requests.filter((item) => item.method === 'POST' && item.url.includes('/resume')).length,
    threadId: taskPayload,
    panel: await page.locator('.match-result-panel').innerText(),
    passed: requests.some((item) => item.method === 'POST' && item.url.includes('/api/tasks'))
      && requests.filter((item) => item.method === 'POST' && item.url.includes('/resume')).length >= 1,
  }
  console.log(JSON.stringify({ matchResult }, null, 2))
  await browser.close()
  process.exitCode = matchResult.passed ? 0 : 1
} finally {
  for (const child of processes) child.kill()
}