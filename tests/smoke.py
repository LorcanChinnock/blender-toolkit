"""Headless smoke test.

    /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python tests/smoke.py
"""

import os
import sys
import tempfile
from types import SimpleNamespace

import bmesh
import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import blender_toolkit  # noqa: E402


def raises(operator, message, **kwargs):
    """bpy.ops raises RuntimeError when an operator reports {'ERROR'}."""
    try:
        operator(**kwargs)
    except RuntimeError as exc:
        assert message in str(exc), str(exc)
        return
    raise AssertionError(f"expected an error containing {message!r}")


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def add_cube(name="Cube"):
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.active_object
    obj.name = name
    return obj


def test_retopo():
    reset()
    sculpt = add_cube("Sculpt")
    assert bpy.ops.tk.retopo_setup() == {'FINISHED'}

    retopo = bpy.context.active_object
    assert retopo.name == "Sculpt_retopo", retopo.name
    assert retopo.show_in_front
    assert len(retopo.data.vertices) == 0
    modifier = retopo.modifiers[0]
    assert modifier.type == 'SHRINKWRAP' and modifier.target == sculpt
    assert modifier.show_on_cage

    tool_settings = bpy.context.scene.tool_settings
    assert tool_settings.use_snap
    assert tool_settings.snap_elements_individual == {'FACE_PROJECT'}
    assert bpy.context.mode == 'EDIT_MESH'
    bpy.ops.object.mode_set(mode='OBJECT')


def _cube_with_keys(modifier_type):
    obj = add_cube()
    obj.shape_key_add(name="Basis")
    for name in ("Smile", "Frown", "Blink"):
        key = obj.shape_key_add(name=name)
        key.value = 0.25
        key.data[0].co.x += 0.5
    obj.modifiers.new("Mod", modifier_type)
    return obj


def test_apply_modifiers_rejects_topology_change():
    reset()
    _cube_with_keys('SUBSURF')
    raises(bpy.ops.tk.apply_modifiers_shapekeys, "change the vertex count")


def test_apply_modifiers_keeps_shapekeys():
    reset()
    obj = _cube_with_keys('CAST')  # deforms, does not change vertex count
    moved = obj.data.shape_keys.key_blocks["Smile"].data[0].co.copy()
    assert bpy.ops.tk.apply_modifiers_shapekeys() == {'FINISHED'}

    keys = obj.data.shape_keys.key_blocks
    assert [k.name for k in keys] == ["Basis", "Smile", "Frown", "Blink"], [
        k.name for k in keys
    ]
    assert not obj.modifiers
    assert keys["Smile"].value == 0.25
    # The key still carries the offset, now with the modifier baked in.
    assert keys["Smile"].data[0].co != keys["Basis"].data[0].co
    assert keys["Smile"].data[0].co != moved
    assert not [o for o in bpy.data.objects if o.name.startswith("__tk_")]


def _grid_with_key(name="Full", subdivisions=10):
    bpy.ops.mesh.primitive_grid_add(
        x_subdivisions=subdivisions, y_subdivisions=subdivisions, size=2
    )
    obj = bpy.context.active_object
    obj.shape_key_add(name="Basis")
    key = obj.shape_key_add(name=name)
    for point in key.data:
        point.co.z += 1.0
    obj.active_shape_key_index = 1
    return obj


def _band(obj, group_a, group_b, start, end, **kwargs):
    """A complementary pair the way the tool now expects: run it twice.

    There is no pair option any more - you write one group, then invert and
    write the other.
    """
    result = bpy.ops.tk.write_gradient(
        source='KEEP', start=start, end=end, group_name=group_a, **kwargs
    )
    bpy.ops.tk.write_gradient(
        source='KEEP', start=start, end=end, group_name=group_b,
        invert=True, **kwargs
    )
    return result


def flush(obj):
    """Run one beat of the session timer.

    Writes are deferred so that a drag firing an update per mouse-move event
    collapses into one write. Background Blender has no main loop, so nothing
    calls the timer - the tests have to.
    """
    from blender_toolkit.tools.weights import properties

    properties.flush(bpy.context, obj.tk_gradient)


def _offset(key, index):
    return key.data[index].co.z - key.relative_key.data[index].co.z


def test_split_shapekey():
    reset()
    obj = _grid_with_key("Smile")
    keys = obj.data.shape_keys.key_blocks

    raises(bpy.ops.tk.split_shapekey, "Missing vertex group")
    assert _band(obj, "Left", "Right", (-1, 0, 0), (1, 0, 0)) == {'FINISHED'}
    assert bpy.ops.tk.split_shapekey() == {'FINISHED'}

    # The mask is baked into the coordinates, not left as a live group.
    assert keys["Smile_L"].vertex_group == ""
    assert keys["Smile_R"].vertex_group == ""
    assert keys["Smile_L"].relative_key == keys["Basis"]
    for vert in obj.data.vertices:
        weight = obj.vertex_groups["Left"].weight(vert.index)
        assert abs(_offset(keys["Smile_L"], vert.index) - weight) < 1e-5
        assert abs(_offset(keys["Smile_R"], vert.index) - (1.0 - weight)) < 1e-5

    # Named key rather than the active one, and drop the source afterwards.
    assert bpy.ops.tk.split_shapekey(
        key="Smile_L", suffix_a="_Up", suffix_b="_Lo", keep_source=False
    ) == {'FINISHED'}
    assert "Smile_L" not in keys
    assert "Smile_L_Up" in keys

    raises(bpy.ops.tk.split_shapekey, "No shapekey named", key="Nope")


def test_split_chain():
    """Two-level split: the first mask must survive the second."""
    reset()
    obj = _grid_with_key()
    keys = obj.data.shape_keys.key_blocks

    _band(obj, "Left", "Right", (-1, 0, 0), (1, 0, 0))
    _band(obj, "Upper", "Lower", (0, -1, 0), (0, 1, 0))

    assert bpy.ops.tk.split_shapekey() == {'FINISHED'}
    assert bpy.ops.tk.split_shapekey(
        key="Full_L", group_a="Upper", group_b="Lower",
        suffix_a="_Up", suffix_b="_Lo",
    ) == {'FINISHED'}

    for vert in obj.data.vertices:
        left = obj.vertex_groups["Left"].weight(vert.index)
        upper = obj.vertex_groups["Upper"].weight(vert.index)
        assert abs(_offset(keys["Full_L_Up"], vert.index) - left * upper) < 1e-5, (
            tuple(vert.co), left, upper, _offset(keys["Full_L_Up"], vert.index)
        )

    # The corner the first split excluded must not move at all.
    corner = min(obj.data.vertices, key=lambda v: (v.co.x, -v.co.y))
    assert obj.vertex_groups["Left"].weight(corner.index) == 0.0
    assert abs(_offset(keys["Full_L_Up"], corner.index)) < 1e-6

    # The four quadrant keys must still reconstruct the original.
    bpy.ops.tk.split_shapekey(
        key="Full_R", group_a="Upper", group_b="Lower",
        suffix_a="_Up", suffix_b="_Lo",
    )
    quadrants = ("Full_L_Up", "Full_L_Lo", "Full_R_Up", "Full_R_Lo")
    for vert in obj.data.vertices:
        total = sum(_offset(keys[q], vert.index) for q in quadrants)
        assert abs(total - 1.0) < 1e-5, (vert.index, total)


def test_gradient_writes_weights():
    reset()
    obj = _grid_with_key()

    assert _band(obj, "Left", "Right", (-1, 0, 0), (1, 0, 0)) == {'FINISHED'}
    left = obj.vertex_groups["Left"]
    right = obj.vertex_groups["Right"]

    for vert in obj.data.vertices:
        a, b = left.weight(vert.index), right.weight(vert.index)
        assert abs(a + b - 1.0) < 1e-5, (vert.index, a, b)
        # Weight is the position along start -> end, remapped to 0..1.
        assert abs(a - (vert.co.x + 1.0) / 2.0) < 1e-5, (tuple(vert.co), a)

    # A band that is not axis-aligned: weight follows the diagonal.
    assert _band(obj, "Left", "Right", (-1, -1, 0), (1, 1, 0)) == {'FINISHED'}
    for vert in obj.data.vertices:
        expected = min(max((vert.co.x + vert.co.y + 2.0) / 4.0, 0.0), 1.0)
        assert abs(left.weight(vert.index) - expected) < 1e-5, tuple(vert.co)

    # Re-running must reuse the groups, not make Left.001.
    assert _band(obj, "Left", "Right", (-1, 0, 0), (1, 0, 0), smooth_repeat=2) == {
        'FINISHED'
    }
    assert [g.name for g in obj.vertex_groups] == ["Left", "Right"]
    assert abs(left.weight(0) + right.weight(0) - 1.0) < 1e-5

    raises(bpy.ops.tk.write_gradient, "same point", source='KEEP',
           start=(0, 0, 0), end=(0, 0, 0))


