"""Spatial falloff maths behind the Weight Gradient tool.

No bpy import: this is the part worth testing directly rather than through an
operator. Coordinates are object space; callers transform if they need world.
"""

import math

SHAPES = (
    ('LINEAR', "Path", "Ramp along the path through the handles"),
    ('SPHERICAL', "Spherical", "Distance from the first handle, 1 at the last"),
    ('BAND', "Band", "0 on the plane through the middle, 1 at both ends"),
)

# Only the path shape uses the handles in between; the other two are radial
# fields defined entirely by the first and last.
PATH_SHAPES = frozenset({'LINEAR'})

# Handle colours: the low end, the high end, and everything between them.
LOW_COLOUR = (0.1, 0.4, 1.0, 1.0)
HIGH_COLOUR = (1.0, 0.6, 0.1, 1.0)
MID_COLOUR = (0.6, 0.6, 0.6, 1.0)


def handle_colours(count, invert=False):
    """Colour per handle, ends swapped when the gradient is inverted.

    Shared by the overlay and the gizmos so the two cannot drift apart.
    """
    low, high = (HIGH_COLOUR, LOW_COLOUR) if invert else (LOW_COLOUR, HIGH_COLOUR)
    if count <= 0:
        return []
    if count == 1:
        return [low]
    return [low] + [MID_COLOUR] * (count - 2) + [high]

# Blender's own proportional-edit vocabulary. The curves mirror those falloffs;
# they are not claimed to be bit-identical to Blender's internals.
PROFILES = (
    ('LINEAR', "Linear", "t"),
    ('SMOOTH', "Smooth", "t^2 (3 - 2t)"),
    ('SPHERE', "Sphere", "sqrt(1 - (1 - t)^2)"),
    ('ROOT', "Root", "sqrt(t)"),
    ('SHARP', "Sharp", "t^2"),
    ('INVERSE_SQUARE', "Inverse Square", "t^4"),
    ('CONSTANT', "Constant", "0 below the midpoint, 1 above"),
)

_PROFILE_CURVES = {
    'LINEAR': lambda t: t,
    'SMOOTH': lambda t: t * t * (3.0 - 2.0 * t),
    'SPHERE': lambda t: math.sqrt(max(1.0 - (1.0 - t) ** 2, 0.0)),
    'ROOT': math.sqrt,
    'SHARP': lambda t: t * t,
    'INVERSE_SQUARE': lambda t: t ** 4,
    'CONSTANT': lambda t: 0.0 if t < 0.5 else 1.0,
}


def _clamp(value):
    return min(max(value, 0.0), 1.0)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _remap_midpoint(t, midpoint):
    """Slide the half-way point of the ramp without bending it into a gamma."""
    if midpoint <= 0.0:
        return 1.0 if t > 0.0 else 0.0
    if midpoint >= 1.0:
        return 0.0 if t < 1.0 else 1.0
    if t < midpoint:
        return 0.5 * t / midpoint
    return 0.5 + 0.5 * (t - midpoint) / (1.0 - midpoint)


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _length(a):
    return math.sqrt(_dot(a, a))


def path_points(handles, curved=False, per_segment=12):
    """The polyline the gradient runs along.

    Straight is the handles themselves. Curved is a Catmull-Rom sampling, chosen
    because it passes *through* its control points - a handle has to end up
    where you put it.
    """
    points = [tuple(h) for h in handles]
    if not curved or len(points) < 3:
        return points

    # Duplicate the ends so the first and last segments are drawn too.
    padded = [points[0]] + points + [points[-1]]
    sampled = []
    for i in range(len(padded) - 3):
        p0, p1, p2, p3 = padded[i:i + 4]
        for step in range(per_segment):
            t = step / per_segment
            # Uniform Catmull-Rom, one axis at a time for legibility:
            # 0.5 * (2p1 + (p2-p0)t + (2p0-5p1+4p2-p3)t^2 + (3p1-p0-3p2+p3)t^3)
            sampled.append(tuple(
                0.5 * (
                    2.0 * p1[a]
                    + (p2[a] - p0[a]) * t
                    + (2.0 * p0[a] - 5.0 * p1[a] + 4.0 * p2[a] - p3[a]) * t * t
                    + (3.0 * p1[a] - p0[a] - 3.0 * p2[a] + p3[a]) * t * t * t
                )
                for a in range(3)
            ))
    sampled.append(points[-1])
    return sampled


