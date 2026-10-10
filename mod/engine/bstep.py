"""bstep - build a Blender model one step at a time and stream it to the live feed.

Usage (run with a Python that has the `bpy` module, or inside Blender with
`blender -b --python-exit-code 1 -P bstep.py -- <command> ...`):
  bstep.py reset                     start a fresh, empty scene
  bstep.py step "Label" file.py      run file.py on the scene, save, snapshot
  bstep.py step "Label" -c "code"    same, with inline code
  bstep.py undo                      drop the last step, restore the one before
  bstep.py movie                     make timelapse.gif + contact_sheet.png
  bstep.py export                    write model.glb (for other tools/engines)

The workspace folder is $BSTEP_DIR (default: ./blender-live). Settings live in
its config.json (see `bstep configure`): preview_tris, pace, mode, paths,
prelude, realtime, bake, bake_size, sky, render, samples, render_size.

Per step, these environment variables override the settings:
  BSTEP_STREAM=1     print a BSTEP_SNAP line per live frame
  BSTEP_FOCUS=...    objects the camera looks at (globs, comma separated)
  BSTEP_PACE=1.5     at most one live frame per this many seconds
  BSTEP_MODE=...     line (a frame per changing line), objects (a frame when
                     objects are added or removed) or end (one frame per step)
  BSTEP_RENDER=cycles[:samples[:WxH]]   also render this step with Cycles

Snippets run with `bpy`, `bmesh`, `Vector`, `math`, `np` and these helpers:
  mat(name, (r, g, b))   get/create a flat-coloured material
  paint(obj, material)   assign a material to an object
  snap("what")           take a live frame now (also inside your own functions)
  focus("Armory*")       point the feed camera at these objects from here on
Objects whose names start with "_" are helpers and are not drawn or counted.
"""
import ast
import json
import math
import os
import shutil
import sys
import time
import traceback

import bpy  # must come first: it provides bmesh and mathutils
import bmesh
import numpy as np
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import feed  # noqa: E402
import realtime  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # Blender's own Python has no Pillow: captions and movies are skipped
    Image = None

ROOT = os.path.abspath(os.environ.get("BSTEP_DIR", "blender-live"))
BLEND = os.path.join(ROOT, "scene.blend")
FRAMES = os.path.join(ROOT, "frames")
LIVE = os.path.join(FRAMES, "live")
GLTF = os.path.join(FRAMES, "gltf")
HISTORY = os.path.join(ROOT, "history")
W, H = 640, 480