def test_gradient_maths():
    """Pure shape/profile/midpoint maths, no bpy."""
    from blender_toolkit.tools.weights import gradient

    path = [(0, 0, 0), (2, 0, 0)]
    start, end = path

    def f(x, **kwargs):
        return gradient.factor((x, 0, 0), path, **kwargs)

    assert f(0.0) == 0.0 and f(2.0) == 1.0
    assert abs(f(1.0) - 0.5) < 1e-9
    assert f(-5.0) == 0.0 and f(5.0) == 1.0  # clamped outside

    # Spherical: 0 at the centre, 1 at the radius, in every direction.
    assert gradient.factor((0, 0, 0), path, shape='SPHERICAL') == 0.0
    for point in ((2, 0, 0), (0, 2, 0), (0, 0, -2)):
        assert abs(gradient.factor(point, path, shape='SPHERICAL') - 1.0) < 1e-9

    # Band: 0 on the centre plane, 1 at both ends.
    assert abs(gradient.factor((1, 0, 0), path, shape='BAND')) < 1e-9
    for x in (0.0, 2.0):
        assert abs(gradient.factor((x, 0, 0), path, shape='BAND') - 1.0) < 1e-9

    # The radial shapes read only the ends, so a bend must not move them.
    bent = [(0, 0, 0), (1, 3, 0), (2, 0, 0)]
    assert gradient.factor((0, 2, 0), bent, shape='SPHERICAL') == gradient.factor(
        (0, 2, 0), path, shape='SPHERICAL'
    )

    # Midpoint slides where the ramp crosses 0.5.
    assert abs(f(0.5, midpoint=0.25) - 0.5) < 1e-9
    assert abs(f(1.5, midpoint=0.75) - 0.5) < 1e-9

    for name, _label, _desc in gradient.PROFILES:
        values = [f(x / 10.0 * 2.0, profile=name) for x in range(11)]
        assert all(0.0 <= v <= 1.0 for v in values), name
        assert all(b >= a - 1e-9 for a, b in zip(values, values[1:])), name
        if name != 'CONSTANT':
            assert values[0] == 0.0 and abs(values[-1] - 1.0) < 1e-9, name
        # Invert negates the weight rather than mirroring the position, so a
        # gradient and its inverse add to exactly 1 - even for a profile that
        # is not symmetric, where sqrt(1 - t) is nothing like 1 - sqrt(t).
        for x in (0.0, 0.3, 0.7, 1.4, 2.0):
            straight = f(x, profile=name)
            flipped = f(x, profile=name, invert=True)
            assert abs(straight + flipped - 1.0) < 1e-9, (name, x)

    # A curve callable replaces the named profile; the session passes its ramp.
    assert abs(f(0.5, curve=lambda t: t * t) - 0.0625) < 1e-9
    assert abs(f(0.5, curve=lambda t: 5.0) - 1.0) < 1e-9  # results are clamped

    try:
        gradient.factor((0, 0, 0), [start, start])
        raise AssertionError("expected ValueError for a zero-length gradient")
    except ValueError:
        pass


def test_path_factor():
    """Arc-length position along a multi-handle path."""
    from blender_toolkit.tools.weights import gradient

    straight = [(0, 0, 0), (2, 0, 0)]
    assert gradient.path_factor((0, 0, 0), straight) == 0.0
    assert gradient.path_factor((2, 0, 0), straight) == 1.0
    assert abs(gradient.path_factor((1, 5, 0), straight) - 0.5) < 1e-9  # off-axis
    assert gradient.path_factor((-9, 0, 0), straight) == 0.0  # clamped past the end

    # An L: two legs of length 2, so the corner sits at half the arc length even
    # though it is nowhere near half the straight-line distance.
    bent = [(0, 0, 0), (2, 0, 0), (2, 2, 0)]
    assert abs(gradient.path_factor((2, 0, 0), bent) - 0.5) < 1e-9
    assert abs(gradient.path_factor((1, 0, 0), bent) - 0.25) < 1e-9
    assert abs(gradient.path_factor((2, 2, 0), bent) - 1.0) < 1e-9
    # A point beside the second leg reads its arc position, not its distance
    # from the start, which is what makes a bent gradient follow the bend.
    assert abs(gradient.path_factor((3, 1, 0), bent) - 0.75) < 1e-9

    try:
        gradient.path_factor((0, 0, 0), [(1, 1, 1)])
        raise AssertionError("expected ValueError for a one-handle path")
    except ValueError:
        pass


def test_curved_sampling_budget():
    """Samples per gap fall as handles rise, so the segment count stays bounded.

    Every sample is a segment tested against every vertex, so a flat 12 per gap
    made a 32-handle curve four times the work of an 8-handle one for a curve
    its own control points already describe.
    """
    from blender_toolkit.tools.weights import gradient

    def segments(count):
        handles = [(i * 0.1, (i % 2) * 0.1, 0) for i in range(count)]
        return len(gradient.path_points(handles, curved=True)) - 1

    # Unchanged where it always was 12 a gap: the budget only bites past 11.
    for count in range(3, 12):
        assert segments(count) == (count - 1) * 12, count

    assert segments(32) <= 120, segments(32)
    assert segments(32) >= 31, "never fewer samples than there are gaps"
    # Monotonic: more handles must never mean a coarser total path.
    totals = [segments(n) for n in range(3, 33)]
    assert min(totals) == totals[0]


def test_path_curved():
    from blender_toolkit.tools.weights import gradient

    handles = [(0, 0, 0), (1, 1, 0), (2, 0, 0)]
    points = gradient.path_points(handles, curved=True, per_segment=8)

    # Catmull-Rom passes through its control points; a handle must end up where
    # it was put.
    for handle in handles:
        assert min(
            sum((a - b) ** 2 for a, b in zip(handle, p)) for p in points
        ) < 1e-12, handle

    lengths, offsets, total = gradient.segment_lengths(points)
    assert total > 0
    assert all(b >= a for a, b in zip(offsets, offsets[1:]))  # monotonic arc length

    # Collinear handles must sample back to a straight line.
    line = gradient.path_points([(0, 0, 0), (1, 0, 0), (2, 0, 0)], curved=True)
    assert all(abs(p[1]) < 1e-9 and abs(p[2]) < 1e-9 for p in line), line

    # Two handles cannot bend, so curved is a no-op there.
    assert gradient.path_points([(0, 0, 0), (1, 0, 0)], curved=True) == [
        (0, 0, 0), (1, 0, 0)
    ]


def test_array_field_matches_the_scalar_one():
    """The vectorised field is only worth having if it changes no answers.

    Swept densely and over a path that folds back on itself, which is where a
    nearest-segment shortcut goes wrong: a vertex can sit exactly equidistant
    from two segments, and the two implementations have to break that tie the
    same way or they disagree by the length of a fold.
    """
    from blender_toolkit.tools.weights import gradient

    handles = [(-1 + i * 0.25, (i % 2) * 0.5, 0) for i in range(8)]
    steps = 60
    coords = [
        (-1.5 + 3.0 * i / steps, -1.5 + 3.0 * j / steps, 0.0)
        for i in range(steps)
        for j in range(steps)
    ]

    for shape in ('LINEAR', 'SPHERICAL', 'BAND'):
        for curved in (False, True):
            points = gradient.path_points(handles, curved=curved)
            metrics = gradient.segment_lengths(points)
            array = gradient.raw_factors(coords, points, shape, metrics)
            worst = max(
                abs(a - gradient.raw_factor(co, points, shape, metrics))
                for a, co in zip(array, coords)
            )
            assert worst < 1e-9, (shape, curved, worst)

    # Both halves refuse a degenerate path the same way.
    for call in (
        lambda: gradient.raw_factor((0, 0, 0), [(1, 1, 1), (1, 1, 1)], 'LINEAR'),
        lambda: gradient.raw_factors([(0, 0, 0)], [(1, 1, 1), (1, 1, 1)], 'LINEAR'),
        lambda: gradient.raw_factors([(0, 0, 0)], [(1, 1, 1), (1, 1, 1)], 'SPHERICAL'),
    ):
        try:
            call()
        except ValueError:
            continue
        raise AssertionError("coincident handles must raise")


def test_weight_colours_round_trip():
    """Weight paint's ramp is one number - the hue - so it inverts exactly."""
    from blender_toolkit.tools.weights import gradient

    # The five colours Blender's weight paint shows at the quarter points.
    reference = {
        0.0: (0, 0, 1), 0.25: (0, 1, 1), 0.5: (0, 1, 0),
        0.75: (1, 1, 0), 1.0: (1, 0, 0),
    }
    for value, expected in reference.items():
        colour = gradient.weight_colour(value)
        assert all(abs(a - b) < 1e-6 for a, b in zip(colour[:3], expected)), colour
        assert colour[3] == 1.0
        assert abs(gradient.weight_of(colour) - value) < 1e-6

    # Dense sweep, both directions.
    for step in range(101):
        value = step / 100.0
        assert abs(gradient.weight_of(gradient.weight_colour(value)) - value) < 1e-6

    # Out of range clamps rather than wrapping the hue back round to blue.
    assert gradient.weight_colour(2.0) == gradient.weight_colour(1.0)
    assert gradient.weight_colour(-1.0) == gradient.weight_colour(0.0)
    # Past blue on the wheel - purple, magenta - clamps to zero, not to red.
    assert gradient.weight_of((0.5, 0.0, 1.0)) == 0.0
    assert gradient.weight_of((1.0, 0.0, 1.0)) == 0.0

    # A grey has no hue, so its brightness is the weight. That is what ramps
    # saved while this was a greyscale picker still mean.
    for grey in (0.0, 0.25, 0.5, 1.0):
        assert abs(gradient.weight_of((grey, grey, grey)) - grey) < 1e-6


