# SPDX-License-Identifier: GPL-3.0-or-later
import os
import struct
import subprocess
import tempfile
import time
import uuid
import zlib
from pathlib import Path

import bpy
import numpy as np

from .encoder import command, executable
from .glb import LIMIT, MARKER, embed, mip_bytes


def source_node(material):
    if not material.use_nodes:
        raise ValueError(f'{material.name}: a node material is required')
    outputs = [n for n in material.node_tree.nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output]
    if not outputs or not outputs[0].inputs['Surface'].is_linked:
        raise ValueError(f'{material.name}: connect a material output')
    socket = outputs[0].inputs['Surface']
    node = socket.links[0].from_node
    if node.type == 'BSDF_PRINCIPLED':
        socket = node.inputs['Base Color']
    elif node.type == 'MIX_SHADER':
        # Official glTF shadeless setup recognized by the upstream exporter:
        # Is Camera Ray -> Mix factor, Transparent -> input 1, Emission -> input 2.
        factor = node.inputs[0]
        shader_1 = node.inputs[1]
        shader_2 = node.inputs[2]
        factor_node = factor.links[0].from_node if factor.is_linked else None
        factor_output = factor.links[0].from_socket if factor.is_linked else None
        transparent = shader_1.links[0].from_node if shader_1.is_linked else None
        emission = shader_2.links[0].from_node if shader_2.is_linked else None
        if (factor_node is None or factor_node.type != 'LIGHT_PATH' or
                factor_output.name != 'Is Camera Ray' or
                transparent is None or transparent.type != 'BSDF_TRANSPARENT' or
                emission is None or emission.type != 'EMISSION'):
            raise ValueError(f'{material.name}: use the official glTF Unlit Camera Ray node setup')
        socket = emission.inputs['Color']
    elif node.type == 'BACKGROUND':
        if node.inputs['Strength'].is_linked or node.inputs['Strength'].default_value != 1:
            raise ValueError(f'{material.name}: Unlit Background strength must be 1')
        socket = node.inputs['Color']
    elif node.type != 'TEX_IMAGE':
        raise ValueError(f'{material.name}: connect Image to Principled Base Color or use the official glTF Unlit Camera Ray setup')
    if not socket.is_linked:
        raise ValueError(f'{material.name}: connect an EXR to Base Color')
    link = socket.links[0]
    if link.from_node.type != 'TEX_IMAGE' or link.from_socket.name != 'Color':
        raise ValueError(f'{material.name}: v0.1 requires a direct image Color connection')
    node = link.from_node
    image = node.image
    if not image or image.source not in {'FILE', 'GENERATED'} or not image.is_float:
        raise ValueError(f'{material.name}: a floating-point EXR image is required')
    if image.source == 'FILE' and image.file_format not in {'OPEN_EXR', 'OPEN_EXR_MULTILAYER'}:
        raise ValueError(f'{material.name}: expected EXR')
    if image.file_format == 'OPEN_EXR_MULTILAYER':
        raise ValueError(f'{material.name}: multilayer EXR is not supported yet')
    if image.colorspace_settings.name not in {'Linear Rec.709', 'Linear', 'Linear BT.709'}:
        raise ValueError(f'{material.name}: expected Linear Rec.709; convert other color spaces before export')
    if node.projection != 'FLAT' or image.size[0] < 1 or image.size[1] < 1:
        raise ValueError(f'{material.name}: a loaded 2D image with flat projection is required')
    return node


def dimensions(image, maximum):
    w, h = image.size
    scale = min(1, maximum / max(w, h)) if maximum else 1
    return max(1, int(w * scale)), max(1, int(h * scale))


def png(path, pixels):
    # Blender pixels are bottom-up linear RGBA; PNG rows are top-down sRGB.
    rgb = pixels[:, :, :3]
    luminance = rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    rgb = rgb / (1 + luminance[:, :, None])
    rgb /= np.maximum(1, rgb.max(axis=2, keepdims=True))
    rgb = np.where(rgb <= 0.0031308, 12.92 * rgb, 1.055 * np.power(rgb, 1 / 2.4) - 0.055)
    rgba = np.concatenate((rgb, pixels[:, :, 3:4]), axis=2)
    rows = np.rint(np.clip(rgba[::-1], 0, 1) * 255).astype(np.uint8)
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    data = b''.join(b'\0' + row.tobytes() for row in rows)
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>2I5B', rows.shape[1], rows.shape[0], 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(data)) + chunk(b'IEND', b''))


def export_materials(context, selected=True):
    objects = [o for o in context.scene.objects if not selected or o.select_get()]
    return sorted({slot.material for obj in objects if obj.type == 'MESH'
                   for slot in obj.material_slots if slot.material}, key=lambda m: m.name)


