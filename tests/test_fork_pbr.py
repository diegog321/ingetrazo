# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""PBR surfaces on named materials (core.pbr, experimental fork): the data,
the document, the undo step, the GLB, and recognising a downloaded set."""
from __future__ import annotations

import json
import os
import struct
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtGui import QImage, QVector3D as V
from PySide6.QtWidgets import QApplication

_inst = QApplication.instance()
if _inst is None:
    _app = QApplication([])

from core import pbr, pbr_maps                                   # noqa: E402
from core.history import History                                 # noqa: E402
from core.materials import Material, register                    # noqa: E402
from core.pbr import MapSlot, Surface                            # noqa: E402
from core.pbr_commands import SetMaterialSurfaceCommand          # noqa: E402
from core.scene import Scene                                     # noqa: E402
from formats import igz                                          # noqa: E402
from formats.gltf import save_glb                                # noqa: E402


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setenv("INGETRAZO_TEXTURE_CACHE", str(tmp_path / "cache"))
    pbr_maps._derived.clear()


def _png(path, rgba):
    arr = np.ascontiguousarray(np.array(rgba, dtype=np.uint8))
    img = QImage(arr.data, arr.shape[1], arr.shape[0], arr.shape[1] * 4,
                 QImage.Format.Format_RGBA8888)
    assert img.copy().save(str(path))
    return str(path)


def _flat(path, rgba, size=4):
    return _png(path, np.tile(np.array(rgba, dtype=np.uint8),
                              (size, size, 1)))


def _glb_json(path):
    data = path.read_bytes()
    n = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20 + n])


def _glb_image(path, js, tex_index):
    data = path.read_bytes()
    n = struct.unpack_from("<I", data, 12)[0]
    binary = data[20 + n + 8:]
    img = js["images"][js["textures"][tex_index]["source"]]
    view = js["bufferViews"][img["bufferView"]]
    raw = binary[view.get("byteOffset", 0):
                 view.get("byteOffset", 0) + view["byteLength"]]
    q = QImage.fromData(raw)
    return pbr_maps.load_qimage(q)


def _quad(scene, x=0.0):
    return scene.mesh.add_face([V(x, 0, 0), V(x + 1, 0, 0),
                                V(x + 1, 1, 0), V(x, 1, 0)])


# ---- The data -----------------------------------------------------------

def test_surface_roundtrip_keeps_maps_and_unknown_keys():
    s = Surface(kind="metal", roughness=0.35, metallic=1.0,
                emission=(1, 0.9, 0.8), luminance=1500.0,
                maps={"normal": MapSlot({"path": "/x/n.png"}, strength=0.8,
                                        y_down=True),
                      "roughness": MapSlot({"path": "/x/arm.png"},
                                           channel="g")})
    raw = s.to_dict()
    raw["future_key"] = {"a": 1}
    again = Surface.from_dict(raw)
    assert again.to_dict() == raw
    assert again.map("normal").y_down and again.map("normal").strength == 0.8
    assert again.map("roughness").channel == "g"


def test_material_carries_its_surface_and_old_files_still_load():
    m = Material("Acero", color=(0.6, 0.6, 0.6), finish="metal",
                 surface=pbr.from_kind("metal"))
    raw = m.to_dict()
    assert raw["finish"] == "metal" and raw["surface"]["kind"] == "metal"
    assert Material.from_dict(raw).surface.metallic == 1.0
    legacy = Material.from_dict({"name": "Old", "color": [1, 1, 1]})
    assert legacy.surface is None


def test_register_tells_surfaces_apart_and_keeps_them_on_rename():
    reg: dict = {}
    plain = Material("Steel", color=(0.5, 0.5, 0.5))
    brushed = Material("Steel", color=(0.5, 0.5, 0.5), finish="metal",
                       surface=pbr.from_kind("metal"))
    assert register(reg, plain) == "Steel"
    assert register(reg, brushed) == "Steel (2)"
    assert reg["Steel (2)"].surface is not None
    assert reg["Steel (2)"].finish == "metal"


def test_effective_prefers_surface_then_finish_then_guess():
    m = Material("Acero inoxidable", color=(0.6, 0.6, 0.6))
    assert pbr.effective(m).kind == "metal"                   # guessed
    m.finish = "matte"
    assert pbr.effective(m).roughness == 0.9                  # chosen
    m.surface = Surface(roughness=0.2)
    assert pbr.effective(m).roughness == 0.2                  # explicit
    assert pbr.effective(None, name="vidrio").kind == "glass"


def test_with_kind_keeps_maps():
    s = pbr.from_kind("metal")
    s.maps["normal"] = MapSlot({"path": "/n.png"})
    g = pbr.with_kind(s, "generic")
    assert g.metallic == 0.0 and g.map("normal") is not None
    light = pbr.from_kind("light")
    assert light.glows and abs(light.blender_strength() - 3000 / 683) < 1e-9


# ---- Undo + document ------------------------------------------------------

def test_surface_command_registers_and_undoes(tmp_path):
    scene = Scene()
    hist = History(scene)
    lib = Material("Porcelanato", color=(0.9, 0.9, 0.9))
    hist.execute(SetMaterialSurfaceCommand("Porcelanato",
                                           pbr.from_kind("generic"), lib))
    assert scene.materials["Porcelanato"].surface is not None
    assert scene.materials["Porcelanato"].finish == "satin"
    hist.undo()
    assert "Porcelanato" not in scene.materials
    hist.redo()
    s = scene.materials["Porcelanato"].surface.copy()
    s.roughness = 0.1
    hist.execute(SetMaterialSurfaceCommand("Porcelanato", s))
    assert scene.materials["Porcelanato"].surface.roughness == 0.1
    hist.undo()
    assert scene.materials["Porcelanato"].surface.roughness == 0.45


def test_igz_embeds_surface_maps_once(tmp_path):
    arm = _flat(tmp_path / "steel_arm.png", (200, 120, 255, 255))
    nrm = _flat(tmp_path / "steel_nor_gl.png", (128, 128, 255, 255))
    scene = Scene()
    s = pbr.from_kind("metal")
    s.maps = {"ao": MapSlot({"path": arm}, channel="r"),
              "roughness": MapSlot({"path": arm}, channel="g"),
              "metallic": MapSlot({"path": arm}, channel="b"),
              "normal": MapSlot({"path": nrm}, strength=0.8)}
    mat = Material("Acero", color=(0.6, 0.6, 0.6), surface=s, finish="metal")
    scene.materials["Acero"] = mat
    _quad(scene).attrs.update(mat.face_attrs())
    path = tmp_path / "doc.igz"
    igz.save_scene(scene, path)
    with zipfile.ZipFile(path) as zf:
        assert sum(n.startswith("textures/") for n in zf.namelist()) == 2
    os.remove(arm)
    os.remove(nrm)
    loaded = Scene()
    igz.load_into(loaded, path)
    back = loaded.materials["Acero"].surface
    assert back.map("roughness").channel == "g"
    assert os.path.exists(back.map("normal").path)
    assert back.map("ao").path == back.map("metallic").path


# ---- GLB ------------------------------------------------------------------

def _two_greys(surface):
    """Two faces painted the same grey; only one material has a surface."""
    scene = Scene()
    for i, (name, surf) in enumerate((("Plain", None), ("Brushed", surface))):
        m = Material(name, color=(0.5, 0.5, 0.5), surface=surf,
                     finish=surf.finish() if surf else None)
        scene.materials[name] = m
        _quad(scene, i * 2).attrs.update(m.face_attrs())
    return scene


def test_glb_keeps_a_surface_material_apart_and_marks_it(tmp_path):
    s = pbr.from_kind("metal")
    s.roughness = 0.35
    scene = _two_greys(s)
    out = tmp_path / "m.glb"
    save_glb(scene, out)
    js = _glb_json(out)
    mats = {m["name"]: m for m in js["materials"]}
    assert set(mats) == {"Plain", "Brushed"}
    b = mats["Brushed"]
    assert b["pbrMetallicRoughness"]["roughnessFactor"] == 0.35
    assert b["pbrMetallicRoughness"]["metallicFactor"] == 1.0
    assert b["extras"]["ingetrazo_surface"] == 1
    assert "ingetrazo_surface" not in mats["Plain"].get("extras", {})


def test_glb_reuses_an_arm_file_and_packs_separate_maps(tmp_path):
    arm = _flat(tmp_path / "x_arm.png", (10, 20, 30, 255))
    s = pbr.from_kind("generic")
    s.maps = {"ao": MapSlot({"path": arm}, channel="r"),
              "roughness": MapSlot({"path": arm}, channel="g"),
              "metallic": MapSlot({"path": arm}, channel="b")}
    path, has_ao, has_rm = pbr_maps.orm_image(s)
    assert path == arm and has_ao and has_rm

    rough = _flat(tmp_path / "x_gloss.png", (51, 51, 51, 255))
    metal = _flat(tmp_path / "x_metal.png", (255, 255, 255, 255), size=8)
    s2 = pbr.from_kind("generic")
    s2.roughness = s2.metallic = 1.0
    s2.maps = {"roughness": MapSlot({"path": rough}, channel="r", invert=True),
               "metallic": MapSlot({"path": metal}, channel="r")}
    scene = _two_greys(s2)
    out = tmp_path / "m.glb"
    save_glb(scene, out)
    js = _glb_json(out)
    b = next(m for m in js["materials"] if m["name"] == "Brushed")
    idx = b["pbrMetallicRoughness"]["metallicRoughnessTexture"]["index"]
    px = _glb_image(out, js, idx)
    assert px.shape[:2] == (8, 8)                  # the largest input
    assert abs(int(px[0, 0, 1]) - 204) <= 1        # 1 - 0.2 gloss
    assert px[0, 0, 2] == 255 and px[0, 0, 0] == 255
    assert "occlusionTexture" not in b
    # The colour face got UVs for its maps.
    prim = next(p for mesh in js["meshes"] for p in mesh["primitives"]
                if p["material"] == js["materials"].index(b))
    assert "TEXCOORD_0" in prim["attributes"]


def test_glb_normal_conventions_and_bump(tmp_path):
    dx = _flat(tmp_path / "n_dx.png", (128, 40, 255, 255))
    s = pbr.from_kind("generic")
    s.maps = {"normal": MapSlot({"path": dx}, strength=0.5, y_down=True)}
    path, scale = pbr_maps.normal_image(s)
    assert scale == 0.5 and pbr_maps.load_rgba(path)[0, 0, 1] == 215

    ramp = np.zeros((8, 8, 4), dtype=np.uint8)
    ramp[..., :3] = (np.arange(8) * 32)[None, :, None]
    ramp[..., 3] = 255
    bump = _png(tmp_path / "h.png", ramp)
    s.maps = {"bump": MapSlot({"path": bump}, channel="r")}
    path, _ = pbr_maps.normal_image(s)
    n = pbr_maps.load_rgba(path)
    assert n[4, 4, 0] < 128 and abs(int(n[4, 4, 1]) - 128) <= 1   # tilts −x


def test_glb_emission_in_blender_units(tmp_path):
    s = pbr.from_kind("light")
    s.luminance = 6830.0
    out = tmp_path / "m.glb"
    save_glb(_two_greys(s), out)
    js = _glb_json(out)
    b = next(m for m in js["materials"] if m["name"] == "Brushed")
    assert b["emissiveFactor"] == [1.0, 1.0, 1.0]
    assert b["extensions"]["KHR_materials_emissive_strength"][
        "emissiveStrength"] == 10.0
    assert "KHR_materials_emissive_strength" in js["extensionsUsed"]

    s.luminance = 341.5                                  # half a unit
    save_glb(_two_greys(s), out)
    b = next(m for m in _glb_json(out)["materials"] if m["name"] == "Brushed")
    assert b["emissiveFactor"] == [0.5, 0.5, 0.5]


def test_glb_without_surfaces_is_unchanged(tmp_path):
    scene = Scene()
    m = Material("Tarrajeo", color=(0.9, 0.9, 0.85))
    scene.materials["Tarrajeo"] = m
    _quad(scene).attrs.update(m.face_attrs())
    out = tmp_path / "m.glb"
    save_glb(scene, out)
    js = _glb_json(out)
    assert "images" not in js
    assert "extras" in js["materials"][0]
    assert "ingetrazo_surface" not in js["materials"][0]["extras"]


# ---- Recognising a PBR set ------------------------------------------------

@pytest.mark.parametrize("name, slot, ch, inv, ydown", [
    ("Bricks076C_2K_Color.jpg", "base", "rgb", False, False),
    ("Bricks076C_2K_NormalDX.jpg", "normal", "rgb", False, True),
    ("Bricks076C_2K_NormalGL.jpg", "normal", "rgb", False, False),
    ("Bricks076C_2K_AmbientOcclusion.jpg", "ao", "r", False, False),
    ("Bricks076C_2K_Displacement.jpg", "bump", "r", False, False),
    ("brushed_steel_diff_2k.jpg", "base", "rgb", False, False),
    ("brushed_steel_nor_gl_2k.png", "normal", "rgb", False, False),
    ("brushed_steel_nor_dx_2k.png", "normal", "rgb", False, True),
    ("brushed_steel_arm_2k.jpg", "arm", "rgb", False, False),
    ("Wood_Floor_Roughness.png", "roughness", "r", False, False),
    ("oak_glossiness.png", "roughness", "r", True, False),
    ("panel_metallic.png", "metallic", "r", False, False),
    ("led_emissive.png", "emission", "rgb", False, False),
    ("T_Rock_BaseColor.png", "base", "rgb", False, False),
])
def test_classify(name, slot, ch, inv, ydown):
    got = pbr_maps.classify(name)
    assert got[:4] == (slot, ch, inv, ydown)


def test_match_set_prefers_gl_and_separate_maps():
    files = ["/d/brushed_steel_diff_2k.jpg", "/d/brushed_steel_nor_gl_2k.png",
             "/d/brushed_steel_nor_dx_2k.png", "/d/brushed_steel_arm_2k.jpg",
             "/d/brushed_steel_rough_2k.jpg", "/d/brushed_steel_spec_2k.jpg"]
    out, name = pbr_maps.match_set(files)
    assert name == "brushed steel"
    assert out[files[1]][0] == "normal" and out[files[2]] is None
    assert out[files[4]][0] == "roughness"
    assert out[files[3]][0] == "arm"          # still fills AO + metallic
    assert out[files[5]] is None


# ---- Seams (after every upstream sync) --------------------------------------

@pytest.mark.parametrize("path, needle", [
    ("core/materials.py", "surface=Surface.from_dict"),
    ("formats/gltf.py", "_apply_surface(mat, surface, _pbr_image, used_ext)"),
    ("formats/meshexport.py", "def _add_face(face, materials, prims, split=None)"),
    ("views/tray.py", "SurfacePanel(window, self)"),
    ("views/tray.py", "surface=old.surface"),
    ("views/tray.py", "surface=existing.surface if existing else None"),
    ("resources/blender/render_scene.py", 'mat.get("ingetrazo_surface")'),
    ("core/i18n.py", '_I18N_DIR / "fork"'),
    ("ingetrazo.spec", "i18n/fork/*.json"),
    ("formats/glb.py", "stamp_import(scene, mesh, js, Path(path))"),
    ("views/viewport.py", "fragment_source(SHADER_DIR)"),
    ("views/viewport.py", "mode = _pbr_pass.begin_frame(self, mode)"),
    ("views/viewport.py", "_pbr_pass.geometry(self, True)"),
    ("views/viewport.py", "_pbr_pass.overlay(self)"),
    ("views/viewport.py", "_pbr_pass.geometry(self, False)"),
    ("core/style.py", 'Style(name="Realistic", face_mode="realistic")'),
    ("views/tray.py", '("realistic", "Realistic")'),
])
def test_fork_seam_is_still_in_place(path, needle):
    """Every hook the fork keeps in an upstream file (marked ``fork:pbr``).
    A sync that drops one fails here, by name, instead of silently."""
    from core.paths import app_root
    assert needle in (app_root() / path).read_text(encoding="utf-8")


# ---- GLB import (M6) ------------------------------------------------------

def test_glb_import_keeps_the_surface_and_reexports_it(tmp_path):
    from formats.glb import load_glb
    base = _flat(tmp_path / "steel_diff.png", (160, 160, 170, 255))
    arm = _flat(tmp_path / "steel_arm.png", (220, 90, 255, 255))
    nrm = _flat(tmp_path / "steel_nor_gl.png", (128, 128, 255, 255))
    s = pbr.from_kind("metal")
    s.roughness = 0.8
    s.maps = {"ao": MapSlot({"path": arm}, channel="r", strength=0.7),
              "roughness": MapSlot({"path": arm}, channel="g"),
              "metallic": MapSlot({"path": arm}, channel="b"),
              "normal": MapSlot({"path": nrm}, strength=0.6)}
    scene = Scene()
    mat = Material("Acero", texture={"path": base, "sw": 1.0, "sh": 1.0},
                   surface=s, finish="metal")
    scene.materials["Acero"] = mat
    _quad(scene).attrs.update(mat.face_attrs())
    glow = Material("LED", color=(1.0, 0.9, 0.7), surface=pbr.from_kind("light"),
                    finish="matte")
    scene.materials["LED"] = glow
    _quad(scene, 2).attrs.update(glow.face_attrs())
    out = tmp_path / "a.glb"
    save_glb(scene, out)

    again = Scene()
    load_glb(again, out)
    got = again.materials["Acero"].surface
    assert got.roughness == pytest.approx(0.8) and got.metallic == 1.0
    assert got.map("roughness").channel == "g"
    assert got.map("metallic").channel == "b"
    assert got.map("ao").strength == pytest.approx(0.7)
    assert got.map("normal").strength == pytest.approx(0.6)
    led = again.materials["LED"].surface
    assert led.kind == "light" and led.luminance == pytest.approx(3000.0, rel=1e-5)
    faces = [f for g in again.groups for f in g.mesh.faces]
    assert {f.attrs.get("mat") for f in faces} == {"Acero", "LED"}

    out2 = tmp_path / "b.glb"
    save_glb(again, out2)
    m1 = {m["name"]: m for m in _glb_json(out)["materials"]}
    m2 = {m["name"]: m for m in _glb_json(out2)["materials"]}
    for name in ("Acero", "LED"):
        for key in ("pbrMetallicRoughness", "occlusionTexture",
                    "normalTexture", "emissiveFactor", "extensions"):
            a, b = m1[name].get(key), m2[name].get(key)
            if isinstance(a, dict):
                a = {k: v for k, v in a.items() if k != "index"
                     and not isinstance(v, dict)}
                b = {k: v for k, v in b.items() if k != "index"
                     and not isinstance(v, dict)}
            assert a == b, (name, key)


def test_glb_import_leaves_plain_materials_anonymous(tmp_path):
    from formats.glb import load_glb
    scene = Scene()
    _quad(scene).attrs.update({"color": (0.3, 0.5, 0.2)})
    out = tmp_path / "p.glb"
    save_glb(scene, out)
    again = Scene()
    load_glb(again, out)
    assert not again.materials


# ---- Translations (M7) --------------------------------------------------------

def test_every_language_has_the_fork_strings():
    """Each interface language has an i18n/fork file with the same keys as
    the Spanish one, and every placeholder survives the translation."""
    import json
    import re
    from core.paths import app_root
    root = app_root() / "i18n"
    ref = json.loads((root / "fork" / "es.json").read_text(encoding="utf-8"))
    langs = [p.stem for p in root.glob("*.json") if p.stem != "en"]
    assert langs
    for lang in langs:
        path = root / "fork" / f"{lang}.json"
        assert path.exists(), lang
        got = json.loads(path.read_text(encoding="utf-8"))
        assert set(got) == set(ref), lang
        for key, text in got.items():
            assert text.strip(), (lang, key)
            assert (sorted(re.findall(r"\{\w+\}", key))
                    == sorted(re.findall(r"\{\w+\}", text))), (lang, key)
