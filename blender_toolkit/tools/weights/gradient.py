"""Spatial falloff maths behind the Weight Gradient tool.

No bpy import: this is the part worth testing directly rather than through an
operator. Coordinates are object space; callers transform if they need world.
"""

import bisect
import colorsys
import math


def _clamp(value):
    return min(max(value, 0.0), 1.0)


SHAPES = (
    ('LINEAR', "Path", "Ramp along the path through the handles"),
    ('SPHERICAL', "Spherical", "Ramp outwards from the first handle to the last"),
    ('BAND', "Band", "Ramp out to both ends from the middle of the path"),
)

# Only the path shape uses the handles in between; the other two are radial
# fields defined entirely by the first and last.
PATH_SHAPES = frozenset({'LINEAR'})

# Weight paint's own ramp is a sweep of fully saturated hue from blue to red,
# through cyan, green and yellow - so the whole thing is one number, the hue,
# running 2/3 down to 0. Everything the tool shows in a weight colour goes
# through here, which is why a handle and the mesh under it agree.
_BLUE_HUE = 2.0 / 3.0


def weight_colour(value):
    """Weight paint's hue at this weight, at full saturation and brightness.

    The scale a ColorRamp can *be*: `weight_of` inverts it exactly, which is
    what lets the ramp widget stand in for a weight editor. Not what weight
    paint actually draws - see `weight_paint_colour`, which is the same hues
    with Blender's brightness ramp on top and cannot be inverted from a stop.
    """
    hue = (1.0 - _clamp(value)) * _BLUE_HUE
    return (*colorsys.hsv_to_rgb(hue, 1.0, 1.0), 1.0)


def weight_paint_colour(value):
    """What weight paint mode actually paints on the mesh, as RGB.

    Blender's own blue -> cyan -> green -> yellow -> red, in four straight
    segments with a brightness that climbs from a half at zero to full at one -
    so the cold end is dark and the warm end is vivid. That brightness is why
    this is not `weight_colour` with a different name, and why the pair cannot
    be collapsed: `weight_of` needs a hue it can read back, and a stop dimmed
    to half would come back as a different weight.

    Not probed, because nothing exposes it: there is no ColorRamp for it on
    tool_settings and no theme entry, so it lives in C where only the source
    says what it is. This is Blender's published algorithm, and the check is to
    put a mesh in Weight Paint next to the overlay and look.
    """
    weight = _clamp(value)
    blend = weight * 0.5 + 0.5
    if weight <= 0.25:  # blue -> cyan
        return (0.0, blend * weight * 4.0, blend)
    if weight <= 0.5:  # cyan -> green
        return (0.0, blend, blend * (1.0 - (weight - 0.25) * 4.0))
    if weight <= 0.75:  # green -> yellow
        return (blend * (weight - 0.5) * 4.0, blend, 0.0)
    return (blend, blend * (1.0 - (weight - 0.75) * 4.0), 0.0)  # yellow -> red


def weight_of(colour):
    """The weight a colour stands for. The inverse of `weight_colour`.

    Greys have no hue to read, so their brightness is the weight instead. That
    covers a stop picked out of the colour wheel's grey axis, and it is also what
    makes ramps saved when this was a greyscale picker still mean what they did.
    """
    hue, saturation, brightness = colorsys.rgb_to_hsv(*colour[:3])
    if saturation < 1e-4:
        return _clamp(brightness)
    return _clamp(1.0 - hue / _BLUE_HUE)


# Blender's own proportional-edit vocabulary. The curves mirror those falloffs;
# they are not claimed to be bit-identical to Blender's internals.
PROFILES = (
    ('LINEAR', "Linear", "Straight ramp"),
    ('SMOOTH', "Smooth", "Eases in and out"),
    ('SPHERE', "Sphere", "Rounded, steepest at the start"),
    ('ROOT', "Root", "Rises fast, then flattens off"),
    ('SHARP', "Sharp", "Starts slow, then rises fast"),
    ('INVERSE_SQUARE', "Inverse Square", "Stays low for longer than Sharp"),
    ('CONSTANT', "Constant", "Jumps straight from 0 to 1 at the midpoint"),
)

PROFILE_CURVES = {
    'LINEAR': lambda t: t,
    'SMOOTH': lambda t: t * t * (3.0 - 2.0 * t),
    'SPHERE': lambda t: math.sqrt(max(1.0 - (1.0 - t) ** 2, 0.0)),
    'ROOT': math.sqrt,
    'SHARP': lambda t: t * t,
    'INVERSE_SQUARE': lambda t: t ** 4,
    'CONSTANT': lambda t: 0.0 if t < 0.5 else 1.0,
}


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


