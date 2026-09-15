#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build offline macOS arm64 fork archive; no binary downloads during user exports."""
import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--ktx-prefix', required=True, type=Path)
parser.add_argument('--legacy-tools', required=True, type=Path)
parser.add_argument('--output', type=Path, default=root / 'dist/glTF-Blender-IO-5.2-KTX-HDR-macos-arm64.zip')
args = parser.parse_args()
if (platform.system(), platform.machine()) != ('Darwin', 'arm64'):
    raise SystemExit('Only macOS arm64 preview packaging is validated')
with tempfile.TemporaryDirectory() as temporary:
    stage = Path(temporary)
    addon = stage / 'io_scene_gltf2'
    shutil.copytree(root / 'addons/io_scene_gltf2', addon,
                    ignore=shutil.ignore_patterns('__pycache__', 'vendor', '.*'))
    vendor = addon / 'hdr/vendor'
    for relative in ('bin/ktx', 'lib/libktx.5.dylib'):
        destination = vendor / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.ktx_prefix / relative, destination)
    shutil.copy2(args.legacy_tools / 'toktx', vendor / 'bin/toktx')
    shutil.copy2(args.legacy_tools / 'libktx.4.dylib', vendor / 'lib/libktx.4.dylib')
    legacy_version = subprocess.check_output([str(vendor / 'bin/toktx'), '--version'], text=True, stderr=subprocess.STDOUT).strip()
    if '4.4.2' not in legacy_version:
        raise SystemExit(f'Expected toktx 4.4.2, got {legacy_version}')
    version = subprocess.check_output([str(vendor / 'bin/ktx'), '--version'], text=True).strip()
    if '5.0.0' not in version or 'rc2' not in version:
        raise SystemExit(f'Expected KTX 5.0.0-rc2, got {version}')
    manifest = dict(platform='Darwin-arm64', version=version, legacy_version=legacy_version,
        legacy_source='https://github.com/KhronosGroup/KTX-Software/releases/tag/v4.4.2',
        source='https://github.com/KhronosGroup/KTX-Software/releases/tag/v5.0.0-rc2',
        executable='bin/ktx', sha256={str(p.relative_to(vendor)): hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in sorted(vendor.rglob('*')) if p.is_file()})
    (vendor / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for name in ['LICENSE.txt', 'AUTHORS.txt', 'HDR-EXPORT.md']:
        shutil.copy2(root / name, stage / name)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(stage.rglob('*')):
            if p.is_file():
                archive.write(p, p.relative_to(stage))
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_suffix('.zip.sha256').write_text(f'{digest}  {args.output.name}\n')
    print(args.output)
