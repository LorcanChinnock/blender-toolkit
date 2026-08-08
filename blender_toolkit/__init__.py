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

    importlib.reload(preferences)
    importlib.reload(ui_pie_menu)
    importlib.reload(tools)
else:
    from . import preferences
    from . import ui_pie_menu
    from . import tools

import bpy  # noqa: E402


def register():
    preferences.register()
    tools.register()
    ui_pie_menu.register()


def unregister():
    ui_pie_menu.unregister()
    tools.unregister()
    preferences.unregister()