# Roughly how many samples a curved path is worth in total. Fidelity comes from
# control points or from samples between them, so a path with a handle every few
# centimetres does not need twelve samples in each gap as well - and every sample
# is a segment tested against every vertex.
TARGET_SAMPLES = 120


def samples_per_gap(handle_count):
    """How finely a curved path is sampled between two handles.

    A budget split across the gaps, so the cost of a curved path stops growing
    with the square of the handle count. Up to eleven handles it works out at
    the flat 12 it used to be.
    """
    return max(3, min(12, TARGET_SAMPLES // max(handle_count - 1, 1)))


def path_points(handles, curved=False, per_segment=None):
    """The polyline the gradient runs along.

    Straight is the handles themselves. Curved is a Catmull-Rom sampling, chosen
    because it passes *through* its control points - a handle has to end up
    where you put it.
    """
    points = [tuple(h) for h in handles]
    if not curved or len(points) < 3:
        return points
    if per_segment is None:
        per_segment = samples_per_gap(len(points))

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


def handle_arc_positions(points, count, curved=False, per_segment=None):
    """Where each handle sits along the path, 0..1.

    Taken from how the polyline was built, never by searching for the handle in
    it. `path_factor` looks like the obvious tool and is wrong here: it returns
    the nearest point on the *whole* path, so on a path that folds back a handle
    sitting near an earlier stretch reports that stretch's arc position instead
    of its own, and its weight lands in the wrong place.

    Straight, handle `i` is polyline vertex `i`. Curved, `path_points` emits
    `per_segment` samples per gap beginning at the handle itself, so handle `i`
    is sample `i * per_segment` and the last is the appended final sample.
    """
    if count < 2:
        return [0.0] * count

    _lengths, offsets, total = segment_lengths(points)
    if not total:  # every handle in one place, mid-drag; spread them rather
        return [i / (count - 1) for i in range(count)]  # than divide by zero

    # offsets holds the arc length at every point but the last, which is `total`.
    arcs = offsets + [total]
    stride = 1
    if curved and count > 2:
        stride = per_segment if per_segment else samples_per_gap(count)
    return [
        _clamp(arcs[min(i * stride, len(arcs) - 1)] / total) for i in range(count)
    ]


# What the gradient does to the weights the group already held. The same set
# and the same names as Blender's Vertex Weight Mix modifier, because that is
# the tool a user reaches for to do this by hand.
BLENDS = (
    ('REPLACE', "Replace", "Use the gradient's weight"),
    ('ADD', "Add", "Add the gradient to the weights already there"),
    ('MULTIPLY', "Multiply", "Multiply the weights already there by the gradient"),
    ('MIN', "Minimum", "Use the lower of the two"),
    ('MAX', "Maximum", "Use the higher of the two"),
)


def blend(mode, base, value):
    """Compose a gradient weight with the one the group already had."""
    if mode == 'REPLACE':
        return value
    if mode == 'ADD':
        return _clamp(base + value)
    if mode == 'MULTIPLY':
        return base * value
    if mode == 'MIN':
        return min(base, value)
    if mode == 'MAX':
        return max(base, value)
    raise ValueError(f"Unknown blend mode: {mode}")


def point_at_arc(points, fraction, metrics=None):
    """The point `fraction` of the way along a polyline, by arc length."""
    lengths, offsets, total = metrics or segment_lengths(points)
    if not total:
        return points[0]

    target = _clamp(fraction) * total
    for index, length in enumerate(lengths):
        if not length:
            continue
        if offsets[index] + length >= target:
            t = (target - offsets[index]) / length
            return _add(points[index], _scale(_sub(points[index + 1], points[index]), t))
    return points[-1]


def spaced_positions(handles, curved=False):
    """Handle positions respaced at equal arc length, the ends pinned.

    The counterpart of spreading the weights evenly: that decides what value
    each handle reaches, this decides where along the path it sits. Hand-dragged
    handles bunch up, and no amount of editing the ramp fixes that.

    On a curved path the samples are respaced along the *current* curve, and the
    curve is then rebuilt from the new handles - so it shifts a little and a
    second press converges. Straight is exact in one.
    """
    points = path_points(handles, curved)
    count = len(handles)
    if count < 3:
        return [tuple(h) for h in handles]

    metrics = segment_lengths(points)
    if not metrics[2]:  # every handle in one place; nothing to space out
        return [tuple(h) for h in handles]
    return [point_at_arc(points, i / (count - 1), metrics) for i in range(count)]


def relax_positions(handles, factor=0.5, repeat=1):
    """Interior handles pulled towards the midpoint of their neighbours.

    Not the same operation as spacing evenly, and it does not converge to it:
    this shortens the path, straightening kinks and pulling a bend towards its
    chord. Repeating walks a path towards a straight line, which is the point -
    it is a strength dial, not an idempotent tidy-up.

    One pass reads every position from the pass before it, so a run of handles
    relaxes symmetrically rather than dragging in the order they are visited.
    """
    current = [tuple(h) for h in handles]
    if len(current) < 3:
        return current

    for _ in range(max(repeat, 0)):
        previous = current
        current = [previous[0]]
        for index in range(1, len(previous) - 1):
            midpoint = _scale(_add(previous[index - 1], previous[index + 1]), 0.5)
            current.append(
                _add(previous[index], _scale(_sub(midpoint, previous[index]), factor))
            )
        current.append(previous[-1])
    return current


def weight_curve(knots, ease=None):
    """Mapping through `(position, weight)` pairs, or None.

    What the ramp's stops mean once their position is the weight rather than a
    place along the path: the pairs come from the handles, one knot each.
    Outside the first and last knot the curve holds flat - a handle is a control
    point, not a boundary, and the path runs past both ends of it.

    `ease` shapes the travel between one knot and the next, straight through the
    profile curves. It is applied to the local 0..1 within a span rather than to
    the result, so every knot still reads back exactly the weight it was given -
    which is the whole premise of a stop being a weight.
    """
    knots = sorted(knots)
    if not knots:
        return None
    positions = [k[0] for k in knots]
    weights = [_clamp(k[1]) for k in knots]
    if len(knots) == 1:
        return lambda t: weights[0]

    def curve(t):
        if t <= positions[0]:
            return weights[0]
        if t >= positions[-1]:
            return weights[-1]
        index = bisect.bisect_right(positions, t) - 1
        span = positions[index + 1] - positions[index]
        if span <= 0.0:  # two handles at the same place along the path
            return weights[index + 1]
        local = (t - positions[index]) / span
        if ease is not None:
            local = _clamp(ease(local))
        return weights[index] + (weights[index + 1] - weights[index]) * local

    return curve


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


# How close a second segment has to be, as a fraction of the path's length,
# before it starts sharing in the answer. Not exposed: measured across 2%, 5%,
# 10% and 20% and the banding is gone at all of them, so it is not a dial worth
# giving anyone.
BLEND_FRACTION = 0.05


def path_factor(co, points, metrics=None):
    """Arc-length position of co along the polyline, 0..1.

    Not simply the nearest point's, because that is discontinuous. On the
    concave side of a bend a point is equidistant from two stretches of the path
    whose arc positions are far apart, and taking the nearest outright makes the
    weight jump across that tie line - a hard band through the mesh that no
    amount of smoothing removes (it diffuses as 1/sqrt(passes): twenty passes
    only takes a 0.52 step down to 0.07, and blurs everything else on the way).

    So segments share the answer in proportion to how close they are to the
    nearest one, with the share falling to zero at `BLEND_FRACTION` of the path
    beyond it. Nothing enters or leaves the blend with a step, so the field is
    continuous everywhere. Where one segment is clearly nearest - which is most
    of the mesh - it takes the whole share and nothing changes.

    Handles are untouched by this: at a bend the two segments meeting there
    both report that corner's arc position, so they agree exactly and blending
    them changes nothing.
    """
    if len(points) < 2:
        raise ValueError("A path needs at least two handles")
    lengths, offsets, total = metrics or segment_lengths(points)
    if not total:
        raise ValueError("Start and end are the same point")

    projected = [
        _project(co, points[index], points[index + 1])
        for index in range(len(lengths))
    ]
    nearest = math.sqrt(min(distance_sq for _t, distance_sq in projected))
    band = total * BLEND_FRACTION

    numerator = denominator = 0.0
    for index, (t, distance_sq) in enumerate(projected):
        share = _clamp(1.0 - (math.sqrt(distance_sq) - nearest) / band) ** 2
        numerator += share * (offsets[index] + t * lengths[index])
        denominator += share
    return _clamp(numerator / denominator / total)


def raw_factors(coords, points, shape, metrics=None):
    """`raw_factor` for a whole mesh at once, as a numpy array.

    The scalar version is ~21 microseconds a vertex, which is 1.4 seconds for a
    65k mesh on a curved eight-handle path - so a handle drag was a slideshow no
    amount of coalescing could fix. The cost is per vertex *per segment*, and it
    is all arithmetic, so it vectorises exactly.

    Every segment is tested against every vertex. That is the same work the
    KDTree used to avoid, except that here it is 84 array operations rather than
    5.5 million interpreter steps - and it drops the bound the accelerator
    needed to be careful about, because nothing is being excluded any more.

    See `path_factor` for why near-tied segments share the answer rather than
    the nearest taking it outright.

    numpy is bundled with Blender, so this adds no dependency.
    """
    import numpy as np

    coords = np.asarray(coords, dtype=np.float64).reshape(-1, 3)
    path = np.asarray(points, dtype=np.float64)

    if shape in PATH_SHAPES:
        lengths, offsets, total = metrics or segment_lengths(points)
        if len(points) < 2:
            raise ValueError("A path needs at least two handles")
        if not total:
            raise ValueError("Start and end are the same point")

        def project(index):
            """Distance and arc position for one segment, every vertex at once."""
            a = path[index]
            direction = path[index + 1] - a
            length_sq = direction @ direction
            if length_sq == 0.0:
                t = np.zeros(len(coords))
            else:
                t = np.clip(((coords - a) @ direction) / length_sq, 0.0, 1.0)
            # Same arithmetic in the same order as _project, not merely the same
            # algebra: `(co - a) - t*d` and `co - (a + t*d)` round differently,
            # and the two implementations have to agree to the last bit.
            gap = coords - (a + t[:, None] * direction)
            return np.einsum('ij,ij->i', gap, gap), offsets[index] + t * lengths[index]

        # Two passes rather than one, so the blend never has to hold a
        # segments-by-vertices array: at 372 segments and 65k verts that would be
        # 400MB. Costs exactly double the projections, which is the whole cost.
        nearest = np.full(len(coords), np.inf)
        for index in range(len(path) - 1):
            np.minimum(nearest, project(index)[0], out=nearest)
        np.sqrt(nearest, out=nearest)

        band = total * BLEND_FRACTION
        numerator = np.zeros(len(coords))
        denominator = np.zeros(len(coords))
        for index in range(len(path) - 1):
            d2, arc = project(index)
            share = np.clip(1.0 - (np.sqrt(d2) - nearest) / band, 0.0, 1.0) ** 2
            numerator += share * arc
            denominator += share
        return np.clip(numerator / denominator / total, 0.0, 1.0)

    start, end = path[0], path[-1]
    direction = end - start
    length_sq = direction @ direction
    if length_sq == 0.0:
        raise ValueError("Start and end are the same point")

    offset = coords - start
    if shape == 'SPHERICAL':
        return np.clip(
            np.sqrt(np.einsum('ij,ij->i', offset, offset) / length_sq), 0.0, 1.0
        )
    if shape == 'BAND':
        centre = start + direction * 0.5
        along = ((coords - centre) @ direction) / length_sq
        return np.clip(np.abs(along) * 2.0, 0.0, 1.0)
    raise ValueError(f"Unknown shape: {shape}")


def raw_factor(co, points, shape, metrics=None):
    """Position of `co` in the 0..1 field, before the value curve and inversion.

    The path shape uses every handle; the radial shapes are defined entirely by
    the first and last, so anything in between is ignored rather than silently
    bending them.
    """
    if shape in PATH_SHAPES:
        return path_factor(co, points, metrics)

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


def value(t, profile='LINEAR', midpoint=0.5, invert=False, curve=None):
    """Weight for a position already resolved to 0..1.

    Split from `raw_factor` because the two halves have very different costs and
    very different lifetimes. Placing a vertex in the field means a nearest-
    segment search; mapping that position to a weight is arithmetic. Everything
    the panel changes except the handles and the shape only touches this half,
    so callers cache the other one and call this on its own.

    `curve` is an optional callable mapping 0..1 to 0..1, used by the session to
    read its ColorRamp. Without one the named profile and midpoint apply, which
    is what the scripting operator uses - an operator property cannot hold a
    ColorRamp.

    `invert` negates the weight, it does not mirror the position. Those are the
    same thing only for a profile that happens to be symmetric: `sqrt(1 - t)` is
    not `1 - sqrt(t)`, so mirroring left Root and its inverse summing to
    anything but one. Negating means a gradient and its inverse always add up to
    exactly 1 everywhere, whatever shape either of them is.
    """
    if curve is not None:
        result = _clamp(curve(t))
    else:
        if midpoint != 0.5:
            t = _remap_midpoint(t, midpoint)
        result = _clamp(PROFILE_CURVES[profile](t))
    return 1.0 - result if invert else result


def factor(
    co,
    points,
    shape='LINEAR',
    profile='LINEAR',
    midpoint=0.5,
    invert=False,
    curve=None,
    metrics=None,
):
    """Weight for one coordinate. Both halves in one call."""
    return value(
        raw_factor(co, points, shape, metrics),
        profile=profile,
        midpoint=midpoint,
        invert=invert,
        curve=curve,
    )


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
