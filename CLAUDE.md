# Blender Live: notes for Claude

The user (atyr647) is a beginner hobbyist making N64-style homebrew. They watch you model through the live feed page, so build in small visible steps and keep explanations plain.

## Set up in a fresh session

1. Install Blender (the `bpy` Python module, about a minute):
   `sh mod/engine/bstep setup` (prints `BSTEP_READY`; it also installs a software OpenGL driver with apt when needed).
2. Load the mod. `.claude/settings.json` enables it from this repo's marketplace. If the `mcp__blender-live__blender_*` tools are not in your tool list, either load the `plugin-authoring` skill and copy `mod/` into the session's mods folder it names, or run the engine directly with Bash (`sh mod/engine/bstep step "Label" file.py`, with `BSTEP_STREAM=1` to get the per-line `BSTEP_SNAP` lines).
3. The feed artifact is https://claude.ai/artifact/SbpoaFvP4JzcAsKurHHSGa. Its source is `feed/blender-live.html`; republish changes with the Artifact tool and that `url` (read it first). It declares `db`, `comments` and `assets`.
4. For a big project, call `blender_configure` once (paths, prelude, blender or bpy_version, mode). See README "Big projects".

## Sending a model to the feed

Every `blender_step` result ends with "Send to the feed now": do it right away, in this order.

1. **Upload** the listed files with the Artifact tool: `url` = the feed, `asset: true`, `file_paths` (up to 25 per call). They are `.js` packs (frames, glTF, sky) and Cycles `.png` renders.
2. **One ArtifactData batch** with:
   - `files/<key with / as _>` = `{run, key, urls: [...]}` for every uploaded `.js` key (the key is inside the file: `window.BLD["<key>/<part>"]`; a key with several parts lists their urls in order);
   - `framesets/<run>_s<step>`, `gltf/<run>_s<step>`, `sky/<run>_s<step>` from the files the result names (`file_path`); doc ids start with the run id so an earlier model's docs never clash;
   - `renders/<run>_s<step>` = `{run, step, url, samples, size, time}` when there is a render;
   - one `chat/mNNNN` note `{seq, at, text}`, continuing `seq` from the highest one there.

After `blender_reset`, read the new run id from `<workspace>/run.txt` and `set` `live/status` = `{run}` (pin `if_version` to the version you read). The page clears and plays the new run from its first frame.

If the user says the feed looks wrong, read `diag/last`: the page writes loading errors there.

The database holds 100 MB in all, so frames go in the file store (one pack per step) and the database only holds small index docs.

## Notes from the user

Notes submitted from the feed arrive as an artifact comment sent to Claude, e.g.
`Notes from the Blender Live feed (frame 17, step "Eyes"): 1. star: Make the points a bit sharper`.
Each note names the object it was pinned to. Make the changes as new `blender_step`s so the user watches them happen, then answer in the comment thread.

## Modeling tips that read well on the feed

- One visible operation per line: add a primitive, then extrude, translate, scale or inset with `bmesh` in edit mode. Each changing line becomes a frame.
- Give every object a name and `paint(obj, mat(name, (r, g, b)))`. Names starting with `_` are hidden helpers.
- Check your work before moving on: each step renders `<workspace>/frames/step_NN.png`, which you can Read. Fix mistakes with `blender_undo` or a follow-up step.
- Big scenes: use `mode: "objects"` or a `pace`, `focus` on the part being built, and real node materials (they come through in the realistic view; procedural textures are baked automatically).
