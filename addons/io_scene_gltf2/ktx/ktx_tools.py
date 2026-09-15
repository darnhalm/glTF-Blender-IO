# Copyright 2024 The glTF-Blender-IO authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unified offline KTX runtime; encoding options retained from glTF-KTX-texture."""
import os
import subprocess
from pathlib import Path
from ..hdr.encoder import executable


def get_tool_path(name):
    try:
        path = Path(executable()).parent / name
        if path.is_file():
            path.chmod(path.stat().st_mode | 0o100)
            return path
    except (OSError, ValueError, RuntimeError):
        pass
    return None


def are_tools_installed():
    return get_tool_path('toktx') is not None


def install_tools(progress_callback=None):
    return (True, None) if are_tools_installed() else (False, 'Reinstall the complete platform package; bundled tools are missing or invalid')


def get_tool_environment():
    return os.environ.copy()


def run_toktx(input_path, output_path, options=None):
    """
    Run the toktx tool to convert an image to KTX2.

    Args:
        input_path: Path to input image (PNG, JPEG, etc.)
        output_path: Path for output KTX2 file
        options: Dict of options:
            - target_format: 'BASISU' or 'ASTC'
            - format: 'ETC1S' or 'UASTC' (for BASISU)
            - quality: 1-255 for ETC1S, 0-4 for UASTC
            - compression: 0-5 for ETC1S, 1-22 for UASTC
            - mipmaps: bool
            - astc_block_size: '4x4', '5x5', '6x6', '8x8' (for ASTC)
            - oetf: Transfer function (linear|srgb)
            - target_type: Target type (R, RG, RGB, RGBA)
            - normal_mode: Encode as a normal map (toktx --normal_mode)
            - normal_two_channel: Store normals as 2-component X+Y (needs shader
              Z-reconstruction); when False a standard 3-channel map is kept

    Returns:
        tuple: (success: bool, error_message: str or None)

    Notes on target formats:
        - BASISU: Basis Universal (ETC1S or UASTC) - universal, transcodes at runtime
                  to any GPU format (BC7, ASTC, ETC2, etc.)
        - ASTC: Native ASTC format - direct GPU upload on ASTC-capable hardware
                (mobile devices, Apple Silicon). No transcoding needed.
    """
    toktx_path = get_tool_path('toktx')
    if not toktx_path:
        return False, "toktx tool not found. Please install KTX tools first."

    options = options or {}

    cmd = [str(toktx_path)]

    target_format = options.get('target_format', 'BASISU')

    if target_format == 'ASTC':
        # Native ASTC compression - direct GPU upload on ASTC hardware
        cmd.extend(['--encode', 'astc'])
        block_size = options.get('astc_block_size', '6x6')
        cmd.extend(['--astc_blk_d', block_size])
        cmd.extend(['--astc_quality', 'medium'])

        compression = options.get('compression', 3)
        if compression > 0:
            cmd.extend(['--zcmp', str(compression)])
    else:
        # Basis Universal (ETC1S or UASTC) - universal format
        # Can be transcoded to BC7, ASTC, ETC2, etc. at runtime
        fmt = options.get('format', 'ETC1S')
        if fmt == 'UASTC':
            cmd.extend(['--encode', 'uastc'])
            quality = options.get('quality', 2)
            cmd.extend(['--uastc_quality', str(quality)])

            compression = options.get('compression', 3)
            if compression > 0:
                cmd.extend(['--zcmp', str(compression)])

            rdo = options.get('rdo', 0)
            if rdo > 0:
                cmd.extend(['--uastc_rdo_l', str(rdo)])
        else:
            # ETC1S (default)
            cmd.extend(['--encode', 'etc1s'])
            quality = options.get('quality', 128)
            cmd.extend(['--qlevel', str(quality)])

            compression = options.get('compression', 1)
            if compression > 0:
                cmd.extend(['--clevel', str(compression)])
    
    # Normal map mode - tunes the encoder for normal maps (requires linear input)
    normal_mode = options.get('normal_mode', False)
    normal_two_channel = options.get('normal_two_channel', False)
    if normal_mode:
        cmd.append('--normal_mode')

    # Transfer function
    oetf = options.get('oetf', 'srgb')
    cmd.extend(['--assign_oetf', oetf])

    # Target type
    if normal_mode and normal_two_channel:
        # Let toktx store its optimized 2-component X+Y normal map (RGB=X, A=Y).
        # Forcing --target_type would drop the Y component, so omit it here.
        pass
    else:
        if normal_mode:
            # Keep a standard 3-channel normal map: rgb1 prevents the default
            # 2-component conversion while still applying the normal-tuned encoder.
            cmd.extend(['--input_swizzle', 'rgb1'])
        target_type = options.get('target_type', 'RGBA')
        cmd.extend(['--target_type', target_type])

    # Scale
    scale = options.get('scale', 1.0)
    cmd.extend(['--scale', str(scale)])

    # Mipmaps
    if options.get('mipmaps', False):
        cmd.append('--genmipmap')

    # Output and input
    cmd.append(str(output_path))
    cmd.append(str(input_path))

    print(cmd)

    try:
        env = get_tool_environment()
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
            env=env
        )

        if result.returncode != 0:
            return False, f"toktx failed: {result.stderr}"

        return True, None

    except subprocess.TimeoutExpired:
        return False, "toktx timed out"
    except Exception as e:
        return False, f"Failed to run toktx: {str(e)}"


def run_ktx_extract(input_path, output_path):
    """
    Run the ktx tool to extract/transcode a KTX2 file to PNG.

    Args:
        input_path: Path to input KTX2 file
        output_path: Path for output PNG file

    Returns:
        tuple: (success: bool, error_message: str or None)
    """
    ktx_path = get_tool_path('ktx')
    if not ktx_path:
        return False, "ktx tool not found. Please install KTX tools first."

    cmd = [
        str(ktx_path),
        'extract',
        str(input_path),
        str(output_path)
    ]

    import struct
    header = Path(input_path).read_bytes()[:16]
    if len(header) >= 16 and struct.unpack_from('<I', header, 12)[0] == 0:
        cmd[2:2] = ['--transcode', 'rgba8']

    try:
        env = get_tool_environment()
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=120
        )

        if result.returncode != 0:
            return False, f"ktx extract failed: {result.stderr}"

        return True, None

    except subprocess.TimeoutExpired:
        return False, "ktx extract timed out"
    except Exception as e:
        return False, f"Failed to run ktx: {str(e)}"
