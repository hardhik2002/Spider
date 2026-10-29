import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { chromium } from 'playwright'

const researchId = process.env.VITE_LIVE_RESEARCH_ID ?? '38f3b146-6170-49c2-8b5c-64677210dd3f'
const snapshotId = process.env.VITE_LIVE_SNAPSHOT_ID ?? 'c259c850-3af0-4610-b29e-78019a971bb2'
const baseUrl = process.env.VITE_VISUAL_BASE_URL ?? 'http://127.0.0.1:5173'
const chromePath = process.env.CHROME_PATH ?? 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const screenshotDir = path.resolve(process.cwd(), '../docs/phase8a/screenshots')
const viewports = [[1920, 1080], [1440, 900], [1366, 768], [1024, 768]]

await mkdir(screenshotDir, { recursive: true })
const browser = await chromium.launch({ executablePath: chromePath, headless: true })
const report = { browser: await browser.version(), baseUrl, researchId, snapshotId, viewports: [] }

try {
  for (const [width, height] of viewports) {
    const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 })
    const consoleErrors = []
    const consoleWarnings = []
    const pageErrors = []
    const failedRequests = []
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
      if (message.type() === 'warning') consoleWarnings.push(message.text())
    })
    page.on('pageerror', (error) => pageErrors.push(error.message))
    page.on('response', (response) => {
      if (response.status() >= 400) failedRequests.push(`${response.status()} ${response.url()}`)
    })
    page.on('requestfailed', (request) => failedRequests.push(`${request.failure()?.errorText} ${request.url()}`))

    await page.goto(`${baseUrl}/research/${researchId}/graph/${snapshotId}`, { waitUntil: 'networkidle' })
    await page.locator('.graph-node.type-claim').first().waitFor({ timeout: 30000 })
    await page.locator('.graph-node.type-claim').first().click()
    await page.locator('.selected-node-heading').waitFor({ timeout: 15000 })
    await page.waitForTimeout(650)
    const screenshot = path.join(screenshotDir, `phase8a-${width}x${height}.png`)
    await page.screenshot({ path: screenshot, fullPage: true })
    const measurements = await page.evaluate(() => {
      const box = (selector) => {
        const element = document.querySelector(selector)
        if (!element) return null
        const rect = element.getBoundingClientRect()
        return { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) }
      }
      const spider = document.querySelector('.spider-hub img')
      return {
        viewport: { width: innerWidth, height: innerHeight },
        page: { scrollWidth: document.documentElement.scrollWidth, scrollHeight: document.documentElement.scrollHeight },
        header: box('.top-header'), sidebar: box('.sidebar'), graph: box('.graph-workspace'),
        spider: box('.spider-hub img'), inspector: box('.inspector'), toolbar: box('.graph-toolbar'),
        spiderImageLoaded: Boolean(spider?.complete && spider.naturalWidth),
        visibleGraphNodes: document.querySelectorAll('.graph-node').length,
        selectedClaim: document.querySelector('.selected-node-heading h2')?.textContent ?? null,
      }
    })
    report.viewports.push({ width, height, screenshot, measurements, consoleErrors, consoleWarnings, pageErrors, failedRequests })
    await page.close()
  }
} finally {
  await browser.close()
}

const reportPath = path.join(screenshotDir, 'measurements.json')
await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`)
console.log(JSON.stringify(report, null, 2))
