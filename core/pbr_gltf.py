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
                    "emissiveStrength": round(strength, 4)}
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
