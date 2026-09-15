"""Run with the same ZIP/output arguments as export.py; exercises the unified KTX integration."""
import runpy
from pathlib import Path
import bpy
import struct
state = runpy.run_path(str(Path(__file__).with_name('export.py')))
output_dir = state['output_dir']
obj = state['obj']
unpack = state['unpack']
props = bpy.context.scene.KTX2ExportProperties
props.enabled = True
props.generate_mipmaps = True
props.create_fallback = True
material = bpy.data.materials.new('Unified Lit')
material.use_nodes = True
obj.data.materials.clear()
obj.data.materials.append(material)
for face in obj.data.polygons:
    face.material_index = 0
nodes = material.node_tree.nodes
links = material.node_tree.links
principled = nodes.get('Principled BSDF')

def png(name, color):
    image = bpy.data.images.new(name, width=16, height=16, float_buffer=True)
    image.pixels[:] = list(color) * 256
    scene = bpy.data.scenes.new('PNG settings')
    scene.render.image_settings.file_format = 'PNG'
    image.save_render(str(output_dir / (name + '.png')), scene=scene)
    bpy.data.scenes.remove(scene)
    bpy.data.images.remove(image)
    return bpy.data.images.load(str(output_dir / (name + '.png')))

base = nodes.new('ShaderNodeTexImage')
base.image = png('base', (0.4, 0.2, 0.1, 1))
links.new(base.outputs['Color'], principled.inputs['Base Color'])
normal = nodes.new('ShaderNodeTexImage')
normal.image = png('normal', (0.5, 0.5, 1, 1))
normal.image.colorspace_settings.name = 'Non-Color'
normal_map = nodes.new('ShaderNodeNormalMap')
links.new(normal.outputs['Color'], normal_map.inputs['Color'])
links.new(normal_map.outputs['Normal'], principled.inputs['Normal'])
paths = []
for mode, mipmaps, fallback in [('ETC1S', True, False), ('UASTC', False, True), ('ASTC', True, True)]:
    props.basecolor.target_format = 'ASTC' if mode == 'ASTC' else 'BASISU'
    props.basecolor.basisu.compression_mode = mode if mode != 'ASTC' else 'UASTC'
    props.generate_mipmaps = mipmaps
    props.create_fallback = fallback
    props.normal.basisu.compression_mode = 'UASTC'
    path = output_dir / (mode + '.glb')
    assert bpy.ops.export_scene.gltf(filepath=str(path), use_selection=True, use_active_scene=True) == {'FINISHED'}
    doc, binary = unpack(path.read_bytes())
    textures = [t for t in doc['textures'] if 'KHR_texture_basisu' in t.get('extensions', {})]
    assert len(textures) == 2, doc
    for t in textures:
        assert ('source' in t) == fallback
        image = doc['images'][t['extensions']['KHR_texture_basisu']['source']]
        assert image['mimeType'] == 'image/ktx2'
        view = doc['bufferViews'][image['bufferView']]
        data = binary[view['byteOffset']:view['byteOffset'] + view['byteLength']]
        assert struct.unpack_from('<I', data, 40)[0] == (5 if mipmaps else 1)
    if mode != 'ASTC':
        paths.append(path)
# Mixing HDR Base Color and ordinary UASTC normals keeps a PNG HDR fallback.
props.basecolor.target_format = 'BASISU'
props.basecolor.basisu.compression_mode = 'UASTC'
props.generate_mipmaps = False
props.create_fallback = False
base.image = bpy.data.images.load(str(output_dir / 'original.exr'), check_existing=False)
base.image.colorspace_settings.name = 'Linear Rec.709'
mixed = output_dir / 'mixed.glb'
assert bpy.ops.export_scene.gltf(filepath=str(mixed), export_hdr=True, use_selection=True, use_active_scene=True) == {'FINISHED'}
doc, binary = unpack(mixed.read_bytes())
assert len(doc['extras']['HERITAGE3D_hdr_surface']['textures']) == 1
m = doc['materials'][0]
base_t = doc['textures'][m['pbrMetallicRoughness']['baseColorTexture']['index']]
normal_t = doc['textures'][m['normalTexture']['index']]
assert 'KHR_texture_basisu' not in base_t.get('extensions', {})
assert 'KHR_texture_basisu' in normal_t['extensions']
assert doc['images'][base_t['source']]['mimeType'] == 'image/png'
# Required Basis textures must decode with no PNG fallback to mask failures.
for path in paths:
    before = set(bpy.data.images)
    assert bpy.ops.import_scene.gltf(filepath=str(path)) == {'FINISHED'}
    imported = set(bpy.data.images) - before
    assert len(imported) >= 2
    assert all(i.size[0] == 16 and i.size[1] == 16 and i.has_data for i in imported)
print('UNIFIED_KTX_PASS: ETC1S/UASTC/ASTC export, mipmaps on/off, fallback on/off, normals, mixed HDR+KTX, Basis import')
# Preserve experimental environment export/import and finalize GLB synchronously.
world = bpy.data.worlds.new('Unified environment')
world.use_nodes = True
bpy.context.scene.world = world
env = world.node_tree.nodes.new('ShaderNodeTexEnvironment')
env.image = png('environment', (0.25, 0.4, 0.6, 1))
world.node_tree.links.new(env.outputs['Color'], world.node_tree.nodes.get('Background').inputs['Color'])
props.export_environment_map = True
props.envmap_resolution = '256'
props.envmap_mode = 'UASTC'
props.generate_mipmaps = True
props.enabled = False
bpy.ops.object.select_all(action='DESELECT')
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
environment = output_dir / 'environment.glb'
assert bpy.ops.export_scene.gltf(filepath=str(environment), use_selection=True, use_active_scene=True) == {'FINISHED'}
env_doc, env_bin = unpack(environment.read_bytes())
assert 'KHR_environment_map' in env_doc['extensions']
assert all('uri' not in i for i in env_doc['images'])
assert not any(i.get('uri', '').startswith('data:') for i in env_doc['images'])
assert bpy.ops.import_scene.gltf(filepath=str(environment)) == {'FINISHED'}
assert any(n.type == 'TEX_ENVIRONMENT' and n.image and n.image.has_data for n in bpy.context.scene.world.node_tree.nodes)
print('UNIFIED_ENVIRONMENT_PASS: embedded cubemap export and import')
