# Blender Live

Watch Claude build 3D models in Blender, live. Every line of Claude's modeling code becomes a frame in a spinning 3D feed, so you see the model come together piece by piece: points pulled out, shapes extruded, new parts building up from the bottom with a gold glow.

Made for low-poly, N64-style models (a few hundred triangles, flat colors).

## What's in here

| Folder | What it is |
| --- | --- |
| `mod/` | A Claude Code mod. It gives Claude Blender tools (`blender_step`, `blender_undo`, `blender_reset`, `blender_finish`) and, in the Claude Code terminal or desktop app, a live view above the prompt. |
| `mod/engine/` | The Blender helper (`bstep`). It runs Blender without a window, saves a snapshot after every line of code, and renders a picture per step. |
| `feed/blender-live.html` | The live feed page, published as a claude.ai artifact. It works in the claude.ai web and phone apps too. |
| `examples/` | Step-by-step model scripts you can replay, like the treasure chest. |

## The live feed

Your feed: **https://claude.ai/artifact/SbpoaFvP4JzcAsKurHHSGa**

Keep it open beside the chat while Claude models. It has:

- **Play:** Pause or Play the stream, Replay build from the first frame, or jump to the Latest frame.
- **Spin:** turn the turntable on or off, flip the direction, and set the speed.
- **View:** Wireframe, the Chat panel (what Claude says while it works), and Add notes.
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

## Running the Blender helper yourself

```
mod/engine/bstep setup                     # install Blender (the bpy module) once
mod/engine/bstep reset                     # start an empty scene
mod/engine/bstep step "Chest body" examples/treasure-chest/01_body.py
mod/engine/bstep movie                     # timelapse.gif + contact_sheet.png
mod/engine/bstep export                    # model.glb
```

Everything lands in `./blender-live/` (or the folder in `BSTEP_DIR`): `scene.blend`, one picture and one triangle file per step, and the per-line snapshots in `frames/live/`.

## Known limits

- The mod's own panels only draw in the Claude Code terminal and desktop app. In the claude.ai web and phone apps, use the feed page.
- The feed updates once per step: Claude sends that step's frames when it finishes, and the page plays them in order. If the page's live connection drops, it checks for new frames about every 30 seconds.
- A frame is taken after each top-level line of code, and after each line directly inside a top-level loop. Helper functions show up as one change.
