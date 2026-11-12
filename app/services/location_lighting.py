import math
import re

from app.services.exceptions import ValidationError
from app.utils.defaults import default_location_lighting


_COLOR = re.compile(r'^#[0-9a-fA-F]{6}$')


def _color(value, label):
    if not isinstance(value, str) or not _COLOR.fullmatch(value):
        raise ValidationError(f'{label}: нужен цвет в формате #RRGGBB')
    return value.lower()


def _number(value, label, minimum, maximum):
    if isinstance(value, bool):
        raise ValidationError(f'{label}: требуется число от {minimum} до {maximum}')
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f'{label}: требуется число от {minimum} до {maximum}') from None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValidationError(f'{label}: требуется число от {minimum} до {maximum}')
    return round(number, 3)


def normalize_location_lighting(value, width, height):
    if not isinstance(value, dict):
        raise ValidationError('Настройки освещения должны быть объектом')
    defaults = default_location_lighting()
    allowed = set(defaults)
    if set(value) - allowed:
        raise ValidationError('Неизвестные параметры освещения')
    result = {**defaults, **value}
    if result['mode'] not in {'world', 'manual'}:
        raise ValidationError('Неизвестный режим освещения')
    for key in ('ambient_color', 'directional_color', 'background_color'):
        result[key] = _color(result[key], key)
    for key in ('ambient_intensity', 'directional_intensity'):
        result[key] = _number(result[key], key, 0, 3)
    result['fog_density'] = _number(result['fog_density'], 'Плотность дымки', 0, 0.1)
    if type(result['water_reflections']) is not bool:
        raise ValidationError('Параметр отражений в воде должен быть логическим')
    if result['natural_detail'] is not None and type(result['natural_detail']) is not bool:
        raise ValidationError('Параметр природного декора должен быть логическим')
    result['directional_azimuth'] = _number(result['directional_azimuth'], 'Направление света', -180, 180)
    result['directional_elevation'] = _number(result['directional_elevation'], 'Высота света', 0, 90)
    lights = result['local_lights']
    if not isinstance(lights, list):
        raise ValidationError('Локальные источники света должны быть списком')
    normalized_lights = []
    for light in lights:
        required = {'x', 'z', 'height', 'color', 'intensity', 'distance'}
        if not isinstance(light, dict) or set(light) - required - {'kind', 'azimuth', 'dimensions', 'gm_hidden', 'level'}:
            raise ValidationError('Некорректный локальный источник света')
        if not required.issubset(light):
            raise ValidationError('У локального источника света не хватает параметров')
        kind = light.get('kind', 'point')
        if kind not in ('point', 'spot'):
            raise ValidationError('Неизвестный тип локального светильника')
        level = light.get('level', 0)
        if type(level) is not int:
            raise ValidationError('Уровень светильника должен быть целым числом')
        normalized = {
            'x': _number(light['x'], 'Координата X', 0, width),
            'z': _number(light['z'], 'Координата Z', 0, height),
            'height': _number(light['height'], 'Высота источника', level * 2.4, level * 2.4 + 10),
            'color': _color(light['color'], 'Цвет источника'),
            'intensity': _number(light['intensity'], 'Яркость источника', 0, 10),
            'distance': _number(light['distance'], 'Дальность источника', 1, 30),
            'kind': kind,
            'azimuth': _number(light.get('azimuth', 0), 'Направление светильника', -180, 180),
        }
        if 'level' in light:
            normalized['level'] = level
        if type(light.get('gm_hidden', False)) is not bool:
            raise ValidationError('gm_hidden must be a boolean')
        normalized['gm_hidden'] = light.get('gm_hidden', False)
        if 'dimensions' in light:
            dimensions = light['dimensions']
            if not isinstance(dimensions, dict) or set(dimensions) != {'width', 'depth', 'height'}:
                raise ValidationError('Размеры светильника должны содержать длину, ширину и высоту')
            normalized['dimensions'] = {
                'width': _number(dimensions['width'], 'Длина светильника', 0.05, 30),
                'depth': _number(dimensions['depth'], 'Ширина светильника', 0.02, 30),
                'height': _number(dimensions['height'], 'Высота светильника', 0.05, 20),
            }
        normalized_lights.append(normalized)
    result['local_lights'] = normalized_lights
    dark_tiles = result['dark_tiles']
    if not isinstance(dark_tiles, list) or len(dark_tiles) > width * height:
        raise ValidationError('Некорректный список тёмных клеток')
    seen = set()
    normalized_tiles = []
    for tile in dark_tiles:
        if not isinstance(tile, dict) or set(tile) != {'x', 'z'}:
            raise ValidationError('Некорректная тёмная клетка')
        x, z = tile['x'], tile['z']
        if (isinstance(x, bool) or isinstance(z, bool) or not isinstance(x, int)
                or not isinstance(z, int) or not (0 <= x < width and 0 <= z < height)):
            raise ValidationError('Тёмная клетка находится вне подлокации')
        if (x, z) in seen:
            raise ValidationError('Тёмная клетка указана повторно')
        seen.add((x, z))
        normalized_tiles.append({'x': x, 'z': z})
    result['dark_tiles'] = normalized_tiles
    return result
