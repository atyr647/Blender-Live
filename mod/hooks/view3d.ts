// A tiny software 3D renderer: turns a step's triangles into one SVG,
// flat-shaded like an N64 viewport, seen from an orbiting camera.

export type ModelObject = { name: string; color: number[]; tris: number[]; open?: boolean }
export type Model = { step: number; label: string; tris: number; objects: ModelObject[] }

export type ViewOptions = {
  /** Turntable angle, degrees. */
  angle: number
  isWire: boolean
  /** Object names drawn with a glowing outline. */
  glow: readonly string[]
  /** Caption drawn in the corner. */
  caption: string
  /**
   * Animates from the previous frame: parts that only moved or changed shape
   * slide there (`t` 0..1), new triangles build up from the bottom and glow.
   */
  transition?: { prev: Model | null; t: number }
  /** The line of code that made this frame, shown like a live terminal. */
  codeLine?: string
  /** The stream badge: a blinking LIVE dot, and what Blender is busy with. */
  live?: { building: string | null }
}

const W = 640
const H = 440
const SVG_LIMIT = 125_000
// the sign that marks a back face in camera space (its axes are mirrored)
const FACING = 1
const PITCH = (24 * Math.PI) / 180

type Face = { d: string; depth: number; fill: string; isGlow: boolean; area: number }

function toHex(linear: number, light: number): string {
  // Blender colours are linear; screens want sRGB
  const v = Math.round(Math.min(1, Math.pow(Math.max(0, linear), 1 / 2.2) * light) * 255)
  return v.toString(16).padStart(2, '0')
}

type V3 = [number, number, number]

