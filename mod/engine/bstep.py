"""bstep - build a Blender model one step at a time and snapshot every step.

Usage (run with a Python that has the `bpy` module):
  bstep.py reset                     start a fresh, empty scene
  bstep.py step "Label" file.py      run file.py on the scene, save, snapshot
  bstep.py step "Label" -c "code"    same, with inline code
  bstep.py undo                      drop the last step, restore the one before
  bstep.py movie                     make timelapse.gif + contact_sheet.png
  bstep.py export                    write model.glb (for other tools/engines)

The workspace folder is $BSTEP_DIR (default: ./blender-live). Each step leaves
frames/step_NN.png (a picture), frames/step_NN.json (the triangles, for the
live 3D pane) and history/step_NN.blend (for undo).

Snippets run with `bpy`, `bmesh`, `Vector`, `math` and these helpers:
  mat(name, (r, g, b))   get/create a flat-coloured material
  paint(obj, material)   assign a material to an object
Objects whose names start with "_" are helpers and are not drawn or counted.
"""
import ast
import hashlib
import json
import math
import os
import shutil
import sys
import traceback

import bpy  # must come first: it provides bmesh and mathutils
import bmesh
from mathutils import Vector
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.abspath(os.environ.get("BSTEP_DIR", "blender-live"))
BLEND = os.path.join(ROOT, "scene.blend")
FRAMES = os.path.join(ROOT, "frames")
HISTORY = os.path.join(ROOT, "history")
W, H = 640, 480


def mat(name, rgb):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.diffuse_color = (*rgb, 1.0)
    return m


def paint(obj, material):
    obj.data.materials.clear()
    obj.data.materials.append(material)


def model_objects():
    return [o for o in bpy.context.scene.objects
            if o.type == "MESH" and not o.name.startswith("_") and o.visible_get()]


def is_open(me):
    """True when the mesh has holes (edges with fewer or more than 2 faces)."""
    uses = {}
    for p in me.polygons:
        for k in p.edge_keys:
            uses[k] = uses.get(k, 0) + 1
    return any(n != 2 for n in uses.values())


def triangles(obj, depsgraph):
    """World-space triangles of an object, each with its material colour."""
    ev = obj.evaluated_get(depsgraph)
    me = ev.to_mesh()
    flip = ev.matrix_world.determinant() < 0  # mirrored objects turn inside out
    holes = is_open(me)
    me.calc_loop_triangles()
    mw = ev.matrix_world
    verts = [mw @ v.co for v in me.vertices]
    out = []
    for t in me.loop_triangles:
        m = obj.material_slots[t.material_index].material if obj.material_slots else None
        rgb = tuple(m.diffuse_color[:3]) if m else (0.7, 0.7, 0.7)
        order = list(t.vertices)[::-1] if flip else list(t.vertices)
        pts = [round(c, 4) for i in order for c in verts[i]]
        out.append((pts, rgb))
    ev.to_mesh_clear()
    return out, holes


def current_run():
    try:
        with open(os.path.join(ROOT, "run.txt")) as f:
            return f.read().strip()
    except OSError:
        return "run0"


def export_json(path, step_no, label, extra=None):
    for o in model_objects():
        if o.mode == "EDIT":
            o.update_from_editmode()  # edit-mode changes live in a bmesh until synced
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    objects, total = [], 0
    for o in model_objects():
        groups = {}
        tris, holes = triangles(o, dg)
        for pts, rgb in tris:
            groups.setdefault(rgb, []).extend(pts)
        for rgb, flat in groups.items():
            objects.append({"name": o.name, "color": [round(c, 4) for c in rgb],
                            "open": holes, "tris": flat})
            total += len(flat) // 9
    with open(path, "w") as f:
        doc = {"step": step_no, "label": label, "tris": total, "objects": objects}
        doc.update(extra or {})
        json.dump(doc, f, separators=(",", ":"))
    return total


def frame_camera(scene):
    """Point a fixed 3/4 camera at everything, zoomed to fit."""
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
    cam.data.clip_end = max(100.0, radius * 20)


def setup_render(scene):
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
    img = Image.open(path).convert("RGB")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 18)
        small = ImageFont.truetype("DejaVuSans.ttf", 14)
    except OSError:
        font = small = ImageFont.load_default()
    d.rectangle([0, H - 40, W, H], fill=(20, 20, 26))
    d.text((12, H - 31), f"Step {step}: {label}"[:52], font=font, fill=(240, 240, 240))
    d.text((W - 110, H - 28), f"{tris} tris", font=small, fill=(150, 200, 255))
    img.save(path)


