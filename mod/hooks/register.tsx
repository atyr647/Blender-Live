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

const STEP_DESCRIPTION = `Run one modeling step in Blender (headless) and stream it to the user's live feed.

The user watches a 3D feed of the scene: a simplified preview streams while your code runs, and when the step finishes the feed swaps in the full-detail model with its real materials (a glTF the step exports, with procedural textures baked into images, lit by the scene's own world/sky). So build in visible steps with short labels ("Hull", "Canopy", "Nose gear"). The scene is saved between calls. Any size works, from N64 low-poly to multi-million-triangle models.

\`code\` is Python run inside Blender with these ready to use: bpy, bmesh, Vector, math, np (numpy), and
  mat(name, (r, g, b))  -> get/create a simple material (linear RGB 0..1); any real node material works too
  paint(obj, material)  -> give an object that material
  snap("what")          -> take a live frame now (also works inside your own helper functions)
  focus("Armory*")      -> point the feed camera at these objects (globs, comma separated) from here on
Objects whose names start with "_" are helpers and are hidden. The ground is at z = 0. Folders and prelude files set with blender_configure (an art kit, gear code) are importable/loaded in every step.

Live frames: by default one after every top-level line (and every line directly in a top-level loop) that changes the scene, paced automatically so big scenes stay fast. Options: \`mode\` "objects" (a frame only when parts are added/removed: good for scripts that make hundreds of parts) or "end" (one frame per step); \`pace\` seconds between frames; \`focus\` to look at part of a big model (everything around it turns see-through in the feed); \`render\` "cycles" (or "cycles:128:1920x1080") to also render the step realistically with Cycles.

If the code raises, nothing is saved and you get the traceback: fix it and call again. On success you get the triangle count, the objects, and a list of files to send to the feed (do that right away, as the result says).`

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
    const raw = JSON.parse(await $.fs.read(path)) as Model & { format?: number; objects: unknown[] }
    const model = raw.format === 3 ? fromFormat3(raw as unknown as Format3) : raw
    models.set(path, model)
    return model
  } catch {
    return null
  }
}

type Format3Entry = { n: string; c: number[]; o?: boolean; lo: number[]; hi: number[]; v: string; i: string; w: number }
type Format3 = { step: number; label: string; tris: number; objects: Format3Entry[] }

function bytesOf(b64: string): Uint8Array {
  const bin = atob(b64)
  const out = new Uint8Array(bin.length)
  for (let k = 0; k < bin.length; k++) out[k] = bin.charCodeAt(k)
  return out
}

// the engine's compact frames (16-bit positions + indices) as flat triangles for the SVG view
function fromFormat3(doc: Format3): Model {
  const objects = doc.objects.map(o => {
    const vb = bytesOf(o.v)
    const q = new Int16Array(vb.buffer, vb.byteOffset, vb.byteLength / 2)
    const ib = bytesOf(o.i)
    const idx = o.w === 4 ? new Uint32Array(ib.buffer, ib.byteOffset, ib.byteLength / 4) : new Uint16Array(ib.buffer, ib.byteOffset, ib.byteLength / 2)
    const tris: number[] = []
    for (const v of idx) {
      for (let a = 0; a < 3; a++) {
        const lo = o.lo[a] ?? 0
        const hi = o.hi[a] ?? 0
        tris.push(((q[v * 3 + a] ?? 0) + 32768) / 65535 * (hi - lo) + lo)
      }
    }
    return { name: o.n, color: o.c, open: o.o ?? true, tris }
  })
  return { step: doc.step, label: doc.label, tris: doc.tris, objects }
}

const fileName = (p: string) => p.split('/').pop()?.replace(/\.json$/, '') ?? p

