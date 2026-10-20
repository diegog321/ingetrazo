# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The Realistic display style (views/pbr_pass.py; experimental fork, PBR
phase 2): the shader patch, the per-material uniforms, the overlay for
colour materials, the fallback, and — where the platform renders GL — the
pixels and a frame-time budget."""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QVector3D as V
from PySide6.QtWidgets import QApplication

from core import pbr, pbr_maps
from core.materials import Material, register
from core.mesh import Mesh
from core.paths import app_root
from core.pbr import MapSlot
from core.style import BUILTIN_STYLES, FACE_MODES, Style
from views import pbr_pass

_app = QApplication.instance() or QApplication([])
_FRAG = app_root() / "resources" / "shaders" / "basic.frag"


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setenv("INGETRAZO_TEXTURE_CACHE", str(tmp_path / "cache"))
    pbr_maps._derived.clear()


def _flat(path, rgba, size=8):
    import numpy as np
    from PySide6.QtGui import QImage
    arr = np.ascontiguousarray(np.tile(np.array(rgba, dtype=np.uint8),
                                       (size, size, 1)))
    QImage(arr.data, size, size, size * 4,
           QImage.Format.Format_RGBA8888).copy().save(str(path))
    return str(path)


# ---- Shader patch -------------------------------------------------------------

def test_the_patch_hooks_the_upstream_shader_once():
    src = _FRAG.read_text(encoding="utf-8")
    out = pbr_pass.patch_fragment(src)
    assert out is not None
    assert out.count("vec3 pbr_shade(vec3 c)") == 1
    assert out.count("if (pbr_active()) c.rgb = pbr_shade(c.rgb);") == 1
    assert out.count("if (pbr_active()) { pbr_lit = lit; } else {") == 1
    # Everything upstream is still there, untouched.
    stripped = [ln.strip() for ln in out.splitlines()]
    for line in src.splitlines():
        assert line.strip() in stripped or line.strip() in out


def test_a_moved_upstream_shader_keeps_the_plain_one(monkeypatch):
    src = _FRAG.read_text(encoding="utf-8")
    assert pbr_pass.patch_fragment(src.replace("out vec4 fragColor;",
                                               "out vec4 colour;")) is None
    assert pbr_pass.patch_fragment(src.replace("* lit);", "* lit2);")) is None
    monkeypatch.setenv("INGETRAZO_NO_PBR", "1")
    assert pbr_pass.fragment_source(_FRAG.parent) is None


# ---- Style ------------------------------------------------------------------

def test_realistic_is_a_style_and_survives_a_save():
    assert "realistic" in FACE_MODES
    style = next(s for s in BUILTIN_STYLES if s.name == "Realistic")
    assert style.face_mode == "realistic"
    assert Style.from_dict(style.to_dict()).face_mode == "realistic"


class _Prog:
    def __init__(self, has):
        self.has = has
        self.set = {}

    def uniformLocation(self, name):
        return 7 if self.has else -1

    def setUniformValue(self, loc, value):
        self.set[loc] = value


class _Vp:
    def __init__(self, has):
        self._program = _Prog(has)


def test_without_the_pbr_shader_realistic_draws_as_textures():
    vp = _Vp(False)
    assert not pbr_pass.available(vp)
    assert pbr_pass.begin_frame(vp, "realistic") == "textures"
    assert not vp._pbr_frame
    assert pbr_pass.begin_frame(vp, "shaded") == "shaded"
    pbr_pass.geometry(vp, True)                # a no-op, no error
    pbr_pass.overlay(vp)


# ---- Uniforms per surface -------------------------------------------------------

def test_params_follow_the_surface(tmp_path):
    steel = pbr.from_kind("metal")
    steel.roughness = 0.25
    params, emit, ao, units, bits = pbr_pass.params_for(steel)
    assert params[:3] == (0.25, 1.0, 0.0) and emit == (0.0, 0.0, 0.0)
    assert bits == 0 and not units

    lamp = pbr.from_kind("light")
    params, emit, ao, units, bits = pbr_pass.params_for(lamp)
    assert emit[0] == pytest.approx(lamp.luminance / pbr_pass.EMIT_REF)

    s = pbr.from_kind("generic")
    s.maps["roughness"] = MapSlot({"path": _flat(tmp_path / "r.png",
                                                 (100, 100, 100, 255))})
    s.maps["ao"] = MapSlot({"path": _flat(tmp_path / "a.png",
                                          (200, 200, 200, 255))}, strength=0.5)
    s.maps["normal"] = MapSlot({"path": _flat(tmp_path / "n.png",
                                              (128, 128, 255, 255))},
                               strength=2.0)
    params, emit, ao, units, bits = pbr_pass.params_for(s)
    assert bits == 1 | 2 | 4
    assert set(units) == {2, 3} and ao == 0.5 and params[3] == 2.0


def test_a_broken_map_draws_without_maps(tmp_path):
    s = pbr.from_kind("generic")
    s.maps["normal"] = MapSlot({"path": str(tmp_path / "missing.png")})
    params, emit, ao, units, bits = pbr_pass.params_for(s)
    assert bits & 4 == 0


# ---- Overlay for colour materials ------------------------------------------------

def _scene_with_boxes():
    from core.scene import Scene
    sc = Scene()
    steel = pbr.from_kind("metal")
    register(sc.materials, Material("Steel", color=(0.8, 0.8, 0.8),
                                    surface=steel))
    register(sc.materials, Material("Paint", color=(0.5, 0.5, 0.5),
                                    finish="matte"))
    m = sc.loose_mesh
    for x, attrs in ((0, {"color": [0.8, 0.8, 0.8], "mat": "Steel"}),
                     (3, {"color": [0.5, 0.5, 0.5], "mat": "Paint"}),
                     (6, {"color": [0.5, 0.5, 0.5]})):
        f = m.add_face([V(x, 0, 0), V(x + 2, 0, 0), V(x + 2, 2, 0),
                        V(x, 2, 0)])
        f.attrs = dict(attrs)
    return sc


def test_only_colour_materials_that_differ_get_an_overlay():
    sc = _scene_with_boxes()
    split = pbr_pass.overlay_split(sc)
    assert set(split) == {"Steel"}            # plain paint: the batch is right
    tris = pbr_pass.overlay_triangles(sc, split)
    assert set(tris) == {"Steel"}
    assert len(tris["Steel"]) == 2 * 3 * 8    # two triangles, 8 floats each


# ---- Pixels (where the platform renders GL) ---------------------------------------

@pytest.fixture
def gl_window():
    from views.main_window import MainWindow
    w = MainWindow()
    w.resize(900, 600)
    w.show()
    for _ in range(10):
        _app.processEvents()
    vp = w.viewport
    if getattr(vp, "_gl", None) is None or not vp.isValid():
        w._saved_version = vp.scene.version
        w.close()
        pytest.skip("no OpenGL context on this platform")
    yield w
    w._saved_version = vp.scene.version
    w.close()


def _box(mesh, x, attrs):
    p = [V(x, 0, 0), V(x + 2, 0, 0), V(x + 2, 2, 0), V(x, 2, 0)]
    q = [V(v.x(), v.y(), 2) for v in p]
    faces = [list(reversed(p)), q]
    for i in range(4):
        a, b = p[i], p[(i + 1) % 4]
        faces.append([a, b, V(b.x(), b.y(), 2), V(a.x(), a.y(), 2)])
    for pts in faces:
        f = mesh.add_face(pts)
        if f is not None:
            f.attrs = dict(attrs)


def _grab(vp, p):
    vp.update()
    _app.processEvents()
    img = vp.grabFramebuffer()
    x, y = vp._world_to_pixel(p)
    r = img.devicePixelRatio()
    return img.pixelColor(int(x * r), int(y * r))


def test_realistic_shows_metal_where_textures_shows_paint(gl_window):
    vp = gl_window.viewport
    assert pbr_pass.available(vp)
    sc = vp.scene
    register(sc.materials, Material("Steel", color=(0.6, 0.6, 0.6),
                                    surface=pbr.from_kind("metal")))
    _box(sc.loose_mesh, 0, {"color": [0.6, 0.6, 0.6], "mat": "Steel"})
    _box(sc.loose_mesh, 4, {"color": [0.6, 0.6, 0.6]})
    sc.version += 1
    vp.camera.target = V(3, 1, 1)
    vp.camera.distance = 14
    side_metal, side_paint = V(1, 0, 1), V(5, 0, 1)
    sc.display_style = Style(name="t", face_mode="textures")
    a, b = _grab(vp, side_metal), _grab(vp, side_paint)
    assert abs(a.lightnessF() - b.lightnessF()) < 0.02   # same paint
    sc.display_style = Style(name="t", face_mode="realistic")
    a, b = _grab(vp, side_metal), _grab(vp, side_paint)
    assert abs(a.lightnessF() - b.lightnessF()) > 0.05   # metal reads apart
    # The frame left the PBR block off for everything after the faces.
    assert "_get_texture" not in vp.__dict__


def test_realistic_frame_time_stays_near_textures(gl_window):
    """M11 budget: a Realistic frame costs at most ~2× a Textures frame of
    the same model (software GL included), once its overlay is built."""
    vp = gl_window.viewport
    sc = vp.scene
    register(sc.materials, Material("Steel", color=(0.6, 0.6, 0.6),
                                    surface=pbr.from_kind("metal")))
    for i in range(40):
        _box(sc.loose_mesh, i * 3, {"color": [0.6, 0.6, 0.6],
                                     "mat": "Steel" if i % 2 else None})
    sc.version += 1
    vp.camera.target = V(60, 1, 1)
    vp.camera.distance = 90

    def frame_ms(mode):
        sc.display_style = Style(name="t", face_mode=mode)
        _grab(vp, V(0, 0, 0))                  # warm up (sync, overlay)
        t = time.perf_counter()
        for _ in range(5):
            _grab(vp, V(0, 0, 0))
        return (time.perf_counter() - t) / 5 * 1000.0

    plain = frame_ms("textures")
    real = frame_ms("realistic")
    assert real < plain * 2.0 + 15.0, (plain, real)
