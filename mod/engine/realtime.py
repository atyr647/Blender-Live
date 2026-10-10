"""realtime - the realistic view's files: glTF models, baked textures, the sky.

At the end of a step (after the scene is saved, so nothing here is kept):

  * bake_procedural(): materials whose look comes from procedural nodes (noise,
    voronoi, wave, bricks, bevel, AO, attributes...) get those patterns baked
    into image textures (base colour, roughness, metallic, normal, emission),
    so a game engine, and the feed, can draw them;
  * export_changed(): every object whose mesh, transform or material changed
    since the last export is written to one .glb (full detail, real PBR
    materials: clearcoat, transmission, IOR, emission...), and a manifest says
    which file holds each object now;
  * render_sky(): the world (HDRI, sky texture or colour) rendered to an
    equirectangular .hdr, the same light the Cycles renders get.

Binary files reach the feed page as small JavaScript files (zlib + base64)
that the page loads with <script>, because the page's file store takes
scripts but not .glb or .hdr files.
"""
import base64
import hashlib
import json
import os
import zlib

import bpy

PROCEDURAL = {
    "TEX_NOISE", "TEX_VORONOI", "TEX_WAVE", "TEX_MAGIC", "TEX_MUSGRAVE", "TEX_GRADIENT",
    "TEX_CHECKER", "TEX_BRICK", "TEX_WHITE_NOISE", "TEX_GABOR", "BEVEL", "AMBIENT_OCCLUSION",
    "ATTRIBUTE", "VERTEX_COLOR", "TEX_POINTDENSITY", "WIREFRAME", "LAYER_WEIGHT", "FRESNEL",
}
JS_CHUNK = 15_000_000


def wrap_js(raw, out_base, key):
    """zlib + base64 into one or more .js files defining window.BLD[key/i]."""
    b64 = base64.b64encode(zlib.compress(raw, 6)).decode()
    paths = []
    for i in range(0, max(1, len(b64)), JS_CHUNK):
        n = i // JS_CHUNK
        path = f"{out_base}_{n}.js"
        with open(path, "w") as f:
            f.write(f'(window.BLD=window.BLD||{{}})["{key}/{n}"]="{b64[i:i + JS_CHUNK]}";\n')
        paths.append(path)
    return paths


# ---------------------------------------------------------------- baking

def _upstream_types(sock, seen=None):
    seen = seen if seen is not None else set()
    out = set()
    for link in sock.links:
        node = link.from_node
        if node.name in seen:
            continue
        seen.add(node.name)
        out.add(node.type)
        if node.type == "GROUP" and node.node_tree:
            out |= {n.type for n in node.node_tree.nodes}
        for s in node.inputs:
            if s.is_linked:
                out |= _upstream_types(s, seen)
    return out


def _principled(m):
    if not (m and m.use_nodes and m.node_tree):
        return None
    return next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)


BAKE_INPUTS = {
    # socket name(s) -> (bake type, colour space)
    "base": (("Base Color",), "DIFFUSE", "sRGB"),
    "rough": (("Roughness",), "ROUGHNESS", "Non-Color"),
    "metal": (("Metallic",), "EMIT", "Non-Color"),
    "normal": (("Normal",), "NORMAL", "Non-Color"),
    "emit": (("Emission Color", "Emission"), "EMIT", "sRGB"),
}


def needs_bake(m):
    """Which inputs of the material are driven by procedural nodes."""
    p = _principled(m)
    if p is None:
        return []
    found = []
    for name, (sockets, _, _) in BAKE_INPUTS.items():
        sock = next((p.inputs.get(s) for s in sockets if p.inputs.get(s) is not None), None)
        if sock is not None and sock.is_linked and (_upstream_types(sock) & PROCEDURAL):
            found.append(name)
    return found


def _ensure_uvs(ob):
    if ob.data.uv_layers:
        return
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.003)
    bpy.ops.object.mode_set(mode="OBJECT")


def _texture_size(ob, base):
    # bigger objects get bigger textures, between 256 and `base`
    d = max(ob.dimensions) if ob.dimensions else 1.0
    size = 256
    while size < base and size < d * 256:
        size *= 2
    return size


