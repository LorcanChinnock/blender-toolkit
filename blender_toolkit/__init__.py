bl_info = {
    "name": "Blender Toolkit",
    "author": "Lorcan Chinnock",
    "version": (1, 0, 0),
    "blender": (5, 2, 0),
    "location": "View3D > Sidebar > Toolkit, Shift+Alt+Q",
    "description": "Retopology, shapekey, rigging and export tools for game-ready assets",
    "category": "3D View",
}

if "bpy" in locals():
    import importlib

    # tools first: ui_pie_menu imports operator classes from it, so reloading
    # the pie against a stale operators module raises ImportError on any rename.
    importlib.reload(preferences)
    importlib.reload(tools)
    importlib.reload(ui_pie_menu)
else:
    from . import preferences
    from . import tools
    from . import ui_pie_menu

import bpy  # noqa: E402


def register():
    preferences.register()
    tools.register()
    ui_pie_menu.register()


def unregister():
    ui_pie_menu.unregister()
    tools.unregister()
    preferences.unregister()
