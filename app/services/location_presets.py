"""Рельеф, освещение и расстановка подлокации «Болотный берег»."""

import json
from math import cos, sin, tanh
from pathlib import Path

from app.services.location_lighting import normalize_location_lighting


WIDTH = HEIGHT = 42
ORIGIN_X, ORIGIN_Z = 16, 20
WATER_HEIGHT = 0.96
PLACEMENTS = Path(__file__).resolve().parents[1] / 'data' / 'swamp_bank_objects.json'

# Источники CC0. Растительность и камни Poly Haven, камыш, сухая трава и
# сухостой Pixor. В игре используются облегчённые варианты из предпросмотра.
# https://polyhaven.com/license
# https://pixor.art/library/license/


def _shoreline(z):
    return 2.1 + .95 * sin(.31 * z) + .31 * sin(.89 * z + .8)


def _tiles():
    tiles = []
    for z in range(HEIGHT):
        row = []
        pz = z + .5 - ORIGIN_Z
        for x in range(WIDTH):
            px = x + .5 - ORIGIN_X
            bank = _shoreline(pz) - px
            if bank < 0:
                row.append(dict(terrain='water', height=WATER_HEIGHT, water_profile='peat',
                                water_level_offset=0, objects=[], radiation=0))
                continue
            edge = .040 * sin(1.6 * px + .82 * pz) + .030 * sin(2.5 * pz - .65 * px)
            edge += .028 * cos(2.6 * px + 2 * pz)
            height = max(WATER_HEIGHT + .025, 1 + .34 * tanh(bank * .62) + edge
                         + .045 * sin(.32 * px - .31 * pz))
            litter = sin(px * 1.3 + pz * .5) + .45 * sin(pz * 2 - px)
            if bank < .82:
                terrain = 'shallow_water'
            else:
                terrain = 'brown_mud_leaves_01' if bank > 1.15 and litter > .07 else 'swamp'
            row.append(dict(terrain=terrain, height=round(height, 3), objects=[], radiation=0))
        tiles.append(row)
    return tiles


def build_swamp_bank():
    return dict(
        description='Топкий берег с затопленным сухостоем. По кромке воды можно пройти между кустами и корнями.',
        grid_width=WIDTH, grid_height=HEIGHT,
        tiles_data=_tiles(),
        spawn_points=[dict(x=15, y=15), dict(x=14, y=15), dict(x=15, y=16)],
        lighting=normalize_location_lighting(dict(
            mode='manual', ambient_color='#c6cec0', ambient_intensity=.78,
            directional_color='#e9e4d1', directional_intensity=.92,
            directional_azimuth=154, directional_elevation=52,
            background_color='#a1a99a', fog_density=.028, water_reflections=True,
            natural_detail=False,
        ), WIDTH, HEIGHT),
        objects=json.loads(PLACEMENTS.read_text(encoding='utf-8')),
    )

