# Blender Live: notes for Claude

The user (atyr647) is a beginner hobbyist making N64-style homebrew. They watch you model through the live feed page, so build in small visible steps and keep explanations plain.

## Set up in a fresh session

1. Install Blender (the `bpy` Python module, about a minute):
   `sh mod/engine/bstep setup` (prints `BSTEP_READY`; it also installs a software OpenGL driver with apt when needed).
2. Load the mod. `.claude/settings.json` enables it from this repo's marketplace. If the `mcp__blender-live__blender_*` tools are not in your tool list, either load the `plugin-authoring` skill and copy `mod/` into the session's mods folder it names, or run the engine directly with Bash (`sh mod/engine/bstep step "Label" file.py`, with `BSTEP_STREAM=1` to get the per-line `BSTEP_SNAP` lines).
3. The feed artifact is https://claude.ai/artifact/SbpoaFvP4JzcAsKurHHSGa. Its source is `feed/blender-live.html`; republish changes with the Artifact tool and that `url` (read it first).

## Sending a model to the feed

The page reads three things from the artifact database (use the `ArtifactData` tool):

- `live/status` `{ run }`: which model is on air. After `blender_reset`, read the new id from `<workspace>/run.txt` and `set` this doc (pin `if_version` to the version you read). The page clears and plays the new run from its first frame.
- `frames/<snap_NNNNN>`: one doc per snapshot, written with `file_path` set to `<workspace>/frames/live/snap_NNNNN.json` (the file already holds `run`, `seq`, `label`, `line`, `code`, `tris`, `objects`). `blender_step` lists the new files at the end of its result.
- `chat/<mNNNN>` `{ seq, at, text }`: short plain-language notes about what you just built. Continue `seq` from the highest one already there.

After each `blender_step`, send that step's frames and one chat note in a single `batch` (up to 50 writes, 1 MiB). Keep frames under the 256 KiB doc limit (a few thousand triangles).

## Notes from the user

Notes submitted from the feed arrive as an artifact comment sent to Claude, e.g.
`Notes from the Blender Live feed (frame 17, step "Eyes"): 1. star: Make the points a bit sharper`.
Each note names the object it was pinned to. Make the changes as new `blender_step`s so the user watches them happen, then answer in the comment thread.

## Modeling tips that read well on the feed

- One visible operation per line: add a primitive, then extrude, translate, scale or inset with `bmesh` in edit mode. Each changing line becomes a frame.
- Give every object a name and `paint(obj, mat(name, (r, g, b)))`. Names starting with `_` are hidden helpers.
- Check your work before moving on: each step renders `<workspace>/frames/step_NN.png`, which you can Read. Fix mistakes with `blender_undo` or a follow-up step.
