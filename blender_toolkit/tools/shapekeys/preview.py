"""Viewport feedback for the split being adjusted in the redo panel.

Nothing here draws anything. The mask goes into a colour attribute and the
viewport's Solid shading is switched to Color = Attribute, so *Blender* renders
it - lit, depth-sorted, and following the shape keys and modifiers that are
deforming the mesh at the time. A hand-written draw handler got none of those
for free and got all three wrong in turn: it blended against the viewport grey,
it left GPU state set for the passes after it, and it built its shell from base
coordinates while the surface on screen was deformed, so it survived only as a
coloured fringe around the silhouette.

Weight Paint mode would be more native still, and it is not available: entering
it is an operator, and running any operator replaces the redo panel the split is
being adjusted from.

The attribute and the shading setting are both temporary. `clear` puts them
back, Done calls it, and the timer calls it unprompted once the redo panel has
gone - a timer because that is a writable context and a draw handler is not.
"""

import bpy
from bpy.app.handlers import persistent

from ..weights.gradient import weight_paint_colour

# Prefixed and removed again, but it is on the user's mesh while it is there and
# would land in an export if it ever leaked - hence the timer that removes it.
ATTRIBUTE = "tk_split_mask"

# Byte colour, not float: byte attributes are sRGB, which is the space the
# weight colours are written in. A float attribute is scene-linear and would
# show them lighter than weight paint does.
ATTRIBUTE_TYPE = 'BYTE_COLOR'

SHADING = 'VERTEX'

# Long enough to cost nothing, short enough that the mesh does not sit recoloured
# after the panel goes.
INTERVAL = 0.2

_preview = None
_timer = None
_purge_pending = False

# Deliberately not used to decide anything: `context.active_operator` reads as
# None from a timer callback, always - measured in a real session, immediately
# after a UI invoke that returned FINISHED. Teardown ran from that timer, so
# "no active operator means the redo panel is gone" fired on the first beat and
# destroyed every preview 0.2 seconds after it was made. Nothing in here may
# depend on context state that only exists on the UI thread.


def stored():
    """What the last split previewed, or None. Read by Done to put it back."""
    return _preview


def _attention():
    """What the user is looking at, as plain data worth comparing.

    Selecting something else, or changing mode, is the ordinary way of saying
    "done with that" - and none of it registers as an operator, so none of it
    reaches an operator at all. Object names rather than the objects: compared
    from a timer, across undo steps, and a stale reference is a crash.
    """
    view_layer = bpy.context.view_layer
    if view_layer is None:
        return None
    active = view_layer.objects.active
    return (
        active.name if active else None,
        active.mode if active else None,
        frozenset(o.name for o in view_layer.objects if o.select_get()),
    )


def moved_on():
    """Has the user's attention left the split?

    True when the values the preview set no longer hold, or when the selection,
    the mode or the active object has changed since it was applied.
    """
    if _preview is None:
        return False
    if _attention() != _preview["attention"]:
        return True

    obj = bpy.data.objects.get(_preview["object"])
    if obj is None or obj.type != 'MESH' or obj.data.shape_keys is None:
        return True

    blocks = obj.data.shape_keys.key_blocks
    half = blocks.get(_preview["halves"][0])
    if half is None or half.value != 1.0:
        return True
    # Absent when the split dropped its source, which is not a reason to stop.
    source = blocks.get(_preview["source"])
    return source is not None and source.value != 0.0


def _viewports():
    """Every 3D viewport's shading, with the address to find it again by.

    Plain indices rather than the RNA references: those are UI data, and holding
    one across an undo is asking for a dangling pointer.
    """
    # None at register time, when the context is restricted - and register is
    # exactly when the stray-cleanup wants to look at the viewports.
    manager = bpy.context.window_manager
    for w, window in enumerate(manager.windows if manager else ()):
        screen = window.screen
        for a, area in enumerate(screen.areas if screen else ()):
            if area.type == 'VIEW_3D' and area.spaces.active is not None:
                yield (w, a), area.spaces.active.shading


def _shading_at(address):
    for found, shading in _viewports():
        if found == address:
            return shading
    return None


def _write_attribute(obj, weights):
    """Put the mask on the mesh as the colours weight paint would give it."""
    mesh = obj.data
    was_active = mesh.color_attributes.active_color
    was_active = was_active.name if was_active else ""

    existing = mesh.color_attributes.get(ATTRIBUTE)
    if existing is not None:
        mesh.color_attributes.remove(existing)
    attribute = mesh.color_attributes.new(
        name=ATTRIBUTE, type=ATTRIBUTE_TYPE, domain='POINT'
    )

    flat = []
    for weight in weights:
        flat.extend(weight_paint_colour(weight))
        flat.append(1.0)
    attribute.data.foreach_set("color", flat)
    mesh.color_attributes.active_color = attribute
    return was_active


