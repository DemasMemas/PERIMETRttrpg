"""Build the visual sandbox in the existing Trava3 lobby.

Run: python -m scripts.seed_visual_test_location --lobby-id 19 --rebuild
"""

import argparse
import math

from app import create_app
from app.constants import CHUNK_SIZE
from app.extensions import db
from app.models.character import LobbyCharacter
from app.models.lobby import Lobby
from app.models.location import Location
from app.models.location_character import LocationCharacter
from app.models.location_combat import LocationCombatState
from app.models.location_object import LocationObject
from app.models.map_chunk import MapChunk
from app.services.anomaly_profiles import anomaly_catalog
from app.services.health import apply_health_maximums, health_zones_to_location
from app.services.location_lighting import normalize_location_lighting
from app.services.world_rules import mutant_character_data, mutant_profile


SCENE_NAME = 'Визуальный полигон'
STRESS_SCENE_NAME = 'Визуальный полигон 2'
SCENE_WIDTH = 38
SCENE_HEIGHT = 34
STRESS_WIDTH = 64
STRESS_HEIGHT = 56
SCENE_DESCRIPTION = (
    'Полигон визуала: бар с окнами и дверями, склад, тёмный туннель, '
    'пять поверхностей, открытый грунт и трава разной густоты у дороги и забора, '
    'плавный холм и резкий уступ, четыре вида ламп и галерея всех аномалий.'
)


