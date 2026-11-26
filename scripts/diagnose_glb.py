"""Run 1 ONLY. Never publish assets. Run once per species in a fresh Blender process.
The wrapped GLB reproduces fd80ea1 for comparison, not a proposed repair.
"""
import bpy
import json
import math
import pathlib
import struct
import sys
import traceback
from mathutils import Matrix, Vector

OUT = pathlib.Path(sys.argv[sys.argv.index('--') + 1]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {'species': OUT.name, 'blender': bpy.app.version_string, 'errors': [], 'materialWarnings': []}


def dump():
    (OUT / 'blender.json').write_text(json.dumps(REPORT, indent=2, allow_nan=False) + '\n')


def attempt(name, fn):
    try:
        REPORT[name] = fn()
        return REPORT[name]
    except Exception:
        REPORT['errors'].append({'stage': name, 'traceback': traceback.format_exc()})
        traceback.print_exc()
        return None
    finally:
        dump()


def bounds(points):
    if not points:
        return None
    if not all(math.isfinite(x) for p in points for x in p):
        raise ValueError('Non-finite vertex')
    lo = [min(p[i] for p in points) for i in range(3)]
    hi = [max(p[i] for p in points) for i in range(3)]
    return {'lo': lo, 'hi': hi, 'dimensionsXYZ': [b-a for a,b in zip(lo,hi)],
            'minZVertex': min(range(len(points)), key=lambda i: points[i][2])}


def parent_chain(o):
    chain = []
    while o.parent:
        o = o.parent
        chain.append(o.name)
    return chain


def material(m):
    if m is None:
        return None
    return {'name': m.name, 'diffuse': list(m.diffuse_color), 'use_nodes': m.use_nodes,
            'nodes': [{'name': n.name, 'type': n.type,
                       'image': n.image.name if n.type == 'TEX_IMAGE' and n.image else None,
                       'inputs': {s.name: list(s.default_value) if hasattr(s.default_value, '__len__') else s.default_value
                                  for s in n.inputs if hasattr(s, 'default_value') and s.name in ('Base Color','Color','Roughness','Metallic')}}
                      for n in m.node_tree.nodes] if m.node_tree else [],
            'links': [[l.from_node.name,l.from_socket.name,l.to_node.name,l.to_socket.name] for l in m.node_tree.links] if m.node_tree else []}


def weights(o):
    names = {b.name for mod in o.modifiers if mod.type == 'ARMATURE' and mod.object
             for b in mod.object.data.bones if b.use_deform}
    counts = []
    sums = []
    over = []
    for v in o.data.vertices:
        ws = [g.weight for g in v.groups if o.vertex_groups[g.group].name in names and g.weight > 0]
        counts.append(len(ws)); sums.append(sum(ws))
        if len(ws) > 4:
            over.append({'vertex': v.index, 'count': len(ws), 'sum': sum(ws)})
    return {'deformGroups': sorted(names), 'maxInfluences': max(counts, default=0),
            'overFour': over, 'zeroInfluenceCount': counts.count(0),
            'weightSumMin': min(sums, default=0), 'weightSumMax': max(sums, default=0)}


def mesh_info(o):
    m = o.data
    m.calc_loop_triangles()
    # Mesh.validate can mutate the mesh. Only operate on an unlinked copy.
    tmp = m.copy()
    try:
        changed = tmp.validate(verbose=True, clean_customdata=False)
        tmp.calc_loop_triangles()
        checked = {'changedByValidate': bool(changed), 'verticesAfter': len(tmp.vertices),
                   'trianglesAfter': len(tmp.loop_triangles), 'polygonsAfter': len(tmp.polygons)}
    finally:
        bpy.data.meshes.remove(tmp)
    return {'meshName': m.name, 'vertices': len(m.vertices), 'triangles': len(m.loop_triangles),
            'polygons': len(m.polygons), 'validationOnCopy': checked,
            'uvLayers': [{'name': uv.name, 'count': len(uv.data),
                          'nonFinite': sum(not all(math.isfinite(x) for x in item.uv) for item in uv.data)} for uv in m.uv_layers],
            'nonFiniteVertexNormals': sum(not all(math.isfinite(x) for x in v.normal) for v in m.vertices),
            'materialSlots': [material(slot.material) for slot in o.material_slots],
            'weights': weights(o)}


def inventory():
    bpy.context.view_layer.update()
    deps = bpy.context.evaluated_depsgraph_get()
    objects = []
    all_world = []
    owners = []
    for o in sorted(bpy.context.scene.objects, key=lambda x: x.name):
        entry = {'name': o.name, 'type': o.type, 'parentChain': parent_chain(o),
                 'parentType': o.parent_type, 'parentBone': o.parent_bone,
                 'hideViewport': o.hide_viewport, 'hideRender': o.hide_render,
                 'visible': o.visible_get(), 'matrixWorld': [list(r) for r in o.matrix_world],
                 'matrixLocal': [list(r) for r in o.matrix_local],
                 'modifiers': [{'name': m.name, 'type': m.type,
                                'armature': m.object.name if m.type == 'ARMATURE' and m.object else None} for m in o.modifiers]}
        if o.type == 'ARMATURE':
            entry['bones'] = [{'name': b.name, 'headWorld': list(o.matrix_world @ b.head_local),
                               'tailWorld': list(o.matrix_world @ b.tail_local), 'deform': b.use_deform,
                               'customShape': o.pose.bones[b.name].custom_shape.name if o.pose.bones[b.name].custom_shape else None}
                              for b in o.data.bones]
        if o.type == 'MESH':
            entry['mesh'] = mesh_info(o)
            ev = o.evaluated_get(deps)
            m = ev.to_mesh()
            try:
                local = [v.co.copy() for v in m.vertices]
                world = [ev.matrix_world @ p for p in local]
                entry['localBounds'] = bounds(local)
                entry['worldBounds'] = bounds(world)
                all_world.extend(world)
                owners.extend((o.name, i) for i in range(len(world)))
            finally:
                ev.to_mesh_clear()
        objects.append(entry)
    b = bounds(all_world)
    if b:
        name, index = owners[b['minZVertex']]
        b['minZOwner'] = {'object': name, 'evaluatedVertexIndex': index}
        x,y,z = b['dimensionsXYZ']
        b['nativeWidthHeightDepth'] = [x,z,y]  # Blender Z-up; depth is Blender Y.
    return {'objects': objects, 'allMeshBounds': b}


def rest_pose():
    arms = [o for o in bpy.context.scene.objects if o.type == 'ARMATURE']
    saved = [(a, a.data.pose_position) for a in arms]
    result = {}
    try:
        for mode in ('POSE', 'REST'):
            for a in arms:
                a.data.pose_position = mode
            result[mode] = inventory()
    finally:
        for a, mode in saved:
            a.data.pose_position = mode
        bpy.context.view_layer.update()
    return result


def read_glb(path):
    data = path.read_bytes()
    magic, version, size = struct.unpack_from('<4sII',data)
    assert (magic,version,size) == (b'glTF',2,len(data))
    n, kind = struct.unpack_from('<I4s',data,12)
    assert kind == b'JSON'
    return json.loads(data[20:20+n]),data[20+n:]


def write_comparison(path,doc,tail):
    # Diagnostic reproduction only. Never modify the raw GLB or publish this copy.
    body = json.dumps(doc,separators=(',',':')).encode()
    body += b' ' * (-len(body)%4)
    path.write_bytes(struct.pack('<4sII',b'glTF',2,20+len(body)+len(tail)) +
                     struct.pack('<I4s',len(body),b'JSON') + body + tail)


def export(path):
    # Exact baseline settings from fd80ea1; no meshes removed or excluded.
    for mat in bpy.data.materials:
        if not mat.use_nodes:
            rgba = tuple(mat.diffuse_color)
            mat.use_nodes = True
            bsdf = mat.node_tree.nodes.get('Principled BSDF')
            if bsdf is None or bsdf.type != 'BSDF_PRINCIPLED':
                nodes = mat.node_tree.nodes
                bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
                fallback = 'existing_node_by_type' if bsdf is not None else 'created_node'
                if bsdf is None:
                    bsdf = nodes.new('ShaderNodeBsdfPrincipled')
                    output = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output), None)
                    if output is None:
                        output = nodes.new('ShaderNodeOutputMaterial')
                    mat.node_tree.links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])
                warning = {'variant': path.stem, 'material': mat.name,
                           'message': 'Expected named Principled BSDF missing or wrong type',
                           'fallback': fallback}
                REPORT['materialWarnings'].append(warning)
                print('MATERIAL_WARNING', json.dumps(warning), flush=True)
            bsdf.inputs['Base Color'].default_value = rgba
            bsdf.inputs['Roughness'].default_value = 0.85
    REPORT[path.stem + 'PreExportRestPose'] = rest_pose()
    bpy.ops.export_scene.gltf(filepath=str(path), export_format='GLB', export_yup=True,
        export_animations=True, export_animation_mode='ACTIONS', export_force_sampling=True,
        export_frame_range=False, export_skins=True, export_cameras=False, export_lights=False,
        export_materials='EXPORT', export_all_vertex_colors=True, export_anim_single_armature=True)
    doc,_ = read_glb(path)
    (OUT / (path.stem + '.gltf.json')).write_text(json.dumps(doc,indent=2)+'\n')
    return {'clips': [a.get('name') for a in doc.get('animations',[])], 'nodes': len(doc.get('nodes',[]))}


