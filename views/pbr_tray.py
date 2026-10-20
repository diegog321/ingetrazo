# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""The Materials tray's «Surface» section and the «Load PBR set» dialog
(experimental fork; core.pbr).

Edits the surface of the ACTIVE material (the one the Paint tool holds),
after V-Ray's and Enscape's editors: a material type up front, a slider
per value with a map button beside it, a live preview ball. Every change
reaches every face that wears the material at once; a slider drag is one
undo step, committed when the slider is released.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMenu, QPushButton, QSlider, QTableWidget,
                               QToolButton, QVBoxLayout, QWidget)

from core import pbr
from core.i18n import tr
from views.fold_section import FoldSection, narrow

_IMAGES = "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp);;All (*)"
_THUMB = 22
_PREVIEW = 112


def _thumb(path, size=_THUMB) -> QPixmap | None:
    img = QImage(str(path))
    if img.isNull():
        return None
    return QPixmap.fromImage(img.scaled(
        size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation))


def _array_pixmap(arr) -> QPixmap:
    import numpy as np
    a = np.ascontiguousarray(arr)
    img = QImage(a.data, a.shape[1], a.shape[0], a.shape[1] * 4,
                 QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(img.copy())


class MapButton(QToolButton):
    """A map slot: shows the map's thumbnail (or «+»), loads an image on a
    click or a drop, and offers channel / invert / convention / clear on
    right-click — V-Ray's map button next to each value."""

    def __init__(self, panel, slot: str) -> None:
        super().__init__()
        self._panel = panel
        self.slot = slot
        self.setAcceptDrops(True)
        self.setFixedSize(_THUMB + 8, _THUMB + 8)
        self.setIconSize(QSize(_THUMB, _THUMB))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda _p: self._menu())
        self.clicked.connect(self._choose)
        self.show_map(None)

    def show_map(self, ms) -> None:
        label = tr(pbr.SLOT_LABELS[self.slot])
        pm = _thumb(ms.path) if ms is not None else None
        if ms is None:
            self.setIcon(QIcon())
            self.setText("+")
            self.setToolTip(tr("{slot}: click or drop an image to load a map",
                               slot=label))
            return
        self.setText("" if pm else "!")
        self.setIcon(QIcon(pm) if pm else QIcon())
        tip = f"{label}: {Path(ms.path).name}"
        if ms.channel != "rgb":
            tip += " · " + tr("channel {c}", c=ms.channel.upper())
        if ms.invert:
            tip += " · " + tr("inverted")
        if pm is None:
            tip += "\n" + tr("File not found — right-click to replace it")
        self.setToolTip(tip)

    def _choose(self) -> None:
        from views.filedialogs import file_dialogs
        path, _ = file_dialogs.getOpenFileName(
            self, tr("Choose a map"), "", tr(_IMAGES))
        if path:
            self._panel.set_map(self.slot, path)

    def dragEnterEvent(self, e) -> None:          # noqa: N802 (Qt)
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:               # noqa: N802 (Qt)
        urls = e.mimeData().urls()
        if urls and urls[0].isLocalFile():
            self._panel.set_map(self.slot, urls[0].toLocalFile())

    def _menu(self) -> None:
        from PySide6.QtGui import QCursor
        ms = self._panel.surface.map(self.slot) if self._panel.surface else None
        menu = QMenu(self)
        menu.addAction(tr("Load map…") if ms is None else tr("Replace map…"),
                       self._choose)
        if ms is not None:
            if self.slot not in ("normal", "emission"):
                sub = menu.addMenu(tr("Channel"))
                for ch, label in (("rgb", tr("Luminance")), ("r", "R"),
                                  ("g", "G"), ("b", "B"), ("a", "A")):
                    act = sub.addAction(label)
                    act.setCheckable(True)
                    act.setChecked(ms.channel == ch
                                   or (ch == "rgb" and ms.channel == "lum"))
                    act.triggered.connect(
                        lambda _c=False, c=ch: self._panel.edit_map(
                            self.slot, channel=c))
                inv = menu.addAction(tr("Invert (gloss map)")
                                     if self.slot == "roughness"
                                     else tr("Invert"))
                inv.setCheckable(True)
                inv.setChecked(ms.invert)
                inv.triggered.connect(lambda on: self._panel.edit_map(
                    self.slot, invert=on))
            if self.slot == "normal":
                dx = menu.addAction(tr("DirectX convention (green down)"))
                dx.setCheckable(True)
                dx.setChecked(ms.y_down)
                dx.triggered.connect(lambda on: self._panel.edit_map(
                    self.slot, y_down=on))
            menu.addSeparator()
            menu.addAction(tr("Remove map"),
                           lambda: self._panel.set_map(self.slot, None))
        menu.exec(QCursor.pos())


