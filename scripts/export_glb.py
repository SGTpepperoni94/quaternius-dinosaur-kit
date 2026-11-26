"""Run 2 candidate: in-memory preparation only; Blender 4.2.8 CI runtime."""
import bpy
import json
import math
import pathlib
import struct
import sys
from mathutils import Matrix, Vector

assert bpy.app.version == (4, 2, 8), 'Wrong Blender version'
out = pathlib.Path(sys.argv[sys.argv.index('--') + 1]).resolve()
out.parent.mkdir(parents=True, exist_ok=True)
species = pathlib.Path(bpy.data.filepath).stem
scales = {'Apatosaurus':3, 'Parasaurolophus':1, 'Stegosaurus':2, 'TRex':3, 'Triceratops':2, 'Velociraptor':1}
expected = {species+'_'+x for x in ('Attack','Death','Idle','Jump','Run','Walk')}
scene = bpy.context.scene
arms = [o for o in scene.objects if o.type == 'ARMATURE']
assert len(arms) == 1
arm = arms[0]
meshes = [o for o in scene.objects if o.type == 'MESH']
assert meshes and all(any(m.type == 'ARMATURE' and m.object == arm for m in o.modifiers) for o in meshes)
S = arm.scale.x
assert abs(S-scales[species]) <= 1e-5 and all(abs(x-S)<=1e-5 for x in arm.scale)
assert max(abs(x) for x in arm.location)<=1e-5
assert all(abs(arm.matrix_world[i][j]-(S if i==j else 0))<=1e-5 for i in range(3) for j in range(3))
assert arm.parent is None

def active(o):
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    o.select_set(True)
    bpy.context.view_layer.objects.active = o


def bounds(objects):
    bpy.context.view_layer.update()
    deps = bpy.context.evaluated_depsgraph_get()
    pts=[]
    for o in objects:
        ev=o.evaluated_get(deps); m=ev.to_mesh()
        try: pts.extend(ev.matrix_world @ v.co for v in m.vertices)
        finally: ev.to_mesh_clear()
    assert pts and all(math.isfinite(x) for p in pts for x in p)
    return [[min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]]


def dims(bb):
    lo,hi=bb
    return [hi[i]-lo[i] for i in (0,2,1)]


def same_bounds(a,b,tol):
    assert all(abs(x-y)<=tol for p,q in zip(a,b) for x,y in zip(p,q)), 'REST bounds changed'

# Capture authored native REST dimensions before changing the rig.
arm.data.pose_position='REST'
native_rest=bounds(meshes)
arm.data.pose_position='POSE'
actions=[a for a in bpy.data.actions if a.fcurves]
if species=='Apatosaurus':
    assert bpy.data.actions.get('Stegosaurus_Death') and not bpy.data.actions.get('Apatosaurus_Death')
    bpy.data.actions['Stegosaurus_Death'].name='Apatosaurus_Death'
assert {a.name for a in actions}==expected and len(actions)==6
# Bake into separate actions so originals remain evaluable until all six are baked.
active(arm)
ad=arm.animation_data_create()
for t in ad.nla_tracks: t.mute=True
baked=[]
for original in actions:
    ad.action=None
    for bone in arm.pose.bones:
        bone.location=(0.0,0.0,0.0)
        bone.rotation_quaternion=(1.0,0.0,0.0,0.0)
        bone.rotation_euler=(0.0,0.0,0.0)
        bone.rotation_axis_angle=(0.0,0.0,1.0,0.0)
        bone.scale=(1.0,1.0,1.0)
    ad.action=original
    lo,hi=map(float,original.frame_range)
    assert hi>lo
    frame_start,frame_end=math.floor(lo),math.ceil(hi)
    scene.frame_set(frame_start)
    bpy.context.view_layer.update()
    bpy.ops.nla.bake(frame_start=frame_start,frame_end=frame_end,step=1,only_selected=False,
                     visual_keying=True,clear_constraints=False,clear_parents=False,
                     use_current_action=False,clean_curves=False,bake_types={'POSE'})
    new=ad.action
    assert new is not None and new!=original and new.fcurves
    new.use_frame_range=False
    new_lo,new_hi=map(float,new.frame_range)
    print(f'BAKE_RANGE {species} {original.name}: orig=({lo},{hi}) req=({frame_start},{frame_end}) baked=({new_lo},{new_hi})',flush=True)
    assert math.isfinite(new_lo) and math.isfinite(new_hi) and new_hi>new_lo, 'Baked action has invalid range'
    orig_span,baked_span=hi-lo,new_hi-new_lo
    assert abs(baked_span-orig_span)<=max(2.0,orig_span*0.10), 'Baked action duration drifted'
    assert new_lo>=frame_start-1.0 and new_hi<=frame_end+1.0, 'Baked action exceeded requested window'
    new.use_fake_user=True
    baked.append((original,new,original.name))
