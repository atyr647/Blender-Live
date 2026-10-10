/** One snapshot of the scene, taken after a line of Claude's code ran. */
export type BlenderFrame = {
  path: string
  /** The frame before it, to animate from; null for the first. */
  prevPath: string | null
  line: number
  code: string
  label: string
}

/** One finished modeling step, as the pane lists it. */
export type BlenderStep = {
  /** 1-based step number. */
  n: number
  label: string
  tris: number
  /** Absolute paths of the step's picture and triangle data. */
  png: string
  json: string
  /** Every object in the scene after this step. */
  objects: string[]
  /** Objects that first appeared in this step (highlighted when shown). */
  added: string[]
  /** The live snapshots taken while this step ran, in order. */
  frames: BlenderFrame[]
}

declare module 'claude-code' {
  interface PluginState {
    'blender-live': {
      steps: BlenderStep[]
      /** The step on screen; null follows the newest one. */
      viewing: number | null
      /** Turntable angle in degrees. */
      angle: number
      /** The turntable spins until this time (ms since epoch). */
      spinUntil: number
      isWire: boolean
      /** The label of the step Blender is working on right now. */
      busy: string | null
      /** New parts glow until this time (ms since epoch). */
      flashUntil: number
      /** The streamed snapshot on screen, and when it started animating in. */
      frame: BlenderFrame | null
      frameStart: number
      /** Snapshots waiting to be played. */
      queue: BlenderFrame[]
      /** New parts build up, layer by layer, from this time (ms since epoch). */
      revealStart: number
      /** The person hid the live view above the prompt. */
      isBandHidden: boolean
      /** A message under the viewer (errors, finished files). */
      note: string | null
    }
  }
}