class _ValueRow:
    """Label · slider · number · map button, one row of the grid."""

    def __init__(self, panel, grid, row, label, field, lo, hi, step,
                 decimals=2, slot=None, suffix="") -> None:
        self.field = field
        self._panel = panel
        self._scale = 1.0 / step
        grid.addWidget(QLabel(label), row, 0)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(int(round(lo * self._scale)),
                             int(round(hi * self._scale)))
        self.spin = QDoubleSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setDecimals(decimals)
        self.spin.setSingleStep(step * 5)
        if suffix:
            self.spin.setToolTip(suffix.strip())
        # No arrows (the slider steps it): the trays share a 240 px floor
        # (tests/test_trays_stay_narrow.py) and every pixel of this row counts.
        self.spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.spin.setAlignment(Qt.AlignRight)
        narrow(self.slider)
        self.spin.setFixedWidth(52 if suffix else 40)
        grid.addWidget(self.slider, row, 1)
        grid.addWidget(self.spin, row, 2)
        self.button = None
        if slot is not None:
            self.button = MapButton(panel, slot)
            grid.addWidget(self.button, row, 3)
        self.slider.valueChanged.connect(self._slid)
        self.slider.sliderReleased.connect(panel.commit)
        self.spin.editingFinished.connect(self._typed)

    def set(self, value) -> None:
        for w in (self.slider, self.spin):
            w.blockSignals(True)
        self.slider.setValue(int(round(value * self._scale)))
        self.spin.setValue(value)
        for w in (self.slider, self.spin):
            w.blockSignals(False)

    def _slid(self, raw) -> None:
        value = raw / self._scale
        self.spin.blockSignals(True)
        self.spin.setValue(value)
        self.spin.blockSignals(False)
        self._panel.preview_value(self.field, value)
        if not self.slider.isSliderDown():       # keyboard, wheel, page click
            self._panel.commit()

    def _typed(self) -> None:
        value = self.spin.value()
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(value * self._scale)))
        self.slider.blockSignals(False)
        self._panel.preview_value(self.field, value)
        self._panel.commit()