def test_snapping():
    from blender_toolkit.tools.weights import snapping

    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0)
    obj = bpy.context.active_object
    outside = (2.0, 0.0, 0.0)

    assert tuple(snapping.snap(obj, outside, 'FREE')) == outside

    on_face = snapping.snap(obj, outside, 'FACE')
    assert abs(on_face.length - 1.0) < 1e-4, on_face.length

    on_vertex = snapping.snap(obj, outside, 'VERTEX')
    assert any(
        (v.co - on_vertex).length < 1e-6 for v in obj.data.vertices
    ), tuple(on_vertex)

    # An edge point lies on some polygon edge of the sphere.
    on_edge = snapping.snap(obj, outside, 'EDGE')
    best = min(
        (snapping._nearest_on_segment(
            on_edge, obj.data.vertices[a].co, obj.data.vertices[b].co
        ) - on_edge).length
        for polygon in obj.data.polygons
        for a, b in polygon.edge_keys
    )
    assert best < 1e-6, best

    # A non-mesh object must pass the point straight through, not explode.
    bpy.ops.object.empty_add()
    assert tuple(snapping.snap(bpy.context.active_object, outside, 'VERTEX')) == outside


def test_snapping_uses_base_geometry():
    """Every mode lands on the base cage, whatever the evaluated mesh looks like.

    `ray_cast` and `closest_point_on_mesh` query evaluated geometry, so indexing
    `obj.data` with the polygon they return put FACE on the deformed surface and
    VERTEX/EDGE on the base one - and raised outright once a modifier changed
    the topology.
    """
    from blender_toolkit.tools.weights import snapping

    reset()
    obj = _grid_with_key(subdivisions=4)
    obj.active_shape_key.value = 1.0  # the evaluated surface a metre above base
    obj.modifiers.new("Sub", 'SUBSURF').levels = 2  # base 16 polygons, evaluated 256
    bpy.context.view_layer.update()

    probe = Vector((0.3, 0.3, 0.0))
    for mode in ('FACE', 'VERTEX', 'EDGE'):
        got = snapping.snap(obj, probe, mode)
        assert abs(got.z) < 1e-6, (mode, tuple(got))  # the flat base, not z = 1

    # The cache keys on topology alone, so moving vertices needs an explicit
    # drop - which is what the depsgraph handler fires on a real mesh edit.
    for vert in obj.data.vertices:
        vert.co.x += 10.0
    snapping.invalidate()
    assert snapping.snap(obj, Vector((10.3, 0.3, 0.0)), 'VERTEX').x > 9.0

    # A depsgraph update that did not touch geometry must leave the tree alone.
    # Writing a handle position fires the handler once per mouse-move event, and
    # rebuilding there costs ~31 ms per 40k verts inside the drag.
    from types import SimpleNamespace

    from blender_toolkit.tools.weights import overlay

    built = snapping._tree_cache[0]
    overlay._on_depsgraph(None, SimpleNamespace(
        updates=[SimpleNamespace(is_updated_geometry=False)]
    ))
    assert snapping._tree_cache[0] == built, "kept when only a property changed"
    overlay._on_depsgraph(None, SimpleNamespace(
        updates=[SimpleNamespace(is_updated_geometry=True)]
    ))
    assert snapping._tree_cache[0] is None, "dropped when the mesh really moved"


def test_snapping_follows_the_cursor():
    """Snap to what the view ray hits, not to whatever is nearest in 3D.

    A handle dragged past the surface is still under the cursor on the near
    side; snapping by 3D proximity puts it on the far side of the mesh, which
    is what made dragging feel wrong.
    """
    from types import SimpleNamespace

    from mathutils import Matrix

    from blender_toolkit.tools.weights import snapping

    reset()
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=1.0)
    obj = bpy.context.active_object

    # Front view: viewer at -Y looking towards +Y.
    view = Matrix.Rotation(1.5707963, 4, 'X')
    view.translation = (0.0, -10.0, 0.0)
    region_data = SimpleNamespace(view_matrix=view.inverted(), is_perspective=True)

    dragged = Vector((0.0, 3.0, 0.0))  # beyond the sphere, cursor still over it
    for mode in ('FACE', 'VERTEX', 'EDGE'):
        with_ray = snapping.snap(obj, dragged, mode, region_data)
        nearest = snapping.snap(obj, dragged, mode, None)
        assert with_ray.y < 0.0, (mode, tuple(with_ray))   # the near face
        assert nearest.y > 0.0, (mode, tuple(nearest))     # what it used to do

    # An orthographic view has no eye position; the ray still points the way.
    ortho = SimpleNamespace(view_matrix=view.inverted(), is_perspective=False)
    assert snapping.snap(obj, dragged, 'FACE', ortho).y < 0.0

    # A ray that misses falls back rather than dropping the handle at the origin.
    away = Vector((50.0, 3.0, 0.0))
    assert snapping.snap(obj, away, 'FACE', region_data).length <= 1.0 + 1e-5


def test_handles_follow_ramp_stops():
    """The gradient is the single control for how many handles there are, and a
    stop's position on the bar is its handle's weight."""
    from blender_toolkit.tools.weights import gradient, properties

    reset()
    obj = _grid_with_key()
    settings = obj.tk_gradient
    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='X') == {'FINISHED'}
    stops = properties.ramp_of(settings).elements
    assert len(settings.handles) == len(stops) == 2
    assert [h.weight for h in settings.handles] == [0.0, 1.0]

    # A new stop grows a handle, seeded midway between its neighbours.
    ends = [tuple(h.position) for h in settings.handles]
    stops.new(0.5)
    assert properties.sync_handles_to_ramp(settings) is True
    assert len(settings.handles) == 3
    assert [round(h.weight, 3) for h in settings.handles] == [0.0, 0.5, 1.0]
    middle = Vector(settings.handles[1].position)
    assert (middle - (Vector(ends[0]) + Vector(ends[1])) * 0.5).length < 1e-6
    assert tuple(settings.handles[0].position) == ends[0]
    assert tuple(settings.handles[2].position) == ends[1]

    # Idempotent: nothing changed, so nothing to do.
    assert properties.sync_handles_to_ramp(settings) is False

    # Dragging a handle in 3D must survive the next stop being added.
    settings.handles[1].position = (0.0, 0.7, 0.0)
    stops.new(0.75)
    assert properties.sync_handles_to_ramp(settings) is True
    assert len(settings.handles) == 4
    assert [round(h.weight, 3) for h in settings.handles] == [0.0, 0.5, 0.75, 1.0]
    assert abs(settings.handles[1].position.y - 0.7) < 1e-6  # kept, not reseeded

    # Sliding a stop sets that handle's weight and moves nothing in 3D.
    before = [tuple(h.position) for h in settings.handles]
    stops[2].position = 0.6
    assert properties.sync_handles_to_ramp(settings) is False
    assert [tuple(h.position) for h in settings.handles] == before
    assert [round(h.weight, 3) for h in settings.handles] == [0.0, 0.5, 0.6, 1.0]
    # And the handle recolours to the weight it now carries.
    assert all(
        abs(a - b) < 1e-6 for a, b in zip(
            properties.handle_colours(settings)[2], gradient.weight_colour(0.6)
        )
    ), properties.handle_colours(settings)[2]

    # Removing stops takes the handles with them.
    while len(stops) > 2:
        stops.remove(stops[1])
    assert properties.sync_handles_to_ramp(settings) is True
    assert len(settings.handles) == 2

    bpy.ops.tk.cancel_gradient()


def test_ramp_edits_are_noticed():
    """The ramp has no update callback, so a change is spotted by signature."""
    from blender_toolkit.tools.weights import overlay, properties

    reset()
    obj = _grid_with_key()
    settings = obj.tk_gradient
    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='X') == {'FINISHED'}

    properties.take_dirty()  # start leaves work pending; begin from a clean slate
    overlay._signature = properties.ramp_signature(settings)
    assert overlay._sync() is not None
    assert not properties.take_dirty(), "an untouched ramp is not a change"

    properties.ramp_of(settings).elements[0].position = 0.25
    overlay._sync()
    assert properties.take_dirty() is False, "_sync writes and clears the flag"

    # Moving a stop is a weight edit, and the weights follow it: the low end of
    # the gradient now bottoms out at 0.25 rather than at zero.
    assert abs(settings.handles[0].weight - 0.25) < 1e-6
    group = obj.vertex_groups[settings.group_name]
    low = min(obj.data.vertices, key=lambda v: v.co.x)
    assert abs(group.weight(low.index) - 0.25) < 1e-5, group.weight(low.index)
    bpy.ops.tk.cancel_gradient()


