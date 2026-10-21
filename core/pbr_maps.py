# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Image work behind PBR maps (experimental fork): channel packing,
normal-map conventions, bump → normal, and recognising the files of a
downloaded PBR set by their names.

glTF wants roughness in the GREEN and metallic in the BLUE channel of one
image, ambient occlusion in RED (the "ORM" layout), and OpenGL-convention
normal maps. People bring whatever their source ships: a Poly Haven ARM
file (already ORM), three separate greyscale maps from ambientCG, a gloss
map instead of roughness, DirectX normals. Everything here turns those into
what glTF wants. Results go to the texture cache, content-addressed like
every other cached image, so exporting twice writes nothing new.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np

#: Largest side of a packed map; bigger inputs are scaled down to it.
MAX_SIZE = 4096


# ---- Pixels ---------------------------------------------------------------

def load_rgba(path, size: int | None = None) -> np.ndarray | None:
    """``path`` as an H×W×4 uint8 array (RGBA), or None when unreadable.
    ``size`` scales it to fit a square of that side first (previews)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    img = QImage(str(path))
    if img.isNull():
        return None
    if size is not None and (img.width() > size or img.height() > size):
        img = img.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    buf = np.frombuffer(img.constBits(), dtype=np.uint8,
                        count=img.bytesPerLine() * h)
    return buf.reshape(h, img.bytesPerLine())[:, :w * 4].reshape(h, w, 4).copy()


def channel(rgba: np.ndarray, which: str) -> np.ndarray:
    """One greyscale plane (float 0–1) of an RGBA array."""
    idx = {"r": 0, "g": 1, "b": 2, "a": 3}.get(which)
    if idx is not None:
        return rgba[..., idx].astype(np.float32) / 255.0
    rgb = rgba[..., :3].astype(np.float32) / 255.0
    return rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def _resize(rgba: np.ndarray, w: int, h: int) -> np.ndarray:
    if rgba.shape[1] == w and rgba.shape[0] == h:
        return rgba
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    src = np.ascontiguousarray(rgba)
    img = QImage(src.data, src.shape[1], src.shape[0], src.shape[1] * 4,
                 QImage.Format.Format_RGBA8888)
    img = img.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    return load_qimage(img)


def load_qimage(img) -> np.ndarray:
    from PySide6.QtGui import QImage
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    buf = np.frombuffer(img.constBits(), dtype=np.uint8,
                        count=img.bytesPerLine() * h)
    return buf.reshape(h, img.bytesPerLine())[:, :w * 4].reshape(h, w, 4).copy()


def save_png(rgba: np.ndarray, name: str) -> Path:
    """Store an RGBA array as a PNG in the texture cache (``pbr/``)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QImage
    from core.texture import cache_image
    src = np.ascontiguousarray(rgba, dtype=np.uint8)
    img = QImage(src.data, src.shape[1], src.shape[0], src.shape[1] * 4,
                 QImage.Format.Format_RGBA8888)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return cache_image(bytes(ba.data()), name, "pbr")


def _stamp(*parts) -> str:
    """A short key for a derived image: its inputs and their mtimes, so an
    edited source re-derives and an unchanged one reuses the cache."""
    h = hashlib.sha1()
    for p in parts:
        if isinstance(p, (str, Path)) and Path(p).exists():
            st = Path(p).stat()
            h.update(f"{p}|{st.st_mtime_ns}|{st.st_size}".encode())
        else:
            h.update(repr(p).encode())
    return h.hexdigest()[:12]


_derived: dict = {}


# ---- glTF layouts ---------------------------------------------------------