def reimport(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(path))
    return rest_pose()


def clear_actions_rest():
    # Reproduce fd80ea1 import_rest exactly, after inventory of imported POSE.
    for o in bpy.context.scene.objects:
        if o.animation_data:
            o.animation_data.action = None
            for t in o.animation_data.nla_tracks:
                t.mute = True
        if o.type == 'ARMATURE':
            o.data.pose_position = 'REST'
    bpy.context.view_layer.update()
    return inventory()


def wrap():
    doc,tail = read_glb(OUT/'raw.glb')
    arms = [a for a in bpy.context.scene.objects if a.type == 'ARMATURE']
    bones = [(b.name,a.matrix_world @ b.head_local) for a in arms for b in a.data.bones]
    heads = [p for n,p in bones if 'head' in n.lower()]
    tails = [p for n,p in bones if 'tail' in n.lower()]
    yaw = 0.0
    if heads and tails:
        d = sum(heads,Vector())/len(heads) - sum(tails,Vector())/len(tails)
        if math.hypot(d.x,d.y)>1e-5:
            yaw = -math.pi/2-math.atan2(d.y,d.x)
    rotation = Matrix.Rotation(yaw,3,'Z')
    deps = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in bpy.context.scene.objects:
        if o.type == 'MESH':
            ev=o.evaluated_get(deps); m=ev.to_mesh()
            try: pts.extend(rotation @ (ev.matrix_world @ v.co) for v in m.vertices)
            finally: ev.to_mesh_clear()
    ground = min(p.z for p in pts)
    scene=doc['scenes'][doc.get('scene',0)]
    root={'name':'DinoNormalization','children':scene['nodes'],
          'rotation':[0,math.sin(yaw/2),0,math.cos(yaw/2)],'translation':[0,-ground,0]}
    scene['nodes']=[len(doc['nodes'])]; doc['nodes'].append(root)
    write_comparison(OUT/'wrapped.glb',doc,tail)
    (OUT/'wrapped.gltf.json').write_text(json.dumps(doc,indent=2)+'\n')
    return {'yawDegrees':math.degrees(yaw),'preWrapperGround':ground,'root':root}