def export_steps(context, target, selected=True, maximum=4096, quality=2, chosen=None, hdr=True, objects=None, export_callback=None, keep_extras=True):
    """Main-thread generator. Encoder runs as a cancellable child process between yields."""
    target = Path(target).expanduser().resolve()
    if target.suffix.lower() != '.glb':
        raise ValueError('Choose a .glb output file')
    objects = list(objects) if objects is not None else list(context.selected_objects if selected else context.scene.objects)
    available = {slot.material for obj in objects if obj.type == 'MESH' for slot in obj.material_slots if slot.material}
    available.update(item.material for obj in objects if obj.type == 'MESH'
                     for item in getattr(obj.data, 'gltf2_variant_mesh_data', []) if item.material)
    if not hdr:
        materials = []
    elif chosen is None:
        materials = []
        for material in available:
            try:
                source_node(material)
                materials.append(material)
            except ValueError:
                pass
    else:
        materials = list(chosen)
        if any(m not in available for m in materials):
            raise ValueError('An HDR material is outside the current export selection')
    if hdr and not 1 <= len(materials) <= 16:
        raise ValueError('Select 1–16 eligible HDR materials in the export settings')
    exe = executable() if materials else None
    nodes = {m: source_node(m) for m in materials}
    sizes = {m: dimensions(nodes[m].image, maximum) for m in materials}
    if any(max(s) > 8192 for s in sizes.values()) or sum(mip_bytes(*s) for s in sizes.values()) > LIMIT:
        raise ValueError('Textures exceed the 256 MiB HDR budget. Reduce resolution or material count')
    copies, images, slots, variant_materials = [], [], [], []
    process = None
    with tempfile.TemporaryDirectory(prefix='heritage3d-hdr-') as tmp:
        root = Path(tmp)
        try:
            attachments, replacements, names = {}, {}, {}
            for i, material in enumerate(materials):
                yield f'Preparing {material.name}'
                image = nodes[material].image.copy()
                images.append(image)
                w, h = sizes[material]
                if tuple(image.size) != (w, h):
                    image.scale(w, h)
                pixels = np.empty(w * h * 4, dtype=np.float32)
                image.pixels.foreach_get(pixels)
                pixels = pixels.reshape(h, w, 4)
                if not np.isfinite(pixels).all() or (pixels < 0).any() or (pixels[:, :, :3] > 65504).any() or (pixels[:, :, 3] > 1).any():
                    raise ValueError(f'{material.name}: expected finite nonnegative RGB ≤65504 and alpha in [0,1]')
                exr, fallback, ktx = root / f'{i}.exr', root / f'{i}.png', root / f'{i}.ktx2'
                exr_scene = bpy.data.scenes.new('HERITAGE3D EXR settings')
                try:
                    exr_scene.render.image_settings.file_format = 'OPEN_EXR'
                    exr_scene.render.image_settings.color_depth = '32'
                    exr_scene.render.image_settings.color_mode = 'RGBA'
                    image.save_render(str(exr), scene=exr_scene)
                finally:
                    bpy.data.scenes.remove(exr_scene)
                png(fallback, pixels)
                del pixels
                images.remove(image)
                bpy.data.images.remove(image)
                with (root / f'{i}.log').open('w+b') as log:
                    process = subprocess.Popen(command(exe, exr, ktx, quality), stdout=log, stderr=subprocess.STDOUT)
                    started = time.monotonic()
                    while process.poll() is None:
                        if time.monotonic() - started > 1800:
                            raise RuntimeError('HDR encoder timed out after 30 minutes')
                        yield f'Encoding {material.name} — Esc to cancel'
                    if process.returncode:
                        log.seek(0)
                        raise RuntimeError(log.read()[-3000:].decode('utf8', errors='replace'))
                    process = None
                key = uuid.uuid4().hex
                attachments[key] = ktx.read_bytes()
                names[key] = material.name
                copy = material.copy()
                copies.append(copy)
                copy[MARKER] = key
                fallback_image = bpy.data.images.load(str(fallback), check_existing=False)
                images.append(fallback_image)
                fallback_image['_heritage3d_hdr_fallback'] = True
                # Replace only this node in a copied material; other maps and links survive.
                copy.node_tree.nodes[nodes[material].name].image = fallback_image
                replacements[material] = copy
            yield 'Exporting GLB'
            # No source mutation while encoder runs. Temporary slot changes are synchronous and restored.
            try:
                for obj in objects:
                    if obj.type != 'MESH':
                        continue
                    for slot in obj.material_slots:
                        if slot.material in replacements:
                            slots.append((slot, slot.material))
                            slot.material = replacements[slot.material]
                    # Variant materials are gathered from this metadata after the
                    # exporter resets visible slots to their original materials.
                    # Stage the same marked fallback copy here and restore it below.
                    for item in getattr(obj.data, 'gltf2_variant_mesh_data', []):
                        if item.material in replacements:
                            variant_materials.append((item, item.material))
                            item.material = replacements[item.material]
                intermediate = root / 'model.glb'
                result = export_callback(intermediate) if export_callback else bpy.ops.export_scene.gltf(
                    filepath=str(intermediate), export_format='GLB', use_selection=selected,
                    export_extras=True, export_image_format='AUTO')
                if 'FINISHED' not in result:
                    raise RuntimeError('Blender glTF export did not finish')
            finally:
                for item, material in reversed(variant_materials):
                    item.material = material
                variant_materials.clear()
                for slot, material in reversed(slots):
                    slot.material = material
                slots.clear()
            packed = embed(intermediate.read_bytes(), attachments, names, keep_extras) if hdr else intermediate.read_bytes()
            # Atomic replacement after complete validation, on the destination filesystem.
            fd, temporary = tempfile.mkstemp(prefix='.heritage3d-', suffix='.glb', dir=target.parent)
            try:
                with os.fdopen(fd, 'wb') as output:
                    output.write(packed)
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for slot, material in reversed(slots):
                slot.material = material
            for item, material in reversed(variant_materials):
                item.material = material
            for material in copies:
                bpy.data.materials.remove(material)
            for image in images:
                bpy.data.images.remove(image)
