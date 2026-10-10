import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { BlenderFrame, BlenderStep } from '../types'
import { renderSvg } from './view3d'
import type { Model } from './view3d'

type $ = EngineInterface

const PLUGIN = 'blender-live'
const PANE = 'blender-live'
const TITLE = 'Blender Live'
const TICK_MS = 110
const SPIN_DEG = 3
const SPIN_AFTER_STEP_MS = 40_000
const GLOW_MS = 7_000
const REVEAL_MS = 2_600
// how long each streamed snapshot stays; faster when many are waiting
const FRAME_MS = 700
const FAST_FRAME_MS = 280

const steps = atom({ plugin: 'blender-live', key: 'steps' } as const, [])
const viewing = atom({ plugin: 'blender-live', key: 'viewing' } as const, null)
const angle = atom({ plugin: 'blender-live', key: 'angle' } as const, 35)
const spinUntil = atom({ plugin: 'blender-live', key: 'spinUntil' } as const, 0)
const isWire = atom({ plugin: 'blender-live', key: 'isWire' } as const, false)
const busy = atom({ plugin: 'blender-live', key: 'busy' } as const, null)
const flashUntil = atom({ plugin: 'blender-live', key: 'flashUntil' } as const, 0)
const revealStart = atom({ plugin: 'blender-live', key: 'revealStart' } as const, 0)
const isBandHidden = atom({ plugin: 'blender-live', key: 'isBandHidden' } as const, false)
const frame = atom({ plugin: 'blender-live', key: 'frame' } as const, null)
const frameStart = atom({ plugin: 'blender-live', key: 'frameStart' } as const, 0)
const queue = atom({ plugin: 'blender-live', key: 'queue' } as const, [])
const note = atom({ plugin: 'blender-live', key: 'note' } as const, null)

const STEP_DESCRIPTION = `Run one modeling step in Blender (headless, Blender 5.x) and show it to the user live.

The user is watching a spinning 3D view of the scene that updates after every call, with the newly added parts glowing, so build the model in small, visible steps: one part or one change per call, each with a short label ("Chest body", "Rounded lid", "Iron bands"). The scene is saved between calls.

\`code\` is Python run inside Blender with these ready to use: bpy, bmesh, Vector, math, and
  mat(name, (r, g, b))  -> get/create a flat-coloured material (linear RGB 0..1)
  paint(obj, material)  -> give an object that material
Give every object a clear name and a material (paint) so it shows in colour. Objects whose names start with "_" are helpers and are hidden. The ground is at z = 0.

The view streams while your code runs: after every top-level line (and every line directly inside a top-level for/while loop) that changes the scene, the user sees a new frame with that line of code under it, and shapes slide smoothly into place. So model the way a person would, one visible operation per line: add a primitive, then extrude, translate, scale, bevel or inset with bmesh (bmesh.from_edit_mesh in edit mode, or bmesh.new() + bm.to_mesh), rather than one big helper function. About 5 to 25 changing lines per step looks good; the call returns after the user has watched them play.

If the code raises, nothing is saved and you get the traceback: fix it and call again. On success you get the step's triangle count, the scene's objects and a rendered picture to check your work. Low-poly, N64-style models (a few hundred triangles, flat colours) look best.`

let workDir = 'blender-live'
let ticker: { cancel: () => void } | undefined
const models = new Map<string, Model>()

function run($: $, args: string[], timeoutMs = 300_000) {
  return $.process.run(['sh', `${$.plugin.root}/engine/bstep`, ...args], {
    env: { BSTEP_DIR: workDir },
    timeoutMs,
  })
}

async function openPane($: $) {
  return $.ui.open({ id: PANE, title: TITLE })
}

function compactLine(input: unknown, output: unknown, isRunning: boolean, isErrored: boolean) {
  const label = String((input as { label?: unknown } | null)?.label ?? '')
  const text = typeof output === 'string' ? output : JSON.stringify(output ?? '')
  const tris = /(\d+) triangles/.exec(text)?.[1]
  if (isRunning) return `Blender: building "${label}"...`
  if (isErrored) return `Blender: "${label}" failed, fixing it`
  return `Blender: "${label}" done${tris ? ` (${tris} tris)` : ''}, watch it in Blender Live`
}

async function spinFor($: $, ms: number) {
  const now = await $.clock.now()
  await update($, spinUntil, () => now + ms)
}

