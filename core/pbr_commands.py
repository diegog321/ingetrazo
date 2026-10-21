# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Undoable edits of a material's surface (core.pbr; experimental fork),
and the AI executor's snapshot that covers the material registry.

Like :class:`core.history.SetMaterialFinishCommand`, only the registry
entry changes: faces and the classic viewport draw the same, so undo is
exact and cheap.
"""
from __future__ import annotations

from core.history import Command, SnapshotImport


class SetMaterialSurfaceCommand(Command):
    """Give the named material ``surface`` (``None`` = back to its finish).

    ``material`` is the recipe to REGISTER when the name is not in the
    document yet: the active paint of the tray is often a library material
    nobody has painted with, and editing its surface makes it part of the
    model, exactly as the first paint stroke would. Undo takes it out again.

    The material's ``finish`` follows the surface's type, so a build that
    knows only finishes (upstream IngeTrazo) still renders a metal as metal.
    """

    def __init__(self, name, surface, material=None) -> None:
        self._name = name
        self._surface = surface.copy() if surface is not None else None
        self._material = material
        self._old = None
        self._old_finish = None
        self._registered = False

    def do(self, scene) -> None:
        mat = scene.materials.get(self._name)
        if mat is None:
            if self._material is None:
                return
            mat = self._material
            scene.materials[self._name] = mat
            self._registered = True
        self._old = mat.surface
        self._old_finish = mat.finish
        mat.surface = self._surface.copy() if self._surface is not None else None
        if self._surface is not None:
            mat.finish = self._surface.finish()
        scene.version += 1

    def undo(self, scene) -> None:
        mat = scene.materials.get(self._name)
        if mat is None:
            return
        mat.surface = self._old
        mat.finish = self._old_finish
        if self._registered:
            scene.materials.pop(self._name, None)
            self._registered = False
        scene.version += 1


def _registry_state(scene) -> dict:
    """``name → (Material copy, its saved form)`` for the whole registry.
    Copies, not references: a script that edits a surface in place must not
    edit the snapshot with it."""
    import copy
    out = {}
    for name, mat in (getattr(scene, "materials", None) or {}).items():
        try:
            form = mat.to_dict()
        except Exception:                      # a foreign registry entry
            form = repr(mat)
        out[name] = (copy.deepcopy(mat), form)
    return out


class SnapshotWithMaterials(SnapshotImport):
    """:class:`core.history.SnapshotImport` that also covers the material
    registry, for the AI executor (core/ai.py ``run_transactional``).

    SnapshotImport records geometry, groups, layers, views, dimensions,
    labels and sheets, but not ``scene.materials``: a ``run_python`` that
    only edited a surface was no undo step at all, the bridge answered
    ``changed: False``, and the user's Ctrl+Z took the step before it.
    """

    def __init__(self, mutate) -> None:
        super().__init__(mutate)
        self.mats_before = None
        self.mats_after = None
        # Faces of EXISTING groups that a material edit repainted
        # (core.pbr_recipes.material follows a new colour onto its faces):
        # SnapshotImport restores the loose mesh only. face → (old, new).
        self.repainted: list = []

    @property
    def materials_changed(self) -> bool:
        if self.mats_before is None or self.mats_after is None:
            return False
        a = {n: f for n, (_m, f) in self.mats_before.items()}
        b = {n: f for n, (_m, f) in self.mats_after.items()}
        return a != b

    def do(self, scene) -> None:
        if self.after is None:
            from core.pbr_recipes import painted_faces
            self.mats_before = _registry_state(scene)
            old = [(f, dict(f.attrs)) for f in painted_faces(scene)]
            super().do(scene)                  # raises → nothing recorded
            self.mats_after = _registry_state(scene)
            self.repainted = [(f, a, dict(f.attrs or {})) for f, a in old
                              if (f.attrs or {}) != a]
        else:
            super().do(scene)
            if self.materials_changed:
                _restore(scene, self.mats_after)
            for f, _a, b in self.repainted:
                f.attrs = dict(b)
        self._redraw(scene)

    @property
    def changed_anything(self) -> bool:
        return self.materials_changed or bool(self.repainted)

    def undo(self, scene) -> None:
        super().undo(scene)
        if self.materials_changed:
            _restore(scene, self.mats_before)
        for f, a, _b in self.repainted:
            f.attrs = dict(a)
        self._redraw(scene)

    def _redraw(self, scene) -> None:
        # Attr edits bypass the mesh primitives: without the dirty flags the
        # viewport kept drawing a group's cached chunk, old picture and all.
        if self.changed_anything:
            from core.history import _dirty_group_chunks
            _dirty_group_chunks(scene)
            scene.version += 1


def _restore(scene, state) -> None:
    import copy
    reg = scene.materials
    reg.clear()                                # same dict: the tray holds it
    for name, (mat, _form) in state.items():
        reg[name] = copy.deepcopy(mat)