def _tiles():
    tiles = []
    for z in range(SCENE_HEIGHT):
        row = []
        for x in range(SCENE_WIDTH):
            height = round(1 + 0.08 * math.sin(x / 3) * math.cos(z / 4), 2)
            terrain = 'grass'
            if 3 <= x <= 16 and 3 <= z <= 15:
                terrain, height = 'rock', 1
            elif 20 <= x <= 35 and 2 <= z <= 18:
                terrain, height = 'rock', 1
            elif abs(z - (20 + math.sin(x / 4))) < 1.4:
                terrain, height = 'sand', 1
            elif x >= 25 and z >= 25:
                terrain = 'swamp'
            if x <= 2 and 6 <= z <= 10:
                terrain = 'sand'
            if 27 <= x <= 33 and 26 <= z <= 30 and math.hypot(x - 30, z - 28) < 3.4:
                terrain, height = 'water', 0.65
            if 10 <= x <= 18 and 24 <= z <= 31:
                terrain, height = 'rock', 1
            if 30 <= x <= 36 and 19 <= z <= 24:
                terrain, height = 'rock', 1
            hill_distance = math.hypot(x - 5, z - 20)
            if 2 <= x <= 9 and 17 <= z <= 23 and hill_distance < 4.5:
                height = round(1 + 1.4 * max(0, 1 - (hill_distance / 4.5) ** 2) ** 2, 2)
            if 22 <= x <= 26 and 21 <= z <= 24:
                height = 2.6
            row.append({'terrain': terrain, 'height': height, 'objects': [], 'radiation': 0})
        tiles.append(row)

    decor = (
        ('tree', 1, 2), ('tree', 1, 12), ('tree', 2, 25), ('tree', 7, 31),
        ('tree', 19, 22), ('tree', 22, 30), ('tree', 36, 23), ('tree', 36, 30),
        ('rock', 20, 25), ('rock', 23, 27), ('rock', 25, 22),
        ('tent', 4, 24), ('tent', 6, 25),
    )
    for kind, x, z in decor:
        tiles[z][x]['objects'].append({'type': kind})
    tiles[3][9]['objects'].append({
        'type': 'poster', 'x': 0, 'z': 0.13, 'elevation': 1.6,
        'rotation': -90, 'color': '#d9b97b',
        'dimensions': {'width': 0.8, 'depth': 0.035, 'height': 1.1},
    })

    colors = {
        'void': '#9a88c7', 'electric': '#7bc5ee', 'fire': '#f6a258',
        'radiation': '#a5c961', 'acid': '#a8d26e', 'psi': '#ba9bd2',
    }
    for index, anomaly in enumerate(anomaly_catalog()):
        x = 20 + (index % 8) * 2
        z = 2 + (index // 8) * 3
        visual = anomaly.get('visual') or 'void'
        tiles[z][x]['objects'].append({
            'type': 'anomaly', 'anomalyKey': anomaly['key'],
            'anomalyName': anomaly['name'], 'anomalyRank': anomaly.get('rank'),
            'anomalyCategory': anomaly.get('category'), 'anomalyType': visual,
            'color': colors.get(visual, '#9a88c7'), 'x': 0, 'z': 0,
        })
    tiles[11][34]['radiation'] = 2
    tiles[14][34]['radiation'] = 4
    return tiles


def _stress_tiles():
    base = _tiles()
    tiles = []
    for z in range(STRESS_HEIGHT):
        row = []
        for x in range(STRESS_WIDTH):
            if x < SCENE_WIDTH and z < SCENE_HEIGHT:
                row.append(base[z][x])
                continue
            terrain = 'grass'
            height = round(1 + 0.05 * math.sin(x / 4) * math.cos(z / 6), 2)
            if x >= 38 and abs(z - (20 + 1.5 * math.sin(x / 5))) < 1.5:
                terrain, height = 'sand', 1
            elif 40 <= x <= 45 and 40 <= z <= 45:
                terrain, height = 'rock', 1.35
            elif x >= 51 and z >= 37:
                terrain = 'swamp'
            if 54 <= x <= 60 and 44 <= z <= 50 and math.hypot(x - 57, z - 47) < 3.5:
                terrain, height = 'water', 0.65
            row.append({'terrain': terrain, 'height': height, 'objects': [], 'radiation': 0})
        tiles.append(row)
    for index in range(45):
        x = 38 + (index * 17) % 26
        z = 34 + (index * 23) % 22
        tile = tiles[z][x]
        if tile['terrain'] == 'grass':
            tile['objects'].append({'type': 'tree' if index % 3 == 0 else 'rock'})
    return tiles


def _structures(location, *, stress=False):
    structures = []

    def add(name, kind, x, z, width, depth, height, color, **extra):
        structures.append(LocationObject(
            location=location, name=name, type=kind, tile_x=x, tile_y=z,
            properties={
                'dimensions': {'width': width, 'depth': depth, 'height': height},
                'color': color, **extra,
            },
        ))

    add('Пол дома', 'floor', 9, 8, 13, 11, 0.12, '#827a70', offset_x=0.5, offset_z=0.5)
    add('Северная стена', 'wall', 9, 3, 13, 0.2, 2.5, '#746e63', offset_x=0.5)
    add('Южная стена', 'wall', 9, 14, 13, 0.2, 2.5, '#746e63', offset_x=0.5)
    add('Западная стена', 'wall', 3, 8, 11, 0.2, 2.5, '#746e63',
        rotation=math.pi / 2, offset_z=0.5)
    add('Восточная стена', 'wall', 16, 8, 11, 0.2, 2.5, '#746e63',
        rotation=math.pi / 2, offset_z=0.5)
    add('Главная дверь', 'door', 9, 14, 0.9, 0.18, 2.1, '#75583e', is_open=False)
    add('Дверь склада', 'door', 16, 9, 0.9, 0.18, 2.1, '#75583e',
        rotation=math.pi / 2, is_open=False)
    add('Окно бара', 'window', 6, 3, 1.2, 0.14, 1.1, '#a9c4cf', elevation=1, is_open=True)
    add('Окно склада', 'window', 12, 3, 1.2, 0.14, 1.1, '#a9c4cf', elevation=1, is_open=False)
    add('Боковое окно', 'window', 16, 6, 1.2, 0.14, 1.1, '#a9c4cf',
        rotation=math.pi / 2, elevation=1, is_open=True)
    add('Настенный факел', 'wall_torch', 5, 14, 0.28, 0.42, 0.75, '#6b4a30',
        rotation=math.pi, offset_z=-0.2, elevation=1.25, is_lit=True)
    add('Потолочная лампочка', 'ceiling_bulb', 10, 7, 0.32, 0.32, 0.7, '#ffe2aa',
        elevation=2.4, is_on=True)
    add('Настенная лампочка', 'wall_bulb', 12, 14, 0.38, 0.4, 0.36, '#ffe2aa',
        rotation=math.pi, offset_z=-0.2, elevation=1.8, is_on=True)
    add('Барная стойка', 'table', 7, 6, 3, 0.8, 1, '#775139')
    for x, z in ((6, 10), (11, 10)):
        add('Стол', 'table', x, z, 1.2, 0.85, 0.8, '#806044')
        add('Стул', 'chair', x + 1, z, 0.55, 0.55, 1, '#74563d')
    add('Стеллаж', 'shelf', 14, 6, 1.2, 0.4, 2, '#78634c')
    add('Военный ящик', 'military_crate', 12, 11, 1.1, 0.8, 0.65, '#45543a', contents=[])
    for x, z in ((5, 5), (13, 5), (17, 20)):
        add('Бочка', 'barrel', x, z, 0.65, 0.65, 1, '#8a6444', weight_kg=40)
    for x, z in ((17, 8), (18, 8), (23, 24)):
        add('Ящик', 'box', x, z, 0.8, 0.8, 0.7, '#8b6b4f', weight_kg=25, contents=[])
    for z in range(5, 18, 3):
        add('Ограждение аномалий', 'fence', 18, z, 2.2, 0.15, 1.2, '#8b7458', rotation=math.pi / 2)
    add('Северная стена туннеля', 'wall', 14, 24, 8, 0.2, 2.5, '#575c5b',
        visual_asset='art_rusty_metal')
    add('Южная стена туннеля', 'wall', 14, 30, 8, 0.2, 2.5, '#575c5b')
    add('Западная стена туннеля', 'wall', 10, 27, 6, 0.2, 2.5, '#575c5b', rotation=math.pi / 2)
    add('Восточная стена туннеля', 'wall', 18, 27, 6, 0.2, 2.5, '#575c5b', rotation=math.pi / 2)
    add('Дверь туннеля', 'door', 14, 24, 0.9, 0.18, 2.1, '#646861', is_open=True)
    add('Пол туннеля', 'floor', 14, 27, 8, 6, 0.12, '#555958',
        visual_asset='art_dirty_earth')
    add('Пол малого дома', 'floor', 33, 21, 4, 4, 0.12, '#756b5d',
        visual_asset='art_aged_wood')
    add('Северная стена малого дома', 'wall', 33, 19, 4, 0.2, 2.3, '#867e70')
    add('Южная стена малого дома', 'wall', 33, 23, 4, 0.2, 2.3, '#867e70',
        visual_asset='art_worn_plaster')
    add('Западная стена малого дома', 'wall', 31, 21, 4, 0.2, 2.3, '#867e70', rotation=math.pi / 2)
    add('Восточная стена малого дома', 'wall', 35, 21, 4, 0.2, 2.3, '#867e70', rotation=math.pi / 2)
    add('Дверь малого дома', 'door', 33, 23, 0.9, 0.18, 2.1, '#75583e', is_open=True)
    add('Окно малого дома', 'window', 33, 19, 1.2, 0.14, 1.1, '#a9c4cf', elevation=1)
    add('Костёр', 'campfire', 6, 27, 0.9, 0.9, 0.7, '#74604b', is_lit=True)
    add('Факел', 'torch', 32, 21, 0.25, 0.25, 1.7, '#6b4a30', is_lit=True)
    add('Бочка с огнём', 'fire_barrel', 23, 19, 0.72, 0.72, 0.78, '#5f625c', is_lit=True)
    # The base types keep storage, collision and interactions unchanged.
    add('Ржавая бочка PBR', 'barrel', 23, 25, 0.65, 0.65, 0.9, '#8a6444',
        visual_asset='art_rusty_barrel', weight_kg=40)
    add('Старый ящик PBR', 'box', 25, 25, 0.8, 0.8, 0.7, '#8b6b4f',
        visual_asset='art_wooden_crate', weight_kg=25, contents=[])
    add('Сетчатый забор', 'fence', 28, 25, 2, 0.15, 1.2, '#8b7458',
        visual_asset='art_chainlink_fence')
    if stress:
        for index, x in enumerate(range(41, 62, 3)):
            add('Сетчатый забор', 'fence', x, 8, 2, 0.15, 1.2, '#8b7458',
                visual_asset='art_chainlink_fence')
            add('Сетчатый забор', 'fence', x, 30, 2, 0.15, 1.2, '#8b7458',
                visual_asset='art_chainlink_fence')
        for index in range(18):
            x = 41 + (index % 6) * 3
            z = 12 + (index // 6) * 4
            add(f'Складской ящик {index}', 'box', x, z, 0.8, 0.8, 0.7, '#8b6b4f',
                visual_asset='art_wooden_crate', contents=[])
        for index in range(8):
            add(f'Бочка полигона {index}', 'barrel', 42 + index * 2, 26,
                0.65, 0.65, 0.9, '#8a6444', visual_asset='art_rusty_barrel', weight_kg=40)
        for index, x in enumerate((43, 49, 55, 61)):
            add('Грязная земля', 'floor', x, 34, 3, 3, 0.12, '#776d5d',
                visual_asset='art_dirty_earth')
    return structures


def _lighting(*, stress=False):
    dark_tiles = [
        {'x': x, 'z': z}
        for x in range(4, 16) for z in range(4, 14)
    ] + [
        {'x': x, 'z': z}
        for x in range(11, 18) for z in range(25, 30)
    ]
    lights = [
        {'x': 7.5, 'z': 7.5, 'height': 2.2, 'color': '#ffd090', 'intensity': 2.5, 'distance': 6},
        {'x': 12.5, 'z': 11.5, 'height': 2.2, 'color': '#ffd090', 'intensity': 2.2, 'distance': 5},
        {'x': 18.5, 'z': 22.5, 'height': 2.2, 'color': '#a9d9ff', 'intensity': 2.5, 'distance': 7},
        {'x': 12.5, 'z': 26.5, 'height': 1.8, 'color': '#ff674f', 'intensity': 1.6, 'distance': 4},
        {'x': 18.5, 'z': 19.5, 'height': 2.6, 'color': '#fff0c8', 'intensity': 4,
         'distance': 12, 'kind': 'spot', 'azimuth': 0},
    ]
    if stress:
        lights.extend([
            {'x': 45.5, 'z': 17.5, 'height': 2.4, 'color': '#f4dfb3', 'intensity': 1.8, 'distance': 8},
            {'x': 56.5, 'z': 25.5, 'height': 2.4, 'color': '#d9e7f0', 'intensity': 1.8, 'distance': 8},
        ])
    return normalize_location_lighting({
        'mode': 'manual',
        'ambient_color': '#aab6bd', 'ambient_intensity': 0.55,
        'directional_color': '#fff0d5', 'directional_intensity': 0.75,
        'directional_azimuth': 50, 'directional_elevation': 58,
        'background_color': '#445467',
        'dark_tiles': dark_tiles,
        'local_lights': lights,
    }, STRESS_WIDTH if stress else SCENE_WIDTH, STRESS_HEIGHT if stress else SCENE_HEIGHT)


def _test_participants(location, gm_id, *, stress=False):
    roster = [
        ('scout', 'Полигон: разведчик', None, 8, 19, 'Испытатели', '#68b8bd'),
        ('keeper', 'Полигон: смотритель', None, 8, 9, 'Испытатели', '#68b8bd'),
        ('dog', 'Полигон: собака', 'Собака', 18, 22, 'Мутанты', '#c46f54'),
        ('blind', 'Полигон: слепыш', 'Слепыш', 25, 25, 'Мутанты', '#c46f54'),
        ('bloodsucker', 'Полигон: кровопийца', 'Кровопийца', 16, 28, 'Мутанты', '#c46f54'),
    ]
    if stress:
        roster.extend((f'stress-dog-{index}', f'Полигон: собака {index}', 'Собака',
                       42 + index * 2, 37, 'Мутанты', '#c46f54') for index in range(6))
        roster.extend((f'stress-blind-{index}', f'Полигон: слепыш {index}', 'Слепыш',
                       43 + index * 3, 48, 'Мутанты', '#c46f54') for index in range(4))
    existing = {
        character.data.get('_visual_test_role'): character
        for character in LobbyCharacter.query.filter_by(lobby_id=location.lobby_id).all()
        if (character.data or {}).get('_visual_test_scene') == location.name
    }
    for role, name, mutant_type, x, z, team, color in roster:
        character = existing.get(role)
        if character is None:
            if mutant_type:
                data = mutant_character_data(mutant_profile(mutant_type))
            else:
                data = {'basic': {'species': 'Человек', 'is_npc': True},
                        'is_npc': True, 'inventory': {'pockets': [], 'backpack': []}}
                apply_health_maximums(data)
            data['_visual_test_scene'] = location.name
            data['_visual_test_role'] = role
            character = LobbyCharacter(
                lobby_id=location.lobby_id, owner_id=gm_id, name=name,
                data=data, visible_to=[], editable_to=[], time_active=False,
            )
            db.session.add(character)
            db.session.flush()
        db.session.add(LocationCharacter(
            location_id=location.id, character_id=character.id,
            pos_x=x, pos_y=z, team_name=team, team_color=color,
            hp_zones=health_zones_to_location((character.data or {}).get('health') or {}),
        ))


def _populate(location, lobby, *, commit=True, stress=False):
    combat = LocationCombatState.query.filter_by(location_id=location.id).first()
    if combat and combat.status != 'idle':
        raise ValueError('Stop combat in the visual sandbox before rebuilding it')
    participants = LocationCharacter.query.filter_by(location_id=location.id).all()
    if any((entry.character.data or {}).get('_visual_test_scene') != location.name for entry in participants):
        raise ValueError('The visual sandbox contains non-test characters; move them out first')
    for entry in participants:
        db.session.delete(entry)
    db.session.flush()
    for obj in LocationObject.query.filter_by(location_id=location.id).all():
        db.session.delete(obj)
    db.session.flush()
    db.session.expire(location, ['objects'])
    location.grid_width = STRESS_WIDTH if stress else SCENE_WIDTH
    location.grid_height = STRESS_HEIGHT if stress else SCENE_HEIGHT
    location.tiles_data = _stress_tiles() if stress else _tiles()
    location.lighting = _lighting(stress=stress)
    location.description = (SCENE_DESCRIPTION + ' Расширенная стресс-сцена для замера FPS.'
                            if stress else SCENE_DESCRIPTION)
    location.spawn_points = [{'x': 8, 'y': 19}]
    db.session.add_all(_structures(location, stress=stress))
    db.session.flush()
    _test_participants(location, lobby.gm_id, stress=stress)
    if commit:
        db.session.commit()


def create_visual_test_location(lobby_id):
    lobby = db.session.get(Lobby, lobby_id)
    if lobby is None or lobby.name != 'Трава3':
        raise ValueError('Expected the selected existing Trava3 lobby')
    existing = Location.query.filter_by(lobby_id=lobby.id, name=SCENE_NAME).first()
    if existing:
        return existing, False

    center_x = lobby.chunks_width * CHUNK_SIZE // 2
    center_z = lobby.chunks_height * CHUNK_SIZE // 2
    center_chunk = MapChunk.query.filter_by(
        lobby_id=lobby.id, chunk_x=center_x // CHUNK_SIZE,
        chunk_y=center_z // CHUNK_SIZE,
    ).first()
    if center_chunk is None:
        raise ValueError('The center chunk does not exist; the world map was not changed')
    if Location.query.filter_by(lobby_id=lobby.id, world_tile_x=center_x, world_tile_z=center_z).first():
        raise ValueError('The center tile already has a location marker')
    location = Location(
        lobby_id=lobby.id, name=SCENE_NAME, description=SCENE_DESCRIPTION,
        type='exploration', grid_width=SCENE_WIDTH, grid_height=SCENE_HEIGHT,
        tiles_data=_tiles(), lighting=_lighting(),
        world_tile_x=center_x, world_tile_z=center_z, world_radius=0,
        spawn_points=[{'x': 8, 'y': 19}],
    )
    db.session.add(location)
    db.session.flush()
    _populate(location, lobby)
    return location, True


def rebuild_visual_test_location(lobby_id):
    lobby = db.session.get(Lobby, lobby_id)
    if lobby is None or lobby.name != 'Трава3':
        raise ValueError('Expected the selected existing Trava3 lobby')
    location = Location.query.filter_by(lobby_id=lobby.id, name=SCENE_NAME).first()
    if location is None:
        return create_visual_test_location(lobby_id)
    _populate(location, lobby)
    return location, False


def rebuild_both_visual_test_locations(lobby_id):
    lobby = db.session.get(Lobby, lobby_id)
    if lobby is None or lobby.name != 'Трава3':
        raise ValueError('Expected the selected existing Trava3 lobby')
    standard = Location.query.filter_by(lobby_id=lobby.id, name=SCENE_NAME).first()
    stress = Location.query.filter_by(lobby_id=lobby.id, name=STRESS_SCENE_NAME).first()
    if standard is None or stress is None:
        raise ValueError('Both visual sandboxes must already exist; no map markers were changed')
    _populate(standard, lobby, commit=False)
    _populate(stress, lobby, commit=False, stress=True)
    db.session.commit()
    return standard, stress


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lobby-id', type=int, required=True)
    parser.add_argument('--rebuild', action='store_true')
    parser.add_argument('--both', action='store_true', help='Rebuild both existing sandboxes atomically')
    args = parser.parse_args()
    app = create_app()
    with app.app_context():
        try:
            if args.both:
                standard, stress = rebuild_both_visual_test_locations(args.lobby_id)
            else:
                location, created = (
                    rebuild_visual_test_location(args.lobby_id) if args.rebuild
                    else create_visual_test_location(args.lobby_id)
                )
        except Exception:
            db.session.rollback()
            raise
        if args.both:
            print(f'Rebuilt: standard {standard.id} ({standard.grid_width}x{standard.grid_height}), '
                  f'stress {stress.id} ({stress.grid_width}x{stress.grid_height}); '
                  'world markers and chunks unchanged')
        else:
            state = 'Created' if created else 'Rebuilt' if args.rebuild else 'Already exists'
            print(f'{state}: location {location.id}, lobby {location.lobby_id}, '
                  f'world tile ({location.world_tile_x}, {location.world_tile_z})')


if __name__ == '__main__':
    main()