def apply(obj, weights, source, source_value, halves, tint=True):
    """Show the mask, and hold what it took to do so.

    `tint` only decides whether the mesh is recoloured. The record is kept
    either way, because Done restores the key values from it and that is not
    optional.
    """
    global _preview
    clear()

    was_active, shading = "", []
    if tint:
        was_active = _write_attribute(obj, weights)
        for address, viewport in _viewports():
            shading.append((address, viewport.color_type))
            viewport.color_type = SHADING

    _preview = {
        "object": obj.name,
        "source": source,
        "source_value": source_value,
        "halves": halves,
        "tinted": tint,
        "active_colour": was_active,
        "shading": shading,
        # Taken last, so it records the split's own effect on the scene rather
        # than being tripped by it on the next poll.
        "attention": _attention(),
    }


def clear():
    """Take the attribute off the mesh and put the viewports back."""
    global _preview
    if _preview is None:
        return

    obj = bpy.data.objects.get(_preview["object"])
    mesh = obj.data if obj is not None and obj.type == 'MESH' else None
    if mesh is not None:
        attribute = mesh.color_attributes.get(ATTRIBUTE)
        if attribute is not None:
            mesh.color_attributes.remove(attribute)
        was_active = mesh.color_attributes.get(_preview["active_colour"])
        if was_active is not None:
            mesh.color_attributes.active_color = was_active

    for address, colour_type in _preview["shading"]:
        shading = _shading_at(address)
        if shading is not None:
            shading.color_type = colour_type

    _preview = None


def purge():
    """Take the attribute off every mesh, wherever it came from.

    `clear` can only undo a preview this session still remembers. The attribute
    is mesh data and the shading is saved in the file, but the record is module
    memory - so quitting, crashing or reloading scripts mid-preview strands both
    with nothing left to tidy them, and every later session strands another. A
    restart made a stuck tint permanent rather than fixing it.

    Names it by our own prefix, so nothing of the user's is at risk. The shading
    can only be guessed at here, because whatever it was is gone with the
    record: it goes to Blender's default, and only on a viewport still showing
    the attribute we just removed.
    """
    # The live preview's own mesh is not a stray. Without this the deferred
    # purge from enable() wipes the attribute a split has just written, and the
    # record survives to say it is still showing something that is not there.
    obj = bpy.data.objects.get(_preview["object"]) if _preview else None
    spared = obj.data if obj is not None and obj.type == 'MESH' else None

    found = [
        mesh
        for mesh in bpy.data.meshes
        if mesh is not spared and mesh.color_attributes.get(ATTRIBUTE)
    ]
    for mesh in found:
        mesh.color_attributes.remove(mesh.color_attributes[ATTRIBUTE])
    if found:
        for _address, shading in _viewports():
            if shading.color_type == SHADING:
                shading.color_type = 'MATERIAL'
    return len(found)


@persistent
def _on_load(_file):
    """A loaded file's meshes are not this session's, and neither is its UI."""
    global _preview
    _preview = None
    purge()


@persistent
def _on_save(_file):
    """Never write a preview into the file - that is what strands it."""
    clear()


def _poll():
    """Drop the preview once the panel holding it has gone.

    A write, so it cannot live in a draw handler - and a timer has no screen,
    which is why `clear` looks the object up in bpy.data rather than taking it
    from the context.

    Nothing in here may raise: Blender unregisters a timer that throws, and this
    timer is the only thing that tidies the attribute off the user's mesh when
    Done is not pressed. Losing it silently is the worst failure available.
    """
    global _purge_pending
    try:
        if _purge_pending:
            _purge_pending = False
            purge()
        if _preview is not None and moved_on():
            clear()
    except Exception:
        import traceback

        traceback.print_exc()
    return INTERVAL


def state():
    """What the preview thinks is going on. For diagnosing a stuck tint."""
    return {
        "preview": None if _preview is None else _preview["object"],
        "tinted": bool(_preview and _preview["tinted"]),
        "timer_registered": (
            _timer is not None and bpy.app.timers.is_registered(_timer)
        ),
        "moved_on": moved_on(),
        "attention": _attention(),
        "shading": [s.color_type for _address, s in _viewports()],
        "stray_meshes": [
            m.name for m in bpy.data.meshes if m.color_attributes.get(ATTRIBUTE)
        ],
    }


def enable():
    """Nothing here may touch data or the context.

    register() runs with a restricted context - no window manager, and data not
    safe to write - so a purge from in here raises, which aborts the whole
    add-on's registration partway and leaves the next attempt reporting
    "already registered as a subclass 'TK_AddonPreferences'". The file open now
    still needs cleaning, so the timer does it on its first beat instead, where
    the context is a real one.
    """
    global _timer, _purge_pending
    if _timer is None:
        _timer = _poll
        bpy.app.timers.register(_timer, persistent=True)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    if _on_save not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_on_save)
    _purge_pending = True


def disable():
    global _timer, _purge_pending
    _purge_pending = False
    # Unregister happens on shutdown too, where the context is restricted again.
    # Handlers and the timer must come off whatever the data does.
    try:
        clear()
        purge()
    except Exception:
        import traceback

        traceback.print_exc()
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if _on_save in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_on_save)
    if _timer is not None:
        if bpy.app.timers.is_registered(_timer):
            bpy.app.timers.unregister(_timer)
        _timer = None
