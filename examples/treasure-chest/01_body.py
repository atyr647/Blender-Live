# A simple box for the chest body (12 triangles - very N64 friendly)
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.45))
body = bpy.context.object
body.name = "body"
body.scale = (1.6, 1.0, 0.9)
bpy.ops.object.transform_apply(scale=True)
paint(body, mat("wood", (0.55, 0.32, 0.14)))
