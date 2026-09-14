"""Run with Blender --background --factory-startup --python this_file -- ZIP OUTPUT_DIR."""
import json
import pathlib
import sys
import time
import struct
import subprocess
import bpy

zip_path, output_dir = map(pathlib.Path, sys.argv[sys.argv.index('--') + 1:])
output_dir.mkdir(parents=True, exist_ok=True)
# Load the actual fork archive in an isolated Blender process, without changing its installation.
import zipfile
import addon_utils
import shutil
import io_scene_gltf2 as installed_exporter
installed_folder = pathlib.Path(installed_exporter.__file__).parent
with zipfile.ZipFile(zip_path) as archive:
    archive.extractall(output_dir / 'package')
# Reproduce source-overlay installation: keep Blender's shipped geometry codecs.
for library in installed_folder.glob('libbf_intern_*'):
    if library.is_file():
        shutil.copy2(library, output_dir / 'package/io_scene_gltf2' / library.name)
addon_utils.disable('io_scene_gltf2', default_set=True)
for name in list(sys.modules):
    if name == 'io_scene_gltf2' or name.startswith('io_scene_gltf2.'):
        del sys.modules[name]
sys.path.insert(0, str(output_dir / 'package'))
addon_utils.enable('io_scene_gltf2', default_set=True)
import io_scene_gltf2
assert pathlib.Path(io_scene_gltf2.__file__).is_relative_to(output_dir / 'package')
from io_scene_gltf2.hdr.exporter import export_steps
from io_scene_gltf2.hdr.glb import unpack
from io_scene_gltf2.hdr.encoder import executable

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add()
obj = bpy.context.object
obj.location = (1, 2, 3)
obj.keyframe_insert(data_path='location', frame=1)
obj.location.x = 2
obj.keyframe_insert(data_path='location', frame=10)
bpy.context.scene.frame_set(1)
for polygon in obj.data.polygons:
    for loop, uv in zip(polygon.loop_indices, ((0, 0), (1, 0), (1, 1), (0, 1))):
        obj.data.uv_layers.active.data[loop].uv = uv
image = bpy.data.images.new('Linear HDR test', width=16, height=16, float_buffer=True)
# Asymmetric radiance catches clipping and flipped texture preparation.
values = [value for y in range(16) for x in range(16) for value in ([4, 0.25, 0.5, 1] if y >= 8 else [0.25, 0.5, 2, 1])]
image.colorspace_settings.name = 'Linear Rec.709'
image.pixels[:] = values
exr_scene = bpy.data.scenes.new('EXR settings')
exr_scene.render.image_settings.file_format = 'OPEN_EXR'
exr_scene.render.image_settings.color_depth = '32'
image.save_render(str(output_dir / 'original.exr'), scene=exr_scene)
bpy.data.scenes.remove(exr_scene)
bpy.data.images.remove(image)
image = bpy.data.images.load(str(output_dir / 'original.exr'))
print('SOURCE_COLORSPACE', image.colorspace_settings.name, 'PIXELS', list(image.pixels[:4]), flush=True)
original_pixels = list(image.pixels)
for unlit in (False, True):
    material = bpy.data.materials.new('Original Unlit' if unlit else 'Original Lit')
    material.use_nodes = True
    nodes = material.node_tree.nodes
    texture = nodes.new('ShaderNodeTexImage')
    texture.image = image
    output = nodes.get('Material Output')
    principled = nodes.get('Principled BSDF')
    material.node_tree.links.new(texture.outputs['Color'], output.inputs['Surface'] if unlit else principled.inputs['Base Color'])
    principled.inputs['Roughness'].default_value = 0.37
    principled.inputs['Metallic'].default_value = 0.2
    obj.data.materials.clear()
    obj.data.materials.append(material)
    material['user_note'] = 'must not leak when Custom Properties is off'
    before_counts = len(bpy.data.materials), len(bpy.data.images)
    path = output_dir / ('unlit.glb' if unlit else 'lit.glb')
    assert bpy.ops.export_scene.gltf(filepath=str(path), export_hdr=True, export_hdr_resolution='2048', use_selection=True, use_active_scene=True, export_yup=False, export_extras=False) == {'FINISHED'}
    doc, binary = unpack(path.read_bytes())
    m = doc['materials'][0]
    assert doc.get('animations'), 'Upstream animation export must remain enabled'
    mesh_node = next(n for n in doc['nodes'] if 'mesh' in n)
    assert mesh_node['translation'] == [1, 2, 3], mesh_node
    assert ('KHR_materials_unlit' in m.get('extensions', {})) == unlit, m
    if not unlit:
        assert abs(m['pbrMetallicRoughness']['roughnessFactor'] - 0.37) < 0.001
        assert abs(m['pbrMetallicRoughness']['metallicFactor'] - 0.2) < 0.001
    assert 'baseColorTexture' in m['pbrMetallicRoughness']
    assert 'user_note' not in m.get('extras', {})
    assert len(doc['extras']['HERITAGE3D_hdr_surface']['textures']) == 1
    binding = doc['extras']['HERITAGE3D_hdr_surface']['textures'][0]
    view = doc['bufferViews'][binding['bufferView']]
    ktx = output_dir / 'texture.ktx2'
    ktx.write_bytes(binary[view['byteOffset']:view['byteOffset'] + view['byteLength']])
    raw = output_dir / 'decoded.bin'
    if raw.exists():
        raw.unlink()
    subprocess.run([executable(), 'extract', '--transcode', 'rgba16f', '--raw', str(ktx), str(raw)], check=True)
    decoded = raw.read_bytes()
    assert struct.unpack_from('<4e', decoded, 0) == (4.0, 0.25, 0.5, 1.0)
    assert struct.unpack_from('<4e', decoded, 15 * 16 * 8) == (0.25, 0.5, 2.0, 1.0)
    assert all('bufferView' in i for i in doc['images'])
    assert obj.active_material == material and texture.image == image
    assert before_counts == (len(bpy.data.materials), len(bpy.data.images))
    assert original_pixels == list(image.pixels)
    # Cancellation between preparation/encoding steps leaves no output or orphan resources.
    cancelled = output_dir / 'cancelled.glb'
    steps = export_steps(bpy.context, cancelled, maximum=2048)
    next(steps)
    next(steps)
    steps.close()
    assert not cancelled.exists()
    assert before_counts == (len(bpy.data.materials), len(bpy.data.images))
    assert obj.active_material == material
