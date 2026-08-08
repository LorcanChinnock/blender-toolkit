bl_info = {
    "name": "Blender Toolkit",
    "author": "Lorcan Chinnock",
    "version": (1, 0, 0),
    "blender": (5, 2, 0),
    "location": "View3D > Sidebar > Toolkit, Shift+Alt+Q",
    "description": "Retopology, shapekey, rigging and export tools for game-ready assets",
    "category": "3D View",
}

import importlib

import bpy

from . import utils

# utils has to be refreshed by hand, before anything imports a name out of it.
# It is the one module load_submodules cannot load: a Reload Scripts re-executes
# this file, and `from .utils import ...` would be resolved against the session's
# cached copy - so adding a helper to utils would raise ImportError right here,
# before the loader it lives in ever runs.
importlib.reload(utils)

# tools before ui_pie_menu: the pie imports operator classes from it, so
# reloading the pie against a stale operators module raises ImportError on any
# rename. load_submodules keeps the order it is given.
utils.load_submodules(globals(), __package__, ("preferences", "tools", "ui_pie_menu"))


def register():
    preferences.register()
    tools.register()
    ui_pie_menu.register()


def unregister():
    ui_pie_menu.unregister()
    tools.unregister()
    preferences.unregister()
