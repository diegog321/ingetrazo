# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""A material preview sphere, shaded on the CPU (experimental fork).

The tray's Surface section shows the material on a ball under a key light
and a simple studio sky, the way V-Ray's and Enscape's editors do, so a
roughness or a normal map can be judged before rendering. It is NumPy,
not OpenGL: a 112-pixel ball costs a few milliseconds, needs no GL context
(the tray, offscreen tests and weak GPUs all get the same picture) and
cannot disturb the viewport's renderer.

The shading is the usual metallic-roughness model: GGX specular with
Schlick Fresnel for the key light, an analytic environment BRDF (Karis)
for the sky reflections, Lambert diffuse, then a Reinhard tone map.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

_maps: dict = {}


def _load(path, size=192):
    """A map as float RGBA 0–1, cached by path and modification time."""
    from core.pbr_maps import load_rgba
    try:
        stamp = Path(path).stat().st_mtime_ns
    except OSError:
        return None
    key = (str(path), stamp, size)
    if key not in _maps:
        arr = load_rgba(path, size)
        if len(_maps) > 48:
            _maps.clear()
        _maps[key] = None if arr is None else arr.astype(np.float32) / 255.0
    return _maps[key]


def _sample(img, u, v):
    h, w = img.shape[:2]
    x = (np.floor(u * w).astype(np.int64)) % w
    y = (np.floor(v * h).astype(np.int64)) % h
    return img[y, x]


def _plane(img, slot, u, v):
    if img is None:
        return None
    px = _sample(img, u, v)
    ch = slot.channel
    if ch in ("r", "g", "b", "a"):
        out = px[..., "rgba".index(ch)]
    else:
        out = px[..., :3] @ np.array([0.2126, 0.7152, 0.0722],
                                     dtype=np.float32)
    return 1.0 - out if slot.invert else out


def _norm(v):
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-6)


def _sky(d):
    """Studio environment radiance for directions ``d`` (…×3, y up)."""
    y = d[..., 1:2]
    sky = np.array([0.55, 0.62, 0.72], dtype=np.float32) * (0.6 + 0.6 * np.clip(y, 0, 1))
    ground = np.array([0.16, 0.15, 0.14], dtype=np.float32)
    out = np.where(y >= 0.0, sky, ground)
    # A softbox up and to the left: what makes a metal read as a metal.
    box = ((d[..., 0] < -0.25) & (d[..., 0] > -0.85) & (d[..., 1] > 0.25)
           & (d[..., 2] > 0.2))
    return out + box[..., None] * 3.5


def _sky_blurred(d):
    """The environment averaged over a hemisphere: diffuse light, and what
    a fully rough reflection sees."""
    y = d[..., 1:2]
    return (np.array([0.42, 0.46, 0.52], dtype=np.float32) * (0.5 + 0.5 * y)
            + np.array([0.16, 0.15, 0.14], dtype=np.float32) * (0.5 - 0.5 * y)
            + 0.35 * np.clip(-d[..., 0:1] * 0.5 + 0.5, 0, 1) * np.clip(y, 0, 1))


