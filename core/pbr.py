# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""A named material's SURFACE: full PBR values and maps (experimental fork).

core.finish answers "how does this surface take light?" with one of six
words. A :class:`Surface` answers it in the numbers every renderer speaks —
roughness, metallic, a normal map, ambient occlusion, emission — so a
brushed steel, a brick with real relief or an LED strip renders as such in
Blender and in any glTF viewer.

The surface lives on the registry :class:`core.materials.Material`, looked
up by the face's ``attrs["mat"]``; faces, the mesh engine and the classic
viewport never see it. Base colour stays where it always was
(``Material.color`` / ``Material.texture`` and the faces' baked attrs).

Which values a consumer gets is decided in ONE place, :func:`effective`:
an explicit surface wins, then the finish someone chose, then the finish
guessed from the name — so exporters never have to know there are three.

Every map hangs its image under a ``"texture"`` key, which is what the
.igz writer walks to embed images: a surface's maps travel inside the
document with no change to formats/igz.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

#: Material types, in the order the editor shows them.
KINDS = ("generic", "metal", "glass", "water", "light")

LABELS = {"generic": "Generic", "metal": "Metal", "glass": "Glass",
          "water": "Water", "light": "Light"}

#: Map slots, in the order the editor shows them.
SLOTS = ("roughness", "metallic", "normal", "bump", "ao", "emission")

SLOT_LABELS = {"roughness": "Roughness", "metallic": "Metallic",
               "normal": "Normal map", "bump": "Bump (height)",
               "ao": "Ambient occlusion", "emission": "Emission"}

#: Channels a map can be read from: a packed ARM/ORM image keeps three
#: greyscale maps in one file.
CHANNELS = ("rgb", "r", "g", "b", "a", "lum")

#: Luminous efficacy Blender (and its glTF importer) assume for a watt:
#: an emission strength of 1 is a radiance of 1 W/(m²·sr), which is
#: 683 cd/m². Lights in Render with Blender are real watts on the same
#: scale, so a lamp and a glowing panel agree.
LUMENS_PER_WATT = 683.0

#: The finish each kind is written as, for builds that know only finishes
#: (upstream IngeTrazo) and for Render with Blender's glass/water setup.
_KIND_FINISH = {"generic": None, "metal": "metal", "glass": "glass",
                "water": "water", "light": "matte"}

_SURFACE_VERSION = 1


@dataclass
class MapSlot:
    """One image feeding one slot."""

    #: ``{"path": …}`` — the attrs texture convention, so .igz embeds it.
    texture: dict
    #: Where the value is read: a whole RGB image, or one channel of it.
    channel: str = "rgb"
    #: Normal/bump strength, AO amount.
    strength: float = 1.0
    #: A gloss map read as roughness (and the like).
    invert: bool = False
    #: A DirectX-convention normal map (green channel points down).
    y_down: bool = False

    @property
    def path(self) -> str | None:
        return (self.texture or {}).get("path")

    def to_dict(self) -> dict:
        out: dict = {"texture": dict(self.texture)}
        if self.channel != "rgb":
            out["channel"] = self.channel
        if self.strength != 1.0:
            out["strength"] = float(self.strength)
        if self.invert:
            out["invert"] = True
        if self.y_down:
            out["y_down"] = True
        return out

    @classmethod
    def from_dict(cls, raw) -> Optional["MapSlot"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("texture"), dict):
            return None
        channel = raw.get("channel", "rgb")
        return cls(texture=dict(raw["texture"]),
                   channel=channel if channel in CHANNELS else "rgb",
                   strength=float(raw.get("strength", 1.0)),
                   invert=bool(raw.get("invert", False)),
                   y_down=bool(raw.get("y_down", False)))


@dataclass
class Surface:
    """PBR values of a named material. With a map in a slot, the slot's
    number MULTIPLIES the map (the glTF rule), so 1.0 means "as the map
    says"."""

    kind: str = "generic"
    roughness: float = 0.5
    metallic: float = 0.0
    transmission: float = 0.0
    ior: float = 1.5
    #: Emitted colour (RGB 0–1), or None for a surface that does not glow.
    emission: Optional[tuple] = None
    #: Luminance of the glowing surface, cd/m² (a lit LED panel: ~3000).
    luminance: float = 0.0
    maps: dict = field(default_factory=dict)        # slot → MapSlot
    #: Unknown keys from a newer build, written back unchanged.
    extra: dict = field(default_factory=dict)

    def map(self, slot: str) -> Optional[MapSlot]:
        m = self.maps.get(slot)
        return m if m is not None and m.path else None

    @property
    def glows(self) -> bool:
        return (self.emission is not None and self.luminance > 0.0) \
            or self.map("emission") is not None

    def blender_strength(self) -> float:
        """Emission strength in Blender's units (see LUMENS_PER_WATT)."""
        return max(0.0, float(self.luminance)) / LUMENS_PER_WATT

    def finish(self) -> Optional[str]:
        """The core.finish word closest to this surface."""
        f = _KIND_FINISH.get(self.kind)
        if f is not None:
            return f
        if self.metallic >= 0.5:
            return "metal"
        if self.roughness < 0.25:
            return "gloss"
        if self.roughness < 0.65:
            return "satin"
        return "matte"

    def copy(self) -> "Surface":
        return replace(self, maps={k: replace(v, texture=dict(v.texture))
                                   for k, v in self.maps.items()},
                       extra=dict(self.extra))

    # ---- Serialization (.igz) -------------------------------------------
    def to_dict(self) -> dict:
        out: dict = dict(self.extra)
        out.update({"v": _SURFACE_VERSION, "kind": self.kind,
                    "roughness": round(float(self.roughness), 4),
                    "metallic": round(float(self.metallic), 4)})
        if self.transmission:
            out["transmission"] = float(self.transmission)
        if self.ior != 1.5:
            out["ior"] = float(self.ior)
        if self.emission is not None:
            out["emission"] = [float(c) for c in self.emission[:3]]
        if self.luminance:
            out["luminance"] = float(self.luminance)
        maps = {k: m.to_dict() for k, m in self.maps.items() if m.path}
        if maps:
            out["maps"] = maps
        return out

    @classmethod
    def from_dict(cls, raw) -> Optional["Surface"]:
        if not isinstance(raw, dict):
            return None
        known = {"v", "kind", "roughness", "metallic", "transmission", "ior",
                 "emission", "luminance", "maps"}
        kind = raw.get("kind", "generic")
        maps = {}
        for slot, m in (raw.get("maps") or {}).items():
            ms = MapSlot.from_dict(m)
            if ms is not None:
                maps[slot] = ms
        emission = raw.get("emission")
        return cls(
            kind=kind if kind in KINDS else "generic",
            roughness=_unit(raw.get("roughness", 0.5)),
            metallic=_unit(raw.get("metallic", 0.0)),
            transmission=_unit(raw.get("transmission", 0.0)),
            ior=float(raw.get("ior", 1.5)),
            emission=tuple(emission[:3]) if emission else None,
            luminance=max(0.0, float(raw.get("luminance", 0.0))),
            maps=maps,
            extra={k: v for k, v in raw.items() if k not in known})


def _unit(v) -> float:
    try:
        return min(1.0, max(0.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def from_kind(kind: str, finish: str | None = None) -> Surface:
    """Starting values for a material type. The numbers come from
    core.finish.PBR, so a surface seeded from «Metal» renders exactly as the
    old Metal finish did. ``finish`` refines a Generic seed (matte, satin,
    gloss)."""
    from core.finish import PBR
    kind = kind if kind in KINDS else "generic"
    key = _KIND_FINISH.get(kind) or (finish if finish in PBR else "satin")
    if kind == "light":
        key = "matte"
    rough, metal, trans, ior = PBR[key]
    s = Surface(kind=kind, roughness=rough, metallic=metal,
                transmission=trans, ior=ior)
    if kind == "light":
        s.emission = (1.0, 1.0, 1.0)
        s.luminance = 3000.0
    return s


def with_kind(surface: Surface, kind: str) -> Surface:
    """``surface`` switched to another type: the type's values, the loaded
    maps kept (switching Metal → Generic must not throw away the maps)."""
    out = from_kind(kind)
    out.maps = {k: replace(v, texture=dict(v.texture))
                for k, v in surface.maps.items()}
    if kind != "light" and surface.emission is not None:
        out.emission, out.luminance = surface.emission, surface.luminance
    return out


def effective(material, name: str | None = None, picture: str | None = None,
              opacity: float | None = None) -> Surface:
    """The surface every exporter and renderer should use for a material.

    1. The material's own ``surface``, when someone set one.
    2. Otherwise its chosen finish, as a surface.
    3. Otherwise the finish guessed from the name and picture (core.finish).

    ``material`` may be None (a face whose ``mat`` names nothing), then the
    guess runs on ``name``/``picture``/``opacity``.
    """
    from core.finish import FINISHES, PBR, guess
    surface = getattr(material, "surface", None)
    if surface is not None:
        return surface
    chosen = getattr(material, "finish", None)
    if material is not None:
        name = name or material.name
        tex = material.texture or {}
        picture = picture or tex.get("path")
        if opacity is None:
            opacity = material.opacity
    if chosen not in FINISHES:
        from pathlib import Path
        chosen = guess(name, picture and Path(picture).name, opacity)
    rough, metal, trans, ior = PBR[chosen]
    kind = {"metal": "metal", "glass": "glass",
            "water": "water"}.get(chosen, "generic")
    return Surface(kind=kind, roughness=rough, metallic=metal,
                   transmission=trans, ior=ior)


def surface_materials(scene) -> dict:
    """``name → Material`` for the registry entries that carry a surface —
    the ones an exporter must keep apart from look-alike paints."""
    registry = getattr(scene, "materials", None) or {}
    return {n: m for n, m in registry.items()
            if getattr(m, "surface", None) is not None}
