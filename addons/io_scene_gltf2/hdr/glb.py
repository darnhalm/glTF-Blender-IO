# SPDX-License-Identifier: GPL-3.0-or-later
"""Portable private HDR attachment writer. No Blender or Node dependency."""
import json
import struct

LIMIT = 256 * 1024 * 1024
MARKER = '_heritage3d_hdr_export_id'


def unpack(data):
    if len(data) < 28 or struct.unpack_from('<III', data) != (0x46546C67, 2, len(data)):
        raise ValueError('Expected GLB 2.0')
    length, kind = struct.unpack_from('<II', data, 12)
    end = 20 + length
    if kind != 0x4E4F534A or end + 8 > len(data):
        raise ValueError('Invalid GLB JSON')
    document = json.loads(data[20:end])
    size, kind = struct.unpack_from('<II', data, end)
    if kind != 0x004E4942 or end + 8 + size != len(data):
        raise ValueError('Expected one BIN chunk')
    if len(document.get('buffers', [])) != 1 or 'uri' in document['buffers'][0] or any('uri' in i for i in document.get('images', [])):
        raise ValueError('All resources must be embedded')
    return document, bytearray(data[end + 8:])


def mip_bytes(width, height):
    total = 0
    while True:
        total += width * height * 8
        if width == height == 1:
            return total
        width, height = max(1, width // 2), max(1, height // 2)


def ktx_dimensions(data):
    if len(data) < 104 or data[:12] != b'\xabKTX 20\xbb\r\n\x1a\n':
        raise ValueError('Expected KTX2')
    width, height, depth, layers, faces, levels = struct.unpack_from('<6I', data, 20)
    dfd, dfd_size = struct.unpack_from('<II', data, 48)
    if not (0 < width <= 8192 and 0 < height <= 8192) or depth or layers or faces != 1:
        raise ValueError('Expected a 2D HDR texture, at most 8192 per axis')
    if levels != max(width, height).bit_length() or 80 + levels * 24 > len(data):
        raise ValueError('A full mip chain is required')
    if dfd_size < 16 or dfd + dfd_size > len(data) or data[dfd + 12:dfd + 15] not in (bytes((167, 1, 1)), bytes((168, 1, 1))):
        raise ValueError('Expected linear BT.709 UASTC HDR')
    for level in range(levels):
        offset, size = struct.unpack_from('<QQ', data, 80 + 24 * level)
        if not size or offset + size > len(data):
            raise ValueError('Truncated KTX mip level')
    return width, height


def embed(data, attachments, names=None, keep_extras=True):
    """attachments maps temporary material UUIDs to KTX bytes. Preserve all other material fields."""
    doc, binary = unpack(data)
    if not attachments or len(attachments) > 16:
        raise ValueError('Select between 1 and 16 HDR materials')
    if 'HERITAGE3D_hdr_surface' in doc.get('extras', {}):
        raise ValueError('GLB already has HDR attachments')
    bindings, found, budget = [], set(), 0
    for index, material in enumerate(doc.get('materials', [])):
        extras = material.get('extras', {})
        key = extras.pop(MARKER, None)
        if key not in attachments:
            continue
        if key in found or 'baseColorTexture' not in material.get('pbrMetallicRoughness', {}):
            raise ValueError('HDR material must export one Base Color texture')
        found.add(key)
        if names and key in names:
            material['name'] = names[key]
        ktx = attachments[key]
        w, h = ktx_dimensions(ktx)
        budget += mip_bytes(w, h)
        if budget > LIMIT:
            raise ValueError('HDR textures exceed the 256 MiB float16 fallback budget')
        view = len(doc['bufferViews'])
        doc['bufferViews'].append(dict(buffer=0, byteOffset=len(binary), byteLength=len(ktx)))
        binary.extend(ktx)
        binary.extend(b'\0' * (-len(binary) % 4))
        bindings.append(dict(material=index, bufferView=view, width=w, height=h))
    if found != set(attachments):
        raise ValueError('Some HDR materials were not exported; check selection and material usage')
    if not keep_extras:
        def clear_extras(value):
            if isinstance(value, dict):
                value.pop('extras', None)
                for child in value.values():
                    clear_extras(child)
            elif isinstance(value, list):
                for child in value:
                    clear_extras(child)
        clear_extras(doc)
    doc.setdefault('extras', {})['HERITAGE3D_hdr_surface'] = dict(
        version=1, format='uastc-hdr-ktx2', colorSpace='linear-srgb', range='hdr', textures=bindings)
    doc['buffers'][0]['byteLength'] = len(binary)
    encoded = json.dumps(doc, ensure_ascii=False, separators=(',', ':')).encode('utf8')
    encoded += b' ' * (-len(encoded) % 4)
    return struct.pack('<5I', 0x46546C67, 2, 28 + len(encoded) + len(binary), len(encoded), 0x4E4F534A) + encoded + struct.pack('<II', len(binary), 0x004E4942) + binary
