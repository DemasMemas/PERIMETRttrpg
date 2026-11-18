"""Object geometry for cover and the catalogue of surfaces that support a weapon."""
import math

from app.services.location_navigation import level_of, number, value
from app.services.movement_geometry import WallBox, tree_trunk_box


SUPPORT_TYPES = {
    'window', 'box', 'chest', 'military_crate', 'household_wood_crate',
    'art_wooden_crate', 'household_nightstand', 'table', 'household_dining_table',
    'household_coffee_table', 'household_school_desk', 'household_safe_small',
    'household_safe_large', 'household_sandbag', 'household_sandbag_wall',
    'sandbag', 'sandbag_wall', 'barricade_wood', 'barricade_metal',
    'barricade_reinforced', 'barricade_brick',
}
SUPPORT_RECIPES = {f'recipe-m{index}' for index in range(19, 24)}
TABLE_TYPES = {'table', 'household_dining_table', 'household_coffee_table', 'household_school_desk'}

# Normalized centers and dimensions in model-local X, Z, height.
TABLE_PARTS = [(0, 0, .92, 1, 1, .16), *[
    (x, z, .42, .08, .08, .84) for x in (-.4, .4) for z in (-.4, .4)
]]
POST_PARTS = [(-.18, 0, .03, .5, .5, .06), (-.18, 0, .48, .05, .05, .9)]
FENCE_FRAME = [(x, 0, .5, .045, .45, 1) for x in (-.48, .48)] + [
    (0, 0, y, 1, .35, .045) for y in (.05, .95)
]
MODEL_PARTS = {**dict.fromkeys(TABLE_TYPES, TABLE_PARTS),
               **dict.fromkeys(('household_street_lamp', 'household_floor_lamp'), POST_PARTS),
               'art_chainlink_fence': FENCE_FRAME}


def supports_weapon(obj):
    properties = value(obj, 'properties', {}) or {}
    kind = value(obj, 'type')
    if number(properties.get('cover_hp'), 1) <= 0:
        return False
    if kind == 'window':
        return bool(properties.get('is_open'))
    return (kind in SUPPORT_TYPES or properties.get('visual_asset') in SUPPORT_TYPES
            or properties.get('crafted_cover') in SUPPORT_RECIPES)


def parts(obj, width, depth, height, bottom):
    properties = obj.properties or {}
    angle = number(properties.get('rotation'), 0)
    center_x = obj.tile_x + .5 + number(properties.get('offset_x'), 0)
    center_z = obj.tile_y + .5 + number(properties.get('offset_z'), 0)
    level = level_of(obj)
    trunk = tree_trunk_box(obj, (width, depth))
    if trunk is not None:
        return [WallBox(trunk.x, trunk.y, trunk.width, trunk.depth, angle, level, bottom, bottom + height)]
    shapes = properties.get('cover_parts')
    if not isinstance(shapes, (list, tuple)):
        shapes = MODEL_PARTS.get(properties.get('visual_asset')) or MODEL_PARTS.get(obj.type, [(0, 0, .5, 1, 1, 1)])
    cosine, sine = math.cos(angle), math.sin(angle)
    result = []
    for shape in shapes[:64]:
        if not isinstance(shape, (list, tuple)) or len(shape) != 6:
            continue
        x, z, y, w, d, h = (number(component, 0) for component in shape)
        if min(w, d, h) <= 0:
            continue
        x, z = x * width, z * depth
        result.append(WallBox(center_x + x * cosine + z * sine, center_z - x * sine + z * cosine,
                              w * width, d * depth, angle, level,
                              bottom + (y - h / 2) * height, bottom + (y + h / 2) * height))
    return result


def ray_entry(box, start, end):
    """First intersection with a rotated box along the full 3D segment."""
    ax, az = box.local((start[0], start[2]))
    bx, bz = box.local((end[0], end[2]))
    entered, left = 0.0, 1.0
    for origin, target, low, high in (
        (ax, bx, -box.width / 2, box.width / 2),
        (az, bz, -box.depth / 2, box.depth / 2),
        (start[1], end[1], box.bottom, box.top),
    ):
        delta = target - origin
        if abs(delta) < 1e-9:
            if not low <= origin <= high:
                return None
            continue
        first, last = sorted(((low - origin) / delta, (high - origin) / delta))
        entered, left = max(entered, first), min(left, last)
        if entered > left:
            return None
    return entered if 1e-9 < entered < 1 - 1e-9 else None