# All actions baked before clearing any constraints.
for p in arm.pose.bones:
    for c in list(p.constraints): p.constraints.remove(c)
ad.action=None
# Remove stale NLA references and old actions in memory only.
for t in list(ad.nla_tracks): ad.nla_tracks.remove(t)
for original,new,name in baked:
    bpy.data.actions.remove(original)
    new.name=name
    assert new.name==name
arm.data.pose_position='REST'
bpy.context.view_layer.update()
for o in meshes:
    world=o.matrix_world.copy()
    o.parent=None
    o.matrix_world=world
    active(o)
    bpy.ops.object.transform_apply(location=True,rotation=True,scale=True)
    bpy.context.view_layer.update()
    assert all(abs(o.matrix_world[i][j]-(1 if i==j else 0))<1e-5 for i in range(4) for j in range(4))
if abs(S-1)>1e-6:
    active(arm)
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
    for _,a,_ in baked:
        for fc in a.fcurves:
            if fc.data_path.startswith('pose.bones[') and fc.data_path.endswith('].location'):
                for k in fc.keyframe_points:
                    k.co.y*=S; k.handle_left.y*=S; k.handle_right.y*=S
                fc.update()
assert all(abs(x-1)<=1e-6 for x in arm.scale)
# Ground the mesh and skeleton together, without a GLB JSON wrapper or yaw rotation.
pre=bounds(meshes)
assert all(abs(x-y)<=max(1e-5,abs(y)*.02) for x,y in zip(dims(pre),dims(native_rest)))
delta=Vector((0,0,-pre[0][2]))
for o in meshes:
    o.data.transform(Matrix.Translation(delta)); o.data.update()
active(arm)
bpy.ops.object.mode_set(mode='EDIT')
# Disconnect temporarily to prevent connected-bone propagation while translating.
connected={b.name:b.use_connect for b in arm.data.edit_bones}
positions={b.name:(b.head.copy(),b.tail.copy()) for b in arm.data.edit_bones}
for b in arm.data.edit_bones: b.use_connect=False
for b in arm.data.edit_bones:
    h,t=positions[b.name]; b.head=h+delta; b.tail=t+delta
for b in arm.data.edit_bones: b.use_connect=connected[b.name]
bpy.ops.object.mode_set(mode='OBJECT')
assert abs(bounds(meshes)[0][2])<=.001

# E-046: deterministic deform-only weights, no live mesh validation/repair.
deform={b.name for b in arm.data.bones if b.use_deform}
def topology(m):
    m.calc_loop_triangles()
    return (len(m.vertices),len(m.edges),len(m.polygons),len(m.loop_triangles))
for o in meshes:
    m=o.data; before=topology(m); bb=bounds([o])
    names={g.index:g.name for g in o.vertex_groups}
    for v in m.vertices:
        entries=[(g.group,float(g.weight)) for g in v.groups]
        ws=[]
        for idx,w in entries:
            if names[idx] not in deform or not math.isfinite(w) or w<=0:
                o.vertex_groups[idx].remove([v.index])
            else: ws.append((idx,w))
        ws.sort(key=lambda x:(-x[1],x[0])); top=ws[:4]
        for idx,_ in ws[4:]: o.vertex_groups[idx].remove([v.index])
        total=sum(w for _,w in top)
        assert top and math.isfinite(total) and total>0
        for idx,w in top: o.vertex_groups[idx].add([v.index],min(1.,max(0.,w/total)),'REPLACE')
    m.update()
    assert topology(m)==before
    same_bounds(bb,bounds([o]),1e-6)
    for v in m.vertices:
        weights=[g.weight for g in v.groups]
        assert 1<=len(weights)<=4 and all(names[g.group] in deform for g in v.groups)
        assert all(math.isfinite(w) and 0<w<=1 for w in weights) and abs(sum(weights)-1)<=1e-5
    tmp=m.copy()
    try: changed=tmp.validate(verbose=True,clean_customdata=False)
    finally: bpy.data.meshes.remove(tmp)
    assert not changed,'Disposable mesh validation failed'