DEFAULTS = {
    "preview_tris": 12000,   # triangles per live frame, the whole scene together
    "pace": 0.0,             # seconds between live frames, at least
    "mode": "line",          # line | objects | end
    "paths": [],             # folders added to sys.path (your own helper modules)
    "prelude": [],           # .py files run before every step (your art kit)
    "realtime": True,        # glTF models + sky for the realistic feed view
    "bake": True,            # bake procedural textures into images for the glTF
    "bake_size": 1024,
    "sky": True,
    "render": "",            # "cycles" renders every step with Cycles
    "samples": 64,
    "render_size": [1280, 720],
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(os.path.join(ROOT, "config.json")) as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


CONFIG = load_config()


def mat(name, rgb):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.diffuse_color = (*rgb, 1.0)
    if m.use_nodes and m.node_tree:
        p = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if p is not None:
            p.inputs["Base Color"].default_value = (*rgb, 1.0)
    return m


def paint(obj, material):
    obj.data.materials.clear()
    obj.data.materials.append(material)


def model_objects():
    return [o for o in bpy.context.scene.objects
            if o.type in feed.DRAWN_TYPES and not o.name.startswith("_") and o.visible_get()]


def current_run():
    try:
        with open(os.path.join(ROOT, "run.txt")) as f:
            return f.read().strip()
    except OSError:
        return "run0"


def sync_edit_mode():
    for o in model_objects():
        if o.mode == "EDIT":
            o.update_from_editmode()  # edit-mode changes live in a bmesh until synced
    bpy.context.view_layer.update()


# ---------------------------------------------------------------- cameras and pictures

def frame_camera(scene, box=None):
    """A 3/4 camera on the focus box, or on everything."""
    if box:
        lo, hi = Vector(box["lo"]), Vector(box["hi"])
    else:
        pts = [o.matrix_world @ Vector(c) for o in model_objects() for c in o.bound_box]
        if not pts:
            pts = [Vector((-1, -1, 0)), Vector((1, 1, 1))]
        lo = Vector([min(p[i] for p in pts) for i in range(3)])
        hi = Vector([max(p[i] for p in pts) for i in range(3)])
    center, radius = (lo + hi) / 2, max((hi - lo).length / 2, 0.5)
    cam = bpy.data.objects.get("_cam")
    if cam is None:
        cam = bpy.data.objects.new("_cam", bpy.data.cameras.new("_cam"))
        scene.collection.objects.link(cam)
    scene.camera = cam
    direction = Vector((1.0, -1.25, 0.8)).normalized()
    cam.location = center + direction * radius * 4.6
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam.data.lens = 50
    cam.data.clip_start = max(0.01, radius * 0.01)
    cam.data.clip_end = max(100.0, radius * 40)


def show_real_colours():
    """Workbench draws the viewport colour; set it from each material's real look."""
    for m in bpy.data.materials:
        look = feed.material_look(m)
        m.diffuse_color = (*look["color"], 1.0)


def setup_workbench(scene):
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x, scene.render.resolution_y = W, H
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    sh = scene.display.shading
    sh.light = "STUDIO"
    sh.color_type = "MATERIAL"
    sh.show_cavity = True
    sh.cavity_type = "BOTH"
    sh.show_object_outline = True
    sh.show_shadows = True
    sh.shadow_intensity = 0.35
    scene.display.render_aa = "8"
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.color = (0.16, 0.17, 0.2)


def caption(path, step, label, tris):
    if Image is None:
        return
    img = Image.open(path).convert("RGB")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 18)
        small = ImageFont.truetype("DejaVuSans.ttf", 14)
    except OSError:
        font = small = ImageFont.load_default()
    d.rectangle([0, H - 40, W, H], fill=(20, 20, 26))
    d.text((12, H - 31), f"Step {step}: {label}"[:52], font=font, fill=(240, 240, 240))
    d.text((W - 130, H - 28), f"{tris:,} tris", font=small, fill=(150, 200, 255))
    img.save(path)


def render_cycles(scene, n, box, spec):
    """BSTEP_RENDER=cycles[:samples[:WxH]]: a realistic picture of this step."""
    parts = spec.split(":")
    samples = int(parts[1]) if len(parts) > 1 and parts[1] else int(CONFIG["samples"])
    w, h = CONFIG["render_size"]
    if len(parts) > 2 and "x" in parts[2]:
        w, h = (int(v) for v in parts[2].split("x"))
    if not any(o.type == "LIGHT" for o in scene.objects) and (scene.world is None or not scene.world.use_nodes):
        sun = bpy.data.objects.new("_sun", bpy.data.lights.new("_sun", "SUN"))
        sun.data.energy = 3.0
        sun.rotation_euler = (math.radians(50), 0, math.radians(35))
        scene.collection.objects.link(sun)
    for o in scene.objects:  # helpers (the grey floor) stay out of the picture
        if o.name.startswith("_") and o.type in feed.DRAWN_TYPES:
            o.hide_render = True
    frame_camera(scene, box)
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    if hasattr(scene.cycles, "use_denoising"):
        scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y = w, h
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    out = os.path.join(FRAMES, f"step_{n:02d}_cycles.png")
    scene.render.filepath = out
    t = time.time()
    bpy.ops.render.render(write_still=True)
    print(f"BSTEP_RENDER {out}\t{samples}\t{w}x{h}\t{time.time() - t:.0f}s", flush=True)


# ---------------------------------------------------------------- steps

def step_count():
    if not os.path.isdir(FRAMES):
        return 0
    return len([f for f in os.listdir(FRAMES) if f.startswith("step_") and f.endswith(".json")])


def pack_frames(n, run, docs):
    """All of a step's live frames as one file for the feed (keeps its database small)."""
    if not docs:
        return
    key = f"{run}/f{n}"
    files = realtime.wrap_js(("[" + ",".join(docs) + "]").encode(), os.path.join(LIVE, f"frames_s{n:02d}"), key)
    seqs = [json.loads(d)["seq"] for d in docs]
    doc = {"run": run, "step": n, "key": key, "parts": len(files), "count": len(docs), "first": min(seqs), "last": max(seqs)}
    path = os.path.join(LIVE, f"frames_s{n:02d}.json")
    with open(path, "w") as f:
        json.dump(doc, f)
    for p in files:
        print(f"BSTEP_ASSET {p}", flush=True)
    print(f"BSTEP_FRAMESET {path}", flush=True)