def bake_procedural(objects, cache_dir, base_size=1024, log=print):
    """Bake procedural looks into images, rewiring each material copy to use them."""
    todo = []
    for ob in objects:
        if ob.type != "MESH" or not ob.material_slots:
            continue
        for i, slot in enumerate(ob.material_slots):
            inputs = needs_bake(slot.material)
            if inputs:
                todo.append((ob, i, inputs))
    if not todo:
        return 0
    os.makedirs(cache_dir, exist_ok=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 4
    scene.render.bake.margin = 4
    baked = 0
    for ob, slot_index, inputs in todo:
        _ensure_uvs(ob)
        src = ob.material_slots[slot_index].material
        m = src.copy()  # this object's own copy: its baked textures follow its UVs
        ob.material_slots[slot_index].material = m
        p = _principled(m)
        nodes, links = m.node_tree.nodes, m.node_tree.links
        size = _texture_size(ob, base_size)
        sig = hashlib.md5(f"{ob.name}|{src.name}|{size}|{len(ob.data.vertices)}|{inputs}".encode()).hexdigest()[:12]
        for name in inputs:
            sockets, bake_type, space = BAKE_INPUTS[name]
            sock = next(p.inputs.get(s) for s in sockets if p.inputs.get(s) is not None)
            path = os.path.join(cache_dir, f"{sig}_{name}.png")
            img = bpy.data.images.new(f"{ob.name}_{src.name}_{name}", size, size, alpha=False)
            img.colorspace_settings.name = space
            tex = nodes.new("ShaderNodeTexImage")
            tex.image = img
            if os.path.exists(path):
                img.filepath_raw = path
                img.source = "FILE"
                img.reload()
            else:
                for n in nodes:
                    n.select = False
                tex.select = True
                nodes.active = tex
                saved = None
                if bake_type == "EMIT" and name == "metal":
                    # route metallic into a temporary emission shader and bake that
                    out = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")
                    saved = out.inputs["Surface"].links[0].from_socket if out.inputs["Surface"].is_linked else None
                    em = nodes.new("ShaderNodeEmission")
                    links.new(sock.links[0].from_socket, em.inputs["Color"])
                    links.new(em.outputs[0], out.inputs["Surface"])
                bpy.ops.object.select_all(action="DESELECT")
                ob.select_set(True)
                bpy.context.view_layer.objects.active = ob
                kw = {"type": bake_type}
                if bake_type == "DIFFUSE":
                    kw["pass_filter"] = {"COLOR"}
                try:
                    bpy.ops.object.bake(**kw)
                except RuntimeError as err:
                    log(f"BSTEP_WARN bake {ob.name}/{src.name}/{name}: {err}")
                    nodes.remove(tex)
                    continue
                finally:
                    if saved is not None:
                        out = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")
                        links.new(saved, out.inputs["Surface"])
                        nodes.remove(em)
                img.filepath_raw = path
                img.file_format = "PNG"
                img.save()
                baked += 1
            # rewire the input to the baked image
            for link in list(sock.links):
                links.remove(link)
            if name == "normal":
                nm = nodes.new("ShaderNodeNormalMap")
                links.new(tex.outputs["Color"], nm.inputs["Color"])
                links.new(nm.outputs["Normal"], sock)
            elif name in ("rough", "metal"):
                sep = nodes.new("ShaderNodeSeparateColor")
                links.new(tex.outputs["Color"], sep.inputs["Color"])
                links.new(sep.outputs[0], sock)
            else:
                links.new(tex.outputs["Color"], sock)
    log(f"BSTEP_BAKED textures={baked} materials={len(todo)}")
    return baked


# ---------------------------------------------------------------- export

def signature(ob, depsgraph):
    """Changes when the object's evaluated mesh, transform or materials change."""
    h = hashlib.md5()
    ev = ob.evaluated_get(depsgraph)
    h.update(repr([round(v, 5) for row in ev.matrix_world for v in row]).encode())
    data = ev.data
    if data is not None:
        h.update(f"{type(data).__name__}:{data.name}".encode())
        if ob.type == "MESH":
            me = ev.to_mesh()
            try:
                import numpy as np
                co = np.empty(len(me.vertices) * 3, np.float32)
                me.vertices.foreach_get("co", co)
                h.update(co.tobytes())
                h.update(str(len(me.polygons)).encode())
            finally:
                ev.to_mesh_clear()
    for slot in ob.material_slots:
        m = slot.material
        if m is None:
            continue
        h.update(m.name.encode())
        if m.node_tree:
            for n in m.node_tree.nodes:
                for s in n.inputs:
                    v = getattr(s, "default_value", None)
                    if v is not None:
                        h.update(repr(v[:] if hasattr(v, "__len__") else v).encode())
        else:
            h.update(repr(m.diffuse_color[:]).encode())
    return h.hexdigest()[:16]


def exportable(scene):
    keep = {"MESH", "CURVE", "SURFACE", "META", "FONT", "EMPTY", "LIGHT"}
    return [o for o in scene.objects
            if o.type in keep and not o.name.startswith("_") and o.visible_get()]


def _gltf_kwargs(path):
    props = bpy.ops.export_scene.gltf.get_rna_type().properties.keys()
    want = {
        "filepath": path, "export_format": "GLB", "use_selection": True,
        "export_apply": True, "export_materials": "EXPORT", "export_cameras": False,
        "export_lights": True, "export_yup": True, "export_image_format": "AUTO",
        "export_gpu_instances": True, "export_draco_mesh_compression_enable": False,
        "export_extras": False, "export_animations": False,
    }
    return {k: v for k, v in want.items() if k in props}


def export_changed(state_path, out_dir, run, step_no, cache_dir, bake=True, bake_size=1024, log=print):
    """One .glb of everything that changed this step; returns (manifest, js files)."""
    os.makedirs(out_dir, exist_ok=True)
    try:
        with open(state_path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {"run": run, "objects": {}}
    if state.get("run") != run:
        state = {"run": run, "objects": {}}

    scene = bpy.context.scene
    dg = bpy.context.evaluated_depsgraph_get()
    objs = exportable(scene)
    sigs = {o.name: signature(o, dg) for o in objs}
    changed = [o for o in objs if state["objects"].get(o.name, {}).get("sig") != sigs[o.name]]
    files = []
    key = f"{run}/g{step_no}"
    if changed:
        if bake:
            bake_procedural([o for o in changed if o.type == "MESH"], cache_dir, bake_size, log)
        bpy.ops.object.mode_set(mode="OBJECT") if bpy.context.object and bpy.context.object.mode != "OBJECT" else None
        bpy.ops.object.select_all(action="DESELECT")
        for o in changed:
            o.select_set(True)
        glb = os.path.join(out_dir, f"step_{step_no:02d}.glb")
        bpy.ops.export_scene.gltf(**_gltf_kwargs(glb))
        with open(glb, "rb") as f:
            files = wrap_js(f.read(), os.path.join(out_dir, f"step_{step_no:02d}_glb"), key)
        for o in changed:
            state["objects"][o.name] = {"sig": sigs[o.name], "file": key}
    # forget objects that were deleted
    alive = {o.name for o in objs}
    state["objects"] = {k: v for k, v in state["objects"].items() if k in alive}
    with open(state_path, "w") as f:
        json.dump(state, f)
    manifest = {
        "run": run, "step": step_no,
        "file": {"key": key, "parts": len(files)} if files else None,
        "objects": {k: v["file"] for k, v in state["objects"].items()},
        "changed": [o.name for o in changed],
    }
    return manifest, files


# ---------------------------------------------------------------- sky

def world_signature(scene):
    w = scene.world
    if w is None:
        return "none"
    h = hashlib.md5(w.name.encode())
    if w.use_nodes and w.node_tree:
        for n in w.node_tree.nodes:
            h.update(n.type.encode())
            if n.type == "TEX_ENVIRONMENT" and n.image:
                h.update(n.image.filepath.encode())
            for s in n.inputs:
                v = getattr(s, "default_value", None)
                if v is not None:
                    h.update(repr(v[:] if hasattr(v, "__len__") else v).encode())
            for attr in ("sun_elevation", "sun_rotation", "altitude", "air_density", "sky_type"):
                if hasattr(n, attr):
                    h.update(repr(getattr(n, attr)).encode())
    else:
        h.update(repr(w.color[:]).encode())
    return h.hexdigest()[:12]


def render_sky(out_base, key, width=1024, log=print):
    """The world as an equirectangular .hdr, wrapped for the page."""
    scene = bpy.context.scene
    if scene.world is None:
        return []
    hidden = []
    for o in scene.objects:
        if not o.hide_render and o.type != "CAMERA":
            o.hide_render = True
            hidden.append(o)
    cam_data = bpy.data.cameras.new("_skycam")
    cam_data.type = "PANO"
    if hasattr(cam_data, "panorama_type"):
        cam_data.panorama_type = "EQUIRECTANGULAR"
    else:
        cam_data.cycles.panorama_type = "EQUIRECTANGULAR"
    cam = bpy.data.objects.new("_skycam", cam_data)
    scene.collection.objects.link(cam)
    cam.rotation_euler = (1.5708, 0, -1.5708)  # image centre faces +X, as the feed page (three.js) expects
    old = (scene.camera, scene.render.engine, scene.render.resolution_x, scene.render.resolution_y,
           scene.render.resolution_percentage, scene.render.image_settings.file_format, scene.render.filepath)
    scene.camera = cam
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 8
    scene.render.resolution_x, scene.render.resolution_y = width, width // 2
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "HDR"
    path = out_base + ".hdr"
    scene.render.filepath = path
    try:
        bpy.ops.render.render(write_still=True)
    except RuntimeError as err:
        log(f"BSTEP_WARN sky render: {err}")
        return []
    finally:
        (scene.camera, scene.render.engine, scene.render.resolution_x, scene.render.resolution_y,
         scene.render.resolution_percentage, scene.render.image_settings.file_format,
         scene.render.filepath) = old
        for o in hidden:
            o.hide_render = False
        bpy.data.objects.remove(cam)
    with open(path, "rb") as f:
        return wrap_js(f.read(), out_base, key)