def step_count():
    if not os.path.isdir(FRAMES):
        return 0
    return len([f for f in os.listdir(FRAMES) if f.endswith(".png")])


def snapshot(n, label):
    """Picture + triangle JSON + .blend copy for step n of the current scene."""
    scene = bpy.context.scene
    setup_render(scene)
    frame_camera(scene)
    os.makedirs(FRAMES, exist_ok=True)
    os.makedirs(HISTORY, exist_ok=True)
    png = os.path.join(FRAMES, f"step_{n:02d}.png")
    scene.render.filepath = png
    bpy.ops.render.render(write_still=True)
    tris = export_json(os.path.join(FRAMES, f"step_{n:02d}.json"), n, label)
    caption(png, n, label, tris)
    bpy.ops.wm.save_as_mainfile(filepath=BLEND)
    shutil.copy(BLEND, os.path.join(HISTORY, f"step_{n:02d}.blend"))
    names = ", ".join(sorted(o.name for o in model_objects()))
    print(f"BSTEP_OK step={n} tris={tris} png={png}", flush=True)
    print(f"BSTEP_OBJECTS {names}", flush=True)


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
    import time
    with open(os.path.join(ROOT, "run.txt"), "w") as f:
        f.write(f"run{int(time.time())}")
    print("BSTEP_RESET")


MAX_SNAPS = 150


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


def make_snapper(label, code):
    """Writes a frame of the scene whenever it changed, printing its path at once."""
    live = os.path.join(FRAMES, "live")
    os.makedirs(live, exist_ok=True)
    lines = code.splitlines()
    state = {"last": None, "count": len(os.listdir(live))}

    def snap(lineno):
        if state["count"] >= MAX_SNAPS + 10_000:
            return
        path = os.path.join(live, f"snap_{state['count']:05d}.json")
        src = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
        export_json(path, 0, label, {"run": current_run(), "seq": state["count"],
                                     "line": lineno, "code": src[:90]})
        with open(path) as f:
            digest = hashlib.md5(json.dumps(json.load(f)["objects"]).encode()).hexdigest()
        if digest == state["last"]:
            os.remove(path)
            return
        state["last"] = digest
        state["count"] += 1
        print(f"BSTEP_SNAP {path}\t{lineno}\t{src[:90]}", flush=True)

    return snap


def step(label, code):
    if os.path.exists(BLEND):
        bpy.ops.wm.open_mainfile(filepath=BLEND)
    else:
        reset()
    ns = {"bpy": bpy, "bmesh": bmesh, "Vector": Vector, "math": math,
          "mat": mat, "paint": paint, "__name__": "__bstep__"}
    ns["__snap__"] = make_snapper(label, code) if os.environ.get("BSTEP_STREAM") else (lambda n: None)
    try:
        tree = ast.fix_missing_locations(_Instrument().visit(ast.parse(code)))
        exec(compile(tree, f"<step: {label}>", "exec"), ns)
    except Exception:
        # nothing is saved: the scene stays as it was before this step
        print("BSTEP_ERROR", flush=True)
        for line in traceback.format_exc().splitlines():
            print(f"BSTEP_TB {line}", flush=True)
        sys.exit(2)
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    snapshot(step_count() + 1, label)


def undo():
    n = step_count()
    if n == 0:
        sys.exit("nothing to undo")
    for ext in ("png", "json"):
        p = os.path.join(FRAMES, f"step_{n:02d}.{ext}")
        if os.path.exists(p):
            os.remove(p)
    last = os.path.join(HISTORY, f"step_{n:02d}.blend")
    if os.path.exists(last):
        os.remove(last)
    prev = os.path.join(HISTORY, f"step_{n - 1:02d}.blend")
    if n > 1 and os.path.exists(prev):
        shutil.copy(prev, BLEND)
    else:
        reset()
    print(f"BSTEP_UNDO now_at={n - 1}")


def movie():
    files = sorted(f for f in os.listdir(FRAMES) if f.endswith(".png"))
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
        o.select_set(o.type == "MESH" and not o.name.startswith("_"))
    out = os.path.join(ROOT, "model.glb")
    bpy.ops.export_scene.gltf(filepath=out, use_selection=True)
    print(f"BSTEP_EXPORT glb={out} blend={BLEND}")


if __name__ == "__main__":
    args = sys.argv[1:]
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