def test_handle_setter_snaps_live():
    """The gizmo's setter snaps as it writes, so a drag lands on the geometry.

    The gizmo reads its position back through the getter, so what it draws is
    the snapped point rather than the raw cursor - which is what keeps the disc
    and the path the same point mid-drag.
    """
    from blender_toolkit.tools.weights import overlay

    reset()
    obj = _grid_with_key()
    settings = obj.tk_gradient
    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='X') == {'FINISHED'}
    get, set = overlay._handle_accessors(0)

    settings.snap = 'FREE'
    set((0.3, 0.3, 5.0))
    assert Vector(get()) == Vector((0.3, 0.3, 5.0)), "FREE stores it untouched"

    settings.snap = 'VERTEX'
    set((0.3, 0.3, 5.0))
    landed = Vector(get())
    assert landed.z == 0.0 and any(
        (v.co - landed).length < 1e-6 for v in obj.data.vertices
    ), tuple(landed)
    # What the gizmo draws and what the path is built from are one value.
    assert Vector(settings.handles[0].position) == landed

    # A gizmo past the end of the handles must not raise.
    overlay._handle_accessors(99)[1]((0.0, 0.0, 0.0))
    assert overlay._handle_accessors(99)[0]() == [0.0, 0.0, 0.0]

    bpy.ops.tk.cancel_gradient()


def test_cancel_resets_the_session():
    """Cancel leaves no persisted state for the next session to inherit."""
    from blender_toolkit.tools.weights import properties

    reset()
    obj = _grid_with_key()
    settings = obj.tk_gradient
    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='X') == {'FINISHED'}

    properties.ramp_of(settings).elements.new(0.5)
    properties.sync_handles_to_ramp(settings)
    settings.invert = True
    settings.curved = True
    assert len(settings.handles) == 3

    assert bpy.ops.tk.cancel_gradient() == {'FINISHED'}
    assert len(properties.ramp_of(settings).elements) == 2
    assert not settings.invert and not settings.curved
    assert len(settings.handles) == 0  # reseeded on the next Start

    # And the next session comes up with the default two handles.
    assert bpy.ops.tk.start_gradient() == {'FINISHED'}
    assert len(settings.handles) == 2
    bpy.ops.tk.cancel_gradient()


def test_handle_arc_positions():
    """Where a handle sits along the path, taken from the polyline's shape.

    path_factor is the wrong tool for this and the reason the helper exists: it
    returns the nearest point on the *whole* path, so a handle that folds back
    onto an earlier stretch reports that stretch's position instead of its own.
    """
    from blender_toolkit.tools.weights import gradient

    handles = [(0, 0, 0), (1, 0, 0), (3, 0, 0)]
    for curved in (False, True):
        points = gradient.path_points(handles, curved=curved)
        arcs = gradient.handle_arc_positions(points, len(handles), curved=curved)
        assert arcs[0] == 0.0 and abs(arcs[-1] - 1.0) < 1e-9, arcs
        assert all(b > a for a, b in zip(arcs, arcs[1:])), arcs
        # Evenly spaced in *arc length*, not in index: the second gap is twice
        # the first, so the middle handle lands a third of the way along.
        assert abs(arcs[1] - 1.0 / 3.0) < 1e-6, arcs

    # A path that returns exactly to where it started. The last handle is at the
    # far end of the arc; path_factor cannot tell it from the first.
    doubled = [(0, 0, 0), (1, 0, 0), (0, 0, 0)]
    points = gradient.path_points(doubled)
    arcs = gradient.handle_arc_positions(points, 3)
    assert arcs == [0.0, 0.5, 1.0], arcs
    # path_factor cannot tell the two ends apart - it sees one point equidistant
    # from both stretches and blends them to the middle. That is exactly the
    # confusion handle_arc_positions exists to avoid.
    assert abs(gradient.path_factor(doubled[-1], points) - 0.5) < 1e-6, (
        gradient.path_factor(doubled[-1], points)
    )

    # Coincident handles mid-drag divide by nothing; spread rather than crash.
    flat = [(1, 1, 1)] * 3
    assert gradient.handle_arc_positions(gradient.path_points(flat), 3) == [
        0.0, 0.5, 1.0
    ]


def test_path_field_is_continuous():
    """No hard bands on the concave side of a bend.

    Taking the nearest segment outright is discontinuous: at a bend a point is
    equidistant from two stretches whose arc positions are far apart, and the
    weight jumps across that tie line. Smoothing cannot fix it - it diffuses as
    1/sqrt(passes), so twenty passes took a 0.52 step only down to 0.07.
    """
    from blender_toolkit.tools.weights import gradient

    zigzag = [(-2, 0, 0), (-1, 0.5, 0), (0, -0.2, 0), (1, 0.5, 0), (2, 0, 0)]
    points = gradient.path_points(zigzag)
    metrics = gradient.segment_lengths(points)
    at = lambda x, y: gradient.path_factor((x, y, 0.0), points, metrics)

    for y in (-1.5, -1.0, -0.5, 0.0, 0.5, 0.9):
        samples = [(-2.5 + 5.0 * i / 400, y) for i in range(401)]
        values = [at(x, y) for x, y in samples]
        index = max(range(400), key=lambda k: abs(values[k + 1] - values[k]))
        # Squeeze the widest step down to nothing. A steep gradient shrinks with
        # the bracket; a jump would not.
        low, high = samples[index][0], samples[index + 1][0]
        a, b = values[index], values[index + 1]
        for _ in range(60):
            mid = 0.5 * (low + high)
            value = at(mid, y)
            if abs(value - a) >= abs(b - value):
                high, b = mid, value
            else:
                low, a = mid, value
        assert abs(b - a) < 1e-9, f"y={y} still steps by {abs(b - a)}"

    # A handle is not smeared by its neighbours: the two segments meeting at a
    # bend both report that corner's arc position, so they agree exactly.
    arcs = gradient.handle_arc_positions(points, len(zigzag))
    for handle, arc in zip(zigzag, arcs):
        assert abs(at(handle[0], handle[1]) - arc) < 1e-9, handle


def test_weight_curve():
    """Piecewise-linear through the handles, flat outside the outermost."""
    from blender_toolkit.tools.weights import gradient

    curve = gradient.weight_curve([(0.0, 0.0), (0.5, 0.9), (1.0, 1.0)])
    assert curve(0.0) == 0.0 and curve(1.0) == 1.0
    assert abs(curve(0.25) - 0.45) < 1e-9
    assert abs(curve(0.75) - 0.95) < 1e-9
    # Held flat past the ends, and unsorted knots are sorted first.
    assert curve(-1.0) == 0.0 and curve(2.0) == 1.0
    assert gradient.weight_curve([(1.0, 1.0), (0.0, 0.2)])(0.0) == 0.2
    # Two handles at the same place along the path must not divide by zero.
    # Which of the two wins is arbitrary; that it answers at all is not.
    assert gradient.weight_curve([(0.5, 0.1), (0.5, 0.8)])(0.5) == 0.1
    assert gradient.weight_curve([(0.0, 0.0), (0.5, 0.1), (0.5, 0.8)])(0.5) == 0.8
    assert gradient.weight_curve([]) is None
    assert gradient.weight_curve([(0.3, 0.4)])(0.9) == 0.4

    # The profile shapes the travel between two knots, never the knots
    # themselves - a handle has to keep reading exactly what its stop says.
    eased = gradient.weight_curve(
        [(0.0, 0.0), (0.5, 0.9), (1.0, 1.0)], ease=gradient.PROFILE_CURVES['SHARP']
    )
    assert eased(0.0) == 0.0 and abs(eased(0.5) - 0.9) < 1e-9 and eased(1.0) == 1.0
    assert eased(0.25) < curve(0.25), "SHARP starts slower than linear"
    assert abs(eased(0.25) - 0.9 * 0.25) < 1e-9  # local 0.5, squared


