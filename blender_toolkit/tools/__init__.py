import bpy

from ..utils import load_submodules, prefs

# Add a module here and to preferences.use_<name>; nothing else needs editing.
MODULE_NAMES = ("retopo", "mesh", "shapekeys", "weights", "rigging", "export")

load_submodules(globals(), __package__, MODULE_NAMES)

MODULES = tuple((globals()[name], f"use_{name}") for name in MODULE_NAMES)


def refresh_panels():
    """Register/unregister each module's panels to match the preferences."""
    settings = prefs(bpy.context)
    for module, flag in MODULES:
        wanted = getattr(settings, flag)
        for cls in module.ui.classes:
            registered = hasattr(bpy.types, cls.__name__)
            if wanted and not registered:
                bpy.utils.register_class(cls)
            elif not wanted and registered:
                bpy.utils.unregister_class(cls)


def register():
    for module, _ in MODULES:
        module.register()
    refresh_panels()


def unregister():
    for module, _ in reversed(MODULES):
        for cls in reversed(module.ui.classes):
            if hasattr(bpy.types, cls.__name__):
                bpy.utils.unregister_class(cls)
        module.unregister()
