# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""PBR materials through the AI doors (experimental fork): material edits
from ``run_python`` are undoable steps, the ``material``/``paint`` helpers,
the recipe text that teaches them, and screenshots that travel inside the
bridge's reply."""
from __future__ import annotations

import base64
import json
import os
import socket
import sys
import threading
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

if QApplication.instance() is None:
    QApplication(sys.argv[:1])

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def _ask(bridge, tool, args=None):
    """One request from a worker thread while this (Qt) thread pumps."""
    app = QApplication.instance()
    out: dict = {}

    def worker():
        s = socket.socket()
        s.settimeout(20.0)
        s.connect(("127.0.0.1", bridge.port))
        try:
            s.sendall((json.dumps({"id": 1, "tool": tool,
                                   "args": args or {}}) + "\n").encode())
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(65536)
                assert chunk, "bridge closed"
                buf += chunk
            out["reply"] = json.loads(buf.split(b"\n", 1)[0])
        finally:
            s.close()

    t = threading.Thread(target=worker)
    t.start()
    while t.is_alive():
        app.processEvents()
        t.join(timeout=0.01)
    return out["reply"]


@pytest.fixture
def bridge(monkeypatch):
    from plugins.ai_bridge import _Bridge
    from views.main_window import MainWindow
    monkeypatch.setenv("INGETRAZO_AI_PORT", "0")
    win = MainWindow()
    b = _Bridge(win.viewport)
    b.start()
    yield b, win.viewport
    b.stop()
    win._saved_version = win.viewport.scene.version
    win.close()


def _run(b, code):
    reply = _ask(b, "run_python", {"code": code})
    assert reply["ok"], reply
    res = reply["result"]
    assert not res.get("error"), res
    return res


# ---- Fix 1: undoable material edits ------------------------------------------

def test_objects_and_materials_undo_as_one_step(bridge):
    b, vp = bridge
    sc = vp.scene
    groups0 = len(sc.groups)
    res = _run(b, 'g = box((0,0,0),(1,1,1), name="Cubo")\n'
                  'paint(g, material("Acero", color=(0.8,0.8,0.82), '
                  'kind="metal", roughness=0.25))')
    assert res["changed"] is True
    assert sc.materials["Acero"].surface.kind == "metal"
    assert all(f.attrs["mat"] == "Acero" for f in sc.groups[-1].mesh.faces)
    assert _ask(b, "undo")["result"]["ok"]
    assert len(sc.groups) == groups0 and "Acero" not in sc.materials
    assert _ask(b, "redo")["result"]["ok"]
    assert sc.materials["Acero"].surface.roughness == 0.25


def test_a_surface_only_edit_is_its_own_step(bridge):
    """The 21 Oct test: an edit of roughness alone said ``changed: False``
    and Ctrl+Z then took the step before it (the objects)."""
    b, vp = bridge
    sc = vp.scene
    _run(b, 'g = box((0,0,0),(1,1,1), name="Cubo")\n'
            'paint(g, material("Laca", color=(0.7,0.1,0.1), roughness=0.1))')
    groups = len(sc.groups)
    res = _run(b, 'material("Laca", kind="metal", roughness=0.6)')
    assert res["changed"] is True
    assert sc.materials["Laca"].surface.kind == "metal"
    assert _ask(b, "undo")["result"]["ok"]
    s = sc.materials["Laca"].surface
    assert s.kind == "generic" and s.roughness == pytest.approx(0.1)
    assert len(sc.groups) == groups                   # the cube stayed
    assert _ask(b, "redo")["result"]["ok"]
    assert sc.materials["Laca"].surface.roughness == pytest.approx(0.6)

    # Editing the surface object in place is covered too.
    res = _run(b, 'scene.materials["Laca"].surface.roughness = 0.9')
    assert res["changed"] is True
    assert _ask(b, "undo")["result"]["ok"]
    assert sc.materials["Laca"].surface.roughness == pytest.approx(0.6)


def test_a_new_colour_follows_onto_painted_faces_and_back(bridge):
    b, vp = bridge
    sc = vp.scene
    _run(b, 'g = box((0,0,0),(1,1,1), name="Cubo")\n'
            'paint(g, material("Pintura", color=(0.2,0.4,0.9)))')
    g = sc.groups[-1]
    _run(b, 'material("Pintura", color=(0.9,0.1,0.1))')
    assert all(tuple(f.attrs["color"][:3]) == (0.9, 0.1, 0.1)
               for f in g.mesh.faces)
    assert _ask(b, "undo")["result"]["ok"]
    assert all(tuple(f.attrs["color"][:3]) == (0.2, 0.4, 0.9)
               for f in g.mesh.faces)