def test_ramp_values():
    """The curve is built from the handles, not read off the ramp."""
    from blender_toolkit.tools.weights import gradient, properties

    reset()
    obj = _grid_with_key()
    settings = obj.tk_gradient
    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='X') == {'FINISHED'}
    ramp = properties.ramp_of(settings)
    assert len(ramp.elements) == 2
    # The bar's ends are weight zero and weight one.
    assert tuple(ramp.elements[0].color) == gradient.weight_colour(0.0)
    assert tuple(ramp.elements[-1].color) == gradient.weight_colour(1.0)
    assert ramp.color_mode == 'HSV' and ramp.hue_interpolation == 'CCW'

    # A third handle, three-quarters of the way along, weighing 0.25.
    ramp.elements.new(0.25)
    properties.sync_handles_to_ramp(settings)
    assert len(settings.handles) == 3
    ends = [Vector(settings.handles[0].position), Vector(settings.handles[2].position)]
    settings.handles[1].position = ends[0] + (ends[1] - ends[0]) * 0.75

    points = properties.path_of(settings)
    assert [round(a, 3) for a in properties.handle_arcs(settings, points)] == [
        0.0, 0.75, 1.0
    ]
    curve = properties.ramp_curve(settings, points)
    # Knots at (0, 0), (0.75, 0.25) and (1, 1): the weight ramps slowly to the
    # third handle and then races. The ramp widget knows nothing about this -
    # the stop only said "0.25", the handle said where.
    assert abs(curve(0.0) - 0.0) < 1e-6
    assert abs(curve(0.375) - 0.125) < 1e-5
    assert abs(curve(0.75) - 0.25) < 1e-5
    assert abs(curve(0.875) - 0.625) < 1e-5
    assert abs(curve(1.0) - 1.0) < 1e-6

    path = [(0, 0, 0), (2, 0, 0)]
    assert abs(gradient.factor((1, 0, 0), path, curve=curve) - curve(0.5)) < 1e-6
    # Inverting negates: the pair adds to 1 wherever you sample it.
    for co in ((0.0, 0, 0), (0.5, 0, 0), (1.3, 0, 0), (2.0, 0, 0)):
        straight = gradient.factor(co, path, curve=curve)
        flipped = gradient.factor(co, path, curve=curve, invert=True)
        assert abs(straight + flipped - 1.0) < 1e-6, co

    bpy.ops.tk.cancel_gradient()


def test_ramp_is_a_weight_scale():
    """The bar is a fixed scale you read a weight off, not a colour picker."""
    from blender_toolkit.tools.weights import gradient, properties

    reset()
    obj = _grid_with_key()
    settings = obj.tk_gradient
    ramp = properties.ensure_ramp(settings).color_ramp

    # A stop's colour is the scale's colour at its position, whatever was
    # picked. That is what makes Blender's own picker inert.
    ramp.elements[0].position = 0.4
    ramp.elements[0].color = (0.9, 0.3, 0.0, 0.5)
    assert properties.normalise_ramp(settings) is True
    position = ramp.elements[0].position
    assert tuple(ramp.elements[0].color) == gradient.weight_colour(position)
    assert abs(gradient.weight_of(ramp.elements[0].color) - 0.4) < 1e-6

    # Idempotent: already on the scale, nothing to do.
    assert properties.normalise_ramp(settings) is False

    # Moving the stop recolours it, because the colour is the position.
    ramp.elements[0].position = 0.8
    assert properties.normalise_ramp(settings) is True
    assert abs(gradient.weight_of(ramp.elements[0].color) - 0.8) < 1e-6

    # Switching the widget's own dropdowns off HSV is undone: in RGB the bar
    # would run blue to red through purple, which is no weight at all.
    ramp.color_mode = 'RGB'
    assert properties.normalise_ramp(settings) is True
    assert ramp.color_mode == 'HSV' and ramp.hue_interpolation == 'CCW'

    # Blender's own floor is one stop; ours is two.
    ramp.elements.remove(ramp.elements[0])
    assert len(ramp.elements) == 1
    assert properties.normalise_ramp(settings) is True
    assert len(ramp.elements) == 2


def test_gradient_mask():
    reset()
    obj = _grid_with_key()

    protect = obj.vertex_groups.new(name="Protect")
    for vert in obj.data.vertices:
        protect.add([vert.index], 1.0 if vert.co.x > 0 else 0.0, 'REPLACE')
    prior = obj.vertex_groups.new(name="Left")
    for vert in obj.data.vertices:
        prior.add([vert.index], 0.25, 'REPLACE')

    assert _band(obj, "Left", "Right", (-1, 0, 0), (1, 0, 0),
                 mask_group="Protect") == {'FINISHED'}
    for vert in obj.data.vertices:
        weight = prior.weight(vert.index)
        if vert.co.x > 0:  # inside the mask, overwritten
            assert abs(weight - (vert.co.x + 1.0) / 2.0) < 1e-5
        else:  # outside it, the prior 0.25 survives
            assert abs(weight - 0.25) < 1e-5, (tuple(vert.co), weight)

    # One run writes exactly one group; the complement is a second run.
    reset()
    obj = _grid_with_key()
    assert bpy.ops.tk.write_gradient(
        source='KEEP', start=(-1, 0, 0), end=(1, 0, 0), group_name="Solo"
    ) == {'FINISHED'}
    assert [g.name for g in obj.vertex_groups] == ["Solo"]

    assert bpy.ops.tk.write_gradient(
        source='KEEP', start=(-1, 0, 0), end=(1, 0, 0),
        group_name="Solo.Other", invert=True,
    ) == {'FINISHED'}
    for vert in obj.data.vertices:
        a = obj.vertex_groups["Solo"].weight(vert.index)
        b = obj.vertex_groups["Solo.Other"].weight(vert.index)
        assert abs(a + b - 1.0) < 1e-5, (a, b)


def test_gradient_session():
    from blender_toolkit.tools.weights import overlay

    reset()
    obj = _grid_with_key()
    settings = obj.tk_gradient

    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='X') == {'FINISHED'}
    assert settings.active
    assert obj.mode == 'WEIGHT_PAINT'
    assert overlay._handler is not None
    assert settings.previous_mode == 'OBJECT'
    def weights():  # the name is chosen by auto-naming, so read it back
        group = obj.vertex_groups[settings.group_name]
        return [group.weight(v.index) for v in obj.data.vertices]

    before = weights()

    # A setting change rewrites the weights, one beat later.
    settings.invert = True
    flush(obj)
    assert all(abs(a + b - 1.0) < 1e-5 for a, b in zip(before, weights()))

    created = settings.group_name
    assert bpy.ops.tk.cancel_gradient() == {'FINISHED'}
    assert not settings.active
    assert overlay._handler is None
    assert obj.mode == 'OBJECT'
    assert created not in obj.vertex_groups  # Cancel removes what it created

    # Add keeps the weights and moves on to the next group by itself: no
    # renaming step to know about, and the next one starts from defaults.
    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='Y') == {'FINISHED'}
    first = settings.group_name
    kept = weights()
    span = [tuple(h.position) for h in settings.handles]
    assert bpy.ops.tk.add_gradient() == {'FINISHED'}
    assert settings.active, "Add must not end the session"
    assert obj.mode == 'WEIGHT_PAINT'
    assert [
        obj.vertex_groups[first].weight(v.index) for v in obj.data.vertices
    ] == kept
    assert settings.group_name != first, "Add advances to the next group"
    assert settings.group_name not in obj.vertex_groups
    assert settings.invert is False, "the next gradient starts from defaults"
    assert [tuple(h.position) for h in settings.handles] == span, "path stays put"
    assert settings.added_names == first

    # A second group without leaving, inverted so the pair adds to 1. Finish
    # keeps it and closes, where Cancel would have thrown it away.
    settings.group_name = "Second"
    settings.invert = True
    assert bpy.ops.tk.finish_gradient() == {'FINISHED'}
    assert not settings.active
    assert obj.mode == 'OBJECT'
    assert {first, "Second"} <= {g.name for g in obj.vertex_groups}
    for vert in obj.data.vertices:
        a = obj.vertex_groups[first].weight(vert.index)
        b = obj.vertex_groups["Second"].weight(vert.index)
        assert abs(a + b - 1.0) < 1e-5, (a, b)


def test_session_backup_round_trips_membership():
    """The way back is data on the object, and it restores non-membership too.

    A dict keyed on the object name does not survive undo, a save or a rename;
    a backup group does. It has to mirror membership exactly, or a vertex that
    was outside the group comes back as a member weighing zero.
    """
    from blender_toolkit.tools.weights import properties

    reset()
    obj = _grid_with_key()
    settings = obj.tk_gradient

    # Half the vertices in the group, the rest deliberately not members.
    prior = obj.vertex_groups.new(name="Partial")
    inside = [v.index for v in obj.data.vertices if v.co.x > 0]
    for index in inside:
        prior.add([index], 0.3, 'REPLACE')

    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='X') == {'FINISHED'}
    settings.group_name = "Partial"
    flush(obj)
    assert properties.BACKUP_PREFIX + "Partial" in obj.vertex_groups
    assert obj[properties.SESSION_KEY]["Partial"] == "borrowed"

    assert bpy.ops.tk.cancel_gradient() == {'FINISHED'}
    members = {
        v.index: {g.group: g.weight for g in v.groups} for v in obj.data.vertices
    }
    assert [i for i, w in members.items() if prior.index in w] == inside
    assert all(abs(members[i][prior.index] - 0.3) < 1e-6 for i in inside)

    # Nothing of the session's own is left lying about.
    assert not [g for g in obj.vertex_groups if g.name.startswith(properties.BACKUP_PREFIX)]
    assert properties.SESSION_KEY not in obj

    # Add commits instead, and clears the way back just the same.
    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='X') == {'FINISHED'}
    settings.group_name = "Partial"
    flush(obj)
    assert bpy.ops.tk.add_gradient() == {'FINISHED'}
    assert properties.SESSION_KEY not in obj
    assert not [g for g in obj.vertex_groups if g.name.startswith(properties.BACKUP_PREFIX)]
    assert any(prior.weight(i) != 0.3 for i in inside), "the write must stand"
    bpy.ops.tk.cancel_gradient()


