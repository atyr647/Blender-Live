# Blender Live

Watch Claude build 3D models in Blender, live. Every line of Claude's modeling code becomes a frame in a spinning 3D feed, so you see the model come together piece by piece: points pulled out, shapes extruded, new parts building up from the bottom with a gold glow.

Works from tiny N64-style models to detailed, multi-million-triangle ones:

- **While Claude works**, the feed streams a simplified preview (about 12,000 triangles, made with Blender's decimate), sending only the parts that changed.
- **When a step finishes**, the feed swaps in the full-detail model as a glTF with its real materials: clearcoat pearl, polished metal, glass that bends light, glowing parts. Procedural patterns (noise, Voronoi, bricks...) are baked into image textures first, so they show up in real time and in a game engine.
- **Lighting** comes from the scene's own Blender world (an HDRI or sky texture), with the sun turned into a real light for soft shadows, a glow around bright parts, and the same colour handling as Blender (AgX, exposure).
- **Cycles renders** can be attached to any step and opened from the feed.

## What's in here

| Folder | What it is |
| --- | --- |
| `mod/` | A Claude Code mod. It gives Claude Blender tools (`blender_step`, `blender_configure`, `blender_undo`, `blender_reset`, `blender_finish`) and, in the Claude Code terminal or desktop app, a live view above the prompt. |
| `mod/engine/` | The Blender helper (`bstep`). It runs Blender without a window, streams a preview frame after every line of code, and at the end of each step exports the glTF model (with baked textures), the sky, a quick picture and, if asked, a Cycles render. |
| `feed/blender-live.html` | The live feed page, published as a claude.ai artifact. It works in the claude.ai web and phone apps too. |
| `examples/` | Step-by-step model scripts you can replay, like the treasure chest. |

## The live feed

Your feed: **https://claude.ai/artifact/SbpoaFvP4JzcAsKurHHSGa**

Keep it open beside the chat while Claude models. It has:

- **Play:** Pause or Play the stream, Replay build from the first frame, or jump to the Latest frame.
- **Spin:** turn the turntable on or off, flip the direction, and set the speed.
- **View:** Realistic (full model with real materials once a step is done) or preview only, Best or Fast (shadows and glow off for slow phones), Sky (show the sky behind the model), Wireframe, the Chat panel (what Claude says while it works), and Add notes.
- **Cycles render:** appears at the top when the step on screen has one.
- **Add notes:** pause (or wait until the build finishes), click parts of the model to pin numbered notes, give each one a reason, then Submit to Claude. The notes arrive in Claude's session as one comment.

## Using it in a new session

### Claude Code on the web (cloud sessions)

Start a session with this repo attached and ask:

> Set up Blender Live, then build an N64 Boo ghost in the feed.

Claude reads `CLAUDE.md` in this repo, which tells it how to install Blender, load the mod, and send frames to your feed. Installing Blender takes about a minute.

### Claude Code in a terminal (on your computer)

Install the mod once:

```
/plugin install blender-live --marketplace atyr647/blender-live
```

Answer `y` to add the marketplace, then pick a scope. Then type `/blender-setup` to install Blender (about a minute, once). After that, ask Claude to build something and use `/blender` to show the live view above the prompt. On the terminal the view shows each step's rendered picture in terminals that can show images, such as kitty or Ghostty.

## Big projects

Tell Claude (or call `blender_configure`) once per project:

- `paths`: folders with your own Python modules (an art kit, gear or mount code), importable in every step.
- `prelude`: `.py` files run before every step.
- `blender`: a Blender executable to use instead of the bundled one, e.g. your 4.2 LTS install. Or `bpy_version` after `/blender-setup 4.2` (`mod/engine/bstep setup 4.2`).
- `mode`: `objects` takes a live frame only when parts are added or removed (good for scripts that make hundreds of parts in loops); `end` takes one per step. `pace` sets the minimum seconds between frames.
- `preview_tris`, `bake_size`, `render` (`"cycles"` for every step), `samples`, `render_size`.

Per step, Claude can also pass `focus` (e.g. `"Washroom*"`): the feed camera zooms there, and big or surrounding parts like the hull turn see-through. In step code, `focus("Armory*")` does the same from that line on, and `snap("door hinges")` takes a frame on demand, even inside your own helper functions.

## Running the Blender helper yourself

```
mod/engine/bstep setup                     # install Blender (the bpy module) once; `setup 4.2` for 4.2 LTS
mod/engine/bstep configure mode=objects    # project settings (see Big projects)
mod/engine/bstep reset                     # start an empty scene
mod/engine/bstep step "Chest body" examples/treasure-chest/01_body.py
mod/engine/bstep movie                     # timelapse.gif + contact_sheet.png
mod/engine/bstep export                    # model.glb
```

Everything lands in `./blender-live/` (or the folder in `BSTEP_DIR`): `scene.blend`, a picture and a preview file per step, the live frames in `frames/live/`, and the glTF models, baked textures and sky in `frames/gltf/` and `baked/`.

## Known limits

- The mod's own panels only draw in the Claude Code terminal and desktop app. In the claude.ai web and phone apps, use the feed page.
- The feed updates once per step: Claude sends that step's frames and model when it finishes, and the page plays them in order. If the page's live connection drops, it checks for new frames about every 30 seconds.
- A frame is taken after each top-level line of code, and after each line directly inside a top-level loop. Helper functions show up as one change unless they call `snap()`.
- The realistic view is a real-time approximation. Glass refraction, reflections and soft shadows are close to Cycles, but not the same; use a Cycles render for the true picture.
- Baking needs UV maps: objects without one get an automatic UV unwrap in the exported copy (your .blend is not changed).
