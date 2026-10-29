import assert from 'node:assert/strict'
import console from 'node:console'
import process from 'node:process'
import { chromium } from 'playwright'

const researchId = process.env.VITE_LIVE_RESEARCH_ID ?? '38f3b146-6170-49c2-8b5c-64677210dd3f'
const snapshotId = process.env.VITE_LIVE_SNAPSHOT_ID ?? 'c259c850-3af0-4610-b29e-78019a971bb2'
const baseUrl = process.env.VITE_VISUAL_BASE_URL ?? 'http://127.0.0.1:5173'
const chromePath = process.env.CHROME_PATH ?? 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'

const browser = await chromium.launch({ executablePath: chromePath, headless: true })
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } })
const consoleErrors = []
const consoleWarnings = []
const pageErrors = []
const failedRequests = []
const verified = []
page.on('console', (message) => {
  if (message.type() === 'error') consoleErrors.push(message.text())
  if (message.type() === 'warning') consoleWarnings.push(message.text())
})
page.on('pageerror', (error) => pageErrors.push(error.message))
page.on('response', (response) => {
  if (response.status() >= 400) failedRequests.push(`${response.status()} ${response.url()}`)
})

try {
  await page.goto(`${baseUrl}/research/${researchId}/graph/${snapshotId}`, { waitUntil: 'networkidle' })
  await page.locator('.graph-node.type-claim').first().waitFor({ state: 'visible' })
  assert.equal(await page.locator('.graph-node').count(), 5)
  verified.push('graph load')

  const search = page.getByRole('textbox', { name: 'Search loaded graph' })
  await search.fill('Knowledge Graph-based GraphRAG')
  assert.ok(await page.getByRole('option').count() > 0)
  await page.getByRole('option').first().click()
  await page.locator('.selected-node-heading').waitFor()
  assert.match(await page.locator('.selected-node-heading').innerText(), /GraphRAG methods include/)
  verified.push('search', 'select claim', 'claim inspector')

  await page.getByRole('tab', { name: 'Citations' }).click()
  assert.ok(await page.locator('.citation-card').count() > 0)
  assert.match(await page.locator('.citation-card').first().innerText(), /Offsets/)
  verified.push('citation exact text and offsets')
  await page.getByRole('tab', { name: 'Evidence' }).click()
  assert.match(await page.locator('.inspector-scroll').innerText(), /SUPPORTING/)
  assert.match(await page.locator('.inspector-scroll').innerText(), /CONTRADICTING/)
  verified.push('supporting evidence', 'contradictory evidence')

  await page.locator('.graph-node.type-claim').first().dblclick()
  await page.locator('.graph-node.type-document').first().waitFor({ state: 'visible' })
  assert.ok(await page.locator('.graph-node').count() > 5)
  verified.push('node expansion')
  await page.locator('.graph-node.type-document').first().click()
  await page.getByRole('link', { name: 'Open document' }).waitFor()
  assert.match(await page.getByRole('link', { name: 'Open document' }).getAttribute('href'), /^https?:\/\//)
  verified.push('document and public URL')

  await page.locator('.graph-node.type-document').first().dblclick()
  await page.locator('.graph-node.type-source_group').first().waitFor({ state: 'visible' })
  await page.locator('.graph-node.type-source_group').first().click()
  await page.getByRole('heading', { name: 'Independent source group' }).waitFor()
  verified.push('source group')

  await page.getByRole('button', { name: /^Claims/ }).click()
  assert.equal(await page.locator('.graph-node').count(), 2)
  await page.getByRole('button', { name: /^Explore/ }).click()
  assert.ok(await page.locator('.graph-node').count() > 2)
  await page.getByRole('button', { name: /Settings & filters/ }).click()
  await page.getByRole('combobox', { name: 'Filter claim status' }).selectOption('SUPPORTED')
  assert.equal(await page.locator('.graph-node.type-claim').count(), 1)
  await page.getByRole('combobox', { name: 'Filter claim status' }).selectOption('ALL')
  verified.push('node and claim-status filters')

  await page.getByRole('button', { name: /Contradictions/ }).click()
  await page.locator('.contradictions-drawer').waitFor()
  assert.ok(await page.locator('.conflict-item').count() > 0)
  await page.locator('.conflict-item').first().click()
  await page.getByRole('tab', { name: 'Graph' }).click()
  await page.getByRole('button', { name: /Trace from research/ }).click()
  await page.locator('.path-clear').waitFor()
  verified.push('contradictions view', 'directed path highlight')

  const beforeZoom = await page.locator('.zoom-readout').innerText()
  await page.getByRole('button', { name: 'Zoom in' }).click()
  await page.waitForTimeout(350)
  assert.notEqual(await page.locator('.zoom-readout').innerText(), beforeZoom)
  verified.push('zoom')
  const graphBox = await page.locator('.graph-workspace').boundingBox()
  assert.ok(graphBox)
  const viewportBeforePan = await page.locator('.react-flow__viewport').getAttribute('style')
  const panPoint = await page.evaluate(({ x, y, width, height }) => {
    for (const yFactor of [0.75, 0.85, 0.6, 0.5]) {
      for (const xFactor of [0.12, 0.28, 0.5, 0.72, 0.88]) {
        const point = { x: x + width * xFactor, y: y + height * yFactor }
        if (globalThis.document.elementFromPoint(point.x, point.y)?.classList.contains('react-flow__pane')) return point
      }
    }
    return null
  }, graphBox)
  assert.ok(panPoint, 'No visible graph pane target for pan')
  const panX = panPoint.x
  const panY = panPoint.y
  await page.mouse.move(panX, panY)
  await page.mouse.down()
  await page.mouse.move(panX + 80, panY - 35, { steps: 6 })
  await page.mouse.up()
  assert.notEqual(await page.locator('.react-flow__viewport').getAttribute('style'), viewportBeforePan)
  verified.push('pan')
  await page.emulateMedia({ reducedMotion: 'reduce' })
  const animationDuration = await page.locator('.spider-halo').evaluate((element) => globalThis.getComputedStyle(element).animationDuration)
  assert.ok(animationDuration === '1e-05s' || animationDuration === '0.00001s' || animationDuration === '0s')
  const animatedEdges = page.locator('.react-flow__edge.animated path')
  if (await animatedEdges.count()) {
    assert.equal(await animatedEdges.first().evaluate((element) => globalThis.getComputedStyle(element).animationName), 'none')
  }
  verified.push('reduced motion')

  assert.deepEqual(consoleErrors, [])
  assert.deepEqual(consoleWarnings, [])
  assert.deepEqual(pageErrors, [])
  assert.deepEqual(failedRequests, [])
  console.log(JSON.stringify({ verified, consoleErrors, consoleWarnings, pageErrors, failedRequests }, null, 2))
} finally {
  await browser.close()
}
