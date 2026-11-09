"""Random world-travel encounters; movement and authored map events live elsewhere."""

from dataclasses import dataclass
import math
import random

from app.constants import CHUNK_SIZE
from app.models import MapChunk


TILE_METERS = 100
SAFE_BASE_METERS = 300
BASE_PERIMETER_METERS = 1000
PROTECTED_BUILDING_TYPES = frozenset({
    'hamlet', 'village', 'factory', 'base', 'camp', 'house', 'fence',
})


@dataclass(frozen=True)
class Outcome:
    category: int
    start: int
    end: int
    key: str
    name: str
    prompt: str
    potential_combat: bool = False
    human_contact: bool = False
    human_attack: bool = False
    empty: bool = False

    @property
    def width(self):
        return self.end - self.start + 1


OUTCOMES = (
    Outcome(1, 1, 30, 'anomaly', 'Аномалия', 'Впереди случайная аномалия. Проверка Внимательности для обнаружения и предотвращения попадания: СЛ = 20 − бонус детектора аномалий − бонус Внимательности.'),
    Outcome(1, 31, 60, 'mutants', 'Мутанты', 'Группа замечает мутантов или признаки их присутствия впереди. Можно оценить угрозу и попробовать обойти их.', potential_combat=True),
    Outcome(1, 61, 90, 'human_attack', 'Нападение людей', 'Враждебные люди появляются на маршруте. Контакт опасен, но решение о развитии встречи остаётся за группой и мастером.', potential_combat=True, human_contact=True, human_attack=True),
    Outcome(1, 91, 99, 'weather_decrease', 'Смена погоды', 'Один случайный параметр погоды изменяется на 0,5 в случайную сторону.'),
    Outcome(1, 100, 100, 'rare_artifact', 'Редкий артефакт', 'Группа обнаруживает редкий артефакт. Мастер определяет находку по действующим правилам.'),
    Outcome(2, 1, 15, 'trader', 'Торговец', 'На маршруте встречается торговец; вступать ли в разговор, решает группа.', human_contact=True),
    Outcome(2, 16, 25, 'marauder', 'Мародёр', 'На маршруте замечен мародёр. Его намерения и возможность избежать встречи определяет мастер.', human_contact=True, human_attack=True),
    Outcome(2, 26, 35, 'passerby_quest', 'Задание от прохожего', 'Прохожий может предложить группе задание.', human_contact=True),
    Outcome(2, 36, 45, 'scam', 'Разводилово', 'Незнакомец пытается обмануть группу; мастер разыгрывает ситуацию.', human_contact=True),
    Outcome(2, 46, 55, 'help', '«Памагите» — просьба о помощи', 'Кто-то просит о помощи; группа решает, реагировать ли.', human_contact=True),
    Outcome(2, 56, 70, 'passerby', 'Прохожий', 'Группа замечает прохожего. Разговор не обязателен.', human_contact=True),
    Outcome(2, 71, 80, 'neutral_enemy', 'Нейтрально настроенный враг', 'Появляется нейтрально настроенный враг; немедленного боя нет.', human_contact=True),
    Outcome(2, 81, 90, 'faction_skirmish', 'Стычка группировок', 'Группа наблюдает стычку группировок и может пройти мимо.', human_contact=True),
    Outcome(2, 91, 99, 'faction_camp', 'Лагерь группировки', 'На маршруте находится лагерь группировки.', human_contact=True),
    Outcome(2, 100, 100, 'massacre', 'Кровавая резня', 'Группа обнаруживает последствия кровавой резни.'),
    Outcome(3, 1, 20, 'corpse', 'Труп', 'Группа обнаруживает труп.'),
    Outcome(3, 21, 40, 'stash', 'Тайник', 'Группа обнаруживает тайник.'),
    Outcome(3, 41, 50, 'attack_on_other', 'Нападение на кого-то, кроме группы', 'В стороне происходит нападение на других; группа решает, вмешиваться ли.'),
    Outcome(3, 51, 60, 'abandoned_item', 'Брошенная вещь', 'На маршруте лежит брошенная вещь.'),
    Outcome(3, 61, 70, 'weather', 'Погода', 'Один случайный параметр погоды изменяется на 0,5 в случайную сторону.'),
    Outcome(3, 71, 85, 'nothing', 'Ничего', '', empty=True),
    Outcome(3, 86, 90, 'anomalous_field', 'Аномальное поле или возмущение', 'Замечено аномальное поле или возмущение; мастер определяет его по действующим правилам.'),
    Outcome(3, 91, 100, 'gm_special', 'Особое событие на усмотрение мастера', 'Мастер определяет особое событие.'),
    Outcome(4, 1, 1, 'trap_anomaly', 'Аномалия-ловушка', 'Аномалия-ловушка. Проверка Внимательности для обнаружения и предотвращения попадания: СЛ = 20 − бонус детектора аномалий − бонус Внимательности.'),
    Outcome(4, 2, 11, 'double_radiation', 'Двойная радиация', 'Двойная радиация; мастер применяет эффект по действующим правилам. Автоматически он не начислен.'),
    Outcome(4, 12, 88, 'disappointment', 'Разочарования', '', empty=True),
    Outcome(4, 89, 99, 'device_failure', 'Отказ приборов на 10 минут', 'Приборы отказывают на 10 минут; мастер отслеживает эффект. Автоматически он не применён.'),
    Outcome(4, 100, 100, 'teleport_anomaly', 'Аномалия-телепорт', 'Аномалия-телепорт. Проверка Внимательности для обнаружения и предотвращения попадания: СЛ = 20 − бонус детектора аномалий − бонус Внимательности. Мастер разрешает эффект.'),
)

