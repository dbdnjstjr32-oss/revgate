"""1-Primitive GLB with a per-vertex `_FACE_ID` attribute (IMPLEMENTATION_BLUEPRINT §2.1).

gltfpack is intentionally NOT used: it reorders triangles and strips custom attributes, which
would break the triangle <-> B-Rep face mapping. The GLB is assembled directly with pygltflib +
numpy:

* one mesh, one primitive, one draw call
* POSITION (VEC3 f32, mm, Z-up — the client sets camera.up = [0, 0, 1])
* NORMAL   (VEC3 f32, unit, face orientation applied)
* COLOR_0  (VEC3 f32, original CAD face colour)
* _FACE_ID (SCALAR f32 — exact for integers < 2^24 = 16,777,216; three.js exposes it as
            geometry.attributes._face_id, so picking is `attributes._face_id.getX(hit.face.a)`)
* indices  (u16 when possible, else u32), triangles grouped per face in face_id order
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pygltflib
from pygltflib import (
    GLTF2,
    Accessor,
    Asset,
    Attributes,
    Buffer,
    BufferView,
    Material,
    Mesh,
    Node,
    PbrMetallicRoughness,
    Primitive,
    Scene,
)

FACE_ID_ATTRIBUTE = "_FACE_ID"
MAX_EXACT_FLOAT32_INT = 2**24  # 16,777,216
GENERATOR = "RevGate CAD worker (OCP/OCCT, pygltflib)"


def _pad4(data: bytes) -> bytes:
    return data + b"\x00" * ((4 - len(data) % 4) % 4)


def build_glb(
    positions: np.ndarray,
    normals: np.ndarray,
    colors: np.ndarray,
    face_ids: np.ndarray,
    indices: np.ndarray,
    *,
    name: str = "revgate",
    extras: dict[str, Any] | None = None,
) -> bytes:
    """Return GLB bytes. Shapes: positions/normals/colors (N, 3), face_ids (N,), indices (M, 3)."""
    pos = np.ascontiguousarray(positions, dtype=np.float32).reshape(-1, 3)
    nrm = np.ascontiguousarray(normals, dtype=np.float32).reshape(-1, 3)
    col = np.ascontiguousarray(colors, dtype=np.float32).reshape(-1, 3)
    fid_src = np.asarray(face_ids).reshape(-1)
    idx = np.asarray(indices).reshape(-1, 3)
    n_vert = pos.shape[0]
    if not (nrm.shape[0] == col.shape[0] == fid_src.shape[0] == n_vert):
        raise ValueError("positions/normals/colors/face_ids length mismatch")
    if n_vert and (fid_src.min() < 0 or fid_src.max() >= MAX_EXACT_FLOAT32_INT):
        raise ValueError("face ids must be in [0, 2^24) to stay exact as FLOAT32")
    if idx.size and (idx.min() < 0 or idx.max() >= n_vert):
        raise ValueError("triangle index out of range")
    fid = np.ascontiguousarray(fid_src, dtype=np.float32)

    asset = Asset(version="2.0", generator=GENERATOR)
    asset.extras = {"revgate": {"unit": "mm", "up_axis": "Z", "face_id_attribute": FACE_ID_ATTRIBUTE, **(extras or {})}}
    gltf = GLTF2(asset=asset, scene=0, scenes=[Scene(nodes=[0])], nodes=[Node(name=name)])

    if idx.shape[0] == 0 or n_vert == 0:
        # Valid, empty scene (e.g. a delta region that vanished) — keeps consumers simple.
        gltf.buffers = []
        return b"".join(gltf.save_to_bytes())

    if n_vert <= 0xFFFF:
        idx_arr = np.ascontiguousarray(idx, dtype=np.uint16).reshape(-1)
        idx_ctype = pygltflib.UNSIGNED_SHORT
    else:
        idx_arr = np.ascontiguousarray(idx, dtype=np.uint32).reshape(-1)
        idx_ctype = pygltflib.UNSIGNED_INT

    chunks: list[bytes] = []
    views: list[BufferView] = []
    offset = 0
    for arr, target in (
        (pos, pygltflib.ARRAY_BUFFER),
        (nrm, pygltflib.ARRAY_BUFFER),
        (col, pygltflib.ARRAY_BUFFER),
        (fid, pygltflib.ARRAY_BUFFER),
        (idx_arr, pygltflib.ELEMENT_ARRAY_BUFFER),
    ):
        raw = arr.tobytes()
        views.append(BufferView(buffer=0, byteOffset=offset, byteLength=len(raw), target=target))
        padded = _pad4(raw)
        chunks.append(padded)
        offset += len(padded)
    blob = b"".join(chunks)

    accessors = [
        Accessor(
            bufferView=0,
            componentType=pygltflib.FLOAT,
            count=n_vert,
            type=pygltflib.VEC3,
            min=[float(v) for v in pos.min(axis=0)],
            max=[float(v) for v in pos.max(axis=0)],
        ),
        Accessor(bufferView=1, componentType=pygltflib.FLOAT, count=n_vert, type=pygltflib.VEC3),
        Accessor(bufferView=2, componentType=pygltflib.FLOAT, count=n_vert, type=pygltflib.VEC3),
        Accessor(
            bufferView=3,
            componentType=pygltflib.FLOAT,
            count=n_vert,
            type=pygltflib.SCALAR,
            min=[float(fid.min())],
            max=[float(fid.max())],
        ),
        Accessor(bufferView=4, componentType=idx_ctype, count=int(idx_arr.size), type=pygltflib.SCALAR),
    ]

    attributes = Attributes(POSITION=0, NORMAL=1, COLOR_0=2)
    setattr(attributes, FACE_ID_ATTRIBUTE, 3)
    primitive = Primitive(attributes=attributes, indices=4, material=0, mode=pygltflib.TRIANGLES)

    gltf.nodes[0].mesh = 0
    gltf.meshes = [Mesh(name=name, primitives=[primitive])]
    gltf.materials = [
        Material(
            name="revgate-cad",
            pbrMetallicRoughness=PbrMetallicRoughness(baseColorFactor=[1.0, 1.0, 1.0, 1.0], metallicFactor=0.15, roughnessFactor=0.55),
            doubleSided=True,
        )
    ]
    gltf.accessors = accessors
    gltf.bufferViews = views
    gltf.buffers = [Buffer(byteLength=len(blob))]
    gltf.set_binary_blob(blob)
    return b"".join(gltf.save_to_bytes())


def read_glb_arrays(data: bytes) -> dict[str, np.ndarray]:
    """Decode a GLB produced by build_glb (used by tests and diagnostics)."""
    gltf = GLTF2.load_from_bytes(data)
    blob = gltf.binary_blob() or b""
    if not gltf.meshes:
        return {"POSITION": np.zeros((0, 3), np.float32), FACE_ID_ATTRIBUTE: np.zeros(0, np.float32), "indices": np.zeros((0, 3), np.uint32)}
    prim = gltf.meshes[0].primitives[0]

    def _read(acc_index: int) -> np.ndarray:
        acc = gltf.accessors[acc_index]
        view = gltf.bufferViews[acc.bufferView]
        dtype = {pygltflib.FLOAT: np.float32, pygltflib.UNSIGNED_INT: np.uint32, pygltflib.UNSIGNED_SHORT: np.uint16}[acc.componentType]
        width = {pygltflib.SCALAR: 1, pygltflib.VEC3: 3}[acc.type]
        start = (view.byteOffset or 0) + (acc.byteOffset or 0)
        arr = np.frombuffer(blob, dtype=dtype, count=acc.count * width, offset=start)
        return arr.reshape(acc.count, width) if width > 1 else arr

    attrs = prim.attributes
    out = {
        "POSITION": _read(attrs.POSITION),
        "NORMAL": _read(attrs.NORMAL),
        "COLOR_0": _read(attrs.COLOR_0),
        FACE_ID_ATTRIBUTE: _read(getattr(attrs, FACE_ID_ATTRIBUTE)),
        "indices": _read(prim.indices).astype(np.uint32).reshape(-1, 3),
    }
    return out
