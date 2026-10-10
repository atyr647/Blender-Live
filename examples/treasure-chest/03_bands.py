# Iron bands: two straps wrapping the body and over the lid
iron = mat("iron", (0.22, 0.22, 0.25))
SEG, R, BASE_Z = 6, 0.53, 0.9

def lid_strap(x0, x1, name):
    bm = bmesh.new()
    a, b = [[bm.verts.new((x, R * math.cos(math.pi * i / SEG),
                           BASE_Z + R * 0.8 * math.sin(math.pi * i / SEG)))
             for i in range(SEG + 1)] for x in (x0, x1)]
    for i in range(SEG):
        bm.faces.new((a[i], a[i + 1], b[i + 1], b[i]))
    bm.faces.new(a[::-1]); bm.faces.new(b)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name); bm.to_mesh(me)
    ob = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(ob)
    paint(ob, iron)

for i, x in enumerate((-0.55, 0.55)):
    bpy.ops.mesh.primitive_cube_add(size=1, location=(x, 0, 0.45))
    strap = bpy.context.object
    strap.name = f"band_body_{i}"
    strap.scale = (0.14, 1.06, 0.92)
    bpy.ops.object.transform_apply(scale=True)
    paint(strap, iron)
    lid_strap(x - 0.07, x + 0.07, f"band_lid_{i}")
