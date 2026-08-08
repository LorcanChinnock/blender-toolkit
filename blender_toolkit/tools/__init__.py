if "bpy" in locals():
    import importlib

    importlib.reload(retopo)
    importlib.reload(shapekeys)
    importlib.reload(rigging)
    importlib.reload(export)
else:
    from . import retopo
    from . import shapekeys
    from . import rigging
    from . import export

import bpy  # noqa: E402

from ..utils import prefs  # noqa: E402

MODULES = (
    (retopo, "use_retopo"),
    (shapekeys, "use_shapekeys"),
    (rigging, "use_rigging"),
    (export, "use_export"),
)


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