def test_factor_cache_matches_the_long_way_round():
    """The cache is keyed on what the field depends on, so it must never differ
    from recomputing, and must not survive a change to the path or the shape."""
    from blender_toolkit.tools.weights import gradient, properties

    reset()
    obj = _grid_with_key()
    settings = obj.tk_gradient
    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='X') == {'FINISHED'}

    def uncached():
        points = properties.path_of(settings)
        metrics = gradient.segment_lengths(points)
        return [
            gradient.raw_factor(v.co, points, settings.shape, metrics)
            for v in obj.data.vertices
        ]

    for change in (
        lambda: None,
        lambda: setattr(settings.handles[0], "position", (-0.5, 0.4, 0.0)),
        lambda: setattr(settings, "curved", True),
        lambda: setattr(settings, "shape", 'SPHERICAL'),
    ):
        change()
        flush(obj)
        cached = properties._raw_factors(obj, settings, properties.path_of(settings))
        assert all(abs(a - b) < 1e-6 for a, b in zip(cached, uncached())), (
            settings.shape, settings.curved
        )

    bpy.ops.tk.cancel_gradient()


def test_record_formats_still_load():
    """Three handle shapes have been written; all three must still mean it."""
    from blender_toolkit.tools.weights import properties

    reset()
    obj = _grid_with_key()
    settings = obj.tk_gradient
    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='X') == {'FINISHED'}

    base = {key: getattr(settings, key) for key in properties.RECORDED}
    positions = [(-1.0, 0.0, 0.0), (0.0, 0.5, 0.0), (1.0, 0.0, 0.0)]

    # Current: weight first, then the position.
    obj[properties.RECORD_KEY] = {"Now": {
        **base, "handles": [[w, *p] for w, p in zip((0.0, 0.4, 1.0), positions)],
    }}
    assert properties.load_record(obj, settings, "Now") is True
    assert [round(h.weight, 3) for h in settings.handles] == [0.0, 0.4, 1.0]

    # Previous: t first, weights in a separate "stops" list.
    obj[properties.RECORD_KEY] = {"Then": {
        **base,
        "handles": [[t, *p] for t, p in zip((0.0, 0.5, 1.0), positions)],
        "stops": [[0.0, 0.0], [0.5, 0.3], [1.0, 1.0]],
    }}
    assert properties.load_record(obj, settings, "Then") is True
    assert [round(h.weight, 3) for h in settings.handles] == [0.0, 0.3, 1.0]

    # Oldest: bare positions, no weight stored at all - spread them evenly.
    obj[properties.RECORD_KEY] = {"Oldest": {
        **base, "handles": [list(p) for p in positions],
    }}
    assert properties.load_record(obj, settings, "Oldest") is True
    assert [round(h.weight, 3) for h in settings.handles] == [0.0, 0.5, 1.0]

    # Every one of them leaves the ramp matching the handles it loaded.
    stops = sorted(e.position for e in properties.ramp_of(settings).elements)
    assert [round(x, 3) for x in stops] == [0.0, 0.5, 1.0]
    assert [tuple(round(c, 3) for c in h.position) for h in settings.handles] == [
        tuple(round(c, 3) for c in p) for p in positions
    ]
    bpy.ops.tk.cancel_gradient()


def test_invert_negates_and_moves_the_stops():
    """Invert flips every weight, and the bar follows.

    The pair must add to 1 at every vertex - that is what makes a gradient and
    its inverse a complementary pair of groups rather than two related ones.
    """
    from blender_toolkit.tools.weights import gradient, properties

    reset()
    obj = _grid_with_key()
    settings = obj.tk_gradient
    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='X') == {'FINISHED'}

    # Three handles with a deliberately lopsided profile, so mirroring the
    # position and negating the weight cannot be confused for each other.
    ramp = properties.ramp_of(settings)
    ramp.elements.new(0.2)
    properties.sync_handles_to_ramp(settings)
    flush(obj)
    straight = [
        obj.vertex_groups[settings.group_name].weight(v.index)
        for v in obj.data.vertices
    ]
    shown = properties.handle_values(settings)
    assert [round(v, 3) for v in shown] == [0.0, 0.2, 1.0]

    settings.invert = True
    flush(obj)

    # Every handle's weight is negated, and its colour with it.
    assert [round(v, 3) for v in properties.handle_values(settings)] == [
        1.0, 0.8, 0.0
    ]
    assert properties.handle_colours(settings)[0] == gradient.weight_colour(1.0)

    # The stops moved to where those weights now read on the bar.
    assert [round(e.position, 3) for e in sorted(
        ramp.elements, key=lambda e: e.position
    )] == [0.0, 0.8, 1.0]

    # And the mesh: original plus inverted is 1 everywhere.
    flipped = [
        obj.vertex_groups[settings.group_name].weight(v.index)
        for v in obj.data.vertices
    ]
    assert all(abs(a + b - 1.0) < 1e-5 for a, b in zip(straight, flipped)), [
        (a, b) for a, b in zip(straight, flipped) if abs(a + b - 1.0) >= 1e-5
    ][:3]

    # Toggling back is exactly the identity, stops included.
    settings.invert = False
    flush(obj)
    assert [round(v, 3) for v in properties.handle_values(settings)] == [
        round(v, 3) for v in shown
    ]
    assert all(
        abs(a - b) < 1e-6
        for a, b in zip(straight, (
            obj.vertex_groups[settings.group_name].weight(v.index)
            for v in obj.data.vertices
        ))
    )
    bpy.ops.tk.cancel_gradient()


def test_gradient_rename_moves_the_group():
    """Renaming mid-session moves the gradient; it does not leave a copy."""
    reset()
    obj = _grid_with_key()
    settings = obj.tk_gradient

    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='X') == {'FINISHED'}
    created = settings.group_name
    settings.group_name = "Renamed"
    flush(obj)
    assert created not in obj.vertex_groups, "the old name must not linger"
    assert "Renamed" in obj.vertex_groups
    assert obj.vertex_groups.active.name == "Renamed"

    # Naming an existing group borrows it; naming away again hands it back.
    prior = obj.vertex_groups.new(name="Keep")
    for vert in obj.data.vertices:
        prior.add([vert.index], 0.25, 'REPLACE')
    settings.group_name = "Keep"
    flush(obj)
    assert any(prior.weight(v.index) != 0.25 for v in obj.data.vertices)
    settings.group_name = "Elsewhere"
    flush(obj)
    assert all(
        abs(prior.weight(v.index) - 0.25) < 1e-6 for v in obj.data.vertices
    ), "a group the session only borrowed must come back untouched"

    bpy.ops.tk.cancel_gradient()


def test_gradient_mask_edge_does_not_erode():
    """A rewrite blends against the pre-session weights, not its own last result.

    Blending against the group as it stands walks a half-masked vertex towards
    the full gradient one property tweak at a time.
    """
    reset()
    obj = _grid_with_key()
    settings = obj.tk_gradient

    half = obj.vertex_groups.new(name="Half")
    for vert in obj.data.vertices:
        half.add([vert.index], 0.5, 'REPLACE')

    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='X') == {'FINISHED'}
    settings.mask_group = "Half"
    flush(obj)

    def weights():
        group = obj.vertex_groups[settings.group_name]
        return [group.weight(v.index) for v in obj.data.vertices]

    first = weights()
    for _ in range(3):  # each toggle is two full rewrites
        settings.invert = True
        flush(obj)
        settings.invert = False
        flush(obj)
    assert all(abs(a - b) < 1e-6 for a, b in zip(first, weights()))

    bpy.ops.tk.cancel_gradient()


