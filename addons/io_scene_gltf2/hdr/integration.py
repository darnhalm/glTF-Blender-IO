# SPDX-License-Identifier: GPL-3.0-or-later
"""Opt-in integration in the upstream export operator. No recursive bpy operator invocation."""
import time
import bpy
from bpy.props import BoolProperty, StringProperty
from .exporter import export_steps, source_node


class HDRMaterial(bpy.types.PropertyGroup):
    material_key: StringProperty()
    material_name: StringProperty()
    texture: StringProperty()
    enabled: BoolProperty(name='HDR', default=True)
    error: StringProperty()


def objects_for_export(operator, context):
    objects = list(context.scene.objects) if operator.use_active_scene else list({o for scene in bpy.data.scenes for o in scene.objects})
    if operator.use_selection:
        objects = [o for o in objects if o.select_get()]
    if operator.use_visible:
        objects = [o for o in objects if o.visible_get()]
    if operator.use_renderable:
        objects = [o for o in objects if not o.hide_render]
    collection = bpy.data.collections.get(operator.collection) if operator.collection else None
    if operator.use_active_collection:
        collection = context.view_layer.active_layer_collection.collection
    if collection:
        members = collection.all_objects if operator.use_active_collection_with_nested else collection.objects
        objects = [o for o in objects if o.name in members]
    return objects


def materials_for_export(operator, context):
    materials = set()
    for obj in objects_for_export(operator, context):
        if obj.type != 'MESH':
            continue
        materials.update(slot.material for slot in obj.material_slots if slot.material)
        # KHR_materials_variants keeps alternate materials on mesh metadata rather
        # than in the currently displayed object slots. Include them in the same
        # HDR candidate list so a layer can carry its own embedded EXR map.
        materials.update(item.material for item in getattr(obj.data, 'gltf2_variant_mesh_data', []) if item.material)
    return sorted(materials, key=lambda m: m.name)


def scan(operator, context):
    old = {r.material_key: r.enabled for r in operator.hdr_materials}
    operator.hdr_materials.clear()
    for material in materials_for_export(operator, context):
        row = operator.hdr_materials.add()
        row.material_key = str(material.as_pointer())
        row.material_name = material.name
        try:
            row.texture = source_node(material).image.name
            row.enabled = old.get(row.material_key, True)
        except ValueError as error:
            row.error = str(error)
            row.enabled = False
    operator.hdr_scanned = True


def draw(operator, context, layout):
    header, body = layout.panel('GLTF_export_hdr', default_closed=False)
    header.label(text='HERITAGE3D HDR')
    if body is None:
        return
    body.prop(operator, 'export_hdr')
    if not operator.export_hdr:
        return
    if operator.export_format != 'GLB':
        body.label(text='HDR embedding requires GLB format', icon='ERROR')
        return
    current = {str(m.as_pointer()) for m in materials_for_export(operator, context)}
    if not operator.hdr_scanned or current != {r.material_key for r in operator.hdr_materials}:
        scan(operator, context)
    if not operator.hdr_materials:
        body.label(text='No mesh materials in export selection', icon='INFO')
    for item in operator.hdr_materials:
        row = body.row()
        row.enabled = not bool(item.error)
        row.prop(item, 'enabled', text=item.material_name)
        row.label(text=item.texture if not item.error else 'Unavailable')
        if item.error:
            body.label(text=item.error, icon='INFO')
    body.prop(operator, 'export_hdr_resolution')
    body.prop(operator, 'export_hdr_quality')
    body.label(text='Private HDR attachment + embedded SDR fallback', icon='INFO')


def start(operator, context):
    if operator.export_format != 'GLB':
        operator.report({'ERROR'}, 'HDR requires GLB; choose GLB or disable Embed HDR textures')
        return {'CANCELLED'}
    if operator.export_use_gltfpack:
        operator.report({'ERROR'}, 'Disable gltfpack for HDR export: it can discard private attachments')
        return {'CANCELLED'}
    if operator.export_materials != 'EXPORT':
        operator.report({'ERROR'}, 'HDR export requires exporting materials')
        return {'CANCELLED'}
    if context.mode != 'OBJECT':
        operator.report({'ERROR'}, 'Switch to Object Mode before HDR export')
        return {'CANCELLED'}
    operator.check(context)
    if not operator.hdr_scanned:
        scan(operator, context)
    available = {str(m.as_pointer()): m for m in materials_for_export(operator, context)}
    chosen = [r for r in operator.hdr_materials if r.enabled]
    if any(r.material_key not in available for r in chosen):
        operator.report({'ERROR'}, 'Material selection changed; reopen the export window')
        return {'CANCELLED'}
    # Preserve every upstream export option; only the staging path and temporary UUID export differ.
    def export_original(path):
        original = operator.filepath, operator.export_extras, operator.will_save_settings
        if operator.will_save_settings:
            operator.save_settings(context)
        try:
            operator.filepath = str(path)
            operator.export_extras = True
            operator.will_save_settings = False
            return operator.execute_standard(context)
        finally:
            operator.filepath, operator.export_extras, operator.will_save_settings = original
    operator._hdr_steps = export_steps(context, operator.filepath,
        maximum=int(operator.export_hdr_resolution), quality=int(operator.export_hdr_quality),
        chosen=[available[r.material_key] for r in chosen],
        objects=objects_for_export(operator, context), export_callback=export_original,
        keep_extras=operator.export_extras)
    if bpy.app.background:
        try:
            for _ in operator._hdr_steps:
                time.sleep(0.05)
            return {'FINISHED'}
        except Exception as error:
            operator.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        finally:
            operator._hdr_steps.close()
    operator._hdr_timer = context.window_manager.event_timer_add(0.1, window=context.window)
    context.window_manager.modal_handler_add(operator)
    return {'RUNNING_MODAL'}


def finish(operator, context):
    if getattr(operator, '_hdr_steps', None) is not None:
        operator._hdr_steps.close()
        operator._hdr_steps = None
    if getattr(operator, '_hdr_timer', None) is not None:
        context.window_manager.event_timer_remove(operator._hdr_timer)
        operator._hdr_timer = None
    if context.workspace:
        context.workspace.status_text_set(None)


def modal(operator, context, event):
    if event.type == 'ESC':
        finish(operator, context)
        operator.report({'INFO'}, 'HDR export cancelled')
        return {'CANCELLED'}
    if event.type == 'TIMER':
        try:
            context.workspace.status_text_set(next(operator._hdr_steps))
        except StopIteration:
            finish(operator, context)
            operator.report({'INFO'}, 'GLB exported with embedded HDR textures')
            return {'FINISHED'}
        except Exception as error:
            finish(operator, context)
            operator.report({'ERROR'}, str(error))
            return {'CANCELLED'}
    return {'RUNNING_MODAL'}
