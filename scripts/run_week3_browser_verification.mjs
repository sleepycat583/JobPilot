/** 第 3 章浏览器真实验证：刷新恢复与 SSE 断线重连。 */
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
  await page.getByLabel('职位描述').fill('后端工程师岗位，要求熟悉 Java、Spring Boot，并具备三年以上接口设计经验。')
  await page.getByRole('button', { name: '开始分析' }).click()
  await page.getByRole('button', { name: '核可' }).waitFor({ timeout: 15_000 })
  const formBeforeReload = await page.locator('.panel').innerText()
  const taskPostsBeforeReload = requests.filter((item) => item.method === 'POST' && item.url.includes('/api/tasks')).length
  const threadId = await page.locator('footer').innerText()
  requests.length = 0
  await page.reload()
  await page.getByRole('button', { name: '核可' }).waitFor({ timeout: 10_000 })
  const formAfterReload = await page.locator('.panel').innerText()
  const refreshRequests = requests.map((item) => `${item.method} ${new URL(item.url).pathname}`)
  const refreshResult = {
    threadId,
    formBeforeReload,
    formAfterReload,
    refreshRequests,
    taskPostsBeforeReload,
    passed: refreshRequests.some((value) => value.startsWith('GET /v1/threads/'))
      && !refreshRequests.some((value) => value === 'POST /api/tasks')
      && formAfterReload.includes('人工审核'),
  }

  // 新页面建立新的 EventSource，避免刷新场景遗留的同 session 订阅影响断线验证。
  const reconnectPage = await browser.newPage()
  reconnectPage.on('request', (request) => {
    if (request.url().includes('/api/') || request.url().includes('/v1/')) {
      requests.push({ method: request.method(), url: request.url(), headers: request.headers() })
    }
  })
  await reconnectPage.goto('http://127.0.0.1:5175')
  await reconnectPage.getByLabel('职位描述').fill('后端工程师岗位，要求熟悉 Java、Spring Boot，并具备三年以上接口设计经验。')
  await reconnectPage.getByRole('button', { name: '开始分析' }).click()
  await wait(300)
  await reconnectPage.context().setOffline(true)
  await wait(2_500)
  await reconnectPage.context().setOffline(false)
  let formRecovered = true
  let recoveryError = null
  try {
    await reconnectPage.getByRole('button', { name: '核可' }).waitFor({ timeout: 15_000 })
  } catch (error) {
    formRecovered = false
    recoveryError = error instanceof Error ? error.message : String(error)
  }
  await wait(1_000)
  const eventStreamRequests = requests.filter((item) => item.url.includes('/api/sessions/') && item.url.includes('/events'))
  const reconnectHeaders = eventStreamRequests.map((item) => item.headers['last-event-id'] ?? null)
  const reconnectResult = {
    eventStreamRequestCount: eventStreamRequests.length,
    lastEventIdHeaders: reconnectHeaders,
    formRecovered,
    recoveryError,
    passed: formRecovered && eventStreamRequests.length > 1 && reconnectHeaders.slice(1).some(Boolean),
    note: '当前服务端未按 Last-Event-ID 回放，且 UI 未暴露原始 event_id；序列去重/倒退不能给出通过结论。',
  }
  // 通过浏览器内 fetch 记录前端实际接收的非法 JSON 422 body，并按 parseApiError 相同规则检查。
  const errorProtocolResult = await page.evaluate(async () => {
    const response = await fetch('/v1/threads/not-a-thread/resume', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{',
    })
    const rawBody = await response.text()
    const parsed = JSON.parse(rawBody)
    const apiError = parsed?.error
    return {
      status: response.status,
      rawBody,
      parseApiErrorResult: {
        code: apiError?.code ?? null,
        message: apiError?.message ?? `审核提交失败。 (HTTP ${response.status})`,
        usedFallback: !apiError?.message,
      },
    }
  })
  console.log(JSON.stringify({ refreshResult, reconnectResult, errorProtocolResult }, null, 2))
  await browser.close()
  process.exitCode = refreshResult.passed && reconnectResult.passed ? 0 : 1
} finally {
  for (const child of processes) child.kill()
}