def test_distribute_evenly_and_reset_for_next():
    """The button that replaces the old ramp toggle, and what Add keeps."""
    from blender_toolkit.tools.weights import properties

    reset()
    obj = _grid_with_key()
    settings = obj.tk_gradient
    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='X') == {'FINISHED'}

    ramp = properties.ramp_of(settings)
    for position in (0.2, 0.3, 0.4):
        ramp.elements.new(position)
    properties.sync_handles_to_ramp(settings)
    assert len(settings.handles) == 5

    # Even either way round: the stored weights are raw, and Invert only moves
    # the stops to where those weights now read on the bar.
    even = [0.0, 0.25, 0.5, 0.75, 1.0]
    for settings.invert in (False, True):
        assert bpy.ops.tk.distribute_handle_weights() == {'FINISHED'}
        assert [round(h.weight, 6) for h in settings.handles] == even
        assert [round(e.position, 6) for e in ramp.elements] == even

    # Add keeps the ends of the path and drops everything else back to default.
    ends = [tuple(settings.handles[i].position) for i in (0, -1)]
    settings.smooth_repeat = 3
    assert bpy.ops.tk.add_gradient() == {'FINISHED'}
    assert [tuple(h.position) for h in settings.handles] == ends
    assert [h.weight for h in settings.handles] == [0.0, 1.0]
    assert settings.smooth_repeat == 0 and settings.invert is False
    assert len(properties.ramp_of(settings).elements) == 2

    bpy.ops.tk.cancel_gradient()


def test_gradient_record_round_trip():
    """Add saves the gradient onto the group; naming it again picks it back up."""
    from blender_toolkit.tools.weights import properties

    reset()
    obj = _grid_with_key()
    settings = obj.tk_gradient

    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='X') == {'FINISHED'}
    settings.group_name = "Saved"
    settings.shape = 'SPHERICAL'
    settings.smooth_repeat = 2
    settings.curved = True
    settings.handles[0].position = (-0.5, 0.25, 0.0)
    flush(obj)
    path = [tuple(round(c, 5) for c in h.position) for h in settings.handles]
    assert bpy.ops.tk.add_gradient() == {'FINISHED'}
    assert "Saved" in obj[properties.RECORD_KEY]

    # Move on to an unrelated group, then come back to the saved one.
    settings.group_name = "Other"
    settings.shape = 'LINEAR'
    settings.curved = False
    settings.smooth_repeat = 0
    settings.group_name = "Saved"
    assert settings.shape == 'SPHERICAL'
    assert settings.curved
    assert settings.smooth_repeat == 2
    assert [tuple(round(c, 5) for c in h.position) for h in settings.handles] == path

    bpy.ops.tk.cancel_gradient()

    # A fresh session starts clean - defaults and an unused name - rather than
    # inheriting the last round or resuming whatever it was last pointed at.
    assert bpy.ops.tk.start_gradient(source='BOUNDS', axis='Y') == {'FINISHED'}
    assert settings.shape == 'LINEAR'
    assert not settings.curved
    assert settings.smooth_repeat == 0
    assert settings.group_name not in {"Saved", "Other"}
    assert "Saved" in obj.vertex_groups, "the committed group must be left alone"
    assert properties.unused_group_name(obj) != settings.group_name, (
        "the session took the name, so the next free one has to differ"
    )

    # Picking the saved group in the panel is what resumes it.
    settings.group_name = "Saved"
    assert settings.shape == 'SPHERICAL'
    assert [tuple(round(c, 5) for c in h.position) for h in settings.handles] == path
    assert properties.has_record(obj, "Saved")
    bpy.ops.tk.cancel_gradient()
    assert settings.shape == 'LINEAR', "Cancel leaves nothing behind either"


def test_overlay_draws():
    """The draw callback builds its shader and runs. Says nothing about looks."""
    import gpu

    from blender_toolkit.tools.weights import overlay

    gpu.init()  # background Blender has no GPU context until this is called
    reset()
    obj = _grid_with_key()
    settings = obj.tk_gradient
    settings.start, settings.end = (-1, 0, 0), (1, 0, 0)
    settings.active = True
    try:
        for shape in ('LINEAR', 'SPHERICAL', 'BAND'):
            settings.shape = shape
            overlay._draw()

        # The mask tint is built and drawn on the same pass.
        mask = obj.vertex_groups.new(name="Protect")
        for vert in obj.data.vertices:
            mask.add([vert.index], 1.0 if vert.co.x > 0 else 0.0, 'REPLACE')
        settings.mask_group = "Protect"
        overlay._draw()
        assert overlay._mask_cache[1] is not None
        assert overlay._mask_shader is not None
    finally:
        settings.active = False
        settings.mask_group = ""
    assert overlay._shader is not None


def test_gradient_seed_points():
    reset()
    obj = _grid_with_key()

    assert bpy.ops.tk.write_gradient(source='BOUNDS', axis='Y') == {'FINISHED'}
    assert [g.name for g in obj.vertex_groups] == ["Group"]
    for vert in obj.data.vertices:
        expected = (vert.co.y + 1.0) / 2.0
        assert abs(obj.vertex_groups["Group"].weight(vert.index) - expected) < 1e-5

    # Active vertex ends the band, the rest of the selection starts it.
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for vert in bm.verts:
        vert.select = False
    bm.select_history.clear()
    first, last = bm.verts[0], bm.verts[len(bm.verts) - 1]
    for vert in (first, last):
        vert.select = True
        bm.select_history.add(vert)
    # BMVerts die on the mode switch below - keep indices and coordinates.
    first_index, last_index = first.index, last.index
    start, end = first.co.copy(), last.co.copy()

    # AUTO is the default and prefers a usable selection over the bounds.
    assert bpy.ops.tk.write_gradient(group_name="A") == {'FINISHED'}
    bpy.ops.object.mode_set(mode='OBJECT')

    weights = obj.vertex_groups["A"]
    assert abs(weights.weight(first_index)) < 1e-5
    assert abs(weights.weight(last_index) - 1.0) < 1e-5

    direction = end - start
    for vert in obj.data.vertices:
        expected = min(max((vert.co - start).dot(direction) / direction.dot(direction), 0.0), 1.0)
        assert abs(weights.weight(vert.index) - expected) < 1e-5


def test_gradient_seeds_without_a_selection():
    """The button must work in Object mode with nothing selected."""
    reset()
    obj = _grid_with_key()

    # No selection to read, so AUTO spans the X bounds - left to right.
    assert bpy.ops.tk.write_gradient(group_name="A") == {'FINISHED'}
    for vert in obj.data.vertices:
        expected = (vert.co.x + 1.0) / 2.0
        assert abs(obj.vertex_groups["A"].weight(vert.index) - expected) < 1e-5

    # A session started cold gets the same treatment.
    settings = obj.tk_gradient
    assert bpy.ops.tk.start_gradient() == {'FINISHED'}
    assert [tuple(round(c, 3) for c in h.position) for h in settings.handles] == [
        (-1.0, 0.0, 0.0), (1.0, 0.0, 0.0)
    ]

    # Bounds come from obj.data.vertices, not obj.bound_box: this grid carries a
    # shape key offset in Z, which moves the evaluated bounding box away from
    # the base coordinates the weights are computed from.
    assert all(abs(h.position.z) < 1e-6 for h in settings.handles)
    bpy.ops.tk.cancel_gradient()

    # A flat mesh has no Z extent, so asking for Z must not produce a
    # zero-length gradient - it falls back to the longest axis.
    assert bpy.ops.tk.write_gradient(
        source='BOUNDS', axis='Z', group_name="C"
    ) == {'FINISHED'}
    values = [obj.vertex_groups["C"].weight(v.index) for v in obj.data.vertices]
    assert min(values) < 0.01 and max(values) > 0.99, (min(values), max(values))

    # Explicit SELECTION still errors rather than quietly falling back.
    raises(bpy.ops.tk.write_gradient, "Edit mode", source='SELECTION')


def _armature(bone_names):
    bpy.ops.object.armature_add()
    obj = bpy.context.active_object
    bpy.ops.object.mode_set(mode='EDIT')
    for bone in list(obj.data.edit_bones):
        obj.data.edit_bones.remove(bone)
    for index, name in enumerate(bone_names):
        bone = obj.data.edit_bones.new(name)
        bone.head = (0, 0, index)
        bone.tail = (0, 0, index + 1)
    bpy.ops.object.mode_set(mode='OBJECT')
    return obj


HUMANOID = [
    "Hips", "Spine", "Chest", "Neck", "Head",
    "UpperArm_L", "UpperArm_R", "lower_arm.L", "lower_arm.R", "Hand_L", "Hand_R",
    "Thigh_L", "Thigh_R", "Calf_L", "Calf_R", "Foot_L", "Foot_R",
]


MIXAMO = [
    "mixamorig:Hips", "mixamorig:Spine", "mixamorig:Spine2", "mixamorig:Neck",
    "mixamorig:Head",
    "mixamorig:LeftArm", "mixamorig:RightArm",
    "mixamorig:LeftForeArm", "mixamorig:RightForeArm",
    "mixamorig:LeftHand", "mixamorig:RightHand",
    "mixamorig:LeftUpLeg", "mixamorig:RightUpLeg",
    "mixamorig:LeftLeg", "mixamorig:RightLeg",
    "mixamorig:LeftFoot", "mixamorig:RightFoot",
]


