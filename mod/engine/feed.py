"""feed - turns the Blender scene into compact frames for the live feed.

Everything that draws (meshes, curves, text, geometry-nodes and collection
instances) becomes "entries": one per object instance and material, with the
material's real look (base colour, metal, roughness, glow, glass).

Two outputs:
  * preview frames, small enough for the feed's database (under 256 KiB):
    big meshes are simplified to a triangle budget, and a frame only carries
    the entries that changed since the frame before (a keyframe carries all);
  * a full preview of the whole scene per frame, for the mod's own view.

Full-detail models with real materials come from realtime.py.
"""
import base64
import fnmatch
import hashlib
import json
import math

import bpy
import numpy as np

DRAWN_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT"}
DOC_LIMIT = 240_000          # bytes of JSON per frame document (the store allows 256 KiB)


# ---------------------------------------------------------------- materials

def _input(node, *names):
    for n in names:
        sock = node.inputs.get(n)
        if sock is not None:
            return sock
    return None


def _socket_color(sock, fallback):
    """The colour feeding a socket: its own value, or a simple node upstream."""
    if sock is None:
        return fallback
    if not sock.is_linked:
        v = sock.default_value
        return [v[0], v[1], v[2]] if hasattr(v, "__len__") else [v, v, v]
    src = sock.links[0].from_node
    if src.type == "RGB":
        v = src.outputs[0].default_value
        return [v[0], v[1], v[2]]
    if src.type == "VALTORGB":  # colour ramp: its middle colour
        els = src.color_ramp.elements
        v = els[len(els) // 2].color
        return [v[0], v[1], v[2]]
    for name in ("Color", "Color1", "A", "Base Color"):
        inner = src.inputs.get(name)
        if inner is not None and inner.type == "RGBA":
            return _socket_color(inner, fallback)
    return fallback


def _socket_value(sock, fallback):
    if sock is None or sock.is_linked:
        return fallback
    v = sock.default_value
    return float(v[0]) if hasattr(v, "__len__") else float(v)


def _shader_look(node, look, depth=0):
    if node is None or depth > 6:
        return
    t = node.type
    if t == "BSDF_PRINCIPLED":
        look["color"] = _socket_color(_input(node, "Base Color"), look["color"])
        look["metal"] = _socket_value(_input(node, "Metallic"), look["metal"])
        look["rough"] = _socket_value(_input(node, "Roughness"), look["rough"])
        strength = _socket_value(_input(node, "Emission Strength"), 1.0)
        emit_col = _socket_color(_input(node, "Emission Color", "Emission"), [0, 0, 0])
        if strength > 0 and max(emit_col) > 0.02:
            look["emit"] = round(min(4.0, strength * max(emit_col)), 3)
            look["emitColor"] = emit_col
        trans = _socket_value(_input(node, "Transmission Weight", "Transmission"), 0.0)
        alpha = _socket_value(_input(node, "Alpha"), 1.0)
        look["alpha"] = round(min(alpha, 1.0 - 0.65 * trans), 3)
    elif t == "EMISSION":
        col = _socket_color(_input(node, "Color"), [1, 1, 1])
        look["color"] = col
        look["emit"] = round(min(4.0, _socket_value(_input(node, "Strength"), 1.0)), 3)
        look["emitColor"] = col
    elif t in ("BSDF_GLASS", "BSDF_REFRACTION", "BSDF_TRANSPARENT"):
        look["color"] = _socket_color(_input(node, "Color"), [0.9, 0.95, 1.0])
        look["alpha"] = 0.35
        look["rough"] = _socket_value(_input(node, "Roughness"), 0.05)
    elif t in ("BSDF_DIFFUSE", "BSDF_GLOSSY", "BSDF_ANISOTROPIC", "SUBSURFACE_SCATTERING", "BSDF_SHEEN"):
        look["color"] = _socket_color(_input(node, "Color", "Base Color"), look["color"])
        if t in ("BSDF_GLOSSY", "BSDF_ANISOTROPIC"):
            look["metal"] = 0.8
            look["rough"] = _socket_value(_input(node, "Roughness"), 0.3)
    elif t in ("MIX_SHADER", "ADD_SHADER"):
        shaders = [s for s in node.inputs if s.type == "SHADER" and s.is_linked]
        for s in shaders[:1]:
            _shader_look(s.links[0].from_node, look, depth + 1)
        if len(shaders) > 1:  # glow mixed in on top: keep it
            extra = {"color": look["color"], "metal": 0, "rough": 0.5, "alpha": 1.0}
            _shader_look(shaders[1].links[0].from_node, extra, depth + 1)
            if extra.get("emit"):
                look["emit"], look["emitColor"] = extra["emit"], extra["emitColor"]
    elif t == "GROUP" and node.node_tree:
        out = next((n for n in node.node_tree.nodes if n.type == "GROUP_OUTPUT"), None)
        sock = out and next((s for s in out.inputs if s.type == "SHADER" and s.is_linked), None)
        if sock:
            _shader_look(sock.links[0].from_node, look, depth + 1)


_look_cache = {}


def material_look(m):
    """What the material looks like, as the feed draws it."""
    if m is None:
        return {"color": [0.7, 0.7, 0.72], "metal": 0.0, "rough": 0.6, "alpha": 1.0}
    if m.name in _look_cache:
        return _look_cache[m.name]
    look = {"color": list(m.diffuse_color[:3]), "metal": float(m.metallic),
            "rough": float(m.roughness), "alpha": float(m.diffuse_color[3])}
    if m.use_nodes and m.node_tree:
        out = next((n for n in m.node_tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None)
        out = out or next((n for n in m.node_tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
        surf = out.inputs.get("Surface") if out else None
        if surf is not None and surf.is_linked:
            _shader_look(surf.links[0].from_node, look)
    look["color"] = [round(float(c), 4) for c in look["color"][:3]]
    if "emitColor" in look:
        look["emitColor"] = [round(float(c), 4) for c in look["emitColor"][:3]]
    _look_cache[m.name] = look
    return look


# ---------------------------------------------------------------- geometry

class Mesh:
    """One evaluated mesh in local space, split by material slot."""

    def __init__(self, ob):
        me = ob.to_mesh()
        try:
            me.calc_loop_triangles()
            nv, nt = len(me.vertices), len(me.loop_triangles)
            co = np.empty(nv * 3, np.float32)
            me.vertices.foreach_get("co", co)
            tri = np.empty(nt * 3, np.int32)
            me.loop_triangles.foreach_get("vertices", tri)
            mi = np.zeros(nt, np.int32)
            if nt:
                me.loop_triangles.foreach_get("material_index", mi)
            np_ = len(me.polygons)
            sm = np.zeros(np_, bool)
            if np_:
                me.polygons.foreach_get("use_smooth", sm)
            ne = len(me.edges)
            ei = np.empty(len(me.loops), np.int32)
            me.loops.foreach_get("edge_index", ei)
            uses = np.bincount(ei, minlength=ne) if ne else np.zeros(0, np.int64)
            self.open = bool((uses != 2).any()) if ne else True
            self.smooth = bool(sm.mean() > 0.5) if np_ else False
        finally:
            ob.to_mesh_clear()
        self.co = co.reshape(-1, 3)
        self.tri = tri.reshape(-1, 3)
        self.mi = mi
        h = hashlib.md5()
        h.update(self.co.tobytes())
        h.update(self.tri.tobytes())
        h.update(mi.tobytes())
        self.hash = h.hexdigest()[:16]

    def parts(self):
        """(material index, local verts, triangles) per material slot used."""
        if len(self.tri) == 0:
            return []
        out = []
        for m in np.unique(self.mi):
            t = self.tri[self.mi == m]
            used, inv = np.unique(t, return_inverse=True)
            out.append((int(m), self.co[used], inv.reshape(-1, 3).astype(np.int32)))
        return out


def collect(depsgraph):
    """Every drawn instance: (key, name, mesh, matrix 4x4, materials)."""
    meshes = {}
    found = []
    counts = {}
    for inst in depsgraph.object_instances:
        ob = inst.object
        if ob.type not in DRAWN_TYPES:
            continue
        orig = ob.original
        if inst.is_instance:
            parent = inst.parent.original.name if inst.parent else ""
            if parent.startswith("_"):
                continue
            base = f"{parent}/{orig.name}"
        else:
            base = orig.name
        if orig.name.startswith("_") or base.startswith("_"):
            continue
        n = counts.get(base, 0)
        counts[base] = n + 1
        key = base if n == 0 else f"{base}#{n}"
        mid = ob.data.name if ob.data is not None else ob.name
        mkey = (ob.type, mid, ob.name if ob.type != "MESH" or ob.modifiers else "")
        mesh = meshes.get(mkey)
        if mesh is None:
            try:
                mesh = Mesh(ob)
            except Exception:
                continue
            meshes[mkey] = mesh
        mats = [s.material for s in ob.material_slots] or [None]
        found.append((key, base, mesh, np.array(inst.matrix_world, np.float64), mats))
    return found


def _world(verts, matrix):
    return (verts.astype(np.float64) @ matrix[:3, :3].T + matrix[:3, 3]).astype(np.float32)


def simplify(verts, tris, budget):
    """Vertex clustering: the same shape with about `budget` triangles."""
    if len(tris) <= budget or budget < 4:
        return verts, tris
    lo = verts.min(0)
    span = float(max((verts.max(0) - lo).max(), 1e-6))
    n = max(2, int(math.sqrt(budget / 2.2)))
    best = (verts, tris)
    for _ in range(10):
        cell = np.floor((verts - lo) / span * n).astype(np.int64)
        k = n + 1
        keys = cell[:, 0] + cell[:, 1] * k + cell[:, 2] * k * k
        uniq, inv = np.unique(keys, return_inverse=True)
        inv = inv.reshape(-1)
        cnt = np.bincount(inv).astype(np.float64)
        nv = np.stack([np.bincount(inv, weights=verts[:, a]) for a in range(3)], 1) / cnt[:, None]
        t = inv[tris]
        keep = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
        t = t[keep]
        if len(t):
            _, first = np.unique(np.sort(t, 1), axis=0, return_index=True)
            t = t[np.sort(first)]
        best = (nv.astype(np.float32), t)
        if len(t) <= budget * 1.05:
            break
        n = max(2, int(n * 0.8))
    v, t = best
    used, inv = np.unique(t, return_inverse=True)
    return v[used], inv.reshape(-1, 3).astype(np.int32)


def decimate(verts, tris, budget):
    """Blender's own decimation (keeps thin parts like rings and pipes), clustering as a fallback."""
    if len(tris) <= budget or budget < 4:
        return verts, tris
    if len(tris) > 600_000:  # very big: cluster first so decimation stays quick
        verts, tris = simplify(verts, tris, 300_000)
    me = obj = None
    try:
        me = bpy.data.meshes.new("_bl_preview")
        me.vertices.add(len(verts))
        me.vertices.foreach_set("co", verts.astype(np.float32).ravel())
        me.loops.add(len(tris) * 3)
        me.loops.foreach_set("vertex_index", tris.astype(np.int32).ravel())
        me.polygons.add(len(tris))
        me.polygons.foreach_set("loop_start", np.arange(0, len(tris) * 3, 3, dtype=np.int32))
        me.update(calc_edges=True)
        obj = bpy.data.objects.new("_bl_preview", me)
        bpy.context.scene.collection.objects.link(obj)
        mod = obj.modifiers.new("d", "DECIMATE")
        mod.ratio = max(0.0005, min(1.0, budget / len(tris)))
        mod.use_collapse_triangulate = True
        dg = bpy.context.evaluated_depsgraph_get()
        dg.update()
        ev = obj.evaluated_get(dg)
        em = ev.to_mesh()
        em.calc_loop_triangles()
        co = np.empty(len(em.vertices) * 3, np.float32)
        em.vertices.foreach_get("co", co)
        tri = np.empty(len(em.loop_triangles) * 3, np.int32)
        em.loop_triangles.foreach_get("vertices", tri)
        ev.to_mesh_clear()
        if len(tri) == 0:
            raise ValueError("empty")
        return co.reshape(-1, 3), tri.reshape(-1, 3)
    except Exception:
        return simplify(verts, tris, budget)
    finally:
        if obj is not None:
            bpy.data.objects.remove(obj)
        if me is not None:
            bpy.data.meshes.remove(me)


def _b64(arr):
    return base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode()


def encode(verts, tris):
    """Quantised 16-bit positions inside the entry's own box, plus indices."""
    lo = verts.min(0) if len(verts) else np.zeros(3, np.float32)
    hi = verts.max(0) if len(verts) else np.zeros(3, np.float32)
    span = np.maximum(hi - lo, 1e-9)
    q = np.round((verts - lo) / span * 65535.0) - 32768
    out = {"lo": [round(float(x), 5) for x in lo], "hi": [round(float(x), 5) for x in hi],
           "v": _b64(q.astype("<i2"))}
    wide = len(verts) > 65535
    out["i"] = _b64(tris.astype("<u4" if wide else "<u2"))
    out["w"] = 4 if wide else 2
    return out


def look_fields(look):
    f = {"c": look["color"]}
    if look.get("metal"):
        f["m"] = round(look["metal"], 3)
    if look.get("rough") is not None:
        f["r"] = round(look["rough"], 3)
    if look.get("emit"):
        f["e"] = look["emit"]
        f["ec"] = look.get("emitColor", look["color"])
    if look.get("alpha", 1.0) < 0.999:
        f["a"] = look["alpha"]
    return f


# ---------------------------------------------------------------- focus

def focus_box(pattern, found):
    """World box around the objects whose names match `pattern` (globs, comma separated)."""
    if not pattern:
        return None
    pats = [p.strip().lower() for p in pattern.split(",") if p.strip()]
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    for key, base, mesh, mw, _ in found:
        names = [base.lower(), key.lower(), base.split("/")[-1].lower()]
        if not any(fnmatch.fnmatch(n, p) or p in n for n in names for p in pats):
            continue
        if len(mesh.co) == 0:
            continue
        w = _world(np.array([mesh.co.min(0), mesh.co.max(0)]), mw)
        lo = np.minimum(lo, w.min(0))
        hi = np.maximum(hi, w.max(0))
    if not np.isfinite(lo).all():
        return None
    return {"lo": [round(float(x), 4) for x in lo], "hi": [round(float(x), 4) for x in hi]}


# ---------------------------------------------------------------- preview frames

class FrameWriter:
    """Keeps what the last frame held, so the next one carries only changes."""

    def __init__(self, budget):
        self.budget = budget
        self.prev = {}        # key -> signature of the entry last sent
        self.cache = {}       # (mesh hash, slot, matrix digest) -> (share, encoded preview)

    def load(self, path):
        """Previews from earlier steps, so unchanged parts look exactly the same."""
        try:
            with open(path) as f:
                self.cache = {k: (v[0], v[1]) for k, v in json.load(f).items()}
        except (OSError, ValueError):
            self.cache = {}

    def save(self, path, keep):
        with open(path, "w") as f:
            json.dump({k: v for k, v in self.cache.items() if k in keep}, f, separators=(",", ":"))

    def build(self, found):
        """All entries of the scene: key -> (signature, payload, full tris, preview tris)."""
        rows = []
        for key, base, mesh, mw, mats in found:
            for slot, verts, tris in mesh.parts():
                m = mats[slot] if slot < len(mats) else mats[-1]
                look = material_look(m)
                ek = key if slot == 0 else f"{key}@{slot}"
                rows.append((ek, base, mesh, mw, slot, verts, tris, look))
        total = sum(len(r[6]) for r in rows) or 1
        entries = {}
        self.used = set()
        for ek, base, mesh, mw, slot, verts, tris, look in rows:
            n = len(tris)
            share = n if total <= self.budget else max(min(n, 24), int(self.budget * n / total))
            mdig = hashlib.md5(np.round(mw, 5).tobytes()).hexdigest()[:12]
            ckey = f"{mesh.hash}.{slot}.{mdig}"
            hit = self.cache.get(ckey)
            # keep an unchanged part's preview as it was unless its budget moved a lot:
            # re-simplifying every part whenever another one is added made the feed rebuild everything
            enc = hit[1] if hit and (hit[0] >= n or 0.5 * share <= hit[0] <= 2 * share) else None
            if enc is None:
                wv = _world(verts, mw)
                if np.linalg.det(mw[:3, :3]) < 0:
                    tris = tris[:, ::-1]
                pv, pt = decimate(wv, tris, share)
                enc = encode(pv, pt)
                enc["pt"] = int(len(pt))
                self.cache[ckey] = (share, enc)
            self.used.add(ckey)
            payload = {"k": ek, "n": base, **look_fields(look), "s": mesh.smooth,
                       "o": mesh.open, "t": int(n), **enc}
            sig = hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            entries[ek] = (sig, payload, n, enc["pt"])
        return entries

    def frame(self, found, meta, keyframe, entries=None):
        entries = entries if entries is not None else self.build(found)
        changed = [p for k, (s, p, _, _) in entries.items() if keyframe or self.prev.get(k) != s]
        removed = set(self.prev) - set(entries)
        if not keyframe and not changed and not removed:
            return None
        doc = dict(meta)
        doc.update({
            "format": 3,
            "kf": bool(keyframe),
            "tris": int(sum(e[2] for e in entries.values())),
            "ptris": int(sum(e[3] for e in entries.values())),
            "objects": changed,
        })
        if not keyframe:
            doc["keep"] = [k for k in entries if k not in {p["k"] for p in changed}]
        self.prev = {k: e[0] for k, e in entries.items()}
        return doc

    def frames(self, found, meta, keyframe):
        """(feed frame text or None when nothing changed, full frame text), both under the size limit."""
        budget = self.budget
        delta = full = None
        for _ in range(6):
            entries = self.build(found)
            saved_prev = dict(self.prev)
            doc = self.frame(found, meta, keyframe, entries)
            delta = None if doc is None else json.dumps(doc, separators=(",", ":"))
            self.prev, prev_after = {}, self.prev
            full_doc = self.frame(found, meta, True, entries)
            self.prev = prev_after
            full = json.dumps(full_doc, separators=(",", ":"))
            if (delta is None or len(delta) <= DOC_LIMIT) and len(full) <= DOC_LIMIT:
                break
            self.prev = saved_prev
            self.budget = max(500, int(self.budget * 0.6))
        self.budget = budget
        return delta, full