material_fixes=[]
for mat in {slot.material for o in meshes for slot in o.material_slots}:
    assert mat is not None
    rgba=tuple(mat.diffuse_color)
    had_nodes=mat.use_nodes
    mat.use_nodes=True
    nodes=mat.node_tree.nodes; links=mat.node_tree.links
    output=next((n for n in nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output),None)
    if output is None: output=nodes.new('ShaderNodeOutputMaterial')
    surface=output.inputs['Surface']
    existing=surface.links[0].from_node if surface.is_linked else None
    if had_nodes and existing is not None and existing.type=='BSDF_PRINCIPLED': continue
    if existing is not None and existing.type=='BSDF_DIFFUSE':
        assert not existing.inputs['Color'].is_linked, 'Unexpected linked diffuse colour'
        rgba=tuple(existing.inputs['Color'].default_value)
    bsdf=nodes.new('ShaderNodeBsdfPrincipled')
    bsdf.inputs['Base Color'].default_value=rgba
    bsdf.inputs['Metallic'].default_value=0
    bsdf.inputs['Roughness'].default_value=.85
    for link in list(surface.links): links.remove(link)
    links.new(bsdf.outputs['BSDF'],surface)
    material_fixes.append(mat.name)

source_mesh_count=len(meshes)
source_triangles=sum(topology(o.data)[3] for o in meshes)
arm.data.pose_position='POSE'
ad.action=None
for p in arm.pose.bones: p.matrix_basis=Matrix.Identity(4)
bpy.context.view_layer.update()
bpy.ops.export_scene.gltf(filepath=str(out),export_format='GLB',export_yup=True,
    export_animations=True,export_animation_mode='ACTIONS',export_force_sampling=True,
    export_frame_range=False,export_skins=True,export_cameras=False,export_lights=False,
    export_materials='EXPORT',export_all_vertex_colors=True,export_anim_single_armature=True)
data=out.read_bytes(); magic,ver,length=struct.unpack_from('<4sII',data)
assert (magic,ver,length)==(b'glTF',2,len(data))
n,kind=struct.unpack_from('<I4s',data,12); assert kind==b'JSON'
doc=json.loads(data[20:20+n]); clips=[a['name'] for a in doc.get('animations',[])]
assert len(clips)==6 and set(clips)==expected
assert doc.get('skins') and not any('uri' in b for b in doc.get('buffers',[])) and not any('uri' in i for i in doc.get('images',[]))
roots=set(doc['scenes'][doc.get('scene',0)]['nodes'])
skinned=[i for i,n in enumerate(doc['nodes']) if 'skin' in n and 'mesh' in n]
assert len(skinned)==source_mesh_count and all(i in roots for i in skinned)
assert not any(n.get('name')=='DinoNormalization' for n in doc['nodes'])
tris=0
for i in skinned:
    for p in doc['meshes'][doc['nodes'][i]['mesh']]['primitives']:
        assert p.get('mode',4)==4 and {'JOINTS_0','WEIGHTS_0'}<=p['attributes'].keys()
        assert 'JOINTS_1' not in p['attributes'] and 'WEIGHTS_1' not in p['attributes']
        count=doc['accessors'][p.get('indices',p['attributes']['POSITION'])]['count']; assert count%3==0
        tris+=count//3
assert tris==source_triangles

# Re-import only: ignore importer custom shapes, never delete or name-filter meshes.
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(out))
objects=list(bpy.context.scene.objects)
for o in objects:
    if o.animation_data:
        o.animation_data.action=None
        for t in o.animation_data.nla_tracks: t.mute=True
    if o.type=='ARMATURE': o.data.pose_position='REST'
bpy.context.view_layer.update()
helpers={p.custom_shape for o in objects if o.type=='ARMATURE' for p in o.pose.bones if p.custom_shape}
actual=[o for o in objects if o.type=='MESH' and o.visible_get() and not o.hide_viewport and o not in helpers and len(o.vertex_groups)>0 and any(m.type=='ARMATURE' for m in o.modifiers)]
assert len(actual)==source_mesh_count
bb=bounds(actual); lo,hi=bb
assert abs(lo[2])<=.001
assert all(abs(x-y)<=abs(y)*.02 for x,y in zip(dims(bb),dims(native_rest)))
metadata={'file':'models/'+out.name,'triangleCount':tris,'nativeWidthHeightDepth':dims(bb),
          'sourceNativeWidthHeightDepth':dims(native_rest),'height':hi[2]-lo[2],
          'bounds':{'min':[lo[0],lo[2],-hi[1]],'max':[hi[0],hi[2],-lo[1]]},
          'animations':clips,'sourceActions':sorted(expected),'materialFixes':material_fixes}
out.with_suffix('.json').write_text(json.dumps(metadata,indent=2,allow_nan=False)+'\n')