def segment_lengths(points):
    """Per-segment lengths, the arc length before each segment, and the total.

    The offsets are prefix sums so path_factor stays linear rather than summing
    a slice per candidate segment.
    """
    lengths = [_length(_sub(points[i + 1], points[i])) for i in range(len(points) - 1)]
    offsets, running = [], 0.0
    for length in lengths:
        offsets.append(running)
        running += length
    return lengths, offsets, running


def _project(co, a, b):
    """Clamped projection of co onto segment a-b: (parameter, squared distance)."""
    direction = _sub(b, a)
    length_sq = _dot(direction, direction)
    if length_sq == 0.0:
        offset = _sub(co, a)
        return 0.0, _dot(offset, offset)
    t = _clamp(_dot(_sub(co, a), direction) / length_sq)
    closest = _add(a, _scale(direction, t))
    offset = _sub(co, closest)
    return t, _dot(offset, offset)


def distance_to_segment(co, a, b):
    return math.sqrt(_project(co, a, b)[1])


def path_factor(co, points, metrics=None, lookup=None):
    """Arc-length position of co's nearest point on the polyline, 0..1.

    `metrics` is segment_lengths(points), hoisted out by callers that run this
    over a whole mesh. `lookup` is an optional callable returning the segment
    indices worth testing - see properties._lookup_for, which narrows them with
    a KDTree without ever excluding the true nearest.
    """
    if len(points) < 2:
        raise ValueError("A path needs at least two handles")
    lengths, offsets, total = metrics or segment_lengths(points)
    if not total:
        raise ValueError("Start and end are the same point")

    # Sorted, and ties keep the first: a path that folds back can put a vertex
    # exactly equidistant from two segments, and an arbitrary set order would
    # otherwise pick a different one than a plain scan does.
    candidates = range(len(lengths)) if lookup is None else sorted(lookup(co))

    best_distance_sq = None
    best_arc = 0.0
    for index in candidates:
        t, distance_sq = _project(co, points[index], points[index + 1])
        if best_distance_sq is None or distance_sq < best_distance_sq:
            best_distance_sq = distance_sq
            best_arc = offsets[index] + t * lengths[index]
    return _clamp(best_arc / total)


def raw_factor(co, points, shape, metrics=None, lookup=None):
    """Position of `co` in the 0..1 field, before the value curve and inversion.

    The path shape uses every handle; the radial shapes are defined entirely by
    the first and last, so anything in between is ignored rather than silently
    bending them.
    """
    if shape in PATH_SHAPES:
        return path_factor(co, points, metrics, lookup)

    start, end = points[0], points[-1]
    direction = _sub(end, start)
    length_sq = _dot(direction, direction)
    if length_sq == 0.0:
        raise ValueError("Start and end are the same point")

    offset = _sub(co, start)
    if shape == 'SPHERICAL':
        return _clamp(math.sqrt(_dot(offset, offset) / length_sq))
    if shape == 'BAND':
        centre = _add(start, _scale(direction, 0.5))
        along = _dot(_sub(co, centre), direction) / length_sq
        return _clamp(abs(along) * 2.0)
    raise ValueError(f"Unknown shape: {shape}")


def factor(
    co,
    points,
    shape='LINEAR',
    profile='LINEAR',
    midpoint=0.5,
    invert=False,
    curve=None,
    metrics=None,
    lookup=None,
):
    """Weight for one coordinate.

    `curve` is an optional callable mapping 0..1 to 0..1, used by the session to
    read its ColorRamp. Without one the named profile and midpoint apply, which
    is what the scripting operator uses - an operator property cannot hold a
    ColorRamp.
    """
    t = raw_factor(co, points, shape, metrics, lookup)
    if invert:
        t = 1.0 - t
    if curve is not None:
        return _clamp(curve(t))
    if midpoint != 0.5:
        t = _remap_midpoint(t, midpoint)
    return _clamp(_PROFILE_CURVES[profile](t))


def smooth(weights, edges, repeat):
    """Average each weight with its edge neighbours, `repeat` times.

    bpy.ops.object.vertex_group_smooth needs an edit-mode context with a
    selection, which is more trouble than the ten lines it replaces.
    """
    if not repeat:
        return weights

    neighbours = {index: [] for index in weights}
    for a, b in edges:
        if a in neighbours and b in neighbours:
            neighbours[a].append(b)
            neighbours[b].append(a)

    for _ in range(repeat):
        weights = {
            index: (weight + sum(weights[n] for n in neighbours[index]))
            / (1 + len(neighbours[index]))
            for index, weight in weights.items()
        }
    return weights