def orm_image(surface):
    """``(path, has_ao, has_rm)``: one image in glTF's ORM layout for the
    surface's AO, roughness and metallic maps, or ``(None, False, False)``
    when it has none of them.

    A Poly Haven ARM file that feeds all three slots channel for channel
    (AO=R, roughness=G, metallic=B, nothing inverted) is used as it is.
    Anything else is packed into a new image the size of the largest input;
    a slot with no map is filled white, so its factor alone decides."""
    ao, rough, metal = (surface.map("ao"), surface.map("roughness"),
                        surface.map("metallic"))
    if not (ao or rough or metal):
        return None, False, False
    has_rm = bool(rough or metal)
    srcs = [m for m in (ao, rough, metal) if m is not None]
    one = {m.path for m in srcs}
    if (len(one) == 1 and all(not m.invert for m in srcs)
            and (ao is None or ao.channel == "r")
            and (rough is None or rough.channel == "g")
            and (metal is None or metal.channel == "b")):
        return srcs[0].path, ao is not None, has_rm
    key = ("orm", _stamp(*[(m.path, m.channel, m.invert) for m in srcs],
                         *[m.path for m in srcs]))
    if key in _derived and Path(_derived[key]).exists():
        return _derived[key], ao is not None, has_rm
    arrays = {}
    for m in srcs:
        if m.path not in arrays:
            arr = load_rgba(m.path)
            if arr is None:
                continue
            arrays[m.path] = arr
    if not arrays:
        return None, False, False
    h = min(MAX_SIZE, max(a.shape[0] for a in arrays.values()))
    w = min(MAX_SIZE, max(a.shape[1] for a in arrays.values()))
    out = np.full((h, w, 4), 255, dtype=np.uint8)
    for idx, m in ((0, ao), (1, rough), (2, metal)):
        if m is None or m.path not in arrays:
            continue
        plane = channel(_resize(arrays[m.path], w, h),
                        "lum" if m.channel == "rgb" else m.channel)
        if m.invert:
            plane = 1.0 - plane
        out[..., idx] = np.clip(plane * 255.0 + 0.5, 0, 255).astype(np.uint8)
    path = str(save_png(out, "orm.png"))
    _derived[key] = path
    return path, ao is not None and ao.path in arrays, has_rm


def normal_image(surface):
    """``(path, scale)``: the surface's relief as an OpenGL normal map, or
    ``(None, 1.0)``. A DirectX map gets its green channel flipped; a bump
    (height) map is turned into normals."""
    n = surface.map("normal")
    if n is not None:
        if not n.y_down:
            return n.path, n.strength
        key = ("dx", _stamp(n.path))
        if key not in _derived or not Path(_derived[key]).exists():
            arr = load_rgba(n.path)
            if arr is None:
                return None, 1.0
            arr[..., 1] = 255 - arr[..., 1]
            _derived[key] = str(save_png(arr, "normal_gl.png"))
        return _derived[key], n.strength
    b = surface.map("bump")
    if b is None:
        return None, 1.0
    key = ("bump", _stamp(b.path, b.channel, b.invert))
    if key not in _derived or not Path(_derived[key]).exists():
        arr = load_rgba(b.path)
        if arr is None:
            return None, 1.0
        _derived[key] = str(save_png(bump_to_normal(
            channel(arr, "lum" if b.channel == "rgb" else b.channel)
            if not b.invert else
            1.0 - channel(arr, "lum" if b.channel == "rgb" else b.channel)),
            "normal_from_bump.png"))
    return _derived[key], b.strength


def bump_to_normal(height: np.ndarray, depth: float = 2.0) -> np.ndarray:
    """A tangent-space (OpenGL) normal map from a height plane (0–1), by
    central differences, wrapping at the edges as a tiling texture does.
    ``depth`` sets how steep a full 0→1 step reads."""
    dx = (np.roll(height, -1, axis=1) - np.roll(height, 1, axis=1)) * depth
    dy = (np.roll(height, -1, axis=0) - np.roll(height, 1, axis=0)) * depth
    # Image rows run down, tangent-space +Y runs up: hence +dy.
    nx, ny, nz = -dx, dy, np.ones_like(height)
    inv = 1.0 / np.sqrt(nx * nx + ny * ny + nz * nz)
    out = np.empty(height.shape + (4,), dtype=np.uint8)
    for i, c in enumerate((nx, ny, nz)):
        out[..., i] = np.clip((c * inv * 0.5 + 0.5) * 255.0 + 0.5, 0, 255)
    out[..., 3] = 255
    return out


# ---- Recognising a PBR set --------------------------------------------------