function bounds(model: Model) {
  let x0 = Infinity, y0 = Infinity, z0 = Infinity
  let x1 = -Infinity, y1 = -Infinity, z1 = -Infinity
  for (const o of model.objects) {
    const t = o.tris
    for (let i = 0; i + 2 < t.length; i += 3) {
      const x = t[i]!, y = t[i + 1]!, z = t[i + 2]!
      x0 = Math.min(x0, x); y0 = Math.min(y0, y); z0 = Math.min(z0, z)
      x1 = Math.max(x1, x); y1 = Math.max(y1, y); z1 = Math.max(z1, z)
    }
  }
  if (x0 === Infinity) return { center: [0, 0, 0.5] as V3, radius: 1, floor: 0 }
  const center: V3 = [(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2]
  const radius = Math.max(0.5, Math.hypot(x1 - x0, y1 - y0, z1 - z0) / 2)
  return { center, radius, floor: Math.min(0, z0) }
}

export function renderSvg(model: Model | null, opt: ViewOptions): string {
  const { center, radius, floor } = model ? bounds(model) : { center: [0, 0, 0.5] as V3, radius: 1, floor: 0 }
  const yaw = (opt.angle * Math.PI) / 180
  const cy = Math.cos(yaw)
  const sy = Math.sin(yaw)
  const cp = Math.cos(PITCH)
  const sp = Math.sin(PITCH)
  const dist = radius * 3.6
  const focal = (H * 0.42 * dist) / radius

  // world -> camera space: [right, up, depth]
  const view = (x: number, y: number, z: number): V3 => {
    const dx = x - center[0]
    const dy = y - center[1]
    const dz = z - center[2]
    const rx = dx * cy - dy * sy
    const ry = dx * sy + dy * cy
    return [rx, dz * cp + ry * sp, ry * cp - dz * sp]
  }
  const project = (v: V3): [number, number] => {
    const s = focal / (dist + v[2])
    return [W / 2 + v[0] * s, H / 2 + 20 - v[1] * s]
  }

  // match each object to the previous frame: same shape -> slide, else new triangles build up
  const t = Math.max(0, Math.min(1, opt.transition?.t ?? 1))
  const ease = t * t * (3 - 2 * t)
  const prevEntries = new Map<string, number[]>()
  const prevKeys = new Set<string>()
  const prevNames = new Set<string>()
  const key3 = (a: number[], i: number) =>
    `${a[i]!.toFixed(3)},${a[i + 1]!.toFixed(3)},${a[i + 2]!.toFixed(3)}`
  if (opt.transition?.prev && t < 1) {
    const seen = new Map<string, number>()
    for (const o of opt.transition.prev.objects) {
      const k = seen.get(o.name) ?? 0
      seen.set(o.name, k + 1)
      prevEntries.set(`${o.name}#${k}`, o.tris)
      prevNames.add(o.name)
      for (let i = 0; i + 8 < o.tris.length; i += 9) {
        prevKeys.add(`${o.name}|${key3(o.tris, i)}|${key3(o.tris, i + 3)}|${key3(o.tris, i + 6)}`)
      }
    }
  }
  const animating = prevNames.size > 0 || (opt.transition?.prev === null && t < 1)
  // new triangles appear lowest first; collect their heights to find the cut
  const isNewTri: boolean[][] = []
  const lows: number[] = []
  const seenCur = new Map<string, number>()
  const slideFrom: (number[] | undefined)[] = []
  for (const o of model?.objects ?? []) {
    const k = seenCur.get(o.name) ?? 0
    seenCur.set(o.name, k + 1)
    const before = prevEntries.get(`${o.name}#${k}`)
    const slides = animating && before !== undefined && before.length === o.tris.length
    slideFrom.push(slides ? before : undefined)
    const flags: boolean[] = []
    for (let i = 0; i + 8 < o.tris.length; i += 9) {
      const isNew =
        animating && !slides &&
        !prevKeys.has(`${o.name}|${key3(o.tris, i)}|${key3(o.tris, i + 3)}|${key3(o.tris, i + 6)}`)
      flags.push(isNew)
      if (isNew) lows.push(Math.min(o.tris[i + 2]!, o.tris[i + 5]!, o.tris[i + 8]!))
    }
    isNewTri.push(flags)
  }
  lows.sort((p, q) => p - q)
  const shownNew = Math.floor(lows.length * ease)
  const cutZ = shownNew === 0 ? -Infinity : lows[shownNew - 1]!
  const edgeZ = lows[Math.max(0, Math.floor(shownNew * 0.8) - 1)] ?? -Infinity

  const faces: Face[] = []
  const glow = new Set(opt.glow)
  let total = 0
  let objIndex = -1
  for (const o of model?.objects ?? []) {
    objIndex++
    const isGlow = glow.has(o.name)
    const flags = isNewTri[objIndex] ?? []
    const from = slideFrom[objIndex]
    const t = from
      ? o.tris.map((v, i) => from[i]! + (v - from[i]!) * ease)
      : o.tris
    const [cr = 0.7, cg = 0.7, cb = 0.7] = o.color
    for (let i = 0; i + 8 < t.length; i += 9) {
      total++
      let isEdge = false
      if (flags[i / 9]) {
        const low = Math.min(t[i + 2]!, t[i + 5]!, t[i + 8]!)
        if (low > cutZ) continue
        isEdge = low >= edgeZ || ease < 1
      }
      const a = view(t[i]!, t[i + 1]!, t[i + 2]!)
      const b = view(t[i + 3]!, t[i + 4]!, t[i + 5]!)
      const c = view(t[i + 6]!, t[i + 7]!, t[i + 8]!)
      // flat shading from the face normal; two-sided so open meshes read well
      const ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2]
      const vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2]
      const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx
      // closed meshes: skip faces turned away from the camera (no overlap glitches)
      const toCam = -a[0] * nx - a[1] * ny + (-dist - a[2]) * nz
      if (!o.open && toCam * FACING > 0) continue
      const len = Math.hypot(nx, ny, nz) || 1
      const facing = Math.abs((nx * -0.45 + ny * 0.75 + nz * -0.5) / len)
      const light = 0.38 + 0.72 * facing
      const fill = '#' + toHex(cr, light) + toHex(cg, light) + toHex(cb, light)
      const pa = project(a), pb = project(b), pc = project(c)
      const area = Math.abs((pb[0] - pa[0]) * (pc[1] - pa[1]) - (pc[0] - pa[0]) * (pb[1] - pa[1]))
      const r = (n: number) => Math.round(n * 10) / 10
      faces.push({
        d: `M${r(pa[0])} ${r(pa[1])}L${r(pb[0])} ${r(pb[1])}L${r(pc[0])} ${r(pc[1])}Z`,
        depth: (a[2] + b[2] + c[2]) / 3,
        fill: isEdge ? '#fff3b0' : fill,
        isGlow: isGlow || isEdge,
        area,
      })
    }
  }

  // ground grid and a soft shadow
  const grid: string[] = []
  const half = radius * 1.6
  const steps = 8
  for (let i = 0; i <= steps; i++) {
    const t = -half + (2 * half * i) / steps
    const lines: [number, number, number, number][] = [
      [t, -half, t, half],
      [-half, t, half, t],
    ]
    for (const [px, py, qx, qy] of lines) {
      const s = project(view(center[0] + px, center[1] + py, floor))
      const e = project(view(center[0] + qx, center[1] + qy, floor))
      grid.push(`M${s[0].toFixed(1)} ${s[1].toFixed(1)}L${e[0].toFixed(1)} ${e[1].toFixed(1)}`)
    }
  }
  const shadow = project(view(center[0], center[1], floor))
  const shadowR = (focal / dist) * radius * 0.85

  faces.sort((p, q) => q.depth - p.depth)
  const stroke = opt.isWire ? 'stroke="#0d0f14" stroke-width=".7"' : ''
  const head =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">` +
    `<defs><radialGradient id="bg" cx="50%" cy="35%" r="75%"><stop offset="0" stop-color="#3a4150"/>` +
    `<stop offset="1" stop-color="#14161c"/></radialGradient>` +
    `<radialGradient id="sh"><stop offset="0" stop-color="#000" stop-opacity=".45"/>` +
    `<stop offset="1" stop-color="#000" stop-opacity="0"/></radialGradient>` +
    `<style>.g{stroke:#ffd84a;stroke-width:1.6;stroke-linejoin:round;animation:p 1s ease-in-out infinite alternate}` +
    `@keyframes p{from{stroke-opacity:1}to{stroke-opacity:.25}}` +
    `.blink{animation:b 1s steps(2,start) infinite}@keyframes b{to{visibility:hidden}}</style></defs>` +
    `<rect width="${W}" height="${H}" fill="url(#bg)"/>` +
    `<path d="${grid.join('')}" stroke="#ffffff" stroke-opacity=".08" fill="none"/>` +
    `<ellipse cx="${shadow[0].toFixed(1)}" cy="${shadow[1].toFixed(1)}" rx="${shadowR.toFixed(1)}" ` +
    `ry="${(shadowR * sp).toFixed(1)}" fill="url(#sh)"/>`
  const badge = opt.live
    ? `<circle class="blink" cx="22" cy="22" r="6" fill="#ff3b4a"/>` +
      `<text x="34" y="27" font-family="monospace" font-size="14" font-weight="bold" fill="#ff6b77">LIVE</text>` +
      (opt.live.building
        ? `<text class="blink" x="${W - 14}" y="27" text-anchor="end" font-family="monospace" font-size="14" ` +
          `fill="#ffd84a">building: ${escapeXml(opt.live.building.slice(0, 40))}</text>`
        : '')
    : ''
  const code = opt.codeLine
    ? `<rect x="0" y="${H - 58}" width="${W}" height="22" fill="#000" fill-opacity=".45"/>` +
      `<text x="14" y="${H - 42}" font-family="monospace" font-size="13" fill="#7dff9b">&#8250; ` +
      `${escapeXml(opt.codeLine.slice(0, 80))}</text>`
    : ''
  const foot =
    code +
    badge +
    `<text x="14" y="${H - 14}" font-family="monospace" font-size="14" fill="#cfd6e4">` +
    `${escapeXml(opt.caption)}</text>` +
    `<text x="${W - 14}" y="${H - 14}" text-anchor="end" font-family="monospace" font-size="14" ` +
    `fill="#8fc1ff">${total} tris</text></svg>`

  // keep inside the SVG size limit: drop the smallest faces first
  const tag = (f: Face) =>
    `<path d="${f.d}" fill="${f.fill}" ` +
    (f.isGlow ? 'class="g"' : opt.isWire ? stroke : `stroke="${f.fill}" stroke-width=".5"`) +
    '/>'
  const budget = SVG_LIMIT - head.length - foot.length
  const tags = faces.map(tag)
  let size = tags.reduce((n, t) => n + t.length, 0)
  const keep = faces.map(() => true)
  if (size > budget) {
    const order = faces.map((_, i) => i).sort((p, q) => faces[p]!.area - faces[q]!.area)
    for (const i of order) {
      if (size <= budget) break
      keep[i] = false
      size -= tags[i]!.length
    }
  }
  const body = tags.filter((_, i) => keep[i]).join('')
  return head + body + foot
}

function escapeXml(s: string): string {
  return s.replace(/[<>&"]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' })[c] ?? c)
}
