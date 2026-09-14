# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
import platform
import shutil
import os
import tempfile
from pathlib import Path


def executable():
    root = Path(__file__).parent / 'vendor'
    manifest = json.loads((root / 'manifest.json').read_text())
    machine = {'aarch64': 'arm64', 'AMD64': 'x86_64'}.get(platform.machine(), platform.machine())
    if manifest['platform'] != f'{platform.system()}-{machine}':
        raise RuntimeError('Install the HERITAGE3D package for this operating system and architecture')
    for name, digest in manifest['sha256'].items():
        path = root / name
        if not path.is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'Encoder integrity check failed: {name}')
    # Keep installed extension files read-only; execute a verified copy in its user cache.
    import bpy
    identity = manifest['sha256'][manifest['executable']][:16]
    cache = Path(bpy.utils.user_resource('DATAFILES', path='heritage3d-hdr/encoder/' + identity, create=True))
    for name, digest in manifest['sha256'].items():
        destination = cache / name
        if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == digest:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=destination.parent)
        os.close(fd)
        try:
            shutil.copyfile(root / name, temporary)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    path = cache / manifest['executable']
    if platform.system() != 'Windows':
        path.chmod(path.stat().st_mode | 0o100)
    return str(path)



def command(exe, source, target, quality):
    args = [exe, 'create', '--format', 'R16G16B16A16_SFLOAT', '--encode', 'uastc-hdr-4x4',
            '--generate-mipmap', '--mipmap-filter', 'box', '--assign-tf', 'linear',
            '--assign-primaries', 'bt709', '--assign-texcoord-origin', 'top-left',
            '--threads', '2']
    if quality == 4:
        args.append('--uastc-hdr-ultra-quant')
    return args + [str(source), str(target)]