class SurfacePanel(FoldSection):
    """The «Surface» fold of the Materials tray."""

    def __init__(self, window, materials_panel) -> None:
        super().__init__(tr("Surface (PBR)"), "materials/surface_open",
                         default_open=False)
        self._window = window
        self._materials = materials_panel
        #: The material being edited, and its working surface.
        self.material = None
        self.surface = None
        self._explicit = False
        self._shown = None                    # last surface drawn, as a dict

        col = QVBoxLayout(self.body)
        col.setContentsMargins(6, 6, 6, 6)
        col.setSpacing(6)

        top = QHBoxLayout()
        self._ball = QLabel()
        self._ball.setFixedSize(_PREVIEW, _PREVIEW)
        top.addWidget(self._ball)
        side = QVBoxLayout()
        self._name = QLabel()
        self._name.setWordWrap(True)
        self._name.setStyleSheet("font-weight: bold;")
        side.addWidget(self._name)
        self._state = QLabel()
        self._state.setWordWrap(True)
        self._state.setStyleSheet("color: palette(mid); font-size: 11px;")
        side.addWidget(self._state)
        # Material type: segmented buttons, wrapping in a narrow tray.
        from views.tray import FlowLayout
        kinds = QWidget()
        flow = FlowLayout(kinds, spacing=2)
        flow.setContentsMargins(0, 0, 0, 0)
        self._kinds = QButtonGroup(self)
        self._kinds.setExclusive(True)
        for k in pbr.KINDS:
            b = QToolButton()
            b.setText(tr(pbr.LABELS[k]))
            b.setCheckable(True)
            b.setProperty("kind", k)
            self._kinds.addButton(b)
            flow.addWidget(b)
        self._kinds.buttonClicked.connect(
            lambda b: self.set_kind(b.property("kind")))
        side.addWidget(kinds)
        side.addStretch(1)
        top.addLayout(side, 1)
        col.addLayout(top)

        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(1, 1)
        self._rough = _ValueRow(self, grid, 0, tr("Roughness"), "roughness",
                                0.0, 1.0, 0.01, slot="roughness")
        self._metal = _ValueRow(self, grid, 1, tr("Metallic"), "metallic",
                                0.0, 1.0, 0.01, slot="metallic")
        self._normal = _ValueRow(self, grid, 2, tr("Normal"), "relief",
                                 0.0, 3.0, 0.01, slot="normal")
        self._normal.slider.setToolTip(tr("Strength of the normal or bump map"))
        grid.addWidget(QLabel(tr("Bump")), 3, 0)
        self._bump_note = QLabel()
        self._bump_note.setStyleSheet("color: palette(mid); font-size: 11px;")
        self._bump_note.setWordWrap(True)
        grid.addWidget(self._bump_note, 3, 1, 1, 2)
        self._bump = MapButton(self, "bump")
        grid.addWidget(self._bump, 3, 3)
        self._ao = _ValueRow(self, grid, 4, tr("AO"), "ao", 0.0, 1.0, 0.01,
                             slot="ao")
        self._ao.slider.setToolTip(tr("Ambient occlusion amount"))
        self._lum = _ValueRow(self, grid, 5, tr("Emission"), "luminance",
                              0.0, 50000.0, 10.0, decimals=0, slot="emission",
                              suffix=" cd/m²")
        self._lum.spin.setToolTip(tr(
            "Luminance of the glowing surface. A lit LED panel is about "
            "3000 cd/m², a TV screen 300. In Blender 683 cd/m² is an "
            "emission strength of 1, the same scale as the lights' watts."))
        self._glow = QPushButton()
        self._glow.setFixedSize(_THUMB + 8, _THUMB + 8)
        self._glow.setToolTip(tr("Emission colour"))
        self._glow.clicked.connect(self._pick_glow)
        grid.addWidget(self._glow, 6, 3)
        hint = QLabel("cd/m² · " + tr("Emission colour"))
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(hint, 6, 1, 1, 2)
        col.addLayout(grid)

        btns = QHBoxLayout()
        load = QPushButton(tr("Load PBR set…"))
        load.setToolTip(tr("Make a material from the images of a downloaded "
                           "PBR set (Poly Haven, ambientCG…): each file goes "
                           "to its slot by its name"))
        load.clicked.connect(self.load_set)
        btns.addWidget(load)
        self._reset = QPushButton(tr("Reset"))
        self._reset.setToolTip(tr("Forget this surface: the material goes back "
                                  "to its finish for the render"))
        self._reset.clicked.connect(self._on_reset)
        btns.addWidget(self._reset)
        btns.addStretch(1)
        col.addLayout(btns)
        note = QLabel(tr("Kept in .igz and glTF/GLB (and Render with Blender). "
                         "SKP, DAE and OBJ keep the base colour only."))
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid); font-size: 11px;")
        col.addWidget(note)

        self._editors = [self._ball, self._rough.slider, self._rough.spin,
                         self._metal.slider, self._metal.spin,
                         self._normal.slider, self._normal.spin,
                         self._ao.slider, self._ao.spin, self._lum.slider,
                         self._lum.spin, self._glow, self._reset, self._bump,
                         *[r.button for r in self._rows()],
                         *self._kinds.buttons()]
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(15)
        self._preview_timer.timeout.connect(self._draw_preview)
        vp = getattr(window, "viewport", None)
        if vp is not None and hasattr(vp, "sceneVersionChanged"):
            vp.sceneVersionChanged.connect(lambda _v: self.refresh())
        self.refresh()

    def _rows(self):
        return (self._rough, self._metal, self._normal, self._ao, self._lum)

    # ---- What is edited ---------------------------------------------------
    def _scene(self):
        return self._window.viewport.scene

    def _target(self):
        from tools.paint import PaintTool
        if PaintTool.current_is_default:
            return None
        mat = PaintTool.current_material
        if mat is None:
            return None
        return self._scene().materials.get(mat.name) or mat

    def refresh(self) -> None:
        """Show the active material's surface (after a pick, undo, load)."""
        mat = self._target()
        self.material = mat
        on = mat is not None
        for w in self._editors:
            if w is not None:
                w.setEnabled(on)
        if not on:
            self._name.setText(tr("No named material"))
            self._state.setText(tr("Pick a material from the swatches (or "
                                   "name a colour) to edit its surface."))
            self.surface = None
            self._shown = None
            self._ball.clear()
            return
        self._explicit = mat.surface is not None
        self.surface = (mat.surface.copy() if self._explicit
                        else pbr.effective(mat))
        self._name.setText(mat.name)
        self._state.setText(
            tr("Custom surface") if self._explicit else
            tr("From its finish (automatic) — any change makes it custom"))
        self._reset.setEnabled(self._explicit)
        self._load_fields()

    def _load_fields(self) -> None:
        s = self.surface
        for b in self._kinds.buttons():
            b.setChecked(b.property("kind") == s.kind)
        self._rough.set(s.roughness)
        self._metal.set(s.metallic)
        relief = s.map("normal") or s.map("bump")
        self._normal.set(relief.strength if relief else 1.0)
        ao = s.map("ao")
        self._ao.set(ao.strength if ao else 1.0)
        self._lum.set(s.luminance)
        for row in self._rows():
            row.button.show_map(s.map(row.button.slot))
        self._bump.show_map(s.map("bump"))
        self._bump_note.setText(tr("Used when there is no normal map")
                                if s.map("normal") is None else
                                tr("The normal map wins over a bump map"))
        r, g, b = (int(c * 255) for c in (s.emission or (1.0, 1.0, 1.0))[:3])
        self._glow.setStyleSheet(f"background: rgb({r},{g},{b});")
        # A slider with a map multiplies it: say so on the label's tip.
        for row, slot in ((self._rough, "roughness"), (self._metal, "metallic")):
            row.slider.setToolTip(
                tr("Multiplies the map (1 = as the map says)")
                if s.map(slot) else "")
        self._schedule_preview()

    # ---- Edits ------------------------------------------------------------
    def preview_value(self, field, value) -> None:
        s = self.surface
        if s is None:
            return
        if field in ("roughness", "metallic", "luminance"):
            setattr(s, field, float(value))
            if field == "luminance" and value > 0 and s.emission is None:
                s.emission = (1.0, 1.0, 1.0)
        elif field == "relief":
            for slot in ("normal", "bump"):
                if s.map(slot):
                    s.maps[slot].strength = float(value)
        elif field == "ao" and s.map("ao"):
            s.maps["ao"].strength = float(value)
        self._schedule_preview()

    def commit(self) -> None:
        """Store the working surface as the material's (one undo step)."""
        if self.material is None or self.surface is None:
            return
        if (self._explicit and self.material.surface is not None
                and self.material.surface.to_dict() == self.surface.to_dict()):
            return
        from core.pbr_commands import SetMaterialSurfaceCommand
        vp = self._window.viewport
        registered = self.material.name in self._scene().materials
        vp.history.execute(SetMaterialSurfaceCommand(
            self.material.name, self.surface,
            None if registered else self.material))
        vp.notify_scene_changed()

    def set_kind(self, kind) -> None:
        if self.surface is None:
            return
        self.surface = pbr.with_kind(self.surface, kind)
        self.commit()

    def set_map(self, slot, path) -> None:
        if self.surface is None:
            return
        from core.pbr import MapSlot
        if path is None:
            self.surface.maps.pop(slot, None)
        else:
            old = self.surface.maps.get(slot)
            ms = MapSlot({"path": str(path)},
                         channel="rgb" if slot in ("normal", "emission")
                         else "r")
            if old is not None:
                ms.strength = old.strength
            from core.pbr_maps import classify
            guess = classify(path)
            if guess and guess[0] in (slot, "arm"):
                if guess[0] == "arm":
                    ms.channel = {"ao": "r", "roughness": "g",
                                  "metallic": "b"}.get(slot, ms.channel)
                else:
                    ms.channel, ms.invert, ms.y_down = guess[1:4]
            self.surface.maps[slot] = ms
            # A map arriving under a slider: the slider becomes its gain.
            if slot in ("roughness", "metallic") and old is None:
                setattr(self.surface, slot, 1.0)
            if slot == "emission" and not self.surface.luminance:
                self.surface.luminance = pbr.LUMENS_PER_WATT
                self.surface.emission = self.surface.emission or (1.0, 1.0, 1.0)
        self.commit()

    def edit_map(self, slot, **changes) -> None:
        ms = self.surface.map(slot) if self.surface else None
        if ms is None:
            return
        for k, v in changes.items():
            setattr(ms, k, v)
        self.commit()

    def _pick_glow(self) -> None:
        from views.color_dialog import get_color
        if self.surface is None:
            return
        cur = self.surface.emission or (1.0, 1.0, 1.0)
        chosen = get_color(QColor.fromRgbF(*cur[:3]), self,
                           tr("Emission colour"))
        if not chosen.isValid():
            return
        self.surface.emission = (chosen.redF(), chosen.greenF(), chosen.blueF())
        if not self.surface.luminance:
            self.surface.luminance = pbr.LUMENS_PER_WATT
        self.commit()

    def _on_reset(self) -> None:
        if self.material is None or not self._explicit:
            return
        from core.pbr_commands import SetMaterialSurfaceCommand
        self._window.viewport.history.execute(
            SetMaterialSurfaceCommand(self.material.name, None))
        self._window.viewport.notify_scene_changed()

    def open_for_active(self) -> None:
        """Unfold and show the active material (swatch menu ▸ Edit surface)."""
        self.set_open(True)
        self.refresh()

    # ---- Preview ----------------------------------------------------------
    def _schedule_preview(self) -> None:
        self._preview_timer.start()

    def _draw_preview(self) -> None:
        if self.surface is None or self.material is None:
            return
        tex = (self.material.texture or {}).get("path")
        color = self.material.color
        if color is None and tex is None:
            from tools.paint import PaintTool
            color = PaintTool.current_color
        key = (repr(self.surface.to_dict()), tex, tuple(color or ()))
        if key == self._shown:
            return
        self._shown = key
        from core.pbr_preview import render
        self._ball.setPixmap(_array_pixmap(
            render(self.surface, color=color, texture=tex, size=_PREVIEW)))

    # ---- Load PBR set -------------------------------------------------------
    def load_set(self) -> None:
        from views.filedialogs import file_dialogs
        paths, _ = file_dialogs.getOpenFileNames(
            self, tr("Choose the images of a PBR set"), "", tr(_IMAGES))
        if not paths:
            return
        dlg = PbrSetDialog(paths, self._scene().materials, self)
        if dlg.exec() != QDialog.Accepted:
            return
        mat = dlg.material()
        self.apply_set(mat)

    def apply_set(self, mat) -> None:
        """Register the material a PBR set made and make it the active
        paint: one undo step registers it, painting is the next click."""
        from core.pbr_commands import SetMaterialSurfaceCommand
        vp = self._window.viewport
        vp.history.execute(SetMaterialSurfaceCommand(mat.name, mat.surface,
                                                     mat))
        if mat.texture:
            t = mat.texture
            self._materials._apply_texture(t["path"], sw=t["sw"], sh=t["sh"],
                                           name=mat.name)
        else:
            self._materials._apply_color(mat.color, name=mat.name)
        vp.notify_scene_changed()
        self.open_for_active()
        self._window.statusBar().showMessage(
            tr("Material '{name}' created — click faces to paint it",
               name=mat.name), 4000)