def render(surface, color=None, texture=None, size: int = 112) -> np.ndarray:
    """The preview ball as an H×W×4 uint8 RGBA array.

    ``surface`` is a core.pbr.Surface; ``color`` the base RGB (0–1) and
    ``texture`` the material's base image path, when it has one."""
    n = size
    ys, xs = np.mgrid[0:n, 0:n].astype(np.float32)
    x = (xs + 0.5) / n * 2.0 - 1.0
    y = 1.0 - (ys + 0.5) / n * 2.0
    r2 = x * x + y * y
    inside = r2 < 1.0
    z = np.sqrt(np.clip(1.0 - r2, 0.0, 1.0))
    N = np.stack([x, y, z], axis=-1)
    # Sphere UVs, the texture wrapped twice around.
    u = 0.5 + np.arctan2(x, z) / (2 * np.pi)
    v = 0.5 - np.arcsin(np.clip(y, -1, 1)) / np.pi
    u, v = u * 2.0, v * 1.0

    base = np.ones((n, n, 3), dtype=np.float32) * np.array(
        color if color is not None else (0.8, 0.8, 0.8), dtype=np.float32)[:3]
    if texture:
        img = _load(texture)
        if img is not None:
            base = _sample(img, u, v)[..., :3]
    base = base ** 2.2                                    # sRGB → linear

    rough = np.full((n, n), float(surface.roughness), np.float32)
    metal = np.full((n, n), float(surface.metallic), np.float32)
    ao = np.ones((n, n), np.float32)
    for slot_name, plane_into in (("roughness", "rough"), ("metallic", "metal"),
                                  ("ao", "ao")):
        slot = surface.map(slot_name)
        if slot is None:
            continue
        p = _plane(_load(slot.path), slot, u, v)
        if p is None:
            continue
        if plane_into == "rough":
            rough = rough * p
        elif plane_into == "metal":
            metal = metal * p
        else:
            ao = 1.0 - slot.strength * (1.0 - p)
    rough = np.clip(rough, 0.03, 1.0)
    metal = np.clip(metal, 0.0, 1.0)

    # Relief: a normal map, or a bump map turned into one.
    nslot = surface.map("normal") or surface.map("bump")
    if nslot is not None:
        img = _load(nslot.path)
        if img is not None:
            if nslot is surface.map("bump"):
                from core.pbr_maps import bump_to_normal, channel
                h = channel((img * 255).astype(np.uint8),
                            "lum" if nslot.channel == "rgb" else nslot.channel)
                img = bump_to_normal(1.0 - h if nslot.invert else h
                                     ).astype(np.float32) / 255.0
            t = _sample(img, u, v)[..., :3] * 2.0 - 1.0
            if getattr(nslot, "y_down", False):
                t[..., 1] = -t[..., 1]
            t[..., :2] *= nslot.strength
            T = _norm(np.stack([z, np.zeros_like(z), -x], axis=-1))
            B = np.cross(N, T)
            N = _norm(T * t[..., 0:1] + B * t[..., 1:2] + N * t[..., 2:3])

    V = np.array([0.0, 0.0, 1.0], dtype=np.float32)
    L = _norm(np.array([-0.55, 0.65, 0.55], dtype=np.float32))
    H = _norm(L + V)
    NoV = np.clip(N @ V, 1e-4, 1.0)[..., None]
    NoL = np.clip(N @ L, 0.0, 1.0)[..., None]
    NoH = np.clip(N @ H, 0.0, 1.0)[..., None]
    VoH = float(np.clip(H @ V, 0.0, 1.0))
    a = (rough * rough)[..., None]
    a2 = a * a
    d = NoH * NoH * (a2 - 1.0) + 1.0
    D = a2 / (np.pi * d * d)
    k = a / 2.0
    G = (NoV / (NoV * (1 - k) + k)) * (NoL / (NoL * (1 - k) + k))
    m = metal[..., None]
    F0 = 0.04 * (1 - m) + base * m
    F = F0 + (1 - F0) * (1 - VoH) ** 5
    spec_key = D * G * F / np.maximum(4 * NoV * NoL, 1e-4)
    diffuse = base * (1 - m)
    key = 2.6
    color_out = (diffuse / np.pi + spec_key) * NoL * key

    # Environment: diffuse from the blurred sky, reflection from the sky
    # blurred by roughness, weighted with Karis' analytic split-sum.
    R = _norm(2 * NoV * N - V)
    env_r = _sky(R) * (1 - a) + _sky_blurred(R) * a
    c0 = np.array([-1.0, -0.0275, -0.572, 0.022], dtype=np.float32)
    c1 = np.array([1.0, 0.0425, 1.04, -0.04], dtype=np.float32)
    rr = rough[..., None] * c0 + c1
    a004 = np.minimum(rr[..., 0:1] ** 2, 2 ** (-9.28 * NoV)) * rr[..., 0:1] + rr[..., 1:2]
    AB0 = -1.04 * a004 + rr[..., 2:3]
    AB1 = 1.04 * a004 + rr[..., 3:4]
    color_out += diffuse * _sky_blurred(N) * ao[..., None]
    color_out += env_r * (F0 * AB0 + AB1) * ao[..., None]

    # Glass and water: what lies behind shows through, bent a little.
    if surface.transmission > 0.0:
        bent = _norm(-V + N * (1.0 - 1.0 / max(surface.ior, 1.01)) * 1.6)
        through = _sky(-bent * np.array([1, 1, -1], np.float32)) * base
        fr = 0.04 + 0.96 * (1 - NoV) ** 5
        color_out = (color_out * (1 - surface.transmission)
                     + (through * (1 - fr) + env_r * fr) * surface.transmission)

    if surface.glows:
        glow = np.array(surface.emission or (1.0, 1.0, 1.0), np.float32)[:3]
        e = np.broadcast_to(glow, (n, n, 3)).copy()
        eslot = surface.map("emission")
        if eslot is not None:
            img = _load(eslot.path)
            if img is not None:
                e = e * (_sample(img, u, v)[..., :3] ** 2.2)
        strength = min(surface.blender_strength(), 8.0) if surface.luminance else 1.0
        color_out = color_out + e * strength

    # Background: a quiet two-tone studio checker, so glass reads as glass.
    check = ((xs // 14 + ys // 14) % 2).astype(np.float32)[..., None]
    bg = 0.20 + 0.05 * check
    mapped = color_out / (1.0 + color_out)                 # Reinhard
    rgb = np.where(inside[..., None], mapped ** (1 / 2.2), bg)
    # Soft edge: one pixel of coverage.
    edge = np.clip((1.0 - np.sqrt(r2)) * n / 2.0, 0.0, 1.0)[..., None]
    rgb = rgb * edge + bg * (1 - edge)
    out = np.empty((n, n, 4), dtype=np.uint8)
    out[..., :3] = np.clip(rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
    out[..., 3] = 255
    return out