def limit_weights():
    # Diagnostic variant only: work in fresh in-memory copy of the source file.
    result=[]
    for o in bpy.context.scene.objects:
        if o.type != 'MESH': continue
        names={b.name for m in o.modifiers if m.type=='ARMATURE' and m.object for b in m.object.data.bones if b.use_deform}
        if not names: continue
        o.data=o.data.copy()
        for v in o.data.vertices:
            ws=sorted([(g.group,g.weight) for g in v.groups if o.vertex_groups[g.group].name in names and g.weight>0], key=lambda x:(-x[1],x[0]))
            top=ws[:4]; total=sum(w for _,w in top)
            for idx,_ in ws[4:]: o.vertex_groups[idx].remove([v.index])
            if total:
                for idx,w in top: o.vertex_groups[idx].add([v.index],w/total,'REPLACE')
        result.append({'object':o.name,'after':weights(o)})
    return result


source = bpy.data.filepath
if not source or not pathlib.Path(source).is_file():
    raise RuntimeError('Expected a downloaded source blend file')
REPORT['source'] = source
REPORT['sourceActions'] = sorted(a.name for a in bpy.data.actions if a.fcurves)
attempt('sourceRestPose',rest_pose)
if attempt('rawExport',lambda:export(OUT/'raw.glb')) is not None:
    if attempt('rawReimport',lambda:reimport(OUT/'raw.glb')) is not None:
        if attempt('rawFd80Rest',clear_actions_rest) is not None and attempt('wrapper',wrap) is not None:
            if attempt('wrappedReimport',lambda:reimport(OUT/'wrapped.glb')) is not None:
                stage=attempt('wrappedFd80Rest',clear_actions_rest)
                if stage and stage['allMeshBounds']:
                    b=stage['allMeshBounds']; height=b['hi'][2]-b['lo'][2]; tol=max(1e-4,height*1e-5)
                    REPORT['originalAssertion']={'lo':b['lo'],'hi':b['hi'],'height':height,'tolerance':tol,
                                                 'pass':height>0 and abs(b['lo'][2])<tol}
                    if not REPORT['originalAssertion']['pass']:
                        REPORT['errors'].append({'stage':'originalAssertion','message':'Original assertion failed (diagnostic reproduction)'})
# Independent explicit four-weight diagnostic variant. No changes to source on disk.
def limited():
    bpy.ops.wm.open_mainfile(filepath=source, use_scripts=False)
    info=limit_weights()
    return {'weights':info,'export':export(OUT/'limited.glb')}
attempt('limitedExport',limited)
dump()
if REPORT['errors']:
    raise RuntimeError('Diagnostic failures recorded; workflow must continue other species')
