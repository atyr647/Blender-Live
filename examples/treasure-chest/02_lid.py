# Rounded lid: a half-cylinder with only 6 segments (that chunky N64 look)
SEG, R, HALF_X, BASE_Z = 6, 0.5, 0.8, 0.9
bm = bmesh.new()
rings = []
for x in (-HALF_X, HALF_X):
    ring = [bm.verts.new((x, R * math.cos(math.pi * i / SEG),
                          BASE_Z + R * 0.8 * math.sin(math.pi * i / SEG)))
            for i in range(SEG + 1)]
    rings.append(ring)
a, b = rings
for i in range(SEG):
    bm.faces.new((a[i], a[i + 1], b[i + 1], b[i]))
bm.faces.new(a[::-1])          # end caps
bm.faces.new(b)
bm.faces.new((a[0], b[0], b[-1], a[-1]))  # bottom
bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
me = bpy.data.meshes.new("lid")
bm.to_mesh(me)
lid = bpy.data.objects.new("lid", me)
bpy.context.collection.objects.link(lid)
paint(lid, mat("wood_dark", (0.45, 0.25, 0.1)))
