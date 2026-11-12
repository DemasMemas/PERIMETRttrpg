"""Freeze legacy visual offsets without rewriting any saved height."""
from math import isfinite


def freeze_water_offsets(tiles):
    changed = False
    seen = set()
    palette = ('grass', 'swamp', 'sand', 'rock')
    for z, row in enumerate(tiles):
        for x, tile in enumerate(row):
            if tile.get('terrain') != 'water' or (x, z) in seen:
                continue
            queue = [(x, z)]
            seen.add((x, z))
            shore = dict.fromkeys(palette, 0)
            for cx, cz in queue:
                for dx, dz in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                    nx, nz = cx + dx, cz + dz
                    if not (0 <= nz < len(tiles) and 0 <= nx < len(tiles[nz])):
                        continue
                    terrain = tiles[nz][nx].get('terrain')
                    if terrain == 'shallow_water':
                        terrain = 'swamp'
                    if terrain == 'water' and (nx, nz) not in seen:
                        seen.add((nx, nz))
                        queue.append((nx, nz))
                    elif terrain in shore:
                        shore[terrain] += 1
            total = sum(shore.values())
            dominant = max(palette, key=shore.get)
            offset = -.2 if total and dominant == 'sand' and shore[dominant] / total >= .55 else 0
            for cx, cz in queue:
                cell = tiles[cz][cx]
                value = cell.get('water_level_offset')
                if type(value) not in (int, float) or not isfinite(value) or value not in (0, -.2):
                    cell['water_level_offset'] = offset
                    changed = True
    return changed


def preserve_water_offsets(tiles, previous):
    """Older full-map editors may omit the optional field on unchanged water."""
    freeze_water_offsets(previous)
    for z, row in enumerate(tiles):
        for x, cell in enumerate(row):
            old = previous[z][x] if z < len(previous) and x < len(previous[z]) else {}
            if cell.get('terrain') == old.get('terrain') == 'water' and 'water_level_offset' not in cell:
                cell['water_level_offset'] = old.get('water_level_offset', 0)
            if cell.get('terrain') == old.get('terrain') == 'water' and 'water_profile' not in cell and 'water_profile' in old:
                cell['water_profile'] = old['water_profile']
    freeze_water_offsets(tiles)