async function loadModel($: $, path: string): Promise<Model | null> {
  const cached = models.get(path)
  if (cached) return cached
  try {
    const model = JSON.parse(await $.fs.read(path)) as Model
    models.set(path, model)
    return model
  } catch {
    return null
  }
}

// a failed call is a deny: the model reads the text as an error result
function textResult(text: string, isError = false) {
  return isError ? { deny: text } : { result: text }
}

// plays the next waiting snapshot once the current one has had its time
async function playNext($: $) {
  const waiting = await read($, queue)
  if (waiting.length === 0) return
  const now = await $.clock.now()
  const hold = waiting.length > 10 ? FAST_FRAME_MS : FRAME_MS
  if (now - (await read($, frameStart)) < hold) return
  const [next, ...rest] = waiting
  await update($, queue, () => rest)
  await update($, frame, () => next ?? null)
  await update($, frameStart, () => now)
  await update($, viewing, () => null)
}

// what the pane's buttons do
async function act($: $, key: string) {
  const list = await read($, steps)
  const current = (await read($, viewing)) ?? list.at(-1)?.n ?? 0
  const go = (n: number | null) => update($, viewing, () => (n === null || n >= (list.at(-1)?.n ?? 0) ? null : Math.max(1, n)))

  if (key === 'prev') await go(current - 1)
  else if (key === 'next') await go(current + 1)
  else if (key === 'latest') await go(null)
  else if (key === 'wire') await update($, isWire, w => !w)
  else if (key === 'spin') {
    const now = await $.clock.now()
    await update($, spinUntil, until => (until > now ? 0 : now + 10 * 60_000))
  } else if (key === 'replay' && list.length > 0) {
    const all = list.flatMap(st => st.frames ?? [])
    await update($, frame, () => null)
    await update($, queue, () => all.map((f, i) => ({ ...f, prevPath: i === 0 ? null : all[i - 1]!.path })))
    await spinFor($, all.length * FAST_FRAME_MS + 20_000)
  } else if (key === 'hide') await update($, isBandHidden, () => true)
  else if (key === 'pane') await openPane($)
  else if (key.startsWith('s')) await go(Number(key.slice(1)))
}

// everything the live view draws from, read in one place (reads subscribe the drawing)
async function liveView($: $) {
  const list = await read($, steps)
  const at = await read($, viewing)
  const shown = at === null ? list.at(-1) : list.find(s => s.n === at)
  const working = await read($, busy)
  const now = await $.clock.now()
  const isSpinning = now < (await read($, spinUntil))
  const fraction = (now - (await read($, revealStart))) / REVEAL_MS
  const isWireOn = await read($, isWire)
  const live = at === null ? await read($, frame) : null
  let svg: string
  if (live) {
    const sinceFrame = now - (await read($, frameStart))
    svg = renderSvg(await loadModel($, live.path), {
      angle: await read($, angle),
      isWire: isWireOn,
      glow: [],
      caption: live.label,
      transition: {
        prev: live.prevPath ? await loadModel($, live.prevPath) : null,
        t: sinceFrame / Math.min(FRAME_MS * 0.8, 520),
      },
      codeLine: live.code,
      live: { building: working },
    })
  } else {
    const before = shown ? list.find(st => st.n === shown.n - 1) : undefined
    svg = renderSvg(shown ? await loadModel($, shown.json) : null, {
      angle: await read($, angle),
      isWire: isWireOn,
      glow: shown && now < (await read($, flashUntil)) ? shown.added : [],
      caption: shown ? `${shown.n}. ${shown.label}` : 'empty scene',
      transition: shown
        ? { prev: before ? await loadModel($, before.json) : null, t: fraction }
        : undefined,
      live: { building: working },
    })
  }
  const status = working
    ? `Building: ${working}...`
    : shown
      ? `Step ${shown.n} of ${list.length}: ${shown.label}`
      : 'Waiting for the first step. Ask Claude to model something!'
  return { list, shown, working, isSpinning, isWireOn, svg, status, message: await read($, note) }
}


