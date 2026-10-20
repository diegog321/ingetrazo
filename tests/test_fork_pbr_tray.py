# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The Materials tray's Surface section, the preview ball and the Load PBR
set dialog (views/pbr_tray.py, core/pbr_preview.py; experimental fork)."""
from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QVector3D as V

from core import pbr, pbr_maps
from core.materials import Material
from core.pbr import MapSlot
from core.pbr_preview import render


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setenv("INGETRAZO_TEXTURE_CACHE", str(tmp_path / "cache"))
    pbr_maps._derived.clear()


def _flat(path, rgba, size=8):
    arr = np.ascontiguousarray(np.tile(np.array(rgba, dtype=np.uint8),
                                       (size, size, 1)))
    QImage(arr.data, size, size, size * 4,
           QImage.Format.Format_RGBA8888).copy().save(str(path))
    return str(path)


@pytest.fixture
def win():
    from views.main_window import MainWindow
    w = MainWindow()
    yield w
    w._saved_version = w.viewport.scene.version
    w.close()


def _panels(w):
    from views.tray import MaterialsPanel
    panel = w.findChild(MaterialsPanel)
    return panel, panel._surface


# ---- Preview ----------------------------------------------------------------

def test_preview_shows_roughness_and_metal():
    smooth = pbr.from_kind("metal")
    smooth.roughness = 0.05
    rough = pbr.from_kind("metal")
    rough.roughness = 0.9
    a = render(smooth, (0.9, 0.9, 0.9), size=64).astype(int)
    b = render(rough, (0.9, 0.9, 0.9), size=64).astype(int)
    assert a.shape == (64, 64, 4)
    # A polished ball has a sharper, brighter highlight than a rough one.
    assert a[..., :3].max() > b[..., :3].max() or a.std() > b.std()
    glow = pbr.from_kind("light")
    g = render(glow, (0.2, 0.2, 0.2), size=64).astype(int)
    assert g[32, 32, :3].mean() > render(pbr.from_kind("generic"),
                                         (0.2, 0.2, 0.2), size=64)[32, 32, :3].mean()


def test_preview_reads_maps(tmp_path):
    nrm = _flat(tmp_path / "n.png", (200, 128, 200, 255))
    s = pbr.from_kind("generic")
    plain = render(s, (0.6, 0.6, 0.6), size=48)
    s.maps["normal"] = MapSlot({"path": nrm})
    bumped = render(s, (0.6, 0.6, 0.6), size=48)
    assert np.abs(plain.astype(int) - bumped.astype(int)).sum() > 0


# ---- Tray -------------------------------------------------------------------

def test_surface_section_edits_the_active_material(win):
    panel, surf = _panels(win)
    panel._apply_color((0.5, 0.5, 0.5), name="Acero cepillado")
    assert surf.material is not None and surf.material.name == "Acero cepillado"
    assert not surf._explicit                         # from its finish
    surf.set_kind("metal")
    scene = win.viewport.scene
    mat = scene.materials["Acero cepillado"]          # registered by the edit
    assert mat.surface.kind == "metal" and mat.finish == "metal"
    surf._rough.spin.setValue(0.6)
    surf._rough._typed()
    assert mat.surface.roughness == pytest.approx(0.6)
    win._on_undo()
    assert scene.materials["Acero cepillado"].surface.roughness == 0.3
    assert surf._rough.spin.value() == pytest.approx(0.3)   # refreshed
    win._on_undo()
    assert "Acero cepillado" not in scene.materials
    assert surf.surface is not None                   # shows the finish again


def test_slider_drag_is_one_undo_step(win):
    panel, surf = _panels(win)
    panel._apply_color((0.5, 0.5, 0.5), name="Porcelanato")
    surf.set_kind("generic")
    hist = win.viewport.history
    depth = len(hist._undo) if hasattr(hist, "_undo") else None
    surf._rough.slider.setSliderDown(True)
    for v in (10, 20, 30, 40):
        surf._rough.slider.setValue(v)
    surf._rough.slider.setSliderDown(False)
    surf.commit()
    mat = win.viewport.scene.materials["Porcelanato"]
    assert mat.surface.roughness == pytest.approx(0.4)
    if depth is not None:
        assert len(hist._undo) == depth + 1


def test_maps_load_and_keep_the_slider_as_gain(win, tmp_path):
    panel, surf = _panels(win)
    panel._apply_color((0.5, 0.5, 0.5), name="Loza")
    rough = _flat(tmp_path / "loza_roughness.png", (128, 128, 128, 255))
    surf.set_map("roughness", rough)
    mat = win.viewport.scene.materials["Loza"]
    assert mat.surface.map("roughness").path == rough
    assert mat.surface.roughness == 1.0
    surf.edit_map("roughness", invert=True)
    assert mat.surface.map("roughness").invert
    surf.set_map("roughness", None)
    assert mat.surface.map("roughness") is None


def test_default_material_disables_the_section(win):
    panel, surf = _panels(win)
    panel._apply_default()
    assert surf.material is None
    assert not surf._rough.slider.isEnabled()


def test_edit_colour_keeps_the_surface(win):
    from core.history import RestampMaterialCommand
    panel, surf = _panels(win)
    panel._apply_color((0.5, 0.5, 0.5), name="Teja")
    surf.set_kind("metal")
    assert panel._recolour_active((0.6, 0.2, 0.1))
    assert win.viewport.scene.materials["Teja"].surface.kind == "metal"


# ---- Load PBR set -------------------------------------------------------------

def test_pbr_set_dialog_builds_and_activates_the_material(win, tmp_path):
    from tools.paint import PaintTool
    from views.pbr_tray import PbrSetDialog
    files = [_flat(tmp_path / f"Bricks076C_2K_{n}.png", c) for n, c in (
        ("Color", (160, 80, 60, 255)), ("NormalDX", (128, 128, 255, 255)),
        ("Roughness", (200, 200, 200, 255)),
        ("AmbientOcclusion", (230, 230, 230, 255)),
        ("Displacement", (100, 100, 100, 255)))]
    panel, surf = _panels(win)
    dlg = PbrSetDialog(files, win.viewport.scene.materials)
    assert dlg._name.text() == "Bricks076C"
    assert "bump" in dlg._note.text().lower()
    mat = dlg.material()
    s = mat.surface
    assert mat.texture["path"] == files[0]
    assert s.map("normal").y_down and s.map("bump") is not None
    assert s.map("roughness").channel == "r" and s.roughness == 1.0
    surf.apply_set(mat)
    assert win.viewport.scene.materials[mat.name].surface is not None
    assert PaintTool.current_material.name == mat.name
    assert surf.material.name == mat.name and surf._explicit