def test_validate_humanoid():
    from blender_toolkit.tools.rigging.operators import (
        _part,
        _side,
        missing_humanoid_bones,
    )

    for name, part, side in (
        ("UpperArm_L", "upperarm", 'L'),
        ("lower_arm.R", "lowerarm", 'R'),
        ("mixamorig:LeftForeArm", "lowerarm", 'L'),
        ("mixamorig:RightUpLeg", "thigh", 'R'),
        ("mixamorig:LeftLeg", "calf", 'L'),
        ("mixamorig:Spine2", "chest", None),
    ):
        assert _part(name) == part, (name, _part(name))
        assert _side(name) == side, (name, _side(name))

    bones = [SimpleNamespace(name=n) for n in HUMANOID]
    assert missing_humanoid_bones(bones) == []
    assert missing_humanoid_bones([SimpleNamespace(name=n) for n in MIXAMO]) == []

    partial = [b for b in bones if b.name not in {"Neck", "Foot_R"}]
    assert missing_humanoid_bones(partial) == ["neck", "foot.R"]

    reset()
    _armature(HUMANOID)
    assert bpy.ops.tk.validate_humanoid() == {'FINISHED'}


def test_twist_bones():
    reset()
    obj = _armature(["UpperArm_L", "Thigh_L"])
    bpy.ops.object.mode_set(mode='EDIT')
    for bone in obj.data.edit_bones:
        bone.select = True
    lengths = {b.name: b.length for b in obj.data.edit_bones}
    assert bpy.ops.tk.add_twist_bones() == {'FINISHED'}

    assert "twist_UpperArm_L" in obj.data.edit_bones
    twist = obj.data.edit_bones["twist_UpperArm_L"]
    assert abs(twist.length - lengths["UpperArm_L"] * 0.5) < 1e-5
    assert twist.parent.name == "UpperArm_L"

    bpy.ops.object.mode_set(mode='POSE')
    constraint = obj.pose.bones["twist_UpperArm_L"].constraints[0]
    assert constraint.type == 'COPY_ROTATION'
    assert constraint.subtarget == "UpperArm_L"
    assert (constraint.use_x, constraint.use_y, constraint.use_z) == (False, True, False)
    assert constraint.owner_space == constraint.target_space == 'LOCAL'
    assert constraint.influence == 0.5
    bpy.ops.object.mode_set(mode='OBJECT')


def test_export_preset_resets():
    from blender_toolkit.tools.export.operators import RECOMMENDED, _apply_preset

    # The class bl_rna only carries the base Operator members; the declared
    # properties live on the registered operator type.
    props = bpy.ops.tk.export_game_fbx.get_rna_type().properties
    for name, value in RECOMMENDED.items():
        assert name in props, name
        assert props[name].default == value, name

    dirty = SimpleNamespace(preset='GAME_READY', **{k: None for k in RECOMMENDED})
    _apply_preset(dirty, None)
    assert {k: getattr(dirty, k) for k in RECOMMENDED} == RECOMMENDED

    untouched = SimpleNamespace(preset='CUSTOM', **{k: None for k in RECOMMENDED})
    _apply_preset(untouched, None)
    assert untouched.mesh_smooth_type is None


def test_toggle_pose_mode():
    reset()
    obj = _armature(["Hips", "Spine"])
    obj.pose.bones["Hips"].location.x = 1.0
    assert bpy.ops.tk.toggle_pose_mode() == {'FINISHED'}
    assert obj.mode == 'POSE'
    assert obj.pose.bones["Hips"].location.x == 0.0

    assert bpy.ops.tk.toggle_pose_mode() == {'FINISHED'}
    assert obj.mode == 'OBJECT'

    obj.pose.bones["Hips"].location.x = 1.0
    assert bpy.ops.tk.toggle_pose_mode(reset=False) == {'FINISHED'}
    assert obj.mode == 'POSE'
    assert obj.pose.bones["Hips"].location.x == 1.0
    bpy.ops.tk.toggle_pose_mode(reset=False)

    reset()
    add_cube()
    assert not bpy.ops.tk.toggle_pose_mode.poll()


def test_export_fbx():
    reset()
    add_cube()
    path = os.path.join(tempfile.mkdtemp(), "out.fbx")
    assert bpy.ops.tk.export_game_fbx(filepath=path) == {'FINISHED'}
    assert os.path.getsize(path) > 0

    # Non-default settings must reach the FBX exporter, not be ignored.
    other = os.path.join(tempfile.mkdtemp(), "edge.fbx")
    assert bpy.ops.tk.export_game_fbx(
        filepath=other, mesh_smooth_type='EDGE', axis_up='Z'
    ) == {'FINISHED'}
    assert os.path.getsize(other) > 0


def test_modules_wired():
    """Every tool directory is in MODULE_NAMES with a matching preference."""
    from blender_toolkit import tools

    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "blender_toolkit", "tools")
    on_disk = {
        name for name in os.listdir(root)
        if os.path.isfile(os.path.join(root, name, "__init__.py"))
    }
    assert on_disk == set(tools.MODULE_NAMES), on_disk ^ set(tools.MODULE_NAMES)

    # __annotations__, not bl_rna: declared properties only reach bl_rna once
    # the class is registered, and preferences are not in a direct import.
    declared = blender_toolkit.preferences.TK_AddonPreferences.__annotations__
    for module, flag in tools.MODULES:
        assert flag in declared, flag
        assert module.ui.classes and module.operators.classes, module.__name__


def test_reload_with_stale_utils():
    """Reload Scripts on a session whose cached utils predates a new helper.

    utils is the one module load_submodules cannot load: re-executing the top
    __init__ resolves `from .utils import ...` against the session's cached copy,
    so a helper added to utils would raise ImportError before the loader that
    lives in it ever runs. The top __init__ reloads utils by hand first.
    """
    import importlib

    from blender_toolkit import utils

    helper = utils.load_submodules
    # Unregister before reloading: reloading rebinds every class object while
    # bpy still holds the old ones registered, and unregister_class on the new
    # objects then fails. This is the same reason a new module needs a full
    # disable/re-enable rather than F3.
    blender_toolkit.unregister()
    del utils.load_submodules
    try:
        importlib.reload(blender_toolkit)
    finally:
        if not hasattr(utils, "load_submodules"):
            utils.load_submodules = helper
        blender_toolkit.register()
    assert hasattr(utils, "load_submodules")


def test_load_submodules():
    """Reload Scripts on a session that predates a newly added submodule.

    The old session has no name bound for the new module, and calling
    importlib.reload() on a missing name raises NameError partway through the
    reload - leaving the add-on half updated, the old classes still registered
    and the new panel missing. Every __init__ routes through this helper.

    Driven against a namespace dict rather than a live package: reloading a real
    module rebinds its classes while bpy still holds the old ones registered,
    which is why a new module needs a full disable/re-enable, not F3.
    """
    from blender_toolkit.utils import load_submodules

    package = "blender_toolkit.tools.weights"
    namespace = {}

    # First load: nothing bound, everything imports fresh.
    load_submodules(namespace, package, ("gradient",))
    first = namespace["gradient"]
    assert first.factor((1, 0, 0), [(0, 0, 0), (2, 0, 0)]) == 0.5

    # Second load: already bound, so it reloads in place.
    load_submodules(namespace, package, ("gradient",))
    assert namespace["gradient"] is first

    # The regression: names added since the last load. Must import, not raise.
    load_submodules(namespace, package, ("gradient", "properties", "overlay"))
    assert list(namespace) == ["gradient", "properties", "overlay"]
    assert namespace["gradient"] is first  # the existing one still reloaded


def test_unregister_is_clean():
    from blender_toolkit.tools.weights import overlay

    blender_toolkit.unregister()
    try:
        assert not hasattr(bpy.types.Object, "tk_gradient")
        assert overlay._handler is None
        assert not bpy.app.timers.is_registered(overlay._sync)
        assert overlay._on_depsgraph not in bpy.app.handlers.depsgraph_update_post
        # Gizmo groups are not exposed on bpy.types by name the way panels are,
        # and bl_rna survives unregister_class. This lookup is what tracks it.
        assert bpy.types.GizmoGroup.bl_rna_get_subclass_py(
            "TK_GGT_weight_gradient"
        ) is None
        assert not hasattr(bpy.types, "TK_PG_gradient_handle")
        assert not hasattr(bpy.types, "TK_PG_weight_gradient")
        for name in (
            "TK_PT_retopo", "TK_PT_shapekeys", "TK_PT_weights", "TK_PT_rigging",
            "TK_PT_export",
            "TK_MT_pie_main", "TK_AddonPreferences",
        ):
            assert not hasattr(bpy.types, name), name
        assert (not hasattr(bpy.ops.tk, "retopo_setup")
                or "tk.retopo_setup" not in dir(bpy.ops.tk))
    finally:
        # Re-register regardless: leaving the add-on down fails every later test
        # with a confusing "operator could not be found".
        blender_toolkit.register()


def main():
    blender_toolkit.register()
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:  # noqa: BLE001 - report and keep going
            failures += 1
            import traceback

            traceback.print_exc()
            print(f"FAIL {test.__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
