# Gold lock plate with a keyhole on the front
gold = mat("gold", (0.95, 0.72, 0.15))
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, -0.53, 0.72))
plate = bpy.context.object
plate.name = "lock"
plate.scale = (0.3, 0.06, 0.34)
bpy.ops.object.transform_apply(scale=True)
paint(plate, gold)

bpy.ops.mesh.primitive_cylinder_add(vertices=6, radius=0.05, depth=0.04,
                                    location=(0, -0.565, 0.76), rotation=(math.pi / 2, 0, 0))
hole = bpy.context.object
hole.name = "keyhole"
paint(hole, mat("black", (0.03, 0.03, 0.03)))