def snapshot(n, label, box, writer, docs=()):
    """End of a step: save, then picture, preview, glTF, sky, render."""
    scene = bpy.context.scene
    sync_edit_mode()
    os.makedirs(FRAMES, exist_ok=True)
    os.makedirs(HISTORY, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=BLEND)
    shutil.copy(BLEND, os.path.join(HISTORY, f"step_{n:02d}.blend"))
    # from here on the scene is only changed for pictures and exports: nothing is saved again

    dg = bpy.context.evaluated_depsgraph_get()
    found = feed.collect(dg)
    meta = {"run": current_run(), "step": n, "label": label, "focus": box}
    _, full = writer.frames(found, meta, True)
    if os.path.isdir(LIVE):
        writer.save(os.path.join(LIVE, "preview_cache.json"), writer.used)
    with open(os.path.join(FRAMES, f"step_{n:02d}.json"), "w") as f:
        f.write(full)
    tris = json.loads(full)["tris"]
    pack_frames(n, meta["run"], list(docs))
    names = ", ".join(sorted({o.name for o in model_objects()}))

    if CONFIG["realtime"]:
        try:
            manifest, files = realtime.export_changed(
                os.path.join(GLTF, "state.json"), GLTF, meta["run"], n,
                os.path.join(ROOT, "baked"), bake=CONFIG["bake"], bake_size=int(CONFIG["bake_size"]),
                log=lambda s: print(s, flush=True))
            mpath = os.path.join(GLTF, f"manifest_s{n:02d}.json")
            with open(mpath, "w") as f:
                json.dump(manifest, f, separators=(",", ":"))
            for p in files:
                print(f"BSTEP_ASSET {p}", flush=True)
            print(f"BSTEP_GLTF {mpath}", flush=True)
        except Exception as err:  # the realistic view is a bonus: never fail the step for it
            print(f"BSTEP_WARN glTF export failed: {err}", flush=True)
        if CONFIG["sky"]:
            try:
                sig = realtime.world_signature(scene)
                sky_state = os.path.join(GLTF, "sky.json")
                old = {}
                if os.path.exists(sky_state):
                    with open(sky_state) as f:
                        old = json.load(f)
                if old.get("sig") != sig or old.get("run") != meta["run"]:
                    key = f"{meta['run']}/sky{n}"
                    files = realtime.render_sky(os.path.join(GLTF, f"sky_s{n:02d}"), key,
                                                log=lambda s: print(s, flush=True))
                    if files:
                        vs = scene.view_settings
                        doc = {"run": meta["run"], "step": n, "key": key, "parts": len(files), "sig": sig,
                               "exposure": round(float(vs.exposure), 3), "view": vs.view_transform}
                        with open(sky_state, "w") as f:
                            json.dump(doc, f)
                        spath = os.path.join(GLTF, f"sky_s{n:02d}.json")
                        with open(spath, "w") as f:
                            json.dump(doc, f)
                        for p in files:
                            print(f"BSTEP_ASSET {p}", flush=True)
                        print(f"BSTEP_SKY {spath}", flush=True)
            except Exception as err:
                print(f"BSTEP_WARN sky failed: {err}", flush=True)

    # the quick picture (Workbench, real material colours)
    show_real_colours()
    setup_workbench(scene)
    frame_camera(scene, box)
    png = os.path.join(FRAMES, f"step_{n:02d}.png")
    scene.render.filepath = png
    bpy.ops.render.render(write_still=True)
    caption(png, n, label, tris)

    spec = os.environ.get("BSTEP_RENDER") or CONFIG["render"]
    if spec.startswith("cycles"):
        try:
            render_cycles(scene, n, box, spec)
        except Exception as err:
            print(f"BSTEP_WARN Cycles render failed: {err}", flush=True)
    print(f"BSTEP_OK step={n} tris={tris} png={png}", flush=True)
    print(f"BSTEP_OBJECTS {names[:4000]}", flush=True)


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for d in (FRAMES, HISTORY):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)
    bpy.ops.mesh.primitive_plane_add(size=6)
    floor = bpy.context.object
    floor.name = "_floor"
    paint(floor, mat("_floor", (0.32, 0.34, 0.38)))
    bpy.ops.wm.save_as_mainfile(filepath=BLEND)
    with open(os.path.join(ROOT, "run.txt"), "w") as f:
        f.write(f"run{int(time.time())}")
    print("BSTEP_RESET")


