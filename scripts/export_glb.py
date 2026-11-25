"""Export one legacy Blender asset, validate the GLB, and write measured metadata."""
import bpy
import json
import math
import pathlib
import struct
import sys
from mathutils import Matrix, Vector

out = pathlib.Path(sys.argv[sys.argv.index("--") + 1]).resolve()
out.parent.mkdir(parents=True, exist_ok=True)
assert bpy.data.armatures, "No source armature"
source_actions = sorted(a.name for a in bpy.data.actions if a.fcurves)
assert source_actions, "No source animation actions"
material_fixes = []
# Blender 2.79 materials may have no node graph. Preserve their imported diffuse colour.
for mat in bpy.data.materials:
    if not mat.use_nodes:
        rgba = tuple(mat.diffuse_color)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        assert bsdf is not None, mat.name
        bsdf.inputs["Base Color"].default_value = rgba
        bsdf.inputs["Roughness"].default_value = 0.85
        material_fixes.append({"name": mat.name, "rgba": rgba})

# Keep native object/bone scales and hierarchy; never apply transforms to skinned meshes.
bpy.ops.export_scene.gltf(
    filepath=str(out), export_format="GLB", export_yup=True,
    export_animations=True, export_animation_mode="ACTIONS",
    export_force_sampling=True, export_frame_range=False,
    export_skins=True, export_cameras=False, export_lights=False,
    export_materials="EXPORT", export_all_vertex_colors=True,
    export_anim_single_armature=True,
)

def read_glb(path):
    data = path.read_bytes()
    magic, ver, length = struct.unpack_from("<4sII", data)
    assert magic == b"glTF" and ver == 2 and length == len(data)
    n, kind = struct.unpack_from("<I4s", data, 12)
    assert kind == b"JSON"
    return json.loads(data[20:20+n]), data[20+n:]

def write_glb(path, doc, chunks):
    body = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    body += b" " * (-len(body) % 4)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 20+len(body)+len(chunks))
                     + struct.pack("<I4s", len(body), b"JSON") + body + chunks)

doc, chunks = read_glb(out)
clips = [a["name"] for a in doc.get("animations", [])]
assert clips and len(clips) == len(set(clips)), "Missing or duplicate clips"
assert set(source_actions).issubset(clips), f"Action coverage mismatch: {source_actions} -> {clips}"
assert doc.get("skins"), "No exported skins"
assert all("uri" not in b for b in doc.get("buffers", [])), "External buffer"
assert all("uri" not in i for i in doc.get("images", [])), "External image"
assert any("JOINTS_0" in p["attributes"] and "WEIGHTS_0" in p["attributes"]
           for m in doc["meshes"] for p in m["primitives"]), "No skinned primitives"

def import_rest(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(path))
    arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    assert arms, "Round-trip lost armature"
    for obj in bpy.context.scene.objects:
        if obj.animation_data:
            obj.animation_data.action = None
            for track in obj.animation_data.nla_tracks:
                track.mute = True
    for arm in arms:
        arm.data.pose_position = "REST"
    bpy.context.view_layer.update()
    return arms

def points():
    deps = bpy.context.evaluated_depsgraph_get()
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        evaluated = obj.evaluated_get(deps)
        mesh = evaluated.to_mesh()
        try:
            for vertex in mesh.vertices:
                yield evaluated.matrix_world @ vertex.co
        finally:
            evaluated.to_mesh_clear()

arms = import_rest(out)
# Infer forward from named head and tail bones where available; otherwise leave source
# yaw unchanged and explicitly flag it for visual review.
bones = [(b.name, a.matrix_world @ b.head_local)
         for a in arms for b in a.data.bones]
heads = [p for name, p in bones if "head" in name.lower()]
tails = [p for name, p in bones if "tail" in name.lower()]
yaw = 0.0
orientation = "source_yaw_unverified"
if heads and tails:
    direction = sum(heads, Vector()) / len(heads) - sum(tails, Vector()) / len(tails)
    if math.hypot(direction.x, direction.y) > 1e-5:
        yaw = -math.pi / 2 - math.atan2(direction.y, direction.x)
        orientation = "head_tail_bone_inference_requires_visual_check"
rotation = Matrix.Rotation(yaw, 3, "Z")
rotated = [rotation @ p for p in points()]
assert rotated and all(math.isfinite(v) for p in rotated for v in p)
ground = min(p.z for p in rotated)
scene = doc["scenes"][doc.get("scene", 0)]
root = {"name": "DinoNormalization", "children": scene["nodes"],
        "rotation": [0, math.sin(yaw/2), 0, math.cos(yaw/2)],
        "translation": [0, -ground, 0]}
scene["nodes"] = [len(doc["nodes"])]
doc["nodes"].append(root)
write_glb(out, doc, chunks)

# Reimport the final asset, not just the source, for actual world-space rest bounds.
import_rest(out)
verts = list(points())
assert verts and all(math.isfinite(v) for p in verts for v in p)
lo = [min(p[i] for p in verts) for i in range(3)]
hi = [max(p[i] for p in verts) for i in range(3)]
height = hi[2] - lo[2]
assert height > 0 and abs(lo[2]) < max(1e-4, height * 1e-5), (lo, hi)
tris = 0
# Count triangles per mesh instance in the exported scene.
def visit(index):
    global tris
    node = doc["nodes"][index]
    if "mesh" in node:
        for primitive in doc["meshes"][node["mesh"]]["primitives"]:
            assert primitive.get("mode", 4) == 4
            n = doc["accessors"][primitive.get("indices", primitive["attributes"]["POSITION"])]["count"]
            assert n % 3 == 0
            tris += n // 3
    for child in node.get("children", []):
        visit(child)
for index in scene["nodes"]:
    visit(index)
assert tris > 0
metadata = {
    "file": "models/" + out.name,
    "bytes": out.stat().st_size,
    "triangleCount": tris,
    "height": round(height, 6),
    "bounds": {"min": [round(lo[0], 6), round(lo[2], 6), round(-hi[1], 6)],
               "max": [round(hi[0], 6), round(hi[2], 6), round(-lo[1], 6)]},
    "animations": clips,
    "sourceActions": source_actions,
    "orientation": orientation,
    "materialFixes": material_fixes,
}
out.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n")
print("DINO_METADATA", json.dumps(metadata))
