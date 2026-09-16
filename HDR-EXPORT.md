# HERITAGE3D KTX + HDR fork — Blender 5.2 preview

Based on KhronosGroup/glTF-Blender-IO `blender-v5.2-release` at
`7df0ca14ee45f6c22c00cae6595c484af7353889`. This is a community fork, not an official Khronos HDR extension.

## Use

In the existing **File → Export → glTF 2.0** window, choose GLB and expand
**HERITAGE3D HDR**. Enable **Embed HDR textures**, select eligible materials,
and choose resolution and encoding quality. HDR defaults to off. In the same dialog, expand **KTX2 Textures** to enable
ordinary texture compression. Its **Generate Mipmaps** and **Create Fallback**
settings apply to ordinary KTX maps. Base Color, Normal, ORM and Other each keep
their format, quality, color-space, downsampling and normal-map controls.
ETC1S and UASTC export/import are supported. Native ASTC remains an experimental
legacy option: its KHR_texture_basisu declaration is not standard Basis Universal,
and native ASTC import/third-party interoperability is not validated.

HDR and ordinary KTX can be enabled together. HDR Base Color always retains an
embedded, standard UASTC KTX2 fallback with a full mip chain, while other material
channels use the KTX settings. The HDR fallback is encoded even when ordinary KTX
export is disabled, so compatible glTF viewers keep a GPU-compressed SDR path.
With both options off, export follows the upstream path. The original import,
geometry, animation and compression controls remain available.

The inherited **Environment Map (Experimental)** option exports a cubemap with
mipmaps and its own ETC1S/UASTC quality controls. GLB embeds this map. Its draft
KHR_environment_map representation is not a ratified portable format. This legacy
path clips HDR to SDR and estimates intensity; it does not preserve EXR radiance.
It is independent of full-range HDR Base Color.

Eligible materials have a linear Rec.709 EXR directly connected to Principled
Base Color (Lit), or Image Color directly connected to Material Output (Unlit).
Background with strength 1 is also supported. Type comes from the original
material graph and the upstream glTF exporter, not HDR detection. Complex node
graphs, UDIM and multilayer EXR are outside the HDR Base Color preview.
Original materials and images are restored; HDR is prepared on temporary copies.

The existing export options are passed through to the upstream exporter.
HDR requires material export and cannot be combined with gltfpack in this preview.
Selection, visibility and collection filters determine HDR candidates; bindings
that do not appear in the final GLB fail validation rather than silently attaching
to the wrong material. HDR extras are written regardless of Custom Properties;
user custom extras follow that original option.

## Format and limits

UASTC HDR 4x4 is embedded under private `extras.HERITAGE3D_hdr_surface` version 1,
with the original shading type and an embedded SDR fallback. It is not declared
as ratified KHR_texture_basisu HDR support. Compatible HERITAGE3D viewers read
HDR; other viewers render the SDR texture. Third-party re-export may lose HDR.

Input is nonnegative finite linear RGB (at most 65504), alpha [0,1]. Negative,
nonfinite and unsupported-color-space data is rejected. No viewport exposure is
baked into HDR. SDR fallback uses luminance Reinhard + sRGB and peak normalization.
KTX compression is lossy; retain archival EXRs. At most 16 bindings, 8192 pixels
per axis and 256 MiB equivalent RGBA16F mip-chain memory; a square 8K map exceeds
this budget. Image preparation and upstream export are synchronous; encoding is
cancellable with Esc. Source files and material types are not overwritten.

## Preview installation and distribution

The package contains the complete `io_scene_gltf2` fork, with that same module ID.
It replaces the upstream exporter in the chosen Blender installation; it is not
the separate `heritage3d_hdr` extension. Back up the existing `io_scene_gltf2`
folder in Blender's `scripts/addons_core` or user `scripts/addons` (whichever is active). **Overlay the archive's io_scene_gltf2
contents into that existing folder; do not delete the folder first.** Keep Blender's
native `libbf_intern_draco_bridge` and `libbf_intern_meshopt_bridge` libraries
(.dylib/.dll/.so): upstream Git does not include those platform binaries and this
archive intentionally does not redistribute them. This follows upstream's
source-overlay development installation workflow. Restart Blender after overlay.
Do not install both copies of io_scene_gltf2 into competing module search paths.
Disable the separate HERITAGE3D HDR and glTF KTX2 add-ons: their functionality is integrated here.

The initial binary package targets macOS Apple Silicon. It includes pinned
KTX-Software 5.0.0-rc2 (HDR/decode) and toktx 4.4.2 (SDR/cubemaps),
with their respective libktx libraries, checks their SHA-256 and runs a verified copy
from Blender's user data cache. No network, Node.js or system KTX is required.
It is a local unsigned preview; Windows/Linux/Intel Mac builds and public-release
signing are not provided by this patch.

Build:
```
python3 tools/build_hdr_preview.py --ktx-prefix /path/to/ktx5-install-prefix \
  --legacy-tools /path/to/ktx4-bin
```
The KTX5 prefix must contain bin/ktx and lib/libktx.5.dylib. The legacy tools
directory must contain toktx and libktx.4.dylib. No personal paths or
credentials are bundled. Upstream Apache-2.0 copyright/license notices remain;
new HDR files carry GPL-3.0-or-later with their license in hdr/LICENSE. The combined
fork includes this GPL component. Separate KTX executables retain their own
licenses and notices in hdr/licenses/KTX. The original hdr/NOTICE refers to the
new HDR implementation; upstream importer/exporter copyrights remain unchanged.

The integrated KTX Python code retains its Apache-2.0 license in ktx/LICENSE;
ktx/PROVENANCE.json records the original installed add-on source checksums.

## Validation

`tests/hdr/ktx.py` also runs the HDR suite and checks ETC1S/UASTC/native ASTC
export, mipmaps and fallback toggles, mixed HDR + KTX normal maps, Basis import,
and the experimental embedded environment export/import.
`tests/hdr/ui.py` opens the ordinary export dialog in a disposable Blender process
to verify HDR material selection and KTX panel registration.


`tests/hdr/export.py` runs against the built archive in an isolated Blender process.
It covers Lit/Unlit, HDR sample values and orientation, original-resource cleanup,
cancellation, standard HDR-off export, per-material selection, custom-property
options, animation and Y-up-off transforms. Example:
```
BLENDER_USER_CONFIG=/tmp/hdr-test-config blender --background --factory-startup \
  --python-exit-code 1 --python tests/hdr/export.py -- \
  /absolute/path/to/preview.zip /tmp/hdr-test-output
```
Generated Lit and Unlit GLBs are additionally checked in the HERITAGE3D viewer's
WebGL and WebGPU integration tests. This is targeted HDR validation; the full
upstream Khronos regression suite has not been run for this preview.