// what Claude sends to the feed page after a step: files to upload, then database writes
async function feedInstructions($: $, n: number, stdout: string, feedFrames: string[]) {
  const assets = [...stdout.matchAll(/^BSTEP_ASSET (.+)$/gm)].map(m => m[1]!.trim())
  const gltf = /^BSTEP_GLTF (.+)$/m.exec(stdout)?.[1]?.trim()
  const sky = /^BSTEP_SKY (.+)$/m.exec(stdout)?.[1]?.trim()
  const render = /^BSTEP_RENDER (.+?)\t(.*)$/m.exec(stdout)
  const frameset = /^BSTEP_FRAMESET (.+)$/m.exec(stdout)?.[1]?.trim()
  const warns = [...stdout.matchAll(/^BSTEP_WARN (.+)$/gm)].map(m => m[1])
  const baked = /^BSTEP_BAKED (.+)$/m.exec(stdout)?.[1]
  const out: string[] = ['', '--- Send to the feed now ---']
  const uploads = [...assets, ...(render ? [render[1]!] : [])]
  if (uploads.length) {
    out.push(`1. Upload to the feed artifact (Artifact publish, url = the feed, asset: true, file_paths up to 25 per call): ${uploads.join(' ')}`)
    out.push('   Each .js file is a part of a packed file: its key is in the file (window.BLD["<key>/<part>"]). ' +
      'Write one "files" doc per key: {run, key, urls: [url of part 0, part 1, ...]}, doc id = key with "/" replaced by "_".')
  }
  // frames in batches under the 1 MiB / 50 writes limits
  const batches: string[][] = [[]]
  let size = 0
  for (const p of feedFrames) {
    let bytes = 0
    try {
      bytes = (await $.fs.stat(p)).size
    } catch {}
    const last = batches[batches.length - 1]!
    if (last.length >= 45 || size + bytes > 900_000) {
      batches.push([])
      size = 0
    }
    batches[batches.length - 1]!.push(p)
    size += bytes
  }
  const writes = batches.filter(b => b.length).map(b => b.map(p => `frames/${fileName(p)} <- ${p}`).join(', '))
  out.push(`${uploads.length ? 2 : 1}. One ArtifactData batch (set; file_path where a file is named):`)
  if (frameset) out.push(`   framesets/s${n} <- ${frameset}  (this step's ${feedFrames.length} live frames, packed in the uploaded frames_s${String(n).padStart(2, '0')} file)`)
  else writes.forEach((w, k) => out.push(`   frames batch ${k + 1}: ${w}`))
  if (gltf) out.push(`   also: gltf/s${n} <- ${gltf}`)
  if (sky) out.push(`   also: sky/s${n} <- ${sky}`)
  if (render) out.push(`   also: renders/s${n} = {run, step: ${n}, url: <uploaded url of ${render[1]}>, samples, size, time} (${render[2]!.split('\t').join(', ')})`)
  out.push('   and one chat/mNNNN note {seq, at, text} saying what you built.')
  if (baked) out.push(`Baked textures: ${baked}`)
  if (warns.length) out.push(`Warnings: ${warns.join(' | ')}`)
  return '\n' + out.join('\n')
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
      description: 'Install Blender for the Blender Live mod (one time, about a minute). Add a version, e.g. 4.2, for that one',
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
          focus: { type: 'string', description: 'Objects the feed camera looks at, e.g. "Armory*" or "NoseGear*,GearDoor*"' },
          mode: { type: 'string', enum: ['line', 'objects', 'end'], description: 'When to take live frames (default line)' },
          pace: { type: 'number', description: 'At least this many seconds between live frames' },
          render: { type: 'string', description: '"cycles", or "cycles:<samples>:<W>x<H>", to also render this step with Cycles' },
        },
        required: ['label', 'code'],
      },
    })
    await $.tool.register({
      name: 'blender_configure',
      description:
        'Change Blender Live settings for this project (kept in config.json, used by every later step). ' +
        'paths: folders to put on sys.path (your helper modules). prelude: .py files run before every step (an art kit). ' +
        'blender: path to a Blender executable to use instead of the bundled bpy (e.g. a 4.2 LTS install). ' +
        'bpy_version: which bundled bpy to use (run /blender-setup <version> first, e.g. 4.2). ' +
        'preview_tris: triangles per live frame (default 12000). pace, mode: live frame defaults. ' +
        'realtime/bake/sky: the realistic feed view (default on). bake_size: baked texture size (default 1024). ' +
        'render: "cycles" to render every step, samples, render_size [W, H].',
      isDeferred: false,
      inputSchema: {
        type: 'object',
        properties: {
          settings: { type: 'object', description: 'Settings to change, e.g. {"paths": ["/repo/kit"], "mode": "objects"}' },
        },
        required: ['settings'],
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

  on('command.run', { command: 'blender-setup' }, async ($, e) => {
    const version = e.args.trim()
    $.ui.toast(`Installing Blender${version ? ' ' + version : ''}... this takes about a minute`)
    const out = await run($, version ? ['setup', version] : ['setup'], 600_000)
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
      const env: Record<string, string> = { BSTEP_DIR: workDir, BSTEP_STREAM: '1' }
      if (e.focus) env.BSTEP_FOCUS = String(e.focus)
      if (e.mode) env.BSTEP_MODE = String(e.mode)
      if (e.pace !== undefined) env.BSTEP_PACE = String(e.pace)
      if (e.render) env.BSTEP_RENDER = String(e.render)
      const feedFrames: string[] = []
      const child = $.process.spawn({
        argv: ['sh', `${$.plugin.root}/engine/bstep`, 'step', label, '-c', code],
        env,
      })
      for await (const chunk of child) {
        pending += chunk.text
        const parts = pending.split('\n')
        pending = parts.pop() ?? ''
        for (const line of parts) {
          if (!line.startsWith('BSTEP_')) continue
          lines.push(line)
          const snap = /^BSTEP_SNAP (.+?)\t(\d+)\t([^\t]*)(?:\t(.*))?$/.exec(line)
          if (snap) {
            if (snap[4]) feedFrames.push(snap[4])
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
        (await feedInstructions($, n, stdout, feedFrames))
      return textResult(summary)
    } finally {
      await update($, busy, () => null)
    }
  })

  on('tool.call', { tool: 'mcp__blender-live__blender_configure' }, async ($, e) => {
    const settings = (e.settings ?? {}) as Record<string, unknown>
    const pairs = Object.entries(settings).map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    if (!pairs.length) return textResult('Nothing to change: pass settings, e.g. {"mode": "objects"}.', true)
    const out = await run($, ['configure', ...pairs])
    if (out.exitCode !== 0) return textResult(`Could not save the settings.\n${out.stdout}${out.stderr}`, true)
    return textResult(`Saved. Settings now: ${out.stdout.replace(/^BSTEP_CONFIG /, '').trim()}`)
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
