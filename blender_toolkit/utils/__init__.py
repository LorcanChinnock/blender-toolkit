"""Shared helpers. Only things with more than one caller live here."""

from types import SimpleNamespace

import bpy

ADDON_PACKAGE = __package__.split(".")[0]

# Used when the package is imported directly (tests, scripts/startup) rather
# than installed as an add-on, so there is no preferences entry to read.
_DEFAULT_PREFS = SimpleNamespace(
    use_retopo=True, use_shapekeys=True, use_rigging=True, use_export=True
)


def prefs(context):
    """AddonPreferences for this add-on."""
    addon = context.preferences.addons.get(ADDON_PACKAGE)
    return addon.preferences if addon else _DEFAULT_PREFS


class ensure_mode:
    """Context manager that switches object mode and restores the old one.

    with ensure_mode(context, 'OBJECT'):
        ...
    """

    def __init__(self, context, mode):
        self.context = context
        self.mode = mode
        self.previous = None

    def __enter__(self):
        obj = self.context.object
        self.previous = obj.mode if obj else None
        if self.previous is not None and self.previous != self.mode:
            bpy.ops.object.mode_set(mode=self.mode)
        return self

    def __exit__(self, *exc):
        obj = self.context.object
        if obj and self.previous is not None and obj.mode != self.previous:
            bpy.ops.object.mode_set(mode=self.previous)
        return False