class _Instrument(ast.NodeTransformer):
    """After each top-level statement, and each statement directly inside a
    top-level loop, call __snap__(line) so the viewer sees the model change."""

    def _wrap(self, body):
        out = []
        for stmt in body:
            out.append(stmt)
            if isinstance(stmt, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
                continue
            call = ast.Expr(ast.Call(ast.Name("__snap__", ast.Load()),
                                     [ast.Constant(stmt.lineno)], []))
            out.append(ast.copy_location(call, stmt))
        return out

    def visit_Module(self, node):
        for stmt in node.body:
            if isinstance(stmt, (ast.For, ast.While)):
                stmt.body = self._wrap(stmt.body)
        node.body = self._wrap(node.body)
        return node


class Snapper:
    """Takes live frames at the pace and in the mode asked for."""

    def __init__(self, label, code, step_no, writer, streaming):
        self.label, self.step_no, self.writer = label, step_no, writer
        self.lines = code.splitlines()
        self.streaming = streaming
        self.mode = os.environ.get("BSTEP_MODE") or CONFIG["mode"]
        self.pace = float(os.environ.get("BSTEP_PACE") or CONFIG["pace"] or 0)
        self.focus_pattern = os.environ.get("BSTEP_FOCUS", "")
        self.box = None
        self.last_at = 0.0
        self.cost = 0.0
        self.last_count = -1
        self.first = True
        self.pending = None
        os.makedirs(LIVE, exist_ok=True)
        self.count = len([f for f in os.listdir(LIVE) if f.startswith("snap_")])
        self.docs = []  # this step's feed frames, packed into one file at the end

    def due(self, forced):
        if forced:
            return True
        if self.mode == "end":
            return False
        # cheap frames: every line; expensive ones: never more than ~40% of the time on frames
        wait = self.pace if self.cost < 0.25 else max(self.pace, self.cost * 1.5)
        return time.time() - self.last_at >= wait

    def line(self, lineno):
        src = self.lines[lineno - 1].strip() if 0 < lineno <= len(self.lines) else ""
        count = len(bpy.context.scene.objects)
        new_part = count != self.last_count
        if self.mode == "objects" and not new_part:
            return
        # a part added or removed always gets its frame, unless frames have become really slow
        self.take(lineno, src, forced=new_part and self.mode != "end" and self.cost < 2.0)

    def take(self, lineno, src, forced):
        if not self.streaming:
            return
        if not self.due(forced):
            self.pending = (lineno, src)  # remember it: the end-of-step frame shows it
            return
        t = time.time()
        sync_edit_mode()
        dg = bpy.context.evaluated_depsgraph_get()
        found = feed.collect(dg)
        if self.focus_pattern:
            self.box = feed.focus_box(self.focus_pattern, found) or self.box
        meta = {"run": current_run(), "seq": self.count, "step": self.step_no, "label": self.label,
                "line": lineno, "code": src[:90], "focus": self.box}
        delta, full = self.writer.frames(found, meta, self.first)
        if not self.first:  # the first frame pays for evaluating the whole opened file once: don't pace on it
            self.cost = time.time() - t
        self.last_at = time.time()
        self.pending = None
        self.last_count = len(bpy.context.scene.objects)
        if delta is None:
            return
        self.first = False
        feed_path = os.path.join(LIVE, f"snap_{self.count:05d}.json")
        full_path = os.path.join(LIVE, f"full_{self.count:05d}.json")
        with open(feed_path, "w") as f:
            f.write(delta)
        self.docs.append(delta)
        with open(full_path, "w") as f:
            f.write(full)
        self.count += 1
        print(f"BSTEP_SNAP {full_path}\t{lineno}\t{src[:90]}\t{feed_path}", flush=True)

    def finish(self):
        lineno, src = self.pending or (0, "step finished")
        self.take(lineno, src, forced=True)


def step(label, code):
    if os.path.exists(BLEND):
        bpy.ops.wm.open_mainfile(filepath=BLEND)
    else:
        reset()
    for p in CONFIG["paths"]:
        if p not in sys.path:
            sys.path.insert(0, p)
    n = step_count() + 1
    writer = feed.FrameWriter(int(CONFIG["preview_tris"]))
    writer.load(os.path.join(LIVE, "preview_cache.json"))
    snapper = Snapper(label, code, n, writer, bool(os.environ.get("BSTEP_STREAM")))

    def snap(what=""):
        snapper.take(0, f"snap({what!r})" if what else "snap()", forced=True)

    def focus(pattern):
        snapper.focus_pattern = pattern
        snapper.box = None

    ns = {"bpy": bpy, "bmesh": bmesh, "Vector": Vector, "math": math, "np": np,
          "mat": mat, "paint": paint, "snap": snap, "focus": focus, "__name__": "__bstep__"}
    ns["__snap__"] = snapper.line
    try:
        for path in CONFIG["prelude"]:
            with open(path) as f:
                exec(compile(f.read(), path, "exec"), ns)  # not instrumented: helpers only
        tree = ast.fix_missing_locations(_Instrument().visit(ast.parse(code)))
        exec(compile(tree, f"<step: {label}>", "exec"), ns)
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        snapper.finish()
    except Exception:
        # nothing is saved: the scene stays as it was before this step
        print("BSTEP_ERROR", flush=True)
        for line in traceback.format_exc().splitlines():
            print(f"BSTEP_TB {line}", flush=True)
        sys.exit(2)
    box = snapper.box
    if snapper.focus_pattern and box is None:
        box = feed.focus_box(snapper.focus_pattern, feed.collect(bpy.context.evaluated_depsgraph_get()))
    snapshot(n, label, box, writer, snapper.docs)


def undo():
    n = step_count()
    if n == 0:
        sys.exit("nothing to undo")
    for name in (f"step_{n:02d}.png", f"step_{n:02d}.json", f"step_{n:02d}_cycles.png"):
        p = os.path.join(FRAMES, name)
        if os.path.exists(p):
            os.remove(p)
    last = os.path.join(HISTORY, f"step_{n:02d}.blend")
    if os.path.exists(last):
        os.remove(last)
    # the next glTF export compares against the restored scene
    state = os.path.join(GLTF, "state.json")
    if os.path.exists(state):
        os.remove(state)
    prev = os.path.join(HISTORY, f"step_{n - 1:02d}.blend")
    if n > 1 and os.path.exists(prev):
        shutil.copy(prev, BLEND)
    else:
        reset()
    print(f"BSTEP_UNDO now_at={n - 1}")


def movie():
    if Image is None:
        sys.exit("movie needs Pillow: run bstep with the Blender Live Python, not Blender's own")
    files = sorted(f for f in os.listdir(FRAMES) if f.startswith("step_") and f.endswith(".png")
                   and not f.endswith("_cycles.png"))
    imgs = [Image.open(os.path.join(FRAMES, f)).convert("RGB") for f in files]
    if not imgs:
        sys.exit("no frames yet")
    durations = [900] * (len(imgs) - 1) + [2500]
    gif = os.path.join(ROOT, "timelapse.gif")
    imgs[0].save(gif, save_all=True, append_images=imgs[1:], duration=durations, loop=0)
    cols = min(4, len(imgs))
    rows = math.ceil(len(imgs) / cols)
    tw, th = W // 2, H // 2
    sheet = Image.new("RGB", (cols * tw, rows * th), (20, 20, 26))
    for i, im in enumerate(imgs):
        sheet.paste(im.resize((tw, th)), ((i % cols) * tw, (i // cols) * th))
    sheet.save(os.path.join(ROOT, "contact_sheet.png"))
    print(f"BSTEP_MOVIE gif={gif} sheet={os.path.join(ROOT, 'contact_sheet.png')}")


def export():
    bpy.ops.wm.open_mainfile(filepath=BLEND)
    for o in bpy.context.scene.objects:
        o.select_set(o.type in feed.DRAWN_TYPES and not o.name.startswith("_"))
    out = os.path.join(ROOT, "model.glb")
    bpy.ops.export_scene.gltf(filepath=out, use_selection=True)
    print(f"BSTEP_EXPORT glb={out} blend={BLEND}")


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    os.makedirs(ROOT, exist_ok=True)
    if args[:1] == ["reset"]:
        reset()
    elif args[:1] == ["undo"]:
        undo()
    elif args[:1] == ["movie"]:
        movie()
    elif args[:1] == ["export"]:
        export()
    elif args[:1] == ["step"] and len(args) >= 3:
        code = args[3] if args[2] == "-c" else open(args[2]).read()
        step(args[1], code)
    else:
        sys.exit(__doc__)
