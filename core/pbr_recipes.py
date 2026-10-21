# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""PBR materials for the AI doors (experimental fork).

Two one-line helpers bound in the AI scope (core/ai.py ``_recipe_helpers``)
and the lines of the recipe book (core/ai_recipes.py) that teach them. Before
them an agent driving the MCP bridge had no way to know surfaces existed:
the recipe book taught ``g.material = {"color": …}`` and told it to keep
off ``scene.materials``.

Stdlib-only at import time, like core/ai_recipes.py: the MCP server is a
bare stdio process that imports the recipe text from a checkout, a bundle,
an AppImage and a Flatpak alike. The helpers import the model lazily.
"""
from __future__ import annotations

#: Appended to core.ai_recipes.RECIPES (same language and style).
RECIPES = """Materiales PBR (rugosidad, metal, vidrio, luz):
- m = material("Acero cepillado", color=(0.8,0.8,0.82), kind="metal", \
roughness=0.25) — crea o actualiza un material con nombre y devuelve su \
nombre. kind: "generic" | "metal" | "glass" | "water" | "light"; opcionales \
roughness (0 pulido … 1 mate), metallic (0..1), emission=(r,g,b) y \
luminance en cd/m² (un panel LED encendido ~3000), texture="ruta.png".
- paint(g, m) — pinta un grupo (o una lista de caras) con ese material. \
Ejemplo: paint(box((0,0,0),(1,1,1), name="Cubo"), material("Laca roja", \
color=(0.7,0.08,0.08), roughness=0.08)). Cambiar el material después \
(material("Laca roja", roughness=0.5)) actualiza todas sus caras.
- Ver los materiales iluminados: scene.display_style = \
Style(name="Realistic", face_mode="realistic") (Style ya está en el \
scope); el render con Blender y la exportación GLB los usan tal cual."""

_KINDS = ("generic", "metal", "glass", "water", "light")


def helpers(scene) -> dict:
    """``material``, ``paint`` and ``Style`` for the AI scope."""
    from core import pbr
    from core.materials import Material
    from core.style import Style

    def material(name, color=None, kind=None, roughness=None, metallic=None,
                 emission=None, luminance=None, texture=None,
                 transmission=None, ior=None):
        if kind is not None and kind not in _KINDS:
            raise ValueError(f"kind must be one of {_KINDS}, not {kind!r}")
        reg = scene.materials
        old = reg.get(name)
        if old is None:
            surface = pbr.from_kind(kind or "generic")
            rgb = tuple(color[:3]) if color is not None else (0.8, 0.8, 0.8)
            mat = Material(name, color=None if texture else rgb,
                           texture={"path": str(texture)} if texture else None)
            reg[name] = mat
        else:
            mat = old
            base = mat.surface if mat.surface is not None \
                else pbr.effective(mat)
            surface = (pbr.with_kind(base, kind) if kind is not None
                       else base.copy())
            if color is not None:
                mat.color = tuple(color[:3])
            if texture is not None:
                mat.texture = {"path": str(texture)}
        for field, value in (("roughness", roughness), ("metallic", metallic),
                             ("transmission", transmission), ("ior", ior),
                             ("luminance", luminance)):
            if value is not None:
                setattr(surface, field, float(value))
        if emission is not None:
            surface.emission = tuple(float(c) for c in emission[:3])
            if not surface.luminance:
                surface.luminance = 683.0        # Blender strength 1
        mat.surface = surface
        mat.finish = surface.finish()
        _repaint(scene, name, mat)
        return name

    def paint(target, name):
        mat = scene.materials.get(name)
        if mat is None:
            raise KeyError(f"no material {name!r}: create it with material()")
        faces = _faces(target)
        for f in faces:
            _stamp(f, name, mat)
        return len(faces)

    return {"material": material, "paint": paint, "Style": Style}


def _faces(target) -> list:
    mesh = getattr(target, "mesh", None)
    if mesh is not None:
        return list(mesh.faces)
    if hasattr(target, "attrs"):
        return [target]
    return list(target)


def _stamp(face, name, mat) -> None:
    attrs = dict(face.attrs or {})
    attrs["mat"] = name
    if mat.texture and mat.texture.get("path"):
        attrs["texture"] = dict(mat.texture)
        attrs.pop("color", None)
    else:
        attrs.pop("texture", None)
        attrs["color"] = (*tuple(mat.color or (0.8, 0.8, 0.8))[:3], 1.0)
    face.attrs = attrs


def _repaint(scene, name, mat) -> None:
    """Faces already painted with ``name`` follow its new colour/picture."""
    for f in painted_faces(scene):
        if (f.attrs or {}).get("mat") == name:
            _stamp(f, name, mat)


def painted_faces(scene):
    """Every face of the loose mesh and of every group's mesh (a shared
    component prototype once) that names a material."""
    seen = set()
    loose = getattr(scene, "loose_mesh", None) or scene.mesh
    for mesh in [loose] + [g.mesh for g in getattr(scene, "groups", [])]:
        if mesh is None or id(mesh) in seen:
            continue
        seen.add(id(mesh))
        for f in mesh.faces:
            if (f.attrs or {}).get("mat"):
                yield f


def png_base64(image) -> str:
    """A QImage as base64 PNG, for the AI bridge's screenshot reply."""
    import base64
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    buf.close()
    return base64.b64encode(bytes(data)).decode("ascii")
