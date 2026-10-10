import { expect, mock, test } from 'claude-code/testing'

import { renderSvg } from '../hooks/view3d'
import type { Model } from '../hooks/view3d'

const cube: Model = {
  step: 1,
  label: 'cube',
  tris: 2,
  objects: [{ name: 'Cube', color: [0.8, 0.1, 0.1], tris: [0, 0, 0, 1, 0, 0, 0, 0, 1, 1, 0, 0, 1, 0, 1, 0, 0, 1] }],
}

test('the 3D view draws a model and stays under the SVG size limit', async () => {
  const svg = renderSvg(cube, { angle: 30, isWire: false, glow: ['Cube'], caption: 'Step 1' })
  expect(svg.startsWith('<svg')).toBe(true)
  expect(svg.includes('class="g"')).toBe(true)

  // a big model is trimmed to fit
  const tris: number[] = []
  for (let i = 0; i < 6000; i++) tris.push(i % 7, (i * 3) % 5, 0, i % 7 + 1, (i * 3) % 5, 0, i % 7, (i * 3) % 5, 1)
  const big = renderSvg({ ...cube, objects: [{ name: 'Big', color: [0.5, 0.5, 0.5], open: true, tris }] }, {
    angle: 0, isWire: true, glow: [], caption: 'big',
  })
  expect(big.length).toBeLessThan(131_072)
})

test('the pane draws an empty scene on every surface', async ($, on) => {
  mock.clock(on, { now: 1_000 })
  for (const surface of ['terminal', 'desktop', 'mobile'] as const) {
    const ui = await $.ui.mount({
      plugin: 'blender-live',
      surface,
      component: 'Pane',
      requestId: 'blender-live',
      props: { title: 'Blender Live', isFocused: false, bodyColumns: 60, seat: 'dock' } as never,
    })
    expect(await ui.find({ type: 'Text', text: /Waiting for the first step/ })).toBeDefined()
    expect(await ui.find({ key: 'replay' })).toBeDefined()
    await ui.unmount()
  }
})
