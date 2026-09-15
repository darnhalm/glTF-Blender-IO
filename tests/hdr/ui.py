# Run in a disposable GUI Blender process: --python ui.py -- PACKAGE_DIRECTORY RESULT_FILE
import sys, pathlib, bpy, addon_utils, traceback
package = pathlib.Path(sys.argv[-2])
addon_utils.disable('io_scene_gltf2', default_set=True)
for name in list(sys.modules):
    if name == 'io_scene_gltf2' or name.startswith('io_scene_gltf2.'):
        del sys.modules[name]
sys.path.insert(0, str(package))
addon_utils.enable('io_scene_gltf2', default_set=True)
bpy.context.scene.KTX2ExportProperties.enabled = True
bpy.context.scene.KTX2ExportProperties.generate_mipmaps = True
obj = bpy.context.active_object
material = bpy.data.materials.new('UI HDR sample')
material.use_nodes = True
image = bpy.data.images.new('UI EXR', width=16, height=16, float_buffer=True)
image.colorspace_settings.name = 'Linear Rec.709'
node = material.node_tree.nodes.new('ShaderNodeTexImage')
node.image = image
material.node_tree.links.new(node.outputs['Color'], material.node_tree.nodes.get('Principled BSDF').inputs['Base Color'])
obj.data.materials.clear()
obj.data.materials.append(material)
def check():
    try:
        area = next(a for window in bpy.context.window_manager.windows for a in window.screen.areas if a.type == 'FILE_BROWSER')
        operator = area.spaces.active.active_operator
        from io_scene_gltf2 import exporter_extension_layout_draw
        assert 'KTX2 Textures' in exporter_extension_layout_draw
        assert bpy.context.scene.KTX2ExportProperties.enabled
        assert bpy.context.scene.KTX2ExportProperties.generate_mipmaps
        assert operator.export_hdr
        assert len(operator.hdr_materials) == 1
        assert operator.hdr_materials[0].enabled
        assert operator.hdr_materials[0].material_name == 'UI HDR sample'
        pathlib.Path(sys.argv[-1]).write_text('PASS: standard glTF file browser draws HDR material selection with registered KTX textures and mipmaps')
    except Exception:
        pathlib.Path(sys.argv[-1]).write_text(traceback.format_exc())
    bpy.ops.wm.quit_blender()
def launch():
    bpy.ops.export_scene.gltf('INVOKE_DEFAULT', export_hdr=True, use_selection=True, use_active_scene=True)
    bpy.app.timers.register(check, first_interval=3)
bpy.app.timers.register(launch, first_interval=1)