# HDR disabled exports a standard GLB without an HDR attachment.
plain = output_dir / 'plain.glb'
assert bpy.ops.export_scene.gltf(filepath=str(plain), export_hdr=False, use_selection=True, use_active_scene=True) == {'FINISHED'}
plain_doc, _ = unpack(plain.read_bytes())
assert 'HERITAGE3D_hdr_surface' not in plain_doc.get('extras', {})
assert not hasattr(bpy.types.Material, 'heritage3d_hdr')
# Explicitly excluding all materials must fail when HDR is enabled.
try:
    list(export_steps(bpy.context, output_dir / 'empty.glb', chosen=[]))
    raise AssertionError('Empty HDR selection accepted')
except ValueError as error:
    assert 'Select 1–16' in str(error)
# Material selection in the standard export operator collection is respected.
second = material.copy()
obj.data.materials.append(second)
obj.data.polygons[0].material_index = 1
selected_path = output_dir / 'selected-material.glb'
assert bpy.ops.export_scene.gltf(filepath=str(selected_path), export_hdr=True,
    use_selection=True, use_active_scene=True, hdr_scanned=True, export_extras=True,
    hdr_materials=[dict(name="first", texture=image.name, error="", material_key=str(material.as_pointer()), material_name=material.name, enabled=True),
                   dict(name="second", texture=image.name, error="", material_key=str(second.as_pointer()), material_name=second.name, enabled=False)]) == {'FINISHED'}
selected_doc, _ = unpack(selected_path.read_bytes())
assert len(selected_doc['materials']) == 2
assert selected_doc['materials'][0]['extras']['user_note']
assert all('_heritage3d_hdr_export_id' not in m.get('extras', {}) for m in selected_doc['materials'])
assert len(selected_doc['extras']['HERITAGE3D_hdr_surface']['textures']) == 1
assert obj.data.materials[0] == material and obj.data.materials[1] == second
# Geometry compression remains available when overlaying the shipped exporter.
draco_path = output_dir / 'draco.glb'
assert bpy.ops.export_scene.gltf(filepath=str(draco_path), export_hdr=True,
    use_selection=True, use_active_scene=True, export_draco_mesh_compression_enable=True) == {'FINISHED'}
draco_doc, _ = unpack(draco_path.read_bytes())
assert 'KHR_draco_mesh_compression' in draco_doc['extensionsUsed']
assert len(draco_doc['extras']['HERITAGE3D_hdr_surface']['textures']) == 2
# Invalid color space fails before touching output or source.
image.colorspace_settings.name = 'Non-Color'
try:
    list(export_steps(bpy.context, output_dir / 'invalid.glb', chosen=[material]))
    raise AssertionError('Invalid color space accepted')
except ValueError as error:
    assert 'Linear Rec.709' in str(error)
assert not (output_dir / 'invalid.glb').exists()
print('HERITAGE3D_TEST_PASS: archive loading, Lit/Unlit, HDR samples, animation, transforms, material selection, extras, cancellation, standard export')
