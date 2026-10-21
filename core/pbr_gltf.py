# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""A material's surface (core.pbr) as glTF 2.0 (experimental fork).

formats/gltf.py writes the base colour and the finish, as always; for a
material with a surface it then calls :func:`apply`, which adds what the
surface says — factors, the ORM map, the normal map, emission — and marks
the material ``extras.ingetrazo_surface`` so Render with Blender knows the
Principled BSDF the importer built is already the whole material.

Blender's glTF importer wires every one of these into its node graph, so a
GLB is all Render with Blender needs.
"""
from __future__ import annotations

from typing import Callable

#: Tile size for maps on a material with no base texture to borrow UVs from.
DEFAULT_TILE = (1.0, 1.0, 0.0)


def export_split(scene) -> dict:
    """``name → UV tile`` for every material with a surface: the glTF writer
    keeps their faces in materials of their own (two paints of the same
    grey must not merge when only one is brushed steel), and a material
    with maps but no base texture gets planar UVs at this tile size
    (``None`` when it has no maps and needs none)."""
    from core.pbr import surface_materials
    out = {}
    for name, mat in surface_materials(scene).items():
        out[name] = DEFAULT_TILE if mat.surface.maps else None
    return out


def apply(gmat: dict, surface, add_image: Callable[[str], int | None],
          used_ext: set) -> None:
    """Write ``surface`` onto the glTF material dict ``gmat`` in place.
    ``add_image(path)`` embeds an image and returns its texture index."""
    from core import pbr_maps
    pbr = gmat.setdefault("pbrMetallicRoughness", {})
    pbr["roughnessFactor"] = round(float(surface.roughness), 4)
    pbr["metallicFactor"] = round(float(surface.metallic), 4)

    orm, has_ao, has_rm = pbr_maps.orm_image(surface)
    if orm:
        idx = add_image(orm)
        if idx is not None:
            if has_rm:
                pbr["metallicRoughnessTexture"] = {"index": idx}
            if has_ao:
                gmat["occlusionTexture"] = {
                    "index": idx,
                    "strength": round(float(surface.map("ao").strength), 4)}

    nrm, scale = pbr_maps.normal_image(surface)
    if nrm:
        idx = add_image(nrm)
        if idx is not None:
            gmat["normalTexture"] = {"index": idx,
                                     "scale": round(float(scale), 4)}

    if surface.glows:
        color = list(surface.emission or (1.0, 1.0, 1.0))[:3]
        strength = surface.blender_strength() if surface.luminance else 1.0
        emap = surface.map("emission")
        if emap is not None:
            idx = add_image(emap.path)
            if idx is not None:
                gmat["emissiveTexture"] = {"index": idx}
        # emissiveFactor is capped at 1; the rest rides the extension.
        if strength <= 1.0:
            gmat["emissiveFactor"] = [round(c * strength, 5) for c in color]
        else:
            gmat["emissiveFactor"] = [round(c, 5) for c in color]
            gmat.setdefault("extensions", {})[
                "KHR_materials_emissive_strength"] = {
                    "emissiveStrength": round(strength, 6)}
            used_ext.add("KHR_materials_emissive_strength")

    ext = gmat.setdefault("extensions", {})
    if surface.transmission:
        ext["KHR_materials_transmission"] = {
            "transmissionFactor": float(surface.transmission)}
        used_ext.add("KHR_materials_transmission")
    else:
        ext.pop("KHR_materials_transmission", None)
    if surface.ior != 1.5:
        ext["KHR_materials_ior"] = {"ior": float(surface.ior)}
        used_ext.add("KHR_materials_ior")
    else:
        ext.pop("KHR_materials_ior", None)
    if not ext:
        gmat.pop("extensions", None)
    gmat.setdefault("extras", {})["ingetrazo_surface"] = 1


# ---- Import -----------------------------------------------------------------

def _has_surface(gmat) -> bool:
    pbr = gmat.get("pbrMetallicRoughness", {}) or {}
    ext = gmat.get("extensions", {}) or {}
    return bool(pbr.get("metallicRoughnessTexture") or gmat.get("normalTexture")
                or gmat.get("occlusionTexture") or gmat.get("emissiveTexture")
                or float(pbr.get("metallicFactor", 1.0 if pbr.get(
                    "metallicRoughnessTexture") else 0.0)) > 0.0
                or any(gmat.get("emissiveFactor", [0, 0, 0]))
                or "KHR_materials_transmission" in ext
                or gmat.get("extras", {}).get("ingetrazo_surface"))


def surface_from_gltf(gmat: dict, image_paths: list):
    """A core.pbr.Surface from one glTF material dict, or None when it says
    nothing beyond a base colour (those stay anonymous paints, as before)."""
    from core.pbr import LUMENS_PER_WATT, MapSlot, Surface
    if not _has_surface(gmat):
        return None
    js_pbr = gmat.get("pbrMetallicRoughness", {}) or {}
    ext = gmat.get("extensions", {}) or {}
    textures = gmat.get("_textures", [])

    def image(info):
        if not info:
            return None
        idx = info.get("index")
        if idx is None or idx >= len(textures):
            return None
        src = textures[idx].get("source")
        if src is None or src >= len(image_paths):
            return None
        return image_paths[src]

    s = Surface(roughness=float(js_pbr.get("roughnessFactor", 1.0)),
                metallic=float(js_pbr.get("metallicFactor", 1.0)))
    mr = image(js_pbr.get("metallicRoughnessTexture"))
    if mr:
        s.maps["roughness"] = MapSlot({"path": mr}, channel="g")
        s.maps["metallic"] = MapSlot({"path": mr}, channel="b")
    occ = gmat.get("occlusionTexture")
    if image(occ):
        s.maps["ao"] = MapSlot({"path": image(occ)}, channel="r",
                               strength=float(occ.get("strength", 1.0)))
    nrm = gmat.get("normalTexture")
    if image(nrm):
        s.maps["normal"] = MapSlot({"path": image(nrm)},
                                   strength=float(nrm.get("scale", 1.0)))
    factor = list(gmat.get("emissiveFactor", [0.0, 0.0, 0.0]))[:3]
    emap = image(gmat.get("emissiveTexture"))
    if emap or any(factor):
        strength = float(ext.get("KHR_materials_emissive_strength", {})
                         .get("emissiveStrength", 1.0))
        peak = max(factor) if any(factor) else 1.0
        s.emission = tuple(c / peak for c in factor) if any(factor) \
            else (1.0, 1.0, 1.0)
        s.luminance = round(peak * strength * LUMENS_PER_WATT, 3)
        if emap:
            s.maps["emission"] = MapSlot({"path": emap})
    trans = ext.get("KHR_materials_transmission", {})
    if trans:
        s.transmission = float(trans.get("transmissionFactor", 0.0))
    ior = ext.get("KHR_materials_ior", {})
    if ior:
        s.ior = float(ior.get("ior", 1.5))
    finish = (gmat.get("extras", {}) or {}).get("ingetrazo_finish")
    if s.transmission > 0.5:
        s.kind = "water" if finish == "water" else "glass"
    elif s.glows and not s.maps.get("emission"):
        s.kind = "light"
    elif s.metallic >= 0.5 and not mr:
        s.kind = "metal"
    return s


def stamp_import(scene, mesh, js: dict, path) -> int:
    """After a GLB import (formats/glb.py): give every glTF material that
    carries more than a base colour a registry material with its surface,
    and stamp its name on the faces painted with it (matched by their base
    image, or by their colour). Returns how many materials it made."""
    from core.materials import Material, register
    gmats = js.get("materials", [])
    if not gmats:
        return 0
    image_paths = _image_paths_again(js, path) if js.get("images") else []
    textures = js.get("textures", [])
    by_tex: dict = {}
    by_col: dict = {}
    made = 0
    for i, gm in enumerate(gmats):
        surface = surface_from_gltf({**gm, "_textures": textures},
                                    image_paths)
        if surface is None:
            continue
        pbr = gm.get("pbrMetallicRoughness", {}) or {}
        base = pbr.get("baseColorTexture")
        tex_path = None
        if base and base.get("index", -1) < len(textures):
            src = textures[base["index"]].get("source")
            if src is not None and src < len(image_paths):
                tex_path = image_paths[src]
        rgba = pbr.get("baseColorFactor") or [1.0, 1.0, 1.0, 1.0]
        name = gm.get("name") or f"glTF material {i + 1}"
        mat = Material(name, color=None if tex_path else tuple(rgba[:3]),
                       texture={"path": tex_path} if tex_path else None,
                       finish=surface.finish(), surface=surface)
        final = register(scene.materials, mat)
        made += 1
        if tex_path:
            by_tex.setdefault(tex_path, final)
        else:
            by_col.setdefault(tuple(round(float(c), 4) for c in rgba[:3]),
                              final)
    if not made:
        return 0
    for f in mesh.faces:
        if f.attrs is None:
            f.attrs = {}
        a = f.attrs
        if a.get("mat"):
            continue
        tex = a.get("texture")
        if tex and tex.get("path") in by_tex:
            a["mat"] = by_tex[tex["path"]]
        elif not tex:
            col = a.get("color") or [1.0, 1.0, 1.0]
            key = tuple(round(float(c), 4) for c in col[:3])
            if key in by_col:
                a["mat"] = by_col[key]
    return made


def _image_paths_again(js, path):
    """The image files the importer already extracted (formats/glb.py
    caches them by source file, so this re-reads nothing)."""
    from pathlib import Path
    from formats.glb import _extract_images, parse_container
    _js, buffers = parse_container(Path(path))
    return _extract_images(js, buffers, Path(path))
