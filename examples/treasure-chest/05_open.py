# Open the lid on a hinge at the back, then fill the chest with treasure
hinge = bpy.data.objects.new("hinge", None)
bpy.context.collection.objects.link(hinge)
hinge.location = (0, 0.5, 0.9)
for name in ("lid", "band_lid_0", "band_lid_1"):
    ob = bpy.data.objects[name]
    ob.parent = hinge
    ob.location = ob.location - hinge.location  # now relative to the hinge
hinge.rotation_euler.x = math.radians(-70)

# cut away the top of the body so we can see inside
body = bpy.data.objects["body"]
bm = bmesh.new(); bm.from_mesh(body.data)
top = [f for f in bm.faces if f.normal.z > 0.9]
bmesh.ops.delete(bm, geom=top, context="FACES_ONLY")
bm.to_mesh(body.data)
for o in ("band_body_0", "band_body_1"):
    bpy.data.objects[o].scale.z = 0.97  # keep straps from poking above the gold

# gold surface just under the rim
bpy.ops.mesh.primitive_plane_add(size=1, location=(0, 0, 0.84))
pile = bpy.context.object
pile.name = "gold_pile"
pile.scale = (1.5, 0.9, 1)
paint(pile, mat("gold", (0.95, 0.72, 0.15)))

# a few chunky 6-sided coins spilling over
import random
random.seed(64)
coin = mat("coin", (1.0, 0.65, 0.05))
for i in range(9):
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=6, radius=0.11, depth=0.03,
        location=(random.uniform(-0.6, 0.6), random.uniform(-0.35, 0.3), 0.87 + random.uniform(0, 0.08)),
        rotation=(random.uniform(-0.5, 0.5), random.uniform(-0.5, 0.5), 0))
    paint(bpy.context.object, coin)
# and two on the floor in front
for x in (-0.4, 0.3):
    bpy.ops.mesh.primitive_cylinder_add(vertices=6, radius=0.11, depth=0.03,
                                        location=(x, -0.8, 0.015))
    paint(bpy.context.object, coin)
