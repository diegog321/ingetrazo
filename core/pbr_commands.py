# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeTrazo contributors.
"""Undoable edits of a material's surface (core.pbr; experimental fork).

Like :class:`core.history.SetMaterialFinishCommand`, only the registry
entry changes: faces and the classic viewport draw the same, so undo is
exact and cheap.
"""
from __future__ import annotations

from core.history import Command


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