export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    workDir = `${started.cwd}/blender-live`

    await $.command.register({ name: 'blender', description: 'Show the Blender Live view above the prompt' })
    await $.command.register({ name: 'blender-pane', description: 'Open Blender Live in a big side pane' })
    await $.command.register({
      name: 'blender-setup',
      description: 'Install Blender for the Blender Live mod (one time, about a minute)',
    })

    await $.tool.register({
      name: 'blender_step',
      description: STEP_DESCRIPTION,
      isDeferred: false,
      inputSchema: {
        type: 'object',
        properties: {
          label: { type: 'string', description: 'Short name of this step, shown to the user' },
          code: { type: 'string', description: 'Python (bpy) to run on the scene' },
        },
        required: ['label', 'code'],
      },
    })
    await $.tool.register({
      name: 'blender_undo',
      description: 'Undo the last Blender Live step: the scene goes back to how it was before it.',
      isDeferred: false,
    })
    await $.tool.register({
      name: 'blender_reset',
      description: 'Start a new, empty Blender Live scene (clears every step). Call this before starting a new model.',
      isDeferred: false,
    })
    await $.tool.register({
      name: 'blender_finish',
      description:
        'Finish the Blender Live model: makes a timelapse GIF of all steps, a contact sheet, and exports model.glb. Returns the file paths (scene.blend is there too).',
      isDeferred: false,
    })

    // the turntable: turn a few degrees per tick while spinning
    ticker?.cancel()
    ticker = $.clock.every(TICK_MS, async () => {
      await playNext($)
      const now = await $.clock.now()
      if (now < (await read($, spinUntil))) {
        await update($, angle, a => (a + SPIN_DEG) % 360)
      }
    })

    return started
  })

  on('command.run', { command: 'blender' }, async $ => {
    await update($, isBandHidden, () => false)
    await spinFor($, SPIN_AFTER_STEP_MS)
    return { text: 'Blender Live is showing above the prompt.' }
  })

  on('command.run', { command: 'blender-pane' }, async $ => {
    await openPane($)
    await spinFor($, SPIN_AFTER_STEP_MS)
    return { text: 'Blender Live pane opened.' }
  })

  on('command.run', { command: 'blender-setup' }, async $ => {
    $.ui.toast('Installing Blender... this takes about a minute')
    const out = await run($, ['setup'], 600_000)
    const ok = out.stdout.includes('BSTEP_READY')
    return { text: ok ? 'Blender is installed and ready.' : `Setup failed:\n${out.stdout}\n${out.stderr}` }
  })

  on('tool.call', { tool: 'mcp__blender-live__blender_step' }, async ($, e, next) => {
    const label = String(e.label ?? 'Step').slice(0, 60)
    const code = String(e.code ?? '')
    await update($, isBandHidden, () => false)
    await openPane($)
    await spinFor($, SPIN_AFTER_STEP_MS)
    await update($, busy, () => label)
    await update($, note, () => null)
    try {
      // stream Blender's output: every BSTEP_SNAP line is a frame to play now
      const lines: string[] = []
      const stepFrames: BlenderFrame[] = []
      let lastPath = (await read($, queue)).at(-1)?.path ?? (await read($, frame))?.path ?? null
      let pending = ''
      const child = $.process.spawn({
        argv: ['sh', `${$.plugin.root}/engine/bstep`, 'step', label, '-c', code],
        env: { BSTEP_DIR: workDir, BSTEP_STREAM: '1' },
      })
      for await (const chunk of child) {
        pending += chunk.text
        const parts = pending.split('\n')
        pending = parts.pop() ?? ''
        for (const line of parts) {
          if (!line.startsWith('BSTEP_')) continue
          lines.push(line)
          const snap = /^BSTEP_SNAP (.+?)\t(\d+)\t(.*)$/.exec(line)
          if (snap) {
            const f: BlenderFrame = { path: snap[1]!, prevPath: lastPath, line: Number(snap[2]), code: snap[3]!, label }
            lastPath = f.path
            stepFrames.push(f)
            await update($, queue, q => [...q, f])
          }
        }
      }
      if (pending.startsWith('BSTEP_')) lines.push(pending)
      const exit = await child.result
      const stdout = lines.join('\n')
      if (exit.code === 3 || stdout.includes('BSTEP_NEEDS_SETUP')) {
        return textResult('Blender is not installed yet. Ask the user to run /blender-setup.', true)
      }
      const ok = /BSTEP_OK step=(\d+) tris=(\d+) png=(.+)/.exec(stdout)
      if (!ok) {
        // the scene was not saved: drop this step's frames from the stream
        await update($, queue, q => q.filter(f => !stepFrames.includes(f)))
        await update($, note, () => `Step "${label}" failed, fixing it...`)
        const tb = lines.filter(l => l.startsWith('BSTEP_TB ')).map(l => l.slice(9)).join('\n')
        return textResult(`The step failed and the scene was not changed.\n${tb || stdout}`.trim(), true)
      }
      const n = Number(ok[1])
      const png = ok[3]!.trim()
      const objects = (/BSTEP_OBJECTS (.*)/.exec(stdout)?.[1] ?? '').split(', ').filter(Boolean)
      const before = (await read($, steps)).filter(s => s.n < n)
      const known = new Set(before.at(-1)?.objects ?? [])
      const step: BlenderStep = {
        n,
        label,
        tris: Number(ok[2]),
        png,
        json: png.replace(/\.png$/, '.json'),
        objects,
        added: objects.filter(o => !known.has(o)),
        frames: stepFrames,
      }
      models.delete(step.json)
      await update($, steps, () => [...before, step])
      await update($, viewing, () => null)
      const now = await $.clock.now()
      await update($, revealStart, () => now)
      await update($, flashUntil, () => now + GLOW_MS)
      await spinFor($, SPIN_AFTER_STEP_MS)


      const summary =
        `Step ${n} "${label}" done: ${step.tris} triangles in the scene.\n` +
        `Objects: ${objects.join(', ') || '(none)'}\n` +
        (step.added.length ? `New this step: ${step.added.join(', ')}` : 'No new objects (existing ones changed).') +
        `\nLive feed frames (collection "frames", doc id = file name): ${stepFrames.map(f => f.path).join(' ')}`
      return textResult(summary)
    } finally {
      await update($, busy, () => null)
    }
  })

  on('tool.call', { tool: 'mcp__blender-live__blender_undo' }, async $ => {
    const out = await run($, ['undo'])
    if (out.exitCode !== 0) return textResult(`Nothing to undo.\n${out.stdout}`.trim(), true)
    const list = await read($, steps)
    const gone = list.at(-1)
    await update($, steps, l => l.slice(0, -1))
    await update($, queue, () => [])
    await update($, frame, () => null)
    await update($, viewing, () => null)
    await update($, note, () => (gone ? `Undid step ${gone.n}: ${gone.label}` : null))
    return textResult(gone ? `Undid step ${gone.n} "${gone.label}".` : 'Undone.')
  })

  on('tool.call', { tool: 'mcp__blender-live__blender_reset' }, async $ => {
    const out = await run($, ['reset'])
    if (out.exitCode === 3) return textResult('Blender is not installed yet. Ask the user to run /blender-setup.', true)
    models.clear()
    await update($, queue, () => [])
    await update($, frame, () => null)
    await update($, steps, () => [])
    await update($, viewing, () => null)
    await update($, note, () => null)
    await update($, isBandHidden, () => false)
    return textResult(`New empty scene ready in ${workDir}.`)
  })

  on('tool.call', { tool: 'mcp__blender-live__blender_finish' }, async $ => {
    const movie = await run($, ['movie'])
    const glb = await run($, ['export'])
    const files = [
      /gif=(\S+)/.exec(movie.stdout)?.[1],
      /sheet=(\S+)/.exec(movie.stdout)?.[1],
      /glb=(\S+)/.exec(glb.stdout)?.[1],
      /blend=(\S+)/.exec(glb.stdout)?.[1],
    ].filter(Boolean)
    await update($, note, () => `Finished! Files are in ${workDir}`)
    await spinFor($, 120_000)
    return textResult(
      files.length
        ? `Finished. Files:\n${files.join('\n')}\nSend the GIF and .blend to the user.`
        : `Could not finish.\n${movie.stdout}${movie.stderr}\n${glb.stdout}${glb.stderr}`,
      files.length === 0,
    )
  })

  // keep the chat tidy: one short line per Blender call, the pane is the window
  on('ui.render', { component: 'ToolUse' }, ($, e, next) => {
    if (!e.props.tool.startsWith('mcp__blender-live__')) return next(e)
    const { Text } = $.ui.resolve(e)
    const p = e.props
    const line = p.tool.endsWith('blender_step')
      ? compactLine(p.input, p.output, p.isRunning, p.isErrored)
      : `Blender: ${p.tool.replace('mcp__blender-live__blender_', '')}${p.isRunning ? '...' : ' done'}`
    return <Text color={p.isErrored ? 'red' : undefined} dimColor={!p.isRunning && !p.isErrored}>{line}</Text>
  })

  on('ui.render', { component: 'ToolResult' }, async ($, e, next) => {
    if (!e.props.tool.startsWith('mcp__blender-live__') || e.props.isErrored) return next(e)
    const { Box } = $.ui.resolve(e)
    return <Box />
  })

  // the live view, pinned above the prompt so it stays on screen while the chat scrolls
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey || (await read($, isBandHidden))) return next(e)
    const v = await liveView($)
    if (!v.shown && !v.working) return next(e)

    if (e.surface === 'terminal') {
      const { Box, Text, Button, Image } = $.ui.resolve(e)
      const cols = Math.max(20, Math.min(56, (e.props.bodyColumns ?? 60) - 2))
      return (
        <Box flexDirection="column">
          {v.shown && (
            <Image key="band-shot" source={{ file: v.shown.png, format: 'png' }} columns={cols}
              rows={Math.round(cols * 0.375)} alt={v.status} />
          )}
          <Box flexDirection="row" gap={1}>
            <Text bold color={v.working ? 'yellow' : 'red'}>{v.working ? '● building' : '● LIVE'}</Text>
            <Text>{v.status}</Text>
            <Button key="hide" label="Hide" onPress={() => act($, 'hide')} />
          </Box>
        </Box>
      )
    }

    const { Box, Text, Button, Svg } = $.ui.resolve(e)
    return (
      <Box flexDirection="column">
        <Svg key="band-view" source={v.svg} alt={v.status} height={250} isInteractive />
        <Box flexDirection="row" flexWrap="wrap" gap={1} alignItems="center">
          <Text bold color={v.working ? 'yellow' : undefined}>{v.status}</Text>
          <Button key="replay" label="Replay" onPress={() => act($, 'replay')} />
          <Button key="spin" label={v.isSpinning ? 'Stop spin' : 'Spin'} onPress={() => act($, 'spin')} />
          <Button key="wire" label={v.isWireOn ? 'Solid' : 'Wireframe'} onPress={() => act($, 'wire')} />
          <Button key="pane" label="Bigger" onPress={() => act($, 'pane')} />
          <Button key="hide" label="Hide" onPress={() => act($, 'hide')} />
        </Box>
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { list, shown, working, isSpinning, svg, status, message } = await liveView($)
    const wire = await read($, isWire)

    const controls = (Box: any, Button: any) => (
      <Box flexDirection="row" flexWrap="wrap" gap={1}>
        <Button key="prev" label="< Prev" onPress={() => act($, 'prev')} />
        <Button key="next" label="Next >" onPress={() => act($, 'next')} />
        <Button key="latest" label="Latest" onPress={() => act($, 'latest')} />
        <Button key="replay" label="Replay build" variant="primary" onPress={() => act($, 'replay')} />
        <Button key="spin" label={isSpinning ? 'Stop spin' : 'Spin'} onPress={() => act($, 'spin')} />
        <Button key="wire" label={wire ? 'Solid' : 'Wireframe'} onPress={() => act($, 'wire')} />
      </Box>
    )
    const timeline = (Box: any, Button: any, Text: any) => (
      <Box flexDirection="row" flexWrap="wrap" gap={1}>
        {list.map(s => (
          <Button key={`s${s.n}`} plain onPress={() => act($, `s${s.n}`)}>
            <Text bold={shown?.n === s.n} color={shown?.n === s.n ? 'yellow' : undefined}>
              {s.n}. {s.label}
            </Text>
            <Text dimColor> {s.tris}</Text>
          </Button>
        ))}
      </Box>
    )

    if (e.surface === 'terminal') {
      const { Box, Text, Button, Image } = $.ui.resolve(e)
      const cols = Math.max(20, Math.min(80, (e.props.bodyColumns ?? 60) - 2))
      return (
        <Box flexDirection="column" gap={1}>
          <Text bold color={working ? 'yellow' : undefined}>{status}</Text>
          {shown && (
            <Image
              key="shot"
              source={{ file: shown.png, format: 'png' }}
              columns={cols}
              rows={Math.round(cols * 0.375)}
              alt={`Render of step ${shown.n}: ${shown.label}`}
            />
          )}
          {message && <Text color="cyan">{message}</Text>}
          {controls(Box, Button)}
          {timeline(Box, Button, Text)}
        </Box>
      )
    }

    const { Box, Text, Button, Svg } = $.ui.resolve(e)
    return (
      <Box flexDirection="column" gap={1}>
        <Text bold color={working ? 'yellow' : undefined}>{status}</Text>
        <Svg key="view" source={svg} alt={status} isInteractive />
        {message && <Text color="cyan">{message}</Text>}
        {controls(Box, Button)}
        {timeline(Box, Button, Text)}
      </Box>
    )
  })
}
