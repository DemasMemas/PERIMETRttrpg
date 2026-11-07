"""Optional JSON tile appearance. Heights and topology are never changed."""
from app.services.exceptions import ValidationError

WATER_PROFILES = frozenset(('auto', 'clean', 'neutral', 'sandy', 'peat', 'mineral', 'industrial'))


def validate_water_profile(value):
    if not isinstance(value, str) or value not in WATER_PROFILES:
        raise ValidationError('Invalid water profile')
    return value


def apply_water_profile(tile, updates):
    if 'terrain' in updates and updates['terrain'] != tile.get('terrain'):
        tile.pop('water_profile', None)
    if 'water_profile' in updates and updates.get('terrain', tile.get('terrain')) == 'water':
        tile['water_profile'] = validate_water_profile(updates['water_profile'])


def connected_world_water_updates(lobby, x, z, profile):
    from app.models import MapChunk
    from app.services.map import MapService
    validate_water_profile(profile)
    if type(x) is not int or type(z) is not int:
        raise ValidationError('Expected integer cell coordinates')
    chunks = {(c.chunk_x, c.chunk_y): c.data for c in MapChunk.query.filter_by(lobby_id=lobby.id)}
    def tile_at(cx, cz):
        if not 0 <= cx < lobby.chunks_width*32 or not 0 <= cz < lobby.chunks_height*32:
            return None
        key = (cx//32, cz//32)
        if key not in chunks:
            chunks[key] = MapService._generate_chunk_data(lobby.id, *key, lobby.map_type)
        return chunks[key][cz%32][cx%32]
    if (tile_at(x,z) or {}).get('terrain') != 'water':
        return []
    cells = [(x,z)]
    seen = {(x,z)}
    updates = []
    for cx, cz in cells:
        if tile_at(cx,cz).get('water_profile', 'auto') != profile:
            updates.append({'chunk_x':cx//32,'chunk_y':cz//32,'tile_x':cx%32,'tile_y':cz%32,
                            'updates':{'water_profile':profile}})
        for dx,dz in ((0,-1),(1,0),(0,1),(-1,0)):
            key = (cx+dx,cz+dz)
            if key not in seen and (tile_at(*key) or {}).get('terrain') == 'water':
                seen.add(key)
                cells.append(key)
    return updates


def connected_world_water_level_updates(lobby, x, z, mode, height=None):
    from app.models import MapChunk
    from app.services.map import MapService

    if type(x) is not int or type(z) is not int:
        raise ValidationError('Нужны целые координаты клетки')
    if mode not in ('minimum', 'exact'):
        raise ValidationError('Неизвестный режим высоты воды')
    if mode == 'exact' and (type(height) not in (int, float) or not .5 <= height <= 3):
        raise ValidationError('Высота воды должна быть от 0,5 до 3 м')
    chunks = {(c.chunk_x, c.chunk_y): c.data for c in MapChunk.query.filter_by(lobby_id=lobby.id)}

    def tile_at(cx, cz):
        if not 0 <= cx < lobby.chunks_width*32 or not 0 <= cz < lobby.chunks_height*32:
            return None
        key = (cx//32, cz//32)
        if key not in chunks:
            chunks[key] = MapService._generate_chunk_data(lobby.id, *key, lobby.map_type)
        return chunks[key][cz%32][cx%32]

    if (tile_at(x, z) or {}).get('terrain') != 'water':
        return []
    cells = [(x, z)]
    seen = {(x, z)}
    for cx, cz in cells:
        for dx, dz in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            neighbor = (cx+dx, cz+dz)
            if neighbor not in seen and (tile_at(*neighbor) or {}).get('terrain') == 'water':
                seen.add(neighbor)
                cells.append(neighbor)
    target = min(tile_at(cx, cz).get('height', 1) for cx, cz in cells) if mode == 'minimum' else height
    if type(target) not in (int, float) or not .5 <= target <= 3:
        raise ValidationError('Высота воды должна быть от 0,5 до 3 м')
    return [{'chunk_x': cx//32, 'chunk_y': cz//32, 'tile_x': cx%32, 'tile_y': cz%32,
             'updates': {'height': target}}
            for cx, cz in cells if tile_at(cx, cz).get('height', 1) != target]