# Slots the dialog offers, in its menu order: (key, label).
_DIALOG_SLOTS = (
    (None, "Skip"),
    ("base", "Base colour"),
    ("roughness", "Roughness"),
    ("gloss", "Gloss (inverted roughness)"),
    ("metallic", "Metallic"),
    ("normal", "Normal (OpenGL)"),
    ("normal_dx", "Normal (DirectX)"),
    ("bump", "Bump / height"),
    ("ao", "Ambient occlusion"),
    ("arm", "Packed AO·Rough·Metal (ARM/ORM)"),
    ("emission", "Emission"),
)


def _dialog_key(info) -> str | None:
    if info is None:
        return None
    slot, _ch, invert, y_down = info[:4]
    if slot == "roughness" and invert:
        return "gloss"
    if slot == "normal" and y_down:
        return "normal_dx"
    return slot


class PbrSetDialog(QDialog):
    """Which file goes to which slot, guessed from the names and editable
    before the material is made."""

    def __init__(self, paths, registry, parent=None) -> None:
        super().__init__(parent)
        from core.pbr_maps import match_set
        self.setWindowTitle(tr("Load PBR set"))
        self._paths = list(paths)
        self._registry = registry
        found, name = match_set(self._paths)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Material name")))
        self._name = QLineEdit(name or tr("PBR material"))
        row.addWidget(self._name, 1)
        lay.addLayout(row)
        size = QHBoxLayout()
        size.addWidget(QLabel(tr("Tile size")))
        self._sw = QDoubleSpinBox()
        self._sh = QDoubleSpinBox()
        for box in (self._sw, self._sh):
            box.setRange(0.01, 1000.0)
            box.setDecimals(2)
            box.setValue(1.0)
            box.setSuffix(" m")
        size.addWidget(self._sw)
        size.addWidget(QLabel("×"))
        size.addWidget(self._sh)
        size.addStretch(1)
        lay.addLayout(size)

        self._table = QTableWidget(len(self._paths), 2)
        self._table.setHorizontalHeaderLabels([tr("File"), tr("Slot")])
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self._combos = []
        from PySide6.QtWidgets import QTableWidgetItem
        for i, p in enumerate(self._paths):
            item = QTableWidgetItem(Path(p).name)
            pm = _thumb(p, 28)
            if pm is not None:
                item.setIcon(QIcon(pm))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._table.setItem(i, 0, item)
            combo = QComboBox()
            for key, label in _DIALOG_SLOTS:
                combo.addItem(tr(label), key)
            combo.setCurrentIndex(max(0, combo.findData(_dialog_key(found[p]))))
            combo.currentIndexChanged.connect(self._update_note)
            self._table.setCellWidget(i, 1, combo)
            self._combos.append(combo)
        self._table.setIconSize(QSize(28, 28))
        self._table.resizeColumnToContents(1)
        lay.addWidget(self._table, 1)
        self._note = QLabel()
        self._note.setWordWrap(True)
        lay.addWidget(self._note)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText(tr("Create material"))
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.resize(560, 360)
        self._update_note()

    def choices(self) -> dict:
        return {p: c.currentData() for p, c in zip(self._paths, self._combos)}

    def _update_note(self) -> None:
        keys = [k for k in self.choices().values() if k]
        notes = []
        if "bump" in keys:
            notes.append(tr("A height map is used as bump: it shows the "
                            "relief but does not move the geometry."))
        dup = {k for k in keys if keys.count(k) > 1}
        if dup:
            notes.append(tr("Two files go to the same slot; the last one "
                            "listed wins."))
        if "base" not in keys:
            notes.append(tr("No base colour image: the material starts light "
                            "grey."))
        self._note.setText(" ".join(notes))

    def material(self):
        """The Material this set makes (not yet registered)."""
        from core.materials import Material
        from core.pbr import MapSlot
        surface = pbr.from_kind("generic")
        base = None
        for path, key in self.choices().items():
            if key is None:
                continue
            if key == "base":
                base = path
            elif key == "arm":
                for slot, ch in (("ao", "r"), ("roughness", "g"),
                                 ("metallic", "b")):
                    surface.maps.setdefault(slot, MapSlot({"path": path},
                                                          channel=ch))
            elif key in ("normal", "normal_dx"):
                surface.maps["normal"] = MapSlot({"path": path},
                                                 y_down=key == "normal_dx")
            elif key == "gloss":
                surface.maps["roughness"] = MapSlot({"path": path},
                                                    channel="r", invert=True)
            elif key == "emission":
                surface.maps["emission"] = MapSlot({"path": path})
                surface.emission = (1.0, 1.0, 1.0)
                surface.luminance = pbr.LUMENS_PER_WATT
            else:
                surface.maps[key] = MapSlot({"path": path}, channel="r")
        if surface.map("roughness"):
            surface.roughness = 1.0
        if surface.map("metallic"):
            surface.metallic = 1.0
            surface.kind = "metal" if "metal" in self._name.text().lower() \
                else surface.kind
        name = self._name.text().strip() or tr("PBR material")
        unique, n = name, 2
        while unique in self._registry:
            unique = f"{name} ({n})"
            n += 1
        texture = ({"path": base, "sw": self._sw.value(),
                    "sh": self._sh.value(), "rot": 0.0} if base else None)
        return Material(unique, color=None if base else (0.8, 0.8, 0.8),
                        texture=texture, finish=surface.finish(),
                        surface=surface)