def test_reading_materials_is_still_no_step(bridge):
    b, vp = bridge
    _run(b, 'material("Vidrio", kind="glass")')
    depth = len(vp.history.undo_stack)
    res = _run(b, 'print(sorted(scene.materials))')
    assert res["changed"] is False
    assert len(vp.history.undo_stack) == depth


# ---- Fix 2: helpers and recipe text ------------------------------------------------

@pytest.mark.parametrize("kind", ["generic", "metal", "glass", "water",
                                  "light"])
def test_material_builds_each_preset(bridge, kind):
    from core import pbr
    b, vp = bridge
    _run(b, f'material("M", color=(0.5,0.5,0.5), kind="{kind}")')
    s = vp.scene.materials["M"].surface
    ref = pbr.from_kind(kind)
    assert (s.kind, s.roughness, s.metallic, s.transmission) == (
        ref.kind, ref.roughness, ref.metallic, ref.transmission)
    assert vp.scene.materials["M"].finish == s.finish()


def test_material_rejects_an_unknown_kind_and_rolls_back(bridge):
    b, vp = bridge
    reply = _ask(b, "run_python", {"code": 'material("X", kind="chrome")'})
    assert reply["result"]["error"] and "kind must be one of" in (
        reply["result"]["stderr"] + str(reply["result"]["error"]))
    assert "X" not in vp.scene.materials


def test_emission_defaults_to_blender_strength_one(bridge):
    b, vp = bridge
    _run(b, 'material("Pantalla", emission=(0.2,0.5,1.0))')
    s = vp.scene.materials["Pantalla"].surface
    assert s.glows and s.luminance == 683.0


def test_the_recipe_book_teaches_pbr():
    from core.ai_recipes import reference
    text = reference()
    assert "material(" in text and "paint(" in text
    assert 'face_mode="realistic"' in text
    assert "NO toques scene.materials" not in text
    import ingetrazo_mcp as mcp
    tools = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    desc = next(t for t in tools["result"]["tools"]
                if t["name"] == "run_python")["description"]
    assert "material(" in desc


def test_query_model_lists_the_surfaces(bridge):
    b, vp = bridge
    _run(b, 'material("Cobre", kind="metal")')
    res = _ask(b, "query_model")["result"]
    assert res["surfaces"]["Cobre"] == "metal"


# ---- Fix 3: screenshots inside the reply ---------------------------------------------

def test_the_screenshot_travels_in_the_reply(bridge, monkeypatch):
    from PySide6.QtGui import QColor, QImage
    b, vp = bridge

    def fake_render(w, h):                   # no GL needed on CI
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(QColor(200, 100, 50))
        return img

    monkeypatch.setattr(vp, "render_image", fake_render)
    res = _ask(b, "screenshot", {"width": 128, "height": 96})["result"]
    png = base64.b64decode(res["png_base64"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert res["path"]                       # older MCP scripts still work


def test_the_mcp_script_uses_the_inline_picture(monkeypatch):
    import ingetrazo_mcp as mcp
    data = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
    monkeypatch.setattr(mcp, "_bridge", lambda tool, args: {
        "ok": True, "result": {"path": "/nowhere/viewport.png",
                               "png_base64": data}})
    out = mcp._call("screenshot", {})
    assert out["content"][0] == {"type": "image", "data": data,
                                 "mimeType": "image/png"}


def test_the_mcp_script_still_reads_an_older_apps_file(monkeypatch, tmp_path):
    import ingetrazo_mcp as mcp
    pic = tmp_path / "viewport.png"
    pic.write_bytes(b"\x89PNG\r\n\x1a\nold")
    monkeypatch.setattr(mcp, "_bridge", lambda tool, args: {
        "ok": True, "result": {"path": str(pic)}})
    out = mcp._call("screenshot", {})
    assert base64.b64decode(out["content"][0]["data"]).endswith(b"old")


# ---- Seams -------------------------------------------------------------------------

@pytest.mark.parametrize("path, needle", [
    ("core/ai.py", "cmd = SnapshotWithMaterials(mutate)"),
    ("core/ai.py", "and not cmd.changed_anything"),
    ("core/ai.py", "**_pbr_helpers(scene)}"),
    ("core/ai_recipes.py", "from core.pbr_recipes import RECIPES as _PBR"),
    ("plugins/ai_bridge.py", '"png_base64": png_base64(image)}'),
    ("plugins/ai_bridge.py", '"surfaces": {n: m.surface.kind'),
    ("scripts/ingetrazo_mcp.py", 'result.get("png_base64")'),
])
def test_fork_seam_is_still_in_place(path, needle):
    from core.paths import app_root
    assert needle in (app_root() / path).read_text(encoding="utf-8")
