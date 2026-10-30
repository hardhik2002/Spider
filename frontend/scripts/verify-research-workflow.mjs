import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'
import console from 'node:console'
import { chromium } from 'playwright'

const base = process.env.VITE_VISUAL_BASE_URL ?? 'http://127.0.0.1:5173'
const chromePath = process.env.CHROME_PATH ?? 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const researchId = process.env.VITE_LIVE_RESEARCH_ID ?? '38f3b146-6170-49c2-8b5c-64677210dd3f'
const evidenceId = process.env.VITE_LIVE_EVIDENCE_ID ?? '249893a3-6887-45b0-95b0-dae6132aa62d'
const snapshotId = process.env.VITE_LIVE_SNAPSHOT_ID ?? 'c259c850-3af0-4610-b29e-78019a971bb2'
const dir = path.resolve('../docs/phase8a/workflow-screenshots')
const sizes = [[1920, 1080], [1440, 900], [1366, 768], [1024, 768]]
const get = async (url) => { const response = await globalThis.fetch(`${base}${url}`); assert.equal(response.status, 200, url); return response.json() }
await mkdir(dir, { recursive: true })
const [summary, claims, research] = await Promise.all([
  get(`/api/v1/research/${researchId}/graph/${snapshotId}/summary`),
  get(`/api/v1/research/${researchId}/evidence/${evidenceId}/claims`),
  get(`/api/v1/research/${researchId}`),
])
console.log('Loaded persisted graph summary and claims')
const baseRun = { question: research.question, researchJobId: researchId, evidenceJobId: evidenceId, graphSnapshotId: snapshotId, startedAt: new Date().toISOString(), updatedAt: new Date().toISOString() }
const scenarios = [
  { name: 'home', url: '/', run: null, selector: '.question-card' },
  { name: 'progress', url: `/research/${researchId}`, run: { ...baseRun, stage: 'RESEARCHING', graphSnapshotId: undefined, evidenceJobId: undefined, researchStep: 'searching', searchResults: 4, sourcesSelected: 1, pagesCrawled: 1 }, selector: '.stage-list' },
  { name: 'result', url: `/research/${researchId}`, run: { ...baseRun, stage: 'COMPLETED', graphValid: true, summary, claims: claims.claims }, selector: '.result-overview' },
  { name: 'graph', url: `/research/${researchId}/graph/${snapshotId}`, run: { ...baseRun, stage: 'COMPLETED' }, selector: '.graph-workspace' },
  { name: 'developer', url: '/developer/open-graph', run: null, selector: '.graph-loader' },
]
const browser = await chromium.launch({ executablePath: chromePath, headless: true })
console.log('Launched Chrome')
const report = { browser: await browser.version(), data: 'Existing persisted Phase 7 graph and claims; progress screen uses a visual-only intercepted status', checks: [] }
try {
  for (const [width, height] of sizes) for (const scenario of scenarios) {
    console.log(`Checking ${scenario.name} ${width}x${height}`)
    const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 })
    console.log('  page created')
    const errors = []
    page.on('pageerror', (error) => errors.push(error.message))
    page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
    if (scenario.name === 'progress') await page.route(`**/api/v1/research/${researchId}`, async (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'running', stage: 'searching', total_search_results: 4, selected_seed_count: 1, pages_crawled: 1 }) }))
    await page.addInitScript((run) => { globalThis.localStorage.clear(); if (run) globalThis.localStorage.setItem('spidermind.activeResearchRun', JSON.stringify(run)) }, scenario.run)
    console.log('  init script added')
    await page.goto(`${base}${scenario.url}`, { waitUntil: 'domcontentloaded', timeout: 15000 })
    console.log('  navigated')
    try { await page.locator(scenario.selector).first().waitFor({ timeout: 10000 }) }
    catch (error) { console.error('page errors', errors); console.error('page text', (await page.locator('body').innerText().catch(() => 'unavailable')).slice(0, 500)); throw error }
    console.log('  selector ready')
    if (scenario.name === 'graph') await page.locator('.graph-node').first().waitFor({ timeout: 30000 })
    await page.waitForTimeout(400)
    const metrics = await page.evaluate(() => ({ scrollWidth: globalThis.document.documentElement.scrollWidth, scrollHeight: globalThis.document.documentElement.scrollHeight, viewportWidth: globalThis.innerWidth, viewportHeight: globalThis.innerHeight, text: globalThis.document.body.innerText }))
    assert.equal(metrics.scrollWidth, width, `${scenario.name} overflow ${width}`)
    if (scenario.name !== 'developer') assert.doesNotMatch(metrics.text, /Research Job ID|Graph Snapshot ID|Evidence Job ID|Job [0-9a-f]{8}/)
    assert.deepEqual(errors, [], `${scenario.name} console errors ${width}`)
    const filename = `workflow-${scenario.name}-${width}x${height}.png`
    await page.screenshot({ path: path.join(dir, filename), fullPage: true })
    report.checks.push({ scenario: scenario.name, viewport: `${width}x${height}`, screenshot: `docs/phase8a/workflow-screenshots/${filename}`, horizontalOverflow: false, consoleErrors: 0, verticalScroll: metrics.scrollHeight > height })
    await page.close()
  }
} finally { await browser.close() }
await writeFile(path.resolve('../docs/phase8a-workflow-visual-results.json'), JSON.stringify(report, null, 2) + '\n')
console.log(`Verified ${report.checks.length} workflow/viewport views`)