# Chance of a nonempty card, then relative weights of the four original categories.
SCENARIOS = {
    'near_base': (0.18, (20, 45, 27, 8)),
    'base_perimeter': (0.27, (20, 45, 27, 8)),
    'wild': (0.36, (30, 20, 32, 18)),
    'forest': (0.30, (35, 4, 40, 21)),
    'road': (0.36, (24, 40, 27, 9)),
}


def _outcome_weight(outcome, scenario):
    if outcome.empty:
        return 0
    if scenario == 'near_base' and (outcome.potential_combat or outcome.key == 'marauder'):
        return 0
    weight = outcome.width
    if scenario == 'base_perimeter' and (outcome.potential_combat or outcome.key == 'marauder'):
        weight *= 0.35
    if scenario == 'forest':
        if outcome.human_attack:
            weight *= 0.2
        if outcome.key in ('trader', 'passerby', 'passerby_quest'):
            weight *= 0.35
    return weight


def outcome_probabilities(scenario):
    """Exact conditional probabilities, after suppressing explicit empty results."""
    _, categories = SCENARIOS[scenario]
    result = {}
    for category, category_weight in enumerate(categories, 1):
        candidates = [(outcome, _outcome_weight(outcome, scenario))
                      for outcome in OUTCOMES if outcome.category == category]
        total = sum(weight for _, weight in candidates)
        for outcome, weight in candidates:
            if weight:
                result[outcome.key] = category_weight / sum(categories) * weight / total
    return result


def anomaly_check_details(outcome_key, characters):
    if outcome_key not in {'anomaly', 'trap_anomaly', 'teleport_anomaly'}:
        return ''
    from app.services.electronics import anomaly_detector_bonus
    from app.services.portable_lights import walk

    details = []
    for character in characters:
        data = character.data or {}
        best = max((bonus for _, item in walk(data, True)
                    if (bonus := anomaly_detector_bonus(data, item)) is not None), default=0)
        if best:
            details.append(f'{character.name} · СЛ {20 - best:g} − бонус Внимательности.')
    return (' ' + ' '.join(details)) if details else ''


def roll_travel_event(scenario, rng=None):
    rng = rng or random
    chance, categories = SCENARIOS[scenario]
    if rng.random() >= chance:
        return None
    category = rng.choices((1, 2, 3, 4), weights=categories, k=1)[0]
    candidates = [outcome for outcome in OUTCOMES if outcome.category == category]
    weights = [_outcome_weight(outcome, scenario) for outcome in candidates]
    return rng.choices(candidates, weights=weights, k=1)[0]


def _active_base(tile):
    if not isinstance(tile, dict):
        return False
    return any(isinstance(obj, dict) and obj.get('type') in PROTECTED_BUILDING_TYPES
               and obj.get('is_active') is True
               for obj in (tile.get('objects') or ()))


def travel_scenario(lobby_id, tile_x, tile_y):
    """Use only persisted map data; unknown tiles are ordinary wilderness."""
    radius = math.ceil(BASE_PERIMETER_METERS / TILE_METERS)
    min_cx, max_cx = max(0, tile_x - radius) // CHUNK_SIZE, (tile_x + radius) // CHUNK_SIZE
    min_cy, max_cy = max(0, tile_y - radius) // CHUNK_SIZE, (tile_y + radius) // CHUNK_SIZE
    chunks = MapChunk.query.filter(
        MapChunk.lobby_id == lobby_id,
        MapChunk.chunk_x.between(min_cx, max_cx),
        MapChunk.chunk_y.between(min_cy, max_cy),
    ).all()
    nearest = float('inf')
    destination = {}
    for chunk in chunks:
        if not isinstance(chunk.data, list):
            continue
        for local_y, row in enumerate(chunk.data):
            if not isinstance(row, list):
                continue
            y = chunk.chunk_y * CHUNK_SIZE + local_y
            if abs(y - tile_y) > radius:
                continue
            for local_x, tile in enumerate(row):
                x = chunk.chunk_x * CHUNK_SIZE + local_x
                if abs(x - tile_x) > radius or not isinstance(tile, dict):
                    continue
                if (x, y) == (tile_x, tile_y):
                    destination = tile
                if _active_base(tile):
                    nearest = min(nearest, math.hypot(x - tile_x, y - tile_y) * TILE_METERS)
    if nearest <= SAFE_BASE_METERS:
        return 'near_base'
    if nearest <= BASE_PERIMETER_METERS:
        return 'base_perimeter'
    objects = destination.get('objects') or []
    if any(isinstance(obj, dict) and obj.get('type') == 'road' for obj in objects):
        return 'road'
    if destination.get('terrain') == 'forest' or any(
        isinstance(obj, dict) and obj.get('type') == 'forest' for obj in objects
    ):
        return 'forest'
    return 'wild'