# Tokens, matched against the END of a file name (after the resolution tag).
# Values: (slot, channel, invert, y_down). "base" is the base colour; an
# "arm" file feeds three slots.
_WORDS = {
    "base": ("base", "rgb", False, False),
    "basecolor": ("base", "rgb", False, False),
    "color": ("base", "rgb", False, False),
    "colour": ("base", "rgb", False, False),
    "col": ("base", "rgb", False, False),
    "albedo": ("base", "rgb", False, False),
    "alb": ("base", "rgb", False, False),
    "diffuse": ("base", "rgb", False, False),
    "diff": ("base", "rgb", False, False),
    "roughness": ("roughness", "r", False, False),
    "rough": ("roughness", "r", False, False),
    "rgh": ("roughness", "r", False, False),
    "glossiness": ("roughness", "r", True, False),
    "gloss": ("roughness", "r", True, False),
    "metallic": ("metallic", "r", False, False),
    "metalness": ("metallic", "r", False, False),
    "metal": ("metallic", "r", False, False),
    "met": ("metallic", "r", False, False),
    "normal": ("normal", "rgb", False, False),
    "normalgl": ("normal", "rgb", False, False),
    "normaldx": ("normal", "rgb", False, True),
    "nor": ("normal", "rgb", False, False),
    "nrm": ("normal", "rgb", False, False),
    "nrml": ("normal", "rgb", False, False),
    "ao": ("ao", "r", False, False),
    "ambientocclusion": ("ao", "r", False, False),
    "occlusion": ("ao", "r", False, False),
    "occ": ("ao", "r", False, False),
    "height": ("bump", "r", False, False),
    "displacement": ("bump", "r", False, False),
    "disp": ("bump", "r", False, False),
    "bump": ("bump", "r", False, False),
    "emission": ("emission", "rgb", False, False),
    "emissive": ("emission", "rgb", False, False),
    "emit": ("emission", "rgb", False, False),
    "arm": ("arm", "rgb", False, False),
    "orm": ("arm", "rgb", False, False),
}
# Words a name may carry that say nothing about the slot.
_NOISE = re.compile(r"^(?:\d+k|\d+|png|jpe?g|tiff?|exr|map|tex|texture)$")
# Map types left out on purpose: shown as "skip" in the dialog.
_IGNORED = {"opacity", "alpha", "mask", "specular", "spec", "sss",
            "translucency", "cavity", "idmap", "preview", "sphere", "cube"}


def _tokens(stem: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", stem)
    return [t for t in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if t]


def classify(path) -> tuple | None:
    """What one file of a PBR set is: ``(slot, channel, invert, y_down,
    confidence)``, or None for a file this does not recognise. Reads the
    last meaningful words of the name — «Bricks076C_2K_NormalDX.jpg»,
    «brushed_steel_nor_gl_2k.png», «Wood_Floor_Roughness.png»."""
    toks = [t for t in _tokens(Path(path).stem) if not _NOISE.match(t)]
    if not toks:
        return None
    # Try the joined tail first («nor_gl», «base_color», «ambient_occlusion»).
    for n, conf in ((3, 1.0), (2, 1.0), (1, 1.0)):
        if len(toks) < n:
            continue
        tail = toks[-n:]
        joined = "".join(tail)
        if joined in _WORDS:
            return (*_WORDS[joined], conf)
        if n >= 2 and tail[-1] in ("gl", "dx", "ogl", "directx", "opengl"):
            head = "".join(tail[:-1])
            if head in ("nor", "normal", "nrm"):
                return ("normal", "rgb", False, tail[-1] in ("dx", "directx"),
                        conf)
    if toks[-1] in _IGNORED:
        return None
    for t in reversed(toks[:-1]):        # a slot word elsewhere in the name
        if t in _WORDS:
            return (*_WORDS[t], 0.7)
    return None


def match_set(paths) -> tuple[dict, str]:
    """Assign the files of a PBR set to slots: ``({path: (slot, channel,
    invert, y_down, confidence)}, suggested material name)``. Files no slot
    takes map to ``None`` («skip»).

    When a set ships both conventions of a normal map, the OpenGL one wins;
    separate roughness/metallic/AO maps win over a packed ARM file, which
    is then expanded only into the slots nothing else fills."""
    found = {p: classify(p) for p in paths}
    taken: dict = {}
    order = sorted(found, key=lambda p: (found[p] is None,
                                         -(found[p] or (0,) * 5)[4]))
    # Normal: GL before DX.
    normals = [p for p in order if found[p] and found[p][0] == "normal"]
    normals.sort(key=lambda p: found[p][3])
    out: dict = {p: None for p in paths}
    for p in normals[:1]:
        out[p] = found[p]
        taken["normal"] = p
    for p in order:
        info = found[p]
        if info is None or info[0] in ("normal", "arm"):
            continue
        if info[0] not in taken:
            out[p] = info
            taken[info[0]] = p
    for p in order:
        info = found[p]
        if info is None or info[0] != "arm":
            continue
        free = [s for s in ("ao", "roughness", "metallic") if s not in taken]
        if free:
            out[p] = ("arm", "rgb", False, False, info[4])
            for s in free:
                taken[s] = p
        break
    return out, _common_name(paths)


def _common_name(paths) -> str:
    stems = [Path(p).stem for p in paths]
    if not stems:
        return ""
    prefix = stems[0]
    for s in stems[1:]:
        while not s.startswith(prefix):
            prefix = prefix[:-1]
    toks = [t for t in re.split(r"[_\-. ]+", prefix) if t]
    while toks and (_NOISE.match(toks[-1].lower())
                    or toks[-1].lower() in _WORDS):
        toks.pop()
    name = " ".join(toks).strip()
    return name or (stems[0] if len(stems) == 1 else "")
