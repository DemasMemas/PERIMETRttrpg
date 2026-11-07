import math
import random
import heapq
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone

from app.extensions import db
from app.models import Location, LocationCharacter, LocationCombatState, LobbyParticipant, LobbyCharacter, LocationObject
from app.services.lighting_rules import location_is_dark
from app.services.weather_rules import at_character as weather_at,profile as weather_profile
from app.services.action_journal import journaled
from app.models.templates import ItemTemplate
from app.services.exceptions import NotFoundError, PermissionDenied, ValidationError
from app.services.effects import health_blocks_new_stress, advance_timed_effects, apply_effect_to_health, apply_expired_effects_to_health, apply_periodic_effects_to_health, normalize_character_effects, normalize_effect_list, sync_health_derived_statuses, tick_effects
from app.services.artifact_effects import artifact_passive_profile
from app.services.magazine import validate_magazine_selection, install_magazine
from app.services.deferred_action import (
    durable_combat_action, snapshot_action_arguments, remember_action,
    update_action_payment, serialize_deferred_action, preserve_action_payment,
    cancel_actor_actions,
    cancel_unavailable_actions,
    ensure_no_deferred_action,
)
from app.services.anomaly_profiles import anomaly_profile
from app.services.anomaly_area import anomaly_covers_tile
from app.services.character_roles import is_npc
from app.services.health import BASE_ORGAN_MAXIMUMS, apply_health_maximums
from app.services.character_traits import passive_profile as character_passives, trait_count, background_is, action_cost as trait_action_cost, weapon_accuracy_bonus,facial_profile
from sqlalchemy.orm.attributes import flag_modified


DEFAULT_ACTION_POINTS = 5
DEFAULT_FREE_ACTIONS = 1
DEFAULT_MOVEMENT_POINTS = 6
DEFAULT_CONVERSION_BASE = 10

ARMOR_MATERIAL_COEFFICIENTS = {
    'текстиль': 0.5,
    'композит': 1.0,
    'кевлар': 1.5,
    'плита': 2.0,
}
ARMOR_STAGE_LABELS = [
    '1. Целая',
    '2. Немного повреждена',
    '3. Повреждена',
    '4. Сильно повреждена',
    '5. Поломана',
]
HIT_ZONES = {
    'head', 'chest', 'abdomen',
    'left_arm', 'right_arm', 'left_leg', 'right_leg',
}

POSTURES = {
    'standing': {
        'label': 'Стоя',
        'movement_multiplier': 1,
        'walk_max_distance': 10,
        'shooting_bonus': 0,
        'ergonomics_bonus': 0,
        'stealth_bonus': 0,
        'can_run': True,
        'can_sprint': True,
        'can_correction': True,
        'can_use_low_cover': False,
    },
    'sitting': {
        'label': 'Присев',
        'movement_multiplier': 2,
        'walk_max_distance': 5,
        'shooting_bonus': 1,
        'ergonomics_bonus': 10,
        'stealth_bonus': 2,
        'can_run': False,
        'can_sprint': False,
        'can_correction': True,
        'can_use_low_cover': True,
    },
    'prone': {
        'label': 'Лёжа',
        'movement_multiplier': 3,
        'walk_max_distance': 3,
        'shooting_bonus': 2,
        'ergonomics_bonus': 20,
        'stealth_bonus': 4,
        'can_run': False,
        'can_sprint': False,
        'can_correction': False,
        'can_use_low_cover': True,
    },
}

MOVEMENT_MODES = {
    'walk': {
        'label': 'Ходьба',
        'max_distance': 10,
        'movement_divisor': 1,
        'action_points': 0,
        'free_actions': 0,
    },
    'correction': {
        'label': 'Корректировка',
        'max_distance': 3,
        'movement_divisor': None,
        'action_points': 0,
        'free_actions': 1,
    },
    'backward_sideways': {
        'label': 'Спиной / боком',
        'max_distance': 5,
        'movement_divisor': 0.5,
        'action_points': 0,
        'free_actions': 0,
    },
    'run': {
        'label': 'Бег',
        'max_distance': 20,
        'movement_divisor': 2,
        'action_points': 2,
        'free_actions': 0,
    },
    'sprint': {
        'label': 'Спринт',
        'max_distance': 30,
        'movement_divisor': 3,
        'action_points': 4,
        'free_actions': 0,
    },
}

COVER_CLASSES = {
    'conditional': {'label': 'Условное', 'max_hp': 25, 'physical_protection': 0},
    'flimsy': {'label': 'Хлипкое', 'max_hp': 50, 'physical_protection': 5},
    'medium': {'label': 'Средней прочности', 'max_hp': 100, 'physical_protection': 20},
    'strong': {'label': 'Прочное', 'max_hp': 200, 'physical_protection': 40},
    'very_strong': {'label': 'Очень прочное', 'max_hp': 400, 'physical_protection': 60},
    'titanium': {'label': 'Титановое', 'max_hp': 800, 'physical_protection': 90},
    'special': {'label': 'Особое', 'max_hp': 200, 'physical_protection': 0},
}


EXPLOSIVE_PROFILES = {
    'rgd5': {'label': '\u0420\u0413\u0414-5', 'fragment': 200, 'radius': 4, 'penetration': 25, 'blast_base': 800, 'blast_falloff': 100, 'projectile_range': None, 'fuse': 'round'},
    'rgn': {'label': '\u0420\u0413\u041d', 'fragment': 150, 'radius': 4, 'penetration': 20, 'blast_base': 900, 'blast_falloff': 100, 'projectile_range': None, 'fuse': 'turn_end'},
    'f1': {'label': '\u0424-1', 'fragment': 650, 'radius': 6, 'penetration': 50, 'blast_base': 200, 'blast_falloff': 40, 'projectile_range': None, 'fuse': 'round'},
    'rgo': {'label': '\u0420\u0413\u041e', 'fragment': 600, 'radius': 5, 'penetration': 45, 'blast_base': 250, 'blast_falloff': 45, 'projectile_range': None, 'fuse': 'turn_end'},
    'og12': {'label': '\u041e\u0413-12', 'fragment': 850, 'radius': 8, 'penetration': 75, 'blast_base': 800, 'blast_falloff': 50, 'projectile_range': 100},
    'n1012': {'label': 'N-101-2', 'fragment': 100, 'radius': 3, 'penetration': 30, 'blast_base': 800, 'blast_falloff': 100, 'projectile_range': 65},
    'vog25': {'label': '\u0412\u041e\u0413-25', 'fragment': 150, 'radius': 3, 'penetration': 20, 'blast_base': 1000, 'blast_falloff': 200, 'projectile_range': 75},
    'bang793': {'label': 'Bang-79-3', 'fragment': 400, 'radius': 6, 'penetration': 50, 'blast_base': 200, 'blast_falloff': 50, 'projectile_range': None, 'fragment_keeps_penetration': True, 'fuse': 'round'},
    'rg60tb': {'label': '\u0420\u0413-60\u0422\u0411', 'fragment': 100, 'radius': 3, 'penetration': 0, 'blast_base': 400, 'blast_falloff': 50, 'projectile_range': None, 'area_effect': 'fire', 'area_radius': 4, 'duration_rounds': 10, 'burn_rounds': 2, 'direct_burn_rounds': 4, 'burn_damage': 150, 'thermal_threshold': 65},
    'rosh92': {'label': '\u0420\u041e\u0428-92', 'fragment': 0, 'radius': 8, 'penetration': 0, 'blast_base': 2000, 'blast_falloff': 100, 'projectile_range': 30, 'incendiary_rounds': 5},
    'underbarrel_gas': {'label': '\u041f\u043e\u0434\u0441\u0442\u0432\u043e\u043b\u044c\u043d\u044b\u0439 \u0433\u0430\u0437\u043e\u0432\u044b\u0439', 'effect': 'gas', 'radius': 2.5, 'duration_rounds': 2, 'pain': 1, 'concussion_chance': 25, 'exit_pain_rounds': 2, 'projectile_range': 30},
    'underbarrel_smoke': {'label': '\u041f\u043e\u0434\u0441\u0442\u0432\u043e\u043b\u044c\u043d\u044b\u0439 \u0434\u044b\u043c\u043e\u0432\u043e\u0439', 'effect': 'smoke_growing', 'radius': 2, 'max_radius': 4, 'grow_rounds': 2, 'hold_rounds': 1, 'shrink_per_round': 2, 'projectile_range': 30, 'fuse': 'turn_end'},
    'underbarrel_flash': {'label': '\u041f\u043e\u0434\u0441\u0442\u0432\u043e\u043b\u044c\u043d\u044b\u0439 \u0441\u0432\u0435\u0442\u043e\u0448\u0443\u043c\u043e\u0432\u043e\u0439', 'effect': 'flash', 'radius': 6, 'blindness': 175, 'noise': 20, 'projectile_range': 30},
    'zarya': {'label': '\u0417\u0430\u0440\u044f', 'effect': 'flash', 'radius': 10, 'blindness': 250, 'noise': 20, 'fuse': 'turn_end'},
    'flashm2': {'label': 'Flash-M2', 'effect': 'flash', 'radius': 8, 'blindness': 300, 'noise': 25},
    'fakel': {'label': '\u0424\u0430\u043a\u0435\u043b', 'effect': 'flash', 'radius': 20, 'blindness': 400, 'noise': 30, 'fuse': 'turn_end'},
    'molotov': {'label': '\u041a\u043e\u043a\u0442\u0435\u0439\u043b\u044c \u041c\u043e\u043b\u043e\u0442\u043e\u0432\u0430', 'effect': 'fire', 'radius': 2, 'duration_rounds': 20, 'burn_rounds': 5, 'direct_burn_rounds': 8},
    'napalm': {'label': 'Napalm-AN', 'effect': 'fire', 'radius': 5, 'duration_rounds': 30, 'burn_rounds': 8, 'direct_burn_rounds': 12},
    'cheremukha': {'label': '\u0427\u0435\u0440\u0435\u043c\u0443\u0445\u0430', 'effect': 'gas', 'radius': 7, 'duration_rounds': 5, 'pain': 1, 'chemical_damage': 75, 'fuse': 'round'},
    'refresher': {'label': 'Refresher', 'effect': 'gas', 'radius': 3, 'duration_rounds': 2, 'pain': 3, 'chemical_damage': 50, 'concussion_chance': 50},
    'rdg6': {'label': '\u0420\u0414\u0413-6', 'effect': 'smoke', 'radius': 4.5, 'hold_rounds': 2, 'shrink_per_round': 1.5, 'fuse': 'round'},
    'screen': {'label': 'Screen', 'effect': 'smoke', 'radius': 3, 'hold_rounds': 2, 'shrink_per_round': 1.5},
}


# Шум из существующего каталога; отсутствие числа у спецсмеси не заменяется догадкой.
for _profile_key, _noise in {'rgd5':15,'f1':15,'rgn':15,'rgo':15,'bang793':15,'vog25':10,'n1012':10,'og12':20,'rg60tb':20}.items():
    EXPLOSIVE_PROFILES[_profile_key]['noise']=_noise

ACTION_CATALOG = [
    {'key':'flamethrower_fire','label':'Залп огнемёта','action_points':3,'free_actions':0,'movement_points':0},
    {'key':'flamethrower_service','label':'Перезарядка огнемёта','action_points':4,'free_actions':0,'movement_points':0},
    {'key':'brigadier_order','label':'Дать указание','action_points':2,'free_actions':0,'movement_points':0},
    {'key':'evade','label':'Уклонение','action_points':4,'free_actions':0,'movement_points':0},
    {'key':'extinguish_fire','label':'Тушить себя','action_points':3,'free_actions':0,'movement_points':0},
    {'key':'prepare_water','label':'Взять воду в руку','action_points':0,'free_actions':0,'movement_points':0},
    {'key':'pour_water','label':'Облить водой','action_points':2,'free_actions':0,'movement_points':0},
    {'key':'recover_concussion','label':'Преодолеть контузию','action_points':2,'free_actions':0,'movement_points':0},
    {'key': 'change_facing', 'label': '\u0420\u0430\u0437\u0432\u043e\u0440\u043e\u0442', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key': 'attack', 'label': 'Атака', 'action_points': 3, 'free_actions': 0, 'movement_points': 0},
    {'key': 'explosive_attack', 'label': '\u0412\u0437\u0440\u044b\u0432\u0447\u0430\u0442\u043a\u0430', 'action_points': 2, 'free_actions': 0, 'movement_points': 0},
    {'key': 'throw_item', 'label': 'Метнуть предмет', 'action_points': 2, 'free_actions': 0, 'movement_points': 0},
    {'key': 'aim', 'label': 'Прицеливание', 'action_points': 1, 'free_actions': 0, 'movement_points': 0},
    {'key': 'draw_weapon', 'label': 'Достать оружие', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key': 'stow_weapon', 'label': 'Освободить руки', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key': 'reload_weapon', 'label': 'Сменить магазин', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key': 'load_ammunition', 'label': 'Зарядка патронов', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key': 'reload_underbarrel', 'label': 'Зарядить подствольник', 'action_points': 5, 'free_actions': 0, 'movement_points': 0},
    {'key':'surgeon_reduction','label':'Вправить конечность без набора','action_points':2,'free_actions':0,'movement_points':0},
    {'key': 'change_equipment', 'label': 'Сменить экипировку', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key':'prosthesis','label':'Надеть или снять протез','action_points':0,'free_actions':0,'movement_points':0},
    {'key': 'change_equipment_module', 'label': 'Сменить модуль экипировки', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key': 'place_gunpoint', 'label': 'Приставить ствол', 'action_points': 2, 'free_actions': 0, 'movement_points': 0},
    {'key': 'gunpoint_shot', 'label': 'Выстрелить в упор', 'action_points': 1, 'free_actions': 0, 'movement_points': 0},
    {'key': 'escape_anomaly', 'label': 'Вырваться из аномалии', 'action_points': 3, 'free_actions': 0, 'movement_points': 0},
    {'key': 'clear_weapon_jam', 'label': 'Устранить клин', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key': 'narrative_action', 'label': 'Другое действие', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key': 'must_do_it', 'label': 'Должен это сделать', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key': 'console_ally', 'label': 'Утешить', 'action_points': 3, 'free_actions': 0, 'movement_points': 0},
    {'key': 'change_posture', 'label': 'Смена положения', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key': 'defend', 'label': 'Защита', 'action_points': 2, 'free_actions': 0, 'movement_points': 0},
    {'key': 'use_item', 'label': 'Использовать предмет', 'action_points': 1, 'free_actions': 0, 'movement_points': 0},
    {'key': 'convert_free_action_to_movement', 'label': 'Получить ОП', 'action_points': 2, 'free_actions': 1, 'movement_points': 0},
    {'key': 'take_cover', 'label': 'Занять укрытие', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key': 'leave_cover', 'label': 'Покинуть укрытие', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key': 'brace_weapon', 'label': 'Поставить оружие на упор', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key': 'melee_swing', 'label': 'Замах', 'action_points': 1, 'free_actions': 0, 'movement_points': 0},
    {'key': 'melee_block', 'label': 'Блок', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key': 'melee_disarm', 'label': 'Выхватить вещь', 'action_points': 3, 'free_actions': 0, 'movement_points': 0},
    {'key': 'melee_shove', 'label': 'Толкание', 'action_points': 2, 'free_actions': 0, 'movement_points': 0},
    {'key': 'grapple', 'label': 'Захват', 'action_points': 4, 'free_actions': 0, 'movement_points': 0},
    {'key': 'grapple_escape', 'label': 'Освободиться', 'action_points': 4, 'free_actions': 0, 'movement_points': 0},
    {'key': 'grapple_release', 'label': 'Отпустить', 'action_points': 0, 'free_actions': 1, 'movement_points': 0},
    {'key': 'grapple_strengthen', 'label': 'Усилить хват', 'action_points': 3, 'free_actions': 0, 'movement_points': 0},
    {'key': 'grapple_choke', 'label': 'Удушение', 'action_points': 5, 'free_actions': 0, 'movement_points': 0},
    {'key': 'grapple_pain_hold', 'label': 'Болевой прием', 'action_points': 3, 'free_actions': 0, 'movement_points': 0},
    {'key': 'grapple_desperate_attack', 'label': 'Отчаянная атака', 'action_points': 3, 'free_actions': 0, 'movement_points': 0},
    {'key': 'grapple_live_shield', 'label': 'Живой щит', 'action_points': 3, 'free_actions': 0, 'movement_points': 0},
    {'key': 'recover_from_shock', 'label': 'Попытаться очнуться', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key': 'mutant_jump', 'label': 'Прыжок', 'action_points': 4, 'free_actions': 0, 'movement_points': 0},
    {'key': 'jump', 'label': 'Прыжок', 'action_points': 2, 'free_actions': 0, 'movement_points': 0},
    {'key': 'recover_jump_balance', 'label': 'Восстановить равновесие', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key': 'mutant_ambush', 'label': 'Засада', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key': 'mutant_battle_cry', 'label': 'Боевой клич', 'action_points': 2, 'free_actions': 0, 'movement_points': 0},
    {'key': 'mutant_camouflage', 'label': 'Маскировка', 'action_points': 1, 'free_actions': 0, 'movement_points': 0},
    {'key': 'mutant_psy_attack', 'label': 'Пси-атака', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key': 'mutant_psy_roar', 'label': 'Пси-рёв', 'action_points': 3, 'free_actions': 0, 'movement_points': 0},
    {'key': 'mutant_psy_sleep', 'label': 'Пси-сон', 'action_points': 4, 'free_actions': 0, 'movement_points': 0},
    {'key': 'mutant_clone', 'label': 'Клонирование', 'action_points': 1, 'free_actions': 0, 'movement_points': 0},
    {'key': 'mutant_object_attack', 'label': 'Бросок объекта', 'action_points': 0, 'free_actions': 0, 'movement_points': 0},
    {'key': 'mutant_anomaly_shield', 'label': 'Аномальный щит', 'action_points': 3, 'free_actions': 0, 'movement_points': 0},
    {'key': 'mutant_steal_device', 'label': 'Украсть устройство', 'action_points': 2, 'free_actions': 0, 'movement_points': 0},
]


class CombatService:
    BUTCHERING_PARTS = {
        'meat': {'label': 'Мясо', 'points': 2, 'max': None, 'price_multiplier': 0.5, 'weight': 0.5},
        'hide': {'label': 'Шкура', 'points': 3, 'max': None, 'price_multiplier': 0.75, 'weight': 0.5},
        'limb': {'label': 'Конечность / хвост', 'points': 5, 'max': 4, 'price_multiplier': 1.0, 'weight': 0.5},
        'organ': {'label': 'Орган', 'points': 7, 'max': 4, 'price_multiplier': 1.25, 'weight': 0.25},
        'eye': {'label': 'Глаз', 'points': 9, 'max': 2, 'price_multiplier': 1.5, 'weight': 0.1},
    }
    BUTCHERING_MUTANT_MODIFIERS = {
        'слепыш': -12,
        'собака': -6,
        'кот': -6,
        'болотник': -6,
        'колобок': -5,
        'лжепес': -4,
        'ползун': -3,
        'псионик': -3,
        'кабан': -2,
        'топотун': -2,
        'кровопийца': -2,
        'туша': 0,
        'биголовый': 5,
    }
    BUTCHERING_BASE_PRICES = {
        'слепыш': 50,
        'собака': 250,
        'кот': 300,
        'болотник': 300,
        'лжепес': 450,
        'туша': 600,
        'кабан': 750,
        'топотун': 750,
        'одичалый': 1200,
        'кровопийца': 1800,
        'псионик': 3000,
        'жмурка': 7500,
        'колобок': 10000,
    }
    BUTCHERING_MEAT_RADIATION = {
        'слепыш': 25,
        'собака': 30,
        'кот': 20,
        'болотник': 20,
        'лжепес': 35,
        'туша': 25,
        'кабан': 40,
        'топотун': 40,
        'кровопийца': 45,
        'псионик': 50,
        'жмурка': 60,
        'колобок': 200,
    }
    THROWABLE_OBJECT_WEIGHTS = {
        'chair': 5.0,
        'box': 10.0,
        'military_crate': 18.0,
        'chest': 15.0,
        'table': 25.0,
        'barrel': 40.0,
    }
    ATTACK_STRESS_TRIGGERS = {
        'direct_attack',
        'indirect_damage',
        'explosion',
        'suppression',
        'area_fire',
        'anomalous_soul_projectile',
    }
    NARRATIVE_SKILLS = {
        'skills.physical.strength': 'Сила',
        'skills.physical.agility': 'Ловкость',
        'skills.physical.will': 'Воля',
        'skills.physical.awareness': 'Внимательность',
        'skills.physical.awareness.visual': 'Внимательность (зрение)',
        'skills.physical.awareness.hearing': 'Внимательность (слух)',
        'skills.physical.melee': 'Ближний бой',
        'skills.physical.shooting': 'Стрельба',
        'skills.social.charisma': 'Харизма',
        'skills.social.barter': 'Бартер',
        'skills.social.persuasion': 'Убеждение',
        'skills.social.deception': 'Обман',
        'skills.social.intimidation': 'Устрашение',
        'skills.other.medicine': 'Медицина',
        'skills.other.engineering': 'Инженерия',
        'skills.other.stealth': 'Скрытность',
        'skills.other.tactics': 'Тактика',
        'skills.other.survival': 'Выживание',
    }
    WEAPON_JAM_RESULTS = {
        1: {'label': 'Гильза не выброшена из затвора', 'fix_ap': 1, 'durability_loss': 0, 'blocks_fire': True},
        2: {'label': 'Гильза застряла в окне затворной рамы', 'fix_ap': 2, 'durability_loss': 1, 'blocks_fire': True},
        3: {'label': 'Гильза смята в затворе', 'fix_ap': 4, 'durability_loss': 3, 'blocks_fire': True},
        4: {'label': 'Сбит прицел', 'fix_ap': 4, 'durability_loss': 3, 'accuracy_penalty': 2},
        5: {'label': 'Искривлён приклад', 'fix_ap': 4, 'durability_loss': 4, 'shooting_disadvantage': True},
        6: {'label': 'Натяжение пружины', 'fix_ap': 4, 'durability_loss': 4, 'extra_wear_per_shot': 1},
        7: {'label': 'Взрыв патрона в оружии', 'fix_ap': 4, 'durability_loss': 5, 'accuracy_penalty': 4},
        8: {'label': 'Двойная подача заклинила затвор', 'fix_ap': 5, 'durability_loss': 5, 'blocks_fire': True},
        9: {'label': 'Искривлён спусковой механизм', 'fix_ap': 5, 'durability_loss': 6, 'blocks_fire': True},
        10: {'label': 'Повреждены курок, затвор и рама', 'fix_ap': 8, 'durability_loss': 10, 'blocks_fire': True},
        11: {'label': 'Разрыв ствола на конце', 'durability_loss': 10, 'accuracy_penalty': 5, 'repair_required': 'increase'},
        12: {'label': 'Разрушены ударник и затвор', 'durability_loss': 15, 'blocks_fire': True, 'repair_required': 'full'},
    }

    @staticmethod
    def _coerce_int(value, default=0):
        try:
            if value is None:
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _display_name(character):
        if not character:return None
        name=getattr(character,'name',None);data=getattr(character,'data',None) or {};basic=data.get('basic') or {}
        if basic.get('is_psi_clone'):
            return basic.get('psi_clone_display_name') or re.sub(r'\s*—\s*копия\s+\d+$','',str(name or ''))
        return name

    @staticmethod
    def _character_condition(character_data):
        data = character_data if isinstance(character_data, dict) else {}
        health = data.get('health') if isinstance(data.get('health'), dict) else {}
        effects = normalize_effect_list(health.get('effects') or [])
        active_types = {
            effect.get('type')
            for effect in effects
            if effect.get('active', True)
            and (
                effect.get('remaining') is None
                or CombatService._coerce_float(effect.get('remaining'), 0) > 0
            )
        }
        zones = health.get('zones') if isinstance(health.get('zones'), dict) else {}
        head_health = CombatService._coerce_float(
            (zones.get('head') or {}).get('current'), 1
        )
        chest_health = CombatService._coerce_float(
            (zones.get('chest') or {}).get('current'), 1
        )
        organs = health.get('organs') if isinstance(health.get('organs'), dict) else {}
        brain_health = CombatService._coerce_float(
            (organs.get('brain') or {}).get('current'), 1
        )
        skull_health = CombatService._coerce_float(
            (organs.get('skull') or {}).get('current'), 1
        )
        intoxication = CombatService._intoxication_profile(character_data)
        blood_stage = str(health.get('bloodStage') or health.get('blood') or 'normal').lower()
        if (
            'death' in active_types
            or brain_health <= 0
            or skull_health <= 0
            or blood_stage == 'fatal'
        ):
            return {'state': 'dead', 'label': 'Мёртв', 'can_act': False, 'can_recover': False}

        temperature = CombatService._coerce_float(health.get('temperature'), 36)
        current_health = CombatService._coerce_float(health.get('current'), 1)
        critical = bool(
            active_types.intersection({'critical_condition', 'unconsciousness', 'sleep'})
            or current_health <= 0
            or head_health <= 0
            or chest_health <= 0
            or blood_stage == 'critical'
            or temperature <= 29
            or temperature >= 41
            or intoxication['unconscious']
        )
        if critical:
            return {
                'state': 'critical',
                'label': 'Критическое состояние',
                'can_act': False,
                'can_recover': False,
            }
        if 'stress_stupor' in active_types:
            return {'state': 'critical', 'label': 'Stress stupor', 'can_act': False, 'can_recover': False}
        if 'shock' in active_types:
            pain_level = max(0, CombatService._coerce_int(health.get('painLevel'), 0))
            pepper_lock = any(
                effect.get('type') == 'shock'
                and effect.get('source') == 'pepper_three_head_hits'
                and effect.get('active', True)
                and CombatService._coerce_float(effect.get('remaining'), 0) > 0
                for effect in effects
            )
            return {
                'state': 'pain_shock',
                'label': 'Болевой шок',
                'can_act': False,
                'can_recover': pain_level < 10 and not pepper_lock,
            }
        return {'state': 'active', 'label': 'В сознании', 'can_act': True, 'can_recover': False}

    @staticmethod
    def _intoxication_profile(character_data):
        data = character_data if isinstance(character_data, dict) else {}
        health = data.get('health') if isinstance(data.get('health'), dict) else {}
        value = max(0, CombatService._coerce_float(health.get('intoxication'), 0))
        forced_extreme = any(
            effect.get('active', True)
            and effect.get('forced_intoxication_stage') == 'extreme'
            and (
                effect.get('remaining') is None
                or CombatService._coerce_float(effect.get('remaining'), 0) > 0
            )
            for effect in normalize_effect_list(health.get('effects') or [])
        )
        if value >= 100:
            stage, modifiers = 'deadly', (-5, -8, -5, 5, True)
        elif value >= 96:
            stage, modifiers = 'near_death', (-5, -8, -5, 5, True)
        elif value >= 81 or forced_extreme:
            stage, modifiers = 'extreme', (-5, -8, -5, 5, False)
        elif value >= 46:
            stage, modifiers = 'heavy', (-3, -5, -2, 3, False)
        elif value >= 31:
            stage, modifiers = 'medium', (-1, -3, 3, 0, False)
        elif value >= 16:
            stage, modifiers = 'light', (-1, 0, 1, 0, False)
        else:
            stage, modifiers = 'none', (0, 0, 0, 0, False)
        accuracy, agility, charisma, movement, unconscious = modifiers
        return {
            'stage': stage,
            'value': value,
            'forced_extreme': forced_extreme,
            'accuracy_modifier': accuracy,
            'agility_modifier': agility,
            'charisma_modifier': charisma,
            'movement_penalty': movement,
            'unconscious': unconscious,
        }

    @staticmethod
    def _location_character_condition(loc_char):
        character = getattr(loc_char, 'character', None)
        data = character.data if character and isinstance(character.data, dict) else {}
        return CombatService._character_condition(data)

    @staticmethod
    def _can_take_combat_turn(loc_char):
        condition = CombatService._location_character_condition(loc_char)
        data = loc_char.character.data if getattr(loc_char, 'character', None) and isinstance(loc_char.character.data, dict) else {}
        meta = (data.get('health') or {}).get('combatMeta') or {}
        return bool(
            not meta.get('pepperSkipTurnActive')
            and (
                condition.get('can_act')
                or (
                    condition.get('state') == 'pain_shock'
                    and condition.get('can_recover')
                )
            )
        )

    @staticmethod
    def ensure_character_can_act(loc_char, action_key=None):
        from app.services.prosthesis_surgery import busy
        if loc_char.character and busy(loc_char.character.data):raise ValidationError('Персонаж участвует в операции установки протеза')
        data = loc_char.character.data if getattr(loc_char, 'character', None) and isinstance(loc_char.character.data, dict) else {}
        if ((data.get('health') or {}).get('combatMeta') or {}).get('pepperSkipTurnActive'):
            raise ValidationError('Персонаж пропускает ход после попадания перцового боеприпаса')
        condition = CombatService._location_character_condition(loc_char)
        if condition['state'] == 'active':
            return condition
        if condition['state'] == 'pain_shock' and action_key == 'recover_from_shock':
            if not condition.get('can_recover', False):
                raise ValidationError("Pain must be reduced below 10 before recovering from pain shock")
            return condition
        if condition['state'] == 'dead':
            raise ValidationError("A dead character cannot act")
        if condition['state'] == 'pain_shock':
            raise ValidationError("Only an attempt to regain consciousness is available during pain shock")
        raise ValidationError("A character in critical condition cannot act")

    @staticmethod
    def _cover_profile(obj):
        properties = dict(getattr(obj, 'properties', {}) or {})
        from app.services.movement_geometry import tree_trunk_box
        tree = tree_trunk_box(obj, CombatService._object_dimensions(obj, axis_aligned=False)) is not None
        cover_class = str(properties.get('cover_class') or 'medium').lower()
        base = COVER_CLASSES.get(cover_class, COVER_CLASSES['medium'])
        max_hp = max(1, CombatService._coerce_int(properties.get('cover_max_hp'), base['max_hp']))
        current_hp = max(0, min(max_hp, CombatService._coerce_int(properties.get('cover_hp'), max_hp)))
        base_protection = max(
            0,
            min(100, CombatService._coerce_int(
                properties.get('cover_base_physical_protection'),
                20 if tree else base['physical_protection'],
            )),
        )
        current_protection = max(
            0,
            min(base_protection, CombatService._coerce_int(
                properties.get('cover_physical_protection'),
                round(base_protection * current_hp / max_hp),
            )),
        )
        return {
            'class': cover_class,
            'label': base['label'],
            'hp': current_hp,
            'max_hp': max_hp,
            'base_physical_protection': base_protection,
            'physical_protection': current_protection,
            'mesh_hit_chance': 25 if properties.get('mesh_cover') else 100,
        }

    @staticmethod
    def _is_cover_object(obj):
        properties = getattr(obj, 'properties', {}) or {}
        obj_type = str(getattr(obj, 'type', '') or '').lower()
        if properties.get('cover_enabled') is False:
            return False
        if obj_type in {'floor', 'ground_item', 'campfire', 'torch', 'fire_barrel', 'anomaly', 'poster', 'ladder', 'floor_opening'}:
            return False
        if obj_type in {'door', 'window'} and properties.get('is_open'):
            return False
        return CombatService._object_height(obj) >= 0.3

    @staticmethod
    def apply_cover_damage(obj, damage, damage_type):
        profile = CombatService._cover_profile(obj)
        damage = max(0, CombatService._coerce_int(damage, 0))
        normalized_damage_type = str(damage_type or '').strip().lower()
        destructive = normalized_damage_type in {
            'explosive', 'blast', 'взрывной', 'взрыв',
            'crushing', 'blunt', 'дробящий', 'дробление', 'fire', 'огонь',
        }
        remaining_hp = max(0, profile['hp'] - damage)
        remaining_protection = min(
            profile['physical_protection'],
            round(profile['base_physical_protection'] * remaining_hp / profile['max_hp']),
        )
        properties = dict(obj.properties or {})
        properties.update({
            'cover_class': profile['class'],
            'cover_max_hp': profile['max_hp'],
            'cover_hp': remaining_hp,
            'cover_base_physical_protection': profile['base_physical_protection'],
            'cover_physical_protection': remaining_protection,
        })
        obj.properties = properties
        flag_modified(obj, 'properties')
        destroyed = destructive and remaining_hp <= 0
        result = {
            'destroyed': destroyed,
            'class': profile['class'], 'label': profile['label'],
            'hp': remaining_hp, 'max_hp': profile['max_hp'],
            'base_physical_protection': profile['base_physical_protection'],
            'physical_protection': remaining_protection,
            'mesh_hit_chance': profile['mesh_hit_chance'],
        }
        if destroyed:
            LocationCharacter.query.filter_by(cover_object_id=obj.id).update({
                'cover_object_id': None,
                'weapon_braced': False,
                'braced_weapon_index': None,
            })
            db.session.delete(obj)
        return result

    @staticmethod
    def _characters_behind_cover(location_id, shooter, cover):
        candidates = []
        for target in LocationCharacter.query.filter_by(location_id=location_id).all():
            if target.id == shooter.id:
                continue
            continuation_distance = CombatService._cover_continuation_distance(
                shooter, cover, target
            )
            if continuation_distance is None:
                continue
            candidates.append((continuation_distance, target))
        return [
            target
            for _, target in sorted(candidates, key=lambda item: item[0])
        ]

    @staticmethod
    def _cover_continuation_distance(shooter, cover, target, max_tiles=3):
        """Return distance behind cover only for cells on the aimed ray."""
        start_x = float(shooter.pos_x) + 0.5
        start_y = float(shooter.pos_y) + 0.5
        cover_props = cover.properties or {}
        cover_x = float(cover.tile_x) + 0.5 + float(cover_props.get('offset_x', 0) or 0)
        cover_y = float(cover.tile_y) + 0.5 + float(cover_props.get('offset_z', 0) or 0)
        target_x = float(target.pos_x) + 0.5
        target_y = float(target.pos_y) + 0.5

        ray_x = cover_x - start_x
        ray_y = cover_y - start_y
        scale = max(abs(ray_x), abs(ray_y))
        if scale <= 1e-9:
            return None
        direction_x = ray_x / scale
        direction_y = ray_y / scale
        direction_length_sq = direction_x ** 2 + direction_y ** 2

        target_offset_x = target_x - cover_x
        target_offset_y = target_y - cover_y
        distance_along_ray = (
            target_offset_x * direction_x + target_offset_y * direction_y
        ) / direction_length_sq
        if distance_along_ray <= 0:
            return None

        perpendicular_distance = abs(
            target_offset_x * direction_y - target_offset_y * direction_x
        ) / math.sqrt(direction_length_sq)
        if perpendicular_distance > 0.5:
            return None

        width, depth = CombatService._object_dimensions(cover)
        exit_distances = []
        if abs(direction_x) > 1e-9:
            exit_distances.append((width / 2) / abs(direction_x))
        if abs(direction_y) > 1e-9:
            exit_distances.append((depth / 2) / abs(direction_y))
        cover_exit = min(exit_distances) if exit_distances else 0
        distance_behind_cover = distance_along_ray - cover_exit
        if distance_behind_cover <= 0 or distance_behind_cover > max_tiles:
            return None
        return distance_behind_cover

    @staticmethod
    def _line_object_entry(shooter, target, obj):
        from app.services.movement_geometry import WallBox, tree_trunk_box
        width, depth = CombatService._object_dimensions(obj,axis_aligned=False)
        properties = obj.properties or {}
        center_x = float(obj.tile_x) + 0.5 + float(properties.get('offset_x', 0) or 0)
        center_y = float(obj.tile_y) + 0.5 + float(properties.get('offset_z', 0) or 0)
        box = tree_trunk_box(obj, (width, depth)) or WallBox(center_x,center_y,width,depth,float(properties.get('rotation',0) or 0),0)
        lower = box.segment_entry((float(shooter.pos_x)+.5,float(shooter.pos_y)+.5),
                                  (float(target.pos_x)+.5,float(target.pos_y)+.5))
        if lower is None or lower <= 1e-9 or lower >= 1-1e-9:
            return None
        return lower

    @staticmethod
    def _cover_analysis(location_id, shooter, target, *, objects=None):
        from app.services.location_navigation import level_of, contains, surface, ground, opening_at
        from app.services.terrain_los import TerrainMesh
        location = db.session.get(Location, location_id)
        objects = list(objects) if objects is not None else LocationObject.query.filter_by(location_id=location_id).all()
        # Декоративные деревья тайлов используют тот же ствол, что размещённые объекты.
        for y, row in enumerate(location.tiles_data or []):
            for x, tile in enumerate(row if isinstance(row, list) else []):
                for raw in (tile.get('objects') or []) if isinstance(tile, dict) else []:
                    if isinstance(raw, dict) and (raw.get('type') == 'tree' or str(raw.get('type', '')).startswith('nature_')):
                        obj = CombatService._embedded_movement_object(raw, x, y)
                        from app.services.movement_geometry import tree_trunk_box
                        if tree_trunk_box(obj, CombatService._object_dimensions(obj, axis_aligned=False)):
                            obj.id, obj.name = None, raw.get('name') or 'Дерево'
                            obj.properties['procedural_tree'] = True
                            objects.append(obj)
        def base_height(actor):
            level = getattr(actor, 'floor_level', 0) or 0
            node = surface(location, objects, math.floor(actor.pos_x), math.floor(actor.pos_y), level)
            return node['height'] if node else ground(location, math.floor(actor.pos_x), math.floor(actor.pos_y)) + level * 2.4
        shooter_level = getattr(shooter, 'floor_level', 0) or 0
        target_level = getattr(target, 'floor_level', 0) or 0
        shooter_eye = {'standing': 1.65, 'sitting': 1.05, 'prone': 0.35}[
            CombatService._posture_key(shooter)
        ]
        if getattr(shooter,'eye_height',None) is not None:
            shooter_eye = shooter.eye_height
        zone_heights = {
            'standing': {
                'left_leg': 0.55, 'right_leg': 0.55, 'abdomen': 1.0,
                'chest': 1.35, 'left_arm': 1.25, 'right_arm': 1.25, 'head': 1.7,
            },
            'sitting': {
                'left_leg': 0.25, 'right_leg': 0.25, 'abdomen': 0.55,
                'chest': 0.85, 'left_arm': 0.8, 'right_arm': 0.8, 'head': 1.1,
            },
            'prone': {
                'left_leg': 0.2, 'right_leg': 0.2, 'abdomen': 0.25,
                'chest': 0.3, 'left_arm': 0.3, 'right_arm': 0.3, 'head': 0.4,
            },
        }[CombatService._posture_key(target)]
        shooter_eye += base_height(shooter)
        target_base=base_height(target)
        zone_heights = {zone: height + target_base for zone, height in zone_heights.items()}
        passages=[obj for obj in objects if obj.type in {'hatch','floor_opening','stairs','ladder'}]
        blocked = {}
        smoke = None if getattr(shooter,'ignore_smoke',False) else CombatService._smoke_blocks_line(location_id, shooter, target)
        if smoke:
            for zone in zone_heights:
                blocked[zone] = {
                    'object_id': None,
                    'object_name': smoke.get('name') or '\u0414\u044b\u043c',
                    'object_height': 10,
                    'distance_factor': 0.5,
                    'max_hp': 0,
                    'hp': 0,
                    'physical_protection': 0,
                    'smoke': True,
                }
        terrain = TerrainMesh(location.tiles_data)
        for zone, target_height in zone_heights.items():
            hit = terrain.first_hit((shooter.pos_x+.5, shooter_eye, shooter.pos_y+.5),
                                    (target.pos_x+.5, target_height, target.pos_y+.5),
                                    lambda hit: not opening_at(passages, 0, hit['x'], hit['z']))
            if hit and (zone not in blocked or hit['t'] < blocked[zone]['distance_factor']):
                blocked[zone] = {'terrain': True, 'object_id': None, 'object_name': 'Рельеф',
                    'object_height': hit['height'], 'distance_factor': hit['t'],
                    'max_hp': 0, 'hp': 0, 'physical_protection': 0}
        doors = [item for item in objects if item.type == 'door' or (item.type == 'window' and (item.properties or {}).get('is_open'))]
        for obj in objects:
            if obj.type == 'hatch' and (obj.properties or {}).get('is_open'):
                continue
            if obj.type in {'floor', 'ceiling', 'roof', 'hatch'}:
                plane = ground(location, obj.tile_x, obj.tile_y) + level_of(obj) * 2.4 + float((obj.properties or {}).get('elevation', 0) or 0)
                for zone, target_height in zone_heights.items():
                    delta = target_height - shooter_eye
                    t = (plane - shooter_eye) / delta if abs(delta) > .00001 else -1
                    x = shooter.pos_x + .5 + (target.pos_x - shooter.pos_x) * t
                    y = shooter.pos_y + .5 + (target.pos_y - shooter.pos_y) * t
                    from app.services.location_navigation import opening_at
                    if not 0 < t < 1 or not contains(obj,x,y):
                        continue
                    stairwell = opening_at(passages, level_of(obj), x, y, (obj.properties or {}).get('building_id'))
                    if not stairwell:
                        blocked[zone] = {'object_id': obj.id, 'object_name': obj.name or obj.type,
                                         'object_height': plane, 'distance_factor': t,
                                         **CombatService._cover_profile(obj)}
                continue
            if shooter_level == target_level and level_of(obj) != shooter_level:
                continue
            if not CombatService._is_cover_object(obj):
                continue
            entry = CombatService._line_object_entry(shooter, target, obj)
            if entry is None:
                continue
            bottom = ground(location, obj.tile_x, obj.tile_y) + level_of(obj) * 2.4 + float((obj.properties or {}).get('elevation', 0) or 0)
            height = bottom + CombatService._object_height(obj)
            for zone, target_height in zone_heights.items():
                ray_height = shooter_eye + (target_height - shooter_eye) * entry
                if obj.type == 'wall' and any(
                    CombatService._line_through_door(shooter, target, door, obj, ray_height - bottom)
                    for door in doors
                ):
                    continue
                if bottom <= ray_height <= height:
                    previous = blocked.get(zone)
                    if not previous or entry < previous['distance_factor']:
                        blocked[zone] = {
                            'object_id': obj.id,
                            'object_name': obj.name or obj.type,
                            'object_height': height,
                            'distance_factor': round(entry, 4),
                            'procedural_tree': bool((obj.properties or {}).get('procedural_tree')),
                            **CombatService._cover_profile(obj),
                        }
        grade, accuracy_penalty, disadvantage, targetable = (
            CombatService._cover_grade(len(blocked), len(zone_heights))
        )
        return {
            'grade': grade,
            'blocked_zones': list(blocked),
            'zones': blocked,
            'accuracy_penalty': accuracy_penalty,
            'disadvantage': disadvantage,
            'targetable': targetable,
        }

    @staticmethod
    def _cover_grade(blocked_count, total_zones=7):
        blocked_count = max(0, CombatService._coerce_int(blocked_count, 0))
        total_zones = max(1, CombatService._coerce_int(total_zones, 7))
        if blocked_count == 0:
            grade, accuracy_penalty, disadvantage, targetable = 'none', 0, False, True
        elif blocked_count <= 3:
            grade, accuracy_penalty, disadvantage, targetable = 'half', 0, False, True
        elif blocked_count < total_zones:
            grade, accuracy_penalty, disadvantage, targetable = 'three_quarters', 0, False, True
        else:
            grade, accuracy_penalty, disadvantage, targetable = 'full', 0, False, False
        return grade, accuracy_penalty, disadvantage, targetable

    @staticmethod
    def _persistent_weapon_index(loc_char):
        character = getattr(loc_char, 'character', None)
        data = character.data if character and isinstance(character.data, dict) else {}
        weapons = data.get('weapons') if isinstance(data.get('weapons'), list) else []
        index = CombatService._coerce_int(data.get('activeWeaponIndex'), -1)
        return index if 0 <= index < len(weapons) else None

    @staticmethod
    def _set_active_weapon(loc_char, weapon_index):
        character = getattr(loc_char, 'character', None)
        if not character or not isinstance(character.data, dict):
            loc_char.drawn_weapon_index = weapon_index
            return
        data = dict(character.data)
        if weapon_index is None:
            data.pop('activeWeaponIndex', None)
        else:
            data['activeWeaponIndex'] = weapon_index
        data.setdefault('health',{}).setdefault('combatMeta',{}).pop('secondaryWeaponId',None)
        character.data = data
        flag_modified(character, 'data')
        loc_char.drawn_weapon_index = weapon_index

    @staticmethod
    def _clear_aim(loc_char):
        loc_char.aimed_target_character_id = None
        loc_char.aimed_weapon_index = None
        loc_char.aim_accuracy_bonus = 0

    @staticmethod
    def _aim_bonus_for_target(loc_char, target_character_id, weapon_index):
        if (
            target_character_id is not None
            and loc_char.aimed_target_character_id == target_character_id
            and loc_char.aimed_weapon_index == weapon_index
        ):
            return max(0, CombatService._coerce_int(loc_char.aim_accuracy_bonus, 0))
        return 0

    @staticmethod
    def _skill_value(character_data, skill_path):
        current = character_data if isinstance(character_data, dict) else {}
        for part in skill_path.split('.'):
            if not isinstance(current, dict):
                return 0
            current = current.get(part)
        if not isinstance(current, dict):
            return 0
        stat_name = skill_path.split('.')[-1]
        equipment_bonus = (
            CombatService._exoskeleton_power_profile(character_data)['strength_level_bonus']
            if skill_path == 'skills.physical.strength'
            else 0
        )
        artifact = artifact_passive_profile(character_data)
        artifact_bonus = artifact['skill_physical'] if skill_path.startswith('skills.physical.') else artifact['skill_other']
        artifact_bonus += {
            'strength': artifact['skill_strength'],
            'will': artifact['skill_will'],
            'awareness': artifact['skill_awareness'],
            'stealth': artifact['skill_stealth'],
        }.get(stat_name, 0)
        return max(
            0,
            CombatService._coerce_int(current.get('base'), 0)
            + CombatService._coerce_int(current.get('bonus'), 0)
            + CombatService._consumable_stat_value_bonus(character_data, stat_name)
            + equipment_bonus
            + (2 if skill_path in {'skills.physical.strength','skills.physical.agility'} and background_is(character_data,'Пожарный') and ((character_data.get('health') or {}).get('combatMeta') or {}).get('firefighterNearby') else 0)
            + (facial_profile(character_data)['charisma_level'] if skill_path=='skills.social.charisma' else 0)
            + artifact_bonus,
        )

    @staticmethod
    def _apply_numeric_modifier(value, modifier):
        text = str(modifier if modifier is not None else '').strip()
        if not text:
            return value
        if text.startswith('='):
            return CombatService._coerce_int(text[1:], 0)
        return value + CombatService._coerce_int(text, 0)

    @staticmethod
    def _ergonomics_effects(value):
        ergonomics = max(0, CombatService._coerce_int(value, 0))
        if ergonomics <= 10:
            draw_cost, reload_modifier, aimed_modifier, accuracy_modifier = 4, 2, 2, -2
        elif ergonomics <= 20:
            draw_cost, reload_modifier, aimed_modifier, accuracy_modifier = 3, 2, 2, -1
        elif ergonomics <= 30:
            draw_cost, reload_modifier, aimed_modifier, accuracy_modifier = 3, 1, 1, -1
        elif ergonomics <= 40:
            draw_cost, reload_modifier, aimed_modifier, accuracy_modifier = 3, 1, 1, 0
        elif ergonomics <= 50:
            draw_cost, reload_modifier, aimed_modifier, accuracy_modifier = 2, 1, 0, 0
        elif ergonomics <= 70:
            draw_cost, reload_modifier, aimed_modifier, accuracy_modifier = 2, 0, 0, 0
        elif ergonomics <= 80:
            draw_cost, reload_modifier, aimed_modifier, accuracy_modifier = 1, 0, -1, 0
        elif ergonomics <= 90:
            draw_cost, reload_modifier, aimed_modifier, accuracy_modifier = 1, 0, -1, 1
        elif ergonomics <= 99:
            draw_cost, reload_modifier, aimed_modifier, accuracy_modifier = 1, -1, -1, 1
        else:
            draw_cost, reload_modifier, aimed_modifier, accuracy_modifier = 0, -2, -2, 2
        return {
            'value': ergonomics,
            'draw_action_points': draw_cost,
            'reload_action_points_modifier': reload_modifier,
            'aimed_shot_action_points_modifier': aimed_modifier,
            'aimed_shot_action_points': max(0, 4 + aimed_modifier),
            'accuracy_modifier': accuracy_modifier,
        }

    @staticmethod
    def _weapon_ergonomics_profile(loc_char, weapon, weapon_index=None):
        character = getattr(loc_char, 'character', None)
        data = character.data if character and isinstance(character.data, dict) else {}
        weapon = weapon if isinstance(weapon, dict) else {}
        template = None
        template_id = CombatService._coerce_int(weapon.get('templateId'), 0)
        if template_id:
            template = db.session.get(ItemTemplate, template_id)
        template_attributes = template.attributes if template and isinstance(template.attributes, dict) else {}

        raw_base = weapon.get('ergonomics')
        if raw_base is None:
            raw_base = (weapon.get('attributes') or {}).get('ergonomics')
        if raw_base is None:
            raw_base = template_attributes.get('ergonomics')
        base_ergonomics = CombatService._coerce_float(raw_base, 0)
        from app.services.equipment_modifications import stat as modification_stat,rule as modification_rule
        weapon_ergonomics = modification_stat(weapon,'ergonomics',base_ergonomics)
        module_modifier = 0
        for module in weapon.get('installedModules') or []:
            if not isinstance(module, dict):
                continue
            modifiers = module.get('modifiers')
            if not isinstance(modifiers, dict):
                modifiers = (module.get('attributes') or {}).get('modifiers') or {}
            before = weapon_ergonomics
            weapon_ergonomics = CombatService._apply_numeric_modifier(
                weapon_ergonomics,
                modifiers.get('ergonomics'),
            )
            module_modifier += weapon_ergonomics - before
            if (
                module.get('slotType') == 'handguard'
                and (module.get('bipod') or (module.get('attributes') or {}).get('bipod') or module.get('name') == 'Сошки')
                and (CombatService._posture_key(loc_char)=='prone' or (
                    getattr(loc_char,'weapon_braced',False) and getattr(loc_char,'braced_weapon_index',None)==weapon_index))
            ):
                bipod_bonus=50 if background_is(data,'Пост принял') else 75
                weapon_ergonomics += bipod_bonus
                module_modifier += bipod_bonus

        magazine = weapon.get('installedMagazine')
        magazine = magazine if isinstance(magazine, dict) else {}
        magazine_modifier = magazine.get('ergonomics')
        if magazine_modifier is None:
            magazine_modifier = (magazine.get('attributes') or {}).get('ergonomics')
        if magazine_modifier is None:
            magazine_template_id = CombatService._coerce_int(magazine.get('templateId'), 0)
            if magazine_template_id:
                magazine_template = db.session.get(ItemTemplate, magazine_template_id)
                magazine_attributes = (
                    magazine_template.attributes
                    if magazine_template and isinstance(magazine_template.attributes, dict)
                    else {}
                )
                magazine_modifier = magazine_attributes.get('ergonomics')
        magazine_modifier = CombatService._coerce_int(magazine_modifier, 0)

        equipment = data.get('equipment') if isinstance(data.get('equipment'), dict) else {}
        helmet = equipment.get('helmet') if isinstance(equipment.get('helmet'), dict) else {}
        helmet_penalty = helmet.get('ergonomicsPenalty')
        if helmet_penalty is None:
            helmet_penalty = helmet.get('ergonomics_penalty')
        if helmet_penalty is None:
            helmet_penalty = (helmet.get('attributes') or {}).get('ergonomics_penalty')
        helmet_penalty = max(0, CombatService._coerce_int(helmet_penalty, 0))
        gas_mask = equipment.get('gasMask') if isinstance(equipment.get('gasMask'), dict) else {}
        gas_mask_penalty = gas_mask.get('ergonomicsPenalty')
        if gas_mask_penalty is None:
            gas_mask_penalty = gas_mask.get('ergonomics_penalty')
        if gas_mask_penalty is None:
            gas_mask_penalty = (gas_mask.get('attributes') or {}).get('ergonomics_penalty')
        gas_mask_penalty = max(0, CombatService._coerce_int(gas_mask_penalty, 0))

        posture = CombatService._posture_key(loc_char)
        posture_bonus = POSTURES[posture]['ergonomics_bonus']
        if background_is(data,'Пост принял'):posture_bonus=0
        shooting_value = (
            CombatService._skill_value(data, 'skills.physical.shooting')
            + CombatService._health_roll_modifier(data, 'skills.physical.shooting')
        )
        tactics_value = (
            CombatService._skill_value(data, 'skills.other.tactics')
            + CombatService._health_roll_modifier(data, 'skills.other.tactics')
        )
        if background_is(data,'Оружейник'):tactics_value=0
        effective_value = max(
            0,
            weapon_ergonomics
            + shooting_value
            + tactics_value
            + posture_bonus
            + magazine_modifier
            - helmet_penalty
            - gas_mask_penalty,
        )
        from app.services.portable_lights import ergonomics as light_ergonomics
        portable_light_modifier=light_ergonomics(loc_char,weapon)
        effective_value=max(0,effective_value+portable_light_modifier)
        from app.services.equipment_modifications import worn_stat,worn_rule
        effective_value=max(0,effective_value+worn_stat(data,'ergonomics'))
        weapon_size=weapon.get('size')
        if weapon_size is None:weapon_size=(weapon.get('attributes') or {}).get('size',template_attributes.get('size'))
        if CombatService._coerce_float(weapon_size,0)>=6:
            effective_value+=worn_rule(data,'large_weapon_ergonomics')
        effective_value=math.floor(effective_value+.5)
        tiers=int(modification_rule(weapon,'ergonomics_tiers')+worn_rule(data,'ergonomics_tiers'))
        thresholds=[0,11,21,31,41,51,71,81,91,100]
        band=max(i for i,minimum in enumerate(thresholds) if effective_value>=minimum)
        if tiers:effective_value=max(effective_value,thresholds[min(9,band+tiers)])
        effects = CombatService._ergonomics_effects(effective_value)
        effects['aimed_shot_action_points']=trait_action_cost(data,'attack',effects['aimed_shot_action_points'],
            weapon_type=CombatService._weapon_class(weapon),aimed=True)
        return {
            'weapon_index': weapon_index,
            'base_weapon_ergonomics': base_ergonomics,
            'portable_light_modifier': portable_light_modifier,
            'module_modifier': module_modifier,
            'shooting_value': shooting_value,
            'tactics_value': tactics_value,
            'posture_bonus': posture_bonus,
            'magazine_modifier': magazine_modifier,
            'helmet_penalty': helmet_penalty,
            'gas_mask_penalty': gas_mask_penalty,
            **effects,
        }

    @staticmethod
    def _weapon_strength_profile(loc_char, weapon):
        character = getattr(loc_char, 'character', None)
        data = character.data if character and isinstance(character.data, dict) else {}
        weapon = weapon if isinstance(weapon, dict) else {}
        template = None
        template_id = CombatService._coerce_int(weapon.get('templateId'), 0)
        if template_id:
            template = db.session.get(ItemTemplate, template_id)
        template_attributes = template.attributes if template and isinstance(template.attributes, dict) else {}
        attributes = weapon.get('attributes') if isinstance(weapon.get('attributes'), dict) else {}
        required = CombatService._coerce_int(
            weapon.get(
                'minStrength',
                weapon.get(
                    'min_strength',
                    attributes.get('min_strength', template_attributes.get('min_strength')),
                ),
            ),
            0,
        )
        base_required = required
        from app.services.equipment_modifications import stat as modification_stat
        required=max(0,modification_stat(weapon,'min_strength',required))
        has_bipod = False
        for module in weapon.get('installedModules') or []:
            if not isinstance(module, dict):
                continue
            module_attributes = module.get('attributes') if isinstance(module.get('attributes'), dict) else {}
            modifiers = module.get('modifiers')
            modifiers = modifiers if isinstance(modifiers, dict) else module_attributes.get('modifiers', {})
            modifier = modifiers.get('min_strength') if isinstance(modifiers, dict) else None
            if modifier is None and isinstance(modifiers, dict):
                modifier = modifiers.get('required_strength')
            required = CombatService._apply_numeric_modifier(required, modifier)
            has_bipod = has_bipod or bool(
                module.get('slotType') == 'handguard'
                and (
                    module.get('bipod')
                    or module_attributes.get('bipod')
                    or module.get('name') == 'Сошки'
                )
            )

        posture = CombatService._posture_key(loc_char)
        from app.services.weapon_grip import hands,pair,melee,minimum_strength
        grip_hands = hands(data)
        paired=pair(loc_char)
        unmodified_grip_required=required
        if paired and all(not melee(item) for item in paired) and any(item is weapon for item in paired):
            other=paired[1] if paired[0] is weapon else paired[0]
            other_required=minimum_strength(other)
            required=max(required,other_required)+math.floor(min(required,other_required)/2)+2
        else:required+=2 if grip_hands==1 else 0
        grip_modifier=required-unmodified_grip_required
        posture_reduction = {'standing': 0, 'sitting': 2, 'prone': 6}[posture]
        braced_index=getattr(loc_char,'braced_weapon_index',None)
        carried=data.get('weapons') or []
        on_rest=bool(getattr(loc_char,'weapon_braced',False) and isinstance(braced_index,int)
                     and 0<=braced_index<len(carried) and carried[braced_index] is weapon)
        ignored_by_bipod = has_bipod and not paired and (posture == 'prone' or on_rest)
        effective_required = 0 if ignored_by_bipod else max(0, required - posture_reduction-character_passives(data)['minimum_strength_reduction'])
        strength = (
            CombatService._skill_value(data, 'skills.physical.strength')
        )
        deficit = max(0, effective_required - strength)
        return {
            'base_required': base_required,
            'module_modifier': required - base_required - grip_modifier,
            'grip_hands': grip_hands,
            'grip_modifier': grip_modifier,
            'posture_reduction': posture_reduction,
            'effective_required': effective_required,
            'strength': strength,
            'deficit': deficit,
            'accuracy_penalty': deficit * 2,
            'ignored_by_bipod': ignored_by_bipod,
        }

    @staticmethod
    def _posture_key(loc_char):
        posture = str(getattr(loc_char, 'posture', None) or 'standing').lower()
        return posture if posture in POSTURES else 'standing'

    @staticmethod
    def _posture_change_options(loc_char, target_posture):
        source = CombatService._posture_key(loc_char)
        target = str(target_posture or '').lower()
        if target not in POSTURES:
            raise ValidationError("Unknown posture")
        if source == target:
            raise ValidationError("Character is already in this posture")

        data = loc_char.character.data if loc_char.character and isinstance(loc_char.character.data, dict) else {}
        agility_bonus = CombatService._base_skill_modifier(data, 'skills.physical.agility')
        transition = frozenset((source, target))
        if transition == frozenset(('standing', 'sitting')):
            return [{'resource': 'movement', 'cost': max(0, 5 - agility_bonus)}]
        if transition == frozenset(('sitting', 'prone')):
            return [
                {'resource': 'movement', 'cost': 4},
                {'resource': 'action', 'cost': 1},
            ]
        if transition == frozenset(('standing', 'prone')):
            return [
                {'resource': 'movement', 'cost': max(0, 8 - agility_bonus)},
                {'resource': 'action', 'cost': 2},
            ]
        raise ValidationError("Unsupported posture transition")

    @staticmethod
    def _facing_change_options(loc_char, target_x, target_y):
        target_x = CombatService._coerce_int(target_x, 0)
        target_y = CombatService._coerce_int(target_y, 0)
        if (target_x, target_y) == (0, 0):
            raise ValidationError("Choose a facing direction")
        current_x = CombatService._coerce_int(getattr(loc_char, 'facing_x', 0), 0)
        current_y = CombatService._coerce_int(getattr(loc_char, 'facing_y', 1), 1)
        current_length = math.hypot(current_x, current_y)
        target_length = math.hypot(target_x, target_y)
        if not current_length:
            current_x, current_y, current_length = 0, 1, 1
        cosine = max(
            -1.0,
            min(1.0, (current_x * target_x + current_y * target_y) / (current_length * target_length)),
        )
        degrees = int(round(math.degrees(math.acos(cosine))))
        if degrees == 0:
            raise ValidationError("Character is already facing that direction")
        character_data = (
            loc_char.character.data
            if loc_char.character and isinstance(loc_char.character.data, dict)
            else {}
        )
        agility_bonus = CombatService._base_skill_modifier(
            character_data, 'skills.physical.agility',
        )
        prone = CombatService._posture_key(loc_char) == 'prone'
        if degrees <= 45:
            return {
                'from_x': current_x,
                'from_y': current_y,
                'to_x': target_x,
                'to_y': target_y,
                'degrees': degrees,
                'prone': prone,
                'agility_bonus': agility_bonus,
                'options': (
                    [
                        {'payment': 'prone_action', 'action_points': 1, 'free_actions': 0, 'movement_points': 0},
                        {'payment': 'prone_movement', 'action_points': 0, 'free_actions': 0, 'movement_points': 3},
                    ]
                    if prone else
                    [{'payment': 'free_turn', 'action_points': 0, 'free_actions': 0, 'movement_points': 0}]
                ),
            }
        movement_cost = (3 if degrees <= 90 else 5) - agility_bonus + (3 if prone else 0)
        action_extra = 1 if prone else 0
        if degrees <= 90:
            options = [
                {'payment': 'free', 'action_points': action_extra, 'free_actions': 1, 'movement_points': 0},
                {'payment': 'action', 'action_points': 1 + action_extra, 'free_actions': 0, 'movement_points': 0},
                {'payment': 'movement', 'action_points': 0, 'free_actions': 0, 'movement_points': max(0, movement_cost)},
            ]
        else:
            options = [
                {'payment': 'mixed', 'action_points': 1 + action_extra, 'free_actions': 1, 'movement_points': 0},
                {'payment': 'action', 'action_points': 2 + action_extra, 'free_actions': 0, 'movement_points': 0},
                {'payment': 'movement', 'action_points': 0, 'free_actions': 0, 'movement_points': max(0, movement_cost)},
            ]
        return {
            'from_x': current_x,
            'from_y': current_y,
            'to_x': target_x,
            'to_y': target_y,
            'degrees': degrees,
            'prone': prone,
            'agility_bonus': agility_bonus,
            'options': options,
        }

    @staticmethod
    def _movement_forward_step(facing_x, facing_y, dx, dy):
        length = math.hypot(dx, dy) * math.hypot(facing_x, facing_y)
        if length <= 1e-9:
            return None
        # Диагональ до 45° включительно оплачивается как движение вперёд.
        return (dx * facing_x + dy * facing_y) / length >= math.sqrt(.5) - 1e-9

    @staticmethod
    def _movement_direction_profile(loc_char, path):
        facing_x = CombatService._coerce_float(getattr(loc_char, 'facing_x', 0), 0)
        facing_y = CombatService._coerce_float(getattr(loc_char, 'facing_y', 1), 1)
        if math.hypot(facing_x, facing_y) <= 1e-9:
            facing_x, facing_y = 0, 1
        forward, backward = False, False
        points = path.get('path') or []
        for before, after in zip(points, points[1:]):
            dx, dy = after[0] - before[0], after[1] - before[1]
            step = CombatService._movement_forward_step(facing_x, facing_y, dx, dy)
            if step is None:
                continue
            if step:
                forward = True
            else:
                backward = True
        return forward, backward

    @staticmethod
    def _effective_movement_mode(loc_char, path, movement_mode):
        forward, backward = CombatService._movement_direction_profile(loc_char, path)
        if movement_mode == 'walk' and backward:
            if forward:
                raise ValidationError('Маршрут смешивает ходьбу вперёд и спиной/боком. Разделите его или сначала измените направление взгляда.')
            return 'backward_sideways'
        if movement_mode in {'run', 'sprint'} and backward:
            raise ValidationError('Для бега в этом направлении сначала измените направление взгляда; спиной/боком доступна ходьба.')
        return movement_mode

    @staticmethod
    def _validate_backward_sideways_path(loc_char, path):
        tiles = path.get('path') if isinstance(path, dict) else None
        if not isinstance(tiles, list):
            raise ValidationError("Movement path is missing")
        for previous, current in zip(tiles, tiles[1:]):
            if not isinstance(previous, (list, tuple)) or not isinstance(current, (list, tuple)):
                raise ValidationError("Invalid movement path")
        forward, _ = CombatService._movement_direction_profile(loc_char, path)
        if forward:
            raise ValidationError("В режиме движения спиной или боком нельзя идти вперёд")

    @staticmethod
    def _movement_posture_profile(posture,character_data=None):
        profile=dict(POSTURES.get(posture,POSTURES['standing']))
        if trait_count(character_data,'Гусь и гусеничка'):
            if posture=='sitting':profile.update(movement_multiplier=1,walk_max_distance=10,can_run=True)
            elif posture=='prone':profile.update(movement_multiplier=2,walk_max_distance=5)
        return profile

    @staticmethod
    def _sprinter_walk(character_data,movement_mode,posture):
        return (background_is(character_data,'Спринтер') and movement_mode in {'walk','run','backward_sideways'}
                and not CombatService._exoskeleton_power_profile(character_data)['is_exoskeleton'])

    @staticmethod
    def _validate_posture_movement(posture, movement_mode,character_data=None):
        posture_key = posture if posture in POSTURES else 'standing'
        posture_profile = CombatService._movement_posture_profile(posture_key,character_data)
        if movement_mode == 'run' and not posture_profile['can_run']:
            raise ValidationError("Running is only possible while standing")
        if movement_mode == 'sprint' and not posture_profile['can_sprint']:
            raise ValidationError("Sprinting is only possible while standing")
        if movement_mode == 'correction' and not posture_profile['can_correction']:
            raise ValidationError("Correction movement is unavailable while prone")
        return posture_profile

    @staticmethod
    def _validate_equipment_movement(character_data, movement_mode):
        profile = CombatService._exoskeleton_power_profile(character_data)
        if (
            movement_mode in {'run', 'sprint'}
            and profile['blocks_strenuous_movement']
            and (movement_mode!='run' or not profile['can_run'])
        ):
            raise ValidationError("Running and sprinting are unavailable in an exoskeleton")
        return profile

    @staticmethod
    def _strenuous_movement_is_blocked(character, movement_mode, current_round):
        if movement_mode not in {'run', 'sprint'}:
            return False
        if character.character and CombatService._consumable_stat_value_bonus(character.character.data, 'breathless_immunity') > 0:
            return False
        # Breathlessness starts after selecting the mode, but must not interrupt
        # further movement segments made with that mode in the same turn.
        if character.movement_mode_this_turn == movement_mode:
            return False
        blocked_until = character.strenuous_movement_blocked_until_round or 0
        return blocked_until > 0 and current_round <= blocked_until

    @staticmethod
    def _jump_profile(character_data, movement_mode='walk', available_ap=None):
        if movement_mode not in MOVEMENT_MODES:
            raise ValidationError('Unknown movement mode')
        running = movement_mode in {'run', 'sprint'}
        strength_modifier = CombatService._base_skill_modifier(character_data, 'skills.physical.strength')
        base_distance = max(0, 2 + .2 * strength_modifier)
        return {
            'base_distance': round(base_distance, 10),
            'max_distance': round(base_distance * (2 if running else 1), 10),
            'strength_modifier': strength_modifier,
            'agility_modifier': CombatService._skill_modifier(character_data, 'skills.physical.agility'),
            'difficulty': 6 if running else 10,
            'action_points': 2,
            'has_action_points': available_ap is None or available_ap >= 2,
            'running': running,
            'failure_1_to_3': 'midpoint' if running else 'unstable_edge',
            'failure_4_or_more': 'midpoint',
            'execution_available': True,
            'max_rise': 1,
            'needs_rules': [],
            'blocked_by': [],
        }

    @staticmethod
    def _movement_route_cost(path, movement_mode, posture='standing',character_data=None):
        mode = MOVEMENT_MODES.get(movement_mode)
        if not mode:
            raise ValidationError("Unknown movement mode")
        posture_profile = CombatService._movement_posture_profile(posture,character_data)
        route = path if isinstance(path, dict) else {}
        route_tiles = route.get('path') if isinstance(route.get('path'), list) else []
        distance = max(0, CombatService._coerce_float(route.get('distance'),len(route_tiles)-1))
        raw_cost = max(0, CombatService._coerce_float(route.get('cost'), distance))
        climb_cost = max(0, CombatService._coerce_float(route.get('climb_cost'), 0))
        travel_cost = max(0, raw_cost - climb_cost)
        divisor = mode['movement_divisor']
        if CombatService._sprinter_walk(character_data,movement_mode,posture):divisor=1 if movement_mode=='backward_sideways' else 2
        adjusted_travel_cost = travel_cost * posture_profile['movement_multiplier']
        movement_cost = 0 if divisor is None else adjusted_travel_cost / divisor
        return {
            'distance': distance,
            'movement_points': movement_cost + climb_cost,
            'climb_cost': climb_cost,
        }

    @staticmethod
    def _get_location(location_id):
        location = Location.query.get(location_id)
        if not location:
            raise NotFoundError("Location not found")
        return location

    @staticmethod
    def _ensure_access(location, user_id):
        if location.lobby.gm_id == user_id:
            return True
        participant = LobbyParticipant.query.filter_by(
            lobby_id=location.lobby_id,
            user_id=user_id,
        ).first()
        if not participant or participant.is_banned:
            raise PermissionDenied("Access denied")
        return False

    @staticmethod
    def _get_or_create_state(location_id):
        state = LocationCombatState.query.filter_by(location_id=location_id).first()
        if not state:
            state = LocationCombatState(location_id=location_id)
            db.session.add(state)
            db.session.flush()
        return state

    @staticmethod
    def _unique_location_characters(characters):
        unique_by_character = {}
        for character in sorted(characters, key=lambda item: item.id, reverse=True):
            if character.character_id not in unique_by_character:
                unique_by_character[character.character_id] = character
        return list(unique_by_character.values())

    @staticmethod
    def _combat_profile(loc_char):
        character = loc_char.character
        data = character.data if character and isinstance(character.data, dict) else {}
        combat_data = data.get('combat', {}) if isinstance(data, dict) else {}

        initiative_bonus = (
            CombatService._skill_modifier(data, 'skills.other.tactics')
            + CombatService._coerce_int(
                combat_data.get(
                    'initiative_bonus',
                    data.get('initiative_bonus', 0),
                ),
                0,
            )
        )
        movement_points = combat_data.get(
            'movement_points',
            combat_data.get(
                'speed',
                data.get('movement_points', data.get('speed', loc_char.movement_points_max or DEFAULT_MOVEMENT_POINTS)),
            ),
        )
        action_points = combat_data.get('action_points', data.get('action_points', DEFAULT_ACTION_POINTS))
        action_points = CombatService._coerce_int(action_points, DEFAULT_ACTION_POINTS) + CombatService._consumable_stat_bonus(data, 'action_points')
        if CombatService._disabled_limb_penalties(data)['all']:
            action_points -= max(0,2-character_passives(data)['injury_penalty_reduction'])
        free_actions = combat_data.get('free_actions', data.get('free_actions', DEFAULT_FREE_ACTIONS))
        movement_penalty = CombatService._movement_penalty(loc_char)
        movement_gain = max(0, DEFAULT_CONVERSION_BASE - movement_penalty)
        health = data.get('health') if isinstance(data.get('health'), dict) else {}
        combat_meta = health.get('combatMeta') if isinstance(health.get('combatMeta'), dict) else {}
        if (
            combat_meta.get('mutantAccelerationActive')
            and CombatService._mutant_has_trait(data, 'Возможность разгона')
        ):
            movement_gain += CombatService._mutant_acceleration_bonus(data)

        return {
            'initiative_bonus': CombatService._coerce_int(initiative_bonus, 0)+(5 if background_is(data,'Математик') else 0),
            'movement_points': max(0, CombatService._coerce_float(movement_points, movement_gain * max(1, CombatService._coerce_int(free_actions, DEFAULT_FREE_ACTIONS)))),
            'action_points': max(0, CombatService._coerce_int(action_points, DEFAULT_ACTION_POINTS)),
            'free_actions': max(0, CombatService._coerce_int(free_actions, DEFAULT_FREE_ACTIONS)),
            'movement_penalty': movement_penalty,
            'movement_gain': movement_gain,
        }

    @staticmethod
    def _movement_penalty(loc_char):
        character = loc_char.character
        data = character.data if character and isinstance(character.data, dict) else {}
        return CombatService._movement_penalty_breakdown(data,loc_char)['total']

    @staticmethod
    def _movement_penalty_breakdown(data,loc_char=None):
        data = data if isinstance(data, dict) else {}
        equipment = data.get('equipment', {}) if isinstance(data, dict) else {}
        armor = equipment.get('armor', {}) if isinstance(equipment, dict) else {}
        helmet = equipment.get('helmet', {}) if isinstance(equipment, dict) else {}
        helmet = helmet if isinstance(helmet, dict) else {}

        armor_penalty = (
            armor.get('movementPenalty')
            if isinstance(armor, dict) else None
        )
        if armor_penalty is None and isinstance(armor, dict):
            armor_penalty = armor.get('movement_penalty')
        helmet_attributes = (
            CombatService._template_attributes(helmet)
            if isinstance(helmet, dict) else {}
        )
        helmet_penalty = 0 if helmet.get('integratedWithArmor') else (
            helmet.get(
                'movementPenalty',
                helmet.get(
                    'movement_penalty',
                    helmet_attributes.get('movement_penalty', 0),
                ),
            )
            if isinstance(helmet, dict) else 0
        )

        from app.services.equipment_modifications import stat as modification_stat, rule as modification_rule
        armor_penalty=modification_stat(armor,'movement_penalty',CombatService._coerce_float(armor_penalty,0))
        helmet_penalty=modification_stat(helmet,'movement_penalty',CombatService._coerce_float(helmet_penalty,0))
        weight_details = CombatService._inventory_weight_details(data)
        weight_penalty = weight_details['penalty']
        temporary_penalty = CombatService._consumable_stat_bonus(data, 'movement_points')
        limb_penalty = CombatService._disabled_limb_penalties(data)['movement']
        health = data.get('health', {}) if isinstance(data, dict) else {}
        effects = normalize_effect_list(health.get('effects') or []) if isinstance(health, dict) else []
        suppressed_fracture_areas = {
            str(effect.get('area') or '').strip().lower().replace('_','')
            for effect in effects
            if effect.get('suppress_fracture')
            and effect.get('active', True)
            and (effect.get('remaining') is None or CombatService._coerce_float(effect.get('remaining'), 0) > 0)
        }
        fracture_penalty = 0
        for effect in effects:
            if not effect.get('active', True):
                continue
            if effect.get('remaining') is not None and CombatService._coerce_float(effect.get('remaining'), 0) <= 0:
                continue
            effect_type = str(effect.get('type') or '').strip()
            area = str(effect.get('area') or '').strip().lower().replace('_','')
            if area in suppressed_fracture_areas:
                continue
            if effect_type in {'fracture', 'fracture_fixed', 'fracture_unfixed'} and any(token in area for token in ('leg', 'foot')):
                fracture_penalty += 2 if effect_type == 'fracture_fixed' else 3
            elif effect_type == 'fracture_sequela' and any(token in area for token in ('leg', 'foot')):
                fracture_penalty += 1
        limb_penalty += fracture_penalty
        intoxication_penalty = CombatService._intoxication_profile(data)['movement_penalty']
        artifact_penalty = artifact_passive_profile(data)['movement_penalty']
        trait_penalty = character_passives(data)['movement_penalty']
        from app.services.body_transport import penalty as transport_penalty
        carrying_penalty = transport_penalty(loc_char) if loc_char is not None else 0
        drawn_index=CombatService._coerce_int(getattr(loc_char,'drawn_weapon_index',None) if loc_char is not None else data.get('activeWeaponIndex'),-1)
        weapons=data.get('weapons') or []
        if background_is(data,'Прорывник') and 0<=drawn_index<len(weapons):
            if re.search(r'пистолет.пулемет|штурмов|карабин|дробов',CombatService._weapon_subcategory(weapons[drawn_index])):
                trait_penalty-=2
        exoskeleton = CombatService._exoskeleton_power_profile(data)
        if exoskeleton['is_exoskeleton']:
            if exoskeleton['powered']:
                armor_penalty = 3 if any(entry.get('ruleId')=='armor-d28'
                    for entry in armor.get('modifications') or [] if isinstance(entry,dict)) else 5
                weight_penalty = 0
            elif (exoskeleton['battery_present'] and not armor.get('protectionDisabled')
                  and modification_rule(armor, 'unpowered_weight_penalty_suppression')):
                weight_penalty = 0
        total = (
            CombatService._coerce_int(armor_penalty, 0)
            + CombatService._coerce_int(helmet_penalty, 0)
            + weight_penalty
            + temporary_penalty
            + limb_penalty
            + intoxication_penalty
            + artifact_penalty
            + trait_penalty
            + carrying_penalty
        )
        return {
            'total': total,
            'armor': CombatService._coerce_int(armor_penalty, 0),
            'helmet': CombatService._coerce_int(helmet_penalty, 0),
            'weight': weight_penalty,
            'weight_raw': weight_details['raw_penalty'],
            'backpack_reduction': weight_details['backpack_reduction'],
            'weight_per_penalty': weight_details['weight_per_penalty'],
            'total_weight': weight_details['total_weight'],
            'temporary': temporary_penalty,
            'injuries': limb_penalty,
            'intoxication': intoxication_penalty,
            'artifacts': artifact_penalty,
            'traits': trait_penalty,
            'body_transport': carrying_penalty,
            'is_exoskeleton': exoskeleton['is_exoskeleton'],
            'powered_exoskeleton': exoskeleton['powered'],
        }

    @staticmethod
    def _exoskeleton_power_profile(character_data):
        data = character_data if isinstance(character_data, dict) else {}
        equipment = data.get('equipment') if isinstance(data.get('equipment'), dict) else {}
        armor = equipment.get('armor') if isinstance(equipment.get('armor'), dict) else {}
        attributes = CombatService._template_attributes(armor)
        armor_name = str(armor.get('name') or '').strip().lower().replace('ё', 'е')
        is_exoskeleton = armor_name == 'экзоскелет' or bool(
            armor.get('isExoskeleton') or attributes.get('is_exoskeleton')
        )
        powered = False
        battery = None
        if is_exoskeleton:
            battery = next(
                (
                    module for module in armor.get('installedModules', [])
                    if isinstance(module, dict)
                    and (
                        module.get('slotType') == 'exoskeleton_battery'
                        or (module.get('attributes') or {}).get('slot_type') == 'exoskeleton_battery'
                    )
                ),
                None,
            )
            if battery is not None:
                battery_attributes = (
                    battery.get('attributes')
                    if isinstance(battery.get('attributes'), dict)
                    else {}
                )
                powered = CombatService._coerce_float(
                    battery_attributes.get('remaining_days'),
                    0,
                ) > 0
            elif armor.get('requiresExoskeletonBattery') is False:
                powered = bool(armor.get('powered', attributes.get('powered')))
        from app.services.equipment_modifications import rule as modification_rule
        return {
            'is_exoskeleton': is_exoskeleton,
            'powered': powered,
            'battery_present': battery is not None,
            'strength_level_bonus': 8 if powered else 0,
            'blocks_strenuous_movement': is_exoskeleton,
            'can_run': bool(is_exoskeleton and not armor.get('protectionDisabled')
                and modification_rule(armor,'exoskeleton_run')),
        }

    @staticmethod
    def _item_total_weight(item,character_data=None):
        if not isinstance(item, dict):
            return 0.0
        if (item.get('attributes') or {}).get('prosthesis_id'):return 0.0
        category = item.get('category')
        quantity = max(0, CombatService._coerce_int(item.get('quantity'), 1))
        try:
            base_weight = float(item.get('weight') or 0)
            from app.services.prostheses import profile as prosthesis_profile
            prosthesis = prosthesis_profile(item)
            if prosthesis:base_weight=prosthesis['weight']
            from app.services.weapon_catalog import is_standard_nvg
            if is_standard_nvg(item):base_weight=1
            from app.services.equipment_modifications import stat as modification_stat
            base_weight=max(0,modification_stat(item,'weight',base_weight))
        except (TypeError, ValueError):
            base_weight = 0.0

        if category == 'magazine':
            ammo = item.get('ammo') if isinstance(item.get('ammo'), list) else []
            current_ammo = sum(
                max(0, CombatService._coerce_int(stack.get('quantity'), 0))
                for stack in ammo
                if isinstance(stack, dict)
            )
            weight_key = 'loadedWeight' if current_ammo > 0 else 'emptyWeight'
            try:
                base_weight = float(item.get(weight_key) or 0)
            except (TypeError, ValueError):
                base_weight = 0.0
        elif category == 'ammo':
            if quantity <= 0:
                return 0.0
            try:
                single_volume = float(item.get('volume') or 0.02)
            except (TypeError, ValueError):
                single_volume = 0.02
            return 0.1 if single_volume * quantity < 0.5 else 0.25

        if character_data and character_passives(character_data)['launcher_weight_reduction'] and CombatService._weapon_class(item)=='grenade_launcher':
            base_weight=max(0,base_weight-character_passives(character_data)['launcher_weight_reduction'])
        total = base_weight * max(1, quantity)
        for key in ('contents', 'installedModules'):
            nested = item.get(key)
            if isinstance(nested, list):
                total += sum(CombatService._item_total_weight(child,character_data) for child in nested)
        return total

    @staticmethod
    def _inventory_movement_penalty(character_data):
        return CombatService._inventory_weight_details(character_data)['penalty']

    @staticmethod
    def _inventory_weight_details(character_data):
        if not isinstance(character_data, dict):
            return {
                'penalty': 0,
                'raw_penalty': 0,
                'backpack_reduction': 0,
                'weight_per_penalty': 5.0,
                'total_weight': 0.0,
                'effective_strength': 10,
                'strength_bonus': 0,
            }
        inventory = character_data.get('inventory')
        inventory = inventory if isinstance(inventory, dict) else {}
        equipment = character_data.get('equipment')
        equipment = equipment if isinstance(equipment, dict) else {}

        carried_items = []
        carried_items.extend(entry['item'] for entry in ((character_data.get('crafting') or {}).get('current') or {}).get('escrow',[]))
        for key in ('backpack', 'pockets'):
            values = inventory.get(key)
            if isinstance(values, list):
                carried_items.extend(values)
        for group in ('belt', 'vest'):
            container = equipment.get(group)
            pouches = container.get('pouches') if isinstance(container, dict) else []
            for pouch in pouches if isinstance(pouches, list) else []:
                if isinstance(pouch, dict) and isinstance(pouch.get('contents'), list):
                    carried_items.extend(pouch['contents'])
        weapons = character_data.get('weapons')
        if isinstance(weapons, list):
            carried_items.extend(weapons)

        from app.services.prostheses import installed
        carried_items.extend(item for parts in installed(character_data).values() for item in parts.values())

        total_weight = sum(CombatService._item_total_weight(item,character_data) for item in carried_items)
        from app.services.equipment_modifications import rule as modification_rule
        belt=equipment.get('belt') or {}
        if isinstance(belt,dict) and not belt.get('protectionDisabled'):
            belt_weight=sum(CombatService._item_total_weight(item,character_data) for pouch in belt.get('pouches') or [] if isinstance(pouch,dict) for item in pouch.get('contents') or [])
            total_weight-=belt_weight*min(1,max(0,modification_rule(belt,'belt_weight_reduction')))
        strength = ((character_data.get('skills') or {}).get('physical') or {}).get('strength')
        effective_strength = (
            CombatService._skill_value(character_data, 'skills.physical.strength')
            if isinstance(strength, dict)
            else 10
        )
        strength_capacity_modifier = math.floor((effective_strength - 10) / 2)
        weight_per_penalty = max(
            0.5,
            5 * (1 + strength_capacity_modifier * 0.1),
        )
        backpack_reduction = 0
        backpack = equipment.get('backpack')
        if isinstance(backpack, dict):
            attributes = backpack.get('attributes')
            attributes = attributes if isinstance(attributes, dict) else {}
            backpack_reduction = max(
                0,
                CombatService._coerce_int(attributes.get('weight_reduction'), 0),
            )
        raw_penalty = math.floor(total_weight / weight_per_penalty)
        from app.services.equipment_modifications import worn_stat
        temporary_modifier=CombatService._consumable_stat_value_bonus(character_data,'weight')+worn_stat(character_data,'weight_penalty')
        return {
            'penalty': max(0, math.floor(raw_penalty*character_passives(character_data)['weight_multiplier']) - backpack_reduction+temporary_modifier),
            'temporary_modifier':temporary_modifier,
            'raw_penalty': raw_penalty,
            'backpack_reduction': backpack_reduction,
            'weight_per_penalty': weight_per_penalty,
            'total_weight': total_weight,
            'effective_strength': effective_strength,
            'strength_bonus': strength_capacity_modifier,
        }

    @staticmethod
    def _health_roll_modifier(
        character_data,
        skill_path,
        include_pain=True,
        include_blood=True,
        include_psy=True,
    ):
        health = character_data.get('health') if isinstance(character_data, dict) else {}
        if not isinstance(health, dict):
            return 0
        modifier = 0
        if skill_path in {'skills.physical.shooting','skills.physical.melee','skills.physical.agility'}:
            modifier+=facial_profile(character_data).get(skill_path.rsplit('.',1)[-1],0)
        if include_pain:
            properties=character_passives(character_data)
            modifier -= max(0,CombatService._coerce_int(health.get('painLevel'), 0)-properties['pain_ignored']-properties['pain_penalty_reduction'])
        exhaustion = CombatService._coerce_int(health.get('exhaustion'), 0)
        modifier -= {1: 1, 2: 2, 3: 4}.get(exhaustion, 6 if exhaustion >= 4 else 0)
        if include_blood:
            blood_stage = str(
                health.get('blood') or health.get('bloodStage') or 'normal'
            ).lower()
            modifier -= {
                'light': 2,
                'medium': 3,
                'severe': 5,
            }.get(blood_stage, 0)
        limb_penalties = CombatService._disabled_limb_penalties(character_data)
        modifier -= limb_penalties['all']
        if skill_path == 'skills.physical.shooting':
            modifier -= limb_penalties['shooting']
        elif skill_path == 'skills.physical.melee':
            modifier -= limb_penalties['melee']
        elif skill_path == 'skills.physical.agility':
            modifier -= limb_penalties['agility']
        elif skill_path.rsplit('.',1)[-1] in {'medicine','engineering','strength','lockpicking','climbing','search'}:
            from app.services.prostheses import limb
            modifier+=sum((limb(character_data,zone) or {}).get('efficiency',0) for zone in ('leftArm','rightArm'))

        temperature = CombatService._coerce_float(health.get('temperature'), 36.0)
        if 30 <= temperature <= 33:
            modifier -= 7
        elif 38 <= temperature <= 39:
            modifier -= 3
        elif 40 <= temperature < 41:
            modifier -= 7

        intoxication = CombatService._intoxication_profile(character_data)
        if skill_path == 'skills.physical.accuracy' or skill_path == 'skills.physical.shooting':
            modifier += intoxication['accuracy_modifier']
        elif skill_path == 'skills.physical.agility':
            modifier += intoxication['agility_modifier']
        elif skill_path == 'skills.social.charisma':
            modifier += intoxication['charisma_modifier']
            level=CombatService._coerce_float(health.get('intoxication'),0)
            if background_is(character_data,'Прогульщик') and level<75:modifier+=math.floor(level/10)
        if skill_path in {'skills.physical.shooting','skills.physical.accuracy'} and background_is(character_data,'Дядя Толик'):modifier-=2
        if skill_path=='skills.physical.agility' and background_is(character_data,'Трюкач'):modifier-=CombatService._coerce_int(health.get('stress'),0)//3

        for zone in (health.get('zones') or {}).values():
            if not isinstance(zone, dict):
                continue
            penalties = zone.get('penalties') if isinstance(zone.get('penalties'), dict) else {}
            modifier -= CombatService._coerce_int(zone.get('rollPenalty', zone.get('roll_penalty')), 0)
            modifier -= CombatService._coerce_int(zone.get('skillPenalty'), 0)
            modifier -= CombatService._coerce_int(penalties.get('all', penalties.get('roll')), 0)
            modifier -= CombatService._coerce_int(penalties.get(skill_path), 0)
            modifier -= CombatService._coerce_int(
                penalties.get('physical' if skill_path.startswith('skills.physical.') else 'other'), 0
            )

        active_effects = normalize_effect_list(health.get('effects') or [])
        if skill_path in {'skills.physical.shooting','skills.physical.accuracy'} and any(e['type']=='burning' and e.get('active',True) and (e.get('remaining') or 0)>0 for e in active_effects):
            modifier-=3
        suppressed_fracture_areas = {
            str(effect.get('area') or '').strip().lower().replace('_','')
            for effect in active_effects
            if effect.get('suppress_fracture')
            and effect.get('active', True)
            and (effect.get('remaining') is None or CombatService._coerce_float(effect.get('remaining'), 0) > 0)
        }
        for effect in active_effects:
            if not effect.get('active', True):
                continue
            if effect.get('remaining') is not None and CombatService._coerce_float(effect.get('remaining'), 0) <= 0:
                continue
            modifiers = effect.get('modifiers') if isinstance(effect.get('modifiers'), dict) else {}
            effect_penalty=CombatService._coerce_float(effect.get('rollPenalty',effect.get('roll_penalty',effect.get('skillPenalty',0))),0)
            if effect.get('type')=='concussion':
                from app.services.equipment_modifications import worn_rule
                effect_penalty=max(0,effect_penalty-worn_rule(character_data,'concussion_reduction'))
            modifier-=effect_penalty
            modifier -= CombatService._coerce_int(modifiers.get('all'), 0)
            modifier -= CombatService._coerce_int(modifiers.get(skill_path), 0)
            modifier -= CombatService._coerce_int(
                modifiers.get('physical' if skill_path.startswith('skills.physical.') else 'other'), 0
            )
            if effect.get('type') == 'stimulant_crash':
                modifier -= CombatService._coerce_int(effect.get('phase_penalty', effect.get('value', 0)), 0)
            if skill_path.startswith('skills.physical.'):
                effect_type = str(effect.get('type') or '').strip()
                area = str(effect.get('area') or '').strip().lower().replace('_','')
                if area in suppressed_fracture_areas:
                    continue
                if effect_type in {'fracture', 'fracture_fixed', 'fracture_unfixed'} and any(token in area for token in ('arm', 'hand')):
                    modifier -= 1 if effect_type == 'fracture_fixed' else 2
                elif effect_type == 'fracture_sequela' and any(token in area for token in ('arm', 'hand')):
                    modifier -= 1

        if include_psy and skill_path == 'skills.physical.will':
            psy_state = CombatService._coerce_int(health.get('psyState', health.get('psy_state')), 0)
            modifier -= 1 if psy_state >= 10 else 0
            combat_meta = health.get('combatMeta') if isinstance(health.get('combatMeta'), dict) else {}
            modifier -= max(
                0,
                CombatService._coerce_int(
                    combat_meta.get('uglyAppearanceWillPenalty'), 0,
                ),
            )
        if skill_path == 'skills.other.stealth':
            combat_meta = health.get('combatMeta') if isinstance(health.get('combatMeta'), dict) else {}
            if combat_meta.get('mutantAmbushActive'):
                modifier += 5
        combat_meta = health.get('combatMeta') if isinstance(health.get('combatMeta'), dict) else {}
        modifier += CombatService._coerce_int(combat_meta.get('mutantPackRollBonus'), 0)
        return modifier

    @staticmethod
    def _zone_current_health(character_data, zone):
        health = character_data.get('health') if isinstance(character_data, dict) else {}
        zones = health.get('zones') if isinstance(health, dict) else {}
        if not isinstance(zones, dict):
            return None
        value = zones.get(zone)
        if not isinstance(value, dict):
            return None
        return CombatService._coerce_float(value.get('current'), None)

    @staticmethod
    def _disabled_limb_penalties(character_data):
        from app.services.prostheses import limb
        left_arm = CombatService._zone_current_health(character_data, 'leftArm')
        right_arm = CombatService._zone_current_health(character_data, 'rightArm')
        left_leg = CombatService._zone_current_health(character_data, 'leftLeg')
        right_leg = CombatService._zone_current_health(character_data, 'rightLeg')
        abdomen = CombatService._zone_current_health(character_data, 'abdomen')
        health = character_data.get('health') if isinstance(character_data, dict) else {}
        effects = normalize_effect_list(health.get('effects') or []) if isinstance(health, dict) else []
        catastrophic_by_area = {
            str(effect.get('area') or ''): str(effect.get('type') or '')
            for effect in effects
            if effect.get('active', True) and effect.get('type') in {'mangled_limb', 'amputation'}
        }
        arm_values = {'leftArm': left_arm, 'rightArm': right_arm}
        leg_values = {'leftLeg': left_leg, 'rightLeg': right_leg}
        disabled_arms = sum(
            (value is not None and value <= 0 or area in catastrophic_by_area) and not limb(character_data,area)
            for area, value in arm_values.items()
        )
        leg_penalties = []
        for area, value in leg_values.items():
            prosthesis=limb(character_data,area)
            injury_type = catastrophic_by_area.get(area)
            if prosthesis:
                leg_penalties.append(-prosthesis['efficiency'])
            elif injury_type == 'amputation':
                leg_penalties.append(6)
            elif injury_type == 'mangled_limb':
                leg_penalties.append(5)
            elif value is not None and value <= 0:
                leg_penalties.append(3)
            else:
                leg_penalties.append(0)
        disabled_legs = sum((area in catastrophic_by_area or value is not None and value<=0) and not limb(character_data,area) for area,value in leg_values.items())
        reduction=character_passives(character_data)['injury_penalty_reduction']
        leg_penalties=[value if limb(character_data,area) else max(0,value-reduction) for area,value in zip(leg_values,leg_penalties)]
        arm_prosthesis_penalty=-sum((limb(character_data,zone) or {}).get('efficiency',0) for zone in arm_values)
        return {
            'all': max(0,3-reduction) if abdomen is not None and abdomen <= 0 else 0,
            'shooting': max(0,3-reduction) * disabled_arms+arm_prosthesis_penalty,
            'melee': max(0,3-reduction) * disabled_arms+arm_prosthesis_penalty,
            'agility': sum(leg_penalties),
            'movement': sum(leg_penalties),
            'sprint_blocked': disabled_legs > 0,
            'unusable_arms': disabled_arms,
        }

    @staticmethod
    def _has_roll_disadvantage(character_data, skill_path, darkness=False):
        health = character_data.get('health') if isinstance(character_data, dict) else {}
        if not isinstance(health, dict):
            health = {}
        psy_state = CombatService._coerce_int(health.get('psyState', health.get('psy_state')), 0)
        threshold_disadvantage = (
            skill_path == 'skills.physical.shooting' and psy_state >= 30
        ) or (
            skill_path == 'skills.physical.will' and psy_state >= 40
        )
        if threshold_disadvantage:
            return True
        pain = CombatService._coerce_int(health.get('painLevel'), 0)
        wounded = CombatService._coerce_float(health.get('current'), health.get('max', 700)) < CombatService._coerce_float(health.get('max'), 700)
        for effect in normalize_effect_list(health.get('effects') or []):
            disadvantage_skills = effect.get('disadvantage_skills') or []
            if (
                effect.get('active', True)
                and ('*' in disadvantage_skills or skill_path in disadvantage_skills)
            ):
                return True
            if not effect.get('active', True) or effect.get('type') != 'phobia':
                continue
            phobia = f"{effect.get('phobia', '')} {effect.get('name', '')}".lower()
            if ('боль' in phobia or 'pain' in phobia) and pain > 0:
                return True
            if ('стрел' in phobia or 'shoot' in phobia) and skill_path == 'skills.physical.shooting':
                return True
            if ('люд' in phobia or 'people' in phobia) and skill_path.startswith('skills.social.'):
                return True
            if ('кров' in phobia or 'blood' in phobia) and wounded and 'medicine' in skill_path:
                return True
        return False

    @staticmethod
    def _deafness_level(character_data):
        health = character_data.get('health') if isinstance(character_data, dict) else {}
        if not isinstance(health, dict):
            return 0
        values = [CombatService._coerce_float(health.get('deafness'), 0)]
        psy_state = max(
            0,
            CombatService._coerce_float(
                health.get('psyState', health.get('psy_state')), 0,
            ),
        )
        if psy_state >= 40:
            values.append(100)
        elif psy_state >= 20:
            values.append(50)
        values.extend(
            CombatService._coerce_float(effect.get('value'), 0)
            for effect in normalize_effect_list(health.get('effects') or [])
            if effect.get('active', True)
            and str(effect.get('type') or '').casefold() == 'deafness'
        )
        return max(values, default=0)

    @staticmethod
    def _has_roll_advantage(character_data, skill_path, consume=False):
        health = character_data.get('health') if isinstance(character_data, dict) else {}
        combat_meta = health.get('combatMeta') if isinstance(health, dict) and isinstance(health.get('combatMeta'), dict) else {}
        if (
            skill_path == 'skills.physical.will'
            and combat_meta.get('mutantPackWillAdvantage')
        ):
            return True
        if (
            skill_path in {
                'skills.physical.shooting',
                'skills.physical.melee',
            }
            and combat_meta.get('mutantOpeningAttackAdvantage')
        ):
            return True
        effects = normalize_effect_list((health or {}).get('effects') or [])
        for effect in effects:
            if not effect.get('active', True) or not effect.get('gmApproved', False):
                continue
            skills = effect.get('advantage_skills') or []
            if '*' in skills or skill_path in skills:
                if consume and effect.get('consume_on_check'):
                    effect['active'] = False
                    health['effects'] = effects
                return True
        return False

    @staticmethod
    def _consume_stress_check_modifier(character_data, *, is_attack=False):
        health = character_data.get('health') if isinstance(character_data, dict) else {}
        effects = normalize_effect_list((health or {}).get('effects') or [])
        for effect in effects:
            if not effect.get('active', True) or not effect.get('gmApproved', False):
                continue
            if 'next_check_modifier' not in effect:
                continue
            value = CombatService._coerce_int(
                effect.get('attack_modifier') if is_attack else effect.get('next_check_modifier'),
                0,
            )
            if effect.get('consume_on_check'):
                effect['active'] = False
                health['effects'] = effects
            return value
        return 0

    @staticmethod
    def _apply_stress_check_consequences(character_data, skill_path, success):
        if not success:
            return
        health = character_data.get('health') if isinstance(character_data, dict) else {}
        if skill_path not in {'skills.physical.strength', 'skills.physical.agility'}:
            return
        for effect in normalize_effect_list((health or {}).get('effects') or []):
            if (
                effect.get('active', True)
                and effect.get('gmApproved', False)
                and effect.get('stress_table') == 'tension'
                and CombatService._coerce_int(effect.get('stress_roll'), 0) == 10
            ):
                apply_effect_to_health(
                    health, {'type': 'exhaustion', 'value': 1, 'source': 'stress_heroism'},
                )
                break

    @staticmethod
    def apply_stress_trigger(
        loc_char, amount=1, trigger='stress_increase', force_manifest=False,
        check_manifestation=True,
    ):
        """Raise stress and resolve its manifestation table when the rules require it."""
        data = loc_char.character.data if loc_char and loc_char.character else {}
        health = data.setdefault('health', {})
        if CombatService._is_mutant_character(data):
            # Mutants do not use the human stress system. Clean up stress that
            # may have been added to an older mutant before this rule existed.
            health['stress'] = 0
            health['effects'] = [
                effect for effect in normalize_effect_list(health.get('effects') or [])
                if effect.get('type') not in {
                    'stress', 'stress_effect', 'stress_stupor', 'phobia',
                }
            ]
            sync_health_derived_statuses(health)
            loc_char.character.data = data
            flag_modified(loc_char.character, 'data')
            return {
                'before': 0,
                'after': 0,
                'trigger': trigger,
                'blocked': True,
                'immune': True,
            }
        before = max(0, CombatService._coerce_int(health.get('stress'), 0))
        amount = CombatService._coerce_int(amount, 0)
        if amount>0 and trigger!='combat_start_panicker':amount*=character_passives(data)['stress_multiplier']
        stress_unavailable = health_blocks_new_stress(health, include_medicine=amount > 0)
        if stress_unavailable and amount >= 0:
            return {
                'before': before, 'after': before, 'trigger': trigger,
                'blocked': True, 'manifested': False,
                'attack_stress_block_chance': 0, 'attack_stress_block_roll': None,
            }
        meta = health.setdefault('combatMeta', {})
        blocked = amount > 0 and CombatService._coerce_int(meta.get('stressBlockTurns'), 0) > 0
        if amount>0 and trait_count(data,'Работник морга') and trigger in {'ally_death','ally_critical','frightening_place','disgusting_place'}:blocked=True
        if amount>0 and background_is(data,'Страшный человек') and trigger=='unsafe_place':blocked=True
        if amount>0 and background_is(data,'Стойкий') and trigger in {'direct_attack','indirect_damage','explosion'} and getattr(loc_char,'location_id',None):
            combat=LocationCombatState.query.filter_by(location_id=loc_char.location_id).first()
            if combat and combat.status=='active':blocked=True
        active_effects = [
            effect for effect in normalize_effect_list(health.get('effects') or [])
            if effect.get('active', True)
            and (
                effect.get('remaining') is None
                or CombatService._coerce_float(effect.get('remaining'), 0) > 0
            )
        ]
        attack_stress_block_roll = None
        attack_stress_block_chance = 0
        if amount > 0 and trigger in CombatService.ATTACK_STRESS_TRIGGERS and not blocked:
            attack_stress_block_chance = max((
                max(0, min(100, CombatService._coerce_int(
                    effect.get('stress_attack_block_chance'), 0,
                )))
                for effect in active_effects
            ), default=0)
            if attack_stress_block_chance:
                attack_stress_block_roll = random.randint(1, 100)
                blocked = attack_stress_block_roll <= attack_stress_block_chance
        if amount and not blocked:
            apply_effect_to_health(health, {'type': 'stress', 'value': amount, 'source': trigger})
        level = max(0, CombatService._coerce_int(health.get('stress'), 0))
        loc_char.character.data = data
        flag_modified(loc_char.character, 'data')
        result = {
            'before': before,
            'after': level,
            'trigger': trigger,
            'blocked': blocked,
            'attack_stress_block_chance': attack_stress_block_chance,
            'attack_stress_block_roll': attack_stress_block_roll,
        }
        if level < before:
            effects = normalize_effect_list(health.get('effects') or [])
            for existing in effects:
                if existing.get('expires_on_stress_decrease'):
                    existing['active'] = False
                if level == 0 and existing.get('expires_on_stress_zero'):
                    existing['active'] = False
            health['effects'] = effects
            sync_health_derived_statuses(health)
            loc_char.character.data = data
            flag_modified(loc_char.character, 'data')
        if stress_unavailable or not check_manifestation or (not force_manifest and (amount <= 0 or blocked)):
            return result
        will_bonus = CombatService._skill_modifier(data, 'skills.physical.will')
        difficulty = 5 + level * 2
        rolls = [random.randint(1, 20)]
        stress_advantage = any(
            effect.get('stress_advantage') and effect.get('active', True)
            for effect in active_effects
        )
        if stress_advantage:
            rolls.append(random.randint(1, 20))
        from app.services.brigadier_order import boost_roll
        rolls,roll,order_bonus=boost_roll(data,rolls,advantage=stress_advantage)
        will_bonus+=order_bonus
        manifested = bool(force_manifest or roll + will_bonus < difficulty)
        result.update({
            'will_bonus': will_bonus, 'difficulty': difficulty, 'rolls': rolls,
            'roll': roll, 'total': roll + will_bonus, 'manifested': manifested,
        })
        if not manifested or level <= 0:
            return result
        table = 'concern' if level <= 3 else 'stress' if level <= 6 else 'tension' if level <= 9 else 'psychosis'
        sides = {'concern': 6, 'stress': 10, 'tension': 12, 'psychosis': 8}[table]
        effect_roll = random.randint(1, sides)
        labels = {
            'concern': ['Боязливость', 'Забота', 'Индивидуальность', 'Обдумывание', 'Несдержанность', 'Пугливость'],
            'stress': ['Ступор', 'Упреждение', 'Конфликт', 'Трусость', 'Утешение', 'К черту безопасность!', 'Приспособленность', 'Стойкость', 'Концентрация', 'Преимущество'],
            'tension': ['Сдача', 'Уничтожать', 'По накатанной', 'Зажим', 'Буду биться', 'Или со мной или подо мной', 'Неуверенность', 'Эффективность', 'Ненадежность', 'Героизм', 'Истошный крик', 'Адреналин'],
            'psychosis': ['Так нельзя', 'Отказ систем', 'Полный ступор', 'Псих', 'Увечье', 'Фобия', 'Спартанец', 'Моральный сброс'],
        }
        requirements = {
            'concern': [
                'Получите ещё 1 уровень стресса без новой проверки проявления.',
                'Предложите перемирие либо помощь ближайшему союзнику.',
                'Отыграй характерную для персонажа безвредную реакцию.',
                'Займите укрытие и обдумайте ситуацию; следующая проверка Тактики с преимуществом.',
                'Привлеките внимание или предложите перемирие; следующая проверка Убеждения с преимуществом.',
                'Следующая проверка получает +3, но атака или бросок гранаты получает -3.',
            ],
            'stress': [
                'Не совершайте действий и реакций 30 секунд. Получение урона прерывает ступор.',
                'Проявите агрессию, перехватите инициативу или начните бой.',
                'Ответьте враждебно и совершите подходящую проверку Запугивания.',
                'Потратьте всё доступное перемещение на отступление либо уклонение.',
                'Утешьте ближайшего союзника за 3 ОД; при успехе снизьте свой стресс на 1.',
                'Совершите важное опасное действие с преимуществом.',
                'Используйте подходящий препарат; негативный эффект заменяется 1 уровнем истощения.',
                'Снизьте боль на 1к4 и получите стресс, равный половине снятой боли.',
                'Потратьте 2 ОД на осмотр; следующая проверка Внимательности с преимуществом.',
                'Совершите одну подходящую проверку навыка с преимуществом.',
            ],
            'tension': [
                'Сдайтесь противнику.',
                'Сделайте два беглых выстрела по ближайшим разрушаемой и живой целям.',
                'Получите ещё 1 уровень стресса без новой проверки проявления.',
                'Выстрелите очередью или трижды бегло по источнику стресса.',
                'Сблизьтесь с источником стресса и атакуйте его в ближнем бою.',
                'Атакуйте тех, кто отказывается подчиниться, пока стресс не снизится.',
                'Штраф 2 ко всем броскам, пока стресс не снизится.',
                'Продолжайте текущую цель, не отвлекаясь на безопасность.',
                'Потратьте всё перемещение, чтобы сменить укрытие.',
                'Проверки Силы и Ловкости с преимуществом; успех даёт 1 истощение.',
                'Закричите; следующая проверка Запугивания с преимуществом.',
                'Игнорируйте новую боль до снижения стресса, конца боя или одной минуты.',
            ],
            'psychosis': [
                'Попытайтесь причинить себе смертельный вред; иначе впадите в полный ступор.',
                'Атакуйте всех вокруг до снижения стресса или потери сознания.',
                'Лягте и впадите в полный ступор до снижения стресса.',
                'Отказывайтесь от еды, сна, лечения и путешествия, пока стресс не станет равен 0.',
                'Наносите себе 50 урона в раунд до снижения стресса.',
                'Получите постоянную фобию, связанную с причиной стресса.',
                'Слепо следуйте текущей цели.',
                'Снизьте стресс на 1.',
            ],
        }
        label = labels[table][effect_roll - 1]
        effect = {
            'id': uuid.uuid4().hex,
            'type': 'stress_effect', 'name': label, 'source': 'stress_manifestation',
            'stress_table': table, 'stress_roll': effect_roll, 'stress_level': level,
            'trigger': trigger, 'active': True, 'tick': 'manual',
            'expires_on_stress_decrease': table in {'tension', 'psychosis'},
            'requirement': requirements[table][effect_roll - 1],
            'gmPending': True,
        }
        effects = normalize_effect_list(health.get('effects') or [])
        pending = [
            item for item in effects
            if item.get('source') == 'stress_manifestation'
            and item.get('gmPending') and item.get('active', True)
        ]
        strength = {'concern': 0, 'stress': 1, 'tension': 2, 'psychosis': 3}
        strongest = max(pending, key=lambda item: strength.get(item.get('stress_table'), -1), default=None)
        if strongest and strength.get(strongest.get('stress_table'), -1) > strength[table]:
            result.update({
                'table': strongest.get('stress_table'),
                'effect_roll': strongest.get('stress_roll'),
                'effect': strongest.get('name'),
            })
            return result
        # Only the strongest unresolved manifestation from a damage sequence awaits the GM.
        health['effects'] = [item for item in effects if item not in pending]
        health['effects'].append(effect)
        sync_health_derived_statuses(health)
        loc_char.character.data = data
        flag_modified(loc_char.character, 'data')
        result.update({'table': table, 'sides': sides, 'effect_roll': effect_roll, 'effect': label})
        return result

    @staticmethod
    def resolve_stress_effect(
        loc_char,
        effect_id,
        action,
        replacement=None,
        effect_name=None,
        stress_table=None,
        stress_roll=None,
    ):
        data = loc_char.character.data if loc_char and loc_char.character else {}
        health = data.setdefault('health', {})
        effects = normalize_effect_list(health.get('effects') or [])
        effect = next((item for item in effects if str(item.get('id')) == str(effect_id)), None)
        if not effect and str(effect_id or '').lower() in {'', 'none', 'null', 'undefined'}:
            candidates = [
                item for item in effects
                if item.get('source') == 'stress_manifestation' and item.get('gmPending')
            ]
            if stress_table:
                candidates = [item for item in candidates if item.get('stress_table') == stress_table]
            if stress_roll not in (None, ''):
                expected_roll = CombatService._coerce_int(stress_roll, 0)
                candidates = [
                    item for item in candidates
                    if CombatService._coerce_int(item.get('stress_roll'), 0) == expected_roll
                ]
            if effect_name:
                candidates = [item for item in candidates if item.get('name') == effect_name]
            if len(candidates) == 1:
                effect = candidates[0]
        if not effect or effect.get('source') != 'stress_manifestation':
            raise NotFoundError('Stress effect not found')
        if not effect.get('gmPending'):
            raise ValidationError('Stress effect has already been resolved')
        if action in {'approve', 'replace'} and health_blocks_new_stress(health, include_medicine=False):
            raise ValidationError('Нельзя применить проявление стресса к мёртвому или бессознательному персонажу')
        if action == 'skip':
            effect.update({'active': False, 'gmPending': False, 'gmSkipped': True})
        elif action == 'replace':
            replacement = ' '.join(str(replacement or '').split())[:160]
            if not replacement:
                raise ValidationError('Replacement is required')
            effect.update({
                'name': 'Требование стресса', 'requirement': replacement,
                'gmPending': False, 'gmApproved': True, 'gmReplaced': True,
                'type': 'stress_effect',
            })
        elif action == 'approve':
            effect.update({'gmPending': False, 'gmApproved': True})
            table = effect.get('stress_table')
            roll = CombatService._coerce_int(effect.get('stress_roll'), 0)
            if (table, roll) in {('stress', 1), ('psychosis', 3)}:
                effect['type'] = 'stress_stupor'
                effect['remaining_seconds'] = 30 if table == 'stress' else None
                loc_char.posture = 'prone'
            elif (table, roll) == ('tension', 7):
                effect['rollPenalty'] = 2
            elif (table, roll) == ('tension', 10):
                effect['advantage_skills'] = ['skills.physical.strength', 'skills.physical.agility']
            elif (table, roll) == ('tension', 12):
                effect.update({'blocks_new_pain': True, 'remaining_seconds': 60})
            elif (table, roll) in {('concern', 1), ('tension', 3)}:
                CombatService.apply_stress_trigger(
                    loc_char, 1, trigger='stress_manifestation', check_manifestation=False,
                )
            elif (table, roll) == ('stress', 8):
                pain = random.randint(1, 4)
                apply_effect_to_health(health, {'type': 'pain', 'value': -pain, 'source': 'stress_manifestation'})
                CombatService.apply_stress_trigger(
                    loc_char, (pain + 1) // 2, trigger='stress_manifestation',
                    check_manifestation=False,
                )
            elif (table, roll) == ('concern', 4):
                effect.update({'advantage_skills': ['skills.other.tactics'], 'consume_on_check': True})
            elif (table, roll) == ('concern', 5):
                effect.update({'advantage_skills': ['skills.social.persuasion'], 'consume_on_check': True})
            elif (table, roll) == ('stress', 9):
                effect.update({'advantage_skills': ['skills.physical.awareness'], 'consume_on_check': True})
            elif (table, roll) == ('stress', 10):
                effect.update({'advantage_skills': ['*'], 'consume_on_check': True})
            elif (table, roll) == ('concern', 6):
                effect.update({
                    'next_check_modifier': 3,
                    'attack_modifier': -3,
                    'consume_on_check': True,
                })
            elif (table, roll) == ('psychosis', 6):
                effect.update({
                    'type': 'phobia', 'phobia': str(effect.get('trigger') or 'событие'),
                    'expires_on_stress_zero': False,
                })
            elif (table, roll) == ('psychosis', 8):
                CombatService.apply_stress_trigger(
                    loc_char, -1, trigger='stress_manifestation', check_manifestation=False,
                )
                effect['active'] = False
        else:
            raise ValidationError('Unknown resolution')
        health['effects'] = effects
        sync_health_derived_statuses(health)
        loc_char.character.data = data
        flag_modified(loc_char.character, 'data')
        return effect

    # Single entry point for combat, GM events, anomalies and social triggers.
    trigger_stress = apply_stress_trigger

    @staticmethod
    def _is_dark_at_character(location, character, *, objects=None):
        return CombatService._is_dark_at_position(location, character.pos_x, character.pos_y,objects=objects)

    @staticmethod
    def _is_dark_at_position(location, x, y, *, objects=None):
        return location_is_dark(
            location.lighting, x, y,
            location.lobby.game_time_minutes,
            objects if objects is not None else LocationObject.query.filter_by(location_id=location.id).all(),
        )

    @staticmethod
    def _narrative_skill_check(character_data, skill_path, advantage=False, darkness=False,roll_bonus=0,forced_disadvantage=False,charisma_level_delta=0,weather=None):
        if skill_path not in CombatService.NARRATIVE_SKILLS:
            raise ValidationError("Unknown skill")
        from app.services.brigadier_order import consume
        order=consume(character_data)
        if order:
            roll_bonus+=3;advantage=advantage or order.get('advantage',False)
        requested_skill_path = skill_path
        awareness_mode = None
        if skill_path == 'skills.physical.awareness.visual':
            awareness_mode = 'visual'
            skill_path = 'skills.physical.awareness'
        elif skill_path == 'skills.physical.awareness.hearing':
            awareness_mode = 'hearing'
            skill_path = 'skills.physical.awareness'
        effective_value = CombatService._skill_value(character_data, skill_path)
        skill_modifier = CombatService._base_skill_modifier(character_data, skill_path)
        status_modifier = CombatService._health_roll_modifier(character_data, skill_path)
        related_modifier = 0
        if skill_path.startswith('skills.social.') and skill_path != 'skills.social.charisma':
            related_modifier = CombatService._base_skill_modifier(
                character_data, 'skills.social.charisma'
            )
        if charisma_level_delta and skill_path.startswith('skills.social.'):
            charisma=max(0,CombatService._skill_value(character_data,'skills.social.charisma')+charisma_level_delta)
            charisma_modifier=math.floor((charisma-10)/2)+facial_profile(character_data)['charisma_bonus']
            if skill_path=='skills.social.charisma':effective_value=charisma;skill_modifier=charisma_modifier
            else:related_modifier=charisma_modifier
        equipment_modifier = 0
        equipment = character_data.get('equipment') if isinstance(character_data, dict) else {}
        equipment = equipment if isinstance(equipment, dict) else {}
        if skill_path == 'skills.social.charisma':
            for key in (
                'glasses', 'gloves', 'ring', 'necklace', 'earrings',
                'bracelet1', 'bracelet2', 'helmet', 'gasMask',
            ):
                item = equipment.get(key)
                if isinstance(item, dict):
                    attrs=item.get('attributes') or {}
                    bonus=CombatService._coerce_float(item.get('charismaBonus',item.get('charisma_bonus',attrs.get('charisma_bonus'))),0)
                    if key=='glasses' and trait_count(character_data,'Сварщик') and (
                        attrs.get('welding_glasses') or str(item.get('name','')).strip().casefold() in {'очки сварщика', 'сварочные защитные очки'}
                    ):bonus=1
                    from app.services.equipment_modifications import charisma_bonus
                    equipment_modifier+=charisma_bonus(item,bonus)
        elif skill_path == 'skills.physical.awareness':
            headphones = equipment.get('headphones')
            if awareness_mode in {None, 'hearing'} and isinstance(headphones, dict):
                attrs = CombatService._template_attributes(headphones)
                equipment_modifier += CombatService._coerce_int(
                    headphones.get('awarenessBonus', headphones.get('awareness_bonus', attrs.get('awareness_bonus'))), 0
                )
            if awareness_mode == 'visual':
                equipment_modifier += CombatService._consumable_stat_value_bonus(
                    character_data, 'vision_awareness',
                )
                if not darkness:
                    equipment_modifier += CombatService._consumable_stat_value_bonus(character_data,'light_awareness')
        from app.services.weapon_modules import uses_nvg
        from app.services.equipment_passives import darkness_modifier
        nvg = uses_nvg(character_data)
        if darkness and (awareness_mode == 'visual' or skill_path == 'skills.physical.shooting'):
            equipment_modifier += darkness_modifier(character_data,
                'awareness_penalty' if awareness_mode == 'visual' else 'accuracy_penalty')
        weather=weather or {}
        weather_modifier=weather.get('visual_awareness' if awareness_mode=='visual' else 'awareness',0) if skill_path=='skills.physical.awareness' else weather.get('accuracy',0) if skill_path=='skills.physical.shooting' else 0
        weather_disadvantage=weather.get('awareness_disadvantage',False) if skill_path=='skills.physical.awareness' else weather.get('accuracy_disadvantage',False) if skill_path=='skills.physical.shooting' else False
        status_modifier+=weather_modifier
        disadvantage = forced_disadvantage or weather_disadvantage or CombatService._has_roll_disadvantage(
            character_data, skill_path, darkness=darkness and not nvg and (awareness_mode == 'visual' or skill_path=='skills.physical.shooting'),
        )
        from app.services.equipment_modifications import worn_rule
        if skill_path=='skills.other.stealth':
            equipment_modifier+=worn_rule(character_data,'stealth_hiding_bonus')
        advantage = bool(advantage or CombatService._has_roll_advantage(character_data,skill_path,consume=True)
            or awareness_mode=='hearing' and worn_rule(character_data,'hearing_advantage'))
        deafness = (
            CombatService._deafness_level(character_data)
            if awareness_mode == 'hearing'
            else 0
        )
        from app.services.noise import hearing_modifier
        hearing_penalty=hearing_modifier(deafness) if awareness_mode=='hearing' else 0
        if awareness_mode=='hearing':hearing_penalty+=facial_profile(character_data)['hearing']
        status_modifier+=hearing_penalty
        if skill_path=='skills.other.stealth' and background_is(character_data,'Ночной дозор'):status_modifier+=3 if darkness else -5
        hearing_blocked = awareness_mode == 'hearing' and (deafness >= 90 or facial_profile(character_data)['ears']==2)
        if hearing_blocked:
            rolls = [0]
            roll = 0
            advantage = False
            disadvantage = False
        else:
            rolls = [random.randint(1, 20) for _ in range(2 if advantage != disadvantage else 1)]
            if advantage and not disadvantage:
                roll = max(rolls)
            elif disadvantage and not advantage:
                roll = min(rolls)
            else:
                roll = rolls[0]
        stress_modifier = CombatService._consume_stress_check_modifier(
            character_data, is_attack=False,
        )
        modifier = skill_modifier + related_modifier + equipment_modifier + status_modifier + stress_modifier+roll_bonus
        return {
            'skill_path': requested_skill_path,
            'base_skill_path': skill_path,
            'skill_label': CombatService.NARRATIVE_SKILLS[requested_skill_path],
            'awareness_mode': awareness_mode,
            'darkness': bool(darkness) if awareness_mode == 'visual' else None,
            'deafness': deafness,
            'hearing_modifier': hearing_penalty,
            'weather_modifier':weather_modifier,'weather_disadvantage':bool(weather_disadvantage),
            'automatic_failure': hearing_blocked,
            'failure_reason': 'Персонаж не слышит' if hearing_blocked else None,
            'effective_value': effective_value,
            'brigadier_order':order,
            'rolls': rolls,
            'roll': roll,
            'disadvantage': disadvantage,
            'advantage': advantage,
            'skill_modifier': skill_modifier,
            'related_modifier': related_modifier,
            'equipment_modifier': equipment_modifier,
            'status_modifier': status_modifier,
            'stress_modifier': stress_modifier,
            'modifier': modifier,
            'total': roll + modifier,
        }

    @staticmethod
    def _consume_help_advantage(character, skill_path):
        """Consume a GM-approved one-use help bonus only for its declared skill."""
        if not character or not character.character:
            return None
        data = character.character.data if isinstance(character.character.data, dict) else {}
        meta = data.get('health', {}).get('combatMeta', {})
        if not isinstance(meta, dict):
            return None
        help_bonus = meta.get('helpAdvantage')
        if not isinstance(help_bonus, dict):
            return None
        declared_skill = str(help_bonus.get('skill_path') or '').strip()
        if declared_skill and declared_skill != skill_path:
            return None
        meta.pop('helpAdvantage', None)
        character.character.data = data
        flag_modified(character.character, 'data')
        return help_bonus

    @staticmethod
    def _item_attributes(item):
        if not isinstance(item, dict):
            return {}
        attributes = item.get('attributes')
        return attributes if isinstance(attributes, dict) else {}

    @staticmethod
    def _template_attributes(item):
        attributes = CombatService._item_attributes(item)
        template_id = CombatService._coerce_int(item.get('templateId'), 0) if isinstance(item, dict) else 0
        if template_id:
            template = db.session.get(ItemTemplate, template_id)
            if template and isinstance(template.attributes, dict):
                merged = dict(template.attributes)
                merged.update(attributes)
                return merged
        return attributes

    @staticmethod
    def _weapon_template(item):
        template_id = CombatService._coerce_int(
            item.get('templateId'), 0
        ) if isinstance(item, dict) else 0
        return db.session.get(ItemTemplate, template_id) if template_id else None

    @staticmethod
    def _manual_cycle_type(weapon):
        attributes = CombatService._template_attributes(weapon)
        explicit = weapon.get('manualCycle') or attributes.get('manual_cycle')
        if explicit:
            return str(explicit)
        name = str(weapon.get('name') or '').strip().lower().replace('ё', 'е')
        if any(value in name for value in (
            'суслик', 'малинова', 'мачеха 51', 'свет-99', 'пылесос'
        )) or re.search(r'(?:^|\s)ау(?:\s|$)', name):
            return 'bolt'
        if any(value in name for value in (
            'гора б88', 'гора 580б2', 'ремень 787', 'спаситель 70', 'д-2', 'д2'
        )):
            return 'pump'
        return None

    @staticmethod
    def _weapon_durability(weapon):
        weapon = weapon if isinstance(weapon, dict) else {}
        attributes = CombatService._template_attributes(weapon)
        maximum = max(1, CombatService._coerce_float(
            weapon.get('maxDurability', weapon.get('max_durability', attributes.get('max_durability', 100))),
            100,
        ))
        current = max(0, min(maximum, CombatService._coerce_float(
            weapon.get('durability', maximum), maximum
        )))
        weapon['maxDurability'] = maximum
        weapon['durability'] = current
        jams = CombatService._weapon_jams(weapon)
        remaining_jams = []
        for jam in jams:
            requirement = jam.get('repair_required')
            if requirement == 'increase' and current > CombatService._coerce_float(
                jam.get('durability_after'), current
            ):
                continue
            elif requirement == 'full' and current >= maximum:
                continue
            remaining_jams.append(jam)
        CombatService._set_weapon_jams(weapon, remaining_jams)
        return current, maximum

    @staticmethod
    def _weapon_jams(weapon):
        if not isinstance(weapon, dict):
            return []
        jams = weapon.get('jams')
        if isinstance(jams, list):
            normalized = [jam for jam in jams if isinstance(jam, dict)]
        else:
            legacy = weapon.get('jam')
            normalized = [legacy] if isinstance(legacy, dict) else []
        CombatService._set_weapon_jams(weapon, normalized)
        return normalized

    @staticmethod
    def _set_weapon_jams(weapon, jams):
        normalized = [jam for jam in (jams or []) if isinstance(jam, dict)]
        if normalized:
            weapon['jams'] = normalized
            weapon['jam'] = normalized[-1]
        else:
            weapon.pop('jams', None)
            weapon.pop('jam', None)

    @staticmethod
    def _weapon_jam_effects(weapon):
        jams = CombatService._weapon_jams(weapon)
        return {
            'jams': deepcopy(jams),
            'blocks_fire': any(bool(jam.get('blocks_fire')) for jam in jams),
            'accuracy_penalty': sum(
                max(0, CombatService._coerce_int(jam.get('accuracy_penalty'), 0))
                for jam in jams
            ),
            'shooting_disadvantage': any(
                bool(jam.get('shooting_disadvantage')) for jam in jams
            ),
            'extra_wear_per_shot': sum(
                max(0, CombatService._coerce_int(jam.get('extra_wear_per_shot'), 0))
                for jam in jams
            ),
        }

    @staticmethod
    def _weapon_wear_multiplier(weapon, ammo_profile=None):
        template = CombatService._weapon_template(weapon)
        subcategory = str(
            (template.subcategory if template else None)
            or weapon.get('subcategory')
            or ''
        ).strip().lower().replace('ё', 'е')
        multiplier = 2 if subcategory in {
            'пистолеты', 'снайперские винтовки', 'пулеметы'
        } else 1
        variant = str((ammo_profile or {}).get('ammo_variant') or '').strip().lower()
        if variant == 'improved':
            multiplier *= .5
        elif variant and variant not in {'base', 'standard', 'обычный', 'обычная'}:
            multiplier *= 2
        return multiplier

    @staticmethod
    def _apply_weapon_wear(weapon, amount):
        current, maximum = CombatService._weapon_durability(weapon)
        loss = max(0, CombatService._coerce_int(amount, 0))
        weapon['durability'] = max(0, current - loss)
        weapon['_combatDurabilityLoss']=max(0,CombatService._coerce_float(weapon.get('_combatDurabilityLoss'),0))+current-weapon['durability']
        return {'before': current, 'after': weapon['durability'], 'maximum': maximum, 'loss': loss}

    @staticmethod
    def _weapon_jam_profile(durability):
        value = max(0, CombatService._coerce_float(durability, 0))
        if value <= 0:
            return 20, 12, 6
        if value <= 10:
            return 15, 12, 2
        if value <= 30:
            return 10, 12, 0
        if value <= 45:
            return 7, 10, 0
        if value <= 60:
            return 4, 8, 0
        if value <= 75:
            return 2, 6, 0
        if value <= 90:
            return 1, 4, 0
        return 0, 0, 0

    @staticmethod
    def _roll_weapon_jam(weapon, attack_roll,character_data=None):
        active = CombatService._weapon_jam_effects(weapon)
        if active['blocks_fire']:
            return None
        current, _ = CombatService._weapon_durability(weapon)
        chance, die, bonus = CombatService._weapon_jam_profile(current)
        ammo_profile, _ = CombatService._ranged_damage_profile(weapon)
        variant = str(ammo_profile.get('ammo_variant') or '').strip().lower()
        if variant == 'improved' and 76 <= current <= 100:
            chance = 0
        elif variant == 'handmade' and 90 <= current <= 100:
            chance = max(chance, 1)
        if chance <= 0:
            return {'triggered': False, 'attack_roll': attack_roll, 'chance': 0}
        chance=min(20,chance+2*trait_count(character_data,'Клинически неуравновешенный'))
        clean_roll = CombatService._coerce_int(attack_roll, 0)
        if clean_roll <= 0 or clean_roll > chance:
            return {
                'triggered': False,
                'attack_roll': attack_roll,
                'chance': chance,
            }
        strength_roll = random.randint(1, die) + bonus
        result_number = min(12, strength_roll)
        jam = {
            'id': uuid.uuid4().hex,
            'result': result_number,
            'attack_roll': clean_roll,
            'chance': chance,
            'strength_roll': strength_roll,
            **CombatService.WEAPON_JAM_RESULTS[result_number],
        }
        wear = CombatService._apply_weapon_wear(weapon, jam.get('durability_loss', 0))
        jam['durability_after'] = wear['after']
        jam['durability_loss_applied'] = wear['loss']
        jams = CombatService._weapon_jams(weapon)
        jams.append(jam)
        CombatService._set_weapon_jams(weapon, jams)
        return {'triggered': True, **jam}

    @staticmethod
    def _roll_character_weapon_jam(character_data,weapon,attack_roll):
        if trait_count(character_data,'Клинически неуравновешенный'):
            return CombatService._roll_weapon_jam(weapon,attack_roll,character_data=character_data)
        return CombatService._roll_weapon_jam(weapon,attack_roll)

    @staticmethod
    def _replace_must_do_weapon_jams(weapon, replaced_jams, attack_roll,character_data=None):
        replaced_jams = replaced_jams if isinstance(replaced_jams, list) else []
        replaced_ids = {
            str(item.get('id')) for item in replaced_jams
            if isinstance(item, dict) and item.get('id')
        }
        if replaced_ids:
            CombatService._set_weapon_jams(weapon, [
                jam for jam in CombatService._weapon_jams(weapon)
                if str(jam.get('id')) not in replaced_ids
            ])
        durability_refund = sum(
            max(0, CombatService._coerce_int(
                item.get('durability_loss_applied'), 0,
            ))
            for item in replaced_jams if isinstance(item, dict)
        )
        if durability_refund:
            current_durability, maximum_durability = CombatService._weapon_durability(weapon)
            weapon['durability'] = min(
                maximum_durability,
                current_durability + durability_refund,
            )
            refunded=weapon['durability']-current_durability
            weapon['_combatDurabilityLoss']=max(0,CombatService._coerce_int(weapon.get('_combatDurabilityLoss'),0)-refunded)
        return CombatService._roll_character_weapon_jam(character_data,weapon,attack_roll)

    @staticmethod
    def _consume_weapon_ammo(weapon, shots):
        remaining = max(0, CombatService._coerce_int(shots, 0))
        magazine = weapon.get('installedMagazine')
        stacks = magazine.get('ammo') if isinstance(magazine, dict) else None
        if not isinstance(stacks, list):
            stacks = weapon.get('fixedAmmo') if isinstance(weapon.get('fixedAmmo'), list) else None
        if isinstance(stacks, list):
            while remaining > 0 and stacks:
                stack = stacks[-1]
                quantity = max(0, CombatService._coerce_int(stack.get('quantity'), 0))
                consumed = min(remaining, quantity)
                stack['quantity'] = quantity - consumed
                remaining -= consumed
                if stack['quantity'] <= 0:
                    stacks.pop()
            weapon['ammo'] = sum(
                max(0, CombatService._coerce_int(stack.get('quantity'), 0))
                for stack in stacks if isinstance(stack, dict)
            )
            if isinstance(magazine, dict):
                magazine['weight'] = CombatService._coerce_float(
                    magazine.get('loadedWeight'), 0.25
                ) if weapon['ammo'] > 0 else CombatService._coerce_float(
                    magazine.get('emptyWeight'), 0
                )
        else:
            weapon['ammo'] = max(0, CombatService._coerce_int(weapon.get('ammo'), 0) - remaining)

    @staticmethod
    def _weapon_fire_rate(weapon):
        weapon = weapon if isinstance(weapon, dict) else {}
        attributes = CombatService._template_attributes(weapon)
        raw_value = weapon.get(
            'fireRate',
            weapon.get('fire_rate', attributes.get('fire_rate')),
        )
        if raw_value in (None, ''):
            return None
        from app.services.equipment_modifications import stat as modification_stat
        value = modification_stat(weapon,'fire_rate',CombatService._coerce_int(raw_value, 0))
        return value if value > 0 else None

    @staticmethod
    def _weapon_round_shots(combat_meta, round_number, weapon_index):
        tracker = combat_meta.get('weaponShots') if isinstance(combat_meta, dict) else None
        if not isinstance(tracker, dict):
            return 0
        if CombatService._coerce_int(tracker.get('round'), 0) != round_number:
            return 0
        shots = tracker.get('shots') if isinstance(tracker.get('shots'), dict) else {}
        return max(0, CombatService._coerce_int(shots.get(str(weapon_index)), 0))

    @staticmethod
    def _validate_weapon_fire_rate(combat_meta, round_number, weapon_index, weapon, shots, character_data=None):
        fire_rate = CombatService._weapon_fire_rate(weapon)
        if fire_rate is not None and CombatService._is_pistol_weapon(weapon):
            fire_rate+=trait_count(character_data,'Карманный пулемет')
        fired = CombatService._weapon_round_shots(combat_meta, round_number, weapon_index)
        requested = max(0, CombatService._coerce_int(shots, 0))
        if fire_rate is not None and fired + requested > fire_rate:
            remaining = max(0, fire_rate - fired)
            raise ValidationError(
                f"Превышена скорострельность оружия: осталось выстрелов в этом раунде: {remaining}"
            )
        return {'fire_rate': fire_rate, 'fired': fired, 'remaining': None if fire_rate is None else fire_rate - fired}

    @staticmethod
    def _record_weapon_shots(combat_meta, round_number, weapon_index, shots):
        tracker = combat_meta.get('weaponShots') if isinstance(combat_meta, dict) else None
        if (
            not isinstance(tracker, dict)
            or CombatService._coerce_int(tracker.get('round'), 0) != round_number
        ):
            tracker = {'round': round_number, 'shots': {}}
            combat_meta['weaponShots'] = tracker
        shot_totals = tracker.get('shots') if isinstance(tracker.get('shots'), dict) else {}
        key = str(weapon_index)
        shot_totals[key] = max(0, CombatService._coerce_int(shot_totals.get(key), 0)) + max(
            0, CombatService._coerce_int(shots, 0)
        )
        tracker['shots'] = shot_totals
        return shot_totals[key]

    @staticmethod
    def _weapon_use_wear(weapon, *, fire_mode=None, shot_count=1, volley_count=1, ammo_profile=None, butt=False):
        multiplier = CombatService._weapon_wear_multiplier(weapon, ammo_profile)
        units = 0
        if not weapon.get('_usedInCurrentCombat'):
            weapon['_usedInCurrentCombat'] = True
            units += 1
        if butt:
            units += 1
        elif fire_mode == 'burst':
            units += max(1, CombatService._coerce_int(volley_count, 1))
        elif fire_mode in {'suppression', 'area'}:
            units += max(1, CombatService._coerce_int(volley_count, 1))
        elif CombatService._coerce_int(shot_count, 1) > 1:
            units += max(1, CombatService._coerce_int(shot_count, 1) // 2)
        from app.services.equipment_modifications import rule as modification_rule
        chance=modification_rule(weapon,'burst_no_wear')
        if fire_mode=='burst' and chance and random.randint(1,100)<=chance*100:units=0
        total=units*multiplier+max(0,CombatService._coerce_float(weapon.get('_wearRemainder'),0))
        wear=math.floor(total+1e-9)
        weapon['_wearRemainder']=round(total-wear,6)
        return CombatService._apply_weapon_wear(weapon,wear)

    @staticmethod
    def _is_pistol_weapon(item):
        return CombatService._weapon_class(item) == 'pistol'

    @staticmethod
    def _weapon_subcategory(item):
        if not isinstance(item, dict):
            return ''
        subcategory = str(
            item.get('subcategory') or ''
        ).strip().lower().replace('ё', 'е')
        template_id = CombatService._coerce_int(item.get('templateId'), 0)
        if template_id:
            template = db.session.get(ItemTemplate, template_id)
            if template:
                subcategory = str(
                    template.subcategory or subcategory
                ).strip().lower().replace('ё', 'е')
        return subcategory

    @staticmethod
    def _weapon_class(item):
        subcategory = CombatService._weapon_subcategory(item)
        if 'пистолет' in subcategory and 'пулемет' in subcategory:
            return 'submachine_gun'
        if 'пистолет' in subcategory:
            return 'pistol'
        if 'дробовик' in subcategory:
            return 'shotgun'
        if 'снайпер' in subcategory:
            return 'sniper_rifle'
        if 'пулемет' in subcategory:
            return 'machine_gun'
        if 'гранатомет' in subcategory:
            return 'grenade_launcher'
        return 'other'

    @staticmethod
    def _close_range_weapon_accuracy_bonus(weapon, distance):
        distance = CombatService._coerce_float(distance, -1)
        return 2 if (
            0 <= distance <= 5
            and CombatService._weapon_class(weapon) in {
                'pistol', 'shotgun', 'submachine_gun',
            }
        ) else 0

    @staticmethod
    def _has_shortened_barrel(weapon):
        attributes = CombatService._template_attributes(weapon)
        if attributes.get('shortened_barrel') or attributes.get('short_barrel'):
            return True
        installed = list(weapon.get('installedModules') or [])
        modifications = list(weapon.get('modifications') or [])
        return any(
            isinstance(item, dict)
            and (
                (item.get('attributes') or {}).get('shortened_barrel')
                or 'укорочен' in str(item.get('name') or '').lower()
            )
            for item in installed + modifications
        )

    @staticmethod
    def _sniper_close_range_penalty(weapon, distance):
        distance = CombatService._coerce_float(distance, -1)
        if (
            CombatService._weapon_class(weapon) != 'sniper_rifle'
            or distance < 0
            or distance > 5
            or CombatService._has_shortened_barrel(weapon)
        ):
            return 0
        return max(1, 6 - math.floor(distance))

    @staticmethod
    def _burst_followup_accuracy_penalty(weapon, shot_index, character_data=None):
        if CombatService._coerce_int(shot_index, 0) <= 0:
            return 0
        weapon_class = CombatService._weapon_class(weapon)
        if weapon_class == 'machine_gun':
            return 0
        from app.services.equipment_modifications import rule as modification_rule, worn_rule
        if weapon_class == 'shotgun':
            return max(0,6-worn_rule(character_data,'worn_burst_accuracy_reduction'))
        base=2 if weapon_class=='submachine_gun' else 4
        return max(0,base-modification_rule(weapon,'burst_accuracy_reduction')
            -worn_rule(character_data,'worn_burst_accuracy_reduction'))

    @staticmethod
    def _burst_action_points(weapon):
        from app.services.equipment_modifications import rule as modification_rule
        return max(0,(2 if CombatService._weapon_class(weapon)=='submachine_gun' else 3)+modification_rule(weapon,'burst_ap_delta'))

    @staticmethod
    def _area_fire_accuracy_penalty(weapon):
        subcategory = CombatService._weapon_subcategory(weapon)
        if 'пистолет' in subcategory and 'пулемет' in subcategory:
            base_penalty = 2
        elif 'дробовик' in subcategory:
            base_penalty = 6
        elif subcategory == 'пулеметы' or subcategory.startswith('пулемет'):
            base_penalty = -4
        else:
            base_penalty = 4
        return base_penalty

    @staticmethod
    def _are_opponents(actor, target):
        if not actor or not target or actor.id == target.id:
            return False
        actor_team = str(getattr(actor, 'team_name', None) or '').strip().lower()
        target_team = str(getattr(target, 'team_name', None) or '').strip().lower()
        return not (actor_team and target_team and actor_team == target_team)

    @staticmethod
    def _area_fire_shot_count(fire_profile):
        profile = fire_profile if isinstance(fire_profile, dict) else {}
        if profile.get('machine_gun_burst'):
            return 10
        return max(0, CombatService._coerce_int(profile.get('burst_size'), 0)) * 2

    @staticmethod
    def _area_fire_hit_count(roll, difficulty, shot_count, roll_modifier=0):
        margin = (
            CombatService._coerce_int(roll, 0)
            + CombatService._coerce_float(roll_modifier, 0)
            - CombatService._coerce_float(difficulty, 0)
        )
        return min(
            max(1, CombatService._coerce_int(shot_count, 1)),
            1 + int(max(0, margin // 3)),
        )

    @staticmethod
    def _aimed_zone_difficulty_penalty(zone):
        return 5 if zone == 'head' else 0

    @staticmethod
    def _parse_percent(value, default=0.0):
        if isinstance(value, (int, float)):
            return float(value)
        match = re.search(r'-?\d+(?:[.,]\d+)?', str(value or ''))
        if not match:
            return default
        try:
            return float(match.group(0).replace(',', '.'))
        except ValueError:
            return default

    @staticmethod
    def _protection_percent(value, default=0.0):
        parsed = CombatService._parse_percent(value, default)
        return round(parsed * 100 if 0 < abs(parsed) <= 1 else parsed, 9)

    @staticmethod
    def _random_hit_zone(roll, aimed_zone=None, melee=False):
        if aimed_zone in {'head', 'chest', 'abdomen', 'left_arm', 'right_arm', 'left_leg', 'right_leg'}:
            return aimed_zone
        if melee:
            if roll <= 2:
                return 'right_leg'
            if roll <= 4:
                return 'left_leg'
            if roll <= 8:
                return 'abdomen'
            if roll <= 12:
                return 'chest'
            if roll <= 16:
                return 'left_arm'
            return 'right_arm'
        if roll <= 3:
            return 'left_arm'
        if roll <= 6:
            return 'left_leg'
        if roll <= 9:
            return 'abdomen'
        if roll <= 12:
            return 'right_leg'
        if roll <= 14:
            return 'abdomen'
        if roll <= 19:
            return 'chest'
        return 'head'

    @staticmethod
    def _weapon_damage_profile(weapon, attack_type=None):
        attributes = CombatService._template_attributes(weapon)
        damage = CombatService._coerce_float(attributes.get('damage', weapon.get('damage', 0)), 0)
        penetration = CombatService._parse_percent(
            attributes.get('armor_piercing', attributes.get('penetration', weapon.get('armor_piercing', 0))),
            0,
        )
        if -1 < penetration < 1:
            penetration *= 100
        bleeding = attributes.get('bleeding', attributes.get('bleeding_level', ''))
        if attack_type:
            attack_modifiers = attributes.get('attack_modifiers') or {}
            selected = attack_modifiers.get(attack_type) if isinstance(attack_modifiers, dict) else None
            if isinstance(selected, dict):
                damage += CombatService._coerce_float(selected.get('damage'), 0)
                penetration += CombatService._parse_percent(selected.get('armor_piercing'), 0)
            else:
                attack_name = str(attack_type).lower()
                if 'кол' in attack_name:
                    damage *= 1.25
                    penetration += 10
                elif 'реж' in attack_name:
                    damage *= 0.75
                    penetration = max(0, penetration - 10)
                elif 'всп' in attack_name:
                    damage *= 1.35
                    penetration += 10
                elif 'круг' in attack_name:
                    penetration = max(0, penetration - 10)
        melee_damage_type = attributes.get(
            'melee_damage_type',
            attributes.get('attack_type', weapon.get('attackType')),
        )
        attack_name = str(attack_type or '').strip().lower().replace('ё', 'е')
        if attack_name:
            if 'круг' in attack_name:
                allowed_attacks = [
                    str(item or '').strip().lower().replace('ё', 'е')
                    for item in (attributes.get('allowed_attacks') or [])
                ]
                melee_damage_type = (
                    'slashing'
                    if any('руб' in item for item in allowed_attacks)
                    else 'crushing'
                )
            else:
                melee_damage_type = attack_type
        return {
            'damage': max(0, damage),
            'armor_piercing': penetration,
            'weight': max(0, CombatService._coerce_float(
                weapon.get('weight', attributes.get('weight', 0)),
                0,
            )),
            'bleeding': bleeding,
            'accuracy': attributes.get('accuracy', weapon.get('accuracy', 0)),
            'effective_range': CombatService._coerce_int(
                attributes.get('effective_range', attributes.get('range', weapon.get('range', 0))), 0
            ),
            'damage_type': attributes.get('damage_type', 'physical'),
            'melee_damage_type': melee_damage_type,
            'weight_class': attributes.get(
                'weight_class',
                weapon.get('weightClass', weapon.get('weight_class', 'heavy')),
            ),
            'action_points': CombatService._coerce_int(
                attributes.get('action_points'), 0,
            ),
            'skip_strength_scaling': bool(attributes.get('skip_strength_scaling')),
        }

    @staticmethod
    def _ranged_damage_profile(weapon):
        magazine = weapon.get('installedMagazine') or {}
        stacks = magazine.get('ammo') if isinstance(magazine, dict) else []
        if not isinstance(stacks, list) or not stacks:
            stacks = weapon.get('fixedAmmo') if isinstance(weapon.get('fixedAmmo'), list) else []
        stack = next((
            item
            for item in reversed(stacks or [])
            if isinstance(item, dict)
            and CombatService._coerce_int(item.get('quantity'), 0) > 0
        ), None)
        if not stack:
            return CombatService._weapon_damage_profile(weapon), None
        profile = CombatService._weapon_damage_profile(stack)
        attributes = CombatService._template_attributes(stack)
        profile['caliber'] = attributes.get('caliber', stack.get('caliber'))
        profile['ammo_variant'] = (
            attributes.get('ammo_variant')
            or stack.get('ammo_variant')
            or attributes.get('ammo_kind')
        )
        profile['ammo_name'] = stack.get('name') or attributes.get('name')
        caliber = CombatService._normalize_caliber(profile['caliber'])
        if re.fullmatch(r'18x45\s*т', caliber):
            profile['pain_on_penetration'] = 3
        elif re.fullmatch(r'18x45\s*и', caliber):
            profile['pain_on_penetration'] = 2
            profile['pepper_round'] = True
        elif caliber.startswith('12x70') and 'газ' in caliber:
            profile.update(damage_type='gas', pain_on_penetration=3,
                           pain_per_round=1, pain_rounds=5, pepper_round=True)
        return profile, stack

    @staticmethod
    def _apply_pepper_head_hit(health, character_data):
        meta = health.setdefault('combatMeta', {})
        hits = max(0, CombatService._coerce_int(meta.get('pepperHeadHits'), 0)) + 1
        meta['pepperHeadHits'] = hits
        meta['pepperSkipTurns'] = max(1, CombatService._coerce_int(meta.get('pepperSkipTurns'), 0))
        apply_effect_to_health(health, {
            'type': 'custom', 'name': 'Перцовка: штраф к броскам',
            'source': 'pepper_head_rolls', 'scope': 'combat', 'tick': 'manual',
            'rollPenalty': 5,
        }, character_data=character_data)
        apply_effect_to_health(health, {
            'type': 'periodic_adjustment', 'name': 'Перцовка: боль каждый ход',
            'source': 'pepper_head_pain', 'scope': 'combat', 'tick': 'turn_end',
            'value': 1, 'adjustments': [{'field': 'painLevel', 'delta': 1, 'min': 0, 'max': 10}],
        }, character_data=character_data)
        shock_roll = None
        if hits >= 3:
            health['effects'] = [
                effect for effect in normalize_effect_list(health.get('effects') or [])
                if effect.get('type') != 'shock'
            ]
            apply_effect_to_health(health, {
                'type': 'shock', 'name': 'Перцовка: болевой шок на 6 часов',
                'source': 'pepper_three_head_hits', 'tick': 'manual',
                'time_unit': 'hour', 'remaining': 6,
            }, character_data=character_data)
            apply_effect_to_health(health, {
                'type': 'custom', 'name': 'Перцовка: штраф к атаке и внимательности на сутки',
                'source': 'pepper_day_penalty', 'tick': 'manual',
                'time_unit': 'hour', 'remaining': 24,
                'modifiers': {
                    'skills.physical.shooting': 5,
                    'skills.physical.melee': 5,
                    'skills.physical.accuracy': 5,
                    'skills.physical.awareness': 5,
                },
            }, character_data=character_data)
        else:
            shock_roll = random.randint(1, 100)
            if shock_roll <= 50:
                apply_effect_to_health(health, {
                    'type': 'shock', 'name': 'Болевой шок от перцового боеприпаса',
                    'source': 'pepper_head', 'tick': 'manual',
                }, character_data=character_data)
        return {'hits': hits, 'shock_roll': shock_roll, 'shock': hits >= 3 or shock_roll <= 50}

    @staticmethod
    def _virtual_melee_profile(attack_type, weapon=None, character_data=None):
        attack_key = str(attack_type or '').strip().lower()
        if attack_key == 'bayonet':
            from app.services.weapon_modules import bayonet_profile
            return bayonet_profile(weapon)
        if attack_key == 'unarmed':
            strength_bonus = CombatService._skill_modifier(
                character_data or {}, 'skills.physical.strength'
            )
            from app.services.equipment_passives import unarmed_damage
            return {
                'damage': unarmed_damage(character_data or {}, strength_bonus),
                'armor_piercing': 0,
                'bleeding': '',
                'accuracy': 0,
                'damage_type': 'physical',
                'melee_damage_type': 'crushing',
                'weight_class': 'light',
                'weight': 0,
                'skip_strength_scaling': True,
            }
        if attack_key == 'firearm_butt':
            weapon = weapon if isinstance(weapon, dict) else {}
            attributes = CombatService._template_attributes(weapon)
            weight = CombatService._coerce_float(
                weapon.get('weight', attributes.get('weight', 0)), 0
            )
            return {
                'damage': 25 if weight <= 1 else 40,
                'armor_piercing': 0,
                'bleeding': '',
                'accuracy': 0,
                'damage_type': 'physical',
                'melee_damage_type': 'crushing',
                'weight_class': 'light' if weight <= 1 else 'heavy',
                'weight': max(0, weight),
            }
        if attack_key == 'grapple_desperate':
            strength_bonus = CombatService._skill_modifier(
                character_data or {}, 'skills.physical.strength'
            )
            return {
                'damage': max(0, 10 + 5 * strength_bonus),
                'armor_piercing': 0,
                'bleeding': '',
                'accuracy': 0,
                'damage_type': 'physical',
                'melee_damage_type': 'crushing',
                'weight_class': 'heavy',
                'weight': 0,
                'skip_strength_scaling': True,
            }
        return None

    @staticmethod
    def _melee_action_cost(profile, attack_type=None):
        profile = profile if isinstance(profile, dict) else {}
        configured_cost = CombatService._coerce_int(profile.get('action_points'), 0)
        if configured_cost > 0:
            return configured_cost
        attack_key = str(attack_type or '').strip().lower().replace('ё', 'е')
        if attack_key == 'unarmed':
            return 2
        weight_class = str(profile.get('weight_class') or '').strip().lower()
        if 'очень' in weight_class or 'heavy_plus' in weight_class:
            base_cost = 4
        elif 'лег' in weight_class or weight_class == 'light':
            base_cost = 2
        else:
            base_cost = 3
        modifier = 0
        if 'кол' in attack_key:
            modifier = 1
        elif 'реж' in attack_key:
            modifier = -1
        elif 'всп' in attack_key:
            modifier = 2
        elif 'круг' in attack_key:
            modifier = 1
        return max(1, base_cost + modifier)

    @staticmethod
    def _crushing_damage_multiplier(profile):
        profile = profile if isinstance(profile, dict) else {}
        damage_type = str(profile.get('melee_damage_type') or '').strip().lower()
        if 'дроб' not in damage_type and damage_type != 'crushing':
            return None
        weight = max(0, CombatService._coerce_float(profile.get('weight'), 0))
        return min(1.0, (20 + 5 * weight) / 100)

    @staticmethod
    def _character_distance(first,second):
        return max(0,math.hypot(first.pos_x-second.pos_x,first.pos_y-second.pos_y)-.5)

    @staticmethod
    def _is_adjacent(first, second):
        if (getattr(first, 'floor_level', 0) or 0) != (getattr(second, 'floor_level', 0) or 0):
            return False
        same_id = getattr(first,'id',None) is not None and getattr(first,'id',None)==getattr(second,'id',None)
        return first is not second and not same_id and CombatService._character_distance(first,second) <= 1+1e-9

    @staticmethod
    def _gunpoint_meta(loc_char):
        data = (
            loc_char.character.data
            if loc_char and loc_char.character and isinstance(loc_char.character.data, dict)
            else {}
        )
        health = data.setdefault('health', {})
        return data, health.setdefault('combatMeta', {})

    @staticmethod
    def _gunpoint_weapon(loc_char, expected_index=None):
        data, _ = CombatService._gunpoint_meta(loc_char)
        weapons = data.get('weapons') if isinstance(data.get('weapons'), list) else []
        index = CombatService._coerce_int(loc_char.drawn_weapon_index, -1)
        if expected_index is not None and index != CombatService._coerce_int(expected_index, -2):
            raise ValidationError('The weapon held at the target is no longer in hand')
        if not 0 <= index < len(weapons) or not isinstance(weapons[index], dict):
            raise ValidationError('Take a firearm in hand first')
        weapon = weapons[index]
        template = CombatService._weapon_template(weapon)
        category = str(
            (template.category if template else None) or weapon.get('category') or ''
        ).strip().lower()
        if category not in {'weapon', 'firearm'}:
            raise ValidationError('Only a firearm can be pressed against a target')
        if CombatService._weapon_jam_effects(weapon)['blocks_fire']:
            raise ValidationError('Clear the weapon jam first')
        if weapon.get('requiresManualCycle'):
            raise ValidationError('Cycle the weapon action first')
        return data, weapons, index, weapon

    @staticmethod
    def _weapon_loaded_shots(weapon):
        magazine = weapon.get('installedMagazine')
        stacks = magazine.get('ammo') if isinstance(magazine, dict) else None
        if not isinstance(stacks, list):
            stacks = weapon.get('fixedAmmo') if isinstance(weapon.get('fixedAmmo'), list) else None
        if isinstance(stacks, list):
            return sum(
                max(0, CombatService._coerce_int(stack.get('quantity'), 0))
                for stack in stacks if isinstance(stack, dict)
            )
        return max(0, CombatService._coerce_int(weapon.get('ammo'), 0))

    @staticmethod
    def _gunpoint_weapon_difficulty_modifier(weapon):
        attributes = CombatService._template_attributes(weapon)
        configured = attributes.get('gunpoint_difficulty_modifier')
        if configured is not None:
            return CombatService._coerce_int(configured, 0)
        return -5 if CombatService._is_pistol_weapon(weapon) else 0

    @staticmethod
    def _clear_gunpoint(loc_char):
        data, meta = CombatService._gunpoint_meta(loc_char)
        removed = meta.pop('gunpoint', None)
        if removed:
            loc_char.character.data = data
            flag_modified(loc_char.character, 'data')
        return removed

    @staticmethod
    def _release_invalid_gunpoints(location_id):
        holders = LocationCharacter.query.filter_by(location_id=location_id).all()
        for holder in holders:
            _, meta = CombatService._gunpoint_meta(holder)
            gunpoint = meta.get('gunpoint')
            if not isinstance(gunpoint, dict):
                continue
            target = db.session.get(
                LocationCharacter,
                CombatService._coerce_int(gunpoint.get('target_location_character_id'), 0),
            )
            invalid = bool(
                not target
                or target.location_id != location_id
                or not CombatService._is_adjacent(holder, target)
                or CombatService._location_character_condition(holder)['state'] != 'active'
            )
            try:
                if not invalid:
                    CombatService._gunpoint_weapon(holder, gunpoint.get('weapon_index'))
            except ValidationError:
                invalid = True
            if invalid:
                CombatService._clear_gunpoint(holder)

    @staticmethod
    def _is_behind(attacker, target):
        facing_x = CombatService._coerce_int(getattr(target, 'facing_x', 0), 0)
        facing_y = CombatService._coerce_int(getattr(target, 'facing_y', 1), 1)
        relative_x = attacker.pos_x - target.pos_x
        relative_y = attacker.pos_y - target.pos_y
        return facing_x * relative_x + facing_y * relative_y < 0

    @staticmethod
    def _melee_target_profile(
        attacker,
        target,
        melee_bonus,
        accuracy=0,
        aimed=False,
        target_zone=None,
        circular=False,
    ):
        target_data = (
            target.character.data
            if getattr(target, 'character', None) and isinstance(target.character.data, dict)
            else {}
        )
        health = target_data.get('health') if isinstance(target_data.get('health'), dict) else {}
        active_types = {
            effect.get('type')
            for effect in normalize_effect_list(health.get('effects') or [])
            if effect.get('active', True)
            and (
                effect.get('remaining') is None
                or CombatService._coerce_float(effect.get('remaining'), 0) > 0
            )
        }
        temperature = CombatService._coerce_float(health.get('temperature'), 36)
        unconscious = bool(
            active_types.intersection({'unconsciousness', 'sleep', 'shock'})
            or CombatService._intoxication_profile(target_data)['unconscious']
            or temperature <= 29
            or temperature >= 41
        )
        behind = CombatService._is_behind(attacker, target)
        prone = str(getattr(target, 'posture', '') or '').strip().lower() == 'prone'
        block_penalty = (
            0
            if behind or unconscious
            else max(
                0,
                CombatService._coerce_int(
                    getattr(target, 'melee_block_effectiveness', 0), 0,
                ),
            )
        )
        combat_meta = health.get('combatMeta') if isinstance(health.get('combatMeta'), dict) else {}
        aimed_penalty = (
            0
            if behind
            else (4 if aimed and target_zone == 'head' else (2 if aimed else 0))
        )
        difficulty = (
            (8 - melee_bonus * 2 if behind else 12 - melee_bonus)
            + block_penalty
            + aimed_penalty
            + (3 if circular else 0)
            - accuracy
        )
        return {
            'from_behind': behind,
            'target_prone': prone,
            'target_unconscious': unconscious,
            'automatic_hit': unconscious,
            'advantage': bool(prone or getattr(target, 'grappled_by_id', None)),
            'block_penalty': block_penalty,
            'block_arm': (
                combat_meta.get('meleeBlockArm')
                if combat_meta.get('meleeBlockArm') in {'left_arm', 'right_arm'}
                else 'right_arm'
            ),
            'block_counterattack': bool(combat_meta.get('meleeBlockCounterattack')),
            'aimed_penalty': aimed_penalty,
            'difficulty': difficulty,
        }

    @staticmethod
    def _is_in_facing_arc(attacker, target_x, target_y, half_angle_degrees=60):
        delta_x = CombatService._coerce_float(target_x, 0) - CombatService._coerce_float(attacker.pos_x, 0)
        delta_y = CombatService._coerce_float(target_y, 0) - CombatService._coerce_float(attacker.pos_y, 0)
        distance = math.hypot(delta_x, delta_y)
        if distance <= 1e-9:
            return True
        facing_x = CombatService._coerce_int(getattr(attacker, 'facing_x', 0), 0)
        facing_y = CombatService._coerce_int(getattr(attacker, 'facing_y', 1), 1)
        facing_length = math.hypot(facing_x, facing_y)
        if facing_length <= 1e-9:
            facing_x, facing_y, facing_length = 0, 1, 1
        cosine = (delta_x * facing_x + delta_y * facing_y) / (distance * facing_length)
        return cosine >= math.cos(math.radians(half_angle_degrees))

    @staticmethod
    def _shooting_movement_modifiers(shooter_mode=None, target_mode=None, target_evading=False,character_data=None):
        """Movement changes accuracy, not the number of attack dice."""
        shooter_mode = str(shooter_mode or '').lower()
        if shooter_mode=='run' and background_is(character_data,'Спринтер'):shooter_mode='walk'
        target_mode = str(target_mode or '').lower()
        difficulty_penalty = {'walk': 1, 'backward_sideways': 1, 'run': 2, 'sprint': 3}.get(shooter_mode, 0)
        difficulty_penalty += {'run': 1, 'sprint': 2}.get(target_mode, 0)
        if target_evading and target_mode in {'walk', 'run', 'sprint'}:
            difficulty_penalty += 2

        return {
            'difficulty_penalty': difficulty_penalty,
            'disadvantage': False,
        }

    @staticmethod
    def _movement_evasion_active(character):
        if not character or getattr(character, 'movement_mode_this_turn', None) not in {'walk', 'run', 'sprint'}:
            return False
        data = character.character.data if character.character else {}
        return bool((data or {}).get('health', {}).get('combatMeta', {}).get('movementEvasion'))

    @staticmethod
    def _target_evasion_trait_penalty(character):
        if not CombatService._movement_evasion_active(character):
            return 0
        data = character.character.data if character.character else {}
        return 2 if trait_count(data, 'Маленький рост') else 0

    @staticmethod
    def _movement_distance_limit(movement_mode, posture='standing', evading=False, movement_penalty=0,character_data=None):
        base = max(0,DEFAULT_CONVERSION_BASE-movement_penalty)
        if movement_mode == 'correction':
            maximum = 3
        elif movement_mode == 'run':
            maximum = 2*base
        elif movement_mode == 'sprint':
            maximum = 3*base
        elif posture == 'prone':
            maximum = math.ceil(base/3)
        elif posture == 'sitting' or movement_mode == 'backward_sideways':
            maximum = math.floor(base/2)
        else:
            maximum = base
        if trait_count(character_data,'Гусь и гусеничка') and movement_mode=='walk':
            if posture=='sitting':maximum=base
            elif posture=='prone':maximum=math.ceil(base/2)
        if CombatService._sprinter_walk(character_data,movement_mode,posture):
            maximum=2*(base if movement_mode=='run' else maximum)
        penalty_mode='walk' if CombatService._sprinter_walk(character_data,movement_mode,posture) else movement_mode
        reduction = {'walk': 3, 'run': 5, 'sprint': 7}.get(penalty_mode, 0) if evading else 0
        return max(0, maximum - reduction)

    @staticmethod
    def _opposed_roll(character_data, skill_path, attribute_paths=(), disadvantage=False):
        skill = CombatService._skill_modifier(character_data, skill_path)
        attribute = max(
            (
                CombatService._skill_modifier(character_data, path)
                for path in attribute_paths
            ),
            default=0,
        )
        from app.services.brigadier_order import boost_roll
        rolls,roll,order_bonus=boost_roll(character_data,[],disadvantage=disadvantage)
        return {
            'roll': roll,
            'rolls': rolls,
            'bonus': skill + attribute+order_bonus,
            'total': roll + skill + attribute+order_bonus,
            'critical_success': roll == 20,
            'critical_failure': roll == 1,
        }

    @staticmethod
    def _has_free_hand(loc_char):
        data = loc_char.character.data if loc_char.character and isinstance(loc_char.character.data, dict) else {}
        from app.services.portable_lights import held_hand
        from app.services.weapon_grip import pair
        if pair(loc_char):return False
        occupied_extra = int(bool(data.get('health', {}).get('combatMeta', {}).get('heldWaterItem')))+int(held_hand(data))
        if loc_char.drawn_weapon_index is None:
            return occupied_extra<2
        data = loc_char.character.data if loc_char.character and isinstance(loc_char.character.data, dict) else {}
        weapons = data.get('weapons') if isinstance(data.get('weapons'), list) else []
        index = CombatService._coerce_int(loc_char.drawn_weapon_index, -1)
        if not 0 <= index < len(weapons):
            return occupied_extra<2
        weapon = weapons[index] if isinstance(weapons[index], dict) else {}
        if weapon.get('naturalWeapon') or CombatService._template_attributes(weapon).get('natural_weapon'):
            return occupied_extra<2
        from app.services.weapon_grip import hands
        return hands(data) + occupied_extra < 2

    @staticmethod
    def _has_usable_free_hand(loc_char):
        if not CombatService._has_free_hand(loc_char):
            return False
        data = (
            loc_char.character.data
            if loc_char and loc_char.character and isinstance(loc_char.character.data, dict)
            else {}
        )
        from app.services.weapon_grip import hands, functional_arms
        weapons=data.get('weapons') or []
        index=getattr(loc_char,'drawn_weapon_index',None)
        weapon=weapons[index] if type(index) is int and 0<=index<len(weapons) else {}
        natural=weapon.get('naturalWeapon') or CombatService._template_attributes(weapon).get('natural_weapon')
        from app.services.portable_lights import held_hand
        occupied_hands=(hands(data) if weapon and not natural else 0)+int(bool(((data.get('health') or {}).get('combatMeta') or {}).get('heldWaterItem')))+int(held_hand(data))
        return functional_arms(data) > occupied_hands

    @staticmethod
    def _clear_grapple(holder, captive=None):
        if not holder:
            return
        if captive is None and holder.grapple_target_id:
            captive = db.session.get(LocationCharacter, holder.grapple_target_id)
        holder.grapple_target_id = None
        holder.grapple_strengthened = False
        holder.grapple_choke_rounds = 0
        holder.grapple_live_shield = False
        if captive and captive.grappled_by_id == holder.id:
            captive.grappled_by_id = None

    @staticmethod
    def _release_invalid_grapples(location_id):
        holders = LocationCharacter.query.filter(
            LocationCharacter.location_id == location_id,
            LocationCharacter.grapple_target_id.isnot(None),
        ).all()
        for holder in holders:
            captive = db.session.get(LocationCharacter, holder.grapple_target_id)
            holder_condition = CombatService._location_character_condition(holder)
            invalid = bool(
                not captive
                or captive.location_id != location_id
                or captive.grappled_by_id != holder.id
                or holder_condition['state'] in {'critical', 'dead'}
                or not CombatService._has_usable_free_hand(holder)
            )
            if invalid:
                CombatService._clear_grapple(holder, captive)

    @staticmethod
    def _sync_grapple_facing(holder, captive=None):
        if not holder:
            return
        if captive is None and holder.grapple_target_id:
            captive = db.session.get(LocationCharacter, holder.grapple_target_id)
        if captive:
            captive.facing_x = CombatService._coerce_int(holder.facing_x, 0)
            captive.facing_y = CombatService._coerce_int(holder.facing_y, 1)

    @staticmethod
    def _live_shield_target(holder):
        if not holder or not getattr(holder, 'grapple_live_shield', False):
            return None
        target_id = getattr(holder, 'grapple_target_id', None)
        return db.session.get(LocationCharacter, target_id) if target_id else None

    @staticmethod
    def _caliber_key(value):
        return re.sub(
            r'[^0-9a-zа-я]+',
            '',
            str(value or '').strip().lower().replace('х', 'x'),
        )

    @staticmethod
    def _is_buckshot_profile(profile):
        profile = profile if isinstance(profile, dict) else {}
        variant = str(profile.get('ammo_variant') or '').strip().lower()
        ammo_name = str(profile.get('ammo_name') or '').strip().lower()
        return (
            bool(re.match(r'^12x70(?:$|[^0-9])', CombatService._normalize_caliber(profile.get('caliber'))))
            and (variant == 'buckshot' or 'картеч' in ammo_name)
        )

    @staticmethod
    def _rapid_fire_accuracy_penalty(weapon):
        ammo_profile, _ = CombatService._ranged_damage_profile(weapon)
        from app.services.equipment_modifications import rule as modification_rule
        return 0 if CombatService._is_buckshot_profile(ammo_profile) else modification_rule(weapon,'rapid_penalty',4)

    @staticmethod
    def _is_12x70_slug_profile(profile):
        profile = profile if isinstance(profile, dict) else {}
        variant = str(profile.get('ammo_variant') or '').strip().lower()
        ammo_name = str(profile.get('ammo_name') or '').strip().lower()
        return (
            bool(re.match(r'^12x70(?:$|[^0-9])', CombatService._normalize_caliber(profile.get('caliber'))))
            and (variant == 'slug' or 'пуля' in ammo_name)
        )

    @staticmethod
    def _is_mutant_character(character_data):
        data = character_data if isinstance(character_data, dict) else {}
        basic = data.get('basic') if isinstance(data.get('basic'), dict) else {}
        if any(
            bool(value)
            for value in (
                data.get('is_mutant'),
                data.get('isMutant'),
                basic.get('is_mutant'),
                basic.get('isMutant'),
            )
        ):
            return True
        labels = (
            data.get('character_type'),
            data.get('characterType'),
            data.get('species'),
            basic.get('character_type'),
            basic.get('characterType'),
            basic.get('species'),
        )
        return any('мутант' in str(value or '').strip().lower() for value in labels)

    @staticmethod
    def _mutant_traits(character_data):
        mutant = character_data.get('mutant') if isinstance(character_data, dict) else {}
        if not isinstance(mutant, dict):
            return []
        traits = list(mutant.get('traits') or [])
        variant = mutant.get('variant') if isinstance(mutant.get('variant'), dict) else {}
        traits.extend(variant.get('traits') or [])
        return [str(value) for value in traits]

    @staticmethod
    def _mutant_shooting_target_modifier(character_data):
        for trait in CombatService._mutant_traits(character_data):
            match = re.search(r'Точность при стрельбе\s*([+-]\d+)', trait, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return 0

    @staticmethod
    def _mutant_pain_profile(character_data):
        traits = CombatService._mutant_traits(character_data)
        if any('Неуязвим к боли' in trait for trait in traits):
            return {'immune': True, 'threshold_multiplier': 1}
        multiplier = 1
        for trait in traits:
            match = re.search(
                r'Урон, необходимый для получения уровня боли\s*(\d+)\s*[хx]',
                trait,
                re.IGNORECASE,
            )
            if match:
                multiplier = max(multiplier, int(match.group(1)))
        return {'immune': False, 'threshold_multiplier': multiplier}

    @staticmethod
    def _mutant_trauma_difficulty_modifier(character_data):
        modifier = 0
        for trait in CombatService._mutant_traits(character_data):
            match = re.search(
                r'Сложность\s+для\s+получения\s+Доп\.?\s*травмы\s*([+-]?\d+)',
                trait,
                re.IGNORECASE,
            )
            if match:
                modifier += int(match.group(1))
        return modifier

    @staticmethod
    def _mutant_has_trait(character_data, fragment):
        needle = str(fragment or '').strip().casefold()
        return bool(needle) and any(
            needle in trait.casefold()
            for trait in CombatService._mutant_traits(character_data)
        )

    @staticmethod
    def _mutant_acceleration_bonus(character_data):
        for trait in CombatService._mutant_traits(character_data):
            if 'Возможность разгона' not in trait:
                continue
            match = re.search(r'получает\s*\+(\d+)\s*Перемещ', trait, re.IGNORECASE)
            if match:
                return max(0, int(match.group(1)))
        return 0

    @staticmethod
    def _mutant_profile_name(character_data):
        data = character_data if isinstance(character_data, dict) else {}
        basic = data.get('basic') if isinstance(data.get('basic'), dict) else {}
        mutant = data.get('mutant') if isinstance(data.get('mutant'), dict) else {}
        return str(basic.get('mutant_type') or mutant.get('profile') or '').strip()

    @staticmethod
    def _mutant_pack_roll_bonus(loc_char, participant_ids=None):
        if not loc_char or not loc_char.character or not isinstance(loc_char.character.data, dict):
            return 0
        data = loc_char.character.data
        profile_name = CombatService._mutant_profile_name(data).casefold()
        if not profile_name:
            return 0
        required = 0
        bonus = 0
        for trait in CombatService._mutant_traits(data):
            if 'Чувство стаи' in trait:
                match = re.search(r'хотя бы\s+(\d+)\s+жив', trait, re.IGNORECASE)
                required = int(match.group(1)) if match else 0
                bonus = 1
                break
            if 'Вожак стаи' in trait:
                match = re.search(r'хотя бы\s+(\d+)\s+жив', trait, re.IGNORECASE)
                required = int(match.group(1)) if match else 0
                bonus = 3
                profile_name = 'собака'
                break
        if required <= 0:
            return 0
        candidates = CombatService._unique_location_characters(
            LocationCharacter.query.filter_by(location_id=loc_char.location_id).all()
        )
        if participant_ids is None:
            state = LocationCombatState.query.filter_by(
                location_id=loc_char.location_id,
            ).first()
            if state and state.status == 'active':
                participant_ids = set(state.turn_order or [])
        if participant_ids is not None:
            participant_ids = {
                CombatService._coerce_int(value, 0) for value in participant_ids
            }
            candidates = [item for item in candidates if item.id in participant_ids]
        alive = 0
        for candidate in candidates:
            candidate_data = (
                candidate.character.data
                if candidate.character and isinstance(candidate.character.data, dict)
                else {}
            )
            if CombatService._mutant_profile_name(candidate_data).casefold() != profile_name:
                continue
            if CombatService._location_character_condition(candidate)['state'] != 'dead':
                alive += 1
        return bonus if alive >= required else 0

    @staticmethod
    def _refresh_mutant_pack_bonus(loc_char, participant_ids=None):
        if not loc_char or not loc_char.character or not isinstance(loc_char.character.data, dict):
            return
        data = loc_char.character.data
        meta = data.setdefault('health', {}).setdefault('combatMeta', {})
        value = CombatService._mutant_pack_roll_bonus(loc_char, participant_ids)
        if CombatService._coerce_int(meta.get('mutantPackRollBonus'), 0) == value:
            return
        if value:
            meta['mutantPackRollBonus'] = value
        else:
            meta.pop('mutantPackRollBonus', None)
        profile_name = CombatService._mutant_profile_name(data).casefold()
        dog_will_advantage = False
        if profile_name == 'собака':
            candidates = CombatService._unique_location_characters(
                LocationCharacter.query.filter_by(
                    location_id=loc_char.location_id,
                ).all()
            )
            if participant_ids is None:
                state = LocationCombatState.query.filter_by(
                    location_id=loc_char.location_id,
                ).first()
                participant_ids = (
                    set(state.turn_order or [])
                    if state and state.status == 'active'
                    else None
                )
            if participant_ids is not None:
                allowed = {
                    CombatService._coerce_int(value, 0)
                    for value in participant_ids
                }
                candidates = [item for item in candidates if item.id in allowed]
            dog_will_advantage = any(
                candidate.character
                and CombatService._location_character_condition(candidate)['state'] != 'dead'
                and CombatService._mutant_has_trait(
                    candidate.character.data
                    if isinstance(candidate.character.data, dict)
                    else {},
                    'Вожак стаи',
                )
                for candidate in candidates
            )
        if dog_will_advantage:
            meta['mutantPackWillAdvantage'] = True
        else:
            meta.pop('mutantPackWillAdvantage', None)
        loc_char.character.data = data
        flag_modified(loc_char.character, 'data')

    @staticmethod
    def _mutant_back_torso_protection(character_data):
        """Return the exact innate torso protection used for attacks from behind."""
        base = None
        variant_bonus = 0
        mutant = character_data.get('mutant') if isinstance(character_data, dict) else {}
        mutant = mutant if isinstance(mutant, dict) else {}
        for trait in mutant.get('traits') or []:
            match = re.search(
                r'защит\w*\s+при\s+атаках\s+со\s+спины\s+по\s+торсу\s*-\s*(\d+)\s*%',
                str(trait), re.IGNORECASE,
            )
            if match:
                base = int(match.group(1))
                break
        variant = mutant.get('variant') if isinstance(mutant.get('variant'), dict) else {}
        for trait in variant.get('traits') or []:
            match = re.search(
                r'физическ\w*\s+защит\w*\s+увеличен\w*\s+на\s*(\d+)\s*%',
                str(trait), re.IGNORECASE,
            )
            if match:
                variant_bonus += int(match.group(1))
        return None if base is None else min(100, base + variant_bonus)

    @staticmethod
    def _mutant_attack_base_cost(profile):
        profile = profile if isinstance(profile, dict) else {}
        raw_effect = str(profile.get('raw_effect') or '')
        match = re.search(r'(\d+)\s*ОД', raw_effect, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return max(0, CombatService._coerce_int(profile.get('action_points'), 0))

    @staticmethod
    def _mutant_first_attack_cost(loc_char, profile, round_number):
        data = (
            loc_char.character.data
            if loc_char and loc_char.character and isinstance(loc_char.character.data, dict)
            else {}
        )
        base_cost = CombatService._mutant_attack_base_cost(profile)
        if not CombatService._mutant_has_trait(data, 'Улучшенные рефлексы'):
            return base_cost
        health = data.get('health') if isinstance(data.get('health'), dict) else {}
        meta = health.get('combatMeta') if isinstance(health.get('combatMeta'), dict) else {}
        if CombatService._coerce_int(meta.get('mutantAttackUsedRound'), 0) == round_number:
            return base_cost
        return max(0, base_cost - 1)

    @staticmethod
    def _mutant_camouflage_active(character_data, round_number):
        data = character_data if isinstance(character_data, dict) else {}
        health = data.get('health') if isinstance(data.get('health'), dict) else {}
        meta = health.get('combatMeta') if isinstance(health.get('combatMeta'), dict) else {}
        if not meta.get('mutantCamouflageActive'):
            return False
        return CombatService._coerce_int(
            meta.get('mutantCamouflageUntilRound'), 0,
        ) >= max(1, CombatService._coerce_int(round_number, 1))

    @staticmethod
    def _mutant_opening_ambush_eligible(character_data):
        data = character_data if isinstance(character_data, dict) else {}
        health = data.get('health') if isinstance(data.get('health'), dict) else {}
        meta = health.get('combatMeta') if isinstance(health.get('combatMeta'), dict) else {}
        return bool(
            meta.get('mutantAmbushActive')
            and CombatService._mutant_has_trait(data, 'Атака из засады')
        )

    @staticmethod
    def _psi_armor_bonus(character_data):
        data = character_data if isinstance(character_data, dict) else {}
        equipment = data.get('equipment') if isinstance(data.get('equipment'), dict) else {}
        total = 0.0
        details = []

        armor = equipment.get('armor')
        if isinstance(armor, dict) and not armor.get('protectionDisabled'):
            attributes = CombatService._template_attributes(armor)
            protection = armor.get('protection')
            if not isinstance(protection, dict):
                protection = attributes.get('protection') if isinstance(attributes.get('protection'), dict) else {}
            explicit = CombatService._protection_percent(protection.get('psi'), 0)
            name = str(armor.get('name') or '').strip().casefold().replace('ё', 'е')
            if explicit:
                bonus = explicit
            elif 'гроб' in name:
                bonus = 50
            elif 'купол-м' in name or 'купол м' in name:
                bonus = 40
            elif 'купол' in name:
                bonus = 25
            elif 'химзащит' in name:
                bonus = 5
            else:
                bonus = 0
            if bonus:
                total += bonus
                details.append({'slot': 'armor', 'name': armor.get('name'), 'bonus': bonus})

        for slot in ('gasMask', 'helmet'):
            item = equipment.get(slot)
            if not isinstance(item, dict) or item.get('protectionDisabled'):
                continue
            attributes = CombatService._template_attributes(item)
            if not CombatService._is_gas_mask_item(slot, item, attributes):
                continue
            durability = CombatService._coerce_float(
                item.get(
                    'durability',
                    item.get(
                        'maxDurability',
                        attributes.get('max_durability', attributes.get('durability', 0)),
                    ),
                ),
                0,
            )
            if durability <= 0:
                continue
            protection = item.get('protection')
            if not isinstance(protection, dict):
                protection = attributes.get('protection') if isinstance(attributes.get('protection'), dict) else {}
            explicit = CombatService._protection_percent(protection.get('psi'), 0)
            name = str(item.get('name') or '').strip().casefold().replace('ё', 'е')
            bonus = explicit or (10 if 'гп-5' in name or 'гп 5' in name else 5)
            total += bonus
            details.append({'slot': slot, 'name': item.get('name'), 'bonus': bonus})
        return total, details

    @staticmethod
    def _psi_defense_profile(character_data,target=None):
        data = character_data if isinstance(character_data, dict) else {}
        will_bonus = CombatService._base_skill_modifier(
            data, 'skills.physical.will',
        )
        will_protection = will_bonus * character_passives(data)['psi_will_multiplier']
        artifact_protection = CombatService._coerce_float(
            artifact_passive_profile(data)['protection'].get('psi'), 0,
        )
        armor_protection, armor_details = CombatService._psi_armor_bonus(data)
        temporary_protection = CombatService._consumable_stat_value_bonus(
            data, 'psy_defense',
        )
        total = max(
            -100,
            min(
                100,
                will_protection + artifact_protection
                + armor_protection + temporary_protection,
            ),
        )
        return {
            'total': total,
            'will_bonus': will_bonus,
            'will': will_protection,
            'artifacts': artifact_protection,
            'armor': armor_protection,
            'temporary': temporary_protection,
            'equipment': armor_details,
        }

    @staticmethod
    def _psi_suicide_melee_attack(weapon):
        attributes = CombatService._template_attributes(weapon)
        attacks = attributes.get('allowed_attacks')
        if not isinstance(attacks,list) or not attacks:
            attacks = [attributes.get('melee_damage_type') or attributes.get('attack_type') or weapon.get('attackType')]
        for tokens in (('руб','chop','slash'),('кол','pierc','thrust'),('реж','cut'),('всп','rip')):
            for attack in attacks:
                name = str(attack or '').casefold()
                if not any(token in name for token in ('круг','circular','crush','дроб')) and any(token in name for token in tokens):
                    return attack
        return None

    @staticmethod
    def _psi_suicide_weapon(character_data, loc_char=None):
        data = character_data if isinstance(character_data, dict) else {}
        weapons = data.get('weapons') if isinstance(data.get('weapons'), list) else []
        index = CombatService._coerce_int(getattr(loc_char,'drawn_weapon_index',None)
            if loc_char is not None else data.get('activeWeaponIndex'),-1)
        # В модели один активный слот оружия. Не достаём другое оружие из инвентаря.
        if 0 <= index < len(weapons) and isinstance(weapons[index],dict):
            weapon = weapons[index]
            attributes = CombatService._template_attributes(weapon)
            if attributes.get('natural_weapon') or str(weapon.get('name') or '').strip().casefold() in {'кулаки','кулак','fists','unarmed'}:
                return None,None,None
            template = CombatService._weapon_template(weapon)
            category = str(
                (template.category if template else None)
                or weapon.get('category') or ''
            ).strip().casefold()
            if category in {'firearm', 'weapon', 'ranged_weapon'}:
                return index, weapon, 'gunshot'
            if CombatService._psi_suicide_melee_attack(weapon):
                return index, weapon, 'cut_throat'
        return None, None, None

    @staticmethod
    def _resolve_psi_suicide_attempt(character_data, loc_char=None, *, round_number=0):
        data = character_data if isinstance(character_data, dict) else {}
        weapon_index, weapon, kind = CombatService._psi_suicide_weapon(
            data, loc_char,
        )
        if not weapon:
            return {
                'attempted': False, 'success': False,
                'failure_reason': 'no_suitable_weapon',
            }
        result = {
            'attempted': True, 'success': False, 'kind': kind,
            'weapon_index': weapon_index, 'weapon': weapon.get('name'),
        }
        if kind == 'cut_throat':
            attack_type = CombatService._psi_suicide_melee_attack(weapon)
            profile = CombatService._weapon_damage_profile(weapon,attack_type)
            if not profile.get('skip_strength_scaling'):
                profile['damage'] *= max(0,1+.1*CombatService._skill_modifier(data,'skills.physical.strength'))
            damage = max(0,round(profile['damage']))
            result.update(attack_type=attack_type,damage=damage,zone='head',ignores_armor=True)
            if loc_char is None:
                raise ValidationError('Для исполнения удара нужен персонаж локации')
            health = CombatService._apply_attack_damage(loc_char,damage,'head',profile,
                round_number=round_number,trauma_checks=0,stress_trigger='psy_state_suicide')
            data = loc_char.character.data
            if damage > 0 and (health['zones']['head']['current'] <= 0 or health['current'] <= 0):
                apply_effect_to_health(health,{'type':'death','name':'Смерть','source':'psy_state_suicide','area':'head','tick':'manual'})
            result['success'] = CombatService._character_condition(data)['state'] == 'dead'
            if not result['success']:
                result['failure_reason'] = 'insufficient_damage'
            return result

        if CombatService._weapon_jam_effects(weapon)['blocks_fire']:
            result['failure_reason'] = 'weapon_jammed'
            result['existing_jam'] = True
            return result
        if weapon.get('requiresManualCycle'):
            result['failure_reason'] = 'weapon_not_cycled'
            return result
        if CombatService._weapon_loaded_shots(weapon) <= 0:
            result['failure_reason'] = 'weapon_unloaded'
            return result

        attack_roll = random.randint(1, 20)
        jam = CombatService._roll_character_weapon_jam(data,weapon,attack_roll)
        result['attack_roll'] = attack_roll
        result['jam'] = jam
        if isinstance(jam, dict) and jam.get('triggered'):
            result['failure_reason'] = 'weapon_jammed'
            return result

        profile, _ = CombatService._ranged_damage_profile(weapon)
        CombatService._consume_weapon_ammo(weapon, 1)
        result['ammo_consumed'] = 1
        result['weapon_wear'] = CombatService._weapon_use_wear(
            weapon, fire_mode='single', shot_count=1, ammo_profile=profile,
        )
        armor_data = dict(data)
        armor_data['_headProtectionZone'] = 'ears'
        armor, armor_layers = CombatService._target_armor(armor_data, 'head',**({'target':loc_char} if background_is(armor_data,'Стойкий') else {}))
        penetration = CombatService._coerce_float(
            profile.get('armor_piercing'), 0,
        )
        result['armor_zone'] = 'ears'
        result['armor'] = armor
        result['armor_piercing'] = penetration
        result['armor_penetrated'] = penetration >= armor
        armor_damage = []
        crystal_layers=[layer['item'] for layer in armor_layers if layer.get('liquid_crystal_first')]
        for layer in armor_layers:
            if layer.get('is_gas_mask'):
                damage_result = CombatService._damage_gas_mask(
                    layer['item'], 'bullet',
                )
            else:
                damage_result = CombatService._damage_armor_item(
                    layer['item'], layer['attributes'], profile.get('damage', 0),
                )
            if damage_result:
                armor_damage.append(damage_result)
        if crystal_layers:
            from app.services.equipment_care import consume_liquid_crystal
            for item in crystal_layers:consume_liquid_crystal(item)
        result['armor_damage'] = armor_damage
        if result['armor_penetrated']:
            apply_effect_to_health(data.setdefault('health', {}), {
                'type': 'death', 'name': 'Смерть',
                'source': 'psy_state_suicide', 'area': 'brain', 'tick': 'manual',
            })
            result['success'] = True
            return result

        result['failure_reason'] = 'armor_not_penetrated'
        deficit = max(0, armor - penetration)
        multiplier = max(0.0, 1 - math.ceil(deficit / 5) * 0.25)
        if multiplier <= 0:
            multiplier = CombatService._behind_armor_damage_multiplier(
                profile, target_data=data, penetration_deficit=deficit,
            )
        behind_armor_damage = max(
            0,
            round(CombatService._coerce_float(profile.get('damage'), 0) * multiplier),
        )
        if crystal_layers:behind_armor_damage=0
        result['behind_armor_damage'] = behind_armor_damage
        if behind_armor_damage and loc_char is not None:
            CombatService._apply_attack_damage(
                loc_char, behind_armor_damage, 'head', profile,
                round_number=round_number, allow_bleeding=False, trauma_checks=0,
                head_impact={
                    'part': 'ear', 'direction': 'side', 'armor_zone': 'ears',
                    'damage_head': False, 'damage_head_if_penetrated': True,
                    'damage_skull_if_penetrated': False, 'primary_organ': None,
                    'secondary_organs_if_penetrated': ['brain'],
                },
                armor_penetrated=False,
            )
        return result

    @staticmethod
    def _transform_into_zombie(loc_char, previous_data):
        if loc_char is None or not getattr(loc_char, 'character', None):
            return None
        from app.services.world_rules import mutant_character_data, mutant_profile

        profile = mutant_profile('Зомбированный')
        if not profile:
            return None
        zombie_data = mutant_character_data(profile)
        original_data=deepcopy(previous_data)
        original_data.pop('psiZombification',None)
        zombie_data['psiZombification']={
            'source':'psi_crisis','previousData':original_data,
            'previousController':getattr(loc_char,'controlled_by',None),
        }
        for key in ('inventory', 'equipment'):
            if isinstance(previous_data.get(key), (dict, list)):
                zombie_data[key] = deepcopy(previous_data[key])
        previous_weapons = previous_data.get('weapons')
        if isinstance(previous_weapons, list):
            zombie_data['weapons'].extend(deepcopy(previous_weapons))
        zombie_data.setdefault('basic', {})['former_character_id'] = loc_char.character_id
        zombie_data['basic']['former_name'] = loc_char.character.name
        previous_data.clear()
        previous_data.update(zombie_data)
        loc_char.character.data = previous_data
        lobby = getattr(getattr(loc_char, 'location', None), 'lobby', None)
        if lobby is not None:
            loc_char.controlled_by = lobby.gm_id
        loc_char.posture = 'prone'
        loc_char.drawn_weapon_index = None
        loc_char.cover_object_id = None
        loc_char.weapon_braced = False
        loc_char.braced_weapon_index = None
        flag_modified(loc_char.character, 'data')
        return {'mutant_type': 'Зомбированный', 'controlled_by_gm': lobby is not None}

    @staticmethod
    def _resolve_psi_crisis(character_data, loc_char=None, *, round_number=0):
        """Сброс после порога — только в конце собственного обычного хода."""
        data = character_data if isinstance(character_data, dict) else {}
        health = data.setdefault('health', {})
        psy_state = max(
            0,
            CombatService._coerce_float(
                health.get('psyState', health.get('psy_state')), 0,
            ),
        )
        if psy_state < 50 or CombatService._character_condition(data)['state'] == 'dead':
            return None
        maximum = max(0, CombatService._coerce_float(health.get('max'), 700))
        reset_damage = 0
        transformed = None
        if psy_state >= 50:
            health['psyState'] = 0
            health['psy_state'] = 0
            reset_damage = psy_state * 10
            health['current'] = max(
                0,
                CombatService._coerce_float(health.get('current'), maximum) - reset_damage,
            )
            CombatService._apply_damage_pain(data,reset_damage,round_number,'psy_state_reset')
            health.setdefault('combatMeta',{})['injuryRound'] = round_number
            sync_health_derived_statuses(health)
            if health['current'] <= 0:
                apply_effect_to_health(health, {
                    'type': 'death', 'name': 'Смерть',
                    'source': 'psy_state_reset', 'tick': 'manual',
                })
                transformed = CombatService._transform_into_zombie(loc_char, data)
        if loc_char is not None and CombatService._character_condition(
            loc_char.character.data if transformed else data,
        )['state'] in {'critical', 'dead'}:
            loc_char.posture = 'prone'
        return {
            'triggered': True,
            'psy_state': psy_state,
            'suicide_attempt': None,
            'suicide_succeeded': False,
            'reset_damage': reset_damage,
            'transformed': transformed,
        }

    @staticmethod
    def _resolve_psi_turn_start(loc_char, round_number):
        if not loc_char.character or not isinstance(loc_char.character.data,dict):
            return None
        data = loc_char.character.data
        health = data.setdefault('health',{})
        if CombatService._coerce_float(health.get('psyState',health.get('psy_state')),0) < 50 or CombatService._character_condition(data)['state'] == 'dead':
            return None
        attempt = CombatService._resolve_psi_suicide_attempt(data,loc_char,round_number=round_number)
        # Общий обработчик ранения может заменить верхний словарь данных.
        data = loc_char.character.data
        data.setdefault('health',{}).setdefault('combatMeta',{})['lastPsiTurnStart'] = {
            'round':round_number,'attempt':attempt,
        }
        flag_modified(loc_char.character,'data')
        return attempt

    @staticmethod
    def _increase_psy_state(character_data, amount, source, loc_char=None, apply_protection=True):
        data = character_data if isinstance(character_data, dict) else {}
        health = data.setdefault('health', {})
        before = max(
            0, CombatService._coerce_float(
                health.get('psyState', health.get('psy_state')), 0,
            ),
        )
        incoming = CombatService._coerce_float(amount, 0)
        defense = (CombatService._psi_defense_profile(data,loc_char) if background_is(data,'Стойкий') else CombatService._psi_defense_profile(data)) if incoming > 0 and apply_protection else {
            'total': 0, 'will_bonus': 0, 'will': 0, 'artifacts': 0,
            'armor': 0, 'temporary': 0, 'equipment': [],
        }
        received = incoming * (1 - defense['total'] / 100) if incoming > 0 else incoming
        received = round(received*(character_passives(data)['psy_state_multiplier'] if received>0 else 1), 2)
        after = max(0, round(before + received, 2))
        health['psyState'] = after
        health['psy_state'] = after
        stored_after = CombatService._coerce_float(
            health.get('psyState', health.get('psy_state')), 0,
        )
        return {
            'before': before,
            'after': stored_after,
            'amount': received,
            'net_change': round(stored_after - before, 2),
            'incoming': incoming,
            'received': received,
            'prevented': round(incoming - received, 2) if incoming > 0 else 0,
            'defense': defense,
            'source': source,
            'crisis': None,
        }

    @staticmethod
    def _mutant_has_direct_sight(location_id, attacker, target):
        if not CombatService._is_in_facing_arc(attacker, target.pos_x, target.pos_y):
            return False
        analysis = CombatService._cover_analysis(location_id, attacker, target)
        return bool(analysis.get('targetable'))

    @staticmethod
    def _mutant_fear_profile(character_data):
        for trait in CombatService._mutant_traits(character_data):
            if 'Пугливость' not in trait:
                continue
            noise_match = re.search(r'шумом\s+больше\s+(\d+)', trait, re.IGNORECASE)
            difficulty_match = re.search(r'СЛ\s*(\d+)', trait, re.IGNORECASE)
            if noise_match and difficulty_match:
                return {
                    'noise_threshold': int(noise_match.group(1)),
                    'difficulty': int(difficulty_match.group(1)),
                }
        return None

    @staticmethod
    def _flee_mutant_from_noise(location, mutant, source, round_number):
        data = mutant.character.data if mutant.character and isinstance(mutant.character.data, dict) else {}
        profile = CombatService._mutant_fear_profile(data)
        if not profile:
            return None
        check = CombatService._mutant_saving_throw(
            mutant, 'skills.physical.will', profile['difficulty'],
        )
        result = {
            'character_id': mutant.character_id,
            'name': mutant.character.name if mutant.character else 'Мутант',
            'check': check,
            'fled': False,
            'distance': 0,
        }
        if check['success']:
            return result

        dx = 0 if mutant.pos_x == source.pos_x else (1 if mutant.pos_x > source.pos_x else -1)
        dy = 0 if mutant.pos_y == source.pos_y else (1 if mutant.pos_y > source.pos_y else -1)
        if dx == 0 and dy == 0:
            dx, dy = 0, 1
        mutant.facing_x, mutant.facing_y = dx, dy
        blocked, _ = CombatService._build_movement_map(
            location, mutant.character_id,
        )
        movement_gain = max(
            0, DEFAULT_CONVERSION_BASE - CombatService._movement_penalty(mutant),
        )
        budget = max(0, mutant.movement_points_current) + max(
            0, mutant.action_points_current,
        ) * movement_gain
        start_x, start_y = mutant.pos_x, mutant.pos_y
        for _ in range(budget):
            candidates = []
            for step_x in (-1, 0, 1):
                for step_y in (-1, 0, 1):
                    if step_x == 0 and step_y == 0:
                        continue
                    destination = (mutant.pos_x + step_x, mutant.pos_y + step_y)
                    if (
                        destination in blocked
                        or not (0 <= destination[0] < location.grid_width)
                        or not (0 <= destination[1] < location.grid_height)
                    ):
                        continue
                    distance = math.hypot(
                        destination[0] - source.pos_x,
                        destination[1] - source.pos_y,
                    )
                    candidates.append((distance, destination))
            if not candidates:
                break
            _, destination = max(candidates, key=lambda item: item[0])
            if math.hypot(
                destination[0] - source.pos_x,
                destination[1] - source.pos_y,
            ) <= math.hypot(
                mutant.pos_x - source.pos_x,
                mutant.pos_y - source.pos_y,
            ):
                break
            mutant.pos_x, mutant.pos_y = destination
            blocked.add(destination)
            result['distance'] += 1

        mutant.action_points_current = 0
        mutant.movement_points_current = 0
        mutant.movement_mode_this_turn = 'run'
        mutant.movement_distance_this_turn = max(
            mutant.movement_distance_this_turn or 0,
            result['distance'],
        )
        mutant.strenuous_movement_blocked_until_round = max(
            mutant.strenuous_movement_blocked_until_round or 0,
            round_number + 1,
        )
        CombatService._clear_aim(mutant)
        result['fled'] = True
        result['from'] = {'x': start_x, 'y': start_y}
        result['to'] = {'x': mutant.pos_x, 'y': mutant.pos_y}
        return result

    @staticmethod
    def _resolve_mutant_fear_from_noise(location, source, noise, round_number):
        noise = CombatService._coerce_float(noise, 0)
        results = []
        for mutant in CombatService._unique_location_characters(
            LocationCharacter.query.filter_by(location_id=location.id).all()
        ):
            if mutant.id == source.id or not mutant.character:
                continue
            data = mutant.character.data if isinstance(mutant.character.data, dict) else {}
            profile = CombatService._mutant_fear_profile(data)
            if (
                not profile
                or noise <= profile['noise_threshold']
                or CombatService._location_character_condition(mutant)['state'] == 'dead'
            ):
                continue
            resolved = CombatService._flee_mutant_from_noise(
                location, mutant, source, round_number,
            )
            if resolved:
                results.append(resolved)
        return results

    @staticmethod
    def _resolve_mutant_psy_end_turn(location, state, loc_char):
        data = loc_char.character.data if loc_char.character and isinstance(loc_char.character.data, dict) else {}
        health = data.setdefault('health', {})
        effects = normalize_effect_list(health.get('effects') or [])
        result = {'zombification': None, 'death_order': False, 'psy_fields': []}

        death_order = next((
            item for item in effects
            if item.get('active', True) and item.get('type') == 'death_order'
        ), None)
        if death_order:
            death_order['active'] = False
            effects.append({
                'type': 'death', 'name': 'Смерть',
                'source': 'death_order', 'active': True,
            })
            loc_char.posture = 'prone'
            result['death_order'] = True

        zombification = next((
            item for item in effects
            if item.get('active', True) and item.get('type') == 'zombification'
        ), None)
        if zombification and not death_order:
            save = CombatService._mutant_saving_throw(
                loc_char, 'skills.physical.will', 14,
            )
            result['zombification'] = save
            if save['success']:
                zombification['active'] = False
                meta = health.setdefault('combatMeta', {})
                loc_char.controlled_by = meta.pop(
                    'zombificationPreviousController', None,
                )
                meta.pop('zombificationController', None)

        participant_ids = set(state.turn_order or []) if state else None
        for psyionik in CombatService._unique_location_characters(
            LocationCharacter.query.filter_by(location_id=location.id).all()
        ):
            if (
                psyionik.id == loc_char.id
                or (participant_ids is not None and psyionik.id not in participant_ids)
                or not psyionik.character
                or not CombatService._are_opponents(psyionik, loc_char)
                or math.hypot(psyionik.pos_x - loc_char.pos_x, psyionik.pos_y - loc_char.pos_y) > 10
            ):
                continue
            psy_data = psyionik.character.data if isinstance(psyionik.character.data, dict) else {}
            if not CombatService._mutant_has_trait(psy_data, 'Пси-Поле'):
                continue
            psy_meta = psy_data.get('health', {}).get('combatMeta', {})
            if psy_meta.get('psyFieldDisabled'):
                continue
            save = CombatService._mutant_saving_throw(
                loc_char, 'skills.physical.will', 15,
            )
            applied = None
            if not save['success']:
                applied = CombatService._increase_psy_state(
                    data, 5, 'psy_field', loc_char=loc_char,
                )
            result['psy_fields'].append({
                'source_character_id': psyionik.character_id,
                'source_name': psyionik.character.name,
                'save': save,
                'psy_state': applied,
            })

        health['effects'] = effects
        sync_health_derived_statuses(health)
        loc_char.character.data = data
        flag_modified(loc_char.character, 'data')
        return result

    @staticmethod
    def _evaporate_dead_psi_clones(location_id, state=None):
        removed = []
        for loc_char in LocationCharacter.query.filter_by(location_id=location_id).all():
            data = loc_char.character.data if loc_char.character and isinstance(loc_char.character.data, dict) else {}
            if (
                not data.get('basic', {}).get('is_psi_clone')
                or CombatService._location_character_condition(loc_char)['state'] != 'dead'
            ):
                continue
            removed.append({
                'location_character_id': loc_char.id,
                'character_id': loc_char.character_id,
                'name': loc_char.character.name if loc_char.character else 'Копия',
            })
            if state:
                state.turn_order = [
                    value for value in (state.turn_order or []) if value != loc_char.id
                ]
            character = loc_char.character
            db.session.delete(loc_char)
            if character:
                db.session.delete(character)
        return removed

    @staticmethod
    def _throwable_object_weight(obj):
        if not obj:
            return 0.0
        properties = obj.properties if isinstance(obj.properties, dict) else {}
        explicit = CombatService._coerce_float(properties.get('weight_kg'), 0)
        if explicit > 0:
            return explicit
        return CombatService.THROWABLE_OBJECT_WEIGHTS.get(
            str(obj.type or '').strip().lower(), 0.0,
        )

    @staticmethod
    def _nearest_throwable_object(location_id, target, maximum_weight=None):
        candidates = []
        for obj in LocationObject.query.filter_by(location_id=location_id).all():
            weight = CombatService._throwable_object_weight(obj)
            if weight <= 0 or (maximum_weight is not None and weight > maximum_weight):
                continue
            distance = math.hypot(obj.tile_x - target.pos_x, obj.tile_y - target.pos_y)
            candidates.append((distance, obj.id, obj, weight))
        if not candidates:
            return None, 0.0
        _, _, obj, weight = min(candidates, key=lambda item: (item[0], item[1]))
        return obj, weight

    @staticmethod
    def _retreat_blood_stage(stage):
        order = ['normal', 'light', 'medium', 'severe', 'critical', 'fatal']
        current = str(stage or 'normal').lower()
        if current not in order:
            current = 'normal'
        return order[max(0, order.index(current) - 1)]

    @staticmethod
    def _mutant_saving_throw(loc_char, skill_path, difficulty):
        data = (
            loc_char.character.data
            if loc_char and loc_char.character and isinstance(loc_char.character.data, dict)
            else {}
        )
        CombatService._refresh_mutant_pack_bonus(loc_char)
        advantage = CombatService._has_roll_advantage(data, skill_path)
        disadvantage = CombatService._has_roll_disadvantage(data, skill_path)
        from app.services.brigadier_order import boost_roll
        rolls,roll,order_bonus=boost_roll(data,[],advantage=advantage,disadvantage=disadvantage)
        bonus = CombatService._skill_modifier(data, skill_path)+order_bonus
        total = roll + bonus
        return {
            'roll': roll,
            'rolls': rolls,
            'bonus': bonus,
            'total': total,
            'difficulty': difficulty,
            'success': roll == 20 or (roll != 1 and total >= difficulty),
            'advantage': advantage,
            'disadvantage': disadvantage,
        }

    @staticmethod
    def _apply_mutant_melee_hit_effects(attacker, target, attack_type, damage, result):
        attacker_data = (
            attacker.character.data
            if attacker and attacker.character and isinstance(attacker.character.data, dict)
            else {}
        )
        target_data = (
            target.character.data
            if target and target.character and isinstance(target.character.data, dict)
            else {}
        )
        if not CombatService._is_mutant_character(attacker_data):
            return
        attack_name = str(attack_type or '').strip().casefold()
        effects = []

        clone_psy_damage = CombatService._coerce_int(
            attacker_data.get('basic', {}).get('psi_clone_damage'), 0,
        )
        if clone_psy_damage > 0:
            psy_state = CombatService._increase_psy_state(
                target_data, clone_psy_damage, 'psi_clone_attack', loc_char=target,
            )
            effects.append({
                'kind': 'psi_clone_attack',
                'psy_state': psy_state,
            })

        if (
            damage > 0
            and 'укус' in attack_name
            and CombatService._mutant_has_trait(attacker_data, 'Ядовитые укусы')
        ):
            check = CombatService._mutant_saving_throw(
                target, 'skills.physical.will', 12,
            )
            effect = {'kind': 'poison_bite', 'save': check}
            if not check['success']:
                health = target_data.setdefault('health', {})
                apply_effect_to_health(health, {
                    'type': 'mutant_poison',
                    'name': 'Яд Слепыша',
                    'remaining': 3,
                    'tick': 'round_end',
                    'source': 'mutant_bite',
                    'rollPenalty': 3,
                })
                effect['applied'] = True
            effects.append(effect)

        if 'сбивание с ног' in attack_name:
            target.posture = 'prone'
            target.cover_object_id = None
            target.weapon_braced = False
            target.braced_weapon_index = None
            CombatService._clear_aim(target)
            effects.append({'kind': 'knockdown', 'posture': 'prone'})

        if (
            'высасывание крови' in attack_name
            and CombatService._mutant_has_trait(attacker_data, 'Вампиризм')
        ):
            attacker_health = attacker_data.setdefault('health', {})
            target_health = target_data.setdefault('health', {})
            attacker_stage = attacker_health.get('blood') or attacker_health.get('bloodStage')
            target_stage = target_health.get('blood') or target_health.get('bloodStage')
            attacker_next = CombatService._retreat_blood_stage(attacker_stage)
            target_next = CombatService._advance_blood_stage(target_stage)
            attacker_health['blood'] = attacker_next
            attacker_health['bloodStage'] = attacker_next
            target_health['blood'] = target_next
            target_health['bloodStage'] = target_next
            modifiers = attacker_health.setdefault('combatMeta', {}).setdefault(
                'bleedingModifiers', [],
            )
            modifiers.append({
                'value': -5,
                'source': 'mutant_vampirism',
                'scope': 'combat',
            })
            effects.append({
                'kind': 'vampirism',
                'attacker_blood_stage': attacker_next,
                'target_blood_stage': target_next,
                'bleeding_severity_modifier': -5,
            })

        if 'атака наповал' in attack_name:
            check = CombatService._mutant_saving_throw(
                target, 'skills.physical.strength', 15,
            )
            effect = {'kind': 'knockout_attack', 'save': check, 'distance': 0}
            if not check['success']:
                location = Location.query.get(getattr(target, 'location_id', None))
                if location:
                    dx = 0 if target.pos_x == attacker.pos_x else (1 if target.pos_x > attacker.pos_x else -1)
                    dy = 0 if target.pos_y == attacker.pos_y else (1 if target.pos_y > attacker.pos_y else -1)
                    blocked, _ = CombatService._build_movement_map(
                        location, target.character_id,
                    )
                    for step in range(1, 6):
                        destination = (target.pos_x + dx, target.pos_y + dy)
                        if (
                            destination in blocked
                            or not (0 <= destination[0] < location.grid_width)
                            or not (0 <= destination[1] < location.grid_height)
                        ):
                            break
                        target.pos_x, target.pos_y = destination
                        effect['distance'] = step
                target.posture = 'prone'
                target.cover_object_id = None
                target.weapon_braced = False
                target.braced_weapon_index = None
                dropped = CombatService._apply_fall_or_drop(
                    target, target_data, 'right_arm',
                )
                apply_effect_to_health(target_data.setdefault('health', {}), {
                    'type': 'concussion',
                    'name': 'Контузия',
                    'remaining': 3,
                    'tick': 'round_end',
                    'source': 'mutant_knockout_attack',
                    'roll_modifier': -3,
                })
                effect.update({'applied': True, 'dropped': dropped})
            effects.append(effect)

        if CombatService._mutant_has_trait(attacker_data, 'Яростные атаки'):
            meta = attacker_data.setdefault('health', {}).setdefault('combatMeta', {})
            meta['mutantRageAccuracy'] = max(
                0, CombatService._coerce_int(meta.get('mutantRageAccuracy'), 0)
            ) + 1
            effects.append({
                'kind': 'rage_accuracy',
                'value': meta['mutantRageAccuracy'],
            })

        attacker.character.data = attacker_data
        target.character.data = target_data
        flag_modified(attacker.character, 'data')
        flag_modified(target.character, 'data')
        if effects:
            result['mutant_effects'] = effects

    @staticmethod
    def _mark_mutant_attack_used(loc_char, round_number):
        if not loc_char or not loc_char.character or not isinstance(loc_char.character.data, dict):
            return
        data = loc_char.character.data
        if not CombatService._mutant_has_trait(data, 'Улучшенные рефлексы'):
            return
        data.setdefault('health', {}).setdefault('combatMeta', {})[
            'mutantAttackUsedRound'
        ] = round_number
        loc_char.character.data = data
        flag_modified(loc_char.character, 'data')

    @staticmethod
    def _is_zhmerka(character_data):
        data = character_data if isinstance(character_data, dict) else {}
        basic = data.get('basic') if isinstance(data.get('basic'), dict) else {}
        mutant = data.get('mutant') if isinstance(data.get('mutant'), dict) else {}
        labels = (
            basic.get('mutant_type'), basic.get('mutant_variant'),
            mutant.get('profile'), (mutant.get('variant') or {}).get('name')
            if isinstance(mutant.get('variant'), dict) else None,
        )
        return any('жмурк' in str(value or '').casefold() for value in labels)

    @staticmethod
    def _can_see_location_character(observer, target, max_distance=25):
        if not observer or not target or observer.id == target.id:
            return False
        distance = max(
            abs(observer.pos_x - target.pos_x),
            abs(observer.pos_y - target.pos_y),
        )
        if distance > max_distance:
            return False
        if not CombatService._is_in_facing_arc(observer, target.pos_x, target.pos_y):
            return False
        return CombatService._cover_analysis(
            observer.location_id, observer, target,
        ).get('grade') != 'full'

    @staticmethod
    def _refresh_ugly_appearance_penalties(location_id):
        characters = CombatService._unique_location_characters(
            LocationCharacter.query.filter_by(location_id=location_id).all()
        )
        ugly_mutants = []
        for loc_char in characters:
            data = (
                loc_char.character.data
                if loc_char.character and isinstance(loc_char.character.data, dict)
                else {}
            )
            for trait in CombatService._mutant_traits(data):
                if 'Уродливый вид' not in trait:
                    continue
                match = re.search(r'штраф\s+(\d+)\s+к броскам Воли', trait, re.IGNORECASE)
                ugly_mutants.append((loc_char, int(match.group(1)) if match else 0))
                break
        for loc_char in characters:
            if not loc_char.character or not isinstance(loc_char.character.data, dict):
                continue
            data = loc_char.character.data
            penalty = 0
            observer_profile = CombatService._mutant_profile_name(data).casefold()
            penalty = max((
                value for ugly, value in ugly_mutants
                if CombatService._mutant_profile_name(
                    ugly.character.data if ugly.character else {}
                ).casefold() != observer_profile
                and CombatService._can_see_location_character(loc_char, ugly)
            ), default=0)
            meta = data.setdefault('health', {}).setdefault('combatMeta', {})
            if CombatService._coerce_int(meta.get('uglyAppearanceWillPenalty'), 0) == penalty:
                continue
            if penalty:
                meta['uglyAppearanceWillPenalty'] = penalty
            else:
                meta.pop('uglyAppearanceWillPenalty', None)
            loc_char.character.data = data
            flag_modified(loc_char.character, 'data')

    @staticmethod
    def _tile_has_bush(location, tile_x, tile_y):
        def is_bush(value):
            if not isinstance(value, dict):
                return False
            properties = value.get('properties') if isinstance(value.get('properties'), dict) else {}
            labels = (
                value.get('type'), value.get('object_type'), value.get('name'),
                properties.get('type'), properties.get('name'), properties.get('model'),
            )
            return any(
                token in str(label or '').casefold()
                for label in labels for token in ('куст', 'bush', 'shrub')
            )

        tiles = location.tiles_data if isinstance(location.tiles_data, list) else []
        if 0 <= tile_y < len(tiles) and isinstance(tiles[tile_y], list) and 0 <= tile_x < len(tiles[tile_y]):
            tile = tiles[tile_y][tile_x]
            if isinstance(tile, dict) and any(is_bush(obj) for obj in tile.get('objects') or []):
                return True
        return any(
            is_bush({'type': obj.type, 'name': obj.name, 'properties': obj.properties or {}})
            for obj in LocationObject.query.filter_by(
                location_id=location.id, tile_x=tile_x, tile_y=tile_y,
            ).all()
        )

    @staticmethod
    def _behind_armor_damage_multiplier(
        profile,
        *,
        target_data=None,
        penetration_deficit=0,
    ):
        profile = profile if isinstance(profile, dict) else {}
        variant = str(profile.get('ammo_variant') or '').strip().lower()
        caliber = CombatService._caliber_key(profile.get('caliber'))
        if CombatService._is_12x70_slug_profile(profile):
            multiplier = 0.10 if variant in {'ep', 'эп', 'rip'} else (1 / 3)
        elif CombatService._is_buckshot_profile(profile):
            multiplier = (
                0.50
                if (
                    CombatService._is_mutant_character(target_data)
                    and CombatService._coerce_float(penetration_deficit, 0) >= 10
                )
                else 0.0
            )
        elif caliber == '127x55':
            multiplier = .10 if variant in {'ep','эп'} else 0.0 if variant == 'rip' else .25
        elif variant in {'ep', 'эп', 'rip'}:
            multiplier = 0.0
        else:
            multiplier = 0.25 if caliber in {'9x39', '127x55'} else 0.20
        if (
            multiplier > 0
            and CombatService._exoskeleton_power_profile(target_data)['is_exoskeleton']
        ):
            multiplier /= 2
        return multiplier

    @staticmethod
    def _armor_covers_zone(slot, item, attributes, zone, head_protection_zone=None):
        zone_group = (
            'head' if zone == 'head'
            else 'torso' if zone in {'chest', 'abdomen'}
            else 'arms' if zone in {'left_arm', 'right_arm'}
            else 'legs'
        )
        name = str(item.get('name') or '').strip().lower()
        if slot == 'armor':
            torso_only = {'армейский бронежилет'}
            torso_and_arms = {
                'кожаная куртка',
                'бандитская куртка',
                'броня путника',
            }
            full_body = {
                'костюм химзащиты',
                'комбинезон купол',
                'комбинезон купол м',
                'комбинезон купол-м',
                'комбинезон гроб',
                'экзоскелет',
            }
            normalized_name = name.replace('ё', 'е')
            if normalized_name in torso_only:
                return zone_group == 'torso'
            if normalized_name in torso_and_arms:
                return zone_group in {'torso', 'arms'}
            if normalized_name in full_body:
                return zone_group in {'torso', 'arms', 'legs', 'head'}
        declared = attributes.get('protection_zones')
        if isinstance(declared, list) and declared:
            normalized = {str(value).strip().lower() for value in declared}
            if zone_group == 'head':
                if head_protection_zone:
                    return bool(
                        'head' in normalized
                        or str(head_protection_zone).strip().lower() in normalized
                    )
                return bool(normalized & {'head', 'crown', 'back', 'ears', 'face'})
            return zone_group in normalized
        return zone == 'head' if slot in {'helmet', 'gasMask'} else zone != 'head'

    @staticmethod
    def _armor_stage_penalty(item, damage_type='physical'):
        stage = max(1, min(5, CombatService._coerce_int(item.get('stage'), 1)))
        penalty = 0
        stage_type=item.get('stage3DamageType') or 'physical'
        if stage >= 3 and damage_type == stage_type:
            from app.services.equipment_modifications import installed
            sealed=any(mod.get('ruleId')=='armor-d11' for mod in installed(item))
            if not (sealed and stage_type in {'thermal','chemical','electric','radiation'}):
                penalty += 10
        if stage >= 4:
            penalty += 10
        if stage >= 5:
            penalty += 25
            penalty += max(0, CombatService._coerce_int(item.get('brokenProtectionLoss'), 0))
        return penalty

    @staticmethod
    def _armor_stage_capacity(item, attributes):
        base_durability = CombatService._coerce_float(
            item.get(
                'durability',
                item.get(
                    'maxDurability',
                    attributes.get('max_durability', attributes.get('durability', 1)),
                ),
            ),
            1,
        )
        material = str(
            item.get('material')
            or attributes.get('material')
            or attributes.get('armor_type')
            or 'композит'
        ).strip().lower()
        coefficient = ARMOR_MATERIAL_COEFFICIENTS.get(material, 1.0)
        return max(1, math.floor(10 * coefficient * max(0, base_durability)))

    @staticmethod
    def _damage_armor_item(item, attributes, incoming_damage, damage_type='physical'):
        damage = max(0, CombatService._coerce_float(incoming_damage, 0))
        if damage <= 0:
            return None
        stage = max(1, min(5, CombatService._coerce_int(item.get('stage'), 1)))
        capacity = max(
            1,
            CombatService._coerce_float(
                item.get('stageDurability'),
                CombatService._armor_stage_capacity(item, attributes),
            ),
        )
        item['stageDurability'] = capacity
        current = CombatService._coerce_float(
            item.get('currentStageDurability'),
            capacity if stage < 5 else 0,
        )
        original_stage = stage
        remaining = damage
        while remaining > 0 and stage < 5:
            available = max(0, current)
            if remaining < available:
                current = available - remaining
                remaining = 0
                break
            remaining -= available
            stage += 1
            if stage == 3:
                kind=str(damage_type or 'physical').strip().lower()
                item['stage3DamageType']=kind if kind in {'physical','thermal','chemical','electric','radiation'} else 'physical'
            current = capacity if stage < 5 else 0

        if stage >= 5 and remaining > 0:
            previous_broken_damage = max(0, CombatService._coerce_float(item.get('brokenDamage'), 0))
            total_broken_damage = previous_broken_damage + remaining
            previous_losses = math.floor(previous_broken_damage / 50)
            total_losses = math.floor(total_broken_damage / 50)
            new_losses = max(0, total_losses - previous_losses)
            item['brokenDamage'] = total_broken_damage
            if new_losses:
                durability = max(
                    0,
                    CombatService._coerce_int(
                        item.get('durability', attributes.get('max_durability', 1)),
                        1,
                    ) - new_losses,
                )
                item['durability'] = durability
                item['brokenProtectionLoss'] = (
                    max(0, CombatService._coerce_int(item.get('brokenProtectionLoss'), 0))
                    + new_losses
                )

        item['stage'] = stage
        item['currentStageDurability'] = max(0, current)
        item['condition'] = ARMOR_STAGE_LABELS[stage - 1]
        return {
            'name': item.get('name'),
            'stage_before': original_stage,
            'stage_after': stage,
            'stage_durability': item['currentStageDurability'],
            'damage': damage,
        }

    @staticmethod
    def _is_gas_mask_item(slot, item, attributes):
        attributes = attributes if isinstance(attributes, dict) else {}
        category = str(item.get('category') or '').strip().lower()
        return bool(
            slot == 'gasMask'
            or category == 'gas_mask'
            or attributes.get('requires_filter')
            or attributes.get('is_gas_mask')
        )

    @staticmethod
    def _uses_gas_mask_durability(slot, item, attributes):
        # Фильтр есть и у бронешлемов; он не меняет их систему износа.
        return (CombatService._is_gas_mask_item(slot,item,attributes)
                and slot != 'helmet' and str(item.get('category') or '').casefold() != 'helmet')

    @staticmethod
    def _damage_gas_mask(item, source):
        if not isinstance(item, dict):
            return None
        source_key = str(source or '').strip().lower()
        durability_damage = 1 if source_key == 'anomaly' else (
            10 if source_key in {'bullet', 'melee'} else 0
        )
        before = max(0, CombatService._coerce_int(item.get('durability'), 0))
        after = max(0, before - durability_damage)
        item['durability'] = after
        item.pop('stage', None)
        item.pop('stageDurability', None)
        item.pop('currentStageDurability', None)
        item.pop('stage3DamageType', None)
        item.pop('brokenDamage', None)
        item.pop('brokenProtectionLoss', None)
        item['condition'] = 'Целый' if after > 0 else 'Сломан'
        return {
            'name': item.get('name'),
            'durability_before': before,
            'durability_after': after,
            'damage': durability_damage,
            'source': source_key,
        }

    @staticmethod
    def _is_gas_or_chemical_profile(profile):
        profile = profile if isinstance(profile, dict) else {}
        values = {
            str(profile.get('damage_type') or '').strip().lower(),
            str(profile.get('ammo_variant') or '').strip().lower(),
            str(profile.get('ammo_kind') or '').strip().lower(),
        }
        return bool(values & {'gas', 'chemical', 'химический', 'газовый', 'газ'})

    @staticmethod
    def _functioning_gas_protection(character_data):
        equipment = (
            character_data.get('equipment')
            if isinstance(character_data, dict)
            and isinstance(character_data.get('equipment'), dict)
            else {}
        )
        for slot in ('gasMask', 'helmet'):
            item = equipment.get(slot)
            if not isinstance(item, dict):
                continue
            attributes = CombatService._template_attributes(item)
            if (
                CombatService._is_gas_mask_item(slot, item, attributes)
                and CombatService._coerce_int(
                    item.get(
                        'durability',
                        item.get(
                            'maxDurability',
                            attributes.get('max_durability', 0),
                        ),
                    ),
                    0,
                ) > 0
            ):
                return item
        return None

    @staticmethod
    def _integrated_helmet_profile(armor):
        if not isinstance(armor, dict):
            return None
        name = str(armor.get('name') or '').strip().lower().replace('ё', 'е')
        fixed_profiles = {
            'костюм химзащиты': (0, 2, 2),
            'комбинезон купол': (10, 3, 3),
            'комбинезон купол м': (35, 2, 3),
            'комбинезон купол-м': (35, 2, 3),
            'комбинезон гроб': (40, 4, 4),
        }
        if name == 'экзоскелет':
            protection = armor.get('protection')
            protection = protection if isinstance(protection, dict) else {}
            physical = max(
                0,
                CombatService._protection_percent(protection.get('physical'), 0) - 10,
            )
            return {
                'physical': physical,
                'charisma_penalty': 0,
                'accuracy_penalty': 2,
            }
        profile = fixed_profiles.get(name)
        if not profile:
            attributes = CombatService._template_attributes(armor)
            configured = attributes.get('integrated_helmet_profile')
            if not isinstance(configured, dict):
                return None
            return {
                'physical': CombatService._protection_percent(configured.get('physical'), 0),
                'charisma_penalty': max(0, CombatService._coerce_int(configured.get('charisma_penalty'), 0)),
                'accuracy_penalty': max(0, CombatService._coerce_int(configured.get('accuracy_penalty'), 0)),
            }
        physical, charisma_penalty, accuracy_penalty = profile
        return {
            'physical': physical,
            'charisma_penalty': charisma_penalty,
            'accuracy_penalty': accuracy_penalty,
        }

    @staticmethod
    def _equipment_accuracy_penalty(character_data):
        equipment = character_data.get('equipment') if isinstance(character_data, dict) else {}
        equipment = equipment if isinstance(equipment, dict) else {}
        armor = equipment.get('armor') if isinstance(equipment.get('armor'), dict) else {}
        integrated_profile = CombatService._integrated_helmet_profile(armor)
        if integrated_profile:
            from app.services.equipment_modifications import gear_stat
            return max(0,gear_stat(armor,'accuracy_penalty',integrated_profile['accuracy_penalty']))

        total = 0
        for slot in ('helmet', 'gasMask'):
            item = equipment.get(slot) if isinstance(equipment.get(slot), dict) else {}
            penalty = item.get('accuracyPenalty')
            if penalty is None:
                penalty = item.get('accuracy_penalty')
            if penalty is None:
                penalty = (item.get('attributes') or {}).get('accuracy_penalty')
            from app.services.equipment_modifications import gear_stat
            total += max(0,gear_stat(item,'accuracy_penalty',CombatService._coerce_float(penalty,0)))
        return total

    @staticmethod
    def _combat_background_active(target,name):
        if not target or not getattr(target,'character',None) or not background_is(target.character.data or {},name):return False
        if not getattr(target,'location_id',None) or not getattr(target,'id',None):return False
        state=LocationCombatState.query.filter_by(location_id=target.location_id).first()
        return bool(state and state.status=='active' and target.id in (state.turn_order or []))

    @staticmethod
    def _target_armor(target_data, zone, head_protection_zone=None,*,target=None,limb_hit=None,explosive=False,plate_side=None,
                      liquid_crystal_context=None):
        if head_protection_zone is None and isinstance(target_data, dict):
            head_protection_zone = target_data.get('_headProtectionZone')
        equipment = target_data.get('equipment') if isinstance(target_data, dict) else {}
        equipment = equipment if isinstance(equipment, dict) else {}
        candidates = []
        armor_item = equipment.get('armor') if isinstance(equipment.get('armor'), dict) else {}
        integrated_profile = (
            CombatService._integrated_helmet_profile(armor_item)
            if zone == 'head'
            else None
        )
        if integrated_profile:
            candidates.append((
                'integratedHelmet',
                armor_item,
                CombatService._template_attributes(armor_item),
                {'physical': integrated_profile['physical']},
            ))
        for slot in ('armor', 'helmet', 'gasMask'):
            item = equipment.get(slot)
            if isinstance(item, dict):
                if integrated_profile and zone == 'head':
                    continue
                attrs = CombatService._template_attributes(item)
                if not CombatService._armor_covers_zone(
                    slot, item, attrs, zone, head_protection_zone,
                ):
                    continue
                protection = item.get('protection')
                if not isinstance(protection, dict):
                    protection = attrs.get('protection') if isinstance(attrs.get('protection'), dict) else {}
                candidates.append((slot, item, attrs, protection))
        zone_group = 'head' if zone == 'head' else ('torso' if zone in {'chest', 'abdomen'} else zone)
        total = 0.0
        details = []
        for slot, item, attrs, protection in candidates:
            if item.get('protectionDisabled'):
                continue
            is_gas_mask = CombatService._uses_gas_mask_durability(slot, item, attrs)
            if is_gas_mask and CombatService._coerce_int(
                item.get(
                    'durability',
                    item.get('maxDurability', attrs.get('max_durability', 0)),
                ),
                0,
            ) <= 0:
                continue
            value = protection.get(zone_group, protection.get('physical', 0))
            parsed = max(0.0, min(100.0, CombatService._protection_percent(value, 0)))
            from app.services.equipment_modifications import stat as modification_stat,rule as modification_rule
            parsed=max(0,min(100,modification_stat(item,'physical',parsed)))
            from app.services.equipment_care import liquid_crystal_ready
            crystal_active=(slot in {'armor','integratedHelmet'} and bool(modification_rule(item,'liquid_crystal_layer'))
                and (liquid_crystal_context.get('active') and liquid_crystal_context.get('item') is item
                     if liquid_crystal_context is not None else liquid_crystal_ready(item)))
            if crystal_active:parsed=min(100,parsed+modification_rule(item,'liquid_crystal_layer'))
            if explosive:parsed=min(100,modification_stat(item,'explosive_physical',parsed))
            if zone=='head' and head_protection_zone=='ears':parsed=min(100,parsed+modification_rule(item,'ear_protection'))
            if not is_gas_mask:
                parsed = max(0.0, parsed - CombatService._armor_stage_penalty(item, 'physical'))
            # Armor protects covered limbs 10 percentage points worse than the body.
            if slot == 'armor' and not modification_rule(item,'full_limb_armor') and zone in {
                'left_arm', 'right_arm', 'left_leg', 'right_leg',
            }:
                parsed = max(0.0, parsed - 10)
            if parsed:
                total = max(total, parsed)
            # Нулевая физзащита не делает надетую ткань неуязвимой для износа.
            if isinstance(item,dict):
                details.append({
                    'slot': slot,
                    'item': item,
                    'attributes': attrs,
                    'protection': parsed,
                    'is_gas_mask': is_gas_mask,
                    'liquid_crystal_first':bool(crystal_active),
                    **({'wear_multiplier':max(0,1-modification_rule(item,'explosion_wear_reduction'))} if explosive else {}),
                })
        if zone in {'chest','abdomen'} and plate_side in {'front','back'}:
            vest=equipment.get('vest')
            if isinstance(vest,dict) and not vest.get('protectionDisabled'):
                plate_pouches=[pouch for pouch in vest.get('pouches') or [] if isinstance(pouch,dict)
                    and any(isinstance(slot,dict) and slot.get('type')=='armor_plate'
                        for slot in (pouch.get('attributes') or {}).get('slots') or [])]
                side_index=0 if plate_side=='front' else 1
                if len(plate_pouches)>side_index:
                    plate=next((module for module in plate_pouches[side_index].get('installedModules') or []
                        if isinstance(module,dict) and module.get('slotType')=='armor_plate'
                        and module.get('category')=='armor_plate'),None)
                    if plate and CombatService._coerce_float(plate.get('durability'),0)>0:
                        attrs=CombatService._template_attributes(plate)
                        protection=(plate.get('protection') if isinstance(plate.get('protection'),dict) else attrs.get('protection') or {})
                        value=max(0,min(100,CombatService._protection_percent(protection.get('physical'),0)))
                        stage=max(1,min(5,CombatService._coerce_int(plate.get('stage'),1)))
                        if stage>=3:value=math.floor(value*.9)
                        if stage>=4:value=math.floor(value*.9)
                        if stage>=5:value=math.floor(value*.75)
                        total=max(total,value)
                        details.append({'slot':'armorPlate','item':plate,'attributes':attrs,
                            'protection':value,'is_gas_mask':False})
        mutant = target_data.get('mutant') if isinstance(target_data, dict) else None
        if isinstance(mutant, dict):
            innate = max(0, min(100, CombatService._coerce_float(
                mutant.get('physical_protection'), 0,
            )))
            if zone == 'head':
                for trait in CombatService._mutant_traits(target_data):
                    match = re.search(
                        r'защит\w*\s+в\s+голове\s+увеличен\w*\s+на\s*(\d+)\s*%',
                        trait,
                        re.IGNORECASE,
                    )
                    if match:
                        innate = min(100, innate + int(match.group(1)))
                        break
            total = max(total, innate)
            if innate:
                details.append({
                    'slot': 'mutant',
                    'item': {'name': 'Природная защита'},
                    'attributes': {'indestructible': True},
                    'protection': innate,
                    'is_gas_mask': False,
                })
        combat_meta = (
            target_data.get('health', {}).get('combatMeta', {})
            if isinstance(target_data, dict)
            else {}
        )
        if isinstance(combat_meta, dict) and combat_meta.get('mutantAnomalyShield'):
            total = max(total, 80.0)
            details.append({
                'slot': 'mutant_anomaly_shield',
                'item': {'name': 'Аномальный щит'},
                'attributes': {'indestructible': True},
                'protection': 80.0,
                'is_gas_mask': False,
            })
        mechanical=bool(limb_hit and limb_hit.get('mechanical'))
        if mechanical:total=min(100,total+limb_hit.get('protection',0))
        artifact_protection = 0 if mechanical else max(
            0,
            artifact_passive_profile(target_data)['protection']['physical'],
        )
        if artifact_protection:
            total = min(100, total + artifact_protection)
            details.append({
                'slot': 'artifact',
                'item': {'name': 'Артефакты'},
                'attributes': {'indestructible': True},
                'protection': artifact_protection,
                'is_gas_mask': False,
            })
        if zone == 'head' and head_protection_zone in {'eyes', 'eye'}:
            from app.services.equipment_passives import eye_protection
            total = max(total, eye_protection(target_data))
        if CombatService._combat_background_active(target,'Стойкий'):total=min(100,total+5)
        return total, details

    @staticmethod
    def _target_elemental_protection(target_data, damage_type, zone='chest',*,target=None):
        equipment = target_data.get('equipment') if isinstance(target_data, dict) else {}
        equipment = equipment if isinstance(equipment, dict) else {}
        best = 0.0
        for slot in ('armor', 'helmet', 'gasMask'):
            item = equipment.get(slot)
            if not isinstance(item, dict):
                continue
            if item.get('protectionDisabled'):
                continue
            attributes = CombatService._template_attributes(item)
            if not CombatService._armor_covers_zone(slot, item, attributes, zone):
                continue
            protection = item.get('protection')
            if not isinstance(protection, dict):
                protection = attributes.get('protection') if isinstance(attributes.get('protection'), dict) else {}
            parsed = CombatService._protection_percent(protection.get(damage_type), 0)
            from app.services.equipment_modifications import stat as modification_stat
            parsed=modification_stat(item,damage_type,parsed)+modification_stat(item,'anomaly',0)
            if not CombatService._uses_gas_mask_durability(slot, item, attributes):
                parsed = max(
                    0,
                    parsed - CombatService._armor_stage_penalty(item, damage_type),
                )
            best = max(
                best,
                parsed,
            )
        mutant = target_data.get('mutant') if isinstance(target_data, dict) else None
        if isinstance(mutant, dict):
            best = max(best, CombatService._coerce_float(
                mutant.get('anomaly_protection'), 0,
            ))
        best += artifact_passive_profile(target_data)['protection'].get(
            damage_type, 0,
        )
        if CombatService._combat_background_active(target,'Стойкий'):best+=5
        if damage_type == 'electric' and background_is(target_data, 'Радиолюбитель'):
            best -= 10
        if damage_type in {'thermal', 'electric', 'chemical', 'radiation'}:
            from app.services.equipment_passives import glove_protection
            best += glove_protection(target_data)
        return max(0, min(100, best))

    @staticmethod
    def _equipped_radiation_protection(character_data,target=None):
        """Sum ambient radiation protection from all worn protective equipment."""
        equipment = character_data.get('equipment') if isinstance(character_data, dict) else {}
        equipment = equipment if isinstance(equipment, dict) else {}
        total = 0.0
        for slot in ('armor', 'helmet', 'gasMask'):
            item = equipment.get(slot)
            if not isinstance(item, dict) or item.get('protectionDisabled'):
                continue
            attributes = CombatService._template_attributes(item)
            is_gas_mask = CombatService._is_gas_mask_item(slot, item, attributes)
            if is_gas_mask and CombatService._coerce_float(
                item.get(
                    'durability',
                    item.get(
                        'maxDurability',
                        attributes.get('max_durability', attributes.get('durability', 0)),
                    ),
                ),
                0,
            ) <= 0:
                continue
            protection = item.get('protection')
            if not isinstance(protection, dict):
                protection = (
                    attributes.get('protection')
                    if isinstance(attributes.get('protection'), dict)
                    else {}
                )
            value = CombatService._protection_percent(protection.get('radiation'), 0)
            from app.services.equipment_modifications import stat as modification_stat
            value=modification_stat(item,'radiation',value)+modification_stat(item,'anomaly',0)
            from app.services.equipment_modifications import rule as modification_rule
            if CombatService._functioning_gas_protection(character_data):value+=modification_rule(item,'radiation_with_mask')
            if is_gas_mask:
                from app.services.gas_mask_filters import is_gas_mask_filter, filter_charges
                if not any(is_gas_mask_filter(module) and filter_charges(module) > 0
                           for module in item.get('installedModules') or []):
                    value = 0
            if not CombatService._uses_gas_mask_durability(slot, item, attributes):
                value -= CombatService._armor_stage_penalty(item, 'radiation')
            total += max(0.0, value)
        total += max(
            0,
            artifact_passive_profile(character_data)['protection']['radiation'],
        )
        if CombatService._combat_background_active(target,'Стойкий'):total+=5
        from app.services.equipment_passives import glove_protection
        total += glove_protection(character_data)
        return total

    @staticmethod
    def _apply_incoming_radiation(character_data, incoming, *, binary=False,target=None,lobby=None):
        incoming = max(0.0, CombatService._coerce_float(incoming, 0))
        original_incoming=incoming
        weather=weather_at(getattr(target,'location',None),target) if target is not None else weather_profile(lobby.weather_settings) if lobby is not None else {}
        weather_multiplier=weather.get('radiation_multiplier',1)
        incoming*=weather_multiplier
        protection = CombatService._equipped_radiation_protection(character_data,target) if background_is(character_data,'Стойкий') else CombatService._equipped_radiation_protection(character_data)
        required_protection = incoming * 10
        equipment_received = (
            incoming if protection < required_protection else 0.0
        ) if binary else max(0.0, incoming - protection / 10)
        health = character_data.setdefault('health', {})
        filter_result = CombatService._apply_world_radiation_filters(
            health,
            equipment_received,
        ) if binary else {
            'received': equipment_received,
            'prevented': 0.0,
            'effects': [],
            'changed': False,
        }
        received = filter_result['received']
        before = max(0.0, CombatService._coerce_float(health.get('radiation'), 0))
        after = before + received
        if received:
            health['radiation'] = after
        threshold_result = CombatService._apply_radiation_threshold_states(health)
        return {
            'incoming': incoming,
            'original_incoming':original_incoming,'weather_multiplier':weather_multiplier,
            'protection': protection,
            'required_protection': required_protection,
            'equipment_received': equipment_received,
            'filtered': filter_result['prevented'],
            'radiation_filters': filter_result['effects'],
            'received': received,
            'before': before,
            'after': after,
            'critical': threshold_result['critical'],
            'death': threshold_result['death'],
            'changed': bool(
                received or filter_result['changed'] or threshold_result['changed']
            ),
        }

    @staticmethod
    def _apply_world_radiation_filters(health, incoming):
        remaining_dose = max(0.0, CombatService._coerce_float(incoming, 0))
        active_effects = normalize_effect_list(health.get('effects') or [])
        updated_effects = []
        applications = []
        prevented_total = 0.0
        changed = False
        for effect in active_effects:
            if (
                effect.get('type') != 'radiation_filter'
                or not effect.get('active', True)
                or remaining_dose <= 0
            ):
                updated_effects.append(effect)
                continue
            capacity = max(
                0.0,
                CombatService._coerce_float(
                    effect.get('remaining_capacity', effect.get('capacity', 0)),
                    0,
                ),
            )
            if capacity <= 0:
                changed = True
                continue
            percent = max(
                0.0,
                min(100.0, CombatService._coerce_float(effect.get('value'), 0)),
            )
            potential_prevented = remaining_dose * percent / 100
            prevented = min(potential_prevented, capacity)
            remaining_dose = max(0.0, remaining_dose - prevented)
            remaining_capacity = max(0.0, capacity - prevented)
            prevented_total += prevented
            changed = True
            applications.append({
                'name': effect.get('name'),
                'percent': percent,
                'prevented': prevented,
                'remaining_capacity': remaining_capacity,
            })
            if remaining_capacity > 0:
                effect['remaining_capacity'] = remaining_capacity
                updated_effects.append(effect)
        if changed:
            health['effects'] = updated_effects
            sync_health_derived_statuses(health)
        return {
            'received': remaining_dose,
            'prevented': prevented_total,
            'effects': applications,
            'changed': changed,
        }

    @staticmethod
    def _apply_radiation_threshold_states(health):
        radiation = max(0.0, CombatService._coerce_float(health.get('radiation'), 0))
        active_effects = normalize_effect_list(health.get('effects') or [])
        had_critical = any(
            effect.get('type') == 'critical_condition'
            and effect.get('source') == 'radiation_sickness'
            and effect.get('active', True)
            for effect in active_effects
        )
        had_death = any(
            effect.get('type') == 'death'
            and effect.get('source') == 'radiation_sickness'
            and effect.get('active', True)
            for effect in active_effects
        )
        if radiation >= 100 and not had_death:
            apply_effect_to_health(health, {
                'type': 'death',
                'name': 'Смерть от радиации',
                'area': 'whole_body',
                'source': 'radiation_sickness',
                'tick': 'manual',
            })
        elif radiation >= 76 and not had_critical:
            apply_effect_to_health(health, {
                'type': 'critical_condition',
                'name': 'Критическое состояние от радиации',
                'area': 'whole_body',
                'source': 'radiation_sickness',
                'tick': 'manual',
            })
        return {
            'critical': radiation >= 76 and radiation < 100,
            'death': radiation >= 100,
            'changed': bool(
                (radiation >= 100 and not had_death)
                or (76 <= radiation < 100 and not had_critical)
            ),
        }

    @staticmethod
    def _recover_world_travel_pain(character_data):
        health = character_data.get('health') if isinstance(character_data, dict) else None
        if not isinstance(health, dict):
            return {'before': 0, 'after': 0, 'changed': False}
        before = max(0.0, CombatService._coerce_float(health.get('painLevel'), 0))
        if before <= 0:
            return {'before': before, 'after': before, 'changed': False}
        health['painLevel'] = 0
        meta = health.get('combatMeta')
        if isinstance(meta, dict):
            meta.pop('painIncreased', None)
        return {'before': before, 'after': 0, 'changed': True}

    @staticmethod
    def _apply_world_radiation_consequences(character_data):
        """Apply one ten-minute radiation sickness interval before world travel."""
        health = apply_health_maximums(character_data)
        radiation = max(0.0, CombatService._coerce_float(health.get('radiation'), 0))
        damage = 0
        bleeding_stage = None
        bleeding_count = 0
        critical = False
        death = radiation >= 100
        if 21 <= radiation <= 30:
            damage, bleeding_stage, bleeding_count = 20, 'light', 1
        elif 31 <= radiation <= 50:
            damage, bleeding_stage, bleeding_count = 50, 'light', 2
        elif 51 <= radiation <= 60:
            damage, bleeding_stage, bleeding_count = 100, 'medium', 1
        elif 61 <= radiation <= 75:
            damage, bleeding_stage, bleeding_count = 200, 'medium', 2
        elif 76 <= radiation < 100:
            damage, bleeding_stage, bleeding_count = 200, 'severe', 1
            critical = True

        before_health = max(
            0.0,
            CombatService._coerce_float(health.get('current'), health.get('max', 700)),
        )
        if damage:
            health['current'] = max(0.0, before_health - damage)
        created_bleedings = []
        for index in range(bleeding_count):
            apply_effect_to_health(health, {
                'id': uuid.uuid4().hex,
                'type': f'bleeding_external_{bleeding_stage}',
                'name': 'Кровотечение от радиации',
                'area': 'whole_body',
                'value': 1,
                'source': 'radiation_sickness',
                'tick': 'manual',
            })
            created_bleedings.append({
                'kind': 'external',
                'stage': bleeding_stage,
                'area': 'whole_body',
                'index': index + 1,
            })
        if critical:
            apply_effect_to_health(health, {
                'type': 'critical_condition',
                'name': 'Критическое состояние от радиации',
                'area': 'whole_body',
                'source': 'radiation_sickness',
                'tick': 'manual',
            })
        if death:
            apply_effect_to_health(health, {
                'type': 'death',
                'name': 'Смерть от радиации',
                'area': 'whole_body',
                'source': 'radiation_sickness',
                'tick': 'manual',
            })
        sync_health_derived_statuses(health)
        active_effects = normalize_effect_list(health.get('effects') or [])
        return {
            'radiation_at_start': radiation,
            'damage': damage,
            'health_before': before_health,
            'health_after': max(
                0.0,
                CombatService._coerce_float(health.get('current'), before_health),
            ),
            'bleedings': created_bleedings,
            'critical': critical,
            'death': death or any(
                effect.get('type') == 'death' and effect.get('active', True)
                for effect in active_effects
            ),
            'applied': bool(damage or created_bleedings or critical or death),
        }

    @staticmethod
    def _location_tile_radiation(location, tile_x, tile_y):
        tiles = location.tiles_data if isinstance(location.tiles_data, list) else []
        try:
            tile = tiles[int(tile_y)][int(tile_x)]
        except (IndexError, TypeError, ValueError):
            return 0.0
        if not isinstance(tile, dict):
            return 0.0
        return max(0.0, CombatService._coerce_float(tile.get('radiation'), 0))

    @staticmethod
    def _apply_location_end_turn_radiation(location, location_character):
        character = getattr(location_character, 'character', None)
        if not character or not isinstance(character.data, dict):
            return None
        incoming = CombatService._location_tile_radiation(
            location,
            location_character.pos_x,
            location_character.pos_y,
        )
        result = CombatService._apply_incoming_radiation(
            character.data,
            incoming,
            binary=False,
            target=location_character,
        )
        if result['critical'] or result['death']:
            location_character.posture = 'prone'
            location_character.cover_object_id = None
            location_character.weapon_braced = False
            location_character.braced_weapon_index = None
        if result['changed']:
            flag_modified(character, 'data')
        return result

    @staticmethod
    def _normalize_caliber(value):
        return (
            str(value or '').strip().lower()
            .replace('×', 'x').replace('*', 'x').replace('х', 'x')
            .replace(' ', '').replace(',', '.')
        )

    @staticmethod
    def _firearm_bleeding_modifier(profile):
        caliber = CombatService._normalize_caliber(profile.get('caliber'))
        modifier = 0
        if caliber.startswith('18x45'):
            modifier -= 20
        elif caliber.startswith(('9x18', '9x19')):
            modifier -= 1
        elif caliber.startswith(('12x70', '7.62x39', '7.62x51', '7.62x54', '9x39')):
            modifier += 2
        elif caliber.startswith('12.7x55') or 'аккумулятор' in caliber:
            modifier += 3
        variant = str(profile.get('ammo_variant') or '').strip().lower()
        modifier += {
            'ubp': -2,
            'bp': -1,
            'incendiary': -1,
            'ep': 2,
            'rip': 4,
            'explosive': 8,
        }.get(variant, 0)
        return modifier

    @staticmethod
    def _roll_firearm_bleeding(profile, armor, forced_roll=None):
        penetration = CombatService._coerce_float(profile.get('armor_piercing'), 0)
        if armor > 0 and penetration - armor < 10:
            return {
                'roll': None,
                'modifier': CombatService._firearm_bleeding_modifier(profile),
                'total': None,
                'stage': None,
                'blocked_by_armor': True,
            }
        roll = (
            max(1, min(6, CombatService._coerce_int(forced_roll, 1)))
            if forced_roll is not None
            else random.randint(1, 6)
        )
        modifier = CombatService._firearm_bleeding_modifier(profile)
        total = roll + modifier
        if total >= 8:
            stage = 'extreme'
        elif total >= 6:
            stage = 'severe'
        elif total >= 4:
            stage = 'medium'
        elif total >= 3:
            stage = 'light'
        else:
            stage = None
        return {
            'roll': roll,
            'modifier': modifier,
            'total': total,
            'stage': stage,
            'blocked_by_armor': False,
        }

    @staticmethod
    def _attack_direction(attacker, target):
        if attacker is None or target is None:
            return 'front'
        facing_x = CombatService._coerce_float(getattr(target, 'facing_x', 0), 0)
        facing_y = CombatService._coerce_float(getattr(target, 'facing_y', 1), 1)
        relative_x = CombatService._coerce_float(getattr(attacker, 'pos_x', 0), 0) - CombatService._coerce_float(getattr(target, 'pos_x', 0), 0)
        relative_y = CombatService._coerce_float(getattr(attacker, 'pos_y', 0), 0) - CombatService._coerce_float(getattr(target, 'pos_y', 0), 0)
        facing_length = math.hypot(facing_x, facing_y)
        relative_length = math.hypot(relative_x, relative_y)
        if facing_length <= 1e-9 or relative_length <= 1e-9:
            return 'front'
        cosine = (facing_x * relative_x + facing_y * relative_y) / (
            facing_length * relative_length
        )
        if cosine >= 0.5:
            return 'front'
        if cosine <= -0.5:
            return 'back'
        return 'side'

    @staticmethod
    def _head_impact_profile(roll, attacker=None, target=None):
        parts = {
            1: ('jaw', 'jaw'),
            2: ('rightCheek', None),
            3: ('crown', None),
            4: ('rightEye', 'rightEye'),
            5: ('crown', None),
            6: ('nose', 'nose'),
            8: ('rightEar', 'rightEar'),
            10: ('crown', None),
            11: ('crown', None),
            12: ('leftCheek', None),
            13: ('jaw', 'jaw'),
            14: ('leftEye', 'leftEye'),
            15: ('crown', None),
            16: ('nose', 'nose'),
            17: ('jaw', 'jaw'),
            18: ('leftCheek', None),
            19: ('crown', None),
            20: ('leftEar', 'leftEar'),
        }
        part, primary_organ = parts.get(CombatService._coerce_int(roll, 0), ('head', None))
        direction = CombatService._attack_direction(attacker, target)
        is_eye = part in {'rightEye', 'leftEye'}
        is_ear = part in {'rightEar', 'leftEar'}
        is_cheek = part in {'rightCheek', 'leftCheek'}

        profile = {
            'part': part,
            'direction': direction,
            'armor_zone': 'crown',
            'damage_head': True,
            'damage_head_if_penetrated': False,
            'damage_skull_if_penetrated': part == 'crown',
            'primary_organ': primary_organ,
            'secondary_organs_if_penetrated': [],
        }
        if part in {'crown', 'head'}:
            return profile

        if is_ear:
            profile.update({
                'armor_zone': 'ears',
                'damage_head': False,
                'damage_head_if_penetrated': direction == 'side',
            })
            if direction == 'side':
                profile['secondary_organs_if_penetrated'] = ['brain']
            return profile

        if direction == 'back':
            profile.update({
                'armor_zone': 'back',
                'damage_head': True,
                'primary_organ': None,
            })
            if is_eye:
                profile['damage_skull_if_penetrated'] = True
                profile['secondary_organs_if_penetrated'] = ['brain']
            return profile

        if direction == 'side':
            profile.update({
                'armor_zone': 'ears' if is_eye else 'face',
                'damage_head': False,
                'damage_head_if_penetrated': is_eye,
            })
            if is_eye:
                profile['secondary_organs_if_penetrated'] = ['brain']
            elif is_cheek:
                profile['primary_organ'] = 'jaw'
            return profile

        profile['armor_zone'] = 'face'
        profile['damage_head'] = True
        return profile

    @staticmethod
    def _apply_fall_or_drop(target, character_data, zone):
        if zone in {'left_leg', 'right_leg', 'leftLeg', 'rightLeg'}:
            target.posture = 'prone'
            target.cover_object_id = None
            target.weapon_braced = False
            target.braced_weapon_index = None
            CombatService._clear_aim(target)
            return {'kind': 'fall', 'posture': 'prone'}

        if zone not in {'left_arm', 'right_arm', 'leftArm', 'rightArm'}:
            return None
        weapons = character_data.get('weapons')
        weapon_index = CombatService._coerce_int(
            getattr(target, 'drawn_weapon_index', -1), -1,
        )
        if not isinstance(weapons, list) or not (0 <= weapon_index < len(weapons)):
            return {'kind': 'drop_weapon', 'dropped': False}

        weapon = weapons.pop(weapon_index)
        wear = CombatService._apply_weapon_wear(weapon, 3)
        character_data.pop('activeWeaponIndex', None)
        target.drawn_weapon_index = None
        target.weapon_braced = False
        target.braced_weapon_index = None
        CombatService._clear_aim(target)

        ground_object = None
        object_event = None
        location_id = getattr(target, 'location_id', None)
        if location_id is not None:
            ground_object = LocationObject.query.filter_by(
                location_id=location_id,
                type='ground_item',
                tile_x=target.pos_x,
                tile_y=target.pos_y,
            ).first()
            if ground_object:
                properties = deepcopy(ground_object.properties or {})
                properties.setdefault('contents', []).append(deepcopy(weapon))
                properties.update({
                    'is_ground_item': True,
                    'passable': True,
                    'interactions': ['open_container'],
                })
                ground_object.properties = properties
                flag_modified(ground_object, 'properties')
                object_event = 'updated'
            else:
                ground_object = LocationObject(
                    location_id=location_id,
                    name='Пол',
                    type='ground_item',
                    tile_x=target.pos_x,
                    tile_y=target.pos_y,
                    properties={
                        'contents': [deepcopy(weapon)],
                        'is_ground_item': True,
                        'passable': True,
                        'dropped_by_character_id': getattr(target, 'character_id', None),
                        'interactions': ['open_container'],
                    },
                )
                db.session.add(ground_object)
                db.session.flush()
                object_event = 'created'
        return {
            'kind': 'drop_weapon',
            'dropped': True,
            'weapon_name': weapon.get('name'),
            'weapon_wear': wear,
            'ground_object_id': getattr(ground_object, 'id', None),
            'ground_object_event': object_event,
        }

    @staticmethod
    def _trauma_effects(zone, roll):
        bleeding = {
            'chest': {
                5: ('internal', 'light'), 8: ('external', 'light'),
                13: ('internal', 'medium'), 15: ('external', 'light'),
                18: ('internal', 'medium'),
            },
            'abdomen': {
                4: ('internal', 'medium'), 5: ('internal', 'light'),
                7: ('internal', 'severe'), 9: ('external', 'light'),
                12: ('external', 'light'), 14: ('internal', 'medium'),
                16: ('internal', 'light'), 19: ('internal', 'severe'),
            },
            'head': {
                7: ('internal', 'severe'), 9: ('external', 'extreme'),
            },
            'limb': {
                4: ('internal', 'light'), 6: ('internal', 'medium'),
                8: ('external', 'light'), 11: ('external', 'severe'),
            },
        }
        pain = {
            'chest': {2: 1, 10: 3, 19: 1},
            'abdomen': {2: 1, 3: 3, 11: 1, 13: 3, 15: 1, 18: 3},
            'head': {3: 3, 15: 1},
            'limb': {2: 2, 5: 1, 9: 3, 12: 1, 14: 1, 15: 2, 17: 3, 19: 2},
        }
        organs = {
            'chest': {
                1: 'heart', 3: 'rightLung', 4: 'leftLung', 6: 'rightLung',
                7: 'leftLung', 12: 'leftLung', 14: 'rightLung',
                16: 'rightLung', 17: 'leftLung', 20: 'spine',
            },
            'abdomen': {
                1: 'kidney', 6: 'stomach', 10: 'liver',
                17: 'spine', 20: 'kidney',
            },
            'head': {
                1: 'jaw', 4: 'rightEye', 6: 'nose', 8: 'rightEar',
                13: 'jaw', 14: 'leftEye', 16: 'nose', 17: 'jaw',
                20: 'leftEar',
            },
        }
        group = 'limb' if zone in {'left_arm', 'right_arm', 'left_leg', 'right_leg'} else zone
        return {
            'bleeding': bleeding.get(group, {}).get(roll),
            'pain': pain.get(group, {}).get(roll, 0),
            'fracture': group == 'limb' and roll in {3, 7, 10, 16, 18, 20},
            'shock': (
                (zone == 'chest' and roll in {9, 11})
                or (zone == 'abdomen' and roll == 8)
                or (zone == 'head' and roll in {10, 18})
            ),
            'organ': organs.get(group, {}).get(roll),
            'fall_or_drop': group == 'limb' and roll in {1, 13},
        }

    @staticmethod
    def _apply_organ_damage(health, organ_key, damage, area):
        toughness = max(1, CombatService._consumable_stat_value_bonus({'health':health}, 'organ_toughness_multiplier'))
        damage = max(0, damage) / toughness
        if organ_key == 'kidney':
            organ_key = random.choice(('rightKidney', 'leftKidney'))
        maximum = BASE_ORGAN_MAXIMUMS.get(organ_key)
        if not maximum:
            return None
        organs = health.setdefault('organs', {})
        organ = organs.setdefault(organ_key, {'current': maximum, 'max': maximum})
        organ['max'] = maximum
        before = max(0, CombatService._coerce_float(organ.get('current'), maximum))
        organ['current'] = max(0, before - max(0, damage))
        result = {
            'organ': organ_key, 'current_before': before,
            'current': organ['current'], 'max': maximum,
            'disabled': before > 0 and organ['current'] <= 0,
        }
        if not result['disabled']:
            return result

        apply_effect_to_health(health, {
            'type': 'organ_loss', 'name': 'Повреждённый орган',
            'area': organ_key, 'source': 'organ_damage', 'tick': 'manual',
        })
        pain = {
            'heart': 10, 'rightLung': 5, 'leftLung': 5,
            'rightKidney': 8, 'leftKidney': 8, 'stomach': 8,
            'liver': 8, 'rightEye': 2, 'leftEye': 2,
            'rightEar': 2, 'leftEar': 2, 'nose': 2, 'jaw': 4, 'spine': 10,
        }.get(organ_key, 0)
        if pain:
            apply_effect_to_health(health, {
                'type': 'pain', 'value': pain,
                'source': 'disabled_organ', 'area': organ_key,
            })
        bleeding = {
            'rightLung': 'severe', 'leftLung': 'severe',
            'rightKidney': 'extreme', 'leftKidney': 'extreme',
            'stomach': 'severe',
        }.get(organ_key)
        if bleeding:
            apply_effect_to_health(health, {
                'type': f'bleeding_internal_{bleeding}',
                'source': 'disabled_organ', 'area': area,
            })
            result['bleeding'] = {'kind': 'internal', 'stage': bleeding, 'area': area}

        both_lungs = all(
            CombatService._coerce_float((organs.get(key) or {}).get('current'), BASE_ORGAN_MAXIMUMS[key]) <= 0
            for key in ('rightLung', 'leftLung')
        )
        both_kidneys = all(
            CombatService._coerce_float((organs.get(key) or {}).get('current'), BASE_ORGAN_MAXIMUMS[key]) <= 0
            for key in ('rightKidney', 'leftKidney')
        )
        if organ_key in {'brain', 'skull'}:
            apply_effect_to_health(health, {
                'type': 'death', 'name': 'Смерть',
                'source': f'{organ_key}_destroyed', 'tick': 'manual',
            })
            result['death'] = True
        else:
            death_seconds = (
                60 if organ_key == 'heart' or both_lungs
                else 3600 if organ_key in {'spine', 'liver'} or both_kidneys
                else None
            )
            if death_seconds:
                apply_effect_to_health(health, {
                    'type': 'organ_failure',
                    'name': 'Смертельное повреждение органа',
                    'area': organ_key, 'source': 'organ_damage',
                    'tick': 'time_elapsed',
                    'remaining': 1,
                    'time_unit': 'minute' if death_seconds == 60 else 'hour',
                    'remaining_seconds': death_seconds,
                    'duration_seconds': death_seconds,
                    'death_on_expire': True,
                })
                result['death_in_seconds'] = death_seconds
        return result

    @staticmethod
    def _damage_pain_requirement(accumulated_damage, single_hit_damage):
        accumulated = max(0, CombatService._coerce_float(accumulated_damage, 0))
        single_hit = max(0, CombatService._coerce_float(single_hit_damage, 0))
        accumulated_pain = 0 if accumulated < 50 else 1 + math.floor((accumulated - 50) / 100)
        single_hit_pain = 5 if single_hit > 300 else (3 if single_hit > 200 else (2 if single_hit >= 150 else 0))
        return accumulated_pain + single_hit_pain

    @staticmethod
    def _apply_damage_pain(character_data, damage, round_number, source):
        """Начислить прирост раундовой боли и усиление именно этого ранения."""
        if damage <= 0:
            return
        health = character_data.setdefault('health', {})
        meta = health.setdefault('combatMeta', {})
        if meta.get('damagePainRound') != round_number:
            meta['damagePainRound'] = round_number
            meta['damageTakenThisRound'] = 0
            meta['damagePainAppliedThisRound'] = 0
        previous_damage = CombatService._coerce_float(meta.get('damageTakenThisRound'), 0)
        accumulated = previous_damage + damage
        profile = CombatService._mutant_pain_profile(character_data)
        multiplier = profile['threshold_multiplier']
        # Уже начисленная накопительная часть не оплачивается повторно.
        # Ступень отдельного ранения, напротив, применяется к каждому ранению.
        pain_delta = 0 if profile['immune'] else max(0,
            CombatService._damage_pain_requirement(accumulated / multiplier, damage / multiplier)
            - CombatService._damage_pain_requirement(previous_damage / multiplier, 0)
        )
        if pain_delta:
            apply_effect_to_health(health, {'type': 'pain', 'value': pain_delta, 'source': source},character_data=character_data)
        meta['damageTakenThisRound'] = accumulated
        meta['damagePainAppliedThisRound'] = (
            CombatService._coerce_int(meta.get('damagePainAppliedThisRound'), 0) + pain_delta
        )

    @staticmethod
    def _apply_attack_damage(
        target,
        damage,
        zone,
        profile,
        *,
        bleeding_result=None,
        round_number=None,
        allow_bleeding=True,
        trauma_checks=1,
        trauma_difficulty_modifier=0,
        force_trauma=False,
        stress_trigger='direct_attack',
        attacker=None,
        head_impact=None,
        armor_penetrated=True,
        prepared_trauma_rolls=None,
        precision_attacker=None,
        limb_hit=None,
    ):
        character = target.character
        data = dict(character.data or {})
        from app.services.prostheses import hit_part,damage_part
        limb_hit=hit_part(data,zone) if limb_hit is None else limb_hit
        if limb_hit and limb_hit.get('mechanical'):
            prosthesis_damage=damage_part(data,limb_hit,damage)
            character.data=data;flag_modified(character,'data')
            health=data.setdefault('health',{})
            health['_attackOutcome']={'applied_damage':0,'prosthesis_damage':prosthesis_damage,
                'bleedings':[],'additional_traumas':[],'death':False}
            return health
        stoic_active=CombatService._combat_background_active(target,'Стойкий')
        if stoic_active:damage=max(0,damage*.9)
        previous_condition = CombatService._character_condition(data)['state']
        health = apply_health_maximums(data)
        before_health=deepcopy(health)
        if damage > 0:
            active_effects = normalize_effect_list(health.get('effects') or [])
            for effect in active_effects:
                if effect.get('type') == 'stress_stupor' and effect.get('remaining_seconds'):
                    effect['active'] = False
            health['effects'] = active_effects
        maximum = CombatService._coerce_float(health.get('max'), 700)
        health['max'] = maximum
        current = CombatService._coerce_float(health.get('current'), maximum)
        health['current'] = max(0, current - damage)
        zones = health.setdefault('zones', {})
        zone_key = zone
        camel_zone = {
            'left_arm': 'leftArm', 'right_arm': 'rightArm',
            'left_leg': 'leftLeg', 'right_leg': 'rightLeg',
        }.get(zone, zone)
        if camel_zone in zones:
            zone_key = camel_zone
        zone_data = zones.setdefault(zone_key, {'current': 0, 'max': 0})
        zone_max = CombatService._coerce_float(zone_data.get('max'), 0)
        zone_data['max'] = zone_max
        zone_current_before = CombatService._coerce_float(zone_data.get('current'), zone_max)
        zone_damage = damage
        if zone == 'head' and isinstance(head_impact, dict):
            damages_head = bool(
                head_impact.get('damage_head')
                or (
                    armor_penetrated
                    and head_impact.get('damage_head_if_penetrated')
                )
            )
            zone_damage = damage if damages_head else 0
        zone_data['current'] = max(0, zone_current_before - zone_damage)
        fatal_vital_hit = bool(
            zone_current_before <= 0
            and zone_key in {'head', 'chest'}
            and not (
                zone == 'head'
                and isinstance(head_impact, dict)
                and zone_damage <= 0
            )
        )
        if fatal_vital_hit:
            apply_effect_to_health(health, {
                'type': 'death',
                'name': 'Смерть',
                'area': zone_key,
                'source': 'hit_disabled_vital_zone',
                'tick': 'manual',
            })
        previous_destruction = CombatService._coerce_float(
            zone_data.get('destructionDamage'),
            max(0, zone_max - min(zone_max, zone_current_before)),
        )
        if zone_current_before > 0:
            previous_destruction = min(
                previous_destruction,
                max(0, zone_max - min(zone_max, zone_current_before)),
            )
        zone_data['destructionDamage'] = max(
            0, previous_destruction + max(0, zone_damage),
        )
        active_effects = normalize_effect_list(health.get('effects') or [])
        minimum_limb_health = max([
            CombatService._coerce_float(effect.get('minimum_limb_health'), 0)
            for effect in active_effects
            if effect.get('type') == 'temporary_limb_restoration'
            and str(effect.get('area') or '') == str(zone_key)
            and effect.get('active', True)
            and (effect.get('remaining') is None or CombatService._coerce_float(effect.get('remaining'), 0) > 0)
        ] or [0])
        if minimum_limb_health > 0:
            zone_data['current'] = max(minimum_limb_health, zone_data['current'])
        catastrophic_limb_injury = None
        limb_zones = {'leftArm', 'rightArm', 'leftLeg', 'rightLeg'}
        if (
            zone_key in limb_zones
            and zone_max > 0
            and zone_data['current'] <= 0
            and minimum_limb_health <= 0
        ):
            destruction_ratio = zone_data['destructionDamage'] / zone_max
            active_area_effects = [
                effect for effect in normalize_effect_list(health.get('effects') or [])
                if effect.get('active', True) and str(effect.get('area') or '') == str(zone_key)
            ]
            has_amputation = any(effect.get('type') == 'amputation' for effect in active_area_effects)
            has_mangled_limb = any(effect.get('type') == 'mangled_limb' for effect in active_area_effects)
            if destruction_ratio >= 5 and not has_amputation:
                loss_roll = random.randint(1, 6)
                loss_extent = (
                    'entire_limb' if loss_roll <= 2
                    else 'elbow_or_knee' if loss_roll <= 4
                    else 'hand_or_foot'
                )
                health['effects'] = [
                    effect for effect in normalize_effect_list(health.get('effects') or [])
                    if not (
                        effect.get('type') == 'mangled_limb'
                        and str(effect.get('area') or '') == str(zone_key)
                    )
                ]
                apply_effect_to_health(health, {
                    'type': 'amputation',
                    'name': 'Утраченная конечность',
                    'area': zone_key,
                    'source': 'catastrophic_limb_damage',
                    'tick': 'manual',
                    'loss_roll': loss_roll,
                    'loss_extent': loss_extent,
                    'treatment_window_seconds': 3600,
                })
                apply_effect_to_health(health, {
                    'type': 'shock', 'area': zone_key,
                    'source': 'catastrophic_limb_damage',
                })
                catastrophic_limb_injury = {
                    'type': 'amputation',
                    'area': zone_key,
                    'damage_ratio': destruction_ratio,
                    'loss_roll': loss_roll,
                    'loss_extent': loss_extent,
                }
            elif destruction_ratio >= 3 and not has_mangled_limb and not has_amputation:
                apply_effect_to_health(health, {
                    'type': 'mangled_limb',
                    'name': 'Искореженная конечность',
                    'area': zone_key,
                    'source': 'catastrophic_limb_damage',
                    'tick': 'manual',
                })
                apply_effect_to_health(health, {
                    'type': 'shock', 'area': zone_key,
                    'source': 'catastrophic_limb_damage',
                })
                catastrophic_limb_injury = {
                    'type': 'mangled_limb',
                    'area': zone_key,
                    'damage_ratio': destruction_ratio,
                }
        meta = health.setdefault('combatMeta', {})
        current_round = max(0, CombatService._coerce_int(round_number, 0))
        mutant_pain = CombatService._mutant_pain_profile(data)
        if damage > 0:
            meta['injuryRound'] = current_round
            if meta.get('damageStressRound') != current_round:
                stress_blocked = CombatService._coerce_int(meta.get('stressBlockTurns'), 0) > 0
                if not stress_blocked:
                    # The manifestation check is resolved after this damage is recorded.
                    meta['pendingDamageStressTrigger'] = True
                meta['damageStressRound'] = current_round
            CombatService._apply_damage_pain(data, damage, current_round, 'combat_damage')
        if zone_current_before > 0 and zone_data['current'] <= 0:
            disabled_zone_pain = {
                'left_arm': 4,
                'right_arm': 4,
                'left_leg': 3,
                'right_leg': 3,
                'abdomen': 3,
            }.get(zone, 0)
            if disabled_zone_pain and not mutant_pain['immune']:
                apply_effect_to_health(health, {
                    'type': 'pain',
                    'value': disabled_zone_pain,
                    'source': 'disabled_body_zone',
                    'area': zone_key,
                },character_data=data)
            if zone in {'head', 'chest'}:
                apply_effect_to_health(health, {
                    'type': 'shock',
                    'source': 'disabled_body_zone',
                    'area': zone_key,
                })
        created_bleedings = []
        if allow_bleeding and bleeding_result and bleeding_result.get('stage'):
            created_bleedings.append({
                'kind': 'external',
                'stage': bleeding_result['stage'],
                'area': zone_key,
                'source': 'firearm_wound',
            })
            apply_effect_to_health(health, {
                'type': f"bleeding_external_{bleeding_result['stage']}",
                'area': zone_key,
                'value': 1,
                'source': 'firearm_wound',
            })
        bleeding_type = str(profile.get('bleeding') or '').lower()
        bleeding_map = {'легкое': 'light', 'лёгкое': 'light', 'среднее': 'medium', 'сильное': 'severe', 'экстремальное': 'extreme'}
        stage = next((suffix for label, suffix in bleeding_map.items() if label in bleeding_type), None)
        if allow_bleeding and stage:
            kind = 'internal' if 'внут' in bleeding_type else 'external'
            created_bleedings.append({
                'kind': kind,
                'stage': stage,
                'area': zone_key,
                'source': 'combat_attack',
            })
            apply_effect_to_health(health, {
                'type': f'bleeding_{kind}_{stage}',
                'area': zone_key,
                'value': 1,
                'source': 'combat_attack',
            })
        trauma_chance_roll = None
        trauma_roll = None
        trauma = None
        traumas = []
        prepared_rolls = list(prepared_trauma_rolls or [])
        from app.services.equipment_modifications import worn_rule
        trauma_disadvantage = bool(worn_rule(data,'incoming_trauma_disadvantage'))
        if damage >= 11 or force_trauma and damage > 0:
            for trauma_index in range(max(0, CombatService._coerce_int(trauma_checks, 1))):
                trauma_chance_roll = random.randint(1, 100)
                if trauma_disadvantage:
                    trauma_chance_roll = min(trauma_chance_roll, random.randint(1, 100))
                threshold = {
                    'head': 0,
                    'chest': 50,
                    'abdomen': 30,
                    'left_arm': 70,
                    'right_arm': 70,
                    'left_leg': 70,
                    'right_leg': 70,
                }.get(zone, 70)
                threshold = max(
                    0,
                    threshold + CombatService._coerce_int(
                        trauma_difficulty_modifier, 0
                    )-character_passives(data)['incoming_trauma_modifier']
                    +(10 if stoic_active else 0)
                    -character_passives(attacker.character.data if attacker and getattr(attacker,'character',None) else {})['outgoing_trauma_modifier'],
                )
                if not force_trauma and trauma_chance_roll < threshold:
                    continue
                trauma_roll = (
                    CombatService._coerce_int(prepared_rolls.pop(0), 1)
                    if prepared_rolls else random.randint(1, 20)
                )
                trauma_rules = dict(CombatService._trauma_effects(zone, trauma_roll))
                impact = None
                if zone == 'head':
                    impact = (
                        head_impact
                        if trauma_index == 0 and isinstance(head_impact, dict)
                        else CombatService._head_impact_profile(
                            trauma_roll, attacker, target,
                        )
                    )
                    trauma_rules['organ'] = impact.get('primary_organ')
                trauma = {
                    'type': 'additional_trauma',
                    'area': zone_key,
                    'chance_roll': trauma_chance_roll,
                    'roll': trauma_roll,
                    'fracture': bool(trauma_rules['fracture']),
                    'bleeding': (
                        {
                            'kind': trauma_rules['bleeding'][0],
                            'stage': trauma_rules['bleeding'][1],
                        }
                        if trauma_rules['bleeding']
                        else None
                    ),
                    'pain': trauma_rules['pain'],
                    'shock': bool(trauma_rules['shock']),
                    'organ': trauma_rules['organ'],
                    'fall_or_drop': trauma_rules['fall_or_drop'],
                    'head_impact': deepcopy(impact),
                }
                traumas.append(trauma)
                from app.services.sniper_trauma import enqueue,apply_trauma
                if precision_attacker:
                    enqueue(precision_attacker,target,trauma,damage,zone,armor_penetrated,allow_bleeding)
                else:
                    created_bleedings.extend(apply_trauma(target,data,trauma,trauma_rules,damage,impact,armor_penetrated,allow_bleeding))
        sync_health_derived_statuses(health)
        resulting_condition = CombatService._character_condition(data)
        tough_nut=None
        if resulting_condition['state']=='dead' and previous_condition!='dead':
            from app.services.character_survival import survive_wound
            tough_nut=survive_wound(data,before_health,zone)
            if tough_nut:
                trauma=None;traumas=[];created_bleedings=[];catastrophic_limb_injury=None;fatal_vital_hit=False
                sync_health_derived_statuses(health);resulting_condition=CombatService._character_condition(data)
        if resulting_condition['state'] in {'pain_shock', 'critical', 'dead'}:
            target.posture = 'prone'
            target.cover_object_id = None
            target.weapon_braced = False
            target.braced_weapon_index = None
        character.data = data
        flag_modified(character, 'data')
        if meta.pop('pendingDamageStressTrigger', False):
            stress_result = CombatService.apply_stress_trigger(
                target, 1, trigger=stress_trigger,
            )
            health['stressResult'] = stress_result
        if (
            previous_condition not in {'pain_shock', 'critical', 'dead'}
            and resulting_condition['state'] in {'pain_shock', 'critical', 'dead'}
            and getattr(target, 'team_name', None)
            and getattr(target, 'location_id', None) is not None
        ):
            ally_trigger = 'ally_death' if resulting_condition['state'] == 'dead' else 'ally_critical'
            for ally in LocationCharacter.query.filter_by(
                location_id=target.location_id, team_name=target.team_name,
            ).all():
                if ally.id != target.id and ally.character:
                    CombatService.apply_stress_trigger(ally, 1, trigger=ally_trigger)
        target.hp_zones = health.get('zones') or target.hp_zones
        flag_modified(target, 'hp_zones')
        health['lastTrauma'] = trauma
        health['lastTraumas'] = traumas
        health['_attackOutcome'] = {
            'applied_damage':damage,
            'bleedings': created_bleedings,
            'additional_traumas': traumas,
            'catastrophic_limb_injury': catastrophic_limb_injury,
            'death': fatal_vital_hit,
            'tough_nut':tough_nut,
        }
        if previous_condition!='dead' and resulting_condition['state']=='dead':CombatService._notify_observed_death(target)
        return health

    @staticmethod
    def _explosive_key(value):
        normalized = re.sub(r'[^0-9a-z\u0430-\u044f]+', '', str(value or '').lower().replace('\u0451', '\u0435'))
        aliases = (
            ('underbarrel_gas', ('\u043f\u043e\u0434\u0441\u0442\u0432\u043e\u043b\u044c\u043d\u044b\u0439\u0433\u0430\u0437\u043e\u0432\u044b\u0439',)),
            ('underbarrel_smoke', ('\u043f\u043e\u0434\u0441\u0442\u0432\u043e\u043b\u044c\u043d\u044b\u0439\u0434\u044b\u043c\u043e\u0432\u043e\u0439',)),
            ('underbarrel_flash', ('\u043f\u043e\u0434\u0441\u0442\u0432\u043e\u043b\u044c\u043d\u044b\u0439\u0441\u0432\u0435\u0442\u043e\u0448\u0443\u043c\u043e\u0432\u043e\u0439',)),
            ('cheremukha', ('\u0447\u0435\u0440\u0435\u043c\u0443\u0445',)),
            ('refresher', ('refresher',)),
            ('molotov', ('\u043a\u043e\u043a\u0442\u0435\u0439\u043b\u044c\u043c\u043e\u043b\u043e\u0442\u043e\u0432',)),
            ('napalm', ('napalman', 'napalm',)),
            ('zarya', ('\u0437\u0430\u0440\u044f',)),
            ('flashm2', ('flashm2',)),
            ('fakel', ('\u0444\u0430\u043a\u0435\u043b',)),
            ('rdg6', ('\u0440\u0434\u04336',)),
            ('screen', ('screen',)),
            ('bang793', ('bang793',)),
            ('rg60tb', ('\u0440\u043360\u0442\u0431', 'rg60tb')),
            ('rosh92', ('\u0440\u043e\u044892', 'rosh92')),
            ('n1012', ('n1012',)),
            ('vog25', ('\u0432\u043e\u043325', 'vog25')),
            ('og12', ('\u043e\u043312', 'og12')),
            ('rgd5', ('\u0440\u0433\u04345', 'rgd5')),
            ('rgn', ('\u0440\u0433\u043d', 'rgn')),
            ('rgo', ('\u0440\u0433\u043e', 'rgo')),
            ('f1', ('\u04441', 'f1')),
        )
        return next((key for key, values in aliases if any(alias in normalized for alias in values)), None)

    @staticmethod
    def _explosive_profile(item):
        item = item if isinstance(item, dict) else {}
        attributes = CombatService._template_attributes(item)
        key = CombatService._explosive_key(
            item.get('name') or attributes.get('caliber') or item.get('caliber')
        )
        if not key:
            key = CombatService._explosive_key(
                attributes.get('name') or attributes.get('ammo_name')
            )
        base = EXPLOSIVE_PROFILES.get(key)
        if not base:
            return None
        profile = deepcopy(base)
        profile['key'] = key
        profile['name'] = str(item.get('name') or profile['label'])
        if 'noise' in attributes or 'noise' in item:
            profile['noise']=max(0,CombatService._coerce_float(attributes.get('noise',item.get('noise')),0))
        configured_range = CombatService._coerce_float(
            attributes.get('effective_range', attributes.get('range')),
            profile.get('projectile_range'),
        )
        if configured_range and configured_range > 0:
            profile['projectile_range'] = configured_range
        return profile

    @staticmethod
    def _point_distance(first_x, first_y, second_x, second_y):
        return math.hypot(float(second_x) - float(first_x), float(second_y) - float(first_y))

    @staticmethod
    def _clamp_projectile_point(start_x, start_y, target_x, target_y, maximum_range):
        distance = CombatService._point_distance(start_x, start_y, target_x, target_y)
        maximum = CombatService._coerce_float(maximum_range, 0)
        if maximum <= 0 or distance <= maximum or distance <= 1e-9:
            return float(target_x), float(target_y), False
        ratio = maximum / distance
        return (
            start_x + (target_x - start_x) * ratio,
            start_y + (target_y - start_y) * ratio,
            True,
        )

    @staticmethod
    def _scatter_point(target_x, target_y, failure, width, height, *, location=None, floor_level=0):
        scatter = min(10,max(0, CombatService._coerce_float(failure, 0)))
        if scatter <= 0:
            return float(target_x), float(target_y)
        angle = random.random() * math.tau
        dx,dy = math.cos(angle)*scatter,math.sin(angle)*scatter
        fraction = 1
        for origin,delta,maximum in ((target_x,dx,width-1),(target_y,dy,height-1)):
            if delta>1e-9:
                fraction=min(fraction,(maximum-origin)/delta)
            elif delta < -1e-9:
                fraction=min(fraction,-origin/delta)
        if location is not None:
            from app.services.location_navigation import surface,ground,STOREY_HEIGHT
            objects=LocationObject.query.filter_by(location_id=location.id).all()
            geometry=CombatService._movement_wall_geometry(location,objects)
            point=surface(location,objects,target_x,target_y,floor_level)
            plane=point['height'] if point else ground(location,target_x,target_y)+floor_level*STOREY_HEIGHT
            for box in [*geometry.boxes,*(item[0] for item in geometry.climb_boxes)]:
                if box.level!=floor_level or not box.bottom-1e-9<=plane<=box.top+1e-9:
                    continue
                entry=box.segment_entry((target_x+.5,target_y+.5),(target_x+dx+.5,target_y+dy+.5))
                if entry is not None:
                    fraction=min(fraction,entry)
        return target_x+dx*max(0,fraction),target_y+dy*max(0,fraction)

    @staticmethod
    def _segment_distance_to_point(start_x, start_y, end_x, end_y, point_x, point_y):
        dx = float(end_x) - float(start_x)
        dy = float(end_y) - float(start_y)
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return CombatService._point_distance(start_x, start_y, point_x, point_y)
        ratio = (
            (float(point_x) - float(start_x)) * dx
            + (float(point_y) - float(start_y)) * dy
        ) / (dx * dx + dy * dy)
        ratio = max(0.0, min(1.0, ratio))
        return CombatService._point_distance(
            float(start_x) + ratio * dx,
            float(start_y) + ratio * dy,
            point_x,
            point_y,
        )

    @staticmethod
    def _smoke_blocks_line(location_id, shooter, target):
        state = LocationCombatState.query.filter_by(location_id=location_id).first()
        from app.services.cloud_geometry import refresh
        refresh(state)
        areas = state.area_effects if state and isinstance(state.area_effects, list) else []
        for area in areas:
            if not isinstance(area, dict) or not str(area.get('type') or '').startswith('smoke'):
                continue
            radius = max(0, CombatService._coerce_float(area.get('radius'), 0))
            if radius <= 0:
                continue
            if area.get('cells') is not None:
                from app.services.movement_geometry import WallBox
                from app.services.explosion_geometry import height
                location=db.session.get(Location,location_id)
                objects=LocationObject.query.filter_by(location_id=location_id).all()
                start_height=height(location,objects,shooter.pos_x,shooter.pos_y,getattr(shooter,'floor_level',0) or 0)+1.65
                end_height=height(location,objects,target.pos_x,target.pos_y,getattr(target,'floor_level',0) or 0)+1.65
                for x,y,level in area['cells']:
                    t=WallBox(x+.5,y+.5,1,1,0,0).segment_entry((shooter.pos_x+.5,shooter.pos_y+.5),(target.pos_x+.5,target.pos_y+.5))
                    bottom=height(location,objects,x,y,level)
                    if t is not None and bottom<=start_height+(end_height-start_height)*t<=bottom+2.4:return area
                continue
            distance = CombatService._segment_distance_to_point(
                shooter.pos_x, shooter.pos_y, target.pos_x, target.pos_y,
                area.get('x'), area.get('y'),
            )
            if distance <= radius:
                return area
        return None

    @staticmethod
    def _flash_facing_multiplier(target, epicenter_x, epicenter_y):
        facing_x = CombatService._coerce_float(getattr(target, 'facing_x', 0), 0)
        facing_y = CombatService._coerce_float(getattr(target, 'facing_y', 1), 1)
        to_flash_x = float(epicenter_x) - float(target.pos_x)
        to_flash_y = float(epicenter_y) - float(target.pos_y)
        facing_length = math.hypot(facing_x, facing_y)
        flash_length = math.hypot(to_flash_x, to_flash_y)
        if flash_length <= 1e-9:
            return 1.0
        if facing_length <= 1e-9:
            facing_x, facing_y, facing_length = 0, 1, 1
        cosine = max(-1.0, min(1.0, (
            facing_x * to_flash_x + facing_y * to_flash_y
        ) / (facing_length * flash_length)))
        angle = math.degrees(math.acos(cosine))
        if angle <= 15:
            return 1.0
        if angle <= 45:
            return 0.75
        if angle <= 75:
            return 0.5
        if angle <= 135:
            return 0.25
        return 0.1

    @staticmethod
    def _resolve_flash(location_id, epicenter_x, epicenter_y, profile, floor_level=0, target_ids=None):
        from app.services.explosion_geometry import distance as spatial_distance
        location=db.session.get(Location,location_id)
        objects=LocationObject.query.filter_by(location_id=location_id).all()
        results = []
        for target in LocationCharacter.query.filter_by(location_id=location_id).all():
            if target_ids is not None and target.id not in target_ids:
                continue
            if not target.character:
                continue
            distance = spatial_distance(location,objects,epicenter_x,epicenter_y,floor_level,target)
            if distance > CombatService._coerce_float(profile.get('radius'), 0):
                continue
            multiplier = CombatService._flash_facing_multiplier(
                target, epicenter_x, epicenter_y,
            )
            blindness = CombatService._received_flash(target.character.data or {}, profile.get('blindness'), distance, multiplier)
            cover=CombatService._explosion_cover(location_id,epicenter_x,epicenter_y,target,floor_level,optical=True)
            if (cover.get('zones') or {}).get('head'):
                blindness=0
            data = dict(target.character.data or {})
            health = data.setdefault('health', {})
            if blindness > 0:
                apply_effect_to_health(health, {
                    'type': 'blindness',
                    'name': '\u041e\u0441\u043b\u0435\u043f\u043b\u0435\u043d\u0438\u0435',
                    'value': blindness,
                    'source': 'flash_grenade',
                    'tick': 'manual',
                })
            target.character.data = data
            flag_modified(target.character, 'data')
            CombatService._sync_location_effects_from_character(target)
            results.append({
                'character_id': target.character_id,
                'name': target.character.name,
                'distance': round(distance, 2),
                'facing_multiplier': multiplier,
                'blindness': blindness,
                'flash_blocked': bool((cover.get('zones') or {}).get('head')),
                'deafness': 0,
            })
        return results

    @staticmethod
    def _received_flash(data, points, distance, facing_multiplier):
        from app.services.weapon_modules import uses_nvg
        value = max(0, CombatService._coerce_float(points, 0) * facing_multiplier - 20 * distance)
        if uses_nvg(data):
            value *= .5
        from app.services.equipment_passives import flash_multiplier
        return round(value * flash_multiplier(data) * character_passives(data)['blindness_multiplier'])

    @staticmethod
    def _flash_round_profile(profile):
        variant = str(profile.get('ammo_variant') or '').casefold()
        name = str(profile.get('ammo_name') or '').casefold()
        caliber = CombatService._caliber_key(CombatService._normalize_caliber(profile.get('caliber')))
        if variant and variant != 'flashbang':
            return None
        signature = name + ' ' + str(profile.get('caliber') or '').casefold()
        if variant != 'flashbang' and 'светошум' not in signature and not (
            caliber.startswith('18x45') and 'сз' in signature):
            return None
        if caliber.startswith('12x70'):
            return {'blindness':100, 'radius':5, 'noise':15}
        if caliber.startswith('18x45'):
            return {'blindness':80, 'radius':4, 'noise':10}
        raise ValidationError('Для светошумового патрона этого калибра не задан профиль вспышки.')

    @staticmethod
    def _resolve_flash_round(target, attacker, details, profile):
        flash = CombatService._flash_round_profile(profile)
        if flash is None:
            return None
        distance = CombatService._coerce_float(details.get('target_distance'), 0)
        positioned = all(getattr(c, 'pos_x', None) is not None for c in (attacker, target))
        if positioned:
            distance = CombatService._character_distance(attacker, target)
        multiplier = CombatService._flash_facing_multiplier(target, attacker.pos_x, attacker.pos_y) if positioned else 1
        people = [target]
        if isinstance(attacker, LocationCharacter):
            people = CombatService._unique_location_characters(LocationCharacter.query.filter_by(location_id=attacker.location_id).all())
        affected=[]; value=0; blocked=False
        for person in people:
            if person is attacker or not person.character:
                continue
            person_distance = CombatService._character_distance(attacker, person) if positioned else distance
            in_cone = True
            if positioned:
                dx,dy=person.pos_x-attacker.pos_x,person.pos_y-attacker.pos_y
                fx,fy=getattr(attacker,'facing_x',0) or 0,getattr(attacker,'facing_y',1) or 0
                length=math.hypot(dx,dy)*math.hypot(fx,fy)
                in_cone = length>0 and (dx*fx+dy*fy)/length >= math.sqrt(.5)-1e-9
            if person_distance>flash['radius'] or not in_cone:
                continue
            cover = CombatService._cover_analysis(attacker.location_id,attacker,person) if isinstance(attacker,LocationCharacter) else details.get('cover') or {}
            person_blocked=bool((cover.get('zones') or {}).get('head'))
            person_multiplier=CombatService._flash_facing_multiplier(person,attacker.pos_x,attacker.pos_y) if positioned else 1
            data=deepcopy(person.character.data or {})
            received=0 if person_blocked else CombatService._received_flash(data,flash['blindness'],person_distance,person_multiplier)
            if received:
                apply_effect_to_health(data.setdefault('health',{}),{'type':'blindness','value':received,
                    'name':'Ослепление','source':'flash_round','tick':'manual'})
                person.character.data=data;flag_modified(person.character,'data')
                if isinstance(person,LocationCharacter):CombatService._sync_location_effects_from_character(person)
            affected.append({'character_id':getattr(person,'character_id',None),'blindness':received,
                'distance':person_distance,'facing_multiplier':person_multiplier,'flash_blocked':person_blocked})
            if person is target:
                value,blocked,multiplier,distance=received,person_blocked,person_multiplier,person_distance
        return {'hit':True,'roll':None,'rolls':[],'difficulty':None,'damage':0,'combined_damage':0,
                'target_character_id':getattr(target,'character_id',None),'target_name':CombatService._display_name(target.character),
                'mode':details.get('fire_mode','unaimed'),'flash_round':True,'blindness':value,'flash_noise':flash['noise'],
                'flash_radius':flash['radius'],'flash_half_angle':45,'flash_targets':affected,'distance':distance,'facing_multiplier':multiplier,
                'flash_blocked':blocked,'additional_traumas':[],'bleedings':[]}

    @staticmethod
    def _apply_fired_noise(location, actor, weapon, results, shot_count):
        """Особый патрон заменяет звук своего выстрела, в том числе в смешанной очереди."""
        from app.services.weapon_modules import weapon_stat
        from app.services.noise import apply as apply_noise
        base=max(0,weapon_stat(weapon,'noise',CombatService._template_attributes(weapon).get('noise',weapon.get('noise',0))))
        groups={}
        flash=[r for r in results if r.get('flash_round')]
        for result in flash:
            volume=result['flash_noise'];groups[volume]=groups.get(volume,0)+1
        ordinary=max(0,shot_count-len(flash))
        if ordinary:groups[base]=groups.get(base,0)+ordinary
        targets=[]
        for volume,count in groups.items():
            targets.extend(apply_noise(location,actor.pos_x,actor.pos_y,actor.floor_level or 0,volume,count=count))
        return max(groups,default=0),targets

    @staticmethod
    def _area_from_profile(profile, x, y, current_round):
        effect = profile.get('area_effect') or profile.get('effect')
        if effect not in {'smoke', 'smoke_growing', 'gas', 'fire'}:
            return None
        return {
            'id': uuid.uuid4().hex,
            'type': effect,
            'name': profile.get('name') or profile.get('label'),
            'x': float(x),
            'y': float(y),
            'radius': CombatService._coerce_float(profile.get('area_radius', profile.get('radius')), 0),
            'max_radius': CombatService._coerce_float(profile.get('max_radius'), profile.get('area_radius', profile.get('radius'))),
            'created_round': max(1, CombatService._coerce_int(current_round, 1)),
            'age': 0,
            'age_step':2 if profile.get('half_duration') else 1,
            'source_character_id':profile.get('source_character_id'),
            'duration_rounds': CombatService._coerce_int(profile.get('duration_rounds'), 0),
            'grow_rounds': CombatService._coerce_int(profile.get('grow_rounds'), 0),
            'hold_rounds': CombatService._coerce_int(profile.get('hold_rounds'), 0),
            'shrink_per_round': CombatService._coerce_float(profile.get('shrink_per_round'), 0),
            'pain': CombatService._coerce_int(profile.get('pain'), 0),
            'exit_pain_rounds': CombatService._coerce_int(profile.get('exit_pain_rounds'), 0),
            'chemical_damage': CombatService._coerce_int(profile.get('chemical_damage'), 0),
            'concussion_chance': CombatService._coerce_int(profile.get('concussion_chance'), 0),
            'burn_rounds': CombatService._coerce_int(profile.get('burn_rounds'), 0),
            'direct_burn_rounds': CombatService._coerce_int(profile.get('direct_burn_rounds'), 0),
            'burn_damage': CombatService._coerce_int(profile.get('burn_damage'), 0),
            'thermal_threshold': CombatService._coerce_int(profile.get('thermal_threshold'), 35),
            'threshold_inclusive': 'thermal_threshold' in profile,
            'extinguish_difficulty_modifier': CombatService._coerce_int(profile.get('extinguish_difficulty_modifier'),0),
        }

    @staticmethod
    def _apply_direct_fire_hit(location_id, x, y, area, target_ids=None):
        if not isinstance(area, dict) or area.get('type') != 'fire':
            return []
        rounds = max(
            1,
            CombatService._coerce_int(
                area.get('direct_burn_rounds'), area.get('burn_rounds'),
            ),
        )
        results = []
        for target in LocationCharacter.query.filter_by(location_id=location_id).all():
            if target_ids is not None and target.id not in target_ids:
                continue
            if not target.character or CombatService._point_distance(target.pos_x,target.pos_y,x,y) > .75:
                continue
            direct_area = {**area, 'burn_rounds': rounds, 'radius': 0.75}
            result = CombatService._apply_area_effect_to_character(
                direct_area, target, area.get('created_round'),
            )
            if result:
                result['direct_hit'] = True
                results.append(result)
        return results

    @staticmethod
    def _grenade_fragment_zone():
        roll = random.randint(1, 6)
        if roll == 1:
            return random.choice(('left_arm', 'right_arm'))
        if roll == 2:
            return random.choice(('left_leg', 'right_leg'))
        if roll == 3:
            return 'abdomen'
        if roll <= 5:
            return 'chest'
        return 'head'

    @staticmethod
    def _explosion_cover(location_id, epicenter_x, epicenter_y, target, floor_level=0, optical=False):
        source = type('ExplosionOrigin', (), {
            'floor_level': floor_level,
            'pos_x': epicenter_x,
            'pos_y': epicenter_y,
            'posture': 'standing',
            'eye_height': .05 if optical else 0,
            'ignore_smoke': not optical,
        })()
        analysis = CombatService._cover_analysis(location_id, source, target)
        zones = analysis.get('zones') or {}
        protection = max(
            [CombatService._coerce_float(value.get('physical_protection'), 0) for value in zones.values()]
            or [0]
        )
        return {
            'protection': max(0, min(100, protection)),
            'grade':analysis.get('grade','none'),
            'zones':zones,
            'objects': list(dict.fromkeys(
                value.get('object_id') for value in zones.values() if value.get('object_id')
            )),
        }

    @staticmethod
    def _penetration_damage_multiplier(penetration, armor):
        return max(0,1-math.ceil(max(0,round(armor-penetration,9))/5)*.25)

    @staticmethod
    def _explosion_fragment_hit(target,damage,penetration,zone,cover,round_number,additional=False,plate_side=None,
                                liquid_crystal_context=None):
        protection=(cover.get('zones') or {}).get(zone)
        cover_multiplier=1
        if cover.get('live_shield') or protection and protection.get('terrain'):
            cover_multiplier=0
        elif protection:
            if random.randint(1,100)<=protection.get('mesh_hit_chance',100):
                cover_multiplier=CombatService._penetration_damage_multiplier(penetration,protection.get('physical_protection',0))
                penetration*=.9
        elif not cover.get('zones') and cover.get('protection',0):
            # Поддержка прежних сохранений/профилей без детализации частей тела.
            cover_multiplier=CombatService._penetration_damage_multiplier(penetration,cover['protection'])
            penetration*=.9
        incoming=max(0,damage*cover_multiplier)
        data=target.character.data or {}
        from app.services.prostheses import hit_part
        limb_hit=hit_part(data,zone)
        armor,layers=CombatService._target_armor(data,zone,explosive=True,**({'limb_hit':limb_hit} if limb_hit else {}),
            **({'target':target} if background_is(data,'Стойкий') else {}),**({'plate_side':plate_side} if plate_side else {}),
            liquid_crystal_context=liquid_crystal_context)
        crystal_first=bool(incoming>0 and any(layer.get('liquid_crystal_first') for layer in layers))
        penetrated=penetration>=armor
        applied=(0 if additional and not penetrated else
                 round(incoming*CombatService._penetration_damage_multiplier(penetration,armor)))
        if crystal_first and not penetrated:applied=0
        if applied>0 and background_is(data,'Гора'):applied=round(applied*1.2)
        wear=[]
        if incoming>0:
            for layer in layers:
                result=(CombatService._damage_gas_mask(layer['item'],'bullet') if layer.get('is_gas_mask') else
                        CombatService._damage_armor_item(layer['item'],layer['attributes'],incoming*layer.get('wear_multiplier',1)))
                if result:wear.append(result)
        if crystal_first:
            from app.services.equipment_care import consume_liquid_crystal
            for layer in layers:
                if layer.get('liquid_crystal_first'):consume_liquid_crystal(layer['item'])
        health=None
        if applied>0:
            health=CombatService._apply_attack_damage(target,applied,zone,
                {'damage_type':'fragment','armor_piercing':penetration},round_number=round_number,
                allow_bleeding=additional,trauma_checks=1 if additional else 0,force_trauma=additional,
                stress_trigger='explosion',**({'limb_hit':limb_hit} if limb_hit else {}))
            if isinstance(health,dict):applied=(health.get('_attackOutcome') or {}).get('applied_damage',applied)
        return {'damage':applied,'penetrated':penetrated,'penetration':penetration,
                'armor':armor,'armor_damage':wear,'health':health}

    @staticmethod
    def _explosion_is_indoors(location,objects,x,y,floor_level):
        from app.services.location_navigation import level_of,contains
        # Помещения генератора имеют полы с building_id; одиночный настил не считается комнатой.
        return any(o.type=='floor' and (o.properties or {}).get('building_id')
                   and level_of(o)==floor_level and contains(o,x+.5,y+.5) for o in objects)

    @staticmethod
    def _throw_obstacle_difficulty(location_id, thrower, target_x, target_y):
        target = type('ThrowTarget', (), {
            'pos_x': target_x,
            'pos_y': target_y,
        })()
        highest = 0.0
        objects = LocationObject.query.filter_by(location_id=location_id).all()
        doors = [item for item in objects if item.type == 'door' or (item.type == 'window' and (item.properties or {}).get('is_open'))]
        for obj in objects:
            from app.services.location_navigation import level_of
            if level_of(obj) != (getattr(thrower,'floor_level',0) or 0):
                continue
            if not CombatService._is_cover_object(obj):
                continue
            if CombatService._line_object_entry(thrower, target, obj) is not None:
                if obj.type == 'wall' and any(
                    CombatService._line_through_door(thrower, target, door, obj, 1.3)
                    for door in doors
                ):
                    continue
                highest = max(highest, CombatService._object_height(obj))
        if highest <= 0:
            return 0
        if highest < 1.5:
            return 1
        if highest <= 3:
            return 3
        return 5

    @staticmethod
    def _hand_throw_difficulty(location_id, thrower, data, target_x, target_y, distance, weight_modifier=0):
        throwing_bonus = sum(CombatService._base_skill_modifier(data, path) for path in (
            'skills.physical.strength', 'skills.physical.agility', 'skills.other.tactics'))
        if any(effect.get('source') == 'background_aggressor' and effect.get('active', True)
               and (effect.get('remaining') or 0) > 0
               for effect in normalize_effect_list((data.get('health') or {}).get('effects') or [])):
            throwing_bonus += 3
        from app.services.equipment_modifications import worn_rule
        throwing_bonus += worn_rule(data, 'throwing_bonus')
        posture = {'standing': 0, 'sitting': 3, 'prone': 5}.get(CombatService._posture_key(thrower), 0)
        obstacle = CombatService._throw_obstacle_difficulty(location_id, thrower, target_x, target_y)
        if background_is(data, 'Глаз-Алмаз'):
            posture = obstacle = 0
        throw_distance = distance
        if trait_count(data, 'Волейболист'):
            if distance < 5:
                raise ValidationError('С чертой «Волейболист» нельзя метать ближе 5 метров')
            throw_distance = max(0, distance - 5)
        condition = CombatService._health_roll_modifier(data, 'skills.physical.agility')
        difficulty = max(1, 3 + throw_distance + posture + obstacle + weight_modifier
                         - throwing_bonus - condition)
        return {'difficulty': difficulty, 'posture': posture, 'obstacle': obstacle}

    @staticmethod
    def _apply_general_damage(target, damage, *, round_number=0, source='explosion',cause_stress=True):
        damage = max(0, round(CombatService._coerce_float(damage, 0)))
        data = dict(target.character.data or {})
        if CombatService._combat_background_active(target,'Стойкий'):damage*=.9
        previous_condition = CombatService._character_condition(data)['state']
        health = apply_health_maximums(data)
        before_health=deepcopy(health)
        maximum = CombatService._coerce_float(health.get('max'), 700)
        health['current'] = max(
            0,
            CombatService._coerce_float(health.get('current'), maximum) - damage,
        )
        meta = health.setdefault('combatMeta', {})
        current_round = max(0, CombatService._coerce_int(round_number, 0))
        if damage > 0:
            meta['injuryRound'] = current_round
            if cause_stress and meta.get('damageStressRound') != current_round:
                meta['damageStressRound'] = current_round
                meta['pendingExplosionStress'] = True
            CombatService._apply_damage_pain(data, damage, current_round, source)
        sync_health_derived_statuses(health)
        resulting_condition = CombatService._character_condition(data)
        survival=None
        if resulting_condition['state']=='dead' and previous_condition!='dead':
            from app.services.character_survival import survive_wound
            survival=survive_wound(data,before_health,'whole_body')
            if survival:
                sync_health_derived_statuses(health);resulting_condition=CombatService._character_condition(data)
        if resulting_condition['state'] in {'pain_shock', 'critical', 'dead'}:
            target.posture = 'prone'
            target.cover_object_id = None
            target.weapon_braced = False
            target.braced_weapon_index = None
        target.character.data = data
        flag_modified(target.character, 'data')
        if meta.pop('pendingExplosionStress', False):
            CombatService.apply_stress_trigger(target, 1, trigger='explosion')
        target.hp_zones = health.get('zones') or target.hp_zones
        flag_modified(target, 'hp_zones')
        if previous_condition!='dead' and resulting_condition['state']=='dead':CombatService._notify_observed_death(target)
        return {
            'damage': damage,
            'tough_nut':survival,
            'health_before_state': previous_condition,
            'health_state': resulting_condition['state'],
            'current_health': health.get('current'),
        }

    @staticmethod
    def _apply_blast_trauma(target, blast_damage, *, round_number=0):
        damage = max(0, CombatService._coerce_int(blast_damage, 0))
        if damage <= 0:
            return None
        category = random.randint(1, 6)
        band = 0 if damage <= 150 else (1 if damage <= 300 else (2 if damage <= 450 else 3))
        data = dict(target.character.data or {})
        health = data.setdefault('health', {})
        result = {'roll': category, 'damage_band': (150, 300, 450, 700)[band]}
        if category == 1:
            deafness = (15, 30, 50, 80)[band]
            hours = (1, 2, 4, 6)[band]
            apply_effect_to_health(health, {
                'type': 'deafness', 'name': '\u041f\u043e\u0432\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u0435 \u0441\u043b\u0443\u0445\u0430',
                'value': deafness, 'remaining': hours, 'time_unit': 'hour',
                'tick': 'time_elapsed', 'source': 'blast_wave',
                'minimum_value': deafness,
            })
            result.update({'type': 'hearing_damage', 'deafness': deafness, 'hours': hours})
        elif category in {2, 3}:
            rounds = (1, 3, 3, 5)[band]
            exhaustion = (0, 0, 1, 2)[band]
            apply_effect_to_health(health, {
                'type': 'concussion', 'name': '\u041a\u043e\u043d\u0442\u0443\u0437\u0438\u044f',
                'remaining': rounds, 'tick': 'turn_end' if band==0 else 'round_end', 'source': 'blast_wave',
                'roll_modifier': -3,
            })
            if exhaustion:
                health['exhaustion'] = CombatService._coerce_float(health.get('exhaustion'), 0) + exhaustion
            result.update({'type': 'concussion', 'rounds': rounds, 'exhaustion': exhaustion})
        elif category == 4:
            stage = ('light', 'medium', 'severe', 'extreme')[band]
            apply_effect_to_health(health, {
                'type': f'bleeding_internal_{stage}', 'area': 'chest',
                'source': 'blast_wave',
            })
            result.update({'type': 'bleeding', 'kind': 'internal', 'stage': stage, 'area': 'chest'})
        elif category == 5 and band > 0:
            zones = (
                [random.choice(('left_leg', 'right_leg'))]
                if band == 1 else
                [random.choice(('left_arm', 'right_arm'))]
                if band == 2 else
                [random.choice(('left_arm', 'right_arm')), random.choice(('left_leg', 'right_leg'))]
            )
            for zone in zones:
                apply_effect_to_health(health, {
                    'type': 'fracture', 'area': zone, 'source': 'blast_wave',
                })
            result.update({'type': 'fracture', 'areas': zones})
        else:
            result['type'] = 'none'
        target.character.data = data
        flag_modified(target.character, 'data')
        return result

    @staticmethod
    def resolve_explosion(location_id, epicenter_x, epicenter_y, profile, *, round_number=0, floor_level=0,
                          target_ids=None,damage_objects=True,create_area=True,live_shield_ids=()):
        from app.services.location_navigation import level_of
        from app.services.explosion_choice import hold
        if target_ids is None and hold(location_id,epicenter_x,epicenter_y,profile,floor_level):
            return {'profile':profile['key'],'name':profile['name'],'epicenter':{'x':epicenter_x,'y':epicenter_y},
                    'radius':profile.get('radius',0),'floor_level':floor_level,'targets':[],'objects':[],
                    'area':None,'awaiting_gm':True}
        from app.services import noise
        noise_location=db.session.get(Location,location_id)
        noise_objects=LocationObject.query.filter_by(location_id=location_id).all()
        noise_volume=CombatService._coerce_float(profile.get('noise'),0)
        noise_context=noise.space(noise_location,noise_objects,epicenter_x,epicenter_y,floor_level) if noise_volume>0 else None
        source_actor=LocationCharacter.query.filter_by(location_id=location_id,character_id=profile.get('source_character_id')).first() if profile.get('source_character_id') else None
        living={p.id for p in LocationCharacter.query.filter_by(location_id=location_id).all() if p.character and CombatService._character_condition(p.character.data)['state']!='dead'} if source_actor else set()
        def finish(payload):
            from app.services.character_rules import explosion_occurred
            if profile.get('blast_base',0)>0 or profile.get('fragment',0)>0 or profile.get('effect')=='flash':explosion_occurred(location_id)
            payload['noise_targets']=noise.apply(noise_location,epicenter_x,epicenter_y,floor_level,noise_volume,
                target_ids=target_ids,objects=noise_objects,context=noise_context)
            by_id={r['character_id']:r for r in payload['noise_targets']}
            for target in payload.get('targets',[]):
                if target['character_id'] in by_id:target['deafness']=by_id[target['character_id']]['deafness']
            if source_actor:
                from app.services.character_rules import after_kill
                for victim in LocationCharacter.query.filter_by(location_id=location_id).all():
                    if victim.character:after_kill(source_actor,victim,victim.id in living)
            return payload
        effect_type = str(profile.get('effect') or '').strip().lower()
        if effect_type == 'flash':
            return finish({
                'profile': profile['key'],
                'name': profile['name'],
                'epicenter': {'x': float(epicenter_x), 'y': float(epicenter_y)},
                'radius': CombatService._coerce_float(profile.get('radius'), 0),
                'floor_level': floor_level,
                'targets': CombatService._resolve_flash(
                    location_id, epicenter_x, epicenter_y, profile, **({'floor_level':floor_level} if floor_level else {}),
                    **({'target_ids':target_ids} if target_ids is not None else {}),
                ),
                'objects': [],
                'area': None,
            })
        if effect_type in {'smoke', 'smoke_growing', 'gas', 'fire'}:
            area = CombatService._area_from_profile(
                profile, epicenter_x, epicenter_y, round_number,
            )
            if floor_level:
                area['floor_level'] = floor_level
            from app.services.cloud_geometry import prepare
            prepare(db.session.get(Location,location_id),LocationObject.query.filter_by(location_id=location_id).all(),area)
            return finish({
                'profile': profile['key'],
                'name': profile['name'],
                'epicenter': {'x': float(epicenter_x), 'y': float(epicenter_y)},
                'radius': CombatService._coerce_float(profile.get('radius'), 0),
                'floor_level': floor_level,
                'targets': CombatService._apply_direct_fire_hit(
                    location_id, epicenter_x, epicenter_y, area,
                    **({'target_ids':target_ids} if target_ids is not None else {}),
                ),
                'objects': [],
                'area': area if create_area else None,
            })
        radius = max(0, CombatService._coerce_float(profile.get('radius'), 0))
        target_results = []
        objects=LocationObject.query.filter_by(location_id=location_id).all()
        location=db.session.get(Location,location_id)
        indoors=CombatService._explosion_is_indoors(location,objects,epicenter_x,epicenter_y,floor_level)
        people=CombatService._unique_location_characters(LocationCharacter.query.filter_by(location_id=location_id).all())
        from app.services.explosion_geometry import distance as spatial_distance,height as surface_height
        living=[(c.id,c.pos_x,c.pos_y,c.floor_level or 0) for c in people
                if c.character and (c.id in live_shield_ids or CombatService._character_condition(c.character.data or {})['state']!='dead')]
        for target in people:
            if target_ids is not None and target.id not in target_ids:
                continue
            if not target.character:
                continue
            posture_at_detonation = CombatService._posture_key(target)
            facing_x=CombatService._coerce_int(getattr(target,'facing_x',0),0)
            facing_y=CombatService._coerce_int(getattr(target,'facing_y',1),1)
            plate_side=('back' if facing_x*(epicenter_x-target.pos_x)+facing_y*(epicenter_y-target.pos_y)<0 else 'front')
            distance = spatial_distance(location,objects,epicenter_x,epicenter_y,floor_level,target)
            if distance > radius:
                continue
            from app.services.equipment_care import liquid_crystal_ready
            crystal_item=((target.character.data or {}).get('equipment') or {}).get('armor')
            crystal_context={'item':crystal_item,'active':liquid_crystal_ready(crystal_item)}
            cover = CombatService._explosion_cover(
                location_id, epicenter_x, epicenter_y, target, **({'floor_level':floor_level} if floor_level else {}),
            )
            # Живое тело на линии взрыва прикрывает следующую цель целиком.
            from app.services.movement_geometry import CHARACTER_RADIUS
            dx,dy=target.pos_x-epicenter_x,target.pos_y-epicenter_y
            length2=dx*dx+dy*dy
            for body_id,bx,by,body_level in living:
                if body_id==target.id or length2<=1e-9:
                    continue
                t=((bx-epicenter_x)*dx+(by-epicenter_y)*dy)/length2
                origin_height=surface_height(location,objects,epicenter_x,epicenter_y,floor_level)
                ray_height=origin_height+(surface_height(location,objects,target.pos_x,target.pos_y,target.floor_level or 0)-origin_height)*t
                body=next(c for c in people if c.id==body_id)
                bottom=surface_height(location,objects,bx,by,body_level)
                top=bottom+{'standing':1.8,'sitting':1.2,'prone':.4}[CombatService._posture_key(body)]
                if 0<t<1 and bottom-1e-8<=ray_height<=top and CombatService._segment_distance_to_point(epicenter_x,epicenter_y,target.pos_x,target.pos_y,bx,by)<=CHARACTER_RADIUS:
                    cover={**cover,'protection':100,'grade':'full','live_shield':body_id}
                    break
            zones=cover.get('zones') or {}
            chest_cover=zones.get('chest')
            full_cover=cover.get('grade')=='full' or cover['protection']>=100
            wave_blocked=bool(full_cover or cover.get('live_shield') or
                chest_cover and (chest_cover.get('terrain') or chest_cover.get('class') not in {'conditional','flimsy'}))
            posture_multiplier = {'standing': 1, 'sitting': 0.75, 'prone': 0.1}.get(
                posture_at_detonation, 1,
            )
            from app.services.trap_catalog import radial_damage
            band=radial_damage(profile,distance)
            fragment_damage = round(max(
                0, band['fragments'] if band else profile['fragment'] * (1 - 0.1 * distance),
            ) * posture_multiplier)
            penetration = band['penetration'] if band else profile['penetration']
            if not band and not profile.get('fragment_keeps_penetration'):
                penetration = max(0, penetration - 5 * distance)
            fragment_zone = CombatService._grenade_fragment_zone()
            fragment=CombatService._explosion_fragment_hit(target,fragment_damage,penetration,fragment_zone,cover,round_number,
                **({'plate_side':plate_side} if fragment_zone in {'chest','abdomen'} else {}),
                liquid_crystal_context=crystal_context)
            armor,penetrated=fragment['armor'],fragment['penetrated']
            applied_fragment_damage,fragment_armor_damage=fragment['damage'],fragment['armor_damage']
            extra_traumas = max(
                0,
                5 - int(math.floor(distance)) - int(cover['protection'] // 10)
                - (1 if posture_at_detonation == 'sitting' else 3 if posture_at_detonation == 'prone' else 0),
            ) if fragment_damage>0 and not cover.get('live_shield') else 0
            extra_results = []
            for _ in range(extra_traumas):
                extra_damage = sum(random.randint(1, 6) for _ in range(4))
                extra_zone = CombatService._grenade_fragment_zone()
                extra=CombatService._explosion_fragment_hit(target,extra_damage,penetration,extra_zone,cover,round_number,
                    additional=True,**({'plate_side':plate_side} if extra_zone in {'chest','abdomen'} else {}),
                    liquid_crystal_context=crystal_context)
                if extra['damage']<=0:
                    continue
                extra_results.append({
                    'zone': extra_zone,
                    'damage': extra['damage'],
                    'traumas': (extra['health'] or {}).get('lastTraumas') or [],
                })

            target_data = target.character.data if isinstance(target.character.data, dict) else {}
            blast_armor, blast_armor_layers = CombatService._target_armor(
                target_data, 'chest',explosive=True,**({'target':target} if background_is(target_data,'Стойкий') else {}),
                plate_side=plate_side,liquid_crystal_context=crystal_context,
            )
            armor_multiplier = max(0, 1 - blast_armor / 100)
            wave_incoming = max(
                0,
                band['blast']*(1.5 if indoors else 1) if band else profile['blast_base']*(1.5 if indoors else 1) - profile['blast_falloff'] * distance,
            )
            # Дробная защита не должна превращать ровные 42,5 в 42,50000000001.
            blast = 0 if wave_blocked else round(round(wave_incoming * armor_multiplier, 9))
            blast_armor_damage = []
            for layer in blast_armor_layers:
                if layer.get('is_gas_mask'):
                    continue
                damage_result = CombatService._damage_armor_item(
                    layer['item'], layer['attributes'], blast*layer.get('wear_multiplier',1),
                )
                if damage_result:
                    blast_armor_damage.append(damage_result)
            if wave_incoming>0 and not wave_blocked and any(layer.get('liquid_crystal_first') for layer in blast_armor_layers):
                from app.services.equipment_care import consume_liquid_crystal
                for layer in blast_armor_layers:
                    if layer.get('liquid_crystal_first'):consume_liquid_crystal(layer['item'])
                blast=0
            if background_is(target_data,'Гора'):blast=round(blast*.8)
            blast_result = CombatService._apply_general_damage(
                target, blast, round_number=round_number, source='blast_wave',
            )

            concussion_chance = 0 if wave_blocked else max(0,150*(2 if indoors else 1)-30*distance)
            concussion_roll = random.randint(1, 100)
            concussion = concussion_roll <= concussion_chance
            if concussion:
                data = target.character.data if isinstance(target.character.data, dict) else {}
                health = data.setdefault('health', {})
                apply_effect_to_health(health, {
                    'type': 'concussion',
                    'name': '\u041a\u043e\u043d\u0442\u0443\u0437\u0438\u044f',
                    'remaining': 5,
                    'tick': 'round_end',
                    'source': 'grenade_explosion',
                    'roll_modifier': -3,
                })
                health['exhaustion'] = CombatService._coerce_float(
                    health.get('exhaustion'), 0,
                ) + 1
                target.character.data = data
                flag_modified(target.character, 'data')

            trauma_checks=2 if profile.get('blast_trauma_checks')==2 else 1
            blast_traumas=[CombatService._apply_blast_trauma(target,blast,round_number=round_number) for _ in range(trauma_checks)]
            blast_trauma=blast_traumas[0]
            concussion_rounds=max((t.get('rounds',0) for t in blast_traumas if t and t.get('type')=='concussion'),default=0)
            if concussion and concussion_rounds:
                for effect in target.character.data['health'].get('effects') or []:
                    if effect.get('type')=='concussion':
                        effect['remaining']=5+concussion_rounds
                        effect['tick']='round_end'
                flag_modified(target.character,'data')

            shock = distance <= 1 and (blast_result['damage']>0 or applied_fragment_damage>0 or any(r['damage']>0 for r in extra_results))
            if shock:
                data = target.character.data if isinstance(target.character.data, dict) else {}
                health = data.setdefault('health', {})
                apply_effect_to_health(health, {
                    'type': 'shock', 'source': 'close_explosion',
                })
                target.posture = 'prone'
                target.character.data = data
                flag_modified(target.character, 'data')
            prone_facing_death = bool(
                posture_at_detonation == 'prone'
                and distance <= 1
                and CombatService._is_in_facing_arc(target, epicenter_x, epicenter_y)
                and not (cover.get('live_shield') or cover['protection']>5
                    or any(z.get('terrain') or z.get('class') not in {'conditional','flimsy'} for z in zones.values()))
            )
            if prone_facing_death:
                data = target.character.data if isinstance(target.character.data, dict) else {}
                health = data.setdefault('health', {})
                apply_effect_to_health(health, {
                    'type': 'death',
                    'name': '\u0421\u043c\u0435\u0440\u0442\u044c',
                    'source': 'prone_facing_grenade',
                    'tick': 'manual',
                })
                target.posture = 'prone'
                target.character.data = data
                flag_modified(target.character, 'data')
            incendiary_rounds = max(
                0, CombatService._coerce_int(profile.get('incendiary_rounds'), 0),
            )
            if incendiary_rounds and distance <= radius:
                from app.services.burning import ignite
                ignite(target,incendiary_rounds,source=profile.get('key'),damage=profile.get('burn_damage') or 50,source_character_id=profile.get('source_character_id'))
            CombatService._sync_location_effects_from_character(target)
            target_results.append({
                'character_id': target.character_id,
                'name': target.character.name,
                'distance': round(distance, 2),
                'cover_protection': round(cover['protection']),
                'blast_armor': round(blast_armor, 2),
                'blast_armor_damage': blast_armor_damage,
                'blast_damage': blast_result['damage'],
                'blast_trauma': blast_trauma,
                'blast_traumas': blast_traumas,
                'fragment_zone': fragment_zone,
                'fragment_damage': applied_fragment_damage,
                'fragment_penetration': round(fragment['penetration'], 2),
                'armor': round(armor, 2),
                'fragment_armor_damage': fragment_armor_damage,
                'penetrated': penetrated,
                'extra_fragment_traumas': extra_results,
                'pain_shock': shock,
                'concussion': concussion,
                'concussion_roll': concussion_roll,
                'concussion_chance': round(concussion_chance, 2),
                'death': prone_facing_death,
                'current_health': (
                    target.character.data.get('health', {}).get('current')
                    if isinstance(target.character.data, dict) else None
                ),
            })

        object_results = []
        for obj in objects if damage_objects else []:
            if level_of(obj) != floor_level:
                continue
            distance = CombatService._point_distance(
                epicenter_x, epicenter_y, obj.tile_x, obj.tile_y,
            )
            if distance > radius or not CombatService._is_cover_object(obj):
                continue
            from app.services.trap_catalog import radial_damage
            band=radial_damage(profile,distance)
            blast = max(0, round(band['blast'] if band else profile['blast_base'] - profile['blast_falloff'] * distance))
            fragment = max(0, round(band['fragments'] if band else profile['fragment'] * (1 - 0.1 * distance)))
            fragment_state = CombatService.apply_cover_damage(obj, fragment, 'fragment')
            if fragment_state['destroyed']:
                object_results.append({'object_id': obj.id, 'name': obj.name, 'fragment_damage': fragment, 'state': fragment_state})
                continue
            blast_state = CombatService.apply_cover_damage(obj, blast, 'blast')
            object_results.append({
                'object_id': obj.id, 'name': obj.name,
                'fragment_damage': fragment, 'blast_damage': blast,
                'state': blast_state,
            })
        area = CombatService._area_from_profile(
            profile, epicenter_x, epicenter_y, round_number,
        )
        if area:
            area['floor_level'] = floor_level
        CombatService._apply_direct_fire_hit(
            location_id, epicenter_x, epicenter_y, area,
            **({'target_ids':target_ids} if target_ids is not None else {}),
        )
        return finish({
            'profile': profile['key'],
            'name': profile['name'],
            'epicenter': {'x': float(epicenter_x), 'y': float(epicenter_y)},
            'radius': radius,
            'floor_level': floor_level,
            'targets': target_results,
            'objects': object_results,
            'area': area if create_area else None,
        })

    @staticmethod
    def _activate_explosive_event(state, event, round_number):
        profile = deepcopy(event.get('profile') or {})
        if not profile:
            return None
        result = CombatService.resolve_explosion(
            state.location_id,
            event.get('x'),
            event.get('y'),
            profile,
            round_number=round_number,
            **({'floor_level':event['floor_level']} if event.get('floor_level') else {}),
        )
        area = result.get('area')
        if isinstance(area, dict):
            areas = list(state.area_effects or [])
            areas.append(area)
            state.area_effects = areas
        return {
            'item_name': event.get('item_name') or profile.get('name'),
            'impact': {'x': float(event.get('x')), 'y': float(event.get('y'))},
            'detonated': not result.get('awaiting_gm',False),
            'explosion': result,
        }

    @staticmethod
    def _process_pending_explosives(state, *, phase, actor_id=None):
        pending = list(state.pending_explosives or [])
        remaining = []
        detonations = []
        for event in pending:
            if not isinstance(event, dict):
                continue
            due = False
            if phase == 'turn_end':
                due = (
                    event.get('trigger') == 'turn_end'
                    and CombatService._coerce_int(event.get('actor_id'), 0)
                    == CombatService._coerce_int(actor_id, -1)
                )
            elif phase == 'round_start':
                due = (
                    event.get('trigger') == 'round_start'
                    and CombatService._coerce_int(event.get('round'), 0)
                    <= CombatService._coerce_int(state.round_number, 0)
                )
            elif phase=='actor_turn_start':
                due=(event.get('trigger')=='actor_turn_start' and event.get('actor_id')==actor_id
                     and event.get('round',0)<=state.round_number)
            if due:
                resolved = CombatService._activate_explosive_event(
                    state, event, state.round_number,
                )
                if resolved:
                    detonations.append(resolved)
            else:
                remaining.append(event)
        original_ids={e.get('id') for e in pending if isinstance(e,dict)}
        state.pending_explosives = remaining+[e for e in state.pending_explosives or [] if e.get('id') not in original_ids]
        return detonations

    @staticmethod
    def _apply_area_effect_to_character(area, target, round_number, geometry_context=None):
        if target.id in area.get('excluded_actor_ids',[]):
            return None
        from app.services.cloud_geometry import prepare
        from app.services.explosion_geometry import distance as spatial_distance
        location,objects=geometry_context or (db.session.get(Location,target.location_id),LocationObject.query.filter_by(location_id=target.location_id).all())
        prepare(location,objects,area)
        if [math.floor(target.pos_x+.5),math.floor(target.pos_y+.5),target.floor_level or 0] not in area['cells']:return None
        distance = spatial_distance(location,objects,area['x'],area['y'],area.get('floor_level',0),target)
        if distance > CombatService._coerce_float(area.get('radius'), 0):
            return None
        area_type = str(area.get('type') or '')
        if area_type.startswith('smoke'):
            return {'character_id': target.character_id, 'type': 'smoke'}
        data = dict(target.character.data or {})
        health = data.setdefault('health', {})
        result = {'character_id': target.character_id, 'type': area_type}
        if area_type == 'gas':
            if CombatService._functioning_gas_protection(data):
                result['blocked_by_gas_mask'] = True
                return result
            pain = max(0, CombatService._coerce_int(area.get('pain'), 0))
            chemical_damage = max(0, CombatService._coerce_int(area.get('chemical_damage'), 0))
            if chemical_damage:
                damage_result = CombatService._apply_general_damage(
                    target, chemical_damage,
                    round_number=round_number,
                    source='chemical_cloud',
                )
                data = dict(target.character.data or {})
                health = data.setdefault('health', {})
                result['damage'] = damage_result['damage']
            if pain:
                apply_effect_to_health(health, {
                    'type': 'pain', 'value': pain, 'source': area.get('name') or 'gas',
                },character_data=data)
            chance = max(0, min(100, CombatService._coerce_int(area.get('concussion_chance'), 0)))
            if chance and random.randint(1, 100) <= chance:
                apply_effect_to_health(health, {
                    'type': 'concussion',
                    'name': '\u041a\u043e\u043d\u0442\u0443\u0437\u0438\u044f',
                    'remaining': 1,
                    'tick': 'turn_end',
                    'source': area.get('name') or 'gas',
                    'roll_modifier': -3,
                })
                result['concussion'] = True
        elif area_type == 'fire':
            rounds = max(1, CombatService._coerce_int(area.get('burn_rounds'), 1))
            from app.services.burning import ignite,extinguish_in_water
            if extinguish_in_water(target,include_traversed=False):
                result['extinguished_by_water']=True
                return result
            ignite(target,rounds,source=area.get('name') or 'fire_area',
                damage=area.get('burn_damage') or 50,threshold=area.get('thermal_threshold',35),
                inclusive=area.get('threshold_inclusive',False),
                extinguish_difficulty_modifier=area.get('extinguish_difficulty_modifier',0),source_character_id=area.get('source_character_id'))
            result['burning_rounds'] = rounds
        target.character.data = data
        flag_modified(target.character, 'data')
        CombatService._sync_location_effects_from_character(target)
        return result

    @staticmethod
    def _advance_area_effects(state):
        updated = []
        affected = []
        targets = LocationCharacter.query.filter_by(location_id=state.location_id).all()
        context=(db.session.get(Location,state.location_id),LocationObject.query.filter_by(location_id=state.location_id).all())
        for source in list(state.area_effects or []):
            if not isinstance(source, dict):
                continue
            area = dict(source)
            age = CombatService._coerce_int(area.get('age'), 0) + max(1,CombatService._coerce_int(area.get('age_step'),1))
            area['age'] = age
            previously_exposed = set(area.get('exposed_actor_ids') or []) if area.get('exit_pain_rounds') else set()
            currently_exposed = set()
            for target in targets:
                if target.character:
                    result = CombatService._apply_area_effect_to_character(
                        area, target, state.round_number, geometry_context=context,
                    )
                    if result:
                        affected.append(result)
                        if area.get('exit_pain_rounds') and result.get('type') == 'gas' and not result.get('blocked_by_gas_mask'):
                            currently_exposed.add(target.id)
            if area.get('exit_pain_rounds'):
                for target in targets:
                    if target.id not in previously_exposed - currently_exposed or not target.character:
                        continue
                    data = dict(target.character.data or {})
                    health = data.setdefault('health', {})
                    apply_effect_to_health(health, {
                        'type': 'periodic_adjustment', 'name': 'Боль после газового облака',
                        'source': 'gas_cloud_exit', 'area': area.get('id'),
                        'remaining': area['exit_pain_rounds'], 'tick': 'round_end',
                        'adjustments': [{'field': 'painLevel', 'delta': 1, 'min': 0, 'max': 10}],
                    }, character_data=data)
                    target.character.data = data
                    flag_modified(target.character, 'data')
                    CombatService._sync_location_effects_from_character(target)
                    affected.append({'character_id': target.character_id, 'type': 'gas_exit'})
                area['exposed_actor_ids'] = sorted(currently_exposed)
            area_type = str(area.get('type') or '')
            keep = True
            if area_type == 'smoke_growing':
                grow_rounds = max(0, CombatService._coerce_int(area.get('grow_rounds'), 0))
                if age <= grow_rounds:
                    area['radius'] = min(
                        CombatService._coerce_float(area.get('max_radius'), area.get('radius')),
                        CombatService._coerce_float(area.get('radius'), 0) + 2,
                    )
                else:
                    shrink_age = age - grow_rounds - CombatService._coerce_int(area.get('hold_rounds'), 0)
                    if shrink_age > 0:
                        area['radius'] = max(0, CombatService._coerce_float(area.get('radius'), 0) - CombatService._coerce_float(area.get('shrink_per_round'), 0))
                        keep = area['radius'] > 0
            elif area_type == 'smoke':
                if age > CombatService._coerce_int(area.get('hold_rounds'), 0):
                    area['radius'] = max(0, CombatService._coerce_float(area.get('radius'), 0) - CombatService._coerce_float(area.get('shrink_per_round'), 0))
                    keep = area['radius'] > 0
            else:
                keep = age < max(1, CombatService._coerce_int(area.get('duration_rounds'), 1))
            if keep:
                updated.append(area)
        from app.services.burning import advance_object_fires
        affected.extend(advance_object_fires(state.location_id))
        state.area_effects = updated
        return affected

    @staticmethod
    def resolve_fall(target, height_meters, *, round_number=0, damage_multiplier=1.0):
        """Resolve impact damage from a fall using the table from the combat rules."""
        height = max(0.0, CombatService._coerce_float(height_meters, 0))
        classified_height = math.floor(height)
        fall_table = (
            (1, 4, 0, 5, False, False),
            (4, 8, 0, 30, False, False),
            (7, 10, 45, 80, False, True),
            (10, 12, 70, 100, False, True),
            (13, 14, 140, 230, True, True),
            (16, 16, 200, 250, True, True),
            (21, 18, 300, 400, True, True),
            (float('inf'), 20, 500, 700, True, True),
        )
        difficulty = success_damage = failure_damage = 0
        success_shock = failure_shock = False
        for upper_bound, difficulty, success_damage, failure_damage, success_shock, failure_shock in fall_table:
            if classified_height < upper_bound:
                break
        attacker_data = target.character.data if target and target.character else {}
        agility_bonus = CombatService._skill_modifier(
            attacker_data, 'skills.physical.agility'
        )
        roll = random.randint(1, 20)
        from app.services.brigadier_order import boost_roll
        _rolls,roll,order_bonus=boost_roll(attacker_data,[roll])
        total = roll + agility_bonus+order_bonus
        success = total >= difficulty
        unscaled_damage = success_damage if success else failure_damage
        multiplier = max(0.0, CombatService._coerce_float(damage_multiplier, 1))
        base_damage = round(unscaled_damage * multiplier)
        profile = {
            'damage_type': 'crushing',
            'armor_piercing': 0,
            'damage': base_damage,
        }
        leg_results = []
        for leg in ('left_leg', 'right_leg'):
            from app.services.prostheses import hit_part
            limb_hit=hit_part(attacker_data,leg)
            protection, _ = CombatService._target_armor(attacker_data, leg,**({'limb_hit':limb_hit} if limb_hit else {}),**({'target':target} if background_is(attacker_data,'Стойкий') else {}))
            # Physical protection is stored as a percentage, as in the armor sheet.
            damage = max(0, round(base_damage * (1 - protection / 100)))
            health = CombatService._apply_attack_damage(
                target,
                damage,
                leg,
                profile,
                round_number=round_number,
                allow_bleeding=False,
                trauma_checks=0,
                stress_trigger='indirect_damage',
                **({'limb_hit':limb_hit} if limb_hit else {}),
            )
            leg_results.append({
                'area': leg,
                'protection': round(protection),
                'damage': (health.get('_attackOutcome') or {}).get('applied_damage',damage),
                **({'prosthesis_damage':(health.get('_attackOutcome') or {}).get('prosthesis_damage'),
                    'mechanical':limb_hit['mechanical']} if limb_hit else {}),
                'current': health.get('zones', {}).get(
                    'leftLeg' if leg == 'left_leg' else 'rightLeg', {}
                ).get('current'),
            })
        shock = bool(success_shock if success else failure_shock) and multiplier >= 1
        fracture_both_legs = bool(
            (not success and failure_damage >= 80)
            or (success and success_damage >= 140)
        ) and multiplier >= 1
        if shock or fracture_both_legs:
            data = target.character.data if isinstance(target.character.data, dict) else {}
            health = data.setdefault('health', {})
            if shock:
                apply_effect_to_health(health, {
                    'type': 'shock', 'source': 'fall', 'area': 'legs',
                })
            if fracture_both_legs:
                for leg in ('leftLeg', 'rightLeg'):
                    if any(item.get('mechanical') for item in leg_results if item['area']==('left_leg' if leg=='leftLeg' else 'right_leg')):continue
                    apply_effect_to_health(health, {
                        'type': 'fracture', 'source': 'fall', 'area': leg,
                    })
            sync_health_derived_statuses(health)
            target.character.data = data
            flag_modified(target.character, 'data')
        if shock:
            target.posture = 'prone'
            target.cover_object_id = None
            target.weapon_braced = False
            target.braced_weapon_index = None
        return {
            'height': height,
            'classified_height': classified_height,
            'difficulty': difficulty,
            'roll': roll,
            'agility_bonus': agility_bonus,
            'total': total,
            'success': success,
            'base_damage': base_damage,
            'unscaled_damage': unscaled_damage,
            'damage_multiplier': multiplier,
            'legs': leg_results,
            'shock': shock,
            'fracture_both_legs': fracture_both_legs,
        }

    @staticmethod
    def _resolve_block_counterattack(
        blocker, original_attacker, round_number, *, weapon_index_override=None,
        attack_type_override=None,
    ):
        blocker_data = (
            blocker.character.data
            if blocker.character and isinstance(blocker.character.data, dict)
            else {}
        )
        weapons = blocker_data.get('weapons') if isinstance(blocker_data.get('weapons'), list) else []
        weapon_index = CombatService._coerce_int(
            weapon_index_override
            if weapon_index_override is not None
            else getattr(blocker, 'drawn_weapon_index', -1),
            -1,
        )
        attack_type = 'unarmed'
        if 0 <= weapon_index < len(weapons):
            weapon = weapons[weapon_index] or {}
            template = (
                db.session.get(ItemTemplate, weapon.get('templateId'))
                if weapon.get('templateId') else None
            )
            weapon_category = template.category if template else weapon.get('category')
            if weapon_category == 'melee_weapon':
                allowed = CombatService._template_attributes(weapon).get('allowed_attacks') or []
                attack_type = attack_type_override or (allowed[0] if allowed else 'slashing')
            elif weapon_category == 'weapon':
                attack_type = 'firearm_butt'
            else:
                weapon_index = -1
        else:
            weapon_index = -1

        profile = (
            CombatService._virtual_melee_profile(attack_type, weapons[weapon_index] if weapon_index >= 0 else {}, blocker_data)
            or CombatService._weapon_damage_profile(
                weapons[weapon_index] if weapon_index >= 0 else {}, attack_type,
            )
        )
        melee_bonus = CombatService._skill_modifier(
            blocker_data, 'skills.physical.melee',
        )
        target_profile = CombatService._melee_target_profile(
            blocker,
            original_attacker,
            melee_bonus,
            CombatService._coerce_int(profile.get('accuracy'), 0),
        )
        details = {
            'weapon_index': weapon_index,
            'attack_type': attack_type,
            'round_number': round_number,
            'melee': True,
            'hit_difficulty': target_profile['difficulty'],
            'from_behind': target_profile['from_behind'],
            'target_prone': target_profile['target_prone'],
            'target_unconscious': target_profile['target_unconscious'],
            'automatic_hit': target_profile['automatic_hit'],
            'block_penalty': target_profile['block_penalty'],
            'block_arm': target_profile['block_arm'],
            'block_counterattack': False,
            'is_block_counterattack': True,
            'melee_advantage': target_profile['advantage'],
        }
        result = CombatService._resolve_attack(
            original_attacker,
            blocker,
            details,
            melee=True,
            attack_type=attack_type,
        )
        result['attack_type'] = attack_type
        result['block_counterattack'] = True
        return result

    @staticmethod
    def _drawn_melee_weapon(loc_char):
        data = (
            loc_char.character.data
            if loc_char.character and isinstance(loc_char.character.data, dict)
            else {}
        )
        weapons = data.get('weapons') if isinstance(data.get('weapons'), list) else []
        weapon_index = CombatService._coerce_int(loc_char.drawn_weapon_index, -1)
        if not 0 <= weapon_index < len(weapons) or not isinstance(weapons[weapon_index], dict):
            return None
        weapon = weapons[weapon_index]
        template = CombatService._weapon_template(weapon)
        category = str(
            (template.category if template else None) or weapon.get('category') or ''
        ).strip().lower()
        return (weapon_index, weapon) if category == 'melee_weapon' else None

    @staticmethod
    def _opportunity_attack_options(loc_char):
        data = (
            loc_char.character.data
            if loc_char.character and isinstance(loc_char.character.data, dict)
            else {}
        )
        weapons = data.get('weapons') if isinstance(data.get('weapons'), list) else []
        drawn = CombatService._drawn_melee_weapon(loc_char)
        if drawn:
            weapon_index, weapon = drawn
            allowed = CombatService._template_attributes(weapon).get('allowed_attacks') or []
            attack_type = str(allowed[0] if allowed else 'slashing')
            return [{
                'weapon_index': weapon_index,
                'attack_type': attack_type,
                'label': attack_type,
            }]
        if not CombatService._is_mutant_character(data):
            return []
        options = []
        for weapon_index, weapon in enumerate(weapons):
            if not isinstance(weapon, dict):
                continue
            attributes = CombatService._template_attributes(weapon)
            if not attributes.get('natural_weapon'):
                continue
            allowed = attributes.get('allowed_attacks') or []
            attack_type = str(allowed[0] if allowed else weapon.get('name') or '').strip()
            if attack_type.casefold() not in {'удар', 'укус'}:
                continue
            options.append({
                'weapon_index': weapon_index,
                'attack_type': attack_type,
                'label': attack_type,
            })
        return options

    @staticmethod
    def _position_is_behind(defender, x, y):
        facing_x = CombatService._coerce_int(defender.facing_x, 0)
        facing_y = CombatService._coerce_int(defender.facing_y, 1)
        relative_x = CombatService._coerce_int(x, 0) - defender.pos_x
        relative_y = CombatService._coerce_int(y, 0) - defender.pos_y
        return facing_x * relative_x + facing_y * relative_y < 0

    @staticmethod
    def _queue_opportunity_attacks(location_id, mover, path_tiles, state):
        if not state or state.status != 'active' or len(path_tiles or []) < 2:
            return
        if mover.grapple_target_id or mover.grappled_by_id:
            return
        current_round = max(1, state.round_number or 1)
        for defender in CombatService._unique_location_characters(
            LocationCharacter.query.filter_by(location_id=location_id).all()
        ):
            if (
                defender.id == mover.id
                or defender.grapple_target_id
                or defender.grappled_by_id
                or not defender.character
                or not CombatService._are_opponents(defender, mover)
                or CombatService._location_character_condition(defender)['state']
                in {'dead', 'critical', 'unconscious', 'pain_shock'}
                or not CombatService._opportunity_attack_options(defender)
            ):
                continue
            defender_data = (
                defender.character.data
                if isinstance(defender.character.data, dict) else {}
            )
            meta = defender_data.setdefault('health', {}).setdefault('combatMeta', {})
            if CombatService._coerce_int(meta.get('opportunityAttackUsedRound'), 0) == current_round:
                continue
            triggered = False
            reason = None
            trigger_position = None
            surfaces = getattr(mover, '_movement_surfaces', [])
            for step_index, (before, after) in enumerate(zip(path_tiles, path_tiles[1:])):
                before_level = surfaces[step_index]['level'] if surfaces else (getattr(mover, 'floor_level', 0) or 0)
                after_level = surfaces[step_index+1]['level'] if surfaces else before_level
                before_distance = max(abs(before[0] - defender.pos_x), abs(before[1] - defender.pos_y))
                after_distance = max(abs(after[0] - defender.pos_x), abs(after[1] - defender.pos_y))
                if before_level != (getattr(defender, 'floor_level', 0) or 0):
                    before_distance = math.inf
                if after_level != (getattr(defender, 'floor_level', 0) or 0):
                    after_distance = math.inf
                if before_distance == 1 and after_distance > 1:
                    triggered = True
                    reason = 'opponent_left_reach'
                    trigger_position = before
                    break
                if (
                    after_distance == 1
                    and not CombatService._position_is_behind(defender, before[0], before[1])
                    and CombatService._position_is_behind(defender, after[0], after[1])
                ):
                    triggered = True
                    reason = 'opponent_moved_behind'
                    trigger_position = after
                    break
            if not triggered:
                continue
            pending = meta.setdefault('opportunityAttacks', [])
            if any(
                CombatService._coerce_int(item.get('target_location_character_id'), 0) == mover.id
                and CombatService._coerce_int(item.get('round'), 0) == current_round
                for item in pending if isinstance(item, dict)
            ):
                continue
            pending.append({
                'id': f'opportunity-{defender.id}-{mover.id}-{current_round}',
                'round': current_round,
                'target_location_character_id': mover.id,
                'target_character_id': mover.character_id,
                'target_name': mover.character.name if mover.character else 'цель',
                'reason': reason,
                'trigger_x': trigger_position[0],
                'trigger_y': trigger_position[1],
                'attack_options': CombatService._opportunity_attack_options(defender),
            })
            defender.character.data = defender_data
            flag_modified(defender.character, 'data')

    @staticmethod
    def resolve_opportunity_attack(
        location_id, user_id, location_character_id, opportunity_id, accept,
        weapon_index=None, attack_type=None,
    ):
        location = CombatService._get_location(location_id)
        is_gm = CombatService._ensure_access(location, user_id)
        state = LocationCombatState.query.filter_by(location_id=location_id).first()
        if not state or state.status != 'active':
            raise ValidationError('Combat is not active')
        defender = LocationCharacter.query.filter_by(
            id=location_character_id, location_id=location_id,
        ).first()
        if not defender:
            raise NotFoundError('Character not found')
        if not CombatService._can_end_turn_for_character(defender, user_id, is_gm=is_gm):
            raise PermissionDenied('You do not control this character')
        defender_data = (
            defender.character.data
            if defender.character and isinstance(defender.character.data, dict)
            else {}
        )
        meta = defender_data.setdefault('health', {}).setdefault('combatMeta', {})
        pending = meta.get('opportunityAttacks')
        if not isinstance(pending, list):
            raise ValidationError('Opportunity attack is no longer available')
        opportunity = next(
            (item for item in pending if isinstance(item, dict) and item.get('id') == opportunity_id),
            None,
        )
        if not opportunity:
            raise ValidationError('Opportunity attack is no longer available')
        pending.remove(opportunity)
        attack = None
        target = None
        if accept:
            current_round = max(1, state.round_number or 1)
            if CombatService._coerce_int(opportunity.get('round'), 0) != current_round:
                raise ValidationError('Opportunity attack has expired')
            if CombatService._coerce_int(meta.get('opportunityAttackUsedRound'), 0) == current_round:
                raise ValidationError('Opportunity attack has already been used this round')
            options = CombatService._opportunity_attack_options(defender)
            selected = next((
                option for option in options
                if (
                    weapon_index is not None
                    and CombatService._coerce_int(option.get('weapon_index'), -1)
                    == CombatService._coerce_int(weapon_index, -2)
                    and str(option.get('attack_type') or '') == str(attack_type or '')
                )
            ), None)
            if selected is None and weapon_index is None and options:
                selected = options[0]
            if selected is None:
                raise ValidationError('Выбранная атака ближнего боя недоступна')
            target = db.session.get(
                LocationCharacter,
                CombatService._coerce_int(opportunity.get('target_location_character_id'), 0),
            )
            if not target or target.location_id != location_id:
                raise ValidationError('The departing target is no longer available')
            CombatService.ensure_character_can_act(defender)
            original_position = (target.pos_x, target.pos_y)
            target.pos_x = CombatService._coerce_int(
                opportunity.get('trigger_x'), target.pos_x,
            )
            target.pos_y = CombatService._coerce_int(
                opportunity.get('trigger_y'), target.pos_y,
            )
            try:
                result = CombatService._resolve_block_counterattack(
                    defender, target, current_round,
                    weapon_index_override=selected['weapon_index'],
                    attack_type_override=selected['attack_type'],
                )
            finally:
                target.pos_x, target.pos_y = original_position
            result.pop('block_counterattack', None)
            result['opportunity_attack'] = True
            attack = {
                'melee': True,
                'fire_mode': 'opportunity',
                'attack_type': 'Атака по возможности',
                'results': [result],
                'damage_total': CombatService._coerce_float(result.get('damage'), 0),
            }
            meta['opportunityAttackUsedRound'] = current_round
            meta['opportunityAttacks'] = []
        defender.character.data = defender_data
        flag_modified(defender.character, 'data')
        db.session.commit()
        return {
            'character': CombatService._serialize_character(
                defender, current_turn_id=state.current_location_character_id,
                combat_state=state,
            ),
            'target': (
                CombatService._serialize_character(
                    target, current_turn_id=state.current_location_character_id,
                    combat_state=state,
                ) if target else None
            ),
            'state': CombatService._serialize_state(location, state),
            'attack': attack,
            'accepted': bool(accept),
        }

    @staticmethod
    def _resolve_attack(
        target,
        attacker,
        attack_details,
        *,
        melee=False,
        attack_type=None,
        aimed_zone=None,
        forced_roll=None,
        profile_override=None,
        profile_adjusted=False,
        ignore_live_shield=False,
        ignore_cover=False,
    ):
        attacker_data = attacker.character.data if attacker.character and isinstance(attacker.character.data, dict) else {}
        target_data = target.character.data if target.character and isinstance(target.character.data, dict) else {}
        if not melee:
            from app.services.body_transport import ensure_firearm_allowed
            ensure_firearm_allowed(attacker)
        from app.services.character_rules import harm_check,evasion_penalty
        permission=harm_check(attacker,target,not melee)
        if permission and not permission['success']:
            return {'hit':False,'damage':0,'target_character_id':target.character_id,'target_name':CombatService._display_name(target.character),
                'harm_check':permission,'blocked_by_will':True,'roll':None,'rolls':[],'difficulty':None,'mode':attack_details.get('fire_mode','melee')}
        if not melee:
            weapon_index = CombatService._coerce_int(attack_details.get('weapon_index'), -1)
            weapons = attacker_data.get('weapons') or []
            if 0 <= weapon_index < len(weapons):
                profile, _ = CombatService._ranged_damage_profile(weapons[weapon_index])
                flash_result = CombatService._resolve_flash_round(target, attacker, attack_details, profile)
                if flash_result is not None:
                    return flash_result
        was_alive=CombatService._character_condition(target_data)['state']!='dead'
        if getattr(target,'character_id',getattr(getattr(target,'character',None),'id',None)) in ((attacker_data.get('health') or {}).get('combatMeta') or {}).get('observerTargets',[]):
            attack_details={**attack_details,'hit_difficulty':attack_details.get('hit_difficulty',12)-1}
        from app.services.brigadier_order import consume
        order=consume(attacker_data)
        if order:
            attack_details={**attack_details,'hit_difficulty':attack_details.get('hit_difficulty',12)-3}
            if order.get('advantage'):attack_details['melee_advantage' if melee else 'shooting_advantage']=True
        if CombatService._mutant_has_trait(target_data, 'Миролюбивый'):
            target_data.setdefault('health', {}).setdefault('combatMeta', {})[
                'mutantProvoked'
            ] = True
            target.character.data = target_data
            flag_modified(target.character, 'data')
        target_character = getattr(target, 'character', None)
        target_character_id = getattr(
            target,
            'character_id',
            getattr(target_character, 'id', None),
        )
        target_name = CombatService._display_name(target_character)
        weapons = attacker_data.get('weapons') or []
        weapon_index = CombatService._coerce_int(attack_details.get('weapon_index'), -1)
        weapon = weapons[weapon_index] if 0 <= weapon_index < len(weapons) else {}
        if melee:
            profile = (
                CombatService._virtual_melee_profile(attack_type, weapon, attacker_data)
                or CombatService._weapon_damage_profile(weapon, attack_type)
            )
            profile = dict(profile_override) if isinstance(profile_override, dict) else dict(profile)
            normalized_attack_type = str(attack_type or '').strip().casefold()
            if (
                'затаптывание' in normalized_attack_type
                and CombatService._posture_key(target) == 'prone'
                and CombatService._mutant_has_trait(attacker_data, 'Тяжелые удары')
            ):
                profile['damage'] = CombatService._coerce_float(profile.get('damage'), 0) + 100
            artifact_melee_bonus = artifact_passive_profile(attacker_data)[
                'melee_damage_percent'
            ]
            if artifact_melee_bonus:
                profile['damage'] = max(
                    0,
                    CombatService._coerce_float(profile.get('damage'), 0)
                    * (1 + artifact_melee_bonus / 100),
                )
            skill = CombatService._skill_modifier(attacker_data, 'skills.physical.melee')
            stress_check_modifier = CombatService._consume_stress_check_modifier(
                attacker_data, is_attack=True,
            )
            difficulty = CombatService._coerce_int(
                attack_details.get('hit_difficulty'),
                12 - skill - CombatService._parse_percent(profile.get('accuracy', 0), 0),
            )
            difficulty-=weapon_accuracy_bonus(attacker_data,weapon,melee=True)
            from app.services.character_traits import target_attack_accuracy
            difficulty-=target_attack_accuracy(attacker_data,target_data,ranged=False)
            difficulty+=evasion_penalty(target_data)
            weather=weather_at(getattr(attacker,'location',None),attacker)
            difficulty-=weather['accuracy']
            automatic_hit = bool(attack_details.get('automatic_hit'))
            rolls = (
                []
                if automatic_hit
                else [forced_roll if forced_roll is not None else random.randint(1, 20)]
            )
            has_disadvantage = bool(
                attack_details.get('melee_disadvantage')
                or weather['accuracy_disadvantage']
                or CombatService._has_roll_disadvantage(
                    attacker_data, 'skills.physical.melee'
                )
            )
            has_advantage = bool(
                attack_details.get('melee_advantage')
                or getattr(target, 'grappled_by_id', None)
                or (
                    not automatic_hit
                    and CombatService._has_roll_advantage(
                        attacker_data, 'skills.physical.melee', consume=True,
                    )
                )
            )
            if not automatic_hit and forced_roll is None and has_advantage != has_disadvantage:
                rolls.append(random.randint(1, 20))
            if automatic_hit:
                roll = None
            elif has_advantage and not has_disadvantage:
                roll = max(rolls)
            elif has_disadvantage and not has_advantage:
                roll = min(rolls)
            else:
                roll = rolls[0]
            hit = automatic_hit or roll == 20 or (
                roll != 1 and roll + stress_check_modifier >= difficulty
            )
            result = {
                'roll': roll,
                'rolls': rolls,
                'total': None if roll is None else roll + stress_check_modifier,
                'difficulty': difficulty,
                'hit': hit,
                'mode': 'melee',
                'weather': weather,
                'target_character_id': target_character_id,
                'target_name': target_name,
                'stress_check_modifier': stress_check_modifier,
                'automatic_hit': automatic_hit,
                'advantage': has_advantage,
                'disadvantage': has_disadvantage,
                'from_behind': bool(attack_details.get('from_behind')),
                'target_prone': bool(attack_details.get('target_prone')),
                'target_unconscious': bool(attack_details.get('target_unconscious')),
            }
            effective_roll = None if roll is None else roll + stress_check_modifier
            shortfall = (
                max(0, difficulty - effective_roll)
                if effective_roll is not None
                else 0
            )
            block_penalty = max(
                0, CombatService._coerce_int(attack_details.get('block_penalty'), 0),
            )
            counterattack_triggered = bool(
                not hit
                and block_penalty > 0
                and shortfall >= 10
                and attack_details.get('block_counterattack')
                and not attack_details.get('is_block_counterattack')
            )
            partial_block = bool(
                not hit
                and not counterattack_triggered
                and block_penalty > 0
                and 0 < shortfall <= block_penalty
            )
            result.update({
                'block_shortfall': shortfall,
                'partial_block': partial_block,
                'block_counterattack_triggered': counterattack_triggered,
            })
            if counterattack_triggered:
                result['counterattack_result'] = CombatService._resolve_block_counterattack(
                    target,
                    attacker,
                    attack_details.get('round_number'),
                )
            if partial_block:
                hit = True
                result['hit'] = True
                result['block_arm'] = attack_details.get('block_arm', 'right_arm')
            if not hit:
                return result
            zone = (
                attack_details.get('block_arm', 'right_arm')
                if partial_block
                else CombatService._random_hit_zone(random.randint(1, 20), aimed_zone, melee=True)
            )
            strength_bonus = CombatService._skill_modifier(attacker_data, 'skills.physical.strength')
            if not profile.get('skip_strength_scaling'):
                from app.services.weapon_grip import pair,melee as melee_weapon
                paired=pair(attacker)
                coefficient=.05 if paired and all(melee_weapon(item) for item in paired) and not trait_count(attacker_data,'Амбидекстр') else .1
                profile['damage'] *= max(0, 1 + coefficient * strength_bonus)
            if attack_details.get('swing_bonus'):
                profile['damage'] *= 1.25
            if partial_block:
                profile['damage'] *= 0.5
        else:
            if isinstance(profile_override, dict):
                profile = dict(profile_override)
            else:
                profile, _ = CombatService._ranged_damage_profile(weapon)
            difficulty = attack_details['hit_difficulty']
            difficulty+=evasion_penalty(target_data)
            from app.services.character_traits import selected_weapon_accuracy
            difficulty-=selected_weapon_accuracy(attacker_data,weapon,target_data)
            from app.services.character_traits import target_attack_accuracy
            difficulty-=target_attack_accuracy(attacker_data,target_data)
            if attack_details.get('fire_mode') == 'area':
                difficulty += CombatService._shooting_movement_modifiers(
                    target_mode=getattr(target, 'movement_mode_this_turn', None),
                    target_evading=CombatService._movement_evasion_active(target),
                )['difficulty_penalty']
                difficulty += CombatService._target_evasion_trait_penalty(target)
                if getattr(target, 'location_id', None) is not None:
                    attack_details = dict(attack_details, cover=CombatService._cover_analysis(
                        target.location_id, attacker, target,
                    ))
            stress_check_modifier = CombatService._consume_stress_check_modifier(
                attacker_data, is_attack=True,
            )
            rolls = [forced_roll if forced_roll is not None else random.randint(1, 20)]
            has_disadvantage = bool(attack_details.get('shooting_disadvantage'))
            has_advantage = bool(
                attack_details.get('shooting_advantage')
                or CombatService._has_roll_advantage(
                    attacker_data, 'skills.physical.shooting', consume=True,
                )
            )
            if forced_roll is None and has_advantage != has_disadvantage:
                rolls.append(random.randint(1, 20))
            if has_advantage and not has_disadvantage:
                roll = max(rolls)
            elif has_disadvantage and not has_advantage:
                roll = min(rolls)
            else:
                roll = rolls[0]
            automatic_hit = bool(attack_details.get('automatic_firearm_hit'))
            hit = automatic_hit or roll == 20 or (
                roll != 1 and roll + stress_check_modifier >= difficulty
            )
            post_bonus_hit=bool(not hit and attack_details.get('post_bonus_available'))
            if post_bonus_hit:hit=True
            result = {
                'roll': roll,
                'rolls': rolls,
                'total': None if automatic_hit else roll + stress_check_modifier,
                'difficulty': difficulty,
                'hit': hit,
                'mode': attack_details['fire_mode'],
                'post_bonus_hit':post_bonus_hit,
                'advantage': has_advantage,
                'disadvantage': has_disadvantage,
                'strength_requirement': attack_details.get('strength_requirement'),
                'target_character_id': target_character_id,
                'target_name': target_name,
                'stress_check_modifier': stress_check_modifier,
                'automatic_hit': automatic_hit,
            }
            if not profile_adjusted:
                if (
                    attack_details.get('target_distance') is not None
                    and CombatService._is_buckshot_profile(profile)
                ):
                    distance_over = max(0, attack_details['target_distance'] - 5)
                    profile['damage'] = max(0, profile['damage'] - 50 * distance_over)
                    profile['armor_piercing'] = max(
                        0,
                        profile['armor_piercing'] - 5 * distance_over,
                    )
                elif attack_details.get('target_distance') is not None and profile.get('effective_range', 0):
                    distance_over = max(0, attack_details['target_distance'] - profile['effective_range'])
                    if distance_over:
                        profile['damage'] *= max(0.1, 1 - 0.05 * distance_over)
                        profile['armor_piercing'] = max(0, profile['armor_piercing'] - 5 * distance_over)
            live_shield = (
                None
                if ignore_live_shield
                else CombatService._live_shield_target(target)
            )
            if not hit:
                if live_shield and aimed_zone == 'head':
                    shield_result = CombatService._resolve_attack(
                        live_shield,
                        attacker,
                        attack_details,
                        aimed_zone='head',
                        forced_roll=20,
                        profile_override=profile,
                        profile_adjusted=True,
                        ignore_live_shield=True,
                    )
                    result.update({
                        'zone': 'head',
                        'damage': 0,
                        'combined_damage': shield_result.get('damage', 0),
                        'live_shield_hit': True,
                        'live_shield_reason': 'aimed_head_miss',
                        'live_shield_result': shield_result,
                    })
                cover_analysis = attack_details.get('cover') or {}
                if (
                    attack_details.get('fire_mode') == 'unaimed'
                    and cover_analysis.get('blind_fire')
                    and not ignore_cover
                ):
                    cover_zones = cover_analysis.get('zones') or {}
                    cover_details = next(iter(cover_zones.values()), None)
                    cover = (
                        db.session.get(LocationObject, cover_details.get('object_id'))
                        if isinstance(cover_details, dict)
                        else None
                    )
                    if cover:
                        cover_profile = CombatService._cover_profile(cover)
                        incoming_penetration = CombatService._coerce_float(
                            profile.get('armor_piercing'), 0
                        )
                        cover_damage = CombatService.apply_cover_damage(
                            cover,
                            profile.get('damage'),
                            profile.get('damage_type') or 'bullet',
                        )
                        result['automatic_cover_hit'] = True
                        result['cover_hit'] = {
                            'object_id': cover.id,
                            'object_name': cover.name or cover.type,
                            'protection': cover_profile['physical_protection'],
                            'penetrated': (
                                incoming_penetration
                                >= cover_profile['physical_protection']
                            ),
                            'damage': cover_damage,
                        }
                return result
            zone = CombatService._random_hit_zone(random.randint(1, 20), aimed_zone)
            cover_zone = (
                ((attack_details.get('cover') or {}).get('zones') or {}).get(zone)
                if not ignore_cover
                else None
            )
            if cover_zone:
                if cover_zone.get('terrain'):
                    result.update({'zone': zone, 'damage': 0, 'combined_damage': 0, 'blocked_by_terrain': True})
                    return result
                cover = db.session.get(LocationObject, cover_zone.get('object_id')) if cover_zone.get('object_id') else None
                cover_profile = CombatService._cover_profile(cover) if cover else cover_zone if cover_zone.get('procedural_tree') else None
                if cover_profile and random.randint(1, 100) <= cover_profile['mesh_hit_chance']:
                    incoming_penetration = CombatService._coerce_float(profile.get('armor_piercing'), 0)
                    cover_damage = CombatService.apply_cover_damage(
                        cover,
                        profile.get('damage'),
                        profile.get('damage_type') or 'bullet',
                    ) if cover else None
                    penetrated = incoming_penetration >= cover_profile['physical_protection']
                    result['cover_hit'] = {
                        'object_id': cover.id if cover else None,
                        'object_name': (cover.name or cover.type) if cover else cover_zone['object_name'],
                        'protection': cover_profile['physical_protection'],
                        'penetrated': penetrated,
                        'damage': cover_damage,
                    }
                    if not penetrated:
                        result.update({
                            'zone': zone, 'damage': 0, 'combined_damage': 0,
                            'blocked_by_cover': True,
                        })
                        return result
                    profile['armor_piercing'] = max(
                        0, incoming_penetration - cover_profile['physical_protection']
                    )
            live_shield_result = None
            if live_shield and zone != 'head':
                live_shield_result = CombatService._resolve_attack(
                    live_shield,
                    attacker,
                    attack_details,
                    aimed_zone=zone,
                    forced_roll=20,
                    profile_override=profile,
                    profile_adjusted=True,
                    ignore_live_shield=True,
                )
                incoming_penetration = CombatService._coerce_float(
                    profile.get('armor_piercing'), 0
                )
                shield_armor = CombatService._coerce_float(
                    live_shield_result.get('armor'), 0
                )
                remaining_penetration = max(0, incoming_penetration - shield_armor)
                if incoming_penetration < shield_armor:
                    result.update({
                        'zone': zone,
                        'damage': 0,
                        'combined_damage': live_shield_result.get('damage', 0),
                        'live_shield_hit': True,
                        'live_shield_blocked': True,
                        'live_shield_result': live_shield_result,
                    })
                    return result
                profile['armor_piercing'] = remaining_penetration
        if (
            CombatService._is_gas_or_chemical_profile(profile)
            and CombatService._functioning_gas_protection(target_data)
        ):
            result.update({
                'zone': zone,
                'base_damage': profile['damage'],
                'armor': 100,
                'armor_piercing': profile['armor_piercing'],
                'effective_armor': 100,
                'penetration_deficit': 100,
                'damage_multiplier': 0,
                'damage': 0,
                'armor_damage': [],
                'bleeding_check': None,
                'gas_or_chemical_blocked': True,
                'health': (target_data.get('health') or {}).get('current'),
                'zone_health': (
                    (target_data.get('health') or {}).get('zones') or {}
                ).get({
                    'left_arm': 'leftArm', 'right_arm': 'rightArm',
                    'left_leg': 'leftLeg', 'right_leg': 'rightLeg',
                }.get(zone, zone), {}).get('current'),
            })
            return result
        # Сигнальные/осветительные патроны не создают ран и износа брони при нулевом уроне.
        if not melee and profile['damage'] <= 0:
            result.update(zone=zone,base_damage=0,damage=0,armor_damage=[],
                          bleeding_check=None,bleedings=[],additional_traumas=[],non_damaging_round=True)
            return result
        head_impact_roll = random.randint(1, 20) if zone == 'head' else None
        from app.services.prostheses import hit_part
        limb_hit=hit_part(target_data,zone)
        head_impact = (
            CombatService._head_impact_profile(
                head_impact_roll, attacker, target,
            )
            if head_impact_roll is not None else None
        )
        mercy=attack_details.get('sniper_mode')=='mercy'
        if mercy and head_impact:
            head_impact={**head_impact,'damage_head':True,'primary_organ':None,
                'damage_skull_if_penetrated':False,'secondary_organs_if_penetrated':[]}
        armor_target_data = target_data
        if head_impact:
            armor_target_data = dict(target_data)
            armor_target_data['_headProtectionZone'] = head_impact.get('armor_zone')
        plate_kwargs={}
        if zone in {'chest','abdomen'}:
            vest=((armor_target_data.get('equipment') or {}).get('vest') or {})
            if isinstance(vest,dict) and any(isinstance(pouch,dict) and
                any(isinstance(slot,dict) and slot.get('type')=='armor_plate'
                    for slot in (pouch.get('attributes') or {}).get('slots') or [])
                for pouch in vest.get('pouches') or []):
                plate_kwargs={'plate_side':'back' if CombatService._is_behind(attacker,target) else 'front'}
        armor, armor_layers = CombatService._target_armor(armor_target_data, zone,
            **({'limb_hit':limb_hit} if limb_hit else {}),**({'target':target} if background_is(armor_target_data,'Стойкий') else {}),
            **plate_kwargs)
        crystal_layers=[layer['item'] for layer in armor_layers if layer.get('liquid_crystal_first')]
        crystal_first=bool(crystal_layers and profile['damage']>0)
        back_protection = (
            CombatService._mutant_back_torso_protection(armor_target_data)
            if zone in {'chest', 'abdomen'}
            else None
        )
        if back_protection is not None and CombatService._is_behind(attacker, target):
            armor = back_protection
            for layer in armor_layers:
                if layer.get('slot') == 'mutant':
                    layer['protection'] = back_protection
        armor_damage = []
        incendiary_protection = (CombatService._target_elemental_protection(target_data,'thermal',zone,
            **({'target':target} if background_is(target_data,'Стойкий') else {}))
            if not melee and profile.get('ammo_variant') == 'incendiary' else None)
        artifact_armor_reduction = artifact_passive_profile(target_data)[
            'armor_damage_reduction'
        ]
        for layer in armor_layers:
            if layer.get('is_gas_mask'):
                damage_result = CombatService._damage_gas_mask(
                    layer['item'],
                    'melee' if melee else 'bullet',
                )
            else:
                damage_result = CombatService._damage_armor_item(
                    layer['item'],
                    layer['attributes'],
                    max(0, profile['damage'] - artifact_armor_reduction),
                )
            if damage_result:
                armor_damage.append(damage_result)
        if crystal_first:
            from app.services.equipment_care import consume_liquid_crystal
            for item in crystal_layers:consume_liquid_crystal(item)
        effective_armor = max(0.0, armor - profile['armor_piercing'])
        penetration_deficit = max(0.0, effective_armor)
        damage_multiplier = CombatService._penetration_damage_multiplier(profile['armor_piercing'],armor)
        crushing_non_penetration = False
        crushing_multiplier = (
            CombatService._crushing_damage_multiplier(profile)
            if melee and penetration_deficit > 0
            else None
        )
        if crushing_multiplier is not None:
            damage_multiplier = crushing_multiplier
            crushing_non_penetration = True
        behind_armor_multiplier = 0.0
        full_non_penetration = bool(
            not melee
            and armor > 0
            and penetration_deficit > 0
            and damage_multiplier <= 0
        )
        buckshot_mutant_non_penetration = bool(
            not melee
            and penetration_deficit >= 10
            and CombatService._is_buckshot_profile(profile)
            and CombatService._is_mutant_character(target_data)
        )
        if full_non_penetration or buckshot_mutant_non_penetration:
            behind_armor_multiplier = CombatService._behind_armor_damage_multiplier(
                profile,
                target_data=target_data,
                penetration_deficit=penetration_deficit,
            )
            damage_multiplier = behind_armor_multiplier
        incoming_artifact_modifier = 0 if limb_hit and limb_hit['mechanical'] else artifact_passive_profile(target_data)[
            'incoming_physical_percent'
        ]
        final_damage = max(0, round(
            profile['damage']
            * damage_multiplier
            * max(0, 1 + incoming_artifact_modifier / 100),
        ))
        armor_penetrated = bool(
            armor <= 0 or profile['armor_piercing'] >= armor
        )
        if crystal_first and not armor_penetrated:
            final_damage=0
            behind_armor_multiplier=0
        allow_bleeding = bool(
            melee
            or armor <= 0
            or profile['armor_piercing'] - armor >= 10
        )
        if crystal_first and not armor_penetrated:
            allow_bleeding=False
        bleeding_result = (
            None
            if melee or limb_hit and limb_hit['mechanical']
            else CombatService._roll_firearm_bleeding(profile, armor)
        )
        if crystal_first and not armor_penetrated:bleeding_result=None
        trauma_checks = 1
        trauma_difficulty_modifier = CombatService._mutant_trauma_difficulty_modifier(
            target_data,
        )
        if not melee and CombatService._is_buckshot_profile(profile):
            trauma_checks = math.floor(final_damage / 50)
            trauma_difficulty_modifier -= 20
        elif (
            not melee
            and CombatService._caliber_key(profile.get('caliber')) == '127x55'
            and armor <= profile['armor_piercing']
        ):
            trauma_checks = 2
        if not melee and profile.get('ammo_variant') == 'explosive':
            trauma_checks += 1
            trauma_difficulty_modifier -= 15
        if mercy:trauma_checks=0
        zone_key={'left_arm':'leftArm','right_arm':'rightArm','left_leg':'leftLeg','right_leg':'rightLeg'}.get(zone,zone)
        zone_before=((target_data.get('health') or {}).get('zones') or {}).get(zone_key,{}).get('current',1)
        health = CombatService._apply_attack_damage(
            target,
            final_damage,
            zone,
            profile,
            bleeding_result=bleeding_result,
            round_number=attack_details.get('round_number'),
            allow_bleeding=allow_bleeding,
            trauma_checks=trauma_checks,
            trauma_difficulty_modifier=trauma_difficulty_modifier,
            attacker=attacker,
            head_impact=head_impact,
            armor_penetrated=armor_penetrated,
            prepared_trauma_rolls=(
                [head_impact_roll] if head_impact_roll is not None else None
            ),
            **({'precision_attacker':attacker} if attack_details.get('sniper_mode')=='precision' else {}),
            **({'limb_hit':limb_hit} if limb_hit else {}),
        )
        attack_outcome = health.pop('_attackOutcome', {})
        if limb_hit:result['limb_hit']={key:limb_hit[key] for key in ('roll','label','mechanical')}
        if attack_outcome.get('prosthesis_damage'):result['prosthesis_damage']=attack_outcome['prosthesis_damage']
        if incendiary_protection is not None and incendiary_protection < 50:
            from app.services.burning import ignite
            ignite(target,3,source='ammunition_incendiary',source_character_id=getattr(attacker,'character_id',None))
            result['ignited_rounds'] = 3
        if not melee and armor_penetrated and final_damage > 0 and not (limb_hit and limb_hit['mechanical']):
            extra_pain = profile.get('pain_on_penetration', 0)
            if extra_pain:
                apply_effect_to_health(health, {'type':'pain','value':extra_pain,
                    'source':'ammunition_penetration'},character_data=target.character.data)
                result['ammunition_pain'] = extra_pain
            if profile.get('pain_per_round'):
                apply_effect_to_health(health, {'type':'periodic_adjustment',
                    'name':'Боль от газового патрона','source':'ammunition_gas',
                    'value':profile['pain_per_round'],'remaining':profile['pain_rounds'],
                    'tick':'round_end','adjustments':[{'field':'painLevel','delta':profile['pain_per_round'],
                        'min':0,'max':10}]},character_data=target.character.data)
            if zone == 'head' and profile.get('pepper_round'):
                pepper = CombatService._apply_pepper_head_hit(health, target.character.data)
                result['pepper_head_hit'] = pepper
        final_damage=attack_outcome.get('applied_damage',final_damage)
        result.update({
            'zone': zone,
            'base_damage': profile['damage'],
            'armor': armor,
            'armor_piercing': profile['armor_piercing'],
            'effective_armor': effective_armor,
            'penetration_deficit': penetration_deficit,
            'damage_multiplier': damage_multiplier,
            'behind_armor_multiplier': behind_armor_multiplier,
            'crushing_non_penetration': crushing_non_penetration,
            'crushing_damage_multiplier': (
                crushing_multiplier if crushing_non_penetration else None
            ),
            'full_non_penetration': full_non_penetration,
            'buckshot_mutant_non_penetration': buckshot_mutant_non_penetration,
            'damage': final_damage,
            'armor_damage': armor_damage,
            'head_impact': head_impact,
            'bleeding_check': bleeding_result,
            'bleedings': attack_outcome.get('bleedings') or [],
            'additional_traumas': attack_outcome.get('additional_traumas') or [],
            'catastrophic_limb_injury': attack_outcome.get('catastrophic_limb_injury'),
            'death': bool(attack_outcome.get('death')),
            'health': health.get('current'),
            'zone_health': (
                health.get('zones') or {}
            ).get({'left_arm': 'leftArm', 'right_arm': 'rightArm', 'left_leg': 'leftLeg', 'right_leg': 'rightLeg'}.get(zone, zone), {}).get('current'),
        })
        if melee and result.get('hit') and not (limb_hit and limb_hit['mechanical']):
            CombatService._apply_mutant_melee_hit_effects(
                attacker, target, attack_type, final_damage, result,
            )
        from app.services.character_rules import shotgun_effects,after_kill
        if not melee:
            result.update(shotgun_effects(attacker,target,weapon,profile,zone,zone_before,final_damage))
        after_kill(attacker,target,was_alive)
        if not melee and 'live_shield_result' in locals() and live_shield_result:
            result.update({
                'live_shield_hit': True,
                'live_shield_blocked': False,
                'live_shield_result': live_shield_result,
                'combined_damage': (
                    final_damage + live_shield_result.get('damage', 0)
                ),
            })
        return result

    @staticmethod
    def _resolve_cover_attack(location_id, cover, attacker, attack_details):
        from app.services.body_transport import ensure_firearm_allowed
        ensure_firearm_allowed(attacker)
        attacker_data = attacker.character.data if attacker.character else {}
        weapons = (attacker_data or {}).get('weapons') or []
        weapon_index = CombatService._coerce_int(attack_details.get('weapon_index'), -1)
        weapon = weapons[weapon_index] if 0 <= weapon_index < len(weapons) else {}
        profile, _ = CombatService._ranged_damage_profile(weapon)
        attack_roll = random.randint(1, 20)
        result = {
            'roll': attack_roll, 'rolls': [attack_roll], 'difficulty': None,
            'hit': True, 'automatic_cover_hit': True,
            'mode': attack_details.get('fire_mode'),
            'target_object_id': cover.id,
            'target_name': cover.name or cover.type,
        }
        cover_profile = CombatService._cover_profile(cover)
        cover_damage = CombatService.apply_cover_damage(
            cover, profile.get('damage'), profile.get('damage_type') or 'bullet'
        )
        penetration = CombatService._coerce_float(profile.get('armor_piercing'), 0)
        penetrated = penetration >= cover_profile['physical_protection']
        result.update({
            'cover_hit': True,
            'cover_penetrated': penetrated,
            'cover_protection': cover_profile['physical_protection'],
            'cover_damage': cover_damage,
            'damage': 0,
        })
        if not penetrated:
            return result
        behind = CombatService._characters_behind_cover(location_id, attacker, cover)
        if not behind:
            return result
        target = behind[0]
        continued_profile = dict(profile)
        continued_profile['armor_piercing'] = max(
            0, penetration - cover_profile['physical_protection']
        )
        continued_details = dict(attack_details)
        continued_details['target_character_id'] = target.character_id
        continued_details['cover'] = None
        target_distance = max(
            abs(attacker.pos_x - target.pos_x), abs(attacker.pos_y - target.pos_y)
        )
        continued_details['target_distance'] = target_distance
        continued_details['automatic_firearm_hit'] = True
        target_result = CombatService._resolve_attack(
            target, attacker, continued_details,
            forced_roll=attack_roll,
            profile_override=continued_profile,
            profile_adjusted=True,
            ignore_cover=True,
        )
        result.update({
            'target_behind_cover_id': target.character_id,
            'target_behind_cover_name': getattr(target.character, 'name', None),
            'target_behind_cover_result': target_result,
            'damage': target_result.get('damage', 0),
            'combined_damage': target_result.get('combined_damage', target_result.get('damage', 0)),
        })
        return result

    @staticmethod
    def _resolve_shot_sequence(
        targets,
        attacker,
        attack_details,
        *,
        aimed_zone=None,
        share_hit_roll=False,
    ):
        results = []
        shared_roll = None
        requested_shots = CombatService._coerce_int(
            attack_details.get('requested_shot_count', attack_details.get('shot_count')), 0,
        )
        attacker_character = getattr(attacker, 'character', None)
        attacker_data = (
            attacker_character.data
            if attacker_character and isinstance(attacker_character.data, dict)
            else {}
        )
        weapons = attacker_data.get('weapons') if isinstance(attacker_data.get('weapons'), list) else []
        weapon_index = CombatService._coerce_int(attack_details.get('weapon_index'), -1)
        weapon = weapons[weapon_index] if 0 <= weapon_index < len(weapons) else None
        shot_states = []
        triggered_jams = []
        post_bonus=background_is(attacker_data,'Пост принял') and attack_details.get('fire_mode')=='burst'
        volley_size=max(1,requested_shots//max(1,CombatService._coerce_int(attack_details.get('volley_count'),1)))
        post_used=set()
        for index in range(requested_shots):
            if not targets:
                break
            target = targets[index % len(targets)]
            shot_details = dict(attack_details)
            shot_details['post_bonus_available']=post_bonus and index//volley_size not in post_used
            jam_effects = (
                CombatService._weapon_jam_effects(weapon)
                if isinstance(weapon, dict)
                else {
                    'accuracy_penalty': 0,
                    'shooting_disadvantage': False,
                    'jams': [],
                    'blocks_fire': False,
                }
            )
            if jam_effects['blocks_fire']:
                break
            shot_state = {
                'shot_number': index + 1,
                'accuracy_penalty': jam_effects['accuracy_penalty'],
                'shooting_disadvantage': jam_effects['shooting_disadvantage'],
                'jams_before_shot': deepcopy(jam_effects['jams']),
                'jam_after_shot': None,
            }
            if isinstance(weapon, dict):
                shot_details['weapon_jam_accuracy_penalty'] = max(
                    0, CombatService._coerce_int(jam_effects['accuracy_penalty'], 0),
                )
                shot_details['hit_difficulty'] = max(
                    1,
                    CombatService._coerce_float(
                        attack_details.get('hit_difficulty_without_weapon_jam'),
                        attack_details.get('hit_difficulty', 1),
                    ) + shot_details['weapon_jam_accuracy_penalty'],
                )
                shot_details['shooting_disadvantage'] = bool(
                    attack_details.get('base_shooting_disadvantage')
                    or jam_effects['shooting_disadvantage']
                )
                shot_details['weapon_jams_before_shot'] = deepcopy(jam_effects['jams'])
            machine_gun_penalty = (
                math.floor(index * 0.5)
                if attack_details.get('machine_gun_burst')
                else 0
            )
            if machine_gun_penalty:
                from app.services.equipment_modifications import worn_rule
                machine_gun_penalty=max(0,machine_gun_penalty
                    -worn_rule(attacker_data,'worn_burst_accuracy_reduction'))
            burst_followup_penalty = (
                CombatService._burst_followup_accuracy_penalty(weapon, index, attacker_data)
                if attack_details.get('fire_mode') == 'burst'
                and not attack_details.get('machine_gun_burst')
                and isinstance(weapon, dict)
                else 0
            )
            shot_details['machine_gun_burst_penalty'] = machine_gun_penalty
            shot_details['burst_followup_accuracy_penalty'] = burst_followup_penalty
            shot_details['hit_difficulty'] = max(
                1,
                CombatService._coerce_float(shot_details.get('hit_difficulty'), 1)
                + machine_gun_penalty
                + burst_followup_penalty,
            )
            result = CombatService._resolve_attack(
                target,
                attacker,
                shot_details,
                aimed_zone=aimed_zone,
                forced_roll=shared_roll if share_hit_roll else None,
            )
            result['shot_number'] = index + 1
            if result.get('post_bonus_hit'):post_used.add(index//volley_size)
            if index == 0 and share_hit_roll:
                shared_roll = result.get('roll')
            if share_hit_roll:
                result['shared_hit_roll'] = True
            results.append(result)
            shot_states.append(shot_state)
            if isinstance(weapon, dict) and not share_hit_roll:
                jam = CombatService._roll_character_weapon_jam(attacker_data,weapon,result.get('roll'))
                shot_state['jam_after_shot'] = deepcopy(jam)
                result['weapon_jam_after_shot'] = deepcopy(jam)
                if isinstance(jam, dict) and jam.get('triggered'):
                    triggered = {'shot_number': index + 1, **deepcopy(jam)}
                    triggered_jams.append(triggered)
                    if jam.get('blocks_fire'):
                        break
        if isinstance(weapon, dict) and share_hit_roll and results:
            jam = CombatService._roll_character_weapon_jam(attacker_data,weapon,shared_roll)
            shot_states[-1]['jam_after_shot'] = deepcopy(jam)
            results[-1]['weapon_jam_after_shot'] = deepcopy(jam)
            if isinstance(jam, dict) and jam.get('triggered'):
                triggered_jams.append({
                    'shot_number': len(results),
                    **deepcopy(jam),
                })
        attack_details['requested_shot_count'] = requested_shots
        attack_details['shot_count'] = len(results)
        attack_details['shot_jam_states'] = shot_states
        attack_details['weapon_jams'] = triggered_jams
        attack_details['weapon_jam'] = triggered_jams[-1] if triggered_jams else None
        attack_details['stopped_by_jam'] = len(results) < requested_shots
        attack_details['post_bonus_hits']=len(post_used)
        return results

    @staticmethod
    def format_attack_summary(result):
        attack = result.get('attack') if isinstance(result, dict) else None
        if not isinstance(attack, dict):
            return None
        results = attack.get('results')
        if not isinstance(results, list) or not results:
            return None

        zone_labels = {
            'head': 'голова',
            'chest': 'грудь',
            'abdomen': 'живот',
            'left_arm': 'левая рука',
            'right_arm': 'правая рука',
            'left_leg': 'левая нога',
            'right_leg': 'правая нога',
        }
        bleeding_kind_labels = {
            'external': 'внешнее',
            'internal': 'внутреннее',
        }
        bleeding_stage_labels = {
            'light': 'лёгкое',
            'medium': 'среднее',
            'severe': 'сильное',
            'extreme': 'экстремальное',
        }
        organ_labels = {
            'heart': '\u0441\u0435\u0440\u0434\u0446\u0435',
            'rightLung': '\u043f\u0440\u0430\u0432\u043e\u0435 \u043b\u0451\u0433\u043a\u043e\u0435',
            'leftLung': '\u043b\u0435\u0432\u043e\u0435 \u043b\u0451\u0433\u043a\u043e\u0435',
            'rightKidney': '\u043f\u0440\u0430\u0432\u0430\u044f \u043f\u043e\u0447\u043a\u0430',
            'leftKidney': '\u043b\u0435\u0432\u0430\u044f \u043f\u043e\u0447\u043a\u0430',
            'stomach': '\u0436\u0435\u043b\u0443\u0434\u043e\u043a',
            'liver': '\u043f\u0435\u0447\u0435\u043d\u044c',
            'rightEye': '\u043f\u0440\u0430\u0432\u044b\u0439 \u0433\u043b\u0430\u0437',
            'leftEye': '\u043b\u0435\u0432\u044b\u0439 \u0433\u043b\u0430\u0437',
            'rightEar': '\u043f\u0440\u0430\u0432\u043e\u0435 \u0443\u0445\u043e',
            'leftEar': '\u043b\u0435\u0432\u043e\u0435 \u0443\u0445\u043e',
            'nose': '\u043d\u043e\u0441',
            'jaw': '\u0447\u0435\u043b\u044e\u0441\u0442\u044c',
            'skull': '\u0447\u0435\u0440\u0435\u043f',
            'spine': '\u043f\u043e\u0437\u0432\u043e\u043d\u043e\u0447\u043d\u0438\u043a',
            'brain': '\u043c\u043e\u0437\u0433',
        }
        mode_labels = {
            'gunpoint': 'выстрел в упор',
            'melee': 'атака ближнего боя',
            'unaimed': 'неприцельный выстрел',
            'rapid': 'беглый выстрел',
            'aimed': 'прицельный выстрел',
            'burst': 'очередь',
            'area': 'огонь по области',
        }

        actor = (
            (result.get('character') or {}).get('name')
            or 'Персонаж'
        )
        mode = mode_labels.get(
            results[0].get('mode'),
            attack.get('attack_type') or attack.get('fire_mode') or 'атака',
        )
        lines = [f"{actor}: {mode}{' из двух оружий одновременно' if attack.get('paired_fire') else ''}."]
        for frightened in attack.get('frightened_mutants') or []:
            check = frightened.get('check') or {}
            rolls = '/'.join(str(value) for value in check.get('rolls') or [check.get('roll')])
            outcome = (
                f"провал, убегает на {frightened.get('distance', 0)} м"
                if frightened.get('fled')
                else 'успех'
            )
            lines.append(
                f"Пугливость: {frightened.get('name')}: d20 {rolls} "
                f"{CombatService._coerce_int(check.get('bonus'), 0):+d} = "
                f"{check.get('total', '—')}, СЛ {check.get('difficulty', '—')} — {outcome}."
            )
        if attack.get('fire_mode') == 'area':
            first = results[0]
            lines.append(
                f"Один бросок: d20 {first.get('roll', '—')}, "
                f"СЛ {first.get('difficulty', '—')}; "
                f"попаданий {attack.get('area_hit_count', 0)}."
            )
        for index, hit_result in enumerate(results, start=1):
            target_name = hit_result.get('target_name') or 'цель'
            if hit_result.get('prosthesis_damage'):
                hit=hit_result['prosthesis_damage']
                description=(f"{hit.get('name')}: урон {hit.get('damage',0):g}, прочность {hit.get('durability',0):g}"
                    +(' — сломан' if hit.get('broken') else '')) if not hit.get('missing') else 'утраченная часть отсутствует'
                lines.append(f"{index}. {target_name}: d6 {hit.get('roll')} — {hit.get('part')}; {description}.")
                continue
            if hit_result.get('flash_round'):
                lines.append(f"{index}. {target_name}: светошумовой выстрел без проверки попадания; "
                    f"ослепление {hit_result.get('blindness',0):g}."
                    +(' Укрытие блокирует вспышку.' if hit_result.get('flash_blocked') else ''))
                continue
            rolls = hit_result.get('rolls') or [hit_result.get('roll')]
            rolls = [roll for roll in rolls if roll is not None]
            roll_text = '/'.join(str(roll) for roll in rolls) or '—'
            difficulty = hit_result.get('difficulty', '—')
            prefix = (
                f"{index}. {target_name}: автоматическое попадание"
                if hit_result.get('automatic_hit')
                else f"{index}. {target_name}: d20 {roll_text}, СЛ {difficulty}"
            )
            if not hit_result.get('hit'):
                lines.append(f"{prefix} — промах.")
                counterattack = hit_result.get('counterattack_result')
                if isinstance(counterattack, dict):
                    counter_rolls = counterattack.get('rolls') or [counterattack.get('roll')]
                    counter_roll_text = '/'.join(
                        str(value) for value in counter_rolls if value is not None
                    ) or '\u2014'
                    counter_result = (
                        f"\u043f\u043e\u043f\u0430\u0434\u0430\u043d\u0438\u0435, \u0443\u0440\u043e\u043d {round(CombatService._coerce_float(counterattack.get('damage'), 0))}"
                        if counterattack.get('hit')
                        else '\u043f\u0440\u043e\u043c\u0430\u0445'
                    )
                    lines.append(
                        f"   \u041e\u0442\u0432\u0435\u0442\u043d\u0430\u044f \u0430\u0442\u0430\u043a\u0430 \u0431\u043b\u043e\u043a\u0430: d20 {counter_roll_text}, "
                        f"\u0421\u041b {counterattack.get('difficulty', '\u2014')} \u2014 {counter_result}."
                    )
                cover_impact = hit_result.get('cover_hit')
                if isinstance(cover_impact, dict):
                    cover_state = cover_impact.get('damage') or {}
                    protection_before = round(CombatService._coerce_float(
                        cover_impact.get('protection'), 0
                    ))
                    protection_after = round(CombatService._coerce_float(
                        cover_state.get('physical_protection'), protection_before
                    ))
                    lines.append(
                        f"   Пуля попала в {cover_impact.get('object_name') or 'укрытие'}: "
                        f"без проверки; "
                        f"защита {protection_before}% → {protection_after}%, ОЗ "
                        f"{cover_state.get('hp', '—')}/{cover_state.get('max_hp', '—')}."
                    )
                continue

            if hit_result.get('partial_block'):
                arm_label = {
                    'left_arm': 'левую руку',
                    'right_arm': 'правую руку',
                }.get(hit_result.get('block_arm'), 'руку')
                lines.append(
                    f"{prefix} — частичный блок: половина урона приходится в {arm_label}."
                )

            if hit_result.get('cover_hit') is True:
                cover_state = hit_result.get('cover_damage') or {}
                protection_before = round(CombatService._coerce_float(
                    hit_result.get('cover_protection'), 0
                ))
                protection_after = round(CombatService._coerce_float(
                    cover_state.get('physical_protection'), protection_before
                ))
                cover_prefix = (
                    f"{index}. {target_name}: без проверки"
                    if hit_result.get('automatic_cover_hit')
                    else prefix
                )
                lines.append(
                    f"{cover_prefix} — попадание по укрытию; защита "
                    f"{protection_before}% → {protection_after}%, ОЗ "
                    f"{cover_state.get('hp', '—')}/{cover_state.get('max_hp', '—')}; "
                    f"пробитие: {'да' if hit_result.get('cover_penetrated') else 'нет'}."
                )
                behind_result = hit_result.get('target_behind_cover_result')
                if isinstance(behind_result, dict):
                    if not behind_result.get('hit'):
                        behind_rolls = behind_result.get('rolls') or [behind_result.get('roll')]
                        behind_rolls = [roll for roll in behind_rolls if roll is not None]
                        behind_roll_text = '/'.join(str(roll) for roll in behind_rolls) or '—'
                        lines.append(
                            f"   За укрытием: {hit_result.get('target_behind_cover_name') or 'цель'}, "
                            f"d20 {behind_roll_text}, СЛ {behind_result.get('difficulty', '—')} — промах."
                        )
                        continue
                    behind_zone = zone_labels.get(
                        behind_result.get('zone'),
                        behind_result.get('zone') or 'неизвестная зона',
                    )
                    lines.append(
                        f"   За укрытием: {hit_result.get('target_behind_cover_name') or 'цель'}, "
                        f"{behind_zone}, урон {round(CombatService._coerce_float(behind_result.get('damage'), 0))}."
                    )
                continue

            zone = zone_labels.get(
                hit_result.get('zone'),
                hit_result.get('zone') or 'неизвестная зона',
            )
            damage = round(CombatService._coerce_float(
                hit_result.get('damage'),
                0,
            ))
            armor = round(CombatService._coerce_float(
                hit_result.get('armor'),
                0,
            ))
            penetration = round(CombatService._coerce_float(
                hit_result.get('armor_piercing'),
                0,
            ))
            lines.append(
                f"{prefix} — попадание: {zone}, урон {damage}; "
                f"защита {armor}%, пробитие {penetration}%."
            )
            pepper = hit_result.get('pepper_head_hit')
            if isinstance(pepper, dict):
                lines.append(
                    f"   Перцовка: попадание в голову {pepper['hits']}/3, "
                    "следующий ход пропущен, −5 ко всем броскам до конца боя, "
                    "+1 уровень боли каждый ход."
                )
                if pepper['hits'] >= 3:
                    lines.append("   Болевой шок на 6 часов; −5 к атаке и внимательности на сутки.")
                else:
                    lines.append(
                        f"   Болевой шок: d100 {pepper['shock_roll']} — "
                        f"{'да' if pepper['shock'] else 'нет'} (шанс 50%)."
                    )
            cover_impact = hit_result.get('cover_hit')
            if isinstance(cover_impact, dict):
                cover_state = cover_impact.get('damage') or {}
                protection_before = round(CombatService._coerce_float(
                    cover_impact.get('protection'), 0
                ))
                protection_after = round(CombatService._coerce_float(
                    cover_state.get('physical_protection'), protection_before
                ))
                lines.append(
                    f"   Укрытие {cover_impact.get('object_name') or ''}: защита "
                    f"{protection_before}% → {protection_after}%, ОЗ "
                    f"{cover_state.get('hp', '—')}/{cover_state.get('max_hp', '—')}; "
                    f"пробитие: {'да' if cover_impact.get('penetrated') else 'нет'}."
                )

            bleedings = hit_result.get('bleedings') or []
            if bleedings:
                bleeding_labels = []
                for bleeding in bleedings:
                    kind = bleeding_kind_labels.get(
                        bleeding.get('kind'),
                        bleeding.get('kind') or '',
                    )
                    stage = bleeding_stage_labels.get(
                        bleeding.get('stage'),
                        bleeding.get('stage') or '',
                    )
                    bleeding_labels.append(f"{stage} {kind}".strip())
                lines.append(
                    f"   Кровотечение: {', '.join(bleeding_labels)}."
                )
            else:
                lines.append("   Кровотечение: нет.")

            traumas = hit_result.get('additional_traumas') or []
            if traumas:
                trauma_labels = []
                for trauma in traumas:
                    if trauma.get('pending'):
                        trauma_labels.append(f"d20 {trauma.get('roll', '—')} — ожидает выбора Микроскопической точности")
                        continue
                    consequences = []
                    head_impact = trauma.get('head_impact')
                    if isinstance(head_impact, dict):
                        part_labels = {
                            'head': 'голова',
                            'crown': 'теменная часть',
                            'rightCheek': 'правая щека',
                            'leftCheek': 'левая щека',
                            'rightEye': 'правый глаз',
                            'leftEye': 'левый глаз',
                            'rightEar': 'правое ухо',
                            'leftEar': 'левое ухо',
                            'nose': 'нос',
                            'jaw': 'челюсть',
                        }
                        direction_labels = {
                            'front': 'спереди',
                            'side': 'сбоку',
                            'back': 'сзади',
                        }
                        consequences.append(
                            f"{part_labels.get(head_impact.get('part'), head_impact.get('part') or 'голова')} "
                            f"{direction_labels.get(head_impact.get('direction'), '')}".strip()
                        )
                    if trauma.get('fracture'):
                        consequences.append('перелом')
                    trauma_bleeding = trauma.get('bleeding')
                    if isinstance(trauma_bleeding, dict):
                        kind = bleeding_kind_labels.get(
                            trauma_bleeding.get('kind'),
                            trauma_bleeding.get('kind') or '',
                        )
                        stage = bleeding_stage_labels.get(
                            trauma_bleeding.get('stage'),
                            trauma_bleeding.get('stage') or '',
                        )
                        consequences.append(
                            f"{stage} {kind} кровотечение".strip()
                        )
                    if trauma.get('pain'):
                        consequences.append(f"боль +{trauma['pain']}")
                    organ_damages = trauma.get('organ_damages')
                    if not isinstance(organ_damages, list):
                        organ_damage = trauma.get('organ_damage')
                        organ_damages = [organ_damage] if isinstance(organ_damage, dict) else []
                    if not organ_damages and trauma.get('organ'):
                        organ_damages = [{'organ': trauma.get('organ')}]
                    for organ_damage in organ_damages:
                        organ_key = organ_damage.get('organ')
                        if not organ_key:
                            continue
                        organ_label = organ_labels.get(organ_key, organ_key)
                        organ_details = f"\u043e\u0440\u0433\u0430\u043d: {organ_label}"
                        if organ_damage.get('current_before') is not None:
                            before = round(CombatService._coerce_float(
                                organ_damage.get('current_before'), 0,
                            ))
                            current = round(CombatService._coerce_float(
                                organ_damage.get('current'), 0,
                            ))
                            maximum = round(CombatService._coerce_float(
                                organ_damage.get('max'), 0,
                            ))
                            organ_details += f" \u041e\u0417 {before} -> {current}/{maximum}"
                            if organ_damage.get('disabled'):
                                organ_details += " (\u0432\u044b\u0432\u0435\u0434\u0435\u043d \u0438\u0437 \u0441\u0442\u0440\u043e\u044f)"
                        consequences.append(organ_details)
                    if trauma.get('shock'):
                        consequences.append('шок')
                    fall_or_drop = trauma.get('fall_or_drop')
                    if isinstance(fall_or_drop, dict):
                        if fall_or_drop.get('kind') == 'fall':
                            consequences.append('падение персонажа')
                        elif fall_or_drop.get('dropped'):
                            consequences.append(
                                f"выпало оружие: {fall_or_drop.get('weapon_name') or 'оружие'}"
                            )
                        else:
                            consequences.append('падение оружия: в руках ничего нет')
                    details = ', '.join(consequences) or 'без доп. эффекта'
                    trauma_labels.append(
                        f"d20 {trauma.get('roll', '—')} ({details})"
                    )
                lines.append(
                    f"   Доп. травма: {'; '.join(trauma_labels)}."
                )
            else:
                lines.append("   Доп. травма: нет.")

            for mutant_effect in hit_result.get('mutant_effects') or []:
                kind = mutant_effect.get('kind')
                save = mutant_effect.get('save') or {}
                if kind == 'poison_bite':
                    outcome = 'успех' if save.get('success') else 'провал, яд на 3 раунда'
                    lines.append(
                        f"   Ядовитый укус: d20 {save.get('roll', '—')} "
                        f"{CombatService._coerce_int(save.get('bonus'), 0):+d} = "
                        f"{save.get('total', '—')}, СЛ 12 — {outcome}."
                    )
                elif kind == 'knockdown':
                    lines.append("   Цель сбита с ног.")
                elif kind == 'vampirism':
                    lines.append(
                        "   Вампиризм: кровопотеря цели ухудшена, кровопотеря "
                        "атакующего улучшена, тяжесть его кровотечений -5."
                    )
                elif kind == 'knockout_attack':
                    outcome = (
                        'успех'
                        if save.get('success')
                        else f"провал, отброшен на {mutant_effect.get('distance', 0)} м и контужен"
                    )
                    lines.append(
                        f"   Атака наповал: d20 {save.get('roll', '—')} "
                        f"{CombatService._coerce_int(save.get('bonus'), 0):+d} = "
                        f"{save.get('total', '—')}, СЛ 15 — {outcome}."
                    )
                elif kind == 'rage_accuracy':
                    lines.append(
                        f"   Яростные атаки: накопленный бонус точности "
                        f"+{mutant_effect.get('value', 0)}."
                    )

            catastrophic = hit_result.get('catastrophic_limb_injury')
            if isinstance(catastrophic, dict):
                if catastrophic.get('type') == 'amputation':
                    extent_labels = {
                        'entire_limb': 'целиком',
                        'elbow_or_knee': 'по локоть/колено',
                        'hand_or_foot': 'по кисть/ступню',
                    }
                    extent = extent_labels.get(
                        catastrophic.get('loss_extent'),
                        catastrophic.get('loss_extent') or 'не указано',
                    )
                    lines.append(
                        f"   Конечность утрачена: {extent}, "
                        f"1к6 = {catastrophic.get('loss_roll', '—')}."
                    )
                else:
                    lines.append("   Конечность искорежена.")
            if hit_result.get('death'):
                lines.append("   Смертельное попадание в выбитую жизненно важную зону.")

        if len(results) > 1:
            lines.append(
                f"Итого: попаданий {attack.get('hits', 0)}/{len(results)}, "
                f"урон {round(CombatService._coerce_float(attack.get('damage_total'), 0))}."
            )
        area_stress = attack.get('area_stressed_characters') or []
        if area_stress:
            lines.append(
                "Стресс от огня по области: "
                + ", ".join(str(item.get('name') or 'цель') for item in area_stress)
                + "."
            )
        wear = attack.get('weapon_wear')
        if isinstance(wear, dict) and wear.get('loss'):
            lines.append(
                f"Прочность оружия: {wear.get('before')} -> {wear.get('after')} "
                f"(-{wear.get('loss')})."
            )
        weapon_jams = attack.get('weapon_jams')
        if not isinstance(weapon_jams, list):
            jam = attack.get('weapon_jam')
            weapon_jams = [jam] if isinstance(jam, dict) and jam.get('triggered') else []
        for jam in weapon_jams:
            lines.append(
                f"Клин после выстрела {jam.get('shot_number', '?')}: "
                f"бросок атаки {jam.get('attack_roll')} (порог {jam.get('chance')}), "
                f"результат {jam.get('strength_roll')} - {jam.get('label')}."
            )
        if attack.get('stopped_by_jam'):
            lines.append(
                f"Очередь остановлена: произведено {attack.get('shot_count', 0)} "
                f"из {attack.get('requested_shot_count', 0)} выстрелов."
            )
        return '\n'.join(lines)

    @staticmethod
    def format_explosion_summary(result):
        details = result.get('explosive') if isinstance(result, dict) else None
        if not isinstance(details, dict):
            return None
        def number(value):
            return f'{round(float(value),1):g}' if isinstance(value,(int,float)) else str(value)
        explosion = details.get('explosion') or {}
        roll_text = ''
        if details.get('roll') is not None:
            roll_text = (
                f"d20 {details.get('roll')} \u043f\u0440\u043e\u0442\u0438\u0432 \u0421\u041b {number(details.get('difficulty'))}"
                f" - {'\u0443\u0441\u043f\u0435\u0445' if details.get('success') else '\u043f\u0440\u043e\u043c\u0430\u0445'}"
            )
        if details.get('disadvantage'):
            roll_text = f"d20 {details.get('rolls')} -> {details.get('roll')} \u0441 \u043f\u043e\u043c\u0435\u0445\u043e\u0439, \u0421\u041b {details.get('difficulty')}"
        impact = details.get('impact') or {}
        header = f"{details.get('item_name')}: "
        if roll_text:
            header += f"{roll_text}. "
        if details.get('delay_check'):
            check=details['delay_check']
            header+=f"Задержка: Тактика {check['total']} против СЛ13 — {'успех' if check['success'] else 'провал'}, +2 ОД. "
        header += f"\u0422\u043e\u0447\u043a\u0430 \u0432\u0437\u0440\u044b\u0432\u0430 {number(impact.get('x'))}, {number(impact.get('y'))}"
        if details.get('airburst'):
            header += " (\u0432\u043e\u0437\u0434\u0443\u0448\u043d\u044b\u0439 \u043f\u043e\u0434\u0440\u044b\u0432 \u043d\u0430 \u043f\u0440\u0435\u0434\u0435\u043b\u044c\u043d\u043e\u0439 \u0434\u0430\u043b\u044c\u043d\u043e\u0441\u0442\u0438)"
        target_lines = []
        for target in explosion.get('targets') or []:
            if 'blindness' in target:
                target_lines.append(
                    f"{target.get('name')}: \u0441\u043b\u0435\u043f\u043e\u0442\u0430 {target.get('blindness')}, "
                    f"\u0433\u043b\u0443\u0445\u043e\u0442\u0430 {target.get('deafness')}"
                )
                continue
            traumas=target.get('blast_traumas') or [target.get('blast_trauma')]
            trauma_names=[t['type'] for t in traumas if t and t.get('type') not in {None,'none'}]
            trauma_text=', травмы: '+', '.join(trauma_names) if trauma_names else ''
            target_lines.append(
                f"{target.get('name')}: \u0432\u043e\u043b\u043d\u0430 {target.get('blast_damage')}, "
                f"\u043e\u0441\u043a\u043e\u043b\u043a\u0438 {target.get('fragment_damage')} \u0432 {target.get('fragment_zone')}, "
                f"\u0443\u043a\u0440\u044b\u0442\u0438\u0435 {target.get('cover_protection')}%{trauma_text}"
            )
        if not target_lines:
            area = explosion.get('area')
            if isinstance(area, dict):
                target_lines.append(
                    f"\u0421\u043e\u0437\u0434\u0430\u043d\u0430 \u043e\u0431\u043b\u0430\u0441\u0442\u044c {area.get('type')} "
                    f"\u0440\u0430\u0434\u0438\u0443\u0441\u043e\u043c {area.get('radius')}"
                )
            elif explosion.get('awaiting_gm'):
                target_lines.append('Ожидается решение ГМа об участниках взрыва.')
            elif details.get('detonated') is False:
                target_lines.append('3 секунды: реакция в ход каждого затронутого персонажа.' if (details.get('pending') or {}).get('trigger')=='short_fuse'
                    else 'Взрыв в начале следующего хода метающего.')
            else:
                target_lines.append("\u041f\u0435\u0440\u0441\u043e\u043d\u0430\u0436\u0438 \u043d\u0435 \u0437\u0430\u0434\u0435\u0442\u044b")
        return header + ". " + "; ".join(target_lines) + "."

    @staticmethod
    def format_narrative_action_summary(result):
        details = result.get('narrative_action') if isinstance(result, dict) else None
        if not isinstance(details, dict):
            return None
        actor = (result.get('character') or {}).get('name') or 'Персонаж'
        lines = [
            f"{actor}: {details.get('name')}. Затрачено ОД: {details.get('action_points', 0)}."
        ]
        check = details.get('check')
        if isinstance(check, dict):
            rolls = check.get('rolls') or [check.get('roll')]
            roll_text = '/'.join(str(value) for value in rolls if value is not None) or '—'
            disadvantage = ' с Помехой' if check.get('disadvantage') else ''
            modifier = CombatService._coerce_int(check.get('modifier'), 0)
            lines.append(
                f"Проверка «{check.get('skill_label')}»{disadvantage}: "
                f"d20 {roll_text} -> {check.get('roll')}, "
                f"модификатор {modifier:+d}, итог {check.get('total')}."
            )
        else:
            lines.append("Проверка не требуется.")
        if details.get('chosen_description'):
            if details.get('tough_nut'):lines.append('Дополнительная травма отменена Крепким орешком.')
            elif details.get('trauma'):lines.append(f"Выбранная травма: d20 {details['trauma']['roll']}, {details['chosen_description']}.")
            else:lines.append(details['chosen_description'])
        return '\n'.join(lines)

    @staticmethod
    def _coerce_float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _skill_modifier(character_data, skill_path, include_pain=True):
        return (
            CombatService._base_skill_modifier(character_data, skill_path)
            + CombatService._health_roll_modifier(
                character_data,
                skill_path,
                include_pain,
            )
        )

    @staticmethod
    def _base_skill_modifier(character_data, skill_path):
        current = character_data if isinstance(character_data, dict) else {}
        for part in skill_path.split('.'):
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(part)
        if not isinstance(current, dict):
            return 0
        skill_value = CombatService._skill_value(character_data, skill_path)
        return math.floor((skill_value - 10) / 2)+(facial_profile(character_data)['charisma_bonus'] if skill_path=='skills.social.charisma' else 0)

    @staticmethod
    def _must_do_usage_profile(character_data, combat_state, initialize=True):
        health = character_data.setdefault('health', {})
        combat_meta = health.setdefault('combatMeta', {})
        started_at = getattr(combat_state, 'started_at', None)
        window = started_at.isoformat() if started_at else 'combat'
        usage = combat_meta.get('mustDoUsage')
        if not isinstance(usage, dict) or usage.get('window') != window:
            usage = {'window': window, 'used': 0}
            if initialize:
                combat_meta['mustDoUsage'] = usage
        will_bonus = CombatService._base_skill_modifier(
            character_data, 'skills.physical.will',
        )
        limit = max(1, will_bonus)
        used = max(0, CombatService._coerce_int(usage.get('used'), 0))
        return {
            'usage': usage,
            'will_bonus': will_bonus,
            'limit': limit,
            'used': used,
            'remaining': max(0, limit - used),
        }

    @staticmethod
    def _pain_shock_recovery_difficulty(character_data, medicine_bonus=0):
        will_bonus = CombatService._base_skill_modifier(
            character_data, 'skills.physical.will'
        )
        difficulty = max(
            1,
            12 - will_bonus - CombatService._coerce_int(medicine_bonus, 0),
        )
        return difficulty, will_bonus

    @staticmethod
    def _consumable_stat_value_bonus(character_data, stat_name):
        if not isinstance(character_data, dict):
            return 0
        if stat_name.startswith('darkness_'):
            from app.services.consumable_effects import normalize_owl_modifiers
            normalize_owl_modifiers(character_data)
        if stat_name=='movement_points':
            from app.services.consumable_effects import remove_stale_movement_modifiers
            remove_stale_movement_modifiers(character_data)
        health = character_data.get('health') if isinstance(character_data.get('health'), dict) else {}
        combat_meta = health.get('combatMeta') if isinstance(health.get('combatMeta'), dict) else {}
        modifiers = combat_meta.get('consumableModifiers')
        if not isinstance(modifiers, list):
            return 0
        total = 0
        for item in modifiers:
            if not isinstance(item, dict):
                continue
            remaining = item.get('remaining')
            if remaining is not None and CombatService._coerce_int(remaining, 0) <= 0:
                continue
            if str(item.get('stat') or '').strip() in {stat_name, f'{stat_name}_delta'}:
                total += CombatService._coerce_int(item.get('value', 0), 0)
        return total

    @staticmethod
    def _consumable_stat_bonus(character_data, stat_name):
        if not isinstance(character_data, dict):
            return 0
        health = character_data.get('health') if isinstance(character_data.get('health'), dict) else {}
        combat_meta = health.get('combatMeta') if isinstance(health, dict) and isinstance(health.get('combatMeta'), dict) else {}
        modifiers = combat_meta.get('consumableModifiers')
        if not isinstance(modifiers, list):
            return 0
        total = 0
        for item in modifiers:
            if not isinstance(item, dict):
                continue
            remaining = item.get('remaining')
            if remaining is not None and CombatService._coerce_int(remaining, 0) <= 0:
                continue
            item_stat = str(item.get('stat') or '').strip()
            if item_stat in {stat_name, f'{stat_name}_delta', 'generic', 'generic_multiplier'}:
                total += CombatService._coerce_int(item.get('value', 0), 0)
        return total

    @staticmethod
    def _bleeding_modifier_total(health):
        if not isinstance(health, dict):
            return 0
        combat_meta = health.get('combatMeta') if isinstance(health.get('combatMeta'), dict) else {}
        modifiers = combat_meta.get('bleedingModifiers')
        if isinstance(modifiers, list):
            total = 0
            for item in modifiers:
                if isinstance(item, dict):
                    if item.get('affects_severity') or item.get('note')=='hematogen':continue
                    remaining = item.get('remaining')
                    if remaining is not None and CombatService._coerce_int(remaining, 0) <= 0:
                        continue
                    total += CombatService._coerce_int(item.get('value', 0), 0)
                else:
                    total += CombatService._coerce_int(item, 0)
            return total
        return CombatService._coerce_int(combat_meta.get('bleedingModifierTotal', health.get('bleedingModifierTotal', 0)), 0)

    @staticmethod
    def _advance_blood_stage(stage):
        order = ['normal', 'light', 'medium', 'severe', 'critical', 'fatal']
        current = str(stage or 'normal').lower()
        if current not in order:
            current = 'normal'
        index = order.index(current)
        next_index = min(len(order) - 1, index + 1)
        return order[next_index]

    @staticmethod
    def _bleeding_check_profile(character_data,target=None):
        health = (
            character_data.get('health')
            if isinstance(character_data, dict)
            else {}
        )
        if not isinstance(health, dict):
            health = {}
        bleeding = (
            health.get('bleeding')
            if isinstance(health.get('bleeding'), dict)
            else {}
        )
        severity = CombatService._coerce_int(
            bleeding.get('totalSeverity', health.get('bleedingSeverity', 0)),
            0,
        )
        severity = max(
            0,
            severity*(.75 if CombatService._combat_background_active(target,'Стойкий') else 1)
            + artifact_passive_profile(character_data)['bleeding_severity']-character_passives(character_data)['bleeding_reduction'],
        )
        stage_penalty = CombatService._coerce_int(
            bleeding.get(
                'stagePenalty',
                health.get('bleedingStagePenalty', 0),
            ),
            0,
        )
        modifier_total = CombatService._bleeding_modifier_total(health)
        will_bonus = CombatService._base_skill_modifier(
            character_data,
            'skills.physical.will',
        )
        state_modifier = CombatService._health_roll_modifier(
            character_data,
            'skills.physical.will',
            include_pain=False,
            include_blood=False,
            include_psy=False,
        )
        return {
            'severity': severity,
            'stagePenalty': stage_penalty,
            'modifierTotal': modifier_total,
            'willBonus': will_bonus,
            'stateModifier': state_modifier,
            'difficulty': max(
                0,
                5
                + severity
                - stage_penalty
                + modifier_total
                - will_bonus
                - state_modifier,
            ),
        }

    @staticmethod
    def _resolve_bleeding_check(loc_char):
        if not loc_char or not getattr(loc_char, 'character', None):
            return None
        character = loc_char.character
        character_data = character.data if isinstance(character.data, dict) else {}
        health = character_data.get('health')
        if not isinstance(health, dict):
            return None

        active_effects = normalize_effect_list(health.get('effects') or [])
        if any(effect.get('type') == 'blood_loss_freeze' and effect.get('active', True) for effect in active_effects):
            return None

        sync_health_derived_statuses(health)
        bleeding = health.get('bleeding') if isinstance(health.get('bleeding'), dict) else {}
        profile = CombatService._bleeding_check_profile(character_data,loc_char) if background_is(character_data,'Стойкий') else CombatService._bleeding_check_profile(character_data)
        severity = profile['severity']
        if severity <= 0:
            return None

        stage = str(health.get('blood') or health.get('bloodStage') or 'normal').lower()
        stage_penalty = profile['stagePenalty']
        modifier_total = profile['modifierTotal']
        will_bonus = profile['willBonus']
        state_modifier = profile['stateModifier']
        roll = random.randint(1, 20)
        # Проверка кровопотери не является обычной проверкой Воли и не получает
        # Помеху от пси-состояния.
        disadvantage = False
        from app.services.brigadier_order import boost_roll
        _rolls,roll,order_bonus=boost_roll(character_data,[roll])
        total = roll+order_bonus
        difficulty = profile['difficulty']
        success = total >= difficulty

        meta = health.setdefault('combatMeta', {})
        meta['bleedingCheck'] = {
            'roll': roll,
            'bonus': will_bonus,
            'total': total,
            'difficulty': difficulty,
            'severity': severity,
            'stagePenalty': stage_penalty,
            'modifierTotal': modifier_total,
            'stateModifier': state_modifier,
            'disadvantage': disadvantage,
            'success': success,
        }

        if not success:
            health['blood'] = CombatService._advance_blood_stage(stage)
            if health['blood'] == 'fatal':
                apply_effect_to_health(health, {
                    'type': 'death',
                    'source': 'blood_loss',
                    'tick': 'manual',
                })
                loc_char.posture = 'prone'
                loc_char.cover_object_id = None
                loc_char.weapon_braced = False
                loc_char.braced_weapon_index = None
        health['bloodStage'] = str(health.get('blood') or stage or 'normal').lower()
        meta['bleedingCheck']['bloodStage'] = health['bloodStage']
        meta['bleedingCheck']['death'] = health['bloodStage'] == 'fatal'
        sync_health_derived_statuses(health)
        character_data['health'] = health
        character.data = character_data
        flag_modified(character, 'data')
        if stage!='fatal' and health['bloodStage']=='fatal':CombatService._notify_observed_death(loc_char)
        return meta['bleedingCheck']

    @staticmethod
    def _tick_character_effects(loc_char, phase='turn_end'):
        if not loc_char:
            return loc_char
        effects = loc_char.effects if isinstance(loc_char.effects, list) else []
        loc_char.effects = tick_effects(effects, phase=phase)
        return loc_char

    @staticmethod
    def _apply_periodic_health_effects(loc_char, phase='turn_end'):
        if not loc_char or not getattr(loc_char, 'character', None):
            return loc_char
        character_data = loc_char.character.data if isinstance(loc_char.character.data, dict) else {}
        health = character_data.get('health')
        if not isinstance(health, dict):
            return loc_char
        from app.services.effects import identify_effects
        health['effects'] = identify_effects(health.get('effects') or [])
        active_effects = normalize_effect_list(health['effects'])
        modifiers = health.get('combatMeta', {}).get('consumableModifiers')
        if isinstance(modifiers, list):
            active_modifiers = []
            for modifier in modifiers:
                if (not isinstance(modifier, dict) or modifier.get('remaining') is None
                        or (modifier.get('tick') or 'turn_end') != phase):
                    active_modifiers.append(modifier)
                    continue
                remaining = max(0, CombatService._coerce_int(modifier['remaining'], 0) - 1)
                if remaining:
                    active_modifiers.append({**modifier,'remaining':remaining,
                        'remaining_seconds':remaining * (600 if phase == 'movement_end' else 6)})
            health['combatMeta']['consumableModifiers'] = active_modifiers
        if phase=='movement_end':
            from app.services.burning import extinguish_in_water
            extinguish_in_water(loc_char)
            active_effects=normalize_effect_list(health.get('effects') or [])
        if phase=='round_end':
            from app.services.burning import tick
            state=LocationCombatState.query.filter_by(location_id=loc_char.location_id).first()
            tick(loc_char,state.round_number if state else 0)
            character_data=loc_char.character.data;health=character_data['health']
            active_effects=normalize_effect_list(health.get('effects') or [])
        for effect in active_effects:
            effect_type = str(effect.get('type') or '')
            if (phase == 'turn_end' and effect_type=='blindness') or (phase=='round_end' and effect_type=='deafness'):
                value = max(CombatService._coerce_float(effect.get('minimum_value'),0), CombatService._coerce_float(effect.get('value'), 0) - 20)
                effect['value'] = value
                if value <= 0:
                    effect['active'] = False
        if phase=='round_end' and 'deafness' in health:
            health['deafness']=max(0,CombatService._coerce_float(health.get('deafness'),0)-20)
        apply_periodic_effects_to_health(health, active_effects, phase=phase,character_data=character_data)
        apply_expired_effects_to_health(health, active_effects, phase=phase,character_data=character_data)
        original_ids = {effect['id'] for effect in active_effects}
        retained_ids = {effect['id'] for effect in health['effects']}
        activated = [effect for effect in health['effects'] if effect['id'] not in original_ids]
        health['effects'] = activated + [
            effect for effect in tick_effects(active_effects, phase=phase)
            if effect.get('active', True) and effect['id'] in retained_ids
        ]
        sync_health_derived_statuses(health)
        combat_meta = health.get('combatMeta') if isinstance(health.get('combatMeta'), dict) else None
        if isinstance(combat_meta, dict):
            for key in ('bleedingModifiers',):
                items = combat_meta.get(key)
                if not isinstance(items, list):
                    continue
                updated = []
                for item in items:
                    if not isinstance(item, dict):
                        updated.append(item)
                        continue
                    remaining = item.get('remaining')
                    if remaining is None:
                        updated.append(item)
                        continue
                    tick_phase = item.get('tick') or 'turn_end'
                    if tick_phase != phase:
                        updated.append(item)
                        continue
                    next_remaining = max(0, CombatService._coerce_int(remaining, 0) - 1)
                    if next_remaining > 0:
                        updated.append({**item, 'remaining': next_remaining})
                combat_meta[key] = updated
        character_data['health'] = health
        loc_char.character.data = character_data
        flag_modified(loc_char.character, 'data')
        return loc_char

    @staticmethod
    def _advance_character_time(loc_char, elapsed_seconds):
        if not loc_char or not getattr(loc_char, 'character', None):
            return loc_char
        character_data = loc_char.character.data if isinstance(loc_char.character.data, dict) else {}
        from app.services.equipment_care import advance as advance_care
        advance_care(character_data,elapsed_seconds)
        from app.services.portable_lights import advance_glow, advance_pda
        advance_glow(character_data,elapsed_seconds)
        advance_pda(character_data,elapsed_seconds)
        health = character_data.get('health')
        if not isinstance(health, dict):
            return loc_char
        health['effects'] = advance_timed_effects(
            health,
            health.get('effects') or [],
            elapsed_seconds,
            character_data=character_data,
            advance_event_modifiers=False,
        )
        character_data['health'] = health
        loc_char.character.data = character_data
        flag_modified(loc_char.character, 'data')
        CombatService._sync_location_effects_from_character(loc_char)
        return loc_char

    @staticmethod
    def _notify_observed_death(target,character_data=None):
        """Дополнительный стресс очевидцам человеческой смерти, один раз за смерть."""
        from flask import has_app_context
        if not has_app_context() or not getattr(target,'location_id',None) or not getattr(target,'character',None):return
        data=character_data if character_data is not None else (target.character.data or {});meta=data.setdefault('health',{}).setdefault('combatMeta',{})
        if CombatService._character_condition(data)['state']!='dead':
            meta.pop('observedDeathNotified',None)
            return
        if meta.get('observedDeathNotified') or CombatService._is_mutant_character(data):return
        meta['observedDeathNotified']=True;flag_modified(target.character,'data')
        for observer in CombatService._unique_location_characters(LocationCharacter.query.filter_by(location_id=target.location_id).all()):
            if observer.id==target.id or not observer.character:continue
            observed=observer.character.data or {}
            amount=int(background_is(observed,'Математик'))+trait_count(observed,'Поставлю за них свечку')
            if not amount or CombatService._character_condition(observed)['state']!='active' or facial_profile(observed)['eyes']==2:continue
            if any(e.get('type')=='blindness' and e.get('active',True) and CombatService._coerce_float(e.get('value'),0)>=100 for e in (observed.get('health') or {}).get('effects') or []):continue
            if CombatService._can_see_location_character(observer,target):
                CombatService.apply_stress_trigger(observer,amount,trigger='witness_human_death')

    @staticmethod
    def _sync_location_effects_from_character(loc_char):
        if not loc_char or not getattr(loc_char, 'character', None):
            return loc_char
        character_data = loc_char.character.data if isinstance(loc_char.character.data, dict) else {}
        normalize_character_effects(character_data)
        loc_char.character.data = character_data
        flag_modified(loc_char.character, 'data')
        health = character_data.get('health') if isinstance(character_data, dict) else {}
        if isinstance(health, dict):
            loc_char.effects = normalize_effect_list(health.get('effects') or [])
            meta = health.setdefault('combatMeta', {})
            meta.pop('mutantAnomalyShield', None)
            triggered_ids = set(meta.get('stressTriggeredEffectIds') or [])
            for effect in loc_char.effects:
                effect_id = str(effect.get('id') or '')
                effect_type = str(effect.get('type') or '').lower()
                sense_overload = (
                    effect_type in {'blindness', 'deafness'}
                    and CombatService._coerce_float(effect.get('value'), 0) >= 90
                )
                fear_effect = effect_type in {'fear', 'fright', 'dread'}
                if effect.get('active', True) and effect_id and effect_id not in triggered_ids and (
                    sense_overload or fear_effect
                ):
                    CombatService.apply_stress_trigger(
                        loc_char, 1,
                        trigger=effect_type if fear_effect else f'{effect_type}_90',
                    )
                    triggered_ids.add(effect_id)
            meta['stressTriggeredEffectIds'] = sorted(triggered_ids)
            loc_char.effects = normalize_effect_list(health.get('effects') or [])
        else:
            loc_char.effects = []
        if CombatService._character_condition(character_data)['state'] in {
            'pain_shock', 'critical', 'dead',
        }:
            loc_char.posture = 'prone'
            loc_char.cover_object_id = None
            loc_char.weapon_braced = False
            loc_char.braced_weapon_index = None
        CombatService._notify_observed_death(loc_char)
        return loc_char

    @staticmethod
    def _apply_end_of_round_pain_recovery(loc_chars):
        for loc_char in loc_chars or []:
            character = getattr(loc_char, 'character', None)
            if not character or not isinstance(character.data, dict):
                continue
            data = character.data
            health = data.get('health')
            if not isinstance(health, dict):
                continue
            meta = health.setdefault('combatMeta', {})
            pain_level = max(0, CombatService._coerce_int(health.get('painLevel', 0), 0))
            pain_increased = bool(meta.get('painIncreased', False))
            if not pain_increased and pain_level > 0:
                health['painLevel'] = pain_level - 1
            meta['painSnapshot'] = health.get('painLevel', 0)
            meta['painIncreased'] = False
            character.data = data
            flag_modified(character, 'data')

    @staticmethod
    def _apply_end_of_round_psi_recovery(loc_chars):
        results = []
        for loc_char in loc_chars or []:
            character = getattr(loc_char, 'character', None)
            if not character or not isinstance(character.data, dict):
                continue
            data = character.data
            health = data.get('health')
            if not isinstance(health, dict):
                continue
            before = max(
                0,
                CombatService._coerce_float(
                    health.get('psyState', health.get('psy_state')), 0,
                ),
            )
            if before <= 0 or before >= 50:
                continue
            after = max(0, round(before - 1, 2))
            health['psyState'] = after
            health['psy_state'] = after
            character.data = data
            flag_modified(character, 'data')
            CombatService._sync_location_effects_from_character(loc_char)
            results.append({
                'character_id': loc_char.character_id,
                'name': character.name,
                'before': before,
                'after': after,
            })
        return results

    @staticmethod
    def _resolve_pain_shock_check(loc_char, round_number):
        character = getattr(loc_char, 'character', None)
        if not character or not isinstance(character.data, dict):
            return None
        data = character.data
        health = data.get('health') if isinstance(data.get('health'), dict) else {}
        pain_level = max(0, CombatService._coerce_int(health.get('painLevel'), 0))
        if pain_level < character_passives(data)['pain_shock_threshold'] or CombatService._character_condition(data)['state'] != 'active':
            return None
        meta = health.setdefault('combatMeta', {})
        previous_check = meta.get('lastPainShockCheck')
        if (
            isinstance(previous_check, dict)
            and CombatService._coerce_int(previous_check.get('round'), 0) == round_number
        ):
            return None
        if CombatService._coerce_int(meta.get('painShockRecoveredRound'), 0) == round_number:
            return None

        blood_stage = str(health.get('blood') or health.get('bloodStage') or 'normal').lower()
        recovered_triggers = {
            'strenuous_movement': (
                getattr(loc_char, 'movement_mode_this_turn', None) in {'run', 'sprint'}
            ),
            'fired': CombatService._coerce_int(meta.get('firedRound'), 0) == round_number,
            'new_injury': CombatService._coerce_int(meta.get('injuryRound'), 0) == round_number,
            'exhaustion': CombatService._coerce_float(health.get('exhaustion'), 0) > 0,
            'severe_blood_loss': blood_stage in {'severe', 'critical'},
        }
        if meta.get('painShockRecovered') and not any(recovered_triggers.values()):
            return None

        will_bonus = CombatService._skill_modifier(
            data,
            'skills.physical.will',
            include_pain=False,
        )
        if background_is(data,'Контрактник'):will_bonus*=2
        difficulty = max(1, pain_level * 2 - will_bonus)
        guaranteed = pain_level >= 10
        roll = None if guaranteed else random.randint(1, 20)
        order_bonus=0
        if not guaranteed:
            from app.services.brigadier_order import boost_roll
            _rolls,roll,order_bonus=boost_roll(data,[roll])
        success = False if guaranteed else (
            roll == 20 or (roll != 1 and roll+order_bonus >= difficulty)
        )
        result = {
            'kind': 'pain_shock_fall',
            'round': round_number,
            'roll': roll,
            'will_bonus': will_bonus,
            'difficulty': difficulty,
            'success': success,
            'pain_level': pain_level,
            'guaranteed': guaranteed,
            'recovered_triggers': recovered_triggers,
        }
        meta['lastPainShockCheck'] = result
        if not success:
            apply_effect_to_health(health, {
                'type': 'shock',
                'name': 'Болевой шок',
                'source': 'pain_end_turn',
                'tick': 'manual',
            })
            loc_char.posture = 'prone'
            loc_char.cover_object_id = None
            loc_char.weapon_braced = False
            loc_char.braced_weapon_index = None
        data['health'] = health
        character.data = data
        flag_modified(character, 'data')
        return result

    @staticmethod
    def _object_height(obj):
        if isinstance(obj, dict):
            obj_type = obj.get('type') or obj.get('object_type') or ''
            properties = obj.get('properties') or {}
            dimensions = properties.get('dimensions') or {}
            raw_height = dimensions.get('height', properties.get('height', obj.get('height')))
        else:
            obj_type = getattr(obj, 'type', '') or ''
            properties = getattr(obj, 'properties', {}) or {}
            dimensions = properties.get('dimensions') or {}
            raw_height = dimensions.get('height', properties.get('height', getattr(obj, 'height', None)))

        try:
            height = float(raw_height)
            if math.isfinite(height) and height >= 0:
                return height
        except (TypeError, ValueError):
            pass

        if obj_type == 'fence':
            return 1.2
        if obj_type == 'chair':
            return 0.9
        if obj_type == 'chest':
            return 1.0
        if obj_type == 'table':
            return 1.0
        if obj_type == 'shelf':
            return 2.0
        if obj_type in {'tree', 'rock', 'house', 'tent', 'wall'}:
            return 2.5
        return 1.5

    @staticmethod
    def _object_dimensions(obj, axis_aligned=True):
        if isinstance(obj, dict):
            obj_type = obj.get('type') or obj.get('object_type') or ''
            properties = obj.get('properties') or {}
            dimensions = properties.get('dimensions') or {}
        else:
            obj_type = getattr(obj, 'type', '') or ''
            properties = getattr(obj, 'properties', {}) or {}
            dimensions = properties.get('dimensions') or {}

        defaults = {
            'door': {'width': 0.9, 'depth': 0.18},
            'table': {'width': 1.4, 'depth': 0.8},
            'chair': {'width': 0.55, 'depth': 0.55},
            'shelf': {'width': 1.0, 'depth': 0.35},
            'chest': {'width': 0.9, 'depth': 0.6},
            'military_crate': {'width': 1.1, 'depth': 0.8},
            'fence': {'width': 2.0, 'depth': 0.15},
            'wall': {'width': 1.5, 'depth': 0.2},
        }
        fallback = defaults.get(obj_type, {'width': 1.0, 'depth': 1.0})
        try:
            width = float(dimensions.get('width', properties.get('width', fallback['width'])))
        except (TypeError, ValueError):
            width = fallback['width']
        try:
            depth = float(dimensions.get('depth', properties.get('depth', fallback['depth'])))
        except (TypeError, ValueError):
            depth = fallback['depth']

        rotation = 0.0
        try:
            rotation = float(properties.get('rotation', 0) or 0)
        except (TypeError, ValueError):
            rotation = 0.0
        if not axis_aligned:
            return max(.01,width),max(.01,depth)
        cosine = abs(math.cos(rotation))
        sine = abs(math.sin(rotation))
        return max(0.1, width * cosine + depth * sine), max(0.1, width * sine + depth * cosine)

    @staticmethod
    def _object_footprint_tiles(obj):
        if isinstance(obj, dict):
            tile_x = obj.get('tile_x', obj.get('x'))
            tile_y = obj.get('tile_y', obj.get('z'))
            properties = obj.get('properties') or {}
        else:
            tile_x = getattr(obj, 'tile_x', getattr(obj, 'x', None))
            tile_y = getattr(obj, 'tile_y', getattr(obj, 'z', None))
            properties = getattr(obj, 'properties', {}) or {}
        if tile_x is None or tile_y is None:
            return set()

        width, depth = CombatService._object_dimensions(obj)
        center_x = float(tile_x) + 0.5 + float(properties.get('offset_x', 0) or 0)
        center_y = float(tile_y) + 0.5 + float(properties.get('offset_z', 0) or 0)
        min_x = center_x - (width / 2)
        max_x = center_x + (width / 2)
        min_y = center_y - (depth / 2)
        max_y = center_y + (depth / 2)

        tiles = set()
        for x in range(math.floor(min_x), math.floor(max_x - 0.0001) + 1):
            for y in range(math.floor(min_y), math.floor(max_y - 0.0001) + 1):
                if min_x < x + 1 and max_x > x and min_y < y + 1 and max_y > y:
                    tiles.add((x, y))
        return tiles

    @staticmethod
    def _door_is_in_wall(door, wall):
        if getattr(door, 'type', None) not in {'door', 'window'} or getattr(wall, 'type', None) != 'wall':
            return False
        from app.services.location_navigation import level_of
        if level_of(door) != level_of(wall):
            return False
        wall_props = wall.properties or {}
        door_props = door.properties or {}

        def building_id(properties):
            value = properties.get('building_id')
            return None if value is None else str(value).strip() or None

        if building_id(wall_props) != building_id(door_props):
            return False
        wall_bottom = float(wall_props.get('elevation', 0) or 0)
        portal_bottom = float(door_props.get('elevation', 0) or 0)
        if door.type == 'window':
            wall_height = float((wall_props.get('dimensions') or {}).get('height', 2.4))
            portal_height = float((door_props.get('dimensions') or {}).get('height', 1))
            if portal_bottom < wall_bottom or portal_bottom + portal_height > wall_bottom + wall_height + 0.15:
                return False
        elif abs(wall_bottom - portal_bottom) > 0.15:
            return False
        wall_dims = wall_props.get('dimensions') or {}
        door_dims = door_props.get('dimensions') or {}
        wall_angle = float(wall_props.get('rotation', 0) or 0)
        door_angle = float(door_props.get('rotation', 0) or 0)
        if abs(math.cos(wall_angle - door_angle)) < 0.94:
            return False
        wall_x = wall.tile_x + 0.5 + float(wall_props.get('offset_x', 0) or 0)
        wall_y = wall.tile_y + 0.5 + float(wall_props.get('offset_z', 0) or 0)
        door_x = door.tile_x + 0.5 + float(door_props.get('offset_x', 0) or 0)
        door_y = door.tile_y + 0.5 + float(door_props.get('offset_z', 0) or 0)
        dx, dy = door_x - wall_x, door_y - wall_y
        along = dx * math.cos(wall_angle) - dy * math.sin(wall_angle)
        away = abs(dx * math.sin(wall_angle) + dy * math.cos(wall_angle))
        wall_width = float(wall_dims.get('width', 3))
        wall_depth = float(wall_dims.get('depth', 0.2))
        door_width = float(door_dims.get('width', 0.9))
        door_depth = float(door_dims.get('depth', 0.18))
        return away <= (wall_depth + door_depth) / 2 + 0.12 and abs(along) <= (wall_width + door_width) / 2

    @staticmethod
    def _line_through_door(shooter, target, door, wall, ray_height=None):
        if not CombatService._door_is_in_wall(door, wall):
            return False
        wall_props = wall.properties or {}
        door_props = door.properties or {}
        wall_angle = float(wall_props.get('rotation', 0) or 0)
        normal_x, normal_y = math.sin(wall_angle), math.cos(wall_angle)
        axis_x, axis_y = math.cos(wall_angle), -math.sin(wall_angle)
        start_x, start_y = float(shooter.pos_x) + 0.5, float(shooter.pos_y) + 0.5
        end_x, end_y = float(target.pos_x) + 0.5, float(target.pos_y) + 0.5
        delta_x, delta_y = end_x - start_x, end_y - start_y
        denominator = delta_x * normal_x + delta_y * normal_y
        if abs(denominator) < 1e-9:
            return False
        wall_x = wall.tile_x + 0.5 + float(wall_props.get('offset_x', 0) or 0)
        wall_y = wall.tile_y + 0.5 + float(wall_props.get('offset_z', 0) or 0)
        fraction = ((wall_x - start_x) * normal_x + (wall_y - start_y) * normal_y) / denominator
        if not 0.01 < fraction < 0.99:
            return False
        door_x = door.tile_x + 0.5 + float(door_props.get('offset_x', 0) or 0)
        door_y = door.tile_y + 0.5 + float(door_props.get('offset_z', 0) or 0)
        hit_x = start_x + fraction * delta_x
        hit_y = start_y + fraction * delta_y
        along = (hit_x - door_x) * axis_x + (hit_y - door_y) * axis_y
        door_width = float((door_props.get('dimensions') or {}).get('width', 0.9))
        door_height = float((door_props.get('dimensions') or {}).get('height', 2))
        opening_bottom = float(door_props.get('elevation', 0) or 0) - float(wall_props.get('elevation', 0) or 0)
        return abs(along) <= door_width / 2 and (
            ray_height is None or opening_bottom <= ray_height <= opening_bottom + door_height
        )

    @staticmethod
    def physical_position(character, *, objects=None):
        from app.services.location_navigation import surface, ground
        level = getattr(character, 'floor_level', 0) or 0
        location = character.location
        objects=objects if objects is not None else LocationObject.query.filter_by(location_id=location.id).all()
        node = surface(location, objects, character.pos_x, character.pos_y, level)
        height = node['height'] if node else ground(location, character.pos_x, character.pos_y) + level * 2.4
        from app.services.character_jump import object_support_height
        height = object_support_height(character, height)
        return {'floor_level': level, 'height': height}

    @staticmethod
    def _grid_move_cost(start_x, start_y, end_x, end_y):
        return max(abs(end_x - start_x), abs(end_y - start_y))

    @staticmethod
    def _object_movement_profile(obj):
        if not obj:
            return {'blocked': False, 'climb_cost': 0}

        if isinstance(obj, dict):
            obj_type = obj.get('type') or obj.get('object_type') or ''
            properties = obj.get('properties') or {}
        else:
            obj_type = getattr(obj, 'type', '') or ''
            properties = getattr(obj, 'properties', {}) or {}

        height = CombatService._object_height(obj)

        # Anomalies trap a character through combat rules, not collision.
        if obj_type == 'anomaly':
            return {'blocked': False, 'climb_cost': 0}

        if obj_type in {'campfire', 'torch', 'wall_torch', 'ceiling_bulb', 'wall_bulb', 'ground_item', 'fire_barrel', 'poster', 'floor', 'stairs', 'ladder', 'hatch', 'floor_opening'}:
            return {'blocked': False, 'climb_cost': 0}

        category = properties.get('traversal_category','auto')
        if not isinstance(category,str):
            category='auto'
        if category in {'passable','low','high','blocked'}:
            return {'blocked':category=='blocked','climb_cost':{'low':5,'high':12}.get(category,0)}
        if properties.get('passable') is True:
            return {'blocked':False,'climb_cost':0}
        if obj_type == 'door':
            if properties.get('is_open'):
                return {'blocked': False, 'climb_cost': 0}
            if properties.get('climbable'):
                return {'blocked': False, 'climb_cost': max(1, CombatService._coerce_float(properties.get('climb_cost'), 12))}
            return {'blocked': True, 'climb_cost': 0}

        if obj_type == 'window':
            return {'blocked': True, 'climb_cost': 0}

        if properties.get('blocks_movement') is False or properties.get('block_movement') is False:
            return {'blocked': False, 'climb_cost': 0}

        if height < .5:
            return {'blocked':False,'climb_cost':0}
        # Старое явное climbable сохраняется как переопределение ГМа.
        if height >= 2 and not properties.get('climbable'):
            return {'blocked':True,'climb_cost':0}
        base = 5 if height < 1 else 12
        return {'blocked':False,'climb_cost':max(1,CombatService._coerce_float(properties.get('climb_cost'),base))}

    @staticmethod
    def _climb_payment_profile(character_data, climbs):
        bonus = CombatService._skill_modifier(character_data or {},'skills.physical.agility')
        return {'movement_points':sum(max(0,item['base_cost']-bonus) for item in climbs),
                'action_points':sum(3 if item.get('category')=='high' else 1 for item in climbs),
                'agility_bonus':bonus}

    @staticmethod
    def _object_climb_event(obj):
        props = obj.get('properties',{}) if isinstance(obj,dict) else obj.properties or {}
        category = props.get('traversal_category')
        if not isinstance(category,str) or category not in {'low','high'}:
            category = 'high' if CombatService._object_height(obj)>=1 else 'low'
        return {'base_cost':CombatService._object_movement_profile(obj)['climb_cost'],'category':category}

    @staticmethod
    def _anomaly_key_from_object(obj):
        if isinstance(obj, dict):
            properties = obj.get('properties') if isinstance(obj.get('properties'), dict) else {}
            raw = (
                obj.get('anomaly_key') or obj.get('anomalyKey')
                or properties.get('anomaly_key') or properties.get('anomalyKey')
                or obj.get('anomalyType') or properties.get('anomalyType')
            )
        else:
            properties = getattr(obj, 'properties', {}) or {}
            raw = properties.get('anomaly_key') or properties.get('anomalyKey') or properties.get('anomalyType')
        raw = str(raw or '').strip().lower()
        legacy = {
            'fire': 'banya', 'electric': 'vspishka', 'acid': 'varevo',
            'void': 'batut', 'radiation': 'zarosli', 'psi': 'ekho',
        }
        return raw if anomaly_profile(raw) else legacy.get(raw)

    @staticmethod
    def _anomaly_candidates(location):
        tiles = location.tiles_data if isinstance(location.tiles_data, list) else []
        candidates = []
        for origin_y, row in enumerate(tiles):
            if not isinstance(row, list):
                continue
            for origin_x, tile in enumerate(row):
                if not isinstance(tile, dict):
                    continue
                for index, obj in enumerate(tile.get('objects') or []):
                    if not isinstance(obj, dict) or (obj.get('type') or obj.get('object_type')) != 'anomaly':
                        continue
                    key = CombatService._anomaly_key_from_object(obj)
                    profile = anomaly_profile(key)
                    if profile:
                        candidates.append((obj, origin_x, origin_y, {
                            'key': key, 'profile': profile, 'object_id': None,
                            'tile_object_index': index, 'origin_x': origin_x, 'origin_y': origin_y,
                        }))
        for obj in LocationObject.query.filter_by(location_id=location.id, type='anomaly').all():
            key = CombatService._anomaly_key_from_object(obj)
            profile = anomaly_profile(key)
            if profile:
                candidates.append((obj, obj.tile_x, obj.tile_y, {
                    'key': key, 'profile': profile, 'object_id': obj.id,
                    'origin_x': obj.tile_x, 'origin_y': obj.tile_y,
                }))
        return candidates

    @staticmethod
    def _anomalies_at_tile(location, tile_x, tile_y, candidates=None, floor_level=0):
        from app.services.location_navigation import level_of
        if candidates is None:
            candidates = CombatService._anomaly_candidates(location)
        matches = [
            (origin_x != tile_x or origin_y != tile_y, entry)
            for obj, origin_x, origin_y, entry in candidates
            if level_of(obj) == floor_level and anomaly_covers_tile(obj, origin_x, origin_y, tile_x, tile_y)
        ]
        matches.sort(key=lambda match: match[0])
        return [entry for _, entry in matches]

    @staticmethod
    def _active_anomaly(loc_char):
        data = loc_char.character.data if loc_char.character and isinstance(loc_char.character.data, dict) else {}
        health = data.setdefault('health', {})
        meta = health.setdefault('combatMeta', {})
        active = meta.get('activeAnomaly')
        return data, health, meta, active if isinstance(active, dict) else None

    @staticmethod
    def _enter_anomaly(loc_char, anomaly, round_number, previous_position=None):
        data, _, meta, _ = CombatService._active_anomaly(loc_char)
        profile = anomaly['profile']
        revealed = False
        if anomaly.get('object_id'):
            obj = db.session.get(LocationObject, anomaly['object_id'])
            if obj and (obj.properties or {}).get('gm_hidden'):
                obj.properties = {**obj.properties, 'gm_hidden': False}
                revealed = True
        else:
            location = loc_char.location
            x, y, index = anomaly.get('origin_x'), anomaly.get('origin_y'), anomaly.get('tile_object_index')
            tiles = location.tiles_data if isinstance(location.tiles_data, list) else []
            if x is not None and y is not None and index is not None and 0 <= y < len(tiles) and 0 <= x < len(tiles[y]):
                tile = tiles[y][x]
                objects = tile.get('objects') if isinstance(tile, dict) else None
                if isinstance(objects, list) and index < len(objects) and isinstance(objects[index], dict) and objects[index].get('gm_hidden'):
                    objects[index] = {**objects[index], 'gm_hidden': False}
                    flag_modified(location, 'tiles_data')
                    revealed = True
        if revealed:
            loc_char._revealed_anomaly = {
                'object_id': anomaly.get('object_id'),
                'tile_x': anomaly.get('origin_x'), 'tile_y': anomaly.get('origin_y'),
            }
        active = {
            'key': anomaly['key'], 'name': profile['name'], 'category': profile['category'],
            'dc': profile['dc'], 'tile_x': loc_char.pos_x, 'tile_y': loc_char.pos_y,
            'object_id': anomaly.get('object_id'), 'entered_round': max(1, round_number or 1),
            'origin_x': anomaly.get('origin_x'), 'origin_y': anomaly.get('origin_y'),
            'rounds': 0,
        }
        if previous_position:
            previous_x, previous_y = previous_position
            active['entry_dx'] = 0 if loc_char.pos_x == previous_x else (1 if loc_char.pos_x > previous_x else -1)
            active['entry_dy'] = 0 if loc_char.pos_y == previous_y else (1 if loc_char.pos_y > previous_y else -1)
        meta['activeAnomaly'] = active
        loc_char.character.data = data
        flag_modified(loc_char.character, 'data')
        return active

    @staticmethod
    def _leave_anomaly(loc_char):
        data, health, meta, active = CombatService._active_anomaly(loc_char)
        if not active:
            return False
        meta.pop('activeAnomaly', None)
        health['effects'] = [
            effect for effect in normalize_effect_list(health.get('effects') or [])
            if not (
                effect.get('type') == 'blindness'
                and str(effect.get('source') or '') == str(active.get('name') or '')
            )
        ]
        sync_health_derived_statuses(health)
        loc_char.character.data = data
        flag_modified(loc_char.character, 'data')
        CombatService._sync_location_effects_from_character(loc_char)
        return True

    @staticmethod
    def _sync_anomaly_after_relocation(location, loc_char, previous_position):
        if (loc_char.pos_x, loc_char.pos_y) == previous_position:
            return False
        _, _, _, active = CombatService._active_anomaly(loc_char)
        if not active:
            return False
        anomalies = CombatService._anomalies_at_tile(location, loc_char.pos_x, loc_char.pos_y, floor_level=getattr(loc_char, 'floor_level', 0) or 0)
        if any(
            anomaly['key'] == active.get('key')
            and anomaly.get('object_id') == active.get('object_id')
            and anomaly.get('origin_x') == active.get('origin_x')
            and anomaly.get('origin_y') == active.get('origin_y')
            for anomaly in anomalies
        ):
            return False
        CombatService._leave_anomaly(loc_char)
        if anomalies:
            CombatService._enter_anomaly(
                loc_char, anomalies[0], 1, previous_position=previous_position,
            )
        return True

    @staticmethod
    def _apply_anomaly_exposure(
        loc_char, active, fraction=1.0, *, apply_category_effect=True,
        round_number=0, exiting=False, escape_margin=None, condition=None,
    ):
        profile = anomaly_profile(active.get('key')) or {}
        fraction = max(0.0, CombatService._coerce_float(fraction, 0))
        rounds = max(0, CombatService._coerce_int(active.get('rounds'), 0))
        damage = CombatService._coerce_float(profile.get('damage'), 0)
        hit_count = None
        if profile.get('unit_damage') and escape_margin is not None and fraction > 0:
            hit_count = max(1, min(
                CombatService._coerce_int(profile.get('max_hits'), 1),
                max(1, CombatService._coerce_int(escape_margin, 1)),
            ))
            damage = CombatService._coerce_float(profile.get('unit_damage'), 0) * hit_count
            fraction = 1.0
        if exiting and profile.get('exit_damage'):
            damage = CombatService._coerce_float(profile.get('exit_damage'), damage)
            fraction = 1.0
        elif active.get('key') == 'kapkan' and rounds >= 1:
            damage = CombatService._coerce_float(
                profile.get('secondary_damage'), damage,
            )
        elif active.get('key') == 'ionny_tuman' and escape_margin is not None:
            damage = CombatService._coerce_float(
                (profile.get('movement_damage') or {}).get('walk'), damage,
            )
        elif profile.get('doubling_damage') and rounds > 0:
            damage *= 2 ** rounds
        if profile.get('third_round_damage') and rounds >= 2:
            damage = CombatService._coerce_float(profile.get('third_round_damage'), damage)
        if profile.get('escalating_damage') and rounds > 0:
            damage += CombatService._coerce_float(profile.get('escalating_damage'), 0) * rounds
        result = {'name': profile.get('name'), 'fraction': fraction, 'damage': 0}
        if hit_count is not None:
            result['hit_count'] = hit_count
        if profile.get('fall_height') and fraction > 0:
            fall = CombatService.resolve_fall(
                loc_char,
                profile['fall_height'],
                round_number=round_number,
                damage_multiplier=fraction,
            )
            result['fall'] = fall
            result['damage'] = sum(item.get('damage', 0) for item in fall.get('legs', []))
        elif damage > 0 and fraction > 0:
            character_data = (
                loc_char.character.data
                if loc_char.character and isinstance(loc_char.character.data, dict)
                else {}
            )
            protection_type = {
                'gravity': 'physical', 'electric': 'electric', 'thermal': 'thermal',
                'chemical': 'chemical', 'psi': 'psi',
            }.get(profile.get('category'))
            protection = (
                CombatService._target_elemental_protection(character_data, protection_type,**({'target':loc_char} if background_is(character_data,'Стойкий') else {}))
                if protection_type else 0
            )
            if profile.get('zone'):
                raw_damage = max(0, round(damage * fraction))
                zone_health = CombatService._apply_attack_damage(
                    loc_char,
                    raw_damage,
                    profile['zone'],
                    {'damage_type': 'crushing', 'armor_piercing': 0, 'damage': raw_damage},
                    round_number=round_number,
                    allow_bleeding=False,
                    trauma_checks=0,
                    stress_trigger='anomaly',
                )
                result['damage'] = (zone_health.get('_attackOutcome') or {}).get('applied_damage',raw_damage)
                result['zone'] = profile['zone']
                result['zone_health'] = (zone_health.get('zones') or {}).get(profile['zone'])
            else:
                damage_after_protection = damage * fraction * max(0, 100 - protection) / 100
                result['damage_result'] = CombatService._apply_general_damage(
                    loc_char, damage_after_protection, round_number=round_number, source='anomaly',
                )
                result['damage'] = result['damage_result']['damage']
                result['protection'] = protection

        data = loc_char.character.data if isinstance(loc_char.character.data, dict) else {}
        health = data.setdefault('health', {})
        radiation = CombatService._coerce_float(profile.get('radiation'), 0) * fraction
        if radiation:
            radiation_result = CombatService._apply_incoming_radiation(
                data, radiation, binary=False,target=loc_char,
            )
            health = data.setdefault('health', {})
            result['radiation'] = radiation_result
        psi_value = CombatService._coerce_float(profile.get('psi'), 0)
        if condition == 'noise' and profile.get('psi_noisy') is not None:
            psi_value = CombatService._coerce_float(profile.get('psi_noisy'), psi_value)
        elif condition == 'eyes_closed' and profile.get('psi_eyes_closed') is not None:
            psi_value = CombatService._coerce_float(profile.get('psi_eyes_closed'), psi_value)
        psi = psi_value * fraction
        if psi:
            psi_result = CombatService._increase_psy_state(
                data, psi, 'anomaly', loc_char=loc_char,
            )
            health = data.setdefault('health', {})
            result['psi'] = psi_result['received']
            result['psi_protection'] = psi_result['defense']['total']
            result['psi_state'] = psi_result
        for effect_type, profile_key in (('pain', 'pain'), ('exhaustion', 'exhaustion')):
            amount = CombatService._coerce_float(profile.get(profile_key), 0) * fraction
            if amount:
                apply_effect_to_health(health, {'type': effect_type, 'value': amount, 'source': 'anomaly'},character_data=data)
                result[profile_key] = amount
        if profile.get('temperature') and fraction:
            delta = CombatService._coerce_float(profile['temperature'], 0) * fraction
            health['temperature'] = CombatService._coerce_float(health.get('temperature'), 36) + delta
            result['temperature'] = delta
        bleeding = profile.get('bleeding') if isinstance(profile.get('bleeding'), dict) else None
        if bleeding and apply_category_effect and fraction:
            kind = bleeding.get('kind', 'external')
            stage = bleeding.get('stage', 'light')
            apply_effect_to_health(health, {
                'type': f'bleeding_{kind}_{stage}', 'area': bleeding.get('area') or 'chest',
                'source': 'anomaly',
            })
            result['bleeding'] = {'kind': kind, 'stage': stage}
        if apply_category_effect and fraction:
            category = profile.get('category')
            if category == 'thermal':
                apply_effect_to_health(health, {'type': 'burning', 'name': '\u0413\u043e\u0440\u0435\u043d\u0438\u0435', 'remaining': 2, 'tick': 'turn_end', 'source': 'anomaly'})
            elif category == 'electric':
                apply_effect_to_health(health, {'type': 'anomaly_electric_penalty', 'name': '\u042d\u043b\u0435\u043a\u0442\u0440\u043e\u0448\u043e\u043a', 'remaining': 2, 'tick': 'turn_end', 'roll_modifier': -2, 'source': 'anomaly'})
            elif category == 'gravity' and not bleeding:
                for area in ('left_leg', 'right_leg'):
                    apply_effect_to_health(health, {'type': 'bleeding_external_light', 'area': area, 'source': 'anomaly'})
            elif category == 'chemical' and not profile.get('pain'):
                if random.randint(1, 100) <= 50:
                    apply_effect_to_health(health, {'type': 'fracture', 'area': random.choice(('left_arm', 'right_arm', 'left_leg', 'right_leg')), 'source': 'anomaly'})
                else:
                    apply_effect_to_health(health, {'type': 'pain', 'value': 3, 'source': 'anomaly'},character_data=data)
            elif category == 'radiation' and not profile.get('exhaustion'):
                apply_effect_to_health(health, {'type': 'exhaustion', 'value': 1, 'source': 'anomaly'})
            elif category == 'psi':
                apply_effect_to_health(health, {'type': 'anomaly_will_penalty', 'name': '\u041f\u0441\u0438-\u0432\u043e\u0437\u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435', 'remaining': 2, 'tick': 'turn_end', 'will_modifier': -2, 'source': 'anomaly'})
            if profile.get('will_penalty'):
                apply_effect_to_health(health, {
                    'type': 'anomaly_will_penalty',
                    'name': '\u041f\u043e\u0434\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u0432\u043e\u043b\u0438',
                    'remaining': 2, 'tick': 'turn_end',
                    'will_modifier': -abs(CombatService._coerce_int(profile['will_penalty'], 0)),
                    'source': profile.get('name') or 'anomaly',
                })
        equipment_damage = CombatService._apply_anomaly_equipment_damage(
            data,
            damage * fraction,
            profile,
        )
        if equipment_damage:
            result['equipment_damage'] = equipment_damage
            if profile.get('blindness'):
                apply_effect_to_health(health, {
                    'type': 'blindness', 'value': profile['blindness'],
                    'source': profile.get('name') or 'anomaly',
                })
            if profile.get('harmful_action_on_failure') and escape_margin is not None and escape_margin > 0:
                apply_effect_to_health(health, {
                    'type': 'anomaly_compulsion',
                    'name': 'Приказ Старшины',
                    'source': profile.get('name') or 'anomaly',
                    'tick': 'manual',
                    'description': 'Персонаж должен совершить действие, вредящее себе.',
                })
                result['harmful_action_required'] = True
        sync_health_derived_statuses(health)
        loc_char.character.data = data
        flag_modified(loc_char.character, 'data')
        CombatService._sync_location_effects_from_character(loc_char)
        return result

    @staticmethod
    def _apply_anomaly_equipment_damage(character_data, damage, profile):
        if profile.get('category',profile.get('damage_type'))=='electric':
            from app.services.prostheses import discharge
            discharge(character_data)
        equipment = character_data.get('equipment') if isinstance(character_data, dict) else {}
        equipment = equipment if isinstance(equipment, dict) else {}
        results = []
        armor_multiplier = max(
            1,
            CombatService._coerce_float(profile.get('armor_damage_multiplier'), 1),
        )
        for slot in ('armor', 'helmet', 'gasMask'):
            item = equipment.get(slot)
            if not isinstance(item, dict):
                continue
            attributes = CombatService._template_attributes(item)
            if CombatService._uses_gas_mask_durability(slot, item, attributes):
                result = CombatService._damage_gas_mask(item, 'anomaly')
            elif armor_multiplier > 1:
                from app.services.equipment_modifications import rule as modification_rule
                result = CombatService._damage_armor_item(
                    item, attributes, damage * armor_multiplier*max(0,1-modification_rule(item,'anomaly_wear_reduction')),
                    {'fire':'thermal','burn':'thermal','heat':'thermal','acid':'chemical','poison':'chemical',
                     'electricity':'electric'}.get(str(profile.get('damage_type') or profile.get('category') or '').lower(),
                         profile.get('damage_type') or profile.get('category') or 'physical'),
                )
            else:
                result = None
            if result:
                result['slot'] = slot
                results.append(result)
        weapon_multiplier = max(
            1,
            CombatService._coerce_float(profile.get('weapon_damage_multiplier'), 1),
        )
        if weapon_multiplier > 1:
            weapons = character_data.get('weapons') or []
            active_index = CombatService._coerce_int(
                character_data.get('activeWeaponIndex'), -1,
            )
            if 0 <= active_index < len(weapons) and isinstance(weapons[active_index], dict):
                weapon_result = CombatService._apply_weapon_wear(
                    weapons[active_index],
                    math.ceil(max(0, damage) * weapon_multiplier / 20),
                )
                weapon_result.update({
                    'slot': 'weapon',
                    'name': weapons[active_index].get('name'),
                })
                results.append(weapon_result)
        return results

    @staticmethod
    def _resolve_anomaly_end_turn(loc_char, round_number):
        _, _, _, active = CombatService._active_anomaly(loc_char)
        if not active:
            return None
        result = CombatService._apply_anomaly_exposure(
            loc_char, active, 1.0, apply_category_effect=True, round_number=round_number,
        )
        data, _, meta, _ = CombatService._active_anomaly(loc_char)
        profile = anomaly_profile(active.get('key')) or {}
        if profile.get('ejects') or profile.get('fall_height'):
            location = CombatService._get_location(loc_char.location_id)
            dx = CombatService._coerce_int(active.get('entry_dx'), 0)
            dy = CombatService._coerce_int(active.get('entry_dy'), 0)
            if not dx and not dy:
                dx = 1
            target_x, target_y = loc_char.pos_x + dx, loc_char.pos_y + dy
            blocked, climb = CombatService._build_movement_map(
                location, loc_char.character_id,
            )
            if (
                0 <= target_x < location.grid_width
                and 0 <= target_y < location.grid_height
                and (target_x, target_y) not in blocked
                and not climb.get((target_x, target_y), 0)
            ):
                loc_char.pos_x, loc_char.pos_y = target_x, target_y
                result['ejected_to'] = {'x': target_x, 'y': target_y}
            meta.pop('activeAnomaly', None)
            loc_char.character.data = data
            flag_modified(loc_char.character, 'data')
            return result
        active['rounds'] = max(0, CombatService._coerce_int(active.get('rounds'), 0)) + 1
        meta['activeAnomaly'] = active
        loc_char.character.data = data
        flag_modified(loc_char.character, 'data')
        return result

    @staticmethod
    def _object_drop_plan(location, character, portal):
        from app.services.location_navigation import drop_route
        objects = LocationObject.query.filter_by(location_id=location.id).all()
        geometry = CombatService._movement_wall_geometry(location, objects)
        geometry.add_characters(c for c in CombatService._unique_location_characters(
            LocationCharacter.query.filter_by(location_id=location.id).all())
            if c.character_id != character.character_id)
        plan = drop_route(location,objects,(character.pos_x,character.pos_y,character.floor_level or 0),
                          portal,None,geometry=geometry)
        if not plan:
            return None
        landing=plan['landing']
        return {**plan,'special_action':'drop_down','object_id':portal.id,
                'floor_level':landing['level'],'path':[(p['x'],p['y']) for p in plan['surfaces']],
                'distance':sum(math.hypot(b['x']-a['x'],b['y']-a['y']) for a,b in zip(plan['surfaces'],plan['surfaces'][1:])),
                'climbs':[],'climb_cost':0,'cost':0}

    @staticmethod
    def _embedded_movement_object(obj, tile_x, tile_y):
        from types import SimpleNamespace
        props = dict(obj.get('properties') or {})
        dimensions = props.get('dimensions', obj.get('dimensions'))
        if isinstance(dimensions,(list,tuple)) and len(dimensions)==3:
            dimensions = dict(zip(('width','depth','height'),dimensions))
        kind = obj.get('type',obj.get('object_type',''))
        if not dimensions and kind=='tree':
            # Тот же preset tree, что в locationAssetCatalog.js и applyDecorSize.
            scale=CombatService._coerce_float(obj.get('scale'),1)
            dimensions=dict(width=.92*scale,depth=.92*scale,height=1.72*scale)
        props.update(level=props.get('level',obj.get('level',0)),
                     offset_x=CombatService._coerce_float(obj.get('x'),0),
                     offset_z=CombatService._coerce_float(obj.get('z'),0),
                     rotation=math.radians(CombatService._coerce_float(obj.get('rotation'),0)),
                     elevation=CombatService._coerce_float(obj.get('elevation'),0))
        if dimensions:
            props['dimensions']=dimensions
        return SimpleNamespace(type=kind,tile_x=tile_x,tile_y=tile_y,properties=props)

    @staticmethod
    def _movement_wall_geometry(location, objects):
        from dataclasses import replace
        from app.services.location_navigation import level_of,ground,STOREY_HEIGHT
        from app.services.movement_geometry import WallGeometry, wall_boxes, tree_trunk_box
        entries = [(o, o) for o in objects]
        for y, row in enumerate(getattr(location, 'tiles_data', None) or []):
            if not isinstance(row, list):
                continue
            for x, tile in enumerate(row):
                if not isinstance(tile, dict):
                    continue
                for obj in tile.get('objects') or []:
                    if isinstance(obj, dict):
                        entries.append((obj, CombatService._embedded_movement_object(obj,x,y)))
        doors = [o for _, o in entries if o.type == 'door' and (o.properties or {}).get('is_open')]
        boxes, handled, climbs, portals = [], set(), [], []
        for original, wall in entries:
            if wall.type in {'stairs','ladder','hatch','floor_opening'}:
                continue
            profile = CombatService._object_movement_profile(wall)
            if profile['blocked'] or profile['climb_cost']:
                openings = [door for door in doors if CombatService._door_is_in_wall(door, wall)]
                handled.add(id(original))
                size=CombatService._object_dimensions(wall,axis_aligned=False)
                trunk=tree_trunk_box(wall,size)
                parts = [trunk] if trunk is not None else wall_boxes(wall,openings,size=size)
                bottom = ground(location,wall.tile_x,wall.tile_y)+level_of(wall)*STOREY_HEIGHT+CombatService._coerce_float((wall.properties or {}).get('elevation'),0)
                parts = [replace(part,bottom=bottom,top=bottom+CombatService._object_height(wall),object_id=getattr(wall,'id',None)) for part in parts]
                if profile['blocked']:
                    boxes.extend(parts)
                else:
                    props=wall.properties or {}
                    event = {**CombatService._object_climb_event(wall),
                        'top_height':ground(location,wall.tile_x,wall.tile_y)+level_of(wall)*STOREY_HEIGHT
                            +CombatService._coerce_float(props.get('elevation'),0)+CombatService._object_height(wall)+.035}
                    climbs.extend((box,profile['climb_cost'],event) for box in parts)
                for door in openings:
                    props = door.properties or {}
                    x,y = door.tile_x+float(props.get('offset_x',0) or 0),door.tile_y+float(props.get('offset_z',0) or 0)
                    angle = float((wall.properties or {}).get('rotation',0) or 0)
                    depth = CombatService._object_dimensions(wall,axis_aligned=False)[1]
                    shift = depth/2+.25+.01+.02
                    portals.extend((x+math.sin(angle)*s,y+math.cos(angle)*s,level_of(door)) for s in (-shift,0,shift))
        return WallGeometry(boxes,handled,climbs,portals)

    @staticmethod
    def _build_movement_map(
        location,
        moving_character_id=None,
        ignored_character_ids=None,
        floor_level=None,
        wall_geometry=None,
        continuous=False,
    ):
        from app.services.location_navigation import level_of
        if floor_level is None:
            actor = LocationCharacter.query.filter_by(location_id=location.id, character_id=moving_character_id).first() if moving_character_id is not None else None
            floor_level = getattr(actor, 'floor_level', 0) or 0
        blocked_tiles = set()
        climb_cost_tiles = {}
        ignored_ids = {
            CombatService._coerce_int(item, -1)
            for item in (ignored_character_ids or [])
        }
        if moving_character_id is not None:
            ignored_ids.add(CombatService._coerce_int(moving_character_id, -1))

        location_objects = LocationObject.query.filter_by(location_id=location.id).all()
        if wall_geometry is None:
            wall_geometry = CombatService._movement_wall_geometry(location, location_objects)
        tiles = location.tiles_data if isinstance(location.tiles_data, list) else []
        for y, row in enumerate(tiles):
            if not isinstance(row, list):
                continue
            for x, tile in enumerate(row):
                if not isinstance(tile, dict):
                    continue
                for obj in tile.get('objects') or []:
                    if level_of(obj) != floor_level:
                        continue
                    profile = CombatService._object_movement_profile(obj)
                    if not profile['blocked'] and profile['climb_cost'] <= 0:
                        continue
                    if wall_geometry is not None and wall_geometry.handles(obj) and (profile['blocked'] or continuous):
                        continue
                    footprint_source = {
                        **obj,
                        'tile_x': obj.get('tile_x', obj.get('x', x)),
                        'tile_y': obj.get('tile_y', obj.get('z', y)),
                    }
                    for footprint_tile in CombatService._object_footprint_tiles(footprint_source):
                        if profile['blocked']:
                            blocked_tiles.add(footprint_tile)
                            climb_cost_tiles.pop(footprint_tile, None)
                        elif profile['climb_cost'] > 0 and footprint_tile not in blocked_tiles:
                            climb_cost_tiles[footprint_tile] = max(climb_cost_tiles.get(footprint_tile, 0), profile['climb_cost'])

        open_doors = [obj for obj in location_objects if obj.type == 'door' and (obj.properties or {}).get('is_open')]
        for obj in location_objects:
            if level_of(obj) != floor_level or obj.type in {'stairs', 'ladder', 'hatch', 'floor_opening'}:
                continue
            profile = CombatService._object_movement_profile(obj)
            if not profile['blocked'] and profile['climb_cost'] <= 0:
                continue
            if wall_geometry is not None and wall_geometry.handles(obj) and (profile['blocked'] or continuous):
                continue
            doorway_tiles = set()
            if obj.type == 'wall':
                for door in open_doors:
                    if CombatService._door_is_in_wall(door, obj):
                        doorway_tiles.update(CombatService._object_footprint_tiles(door))
            for coords in CombatService._object_footprint_tiles(obj):
                if coords in doorway_tiles:
                    continue
                if profile['blocked']:
                    blocked_tiles.add(coords)
                    climb_cost_tiles.pop(coords, None)
                elif profile['climb_cost'] > 0 and coords not in blocked_tiles:
                    climb_cost_tiles[coords] = max(climb_cost_tiles.get(coords, 0), profile['climb_cost'])

        if wall_geometry is not None and not continuous:
            blocked_tiles.update(wall_geometry.blocked_cells(floor_level))
            for coords in blocked_tiles:
                climb_cost_tiles.pop(coords, None)

        for character in CombatService._unique_location_characters(
            LocationCharacter.query.filter_by(location_id=location.id).all()
        ):
            if continuous:
                continue
            if (getattr(character, 'floor_level', 0) or 0) != floor_level:
                continue
            if CombatService._coerce_int(character.character_id, -1) in ignored_ids:
                continue
            blocked_tiles.add((character.pos_x, character.pos_y))
            climb_cost_tiles.pop((character.pos_x, character.pos_y), None)

        return blocked_tiles, climb_cost_tiles

    @staticmethod
    def _find_climb_landing(location, object_item, actor_character):
        from app.services.location_navigation import surface,level_of,pose,opening_at
        from app.services.movement_geometry import WallBox,CHARACTER_RADIUS,WALL_CLEARANCE
        occupied_tiles = CombatService._object_footprint_tiles(object_item)
        if not occupied_tiles:
            return None

        level = actor_character.floor_level or 0
        objects = LocationObject.query.filter_by(location_id=location.id).all()
        origin = surface(location,objects,actor_character.pos_x,actor_character.pos_y,level)
        if not origin or level_of(object_item)!=level:
            return None
        cx,cy,_,_,angle=pose(object_item)
        width,depth=CombatService._object_dimensions(object_item,axis_aligned=False)
        barrier=WallBox(cx,cy,width,depth,angle,level)
        start_world=(actor_character.pos_x+.5,actor_character.pos_y+.5)
        local_x,local_y=barrier.local(start_world)
        if math.hypot(max(0,abs(local_x)-width/2),max(0,abs(local_y)-depth/2))-CHARACTER_RADIUS>1+1e-9:
            return None
        geometry = CombatService._movement_wall_geometry(location,[o for o in objects if o.id!=object_item.id])
        geometry.add_characters(c for c in CombatService._unique_location_characters(
            LocationCharacter.query.filter_by(location_id=location.id).all()) if c.character_id!=actor_character.character_id)
        blocked_tiles, climb_cost_tiles = CombatService._build_movement_map(location, actor_character.character_id,floor_level=level,
            wall_geometry=CombatService._movement_wall_geometry(location,objects),continuous=True)
        actor_x = actor_character.pos_x
        actor_y = actor_character.pos_y
        facing_x=CombatService._coerce_float(actor_character.facing_x,0)
        facing_y=CombatService._coerce_float(actor_character.facing_y,1)
        length=math.hypot(facing_x,facing_y)
        if length<1e-9:facing_x,facing_y,length=0,1,1
        facing_x,facing_y=facing_x/length,facing_y/length
        ray_length=math.hypot(cx-start_world[0],cy-start_world[1])+math.hypot(width,depth)+2
        far=(start_world[0]+facing_x*ray_length,start_world[1]+facing_y*ray_length)
        if barrier.segment_entry(start_world,far) is None:
            return None
        exit_distance=ray_length*(1-barrier.segment_entry(far,start_world))
        clearance=exit_distance+CHARACTER_RADIUS+WALL_CLEARANCE+1e-8
        ideal=(actor_x+facing_x*clearance,actor_y+facing_y*clearance)
        directions=((1,0),(1,1),(0,1),(-1,1),(-1,0),(-1,-1),(0,-1),(1,-1))
        index=math.floor(math.atan2(facing_y,facing_x)/(math.pi/4)+.5)%8
        dx,dy=directions[index]
        preferred=(math.floor(ideal[0]+.5),math.floor(ideal[1]+.5))
        # Округление точки выхода может оставить центр внутри тонкой стены.
        # Сдвигаемся лишь за выбранное препятствие, не ищем дальше занятых клеток.
        for _ in range(2):
            p=(preferred[0]+.5,preferred[1]+.5)
            if not barrier.intersects_sweep(p,p):break
            preferred=(preferred[0]+dx,preferred[1]+dy)
        anchor=(preferred[0]-dx,preferred[1]-dy)
        sides=[(anchor[0]+sx,anchor[1]+sy) for sx,sy in (directions[(index-1)%8],directions[(index+1)%8])]
        sides.sort(key=lambda tile:((tile[0]-ideal[0])**2+(tile[1]-ideal[1])**2,tile))
        candidate_tiles=[preferred,*sides]
        anomalies=CombatService._anomaly_candidates(location)
        for tx, ty in candidate_tiles:
            if not (0<=tx<location.grid_width and 0<=ty<location.grid_height):
                continue
            if tx == actor_x and ty == actor_y:
                continue
            end_world=(tx+.5,ty+.5)
            if barrier.intersects_sweep(end_world,end_world) or barrier.segment_entry(start_world,end_world) is None:
                continue
            if (tx, ty) in blocked_tiles:
                continue
            if climb_cost_tiles.get((tx, ty), 0) > 0:
                continue
            start,end = (actor_x,actor_y,level),(tx,ty,level)
            landing = surface(location,objects,*end)
            if not landing or abs(landing['height']-origin['height'])>=1 or (not landing.get('stair') and opening_at(objects,level,tx+.5,ty+.5)):
                continue
            if CombatService._anomalies_at_tile(location,tx,ty,candidates=anomalies,floor_level=level):
                continue
            if not geometry.can_traverse(start,end) or geometry.climb_edge_events(start,end):
                continue
            return tx, ty
        return None

    @staticmethod
    def _vertical_movement_plan(location,actor,x,y,level):
        """Явный подъём или спуск между соседними опорами; обычный путь их не заменяет."""
        from app.services.location_navigation import surface,opening_at
        if math.hypot(x-actor.pos_x,y-actor.pos_y)>1.5+1e-9:
            return None
        objects = LocationObject.query.filter_by(location_id=location.id).all()
        start = (actor.pos_x,actor.pos_y,actor.floor_level or 0)
        end = (x,y,level)
        a,b = surface(location,objects,*start),surface(location,objects,*end)
        if not a or not b or a['stair'] or b['stair']:
            return None
        a = {**a,'height':CombatService.physical_position(actor)['height']}
        if start[2]!=level:
            upper = max(start[2],level)
            crossing = (x,y) if start[2]>level else (actor.pos_x,actor.pos_y)
            if surface(location,objects,*crossing,upper) and not opening_at(objects,upper,crossing[0]+.5,crossing[1]+.5):
                return None
            if any(surface(location,objects,*crossing,middle) and not opening_at(objects,middle,crossing[0]+.5,crossing[1]+.5)
                   for middle in range(min(start[2],level)+1,upper)):
                return None
        elif surface(location,objects,(start[0]+x)/2,(start[1]+y)/2,level) is None:
            return None
        delta = b['height']-a['height']
        if not (1<=delta<2 or delta<=-1):
            return None
        geometry = CombatService._movement_wall_geometry(location,objects)
        geometry.add_characters(c for c in CombatService._unique_location_characters(
            LocationCharacter.query.filter_by(location_id=location.id).all()) if c.character_id!=actor.character_id)
        if not geometry.can_traverse(start,end) or geometry.climb_edge_events(start,end) or geometry.climb_at(end):
            return None
        distance = math.hypot(x-actor.pos_x,y-actor.pos_y) or abs(delta)
        climbs = [{'base_cost':12,'category':'high'}] if delta>0 else []
        return {'path':[(actor.pos_x,actor.pos_y),(x,y)],'distance':distance,
                'cost':12 if delta>0 else 0,'climb_cost':12 if delta>0 else 0,'climbs':climbs,
                'special_action':'climb_up' if delta>0 else 'descend','floor_level':level,
                'height_change':delta,'surfaces':[{k:n[k] for k in ('x','y','level','height')} for n in (a,b)]}

    @staticmethod
    def _object_climb_plan(location,actor,obj):
        from app.services.location_navigation import surface,level_of
        profile = CombatService._object_movement_profile(obj)
        if obj.type in {'stairs','ladder'} or level_of(obj)!=(actor.floor_level or 0) or profile['blocked'] or profile['climb_cost']<=0:
            return None
        landing = CombatService._find_climb_landing(location,obj,actor)
        if not landing:
            return None
        objects = LocationObject.query.filter_by(location_id=location.id).all()
        a,b = (surface(location,objects,x,y,actor.floor_level or 0)
               for x,y in ((actor.pos_x,actor.pos_y),landing))
        top = max(a['height'],b['height'])+CombatService._object_height(obj)+max(0,CombatService._coerce_float((obj.properties or {}).get('elevation'),0))
        return {'path':[(actor.pos_x,actor.pos_y),landing],'distance':math.hypot(landing[0]-actor.pos_x,landing[1]-actor.pos_y),
                'cost':profile['climb_cost'],'climb_cost':profile['climb_cost'],'climbs':[CombatService._object_climb_event(obj)],
                'special_action':'climb','object_id':obj.id,'floor_level':actor.floor_level or 0,
                'surfaces':[{k:a[k] for k in ('x','y','level','height')},
                            {'x':a['x'],'y':a['y'],'level':a['level'],'height':top},
                            {'x':b['x'],'y':b['y'],'level':b['level'],'height':top},
                            {k:b[k] for k in ('x','y','level','height')}]}

    @staticmethod
    def _validate_special_movement_distance(actor,location,state,distance):
        if not state or state.status!='active':
            return
        mode = actor.movement_mode_this_turn if actor.movement_mode_this_turn not in {None,'correction'} else 'walk'
        from app.services.slippery_movement import saved_state
        limit_mode = saved_state(actor).get('forced_from', mode) if mode == 'walk' else mode
        maximum = CombatService._movement_distance_limit(limit_mode,CombatService._posture_key(actor),
            CombatService._movement_evasion_active(actor),CombatService._movement_penalty(actor),actor.character.data)
        if (actor.movement_distance_this_turn or 0)+distance>maximum+1e-9:
            raise ValidationError('Превышен общий лимит перемещения за ход')

    @staticmethod
    def _pay_climb(actor,profile,payment,state):
        if not state or state.status!='active':
            return 0
        if not isinstance(payment,str) or payment not in {'movement','action'}:
            raise ValidationError('Выберите оплату перелезания: ОП или ОД')
        field = 'movement_points_current' if payment=='movement' else 'action_points_current'
        cost = profile['movement_points' if payment=='movement' else 'action_points']
        if getattr(actor,field)+1e-9<cost:
            raise ValidationError('Не хватает ОП' if payment=='movement' else 'Не хватает ОД')
        setattr(actor,field,max(0,round(getattr(actor,field)-cost,9)))
        return cost if payment=='movement' else 0

    @staticmethod
    def _movement_surface_costs(location, objects=(), ignores_terrain=False):
        from app.services.location_navigation import level_of
        if ignores_terrain:
            return {}
        vegetation = {f'nature_{species}_{state}' for species in ('bush', 'tall_grass')
                      for state in ('green', 'dry', 'dead')}
        costs, all_objects = {}, list(objects)
        for y, row in enumerate(getattr(location, 'tiles_data', None) or []):
            if not isinstance(row, list):
                continue
            for x, tile in enumerate(row):
                if not isinstance(tile, dict):
                    continue
                if tile.get('terrain') in {'snow', 'sand', 'swamp', 'ice'}:
                    costs[x, y, 0] = 2
                all_objects.extend({**o, 'tile_x': x, 'tile_y': y}
                                   for o in tile.get('objects') or [] if isinstance(o, dict))
        entries = [(o, o.get('type') if isinstance(o, dict) else o.type,
                    (o.get('properties') if isinstance(o, dict) else o.properties) or {})
                   for o in all_objects]
        # Пол использует собственную поверхность, не грунт под ним.
        for obj, kind, properties in entries:
            if kind == 'floor':
                for x, y in CombatService._object_footprint_tiles(obj):
                    costs.pop((x, y, level_of(obj)), None)
        for obj, kind, properties in entries:
            if kind == 'bush' or properties.get('visual_asset') in vegetation:
                for x, y in CombatService._object_footprint_tiles(obj):
                    costs[x, y, level_of(obj)] = 2
        return costs

    @staticmethod
    def _find_movement_path(
        location,
        start_x,
        start_y,
        end_x,
        end_y,
        moving_character_id=None,
        ignored_character_ids=None,
        companion_offset=None,
        start_level=None,
        end_level=None,
        movement_mode=None,
        _search_context=None,
    ):
        from app.services.location_navigation import find_path
        from flask import has_app_context
        actor = LocationCharacter.query.filter_by(location_id=location.id, character_id=moving_character_id).first() if moving_character_id is not None and has_app_context() else None
        if actor:
            from app.services.body_transport import companion
            body = companion(actor)
            if body:
                ignored_character_ids = [*(ignored_character_ids or []), actor.character_id, body.character_id]
        if actor and getattr(actor, 'grapple_target_id', None) and companion_offset is None:
            captive = LocationCharacter.query.filter_by(id=actor.grapple_target_id, location_id=location.id).first()
            if captive:
                companion_offset = (captive.pos_x - actor.pos_x, captive.pos_y - actor.pos_y)
                ignored_character_ids = [*(ignored_character_ids or []), actor.character_id, captive.character_id]
        start_level = (getattr(actor, 'floor_level', 0) or 0) if start_level is None else start_level
        end_level = start_level if end_level is None else end_level
        direction_mode = movement_mode if actor else None
        facing_x = CombatService._coerce_float(getattr(actor, 'facing_x', 0), 0)
        facing_y = CombatService._coerce_float(getattr(actor, 'facing_y', 1), 1)
        if math.hypot(facing_x,facing_y)<=1e-9:
            facing_x,facing_y = 0,1
        if direction_mode == 'walk':
            used_mode = actor.movement_mode_this_turn
            if used_mode in {'walk','backward_sideways'}:
                direction_mode = used_mode
            elif CombatService._movement_forward_step(facing_x,facing_y,end_x-start_x,end_y-start_y) is False:
                direction_mode = 'backward_sideways'
        if direction_mode in {'walk','run','sprint'} and CombatService._movement_forward_step(facing_x,facing_y,end_x-start_x,end_y-start_y) is False:
            return None
        # Несколько целей одного поиска используют одну геометрию. Контекст
        # живёт только в вызвавшем поиске и не переносится между действиями.
        context={} if _search_context is None else _search_context
        if 'objects' not in context:
            context['objects']=LocationObject.query.filter_by(location_id=location.id).all() if has_app_context() else []
            context['geometry']=CombatService._movement_wall_geometry(location,context['objects'])
            ignores_terrain=bool(actor and actor.character and CombatService._mutant_has_trait(actor.character.data or {},'Полёт'))
            context['terrain_costs']=CombatService._movement_surface_costs(location,context['objects'],ignores_terrain=ignores_terrain)
            ignored=set(ignored_character_ids or [])|{moving_character_id}
            if has_app_context():context['geometry'].add_characters(c for c in CombatService._unique_location_characters(LocationCharacter.query.filter_by(location_id=location.id).all()) if c.character_id not in ignored)
        objects=context['objects'];wall_geometry=context['geometry'];terrain_costs=context['terrain_costs']
        maps=context.setdefault('maps',{})
        def movement_map(level):
            if level not in maps:
                maps[level] = CombatService._build_movement_map(
                    location,moving_character_id,ignored_character_ids,floor_level=level,
                    wall_geometry=wall_geometry,continuous=True,
                )
            return maps[level]
        def can_traverse(a,b):
            if direction_mode in {'walk','run','sprint','backward_sideways'}:
                step = CombatService._movement_forward_step(facing_x,facing_y,b[0]-a[0],b[1]-a[1])
                if step is not None and step != (direction_mode != 'backward_sideways'):
                    return False
                # Из переднего сектора нельзя вернуться к цели за его границей.
                if direction_mode != 'backward_sideways' and CombatService._movement_forward_step(facing_x,facing_y,end_x-b[0],end_y-b[1]) is False:
                    return False
            if not wall_geometry.can_traverse(a,b):
                return False
            if companion_offset:
                a = (a[0]+companion_offset[0],a[1]+companion_offset[1],a[2])
                b = (b[0]+companion_offset[0],b[1]+companion_offset[1],b[2])
                if not all(0<=point[0]<location.grid_width and 0<=point[1]<location.grid_height for point in (a,b)):
                    return False
                if not wall_geometry.can_traverse(a,b):
                    return False
                blocked,costs = movement_map(b[2])
                cell = (math.floor(b[0]+.5),math.floor(b[1]+.5))
                if cell in blocked or costs.get(cell,0):
                    return False
                from app.services.location_navigation import surface
                if not surface(location,objects,*b) or wall_geometry.climb_at(b):
                    return False
            return True
        support = (((actor.character.data or {}).get('health') or {}).get('combatMeta') or {}).get('jumpSupport') if actor else None
        height_options = ({'start_height':CombatService.physical_position(actor)['height']}
                          if support and support.get('position')==[start_x,start_y,start_level] else {})
        result = find_path(location,objects,(start_x,start_y,start_level),(end_x,end_y,end_level),
                         movement_map,terrain_costs=terrain_costs,can_traverse=can_traverse,
                         geometry=wall_geometry,continuous=True,
                         directional=direction_mode in {'walk','run','sprint','backward_sideways'},**height_options)
        if result:
            from app.services.slippery_movement import slippery_tiles
            ice = slippery_tiles(location, objects, ignores_terrain)
            result['slippery'] = any(tuple(part['cell']) in ice for part in result.get('movement_segments', []))
            result['slippery_start'] = (math.floor(start_x+.5), math.floor(start_y+.5), start_level) in ice
        return result

    @staticmethod
    def _update_helper_penalties(state):
        for actor in LocationCharacter.query.filter(LocationCharacter.id.in_(state.turn_order or [])).all():
            data=actor.character.data or {}
            if not background_is(data,'Помощник'):continue
            health=data.setdefault('health',{});meta=health.setdefault('combatMeta',{})
            health['effects']=[e for e in normalize_effect_list(health.get('effects') or []) if e.get('source')!='background_helper_inactive']
            if (state.round_number or 1)-CombatService._coerce_int(meta.get('lastPaidHelpRound'),0)>3:
                from app.services.character_creation import _catalog
                modifiers={p:3 for g,items in _catalog()['skills'].items() for k in items for p in (f'{g}.{k}',f'skills.{g}.{k}')}
                apply_effect_to_health(health,{'type':'custom','name':'Помощник: давно не помогал','source':'background_helper_inactive',
                    'tick':'manual','scope':'combat','modifiers':modifiers})
            actor.character.data=data;flag_modified(actor.character,'data')
            CombatService._sync_location_effects_from_character(actor)

    @staticmethod
    def _prepare_character_for_turn(loc_char):
        if getattr(loc_char, 'character', None) and isinstance(loc_char.character.data, dict):
            cancel_unavailable_actions(loc_char)
        if getattr(loc_char, 'character', None) and isinstance(loc_char.character.data, dict):
            turn_data = loc_char.character.data
            turn_meta = turn_data.setdefault('health', {}).setdefault('combatMeta', {})
            skipped = max(0, CombatService._coerce_int(turn_meta.get('pepperSkipTurns'), 0))
            turn_meta['pepperSkipTurnActive'] = skipped > 0
            if skipped:
                turn_meta['pepperSkipTurns'] = skipped - 1
            loc_char.character.data = turn_data
            flag_modified(loc_char.character, 'data')
            if CombatService._mutant_has_trait(turn_data, 'Возможность разгона'):
                turn_meta = turn_data.setdefault('health', {}).setdefault('combatMeta', {})
                previous = turn_meta.get('mutantTurnActivity')
                if isinstance(previous, dict) and previous.get('moved') and not previous.get('other'):
                    turn_meta['mutantAccelerationActive'] = True
                state = LocationCombatState.query.filter_by(location_id=loc_char.location_id).first()
                turn_meta['mutantTurnActivity'] = {
                    'round': max(1, getattr(state, 'round_number', 1) or 1),
                    'moved': False,
                    'other': False,
                }
                loc_char.character.data = turn_data
                flag_modified(loc_char.character, 'data')
        profile = CombatService._combat_profile(loc_char)
        turn_data = loc_char.character.data if getattr(loc_char,'character',None) else {}
        first_round_bonus = character_passives(turn_data)['first_rounds_ap']
        state = (LocationCombatState.query.filter_by(location_id=loc_char.location_id).first()
                 if getattr(loc_char,'location_id',None) is not None
                 and (first_round_bonus or background_is(turn_data,'Математик')) else None)
        if state and state.status=='active' and (state.round_number or 1)<=3:
            profile['action_points']+=first_round_bonus
            token=state.started_at.replace(tzinfo=None).isoformat() if state.started_at else None
            first_meta=(loc_char.character.data.get('health') or {}).get('combatMeta') or {}
            first_token=first_meta.get('firstCombatTurnToken')
            if background_is(loc_char.character.data,'Математик') and token and first_token==token:
                profile['action_points']+=2
        condition = CombatService._location_character_condition(loc_char)
        loc_char.initiative_bonus = profile['initiative_bonus']
        loc_char.action_points_max = profile['action_points']
        loc_char.action_points_current = profile['action_points'] if condition['can_act'] else 0
        loc_char.free_actions_max = profile['free_actions']
        loc_char.free_actions_current = profile['free_actions'] if condition['can_act'] else 0
        if ((turn_data.get('health') or {}).get('combatMeta') or {}).get('pepperSkipTurnActive'):
            loc_char.action_points_current = 0
            loc_char.free_actions_current = 0
        loc_char.movement_points_max = 0
        loc_char.movement_points_current = 0
        loc_char.movement_mode_this_turn = None
        loc_char.movement_distance_this_turn = 0
        loc_char.correction_distance_this_turn = 0
        loc_char.melee_block_round = None
        loc_char.melee_block_effectiveness = 0
        loc_char.melee_swing_round = None
        if getattr(loc_char, 'character', None) and isinstance(loc_char.character.data, dict):
            data = loc_char.character.data
            health = data.get('health') if isinstance(data.get('health'), dict) else {}
            meta = health.setdefault('combatMeta', {})
            meta['consumableUsage'] = {}
            meta.pop('movementEvasion', None)
            meta.pop('slipperyMovement', None)
            meta.pop('fencerUsed',None)
            for key in ('standaloneEvasion','breakthroughProgress','breakthroughProgressOP','breakthroughMovementImpossible','breakthroughWillPassed','breakthroughWillFailed','grenadirCheck','grenadirMustExplode'):
                meta.pop(key,None)
            from app.services.character_rules import start_turn
            start_turn(loc_char)
            from app.services.grenade_reaction import begin,window
            loc_char._grenade_detonations=begin(loc_char)
            if not window(loc_char):
                CombatService._pay_turn_obligations(loc_char)
            flag_modified(loc_char.character,'data')
        return loc_char

    @staticmethod
    def _pay_turn_obligations(loc_char):
        """После окна гранаты оплачиваются те же долги, что в обычном начале хода."""
        if getattr(loc_char,'character',None) and isinstance(loc_char.character.data,dict):
            data=loc_char.character.data
            health=data.setdefault('health',{})
            meta=health.setdefault('combatMeta',{})
            # A reserve is valid only until the character receives their next regular turn.
            reaction_reserve = meta.get('reactionReserve')
            deferred_help_cost = 0
            deferred_help_ready = False
            if isinstance(reaction_reserve, dict) and reaction_reserve.get('kind') == 'help':
                deferred_help_cost = max(
                    0,
                    CombatService._coerce_int(
                        reaction_reserve.get('deferred_action_points'), 0,
                    ),
                )
                deferred_help_ready = bool(reaction_reserve.get('deferred_help_ready'))
                if deferred_help_cost:
                    paid = min(loc_char.action_points_current, deferred_help_cost)
                    loc_char.action_points_current -= paid
                    deferred_help_cost -= paid
                    reaction_reserve['deferred_action_points'] = deferred_help_cost
                    if not deferred_help_cost:
                        reaction_reserve['deferred_help_ready'] = True
                        state=LocationCombatState.query.filter_by(location_id=loc_char.location_id).first()
                        if state and state.status=='active':meta['lastPaidHelpRound']=state.round_number
                        health['effects']=[e for e in normalize_effect_list(health.get('effects') or [])
                                           if e.get('source')!='background_helper_inactive']
            if (
                not isinstance(reaction_reserve, dict)
                or reaction_reserve.get('kind') != 'help'
                or (not deferred_help_cost and deferred_help_ready)
                or (not deferred_help_cost and not deferred_help_ready
                    and not reaction_reserve.get('deferred_help_ready'))
            ):
                # A fully paid reserve expires when its owner receives a new regular turn.
                meta.pop('reactionReserve', None)
            meta.pop('reactionActive', None)
            pending_action = meta.get('pendingAction')
            if isinstance(pending_action, dict):
                remaining_cost = max(
                    0,
                    CombatService._coerce_int(pending_action.get('remaining_action_points'), 0),
                )
                paid = min(loc_char.action_points_current, remaining_cost)
                loc_char.action_points_current -= paid
                remaining_cost -= paid
                pending_action['remaining_action_points'] = remaining_cost
                update_action_payment(loc_char, pending_action.get('id'), remaining_cost)
                if remaining_cost <= 0:
                    meta.pop('pendingAction', None)
                    meta['completedPendingActionId'] = pending_action.get('id')
            data['health'] = health
            loc_char.character.data = data
            flag_modified(loc_char.character, 'data')
        return loc_char

    @staticmethod
    def _serialize_character(loc_char, current_turn_id=None, combat_state=None, *, scene_objects=None, light_actors=None):
        from app.services.character_rules import allied_ids
        from app.services.character_jump import balance_state
        from app.services.weapon_grip import hands as grip_hands,secondary_index
        from app.services.portable_lights import ready_sources,illumination
        character = loc_char.character
        profile = CombatService._combat_profile(loc_char)
        posture = CombatService._posture_key(loc_char)
        posture_profile = CombatService._movement_posture_profile(posture,character.data if character else {})
        posture_change_options = {}
        for target_posture in POSTURES:
            if target_posture == posture:
                continue
            posture_change_options[target_posture] = CombatService._posture_change_options(
                loc_char,
                target_posture,
            )
        data = character.data if character and isinstance(character.data, dict) else {}
        weapons = data.get('weapons') if isinstance(data.get('weapons'), list) else []
        weapon_ergonomics = [
            CombatService._weapon_ergonomics_profile(loc_char, weapon, index)
            for index, weapon in enumerate(weapons)
        ]
        health = data.get('health') if isinstance(data, dict) else {}
        if not isinstance(health, dict):
            health = {}
        bleeding_profile = CombatService._bleeding_check_profile(data,loc_char) if background_is(data,'Стойкий') else CombatService._bleeding_check_profile(data)
        condition = CombatService._character_condition(data)
        reaction_reserve = (
            health.get('combatMeta', {}).get('reactionReserve')
            if isinstance(health.get('combatMeta'), dict)
            else None
        )
        opportunity_attacks = (
            health.get('combatMeta', {}).get('opportunityAttacks')
            if isinstance(health.get('combatMeta'), dict)
            else None
        )
        if isinstance(opportunity_attacks, list) and combat_state is not None:
            opportunity_attacks = [
                item for item in opportunity_attacks
                if isinstance(item, dict)
                and CombatService._coerce_int(item.get('round'), 0)
                == max(1, combat_state.round_number or 1)
            ]
        help_advantage = (
            health.get('combatMeta', {}).get('helpAdvantage')
            if isinstance(health.get('combatMeta'), dict)
            else None
        )
        must_do_retry = (
            health.get('combatMeta', {}).get('mustDoRetry')
            if isinstance(health.get('combatMeta'), dict)
            else None
        )
        if isinstance(must_do_retry, dict):
            must_do_retry = deepcopy(must_do_retry)
            must_do_retry.pop('attack_details', None)
            must_do_retry.pop('medical_retry', None)
        is_mutant = CombatService._is_mutant_character(data)
        if is_mutant:
            must_do_retry = None
        must_do_usage = None
        if (
            not is_mutant
            and combat_state is not None
            and combat_state.status == 'active'
        ):
            usage_profile = CombatService._must_do_usage_profile(
                data, combat_state, initialize=False,
            )
            must_do_usage = {
                'will_bonus': usage_profile['will_bonus'],
                'limit': usage_profile['limit'],
                'used': usage_profile['used'],
                'remaining': usage_profile['remaining'],
            }
        stress_effects = [
            effect for effect in normalize_effect_list(health.get('effects') or [])
            if effect.get('active', True) and effect.get('type') in {'stress_effect', 'stress_stupor', 'phobia'}
        ]
        from app.services.sniper_trauma import choices as sniper_choices
        from app.services.sniper_position import status as sniper_position
        return {
            'sniper_position':sniper_position(loc_char,combat_state) if (health.get('combatMeta') or {}).get('sniperOldCover') else None,
            'location_character_id': loc_char.id,
            'character_id': character.id if character else None,
            'name': CombatService._display_name(character),
            'sniper_trauma_choices':sniper_choices(data),
            'is_mutant': is_mutant,
            'melee_swing_action_points':0 if character_passives(data)['free_swing'] else 1,
            'melee_block_discount':character_passives(data)['block_discount'],
            'ignores_difficult_surface': CombatService._mutant_has_trait(data, 'Полёт'),
            'jump_preview': None if is_mutant else CombatService._jump_profile(data, loc_char.movement_mode_this_turn or 'walk', loc_char.action_points_current or 0),
            'unstable_jump': deepcopy(balance_state(loc_char)),
            'mutant_type': CombatService._mutant_profile_name(data) if is_mutant else '',
            'is_npc': is_npc(data),
            'gm_hidden': bool(loc_char.gm_hidden),
            'camouflaged': bool(is_mutant and combat_state and combat_state.status == 'active'
                               and CombatService._mutant_camouflage_active(data, combat_state.round_number)),
            'owner_id': character.owner_id if character else None,
            'owner_username': character.owner.username if character and character.owner else None,
            'controlled_by': loc_char.controlled_by,
            'team_name': loc_char.team_name,
            'team_color': loc_char.team_color,
            'x': loc_char.pos_x,
            'y': loc_char.pos_y,
            **CombatService.physical_position(loc_char,objects=scene_objects),
            'status': loc_char.status,
            'initiative_bonus': loc_char.initiative_bonus or 0,
            'initiative_roll': loc_char.initiative_roll,
            'initiative_total': loc_char.initiative_total,
            'action_points_max': loc_char.action_points_max if loc_char.action_points_max is not None else DEFAULT_ACTION_POINTS,
            'action_points_current': loc_char.action_points_current or 0,
            'free_actions_max': loc_char.free_actions_max if loc_char.free_actions_max is not None else DEFAULT_FREE_ACTIONS,
            'free_actions_current': loc_char.free_actions_current or 0,
            'movement_points_max': loc_char.movement_points_max if loc_char.movement_points_max is not None else profile['movement_points'],
            'movement_points_current': loc_char.movement_points_current or 0,
            'movement_penalty': profile['movement_penalty'],
            'climb_agility_bonus': CombatService._skill_modifier(data,'skills.physical.agility'),
            'pending_action': (
                health.get('combatMeta', {}).get('pendingAction')
                if isinstance(health.get('combatMeta'), dict)
                else None
            ),
            'reaction_reserve': reaction_reserve if isinstance(reaction_reserve, dict) else None,
            'opportunity_attacks': (
                deepcopy(opportunity_attacks) if isinstance(opportunity_attacks, list) else []
            ),
            'help_advantage': help_advantage if isinstance(help_advantage, dict) else None,
            'must_do_retry': must_do_retry if isinstance(must_do_retry, dict) else None,
            'must_do_usage': must_do_usage,
            'gunpoint': (
                deepcopy(health.get('combatMeta', {}).get('gunpoint'))
                if isinstance(health.get('combatMeta'), dict)
                and isinstance(health.get('combatMeta', {}).get('gunpoint'), dict)
                else None
            ),
            'active_anomaly': (
                deepcopy(health.get('combatMeta', {}).get('activeAnomaly'))
                if isinstance(health.get('combatMeta'), dict)
                and isinstance(health.get('combatMeta', {}).get('activeAnomaly'), dict)
                else None
            ),
            'stress_effects': stress_effects,
            'deferred_action': serialize_deferred_action(loc_char, health),
            'completed_pending_action_id': (
                health.get('combatMeta', {}).get('completedPendingActionId')
                if isinstance(health.get('combatMeta'), dict)
                else None
            ),
            'trait_state':{key:deepcopy((health.get('combatMeta') or {}).get(key)) for key in ('firefighterNearby','standaloneEvasion','grenadirCheck','grenadirMustExplode','sniperOldCover','sniperPositionCheck','breakthroughCheck','breakthroughProgress','hunterStressDebt')},
            'ally_character_ids':sorted(allied_ids(loc_char)) if background_is(data,'Бригадир') else [],
            'movement_gain': profile['movement_gain'],
            'is_exoskeleton': CombatService._exoskeleton_power_profile(data)['is_exoskeleton'],
            'powered_exoskeleton': CombatService._exoskeleton_power_profile(data)['powered'],
            'exoskeleton_can_run': CombatService._exoskeleton_power_profile(data)['can_run'],
            'movement_mode_this_turn': loc_char.movement_mode_this_turn,
            'slippery_movement': health.get('combatMeta', {}).get('slipperyMovement', {}) if loc_char.movement_mode_this_turn else {},
            'grenade_reaction':deepcopy(health.get('combatMeta',{}).get('grenadeReaction')),
            'water_in_hand':deepcopy(health.get('combatMeta',{}).get('heldWaterItem')),
            'movement_evasion': CombatService._movement_evasion_active(loc_char),
            'movement_distance_this_turn': loc_char.movement_distance_this_turn or 0,
            'correction_distance_this_turn': loc_char.correction_distance_this_turn or 0,
            'strenuous_movement_blocked_until_round': loc_char.strenuous_movement_blocked_until_round or 0,
            'posture': posture,
            'posture_label': posture_profile['label'],
            'posture_change_options': posture_change_options,
            'posture_modifiers': {
                'shooting_bonus': posture_profile['shooting_bonus'],
                'ergonomics_bonus': 0 if background_is(data,'Пост принял') else posture_profile['ergonomics_bonus'],
                'stealth_bonus': posture_profile['stealth_bonus'],
                'movement_multiplier': posture_profile['movement_multiplier'],
                'walk_max_distance': posture_profile['walk_max_distance'],
                'can_run': posture_profile['can_run'],
                'can_sprint': (
                    posture_profile['can_sprint']
                    and not CombatService._disabled_limb_penalties(data)['sprint_blocked']
                ),
                'can_correction': posture_profile['can_correction'],
                'can_use_low_cover': posture_profile['can_use_low_cover'],
            },
            'movement_rules':{'sprinter':background_is(data,'Спринтер'),'goose':bool(trait_count(data,'Гусь и гусеничка'))},
            'weapon_ergonomics': weapon_ergonomics,
            'drawn_weapon_index': loc_char.drawn_weapon_index,
            'weapon_grip_hands': grip_hands(data),
            'secondary_weapon_index': secondary_index(loc_char),
            'portable_lights': ready_sources(loc_char,objects=scene_objects),
            'portable_illumination': illumination(loc_char,actors=light_actors,objects=scene_objects),
            'aimed_target_character_id': loc_char.aimed_target_character_id,
            'aimed_weapon_index': loc_char.aimed_weapon_index,
            'aim_accuracy_bonus': max(0, CombatService._coerce_int(loc_char.aim_accuracy_bonus, 0)),
            'cover_object_id': loc_char.cover_object_id,
            'weapon_braced': bool(loc_char.weapon_braced),
            'braced_weapon_index': loc_char.braced_weapon_index,
            'facing_x': CombatService._coerce_int(loc_char.facing_x, 0),
            'facing_y': CombatService._coerce_int(loc_char.facing_y, 1),
            'facing_changed_round': loc_char.facing_changed_round,
            'agility_bonus': CombatService._base_skill_modifier(data, 'skills.physical.agility'),
            'melee_swing_round': loc_char.melee_swing_round,
            'fencer_available':background_is(data,'Фехтовальщик') and not (health.get('combatMeta') or {}).get('fencerUsed'),
            'melee_block_round': loc_char.melee_block_round,
            'melee_block_effectiveness': loc_char.melee_block_effectiveness or 0,
            'grapple_target_id': loc_char.grapple_target_id,
            'grappled_by_id': loc_char.grappled_by_id,
            'grapple_strengthened': bool(loc_char.grapple_strengthened),
            'grapple_choke_rounds': loc_char.grapple_choke_rounds or 0,
            'grapple_live_shield': bool(loc_char.grapple_live_shield),
            'body_transport': ((health.get('combatMeta') or {}).get('bodyTransport') or {}),
            'transported_by': (health.get('combatMeta') or {}).get('transportedBy'),
            'body_transport_methods': {'shoulder': bool(trait_count(data, 'Санитар'))},
            'hp_zones': loc_char.hp_zones,
            'effects': loc_char.effects,
            'pain_level': CombatService._coerce_int(health.get('painLevel', 0), 0),
            'exhaustion': CombatService._coerce_int(health.get('exhaustion', 0), 0),
            'stress': CombatService._coerce_int(health.get('stress', 0), 0),
            'radiation': CombatService._coerce_int(health.get('radiation', 0), 0),
            'blood': health.get('blood') or health.get('bloodStage') or 'normal',
            'blood_stage': health.get('bloodStage') or health.get('blood') or 'normal',
            'bleeding_severity': CombatService._coerce_int(health.get('bleedingSeverity', 0), 0),
            'bleeding_difficulty': bleeding_profile['difficulty'],
            'bleeding_modifier_total': CombatService._coerce_int(health.get('bleedingModifierTotal', 0), 0),
            'will_bonus': bleeding_profile['willBonus'],
            'bleeding': health.get('bleeding', {}),
            'condition': condition,
            'is_current_turn': loc_char.id == current_turn_id,
        }

    @staticmethod
    def _can_end_turn_for_character(loc_char, user_id, is_gm=False):
        if is_gm:
            return True
        character = getattr(loc_char, 'character', None)
        data = character.data if character and isinstance(character.data, dict) else {}
        meta = data.get('health', {}).get('combatMeta', {})
        zombification_controller = CombatService._coerce_int(
            meta.get('zombificationController') if isinstance(meta, dict) else None,
            0,
        )
        if zombification_controller:
            return zombification_controller == user_id
        if character and character.owner_id == user_id:
            return True
        return loc_char.controlled_by == user_id

    @staticmethod
    def _validate_incapacitated_interaction(
        location_id,
        user_id,
        actor_location_character_id,
        target_character_id,
    ):
        location = CombatService._get_location(location_id)
        is_gm = CombatService._ensure_access(location, user_id)
        actor = LocationCharacter.query.filter_by(
            id=actor_location_character_id,
            location_id=location_id,
        ).first()
        target = LocationCharacter.query.filter_by(
            character_id=target_character_id,
            location_id=location_id,
        ).first()
        if not actor or not target or actor.id == target.id:
            raise NotFoundError("Interaction target not found")
        if not CombatService._can_end_turn_for_character(actor, user_id, is_gm=is_gm):
            raise PermissionDenied("You do not control the acting character")
        CombatService.ensure_character_can_act(actor)
        state = LocationCombatState.query.filter_by(location_id=location_id).first()
        if (
            state
            and state.status == 'active'
            and state.current_location_character_id != actor.id
        ):
            raise PermissionDenied("It is not this character's turn")
        if not CombatService._is_adjacent(actor, target):
            raise ValidationError("Цель должна находиться на соседней клетке")
        condition = CombatService._location_character_condition(target)
        if condition['state'] == 'active':
            raise ValidationError("Only an incapacitated character can be searched or examined")
        return actor, target, condition

    @staticmethod
    def inspect_incapacitated_character(
        location_id,
        user_id,
        actor_location_character_id,
        target_character_id,
    ):
        actor, target, condition = CombatService._validate_incapacitated_interaction(
            location_id,
            user_id,
            actor_location_character_id,
            target_character_id,
        )
        return CombatService._incapacitated_character_snapshot(
            actor,
            target,
            condition,
        )

    @staticmethod
    def _incapacitated_character_snapshot(actor, target, condition=None):
        target_data = (
            deepcopy(target.character.data)
            if target.character and isinstance(target.character.data, dict)
            else {}
        )
        health = target_data.get('health') if isinstance(target_data.get('health'), dict) else {}
        condition = condition or CombatService._location_character_condition(target)
        return {
            'actor_character_id': actor.character_id,
            'target_character_id': target.character_id,
            'target_name': target.character.name if target.character else 'Персонаж',
            'condition': condition,
            'health': {
                'current': health.get('current'),
                'max': health.get('max'),
                'blood_stage': health.get('bloodStage') or health.get('blood') or 'normal',
                'pain_level': CombatService._coerce_int(health.get('painLevel'), 0),
                'effects': normalize_effect_list(health.get('effects') or []),
            },
            'target_data': target.character.data_snapshot() if target.character else {},
        }

    @staticmethod
    def _take_inventory_item(character_data, path, amount):
        if not isinstance(path, list) or not path or path[0] not in {'inventory', 'equipment'}:
            raise ValidationError("Invalid inventory path")
        current = character_data
        for key in path[:-1]:
            if isinstance(current, list):
                index = CombatService._coerce_int(key, -1)
                if index < 0 or index >= len(current):
                    raise NotFoundError("Item not found")
                current = current[index]
            elif isinstance(current, dict) and key in current:
                current = current[key]
            else:
                raise NotFoundError("Item not found")
        index = CombatService._coerce_int(path[-1], -1)
        if isinstance(current, dict):
            key = path[-1]
            if key not in current or not isinstance(current[key], dict):
                raise NotFoundError("Item not found")
            return current.pop(key)
        if not isinstance(current, list) or index < 0 or index >= len(current):
            raise NotFoundError("Item not found")
        item = current[index]
        if not isinstance(item, dict):
            return current.pop(index)
        from app.services.character_medicine import take_item as take_economy_medicine
        economy_item=take_economy_medicine(current,index,amount)
        if economy_item is not None:return economy_item
        quantity = max(1, CombatService._coerce_int(item.get('quantity'), 1))
        transfer_amount = max(1, min(CombatService._coerce_int(amount, 1), quantity))
        transferred = deepcopy(item)
        transferred['quantity'] = transfer_amount
        if transfer_amount == quantity:
            current.pop(index)
        else:
            item['quantity'] = quantity - transfer_amount
        return transferred

    @staticmethod
    def _is_stealable_electronic(item):
        if not isinstance(item, dict):
            return False
        name = str(item.get('name') or '').strip().casefold()
        return any(token in name for token in (
            'детектор аномал', 'рация', 'радио', 'пнв', 'кпк',
        ))

    @staticmethod
    def _inventory_item_at_path(character_data, path):
        if not isinstance(path, list) or not path or path[0] not in {'inventory', 'equipment'}:
            raise ValidationError("Invalid inventory path")
        current = character_data
        for key in path:
            if isinstance(current, list):
                index = CombatService._coerce_int(key, -1)
                if index < 0 or index >= len(current):
                    raise NotFoundError("Item not found")
                current = current[index]
            elif isinstance(current, dict) and key in current:
                current = current[key]
            else:
                raise NotFoundError("Item not found")
        if not isinstance(current, dict):
            raise NotFoundError("Item not found")
        return current

    @staticmethod
    def _butchering_species_key(character_data):
        name = CombatService._mutant_profile_name(character_data).casefold().replace('ё', 'е')
        for prefix in ('матерая ', 'матерый ', 'матерое '):
            if name.startswith(prefix):
                name = name[len(prefix):]
        return name.strip()

    @staticmethod
    def _butchering_weapon_profile(actor):
        data = actor.character.data if actor.character and isinstance(actor.character.data, dict) else {}
        weapons = data.get('weapons') if isinstance(data.get('weapons'), list) else []
        index = CombatService._coerce_int(actor.drawn_weapon_index, -1)
        if not 0 <= index < len(weapons) or not isinstance(weapons[index], dict):
            raise ValidationError('Для разделки нужно взять в руки острое оружие')
        weapon = weapons[index]
        template = CombatService._weapon_template(weapon)
        category = str((template.category if template else None) or weapon.get('category') or '').casefold()
        if category != 'melee_weapon':
            raise ValidationError('Для разделки нужно взять в руки острое оружие')
        attributes = CombatService._template_attributes(weapon)
        attacks = [
            str(value or '').casefold().replace('ё', 'е')
            for value in (attributes.get('allowed_attacks') or [])
        ]
        damage_type = str(attributes.get('melee_damage_type') or '').casefold().replace('ё', 'е')
        sharp_attacks = [value for value in attacks if 'дроб' not in value and 'нелет' not in value]
        if not sharp_attacks and ('дроб' in damage_type or not damage_type):
            raise ValidationError('Дробящим оружием мутанта разделать нельзя')
        penetration = max(
            [CombatService._weapon_damage_profile(weapon, attack)['armor_piercing'] for attack in sharp_attacks]
            or [CombatService._weapon_damage_profile(weapon)['armor_piercing']]
        )
        name = str((template.name if template else None) or weapon.get('name') or 'Оружие').strip()
        normalized_name = name.casefold().replace('ё', 'е')
        if 'охотнич' in normalized_name and 'нож' in normalized_name:
            bonus = 3
        elif 'штык' in normalized_name or 'кортик' in normalized_name:
            bonus = 1
        elif any(value in normalized_name for value in ('нож', 'топор', 'томагавк', 'меч')):
            bonus = 0
        else:
            bonus = -3
        has_hunter_toolkit = any(
            'набор инструментов охотника'
            in str(item.get('name') or '').casefold().replace('ё', 'е')
            for item, _path in CombatService._iter_inventory_items_with_paths(data)
        )
        if has_hunter_toolkit:
            bonus = 6
            name = f"{name} + набор инструментов охотника"
        return {'index': index, 'name': name, 'bonus': bonus, 'penetration': penetration}

    @staticmethod
    def butcher_mutant(location_id, user_id, actor_location_character_id, target_character_id, allocation=None):
        location = CombatService._get_location(location_id)
        CombatService._ensure_access(location, user_id)
        state = LocationCombatState.query.filter_by(location_id=location_id).first()
        if state and state.status == 'active':
            raise ValidationError('Разделывать мутанта можно только вне боя')
        actor = db.session.get(LocationCharacter, CombatService._coerce_int(actor_location_character_id, 0))
        target = LocationCharacter.query.filter_by(
            location_id=location_id,
            character_id=CombatService._coerce_int(target_character_id, 0),
        ).first()
        if not actor or actor.location_id != location_id or not actor.character:
            raise NotFoundError('Разделывающий персонаж не найден')
        if not target or not target.character:
            raise NotFoundError('Тело мутанта не найдено')
        can_control = bool(
            location.lobby.gm_id == user_id
            or actor.controlled_by == user_id
            or actor.character.owner_id == user_id
        )
        if not can_control:
            raise PermissionDenied('Вы не управляете этим персонажем')
        if max(abs(actor.pos_x - target.pos_x), abs(actor.pos_y - target.pos_y)) > 1:
            raise ValidationError('Для разделки нужно находиться рядом с телом')
        target_data = target.character.data if isinstance(target.character.data, dict) else {}
        if not CombatService._is_mutant_character(target_data):
            raise ValidationError('Разделывать по этим правилам можно только мутантов')
        if CombatService._location_character_condition(target)['state'] != 'dead':
            raise ValidationError('Разделывать можно только мёртвого мутанта')
        target_meta = target_data.setdefault('health', {}).setdefault('combatMeta', {})
        if target_meta.get('butchered'):
            raise ValidationError('Этот мутант уже разделан')
        weapon = CombatService._butchering_weapon_profile(actor)
        protection = CombatService._coerce_float(
            (target_data.get('mutant') or {}).get('physical_protection'), 0,
        )
        if weapon['penetration'] + 20 <= protection:
            raise ValidationError(
                f"Пробития недостаточно: {round(weapon['penetration'])}% + 20% не превышает защиту {round(protection)}%"
            )
        species = CombatService._butchering_species_key(target_data)
        if species not in CombatService.BUTCHERING_BASE_PRICES:
            raise ValidationError('Для этого вида мутанта в правилах не указана добыча разделки')
        pending = target_meta.get('butcheringRoll')
        if not isinstance(pending, dict):
            actor_data = actor.character.data if isinstance(actor.character.data, dict) else {}
            survival_bonus = CombatService._base_skill_modifier(actor_data, 'skills.other.survival')
            roll = random.randint(1, 20)
            mutant_modifier = CombatService.BUTCHERING_MUTANT_MODIFIERS.get(species, 0)
            from app.services.character_traits import butchering_bonus
            property_bonus=butchering_bonus(actor_data,species)
            from app.services.brigadier_order import boost_roll
            _rolls,roll,order_bonus=boost_roll(actor_data,[roll]);property_bonus+=order_bonus
            total = max(0, roll + mutant_modifier + weapon['bonus'] + survival_bonus+property_bonus - 5)
            pending = {
                'actor_location_character_id': actor.id,
                'roll': roll,
                'mutant_modifier': mutant_modifier,
                'weapon_bonus': weapon['bonus'],
                'survival_bonus': survival_bonus,
                'character_bonus':property_bonus,
                'total': total,
                'weapon_name': weapon['name'],
            }
            target_meta['butcheringRoll'] = pending
            target.character.data = target_data
            flag_modified(target.character, 'data')
            db.session.commit()
        elif CombatService._coerce_int(pending.get('actor_location_character_id'), 0) != actor.id:
            raise ValidationError('Разделку уже начал другой персонаж')

        base_price = CombatService.BUTCHERING_BASE_PRICES.get(species, 0)
        unavailable = {'meat', 'hide'} if species == 'одичалый' else set()
        parts = {
            key: {
                **profile,
                'available': key not in unavailable,
                'unit_price': math.floor(base_price * profile['price_multiplier']),
                'radiation': CombatService.BUTCHERING_MEAT_RADIATION.get(species, 0) if key == 'meat' else 0,
            }
            for key, profile in CombatService.BUTCHERING_PARTS.items()
        }
        if allocation is None:
            return {
                'target_character_id': target.character_id,
                'target_name': target.character.name,
                'species': CombatService._mutant_profile_name(target_data),
                'roll': deepcopy(pending),
                'parts': parts,
            }

        selected = allocation if isinstance(allocation, dict) else {}
        normalized = {}
        spent = 0
        for key, profile in parts.items():
            amount = max(0, CombatService._coerce_int(selected.get(key), 0))
            if not profile['available'] and amount:
                raise ValidationError(f"Часть «{profile['label']}» недоступна для этого мутанта")
            if profile['max'] is not None and amount > profile['max']:
                raise ValidationError(f"Превышен максимум для части «{profile['label']}»")
            normalized[key] = amount
            spent += amount * profile['points']
        if spent > CombatService._coerce_int(pending.get('total'), 0):
            raise ValidationError('На выбранные части не хватает очков разделки')

        actor_data = actor.character.data if isinstance(actor.character.data, dict) else {}
        equipment = actor_data.get('equipment') or {}
        destination = 'backpack' if isinstance(equipment, dict) and equipment.get('backpack') else 'pockets'
        inventory = actor_data.setdefault('inventory', {})
        loot_container = inventory.setdefault(destination, [])
        if not isinstance(loot_container, list):
            loot_container = []
            inventory[destination] = loot_container
        loot = []
        for key, amount in normalized.items():
            if amount <= 0:
                continue
            profile = parts[key]
            item = {
                'id': f"item_{uuid.uuid4().hex}",
                'name': f"{profile['label']}: {CombatService._mutant_profile_name(target_data)}",
                'category': 'mutant_part',
                'subcategory': profile['label'],
                'quantity': amount,
                'weight': profile['weight'],
                'price': profile['unit_price'],
                'attributes': {
                    'mutant_part': key,
                    'source_mutant': CombatService._mutant_profile_name(target_data),
                    'radiation': profile['radiation'],
                },
            }
            loot_container.append(item)
            loot.append(deepcopy(item))
        from app.services.character_rules import mark_hunt
        mark_hunt(actor)
        if background_is(actor_data,'Охотник'):
            hunt_meta=actor_data.setdefault('health',{}).setdefault('combatMeta',{})
            debt=max(0,CombatService._coerce_float(hunt_meta.pop('hunterStressDebt',0),0))
            actor_data['health']['stress']=max(0,CombatService._coerce_float(actor_data['health'].get('stress'),0)-debt)
            hunt_meta['hunterActivityDay']=location.lobby.game_day or 1
        target_meta['butchered'] = True
        target_meta['butcheringResult'] = {'allocation': normalized, 'spent': spent}
        target_meta.pop('butcheringRoll', None)
        actor.character.data = actor_data
        target.character.data = target_data
        flag_modified(actor.character, 'data')
        flag_modified(target.character, 'data')
        db.session.commit()
        return {
            'completed': True,
            'actor_character_id': actor.character_id,
            'target_character_id': target.character_id,
            'target_name': target.character.name,
            'species': CombatService._mutant_profile_name(target_data),
            'roll': deepcopy(pending),
            'spent': spent,
            'remaining': max(0, CombatService._coerce_int(pending.get('total'), 0) - spent),
            'loot': loot,
            'actor_data': actor_data,
        }

    @staticmethod
    def _equipment_template(item):
        if not isinstance(item, dict):
            return None
        template_id = CombatService._coerce_int(item.get('templateId'), 0)
        return db.session.get(ItemTemplate, template_id) if template_id else None

    @staticmethod
    def _is_exoskeleton_item(item, template=None):
        attributes = CombatService._template_attributes(item)
        if template and isinstance(template.attributes, dict):
            attributes = {**template.attributes, **attributes}
        name = str((template.name if template else item.get('name')) or '').strip().lower().replace('ё', 'е')
        return bool(item.get('isExoskeleton') or attributes.get('is_exoskeleton')) or name == 'экзоскелет'

    @staticmethod
    def _equipment_action_points(item, slot, operation, template=None):
        if slot in {'helmet', 'gasMask'}:
            weight = CombatService._coerce_float(
                item.get('weight') if isinstance(item, dict) else 0,
                0,
            )
            return (2 if weight <= 2 else 4) if operation == 'equip' else (1 if weight <= 2 else 3)
        if slot != 'armor':
            raise ValidationError('Unsupported equipment slot')
        category = str(
            (template.subcategory if template else None)
            or item.get('subcategory')
            or ''
        ).strip().lower().replace('ё', 'е')
        if 'легк' in category:
            base = 10
        elif 'средн' in category:
            base = 20
        else:
            base = 30
        return base if operation == 'equip' else base // 2

    @staticmethod
    def _iter_inventory_items_with_paths(character_data):
        data = character_data if isinstance(character_data, dict) else {}
        inventory = data.get('inventory') if isinstance(data.get('inventory'), dict) else {}
        equipment = data.get('equipment') if isinstance(data.get('equipment'), dict) else {}
        pending = []
        for key in ('backpack', 'pockets'):
            items = inventory.get(key)
            if isinstance(items, list):
                pending.extend((item, ['inventory', key, index]) for index, item in enumerate(items))
        for group_name in ('belt', 'vest'):
            container = equipment.get(group_name)
            pouches = container.get('pouches') if isinstance(container, dict) else []
            for pouch_index, pouch in enumerate(pouches if isinstance(pouches, list) else []):
                contents = pouch.get('contents') if isinstance(pouch, dict) else []
                if isinstance(contents, list):
                    pending.extend(
                        (item, ['equipment', group_name, 'pouches', pouch_index, 'contents', index])
                        for index, item in enumerate(contents)
                    )
        while pending:
            item, path = pending.pop()
            if not isinstance(item, dict):
                continue
            yield item, path
            contents = item.get('contents')
            if isinstance(contents, list):
                pending.extend(
                    (child, [*path, 'contents', index])
                    for index, child in enumerate(contents)
                )

    @staticmethod
    def _exoskeleton_tool_profile(character_data, *, removal=False):
        best = None
        for item, path in CombatService._iter_inventory_items_with_paths(character_data):
            attributes = CombatService._template_attributes(item)
            profile = attributes.get('repair_profile') if isinstance(attributes.get('repair_profile'), dict) else {}
            if profile.get('kind') != 'armor':
                continue
            max_class = profile.get('max_item_class')
            is_extended = max_class in {None, ''} and CombatService._coerce_int(
                profile.get('engineering_min'), 0,
            ) >= 17
            if removal and not is_extended:
                continue
            rank = 3 if is_extended else max(1, CombatService._coerce_int(max_class, 1))
            if not best or rank > best['rank']:
                best = {'item': item, 'path': path, 'rank': rank}
        return best

    @staticmethod
    def _integrated_helmet_from_armor(armor, template):
        attributes = dict(template.attributes or {}) if template else {}
        profile = dict(attributes.get('integrated_helmet_profile') or {})
        if not profile and not attributes.get('integrated_helmet'):
            return None
        physical = CombatService._coerce_float(profile.get('physical'), 0)
        if physical > 1:
            physical /= 100
        return {
            'templateId': f"integrated:{armor.get('templateId') or 'armor'}",
            'integratedWithArmor': True,
            'sourceArmorTemplateId': armor.get('templateId'),
            'name': attributes.get('integrated_helmet_name') or f"{armor.get('name')} · встроенный шлем",
            'material': armor.get('material'),
            'protection': {
                'physical': physical,
                'chemical': 0,
                'thermal': 0,
                'electric': 0,
                'radiation': 0,
            },
            'durability': armor.get('durability'),
            'maxDurability': armor.get('maxDurability'),
            'stage': armor.get('stage'),
            'condition': armor.get('condition'),
            'stageDurability': armor.get('stageDurability'),
            'currentStageDurability': armor.get('currentStageDurability'),
            'accuracyPenalty': CombatService._coerce_int(profile.get('accuracyPenalty'), 0),
            'ergonomicsPenalty': 0,
            'charismaBonus': -CombatService._coerce_float(profile.get('charismaPenalty'), 0),
            'modifications': [],
            'installedModules': [],
        }

    @staticmethod
    def _prepare_equipped_item(item, slot, template=None):
        equipped = deepcopy(item)
        attributes = dict(template.attributes or {}) if template else {}
        stored_attributes = equipped.get('attributes') if isinstance(equipped.get('attributes'), dict) else {}
        attributes.update(stored_attributes)
        if template:
            equipped['templateId'] = template.id
            equipped['name'] = template.name
            equipped['weight'] = template.weight
            equipped['volume'] = template.volume
            equipped['subcategory'] = template.subcategory
            equipped['itemClass'] = template.item_class
        equipped['quantity'] = 1
        equipped['attributes'] = attributes
        equipped.setdefault('protection', deepcopy(attributes.get('protection') or {}))
        equipped.setdefault('durability', attributes.get('max_durability', 0))
        equipped.setdefault('maxDurability', attributes.get('max_durability', equipped.get('durability', 0)))
        equipped.setdefault('installedModules', [])
        equipped.setdefault('modifications', [])
        if slot == 'armor':
            equipped.setdefault('movementPenalty', attributes.get('movement_penalty', 0))
            equipped.setdefault('protectionZones', deepcopy(attributes.get('protection_zones') or []))
            equipped['integratedHelmet'] = bool(attributes.get('integrated_helmet'))
            equipped['isExoskeleton'] = CombatService._is_exoskeleton_item(equipped, template)
            equipped['requiresExoskeletonBattery'] = bool(
                attributes.get('requires_exoskeleton_battery') or equipped['isExoskeleton']
            )
        elif slot in {'helmet', 'gasMask'}:
            equipped.setdefault('accuracyPenalty', attributes.get('accuracy_penalty', 0))
            equipped.setdefault('ergonomicsPenalty', attributes.get('ergonomics_penalty', 0))
            equipped.setdefault('charismaBonus', attributes.get('charisma_bonus', 0))
            equipped.setdefault('movementPenalty', attributes.get('movement_penalty', 0))
        return equipped

    @staticmethod
    def _helmet_blocks_gas_mask(item, template=None):
        if not isinstance(item, dict):
            return False
        attributes = dict(template.attributes or {}) if template else {}
        attributes.update(
            item.get('attributes') if isinstance(item.get('attributes'), dict) else {}
        )
        modules = item.get('installedModules') if isinstance(item.get('installedModules'), list) else []
        name = str((template.name if template else None) or item.get('name') or '').lower()
        return bool(
            item.get('integratedWithArmor')
            or attributes.get('integrated_visor')
            or attributes.get('requires_filter')
            or any(
                isinstance(module, dict) and module.get('slotType') == 'visor'
                for module in modules
            )
            or 'противогазо-шлем' in name
        )

    @staticmethod
    def equipment_action_details(
        character_data,
        operation,
        slot,
        *,
        item_path=None,
        retrieval_action_points=0,
        in_combat=False,
        preconfigured=False,
    ):
        operation = str(operation or '').strip().lower()
        slot = str(slot or '').strip()
        if operation not in {'equip', 'unequip'}:
            raise ValidationError('Unknown equipment operation')
        if slot not in {'armor', 'helmet', 'gasMask'}:
            raise ValidationError('Unsupported equipment slot')
        equipment = character_data.setdefault('equipment', {})
        if operation == 'equip':
            if equipment.get(slot, {}).get('templateId'):
                raise ValidationError('Remove the currently equipped item first')
            item = CombatService._inventory_item_at_path(character_data, item_path)
            expected_category = {'armor': 'armor', 'helmet': 'helmet', 'gasMask': 'gas_mask'}[slot]
            if str(item.get('category') or '').lower() != expected_category:
                raise ValidationError('The selected item does not fit this equipment slot')
        else:
            item = equipment.get(slot)
            if not isinstance(item, dict) or not item.get('templateId'):
                raise ValidationError('Nothing is equipped in this slot')
            if item.get('integratedWithArmor'):
                raise ValidationError('The integrated helmet is removed together with the armor')
        template = CombatService._equipment_template(item)
        if operation == 'equip':
            from app.services.equipment_passives import validate_glasses
            proposed = {**character_data, 'equipment': {**equipment, slot: item}}
            validate_glasses(proposed)
            if slot == 'armor' and CombatService._integrated_helmet_from_armor(item, template):
                current_helmet = equipment.get('helmet')
                if isinstance(current_helmet, dict) and current_helmet.get('templateId'):
                    raise ValidationError('Remove the helmet before equipping this armor')
                current_mask = equipment.get('gasMask')
                if isinstance(current_mask, dict) and current_mask.get('templateId'):
                    raise ValidationError('Remove the gas mask before equipping this armor')
            elif slot == 'helmet':
                current_mask = equipment.get('gasMask')
                if (
                    isinstance(current_mask, dict)
                    and current_mask.get('templateId')
                    and CombatService._helmet_blocks_gas_mask(item, template)
                ):
                    raise ValidationError('This helmet cannot be worn with a gas mask')
            elif slot == 'gasMask':
                current_helmet = equipment.get('helmet')
                helmet_template = CombatService._equipment_template(current_helmet)
                armor = equipment.get('armor')
                armor_template = CombatService._equipment_template(armor)
                if (
                    isinstance(armor, dict)
                    and CombatService._integrated_helmet_from_armor(armor, armor_template)
                ):
                    raise ValidationError('This gas mask cannot be worn with integrated armor helmet')
                if (
                    isinstance(current_helmet, dict)
                    and current_helmet.get('templateId')
                    and CombatService._helmet_blocks_gas_mask(
                        current_helmet, helmet_template,
                    )
                ):
                    raise ValidationError('This gas mask cannot be worn with the current helmet')
        is_exoskeleton = slot == 'armor' and CombatService._is_exoskeleton_item(item, template)
        if is_exoskeleton and preconfigured:
            # Генерация персонажа задаёт уже надетое снаряжение, а не игровое действие.
            duration_minutes = 0
            action_points = 0
        elif is_exoskeleton:
            if in_combat:
                raise ValidationError('The exoskeleton cannot be equipped or removed during combat')
            tool = CombatService._exoskeleton_tool_profile(
                character_data,
                removal=operation == 'unequip',
            )
            if not tool:
                raise ValidationError(
                    'Equipping requires armorer tools; removal requires advanced armorer tools'
                )
            if operation == 'unequip':
                engineering = CombatService._skill_value(
                    character_data,
                    'skills.other.engineering',
                )
                if engineering < 18:
                    raise ValidationError('Engineering 18 is required to remove the exoskeleton')
            duration_minutes = 10 if operation == 'equip' else 60
            action_points = 0
        else:
            duration_minutes = 0
            action_points = CombatService._equipment_action_points(
                item, slot, operation, template,
            )
            access = None
            if operation == 'equip':
                if in_combat:
                    from app.services.inventory_access import calculate_inventory_access

                    access = calculate_inventory_access(character_data, item_path, item)
                    action_points += int(access['retrieval_action_points'])
        return {
            'operation': operation,
            'slot': slot,
            'item_path': list(item_path or []),
            'item': item,
            'template': template,
            'is_exoskeleton': is_exoskeleton,
            'action_points': action_points,
            'duration_minutes': duration_minutes,
            'inventory_access': access if not is_exoskeleton else None,
        }

    @staticmethod
    def apply_equipment_action(character_data, details):
        equipment = character_data.setdefault('equipment', {})
        slot = details['slot']
        if details['operation'] == 'equip':
            item = CombatService._take_inventory_item(
                character_data,
                details['item_path'],
                1,
            )
            equipped = CombatService._prepare_equipped_item(
                item,
                slot,
                details.get('template'),
            )
            equipment[slot] = equipped
            if slot == 'armor':
                integrated = CombatService._integrated_helmet_from_armor(
                    equipped,
                    details.get('template'),
                )
                if integrated:
                    equipment['helmet'] = integrated
        else:
            equipped = equipment.pop(slot)
            inventory = character_data.setdefault('inventory', {})
            inventory.setdefault('backpack', []).append(deepcopy(equipped))
            if slot == 'armor' and isinstance(equipment.get('helmet'), dict):
                if equipment['helmet'].get('integratedWithArmor'):
                    equipment.pop('helmet', None)
        return {
            'operation': details['operation'],
            'slot': slot,
            'item_name': details['item'].get('name'),
            'action_points': details['action_points'],
            'duration_minutes': details['duration_minutes'],
            'is_exoskeleton': details['is_exoskeleton'],
        }

    @staticmethod
    def loot_incapacitated_character(
        location_id,
        user_id,
        actor_location_character_id,
        target_character_id,
        item_path,
        amount=1,
    ):
        actor, target, _ = CombatService._validate_incapacitated_interaction(
            location_id,
            user_id,
            actor_location_character_id,
            target_character_id,
        )
        actor_data = (
            deepcopy(actor.character.data)
            if actor.character and isinstance(actor.character.data, dict)
            else {}
        )
        target_data = (
            deepcopy(target.character.data)
            if target.character and isinstance(target.character.data, dict)
            else {}
        )
        transferred = CombatService._take_inventory_item(
            target_data,
            item_path,
            amount,
        )
        inventory = actor_data.setdefault('inventory', {})
        backpack = inventory.setdefault('backpack', [])
        if not isinstance(backpack, list):
            backpack = []
            inventory['backpack'] = backpack
        backpack.append(transferred)
        actor.character.data = actor_data
        target.character.data = target_data
        flag_modified(actor.character, 'data')
        flag_modified(target.character, 'data')
        db.session.commit()
        return CombatService._incapacitated_character_snapshot(
            actor,
            target,
            CombatService._location_character_condition(target),
        )

    @staticmethod
    def update_incapacitated_character_health(
        location_id,
        user_id,
        actor_location_character_id,
        target_character_id,
        health,
        treatment_request_id=None,
        *, commit=True,
    ):
        if treatment_request_id:
            from app.services.character_interaction import CharacterInteractionService

            _, actor, target = CharacterInteractionService._pair(
                location_id,
                user_id,
                actor_location_character_id,
                target_character_id,
            )
            CharacterInteractionService.validate_treatment(
                treatment_request_id,
                actor,
                target,
            )
        else:
            actor, target, _ = CombatService._validate_incapacitated_interaction(
                location_id,
                user_id,
                actor_location_character_id,
                target_character_id,
            )
        if not isinstance(health, dict):
            raise ValidationError("Health data is required")
        target_data = (
            deepcopy(target.character.data)
            if target.character and isinstance(target.character.data, dict)
            else {}
        )
        target_data['health'] = deepcopy(health)
        preserve_action_payment(target.character.data, target_data)
        normalize_character_effects(target_data)
        target.character.data = target_data
        flag_modified(target.character, 'data')
        CombatService._sync_location_effects_from_character(target)
        if commit:
            db.session.commit()
        return CombatService._incapacitated_character_snapshot(
            actor,
            target,
            CombatService._location_character_condition(target),
        )

    @staticmethod
    def _serialize_state(location, state):
        from app.services.cloud_geometry import refresh
        refresh(state)
        characters = CombatService._unique_location_characters(
            LocationCharacter.query.filter_by(location_id=location.id).all()
        )
        from app.services.character_rules import refresh_nearby
        refresh_nearby(location.id,characters)
        turn_order = list(dict.fromkeys(state.turn_order or []))
        order_map = {character_id: index for index, character_id in enumerate(turn_order)}

        if state.turn_order:
            ordered_characters = sorted(
                characters,
                key=lambda item: (
                    order_map.get(item.id, len(order_map) + 1),
                    -(item.initiative_total or 0),
                    item.id,
                ),
            )
        else:
            ordered_characters = sorted(
                characters,
                key=lambda item: (-(item.initiative_total or 0), item.id),
            )

        current_character = next(
            (item for item in ordered_characters if item.id == state.current_location_character_id),
            None,
        )
        # Один снимок геометрии на сборку ответа, без кеша между действиями.
        scene_objects=LocationObject.query.filter_by(location_id=location.id).all()
        from app.services.portable_lights import bindings, ground_sources
        light_actors=[item for item in characters if item.character and bindings(item.character.data)]

        return {
            'location_id': location.id,
            'status': state.status,
            'round_number': state.round_number,
            'turn_index': state.turn_index,
            'turn_order': turn_order,
            'current_location_character_id': state.current_location_character_id,
            'reaction': {
                'pending_location_character_id': state.reaction_pending_location_character_id,
                'return_location_character_id': state.reaction_return_location_character_id,
            },
            'pending_explosives': list(state.pending_explosives or []),
            'area_effects': list(state.area_effects or []),
            'ground_lights': ground_sources(location.id,scene_objects),
            'current_character': CombatService._serialize_character(
                current_character,
                current_turn_id=state.current_location_character_id,
                combat_state=state,
                scene_objects=scene_objects,light_actors=light_actors,
            ) if current_character else None,
            'characters': [
                CombatService._serialize_character(
                    item,
                    current_turn_id=state.current_location_character_id,
                    combat_state=state,
                    scene_objects=scene_objects,light_actors=light_actors,
                )
                for item in ordered_characters
            ],
            'available_actions': CombatService.action_catalog(current_character) if current_character else [],
        }

    @staticmethod
    def action_catalog(loc_char=None):
        if not loc_char:
            return [dict(action) for action in ACTION_CATALOG]
        profile = CombatService._combat_profile(loc_char)
        movement_gain = profile['movement_gain']
        return [
            ({**action,'action_points':2 if trait_count(loc_char.character.data,'Уклонист') else 4} if action['key']=='evade' else dict(action)) if action['key'] != 'convert_free_action_to_movement'
            else {**action, 'movement_points_gain': movement_gain}
            for action in ACTION_CATALOG
        ]

    @staticmethod
    def get_state(location_id, user_id):
        location = CombatService._get_location(location_id)
        CombatService._ensure_access(location, user_id)
        state = LocationCombatState.query.filter_by(location_id=location_id).first()
        if not state:
            state = LocationCombatState(
                location_id=location_id,
                status='idle',
                round_number=0,
                turn_index=0,
                turn_order=[],
                current_location_character_id=None,
            )
        return CombatService._serialize_state(location, state)

    @staticmethod
    def start_combat(
        location_id,
        user_id,
        location_character_ids=None,
        initiator_location_character_id=None,
        initiator_first=True,
    ):
        location = CombatService._get_location(location_id)
        CombatService._ensure_access(location, user_id)
        if location.lobby.gm_id != user_id:
            raise PermissionDenied("Only GM can start combat")

        available_characters = CombatService._unique_location_characters(
            LocationCharacter.query.filter_by(location_id=location_id).all()
        )
        if not available_characters:
            raise ValidationError("No characters are present in this location")
        available_by_id = {item.id: item for item in available_characters}
        if location_character_ids is None:
            loc_chars = [
                item for item in available_characters
                if CombatService._location_character_condition(item)['state'] != 'dead'
            ]
            if not loc_chars:
                raise ValidationError('На подлокации нет живых участников боя')
        else:
            if not isinstance(location_character_ids, (list, tuple, set)):
                raise ValidationError("Combat participants must be a list")
            selected_ids = list(dict.fromkeys(
                CombatService._coerce_int(value, 0)
                for value in location_character_ids
            ))
            if not selected_ids or any(value <= 0 for value in selected_ids):
                raise ValidationError("Select at least one combat participant")
            missing_ids = [
                value for value in selected_ids if value not in available_by_id
            ]
            if missing_ids:
                raise ValidationError("Selected character is not in this location")
            loc_chars = [available_by_id[value] for value in selected_ids]
            if any(
                CombatService._location_character_condition(item)['state'] == 'dead'
                for item in loc_chars
            ):
                raise ValidationError('Мёртвого персонажа нельзя добавить в инициативу')

        initiator_id = CombatService._coerce_int(
            initiator_location_character_id, 0,
        )
        if initiator_id and initiator_id not in {item.id for item in loc_chars}:
            raise ValidationError("The combat initiator must be a selected participant")
        if initiator_id and CombatService._location_character_condition(
            available_by_id[initiator_id]
        )['state'] != 'active':
            raise ValidationError('Только персонаж в сознании может начать бой действием')
        if not isinstance(initiator_first, bool):
            raise ValidationError('Укажите, должен ли инициатор ходить первым')

        state = CombatService._get_or_create_state(location_id)
        if state.status == 'active':
            raise ValidationError("Combat is already active")
        state.started_at = datetime.now(timezone.utc)

        selected_location_ids = {item.id for item in loc_chars}
        for loc_char in loc_chars:
            CombatService._refresh_mutant_pack_bonus(
                loc_char, selected_location_ids,
            )
        for loc_char in available_characters:
            character_data = (
                loc_char.character.data
                if loc_char.character and isinstance(loc_char.character.data, dict)
                else {}
            )
            pepper_meta = (character_data.get('health') or {}).get('combatMeta')
            if isinstance(pepper_meta, dict):
                for key in ('pepperHeadHits', 'pepperSkipTurns', 'pepperSkipTurnActive'):
                    pepper_meta.pop(key, None)
            from app.services.character_traits import carried_weapons
            from app.services.portable_lights import start_battle
            if start_battle(character_data):flag_modified(loc_char.character,'data')
            weapons = carried_weapons(character_data)
            changed_weapons = False
            for weapon in weapons:
                if isinstance(weapon, dict):
                    if weapon.pop('_usedInCurrentCombat',None) is not None:changed_weapons=True
                    if weapon.pop('_combatDurabilityLoss',None) is not None:changed_weapons=True
            if changed_weapons:
                loc_char.character.data = character_data
                flag_modified(loc_char.character, 'data')
            profile = CombatService._combat_profile(loc_char)
            loc_char.initiative_bonus = profile['initiative_bonus']
            if loc_char.id not in selected_location_ids:
                loc_char.initiative_roll = None
                loc_char.initiative_total = None
                continue
            loc_char.initiative_roll = random.randint(1, 20)
            from app.services.brigadier_order import boost_roll
            _rolls,loc_char.initiative_roll,order_bonus=boost_roll(loc_char.character.data,[loc_char.initiative_roll])
            loc_char.initiative_total = loc_char.initiative_roll + loc_char.initiative_bonus+order_bonus
            loc_char.strenuous_movement_blocked_until_round = 0
            loc_char.drawn_weapon_index = CombatService._persistent_weapon_index(loc_char)
            CombatService._clear_aim(loc_char)
            CombatService._prepare_character_for_turn(loc_char)
            CombatService._sync_location_effects_from_character(loc_char)

        ordered_chars = sorted(
            loc_chars,
            key=lambda item: (
                0 if initiator_first and initiator_id and item.id == initiator_id else 1,
                -(item.initiative_total or 0),
                -(item.initiative_bonus or 0),
                item.id,
            ),
        )

        opening_character = ordered_chars[0] if ordered_chars else None
        if opening_character and opening_character.character:
            opening_data = opening_character.character.data
            opening_data = opening_data if isinstance(opening_data, dict) else {}
            opening_meta = opening_data.setdefault('health', {}).setdefault(
                'combatMeta', {},
            )
            if CombatService._mutant_opening_ambush_eligible(opening_data):
                opening_meta['mutantOpeningMovementBonus'] = 5
                opening_meta['mutantOpeningAttackAdvantage'] = True
                opening_meta['mutantOpeningRound'] = 1
                opening_character.movement_points_max = (
                    opening_character.movement_points_max or 0
                ) + 5
                opening_character.movement_points_current = (
                    opening_character.movement_points_current or 0
                ) + 5
                opening_character.character.data = opening_data
                flag_modified(opening_character.character, 'data')

        first_actionable_index = next(
            (
                index for index, loc_char in enumerate(ordered_chars)
                if CombatService._can_take_combat_turn(loc_char)
            ),
            None,
        )
        if first_actionable_index is None:
            raise ValidationError("No selected combat participant can take a turn")

        state.status = 'active'
        state.round_number = 1
        state.turn_index = first_actionable_index
        state.turn_order = [item.id for item in ordered_chars]
        state.current_location_character_id = ordered_chars[first_actionable_index].id
        state.pending_explosives = []
        state.area_effects = []
        from app.services.prosthesis_surgery import interrupt_location
        interrupted_surgeries=interrupt_location(location)
        from app.services.location_scout import start_combat as apply_scout
        apply_scout(location_id,ordered_chars)
        from app.services.character_rules import start_turn
        first_character = ordered_chars[first_actionable_index]
        token=state.started_at.replace(tzinfo=None).isoformat()
        for participant in ordered_chars:
            data=participant.character.data or {}
            meta=data.setdefault('health',{}).setdefault('combatMeta',{})
            meta['firstCombatTurnToken']=token if participant.id==first_character.id else None
            meta['lastPaidHelpRound']=0
            for key in ('harmAllowedTargets','combatExplosionOccurred','grenadirMustExplode','sniperOldCover'):
                meta.pop(key,None)
            health=data['health']
            health['effects']=[e for e in normalize_effect_list(health.get('effects') or []) if e.get('source')!='background_aggressor']
            if participant.id==initiator_id and background_is(data,'Агрессор'):
                apply_effect_to_health(health,{'type':'custom','name':'Агрессор: начало боя','source':'background_aggressor',
                    'value':3,'remaining':3,'duration':3,'tick':'round_end',
                    'modifiers':{p:-3 for p in ('skills.physical.shooting','skills.physical.melee','skills.social.intimidation',
                                               'physical.shooting','physical.melee','social.intimidation')}})
            bonus=character_passives(data)['first_rounds_ap']+(2 if participant.id==first_character.id and background_is(data,'Математик') else 0)
            if CombatService._can_take_combat_turn(participant):
                participant.action_points_current+=bonus
                participant.action_points_max+=bonus
            flag_modified(participant.character,'data')
            CombatService._sync_location_effects_from_character(participant)
            if trait_count(data,'Паникёр') and CombatService._can_take_combat_turn(participant):
                CombatService.apply_stress_trigger(participant,1,trigger='combat_start_panicker')
        start_turn(first_character)
        CombatService._update_helper_penalties(state)
        psi_start = CombatService._resolve_psi_turn_start(first_character,1)
        CombatService._sync_location_effects_from_character(first_character)
        db.session.commit()
        if interrupted_surgeries:
            from app.services.character_events import emit_character_update
            for updated in interrupted_surgeries:
                emit_character_update({'character_id':updated.id,'updates':{'data':updated.data_snapshot()},'source':'prosthesis'})
        if not CombatService._can_take_combat_turn(first_character):
            return CombatService.end_turn(location_id,user_id,location_character_id=first_character.id,_continue_pending=True)
        payload = CombatService._serialize_state(location,state)
        payload['psy_turn_start'] = psi_start
        return payload

    @staticmethod
    @journaled
    def end_turn(
        location_id,
        user_id,
        location_character_id=None,
        _continue_pending=False,
        _auto_skip_remaining=None,
    ):
        location = CombatService._get_location(location_id)
        is_gm = CombatService._ensure_access(location, user_id)
        CombatService._release_invalid_grapples(location_id)
        CombatService._release_invalid_gunpoints(location_id)
        state = LocationCombatState.query.filter_by(location_id=location_id).first()
        if not state:
            raise ValidationError("Combat is not active")
        if state.status != 'active':
            raise ValidationError("Combat is not active")
        from app.services.explosion_choice import require_resolved
        require_resolved(state)
        from app.services.sniper_trauma import require_resolved as require_trauma
        require_trauma(state)
        if not state.turn_order:
            raise ValidationError("Turn order is empty")

        ending_round = max(1, state.round_number or 1)
        current_character_id = state.current_location_character_id
        if location_character_id is None:
            location_character_id = current_character_id

        if location_character_id != current_character_id and not is_gm:
            raise PermissionDenied("It is not this character's turn")

        if location_character_id != current_character_id and is_gm:
            current_character_id = location_character_id

        current_character = LocationCharacter.query.filter_by(
            id=current_character_id,
            location_id=location_id,
        ).first()
        if not current_character:
            raise NotFoundError("Current character not found")
        if not _continue_pending and not CombatService._can_end_turn_for_character(current_character, user_id, is_gm=is_gm):
            raise PermissionDenied("You do not control this character")
        if not _continue_pending and state.reaction_return_location_character_id is None:
            from app.services.character_rules import before_end_turn
            before_end_turn(current_character,state)

        from app.services.grenade_reaction import window,finish,begin
        if window(current_character):
            detonations=finish(current_character,state)
            cancel_unavailable_actions(current_character)
            detonations.extend(begin(current_character,state))
            if not window(current_character):
                CombatService._pay_turn_obligations(current_character)
            CombatService._sync_location_effects_from_character(current_character)
            db.session.commit()
            meta=(current_character.character.data.get('health') or {}).get('combatMeta') or {}
            if not window(current_character) and (not CombatService._can_take_combat_turn(current_character) or (meta.get('pendingAction') or {}).get('remaining_action_points',0)>0):
                payload=CombatService.end_turn(location_id,user_id,location_character_id=current_character.id,_continue_pending=True)
            else:
                payload=CombatService._serialize_state(location,state)
            payload['detonations']=detonations+payload.get('detonations',[])
            payload['grenade_reaction_finished']=True
            return payload

        if state.reaction_return_location_character_id is not None:
            if current_character.id != state.current_location_character_id:
                raise PermissionDenied("Only the reacting character can end this reaction")
            return_character = LocationCharacter.query.filter_by(
                id=state.reaction_return_location_character_id,
                location_id=location_id,
            ).first()
            if not return_character:
                raise NotFoundError("Interrupted character not found")
            reaction_data = (
                current_character.character.data
                if current_character.character and isinstance(current_character.character.data, dict)
                else {}
            )
            reaction_health = reaction_data.setdefault('health', {})
            reaction_meta = reaction_health.setdefault('combatMeta', {})
            reaction_meta.pop('reactionActive', None)
            current_character.character.data = reaction_data
            flag_modified(current_character.character, 'data')
            current_character.action_points_current = 0
            current_character.free_actions_current = 0
            current_character.movement_points_current = 0
            state.current_location_character_id = return_character.id
            state.reaction_return_location_character_id = None
            state.reaction_pending_location_character_id = None
            db.session.commit()
            if not CombatService._can_take_combat_turn(return_character):
                remaining = (
                    max(0, len(state.turn_order) - 1)
                    if _auto_skip_remaining is None
                    else _auto_skip_remaining
                )
                if remaining <= 0:
                    payload = CombatService.end_combat(location_id, location.lobby.gm_id)
                    payload['auto_ended'] = True
                    return payload
                payload = CombatService.end_turn(
                    location_id,
                    user_id,
                    location_character_id=return_character.id,
                    _continue_pending=True,
                    _auto_skip_remaining=remaining - 1,
                )
                payload.setdefault('auto_skipped', []).insert(0, {
                    'location_character_id': return_character.id,
                    'name': return_character.character.name if return_character.character else '',
                    'condition': CombatService._location_character_condition(return_character),
                })
                payload['reaction_returned'] = True
                return payload
            payload = CombatService._serialize_state(location, state)
            payload['reaction_returned'] = True
            return payload

        state.turn_order = list(dict.fromkeys(state.turn_order or []))
        current_index = state.turn_order.index(current_character_id)
        next_index = (current_index + 1) % len(state.turn_order)
        next_character_id = state.turn_order[next_index]
        next_character = LocationCharacter.query.filter_by(
            id=next_character_id,
            location_id=location_id,
        ).first()
        if not next_character:
            raise NotFoundError("Next character not found")

        detonations = CombatService._process_pending_explosives(
            state, phase='turn_end', actor_id=current_character.id,
        )
        CombatService._tick_character_effects(current_character, phase='turn_end')
        CombatService._apply_periodic_health_effects(current_character, phase='turn_end')
        current_data = current_character.character.data if current_character.character else {}
        current_meta = (current_data.get('health') or {}).get('combatMeta')
        if isinstance(current_meta, dict) and current_meta.pop('pepperSkipTurnActive', None):
            current_character.character.data = current_data
            flag_modified(current_character.character, 'data')
        psy_end_turn = CombatService._resolve_mutant_psy_end_turn(
            location, state, current_character,
        )
        anomaly_result = CombatService._resolve_anomaly_end_turn(
            current_character, ending_round,
        )
        radiation_result = CombatService._apply_location_end_turn_radiation(
            location,
            current_character,
        )
        psi_crisis = CombatService._resolve_psi_crisis(current_character.character.data,current_character,round_number=ending_round)
        if psi_crisis:
            flag_modified(current_character.character,'data')
        CombatService._resolve_bleeding_check(current_character)
        pain_shock_check = CombatService._resolve_pain_shock_check(
            current_character,
            ending_round,
        )
        current_data = (
            current_character.character.data
            if current_character.character
            and isinstance(current_character.character.data, dict)
            else {}
        )
        current_health = current_data.get('health')
        current_meta = (
            current_health.get('combatMeta')
            if isinstance(current_health, dict)
            and isinstance(current_health.get('combatMeta'), dict)
            else {}
        )
        # Help applies only during this character's current turn.
        if current_meta.pop('helpAdvantage', None) is not None:
            current_character.character.data = current_data
            flag_modified(current_character.character, 'data')
        if (
            CombatService._coerce_int(
                current_meta.get('mutantOpeningRound'), 0,
            ) == ending_round
        ):
            current_meta.pop('mutantOpeningMovementBonus', None)
            current_meta.pop('mutantOpeningAttackAdvantage', None)
            current_meta.pop('mutantOpeningRound', None)
            current_character.character.data = current_data
            flag_modified(current_character.character, 'data')
        if current_meta.pop('circularAttackRound', None) == ending_round:
            apply_effect_to_health(current_health, {
                'type': 'circular_attack_recovery',
                'name': 'Восстановление после круговой атаки',
                'remaining': 1,
                'tick': 'turn_end',
                'rollPenalty': 2,
                'source': 'circular_attack',
            })
            current_character.character.data = current_data
            flag_modified(current_character.character, 'data')
        CombatService._release_invalid_grapples(location_id)
        CombatService._sync_location_effects_from_character(current_character)

        if next_index == 0:
            round_characters = CombatService._unique_location_characters(
                LocationCharacter.query.filter_by(location_id=location_id).all()
            )
            area_updates = CombatService._advance_area_effects(state)
            for round_character in round_characters:
                CombatService._apply_periodic_health_effects(round_character,phase='round_end')
                CombatService._sync_location_effects_from_character(round_character)
            CombatService._apply_end_of_round_pain_recovery(round_characters)
            participant_ids = set(state.turn_order or [])
            psy_recovery = CombatService._apply_end_of_round_psi_recovery(
                [item for item in round_characters if item.id in participant_ids],
            )
            for round_character in round_characters:
                CombatService._advance_character_time(round_character, 6)
            from app.services.portable_lights import advance_ground
            advance_ground(location_id,6)
            state.round_number += 1
            from app.services.sniper_position import expire as expire_sniper_position
            expire_sniper_position(location_id,state)
            CombatService._update_helper_penalties(state)
            detonations.extend(CombatService._process_pending_explosives(
                state, phase='round_start',
            ))
        else:
            area_updates = []
            psy_recovery = []
        state.turn_index = next_index
        state.current_location_character_id = next_character_id
        detonations.extend(CombatService._process_pending_explosives(state,phase='actor_turn_start',actor_id=next_character_id))
        psi_start = CombatService._resolve_psi_turn_start(next_character,state.round_number)
        CombatService._prepare_character_for_turn(next_character)
        detonations.extend(getattr(next_character,'_grenade_detonations',[]))
        CombatService._sync_location_effects_from_character(next_character)
        db.session.commit()

        next_data = (
            next_character.character.data
            if next_character.character and isinstance(next_character.character.data, dict)
            else {}
        )
        next_health = next_data.get('health') if isinstance(next_data.get('health'), dict) else {}
        next_meta = next_health.get('combatMeta') if isinstance(next_health.get('combatMeta'), dict) else {}
        pending_action = next_meta.get('pendingAction')
        if not window(next_character) and isinstance(pending_action, dict) and CombatService._coerce_int(
            pending_action.get('remaining_action_points'), 0
        ) > 0:
            payload = CombatService.end_turn(
                location_id,
                user_id,
                location_character_id=next_character.id,
                _continue_pending=True,
            )
            payload['detonations'] = detonations + payload.get('detonations', [])
            return payload
        reaction_reserve = next_meta.get('reactionReserve')
        if not window(next_character) and isinstance(reaction_reserve, dict) and CombatService._coerce_int(
            reaction_reserve.get('deferred_action_points'), 0
        ) > 0:
            payload = CombatService.end_turn(
                location_id,
                user_id,
                location_character_id=next_character.id,
                _continue_pending=True,
            )
            payload['detonations'] = detonations + payload.get('detonations', [])
            return payload

        if not CombatService._can_take_combat_turn(next_character):
            remaining = (
                max(0, len(state.turn_order) - 1)
                if _auto_skip_remaining is None
                else _auto_skip_remaining
            )
            if remaining <= 0:
                payload = CombatService.end_combat(location_id, location.lobby.gm_id)
                payload['auto_ended'] = True
                payload['detonations'] = detonations + payload.get('detonations', [])
                return payload
            payload = CombatService.end_turn(
                location_id,
                user_id,
                location_character_id=next_character.id,
                _continue_pending=True,
                _auto_skip_remaining=remaining - 1,
            )
            payload.setdefault('auto_skipped', []).insert(0, {
                'location_character_id': next_character.id,
                'name': next_character.character.name if next_character.character else '',
                'condition': CombatService._location_character_condition(next_character),
            })
            payload['detonations'] = detonations + payload.get('detonations', [])
            return payload

        payload = CombatService._serialize_state(location, state)
        payload['radiation'] = radiation_result
        payload['anomaly'] = anomaly_result
        payload['pain_shock_check'] = pain_shock_check
        payload['detonations'] = detonations
        payload['area_updates'] = area_updates
        payload['psy_end_turn'] = psy_end_turn
        payload['psy_crisis'] = psi_crisis
        payload['psy_turn_start'] = psi_start
        payload['psy_recovery'] = psy_recovery
        return payload

    @staticmethod
    def add_combat_participant(location_id, user_id, location_character_id, *, _commit=True, _allow_incapacitated=False):
        location = CombatService._get_location(location_id)
        CombatService._ensure_access(location, user_id)
        if location.lobby.gm_id != user_id:
            raise PermissionDenied('Only GM can add combat participants')
        state = LocationCombatState.query.filter_by(location_id=location_id).first()
        if not state or state.status != 'active':
            raise ValidationError('Combat is not active')
        participant_id = CombatService._coerce_int(location_character_id, 0)
        participant = LocationCharacter.query.filter_by(
            id=participant_id, location_id=location_id,
        ).first()
        if not participant:
            raise NotFoundError('Character not found')
        if not _allow_incapacitated and not CombatService._can_take_combat_turn(participant):
            raise ValidationError('Incapacitated character cannot join combat')
        turn_order = list(dict.fromkeys(state.turn_order or []))
        if participant_id in turn_order:
            raise ValidationError('Character is already in the turn order')
        if state.current_location_character_id not in turn_order:
            raise ValidationError('Current combat turn is unavailable')

        profile = CombatService._combat_profile(participant)
        participant.initiative_bonus = profile['initiative_bonus']
        participant.initiative_roll = random.randint(1, 20)
        from app.services.brigadier_order import boost_roll
        _rolls,participant.initiative_roll,order_bonus=boost_roll(participant.character.data,[participant.initiative_roll])
        participant.initiative_total = participant.initiative_roll + participant.initiative_bonus+order_bonus
        participant.strenuous_movement_blocked_until_round = 0
        participant.drawn_weapon_index = CombatService._persistent_weapon_index(participant)
        CombatService._clear_aim(participant)
        # Place the newcomer at the start; their first turn begins next round.
        turn_order.insert(0, participant_id)
        state.turn_order = turn_order
        state.turn_index = turn_order.index(state.current_location_character_id)
        if _commit:
            db.session.commit()
        return CombatService._serialize_state(location, state)

    @staticmethod
    def remove_combat_participant(location_id, user_id, location_character_id):
        location = CombatService._get_location(location_id)
        CombatService._ensure_access(location, user_id)
        if location.lobby.gm_id != user_id:
            raise PermissionDenied("Only GM can remove combat participants")

        state = LocationCombatState.query.filter_by(location_id=location_id).first()
        if not state or state.status != 'active':
            raise ValidationError("Combat is not active")

        participant_id = CombatService._coerce_int(location_character_id, 0)
        turn_order = list(dict.fromkeys(state.turn_order or []))
        if participant_id not in turn_order:
            raise ValidationError("Character is not in the turn order")
        participant = LocationCharacter.query.filter_by(
            id=participant_id,
            location_id=location_id,
        ).first()
        if not participant:
            raise NotFoundError("Character not found")

        removed_index = turn_order.index(participant_id)
        cancel_actor_actions(participant)
        was_current = state.current_location_character_id == participant_id
        turn_order.remove(participant_id)
        participant.initiative_roll = None
        participant.initiative_total = None
        participant.action_points_current = 0
        participant.free_actions_current = 0
        participant.movement_points_current = 0

        if state.reaction_pending_location_character_id == participant_id:
            state.reaction_pending_location_character_id = None
        if state.reaction_return_location_character_id == participant_id:
            state.reaction_return_location_character_id = None
        if was_current and state.reaction_return_location_character_id is not None:
            state.reaction_return_location_character_id = None
            state.reaction_pending_location_character_id = None

        if not turn_order:
            db.session.flush()
            payload = CombatService.end_combat(location_id, user_id)
            payload['removed_location_character_id'] = participant_id
            return payload

        state.turn_order = turn_order
        if was_current:
            next_index = removed_index % len(turn_order)
            if removed_index >= len(turn_order):
                state.round_number = max(1, state.round_number or 1) + 1
                from app.services.sniper_position import expire as expire_sniper_position
                expire_sniper_position(location_id,state)
                CombatService._update_helper_penalties(state)
            next_character_id = turn_order[next_index]
            next_character = LocationCharacter.query.filter_by(
                id=next_character_id,
                location_id=location_id,
            ).first()
            if not next_character:
                raise NotFoundError("Next character not found")
            state.turn_index = next_index
            state.current_location_character_id = next_character_id
            CombatService._resolve_psi_turn_start(next_character,state.round_number)
            CombatService._prepare_character_for_turn(next_character)
            CombatService._sync_location_effects_from_character(next_character)
        else:
            try:
                state.turn_index = turn_order.index(state.current_location_character_id)
            except ValueError:
                state.turn_index = 0
                state.current_location_character_id = turn_order[0]

        db.session.commit()

        current_character = LocationCharacter.query.filter_by(
            id=state.current_location_character_id,
            location_id=location_id,
        ).first()
        if current_character and not CombatService._can_take_combat_turn(current_character):
            payload = CombatService.end_turn(
                location_id,
                user_id,
                location_character_id=current_character.id,
                _continue_pending=True,
                _auto_skip_remaining=len(turn_order) - 1,
            )
        else:
            payload = CombatService._serialize_state(location, state)
        payload['removed_location_character_id'] = participant_id
        return payload

    @staticmethod
    def reserve_reaction(
        location_id,
        user_id,
        location_character_id,
        action_points=0,
        free_actions=0,
        movement_points=0,
        trigger='',
        kind='reaction',
        help_target_character_id=None,
        help_action_label='',
        help_skill_path='',
    ):
        location = CombatService._get_location(location_id)
        is_gm = CombatService._ensure_access(location, user_id)
        state = LocationCombatState.query.filter_by(location_id=location_id).first()
        if not state or state.status != 'active':
            raise ValidationError("Combat is not active")
        if state.reaction_return_location_character_id is not None:
            raise ValidationError("A reaction is already in progress")
        character = LocationCharacter.query.filter_by(
            id=location_character_id, location_id=location_id,
        ).first()
        if not character:
            raise NotFoundError("Character not found")

        CombatService._refresh_ugly_appearance_penalties(location_id)
        if state.current_location_character_id != character.id:
            raise PermissionDenied("A reaction can be reserved only during your turn")
        if not CombatService._can_end_turn_for_character(character, user_id, is_gm=is_gm):
            raise PermissionDenied("You do not control this character")
        CombatService.ensure_character_can_act(character)
        kind = str(kind or 'reaction').strip().lower()
        if kind not in {'reaction', 'help'}:
            raise ValidationError("Unknown reaction type")
        values = {
            'action_points': max(0, CombatService._coerce_int(action_points, 0)),
            'free_actions': max(0, CombatService._coerce_int(free_actions, 0)),
            'movement_points': max(0, CombatService._coerce_float(movement_points, 0)),
        }
        if not any(values.values()):
            raise ValidationError("Reserve at least one resource for a reaction")
        help_deferred_cost = 0
        if (
            (kind != 'help' and values['action_points'] > character.action_points_current)
            or values['free_actions'] > character.free_actions_current
            or values['movement_points'] > character.movement_points_current
        ):
            raise ValidationError("Not enough resources for this reaction")
        data = character.character.data if isinstance(character.character.data, dict) else {}
        meta = data.setdefault('health', {}).setdefault('combatMeta', {})
        if isinstance(meta.get('reactionReserve'), dict):
            raise ValidationError("This character already has a reserved reaction")
        help_target = None
        if kind == 'help':
            if values['free_actions'] or values['movement_points']:
                raise ValidationError("Help can reserve only action points")
            help_target = LocationCharacter.query.filter_by(
                location_id=location_id,
                character_id=CombatService._coerce_int(help_target_character_id, 0),
            ).first()
            if not help_target or help_target.id == character.id:
                raise ValidationError("Choose another character to help")
            _, _, _, target_anomaly = CombatService._active_anomaly(help_target)
            target_anomaly_profile = anomaly_profile(
                target_anomaly.get('key') if target_anomaly else None,
            ) or {}
            if target_anomaly_profile.get('cannot_help'):
                raise ValidationError("This anomaly does not allow outside help")
            help_action_label = ' '.join(str(help_action_label or '').split())
            if not help_action_label or len(help_action_label) > 200:
                raise ValidationError("Describe the action being helped")
            help_skill_path = str(help_skill_path or '').strip()
            if help_skill_path and help_skill_path not in CombatService.NARRATIVE_SKILLS:
                raise ValidationError("Unknown help skill")
            help_deferred_cost = max(
                0, values['action_points'] - character.action_points_current
            )
        paid_action_points = min(character.action_points_current, values['action_points'])
        character.action_points_current -= paid_action_points
        character.free_actions_current -= values['free_actions']
        character.movement_points_current -= values['movement_points']
        meta['reactionReserve'] = {
            **values,
            'kind': kind,
            'paid_action_points': paid_action_points,
            'deferred_action_points': help_deferred_cost,
            'trigger': str(trigger or '').strip()[:240],
            'round': state.round_number,
        }
        if kind == 'help':
            meta['reactionReserve'].update({
                'target_character_id': help_target.character_id,
                'target_name': help_target.character.name if help_target.character else '',
                'action_label': help_action_label,
                'skill_path': help_skill_path,
                'skill_label': CombatService.NARRATIVE_SKILLS.get(help_skill_path, ''),
            })
            if not help_deferred_cost:meta['lastPaidHelpRound']=state.round_number
        character.character.data = data
        flag_modified(character.character, 'data')
        if kind=='help':CombatService._update_helper_penalties(state)
        db.session.commit()
        return CombatService._serialize_state(location, state)

    @staticmethod
    def request_reaction(location_id, user_id, location_character_id):
        location = CombatService._get_location(location_id)
        is_gm = CombatService._ensure_access(location, user_id)
        state = LocationCombatState.query.filter_by(location_id=location_id).first()
        if not state or state.status != 'active':
            raise ValidationError("Combat is not active")
        if state.current_location_character_id == location_character_id:
            raise ValidationError("Use your regular turn instead of a reaction")
        if state.reaction_return_location_character_id is not None:
            raise ValidationError("A reaction is already in progress")
        if state.reaction_pending_location_character_id is not None:
            raise ValidationError("A reaction request is already waiting for the GM")
        character = LocationCharacter.query.filter_by(
            id=location_character_id, location_id=location_id,
        ).first()
        if not character:
            raise NotFoundError("Character not found")
        if not CombatService._can_end_turn_for_character(character, user_id, is_gm=is_gm):
            raise PermissionDenied("You do not control this character")
        CombatService.ensure_character_can_act(character)
        data = character.character.data if isinstance(character.character.data, dict) else {}
        reserve = data.get('health', {}).get('combatMeta', {}).get('reactionReserve')
        if not isinstance(reserve, dict) or not any(
            CombatService._coerce_int(reserve.get(key), 0) > 0
            for key in ('action_points', 'free_actions', 'movement_points')
        ):
            raise ValidationError("No reaction resources are reserved")
        if CombatService._coerce_int(reserve.get('deferred_action_points'), 0) > 0:
            raise ValidationError("Finish paying the help action points first")
        state.reaction_pending_location_character_id = character.id
        db.session.commit()
        return CombatService._serialize_state(location, state)

    @staticmethod
    def resolve_reaction_request(location_id, user_id, approve):
        location = CombatService._get_location(location_id)
        CombatService._ensure_access(location, user_id)
        if location.lobby.gm_id != user_id:
            raise PermissionDenied("Only GM can resolve a reaction request")
        state = LocationCombatState.query.filter_by(location_id=location_id).first()
        if not state or state.status != 'active':
            raise ValidationError("Combat is not active")
        pending_id = state.reaction_pending_location_character_id
        if not pending_id:
            raise ValidationError("There is no reaction request")
        character = LocationCharacter.query.filter_by(id=pending_id, location_id=location_id).first()
        if not character:
            raise NotFoundError("Reacting character not found")
        if not approve:
            state.reaction_pending_location_character_id = None
            db.session.commit()
            return CombatService._serialize_state(location, state)
        if state.current_location_character_id == character.id:
            raise ValidationError("The reacting character already has the turn")
        data = character.character.data if isinstance(character.character.data, dict) else {}
        meta = data.setdefault('health', {}).setdefault('combatMeta', {})
        reserve = meta.pop('reactionReserve', None)
        if not isinstance(reserve, dict):
            raise ValidationError("The reaction reserve is no longer available")
        if reserve.get('kind') == 'help':
            target = LocationCharacter.query.filter_by(
                location_id=location_id,
                character_id=CombatService._coerce_int(reserve.get('target_character_id'), 0),
            ).first()
            if not target or not target.character:
                raise ValidationError("The character receiving help is no longer here")
            target_data = target.character.data if isinstance(target.character.data, dict) else {}
            target_meta = target_data.setdefault('health', {}).setdefault('combatMeta', {})
            target_meta['helpAdvantage'] = {
                'source_character_id': character.character_id,
                'source_name': character.character.name if character.character else '',
                'action_label': str(reserve.get('action_label') or ''),
                'skill_path': str(reserve.get('skill_path') or ''),
                'skill_label': str(reserve.get('skill_label') or ''),
                'round': state.round_number,
                'roll_bonus':2 if background_is(data,'Помощник') else 0,
                'medical_dc_reduction':4 if background_is(data,'Акушер') else 0,
            }
            target.character.data = target_data
            character.character.data = data
            flag_modified(target.character, 'data')
            flag_modified(character.character, 'data')
            state.reaction_pending_location_character_id = None
            db.session.commit()
            return CombatService._serialize_state(location, state)
        state.reaction_return_location_character_id = state.current_location_character_id
        state.reaction_pending_location_character_id = None
        state.current_location_character_id = character.id
        character.action_points_current = max(0, CombatService._coerce_int(reserve.get('action_points'), 0))
        character.free_actions_current = max(0, CombatService._coerce_int(reserve.get('free_actions'), 0))
        character.movement_points_current = max(0, CombatService._coerce_float(reserve.get('movement_points'), 0))
        meta['reactionActive'] = reserve
        character.character.data = data
        flag_modified(character.character, 'data')
        db.session.commit()
        return CombatService._serialize_state(location, state)

    @staticmethod
    @journaled
    def spend_resources(
        location_id,
        user_id,
        location_character_id,
        action_points=0,
        free_actions=0,
        movement_points=0,
        *,
        allow_deferred=False,
        pending_action_id=None,
        pending_action_label=None,
        consumable_selection=None,
    ):
        location = CombatService._get_location(location_id)
        is_gm = CombatService._ensure_access(location, user_id)
        CombatService._release_invalid_grapples(location_id)
        state = LocationCombatState.query.filter_by(location_id=location_id).first()
        if not state:
            raise ValidationError("Combat is not active")
        if state.status != 'active':
            raise ValidationError("Combat is not active")
        character = LocationCharacter.query.filter_by(
            id=location_character_id,
            location_id=location_id,
        ).first()
        if not character:
            raise NotFoundError("Character not found")

        if state.status == 'active' and state.current_location_character_id != character.id:
            raise PermissionDenied("It is not this character's turn")
        if not CombatService._can_end_turn_for_character(character, user_id, is_gm=is_gm):
            raise PermissionDenied('Вы не управляете этим персонажем')
        CombatService.ensure_character_can_act(character)

        if consumable_selection is not None:
            from app.services.deferred_consumable import required_payment
            if not isinstance(pending_action_id, str) or not 1 <= len(pending_action_id) <= 120:
                raise ValidationError('Некорректный идентификатор процедуры')
            from app.models import DeferredCombatAction
            if db.session.get(DeferredCombatAction, pending_action_id):
                from app.services.exceptions import ConflictError
                raise ConflictError('Эта процедура уже принята')
            ensure_no_deferred_action(character)
            _, _, payment = required_payment(character, user_id, consumable_selection)
            try:
                requested_action_points = int(action_points or 0)
            except (TypeError, ValueError):
                requested_action_points = 0
            if requested_action_points < payment['total_action_points']:
                raise ValidationError(
                    f"Недостаточно ОД для применения предмета: требуется {payment['total_action_points']}"
                )

        action_points = max(0, CombatService._coerce_int(action_points, 0))
        free_actions = max(0, CombatService._coerce_int(free_actions, 0))
        movement_points = max(0, CombatService._coerce_float(movement_points, 0))

        deferred = action_points > character.action_points_current
        if deferred and not allow_deferred:
            raise ValidationError("Not enough action points")
        if free_actions > character.free_actions_current:
            raise ValidationError("Not enough free actions")
        if movement_points > character.movement_points_current:
            raise ValidationError("Not enough movement points")

        if deferred:
            ensure_no_deferred_action(character)
            paid_action_points = max(0, character.action_points_current)
            character.action_points_current = 0
            character_data = character.character.data if isinstance(character.character.data, dict) else {}
            health = character_data.setdefault('health', {})
            meta = health.setdefault('combatMeta', {})
            meta['pendingAction'] = {
                'id': str(pending_action_id or f'action-{character.id}-{db.func.now()}'),
                'label': str(pending_action_label or 'Длительное действие'),
                'total_action_points': action_points,
                'remaining_action_points': action_points - paid_action_points,
            }
            meta.pop('completedPendingActionId', None)
            if consumable_selection is not None:
                from app.services.deferred_consumable import remember_consumable
                remember_consumable(character, user_id, meta['pendingAction'], consumable_selection)
            character.character.data = character_data
            flag_modified(character.character, 'data')
        else:
            character.action_points_current -= action_points
            if isinstance(consumable_selection, dict) and consumable_selection.get('server_authoritative') is True:
                character_data = character.character.data if isinstance(character.character.data, dict) else {}
                health = character_data.setdefault('health', {})
                meta = health.setdefault('combatMeta', {})
                completed = {
                    'id': str(pending_action_id),
                    'label': str(pending_action_label or 'Медицинская процедура'),
                    'total_action_points': action_points,
                    'remaining_action_points': 0,
                }
                from app.services.deferred_consumable import remember_consumable
                remember_consumable(character, user_id, completed, consumable_selection, ready=True)
                meta.pop('pendingAction', None)
                meta['completedPendingActionId'] = completed['id']
                character.character.data = character_data
                flag_modified(character.character, 'data')
        character.free_actions_current -= free_actions
        character.movement_points_current -= movement_points
        CombatService._clear_aim(character)
        character.last_action = db.func.now()
        db.session.commit()
        serialized = CombatService._serialize_character(
            character,
            current_turn_id=state.current_location_character_id,
        )
        serialized['payment_complete'] = not deferred
        if deferred:
            serialized['pending_action_id'] = meta['pendingAction']['id']
            serialized['state'] = CombatService.end_turn(
                location_id,
                user_id,
                location_character_id=character.id,
            )
        return serialized

    @staticmethod
    def adjust_resources(location_id, user_id, location_character_id, action_points=0, movement_points=0):
        location = CombatService._get_location(location_id)
        CombatService._ensure_access(location, user_id)
        state = LocationCombatState.query.filter_by(location_id=location_id).first()
        if not state or state.status != 'active':
            raise ValidationError("Combat is not active")
        character = LocationCharacter.query.filter_by(id=location_character_id, location_id=location_id).first()
        if not character:
            raise NotFoundError("Character not found")

        data = character.character.data if character.character and isinstance(character.character.data, dict) else {}
        if state.current_location_character_id != character.id:
            raise PermissionDenied("It is not this character's turn")
        CombatService.ensure_character_can_act(character)
        action_points = max(-10, min(10, CombatService._coerce_int(action_points, 0)))
        movement_points = max(-50, min(50, CombatService._coerce_float(movement_points, 0)))
        character.action_points_current = max(0, character.action_points_current + action_points)
        character.movement_points_current = max(0, character.movement_points_current + movement_points)
        CombatService._release_invalid_grapples(location_id)
        character.last_action = db.func.now()
        db.session.commit()
        return CombatService._serialize_character(character, current_turn_id=state.current_location_character_id)

    @staticmethod
    def _perform_gunpoint_shot(location, state, character, user_id, is_gm):
        if not CombatService._can_end_turn_for_character(character, user_id, is_gm=is_gm):
            raise PermissionDenied('You do not control this character')
        CombatService.ensure_character_can_act(character, 'gunpoint_shot')
        from app.services.sniper_position import require_reposition
        require_reposition(character,state)
        data, combat_meta = CombatService._gunpoint_meta(character)
        if facial_profile(data)['eyes']==2:raise ValidationError('Нельзя стрелять при потере обоих глаз')
        gunpoint = combat_meta.get('gunpoint')
        if not isinstance(gunpoint, dict):
            raise ValidationError('No weapon is currently pressed against a target')
        target = db.session.get(
            LocationCharacter,
            CombatService._coerce_int(gunpoint.get('target_location_character_id'), 0),
        )
        if not target or target.location_id != location.id:
            CombatService._clear_gunpoint(character)
            raise ValidationError('The gunpoint target is no longer in this location')
        if state.current_location_character_id != target.id:
            raise ValidationError('This reaction is available only during the target turn')
        if not CombatService._is_adjacent(character, target):
            CombatService._clear_gunpoint(character)
            raise ValidationError('The target is no longer adjacent')
        if character.action_points_current < 1:
            raise ValidationError('Not enough action points')

        data, weapons, weapon_index, weapon = CombatService._gunpoint_weapon(
            character, gunpoint.get('weapon_index'),
        )
        from app.services.weapon_grip import ensure_usable
        ensure_usable(character, weapon)
        if CombatService._weapon_loaded_shots(weapon) <= 0:
            raise ValidationError('The weapon is not loaded')
        current_round = max(1, state.round_number or 1)
        CombatService._validate_weapon_fire_rate(
            combat_meta, current_round, weapon_index, weapon, 1,data,
        )
        target_zone = gunpoint.get('target_zone')
        if target_zone not in HIT_ZONES:
            CombatService._clear_gunpoint(character)
            raise ValidationError('The selected body part is no longer valid')

        from app.services.character_rules import harm_check
        permission=harm_check(character,target,True)
        if permission and not permission['success']:
            character.action_points_current-=1;flag_modified(character.character,'data');db.session.commit()
            return {'action':'gunpoint_shot','attack':None,'character':CombatService._serialize_character(character),
                'state':CombatService._serialize_state(location,state),'narrative_action':{'name':'Воля перед выстрелом','check':permission,'roll_required':True,'blocked_by_will':True}}

        attack_details = {
            'weapon_index': weapon_index,
            'fire_mode': 'gunpoint',
            'shot_count': 1,
            'requested_shot_count': 1,
            'volley_count': 1,
            'action_points': 1,
            'target_character_id': target.character_id,
            'target_zone': target_zone,
            'target_distance': CombatService._character_distance(character,target),
            'hit_difficulty': 1,
            'automatic_firearm_hit': True,
            'shooting_disadvantage': False,
            'gunpoint': True,
        }
        result = CombatService._resolve_attack(
            target,
            character,
            attack_details,
            aimed_zone=target_zone,
            ignore_cover=True,
            ignore_live_shield=True,
        )
        jam = CombatService._roll_character_weapon_jam(data,weapon,result.get('roll'))
        result['weapon_jam_after_shot'] = deepcopy(jam)
        attack_details.update({
            'results': [result],
            'hits': 1 if result.get('hit') else 0,
            'damage_total': result.get('combined_damage', result.get('damage', 0)),
            'weapon_jam': jam,
            'weapon_jams': [jam] if isinstance(jam, dict) and jam.get('triggered') else [],
        })
        CombatService._consume_weapon_ammo(weapon, 1)
        CombatService._record_weapon_shots(combat_meta, current_round, weapon_index, 1)
        ammo_profile, _ = CombatService._ranged_damage_profile(weapon)
        attack_details['weapon_wear'] = CombatService._weapon_use_wear(
            weapon,
            fire_mode='gunpoint',
            shot_count=1,
            ammo_profile=ammo_profile,
        )
        if CombatService._manual_cycle_type(weapon):
            weapon['requiresManualCycle'] = True
        attack_details['ammo_remaining'] = CombatService._weapon_loaded_shots(weapon)
        combat_meta['firedRound'] = current_round
        combat_meta.pop('gunpoint', None)
        character.action_points_current -= 1
        character.character.data = data
        flag_modified(character.character, 'data')
        character.last_action = db.func.now()
        CombatService._release_invalid_gunpoints(location.id)
        attack_details['noise'],attack_details['noise_targets']=CombatService._apply_fired_noise(location,character,weapon,[result],1)
        db.session.commit()
        serialized_state = CombatService._serialize_state(location, state)
        return {
            'character': CombatService._serialize_character(
                character,
                current_turn_id=state.current_location_character_id,
                combat_state=state,
            ),
            'state': serialized_state,
            'action': 'gunpoint_shot',
            'attack': attack_details,
            'gunpoint': {'released': True, 'target_character_id': target.character_id},
        }

    @staticmethod
    @journaled
    @durable_combat_action
    def perform_action(
        location_id,
        user_id,
        location_character_id,
        action_key,
        weapon_index=None,
        fire_mode=None,
        shot_count=None,
        volley_count=None,
        action_points=None,
        target_character_id=None,
        target_character_ids=None,
        target_object_id=None,
        area_center_x=None,
        area_center_y=None,
        target_x=None,
        target_y=None,
        posture=None,
        facing_x=None,
        facing_y=None,
        attack_type=None,
        target_zone=None,
        payment=None,
        magazine_template_id=None,
        inventory_retrieval_action_points=None,
        inventory_use_action_discount=None,
        attribute_choice=None,
        pending_action_id=None,
        resume_pending_action_id=None,
        narrative_action_name=None,
        narrative_skill_path=None,
        narrative_roll_required=False,
        narrative_difficulty=None,
        item_path=None,
        item_id=None,
        explosive_source=None,
        explosive_fire_mode=None,
        explosive_fuse_mode=None,
        equipment_operation=None,
        equipment_slot=None,
        module_operation=None,
        module_slot_type=None,
        module_target_path=None,
        module_selection=None,
        magazine_selection=None,
        loading=None,
        jump_origin=None,
        target_floor_level=None,
    ):
        deferred_request = snapshot_action_arguments(locals())
        from app.services.character_jump import balance_state
        location = CombatService._get_location(location_id)
        is_gm = CombatService._ensure_access(location, user_id)
        CombatService._release_invalid_grapples(location_id)
        state = LocationCombatState.query.filter_by(location_id=location_id).first()
        character = LocationCharacter.query.filter_by(
            id=location_character_id,
            location_id=location_id,
        ).first()
        if not character:
            raise NotFoundError("Character not found")

        character._trap_end_turn=False
        if action_key in {'scout_location','brigadier_refusal'}:
            if not CombatService._can_end_turn_for_character(character,user_id,is_gm=is_gm):raise PermissionDenied('Вы не управляете персонажем')
            CombatService.ensure_character_can_act(character)
            if action_key=='scout_location':
                from app.services.location_scout import inspect
                details=inspect(character,attribute_choice)
            else:
                from app.services.brigadier_order import refusal
                details=refusal(character,target_character_id,attribute_choice);db.session.commit()
            return {'action':action_key,'narrative_action':details,'character':CombatService._serialize_character(character),'state':CombatService.get_state(location_id,user_id)}

        if action_key=='choose_sniper_trauma':
            from app.services.sniper_trauma import decide
            choice_result=decide(character,user_id,attribute_choice)
            return {'character':CombatService._serialize_character(character),'state':CombatService.get_state(location_id,user_id),
                'action':action_key,'narrative_action':{'name':'Микроскопическая точность','roll_required':False,**choice_result}}

        if (not state or state.status != 'active') and action_key == 'mutant_ambush':
            if not CombatService._can_end_turn_for_character(
                character, user_id, is_gm=is_gm,
            ):
                raise PermissionDenied('Вы не управляете этой Жмуркой')
            data = (
                character.character.data
                if character.character and isinstance(character.character.data, dict)
                else {}
            )
            CombatService.ensure_character_can_act(character, action_key)
            if not CombatService._mutant_has_trait(data, 'Засада'):
                raise ValidationError('Этому мутанту недоступна засада')
            if not CombatService._tile_has_bush(location, character.pos_x, character.pos_y):
                raise ValidationError('Для засады Жмурка должна находиться в кусте')
            data.setdefault('health', {}).setdefault('combatMeta', {})[
                'mutantAmbushActive'
            ] = True
            character.character.data = data
            flag_modified(character.character, 'data')
            state = state or CombatService._get_or_create_state(location_id)
            db.session.commit()
            return {
                'character': CombatService._serialize_character(character),
                'state': CombatService._serialize_state(location, state),
                'action': action_key,
                'mutant_action': {
                    'kind': 'ambush', 'detection_difficulty_bonus': 5,
                },
            }
        if (not state or state.status != 'active') and action_key=='brigadier_order':
            if not CombatService._can_end_turn_for_character(character,user_id,is_gm=is_gm):raise PermissionDenied('Вы не управляете Бригадиром')
            CombatService.ensure_character_can_act(character)
            from app.services.brigadier_order import prepare,create
            plan=prepare(character,target_character_id,attribute_choice)
            details={'name':'Указание Бригадира','order':create(character,user_id,*plan),'roll_required':False}
            db.session.commit()
            return {'action':action_key,'narrative_action':details,'character':CombatService._serialize_character(character),'state':CombatService.get_state(location_id,user_id)}
        if (not state or state.status != 'active') and action_key=='surgeon_reduction':
            if not CombatService._can_end_turn_for_character(character,user_id,is_gm=is_gm):
                raise PermissionDenied('Вы не управляете врачом')
            CombatService.ensure_character_can_act(character)
            from app.services.surgeon_reduction import prepare,apply
            if not isinstance(attribute_choice,dict) or set(attribute_choice)-{'consentId'}:raise ValidationError('Некорректное согласие пациента')
            plan=prepare(location,character,target_character_id,target_zone,attribute_choice.get('consentId'),user_id)
            result=apply(character,plan,user_id);db.session.commit()
            return {'character':CombatService._serialize_character(character),'state':CombatService.get_state(location_id,user_id),
                    'action':action_key,'narrative_action':result}
        if (not state or state.status != 'active') and action_key in {'jump','recover_jump_balance'}:
            if not CombatService._can_end_turn_for_character(character,user_id,is_gm=is_gm):
                raise PermissionDenied('Вы не управляете этим персонажем')
            from app.services.character_jump import prepare_jump,execute_jump,recover_balance
            details = (execute_jump(location,character,prepare_jump(location,character,target_x,target_y),0)
                if action_key=='jump' else recover_balance(location,character,0))
            character.last_action = db.func.now()
            db.session.commit()
            return {'character':CombatService._serialize_character(character),
                    'state':CombatService.get_state(location_id,user_id),'action':action_key,'jump':details}
        if (not state or state.status != 'active') and action_key == 'throw_item':
            if not CombatService._can_end_turn_for_character(character, user_id, is_gm=is_gm):
                raise PermissionDenied('Вы не управляете этим персонажем')
            CombatService.ensure_character_can_act(character)
            if not isinstance(item_path, list) or not item_path or item_path[0] != 'inventory':
                raise ValidationError('Метать оружие вне боя нельзя')
            from app.services.thrown_items import prepare as prepare_throw, execute as execute_throw
            data = character.character.data if character.character and isinstance(character.character.data, dict) else {}
            plan = prepare_throw(location, character, data, item_path, item_id, target_x, target_y,
                target_floor_level, attack_type)
            tile_x = min(location.grid_width - 1, int(plan['target_x'] + .5))
            tile_y = min(location.grid_height - 1, int(plan['target_y'] + .5))
            occupied = any(other.id != character.id and (other.floor_level or 0) == plan['floor_level']
                and other.pos_x == tile_x and other.pos_y == tile_y
                for other in LocationCharacter.query.filter_by(location_id=location_id).all())
            if occupied:
                raise ValidationError('Для броска в персонажа начните бой')
            details = execute_throw(location, character, data, plan, 0)
            character.character.data = data
            flag_modified(character.character, 'data')
            character.last_action = db.func.now()
            db.session.commit()
            return {'action': action_key, 'thrown_item': details,
                'character': CombatService._serialize_character(character),
                'state': CombatService.get_state(location_id, user_id)}
        if (not state or state.status!='active') and action_key=='flamethrower_service':
            if not CombatService._can_end_turn_for_character(character,user_id,is_gm=is_gm):raise PermissionDenied('Вы не управляете персонажем')
            CombatService.ensure_character_can_act(character)
            from app.services.flamethrower import service_plan,service
            details=service(character,service_plan(character,weapon_index,item_path,equipment_slot,equipment_operation))
            details['action_points']=0;db.session.commit()
            return {'action':action_key,'narrative_action':details,'character':CombatService._serialize_character(character),'state':CombatService.get_state(location_id,user_id)}
        if not state or state.status != 'active':
            raise ValidationError("Combat is not active")

        CombatService._refresh_ugly_appearance_penalties(location_id)
        from app.services.explosion_choice import require_resolved
        require_resolved(state)
        from app.services.sniper_trauma import require_resolved as require_trauma
        require_trauma(state)
        if not CombatService._can_end_turn_for_character(character, user_id, is_gm=is_gm):
            raise PermissionDenied('Вы не управляете этим персонажем')
        CombatService._refresh_mutant_pack_bonus(character)
        from app.services.character_rules import refresh_nearby
        refresh_nearby(location_id)

        data = (
            character.character.data
            if character.character and isinstance(character.character.data, dict)
            else {}
        )

        if action_key == 'gunpoint_shot':
            from app.services.body_transport import ensure_firearm_allowed
            ensure_firearm_allowed(character)
            return CombatService._perform_gunpoint_shot(
                location, state, character, user_id, is_gm,
            )

        if state.current_location_character_id != character.id:
            raise PermissionDenied("It is not this character's turn")
        CombatService.ensure_character_can_act(character, action_key)
        from app.services.weapon_grip import pair,held,revolver
        paired=pair(character)
        if paired and action_key in {'reload_weapon','reload_underbarrel','clear_weapon_jam','load_ammunition'}:
            raise ValidationError('При двух оружиях в руках нельзя перезаряжать, устранять клин или передёргивать затвор')
        if paired and action_key=='attack':
            if weapon_index is not None and not held(character,CombatService._coerce_int(weapon_index,-1)):
                raise ValidationError('Для атаки выберите одно из двух удерживаемых оружий')
            selected=(data.get('weapons') or [])[CombatService._coerce_int(weapon_index,0)] if weapon_index is not None and 0<=CombatService._coerce_int(weapon_index,-1)<len(data.get('weapons') or []) else {}
            if revolver(selected) and fire_mode in {'burst','area','suppression'}:
                raise ValidationError('При парном хвате револьвером нельзя стрелять очередью')
        if action_key in {'attack','aim','brace_weapon','place_gunpoint','gunpoint_shot'} or action_key=='explosive_attack' and explosive_source!='hand':
            grip_index = CombatService._coerce_int(weapon_index, -1)
            grip_weapons = data.get('weapons') or []
            if 0 <= grip_index < len(grip_weapons):
                from app.services.weapon_grip import ensure_usable
                ensure_usable(character, grip_weapons[grip_index])
        if action_key in {'aim','reload_weapon','reload_underbarrel','clear_weapon_jam','brace_weapon','place_gunpoint'}:
            from app.services.body_transport import ensure_firearm_allowed
            ensure_firearm_allowed(character)
        if action_key == 'draw_weapon':
            index = CombatService._coerce_int(weapon_index, -1)
            weapons = data.get('weapons') or []
            if 0 <= index < len(weapons):
                template = CombatService._weapon_template(weapons[index])
                category = (template.category if template else None) or weapons[index].get('category')
                if category != 'melee_weapon':
                    from app.services.body_transport import ensure_firearm_allowed
                    ensure_firearm_allowed(character)

        health = data.setdefault('health', {})
        combat_meta = health.setdefault('combatMeta', {})
        resumed_paid_action = bool(
            resume_pending_action_id
            and str(combat_meta.get('completedPendingActionId') or '') == str(resume_pending_action_id)
        )
        if resume_pending_action_id and not resumed_paid_action:
            raise ValidationError("The pending action has not been paid yet")

        action = next((item for item in ACTION_CATALOG if item['key'] == action_key), None)
        if action_key=='place_cover':
            action={'key':'place_cover','label':'Установка укрытия','action_points':5,'free_actions':0,'movement_points':0}
        if action_key=='trap':action={'key':'trap','label':'Ловушка','action_points':5,'free_actions':0,'movement_points':0}
        if not action:
            raise ValidationError("Unknown action")

        attack_details = None
        aim_details = None
        posture_details = None
        facing_details = None
        draw_details = None
        reload_details = None
        underbarrel_reload_details = None
        clear_jam_details = None
        narrative_action_details = None
        must_do_details = None
        consolation_details = None
        cover_details = None
        brace_details = None
        explosive_details = None
        thrown_details = None
        explosive_item = None
        explosive_weapon = None
        explosive_module = None
        explosive_fire_rate_state = None
        melee_action_details = None
        equipment_action_details = None
        equipment_module_details = None
        gunpoint_details = None
        anomaly_details = None
        mutant_action_details = None
        jump_details = None
        special_action_cost = None
        flame_details = None
        resolved_hits = []
        single_fire = False
        prepared_attacks = None
        current_round = max(1, state.round_number or 1)
        order_plan=None
        reduction_plan=None
        cover_placement=None
        if action_key=='trap':
            from app.services.traps import prepare as trap_plan
            trap_prepared=trap_plan(character,data,attribute_choice,user_id)
            special_action_cost=trap_prepared['action_points']
            action['label']={'place':'Установка ловушки','mask':'Маскировка ловушки','defuse':'Обезвреживание ловушки','recover':'Подбор ловушки','detect':'Поиск ловушек','escape_net':'Освободиться из сети','cut_net':'Разрезать сеть'}[trap_prepared['operation']]
        if action_key=='place_cover':
            from app.services.crafted_covers import plan as cover_plan
            cover_placement=cover_plan(character,data,attribute_choice)
            special_action_cost=cover_placement['action_points']
            action['label']={'place':'Установка укрытия','move':'Перенос укрытия','recover':'Снятие укрытия'}[cover_placement['operation']]
        if action_key=='evade':special_action_cost=2 if trait_count(data,'Уклонист') else 4
        if action_key=='throw_item':
            from app.services.thrown_items import prepare as prepare_throw
            thrown_details=prepare_throw(location,character,data,item_path,item_id,target_x,target_y,
                target_floor_level,attack_type)
            special_action_cost=thrown_details['action_points']
        if action_key=='brigadier_order':
            from app.services.brigadier_order import prepare
            order_plan=prepare(character,target_character_id,attribute_choice)
            special_action_cost=4 if order_plan[1]['detailed'] else 2
        if action_key=='surgeon_reduction':
            from app.services.surgeon_reduction import prepare
            if not isinstance(attribute_choice,dict) or set(attribute_choice)-{'consentId'}:raise ValidationError('Некорректное согласие пациента')
            reduction_plan=prepare(location,character,target_character_id,target_zone,attribute_choice.get('consentId'),user_id)
        if action_key in {'jump','recover_jump_balance'}:
            from app.services.character_jump import prepare_jump, balance_state
            if action_key == 'jump':
                jump_details = prepare_jump(location,character,target_x,target_y,jump_origin)
                deferred_request['jump_origin'] = jump_details['origin']
            elif not balance_state(character):
                raise ValidationError('Персонаж уже находится в устойчивом положении')
        if action_key in {'change_facing','escape_anomaly'}:
            from app.services.character_jump import require_balance
            require_balance(character)
        if action_key == 'mutant_jump':
            if not CombatService._mutant_has_trait(data, 'Прыгун'):
                raise ValidationError('Этому мутанту недоступен прыжок')
            target_x = CombatService._coerce_int(target_x, -1)
            target_y = CombatService._coerce_int(target_y, -1)
            if not (0 <= target_x < location.grid_width and 0 <= target_y < location.grid_height):
                raise ValidationError('Выберите клетку на карте')
            distance = math.hypot(target_x - character.pos_x, target_y - character.pos_y)
            if distance <= 0 or distance > 15:
                raise ValidationError('Дальность прыжка ограничена 15 метрами')
            blocked_tiles, climb_tiles = CombatService._build_movement_map(
                location, character.character_id,
            )
            if (target_x, target_y) in blocked_tiles:
                raise ValidationError('Клетка приземления занята')
            special_action_cost = 4
            mutant_action_details = {
                'kind': 'jump', 'target_x': target_x, 'target_y': target_y,
                'distance': round(distance, 2), 'action_points': 4,
            }
        elif action_key == 'mutant_ambush':
            if not CombatService._mutant_has_trait(data, 'Засада'):
                raise ValidationError('Этому мутанту недоступна засада')
            if not CombatService._tile_has_bush(location, character.pos_x, character.pos_y):
                raise ValidationError('Для засады Жмурка должна находиться в кусте')
            special_action_cost = 0
            mutant_action_details = {
                'kind': 'ambush', 'detection_difficulty_bonus': 5,
            }
        elif action_key == 'mutant_battle_cry':
            if not any(
                CombatService._template_attributes(weapon).get('special_action') == 'mutant_battle_cry'
                or 'клич' in str(weapon.get('name') or '').casefold()
                for weapon in (data.get('weapons') or []) if isinstance(weapon, dict)
            ):
                raise ValidationError('Этому мутанту недоступен боевой клич')
            special_action_cost = CombatService._mutant_first_attack_cost(
                character, {'action_points': 2, 'raw_effect': '2 ОД'}, current_round,
            )
            mutant_action_details = {
                'kind': 'battle_cry', 'difficulty': 15,
                'action_points': special_action_cost, 'targets': [],
            }
        elif action_key == 'mutant_camouflage':
            if not CombatService._mutant_has_trait(data, 'Маскировка'):
                raise ValidationError('Этому мутанту недоступна маскировка')
            last_round = CombatService._coerce_int(
                combat_meta.get('mutantCamouflageUsedRound'), -100,
            )
            if current_round - last_round < 4:
                raise ValidationError('Маскировка восстанавливается раз в 4 раунда')
            special_action_cost = 1
            mutant_action_details = {
                'kind': 'camouflage',
                'action_points': 1,
                'until_round': current_round + 2,
            }
        elif action_key == 'mutant_psy_attack':
            weapons = data.get('weapons') if isinstance(data.get('weapons'), list) else []
            weapon_index = CombatService._coerce_int(weapon_index, -1)
            if not 0 <= weapon_index < len(weapons):
                raise ValidationError('Пси-атака не найдена')
            weapon = weapons[weapon_index] if isinstance(weapons[weapon_index], dict) else {}
            attack_name = str(weapon.get('name') or '')
            if attack_name.casefold() not in {
                'пси-удар', 'зомбирование', 'приказ смерти',
            }:
                raise ValidationError('Выбранная атака не является пси-атакой Псионика')
            target = LocationCharacter.query.filter_by(
                location_id=location_id,
                character_id=target_character_id,
            ).first()
            if not target or target.id == character.id:
                raise ValidationError('Выберите цель пси-атаки')
            if not CombatService._mutant_has_direct_sight(location_id, character, target):
                raise ValidationError('Для пси-атаки нужна прямая видимость')
            raw_effect = str(CombatService._template_attributes(weapon).get('raw_effect') or '')
            cost_match = re.search(r'(\d+)\s*ОД', raw_effect, re.IGNORECASE)
            special_action_cost = int(cost_match.group(1)) if cost_match else 0
            mutant_action_details = {
                'kind': 'psy_attack',
                'attack_name': attack_name,
                'weapon_index': weapon_index,
                'target': target,
                'target_character_id': target.character_id,
                'action_points': special_action_cost,
            }
        elif action_key == 'mutant_psy_roar':
            if not CombatService._mutant_has_trait(data, 'Пси-рёв'):
                raise ValidationError('Этому мутанту недоступен Пси-рёв')
            special_action_cost = 3
            mutant_action_details = {
                'kind': 'psy_roar',
                'action_points': 3,
                'targets': [],
            }
        elif action_key == 'mutant_psy_sleep':
            if not CombatService._mutant_has_trait(data, 'Сон'):
                raise ValidationError('Этому мутанту недоступен Пси-сон')
            target = LocationCharacter.query.filter_by(
                location_id=location_id,
                character_id=target_character_id,
            ).first()
            if not target or target.id == character.id:
                raise ValidationError('Выберите цель Пси-сна')
            if math.hypot(target.pos_x - character.pos_x, target.pos_y - character.pos_y) > 10:
                raise ValidationError('Дальность Пси-сна ограничена 10 метрами')
            if not CombatService._mutant_has_direct_sight(location_id, character, target):
                raise ValidationError('Для Пси-сна нужна прямая видимость')
            special_action_cost = 4
            mutant_action_details = {
                'kind': 'psy_sleep',
                'target': target,
                'target_character_id': target.character_id,
                'action_points': 4,
            }
        elif action_key == 'mutant_object_attack':
            weapons = data.get('weapons') if isinstance(data.get('weapons'), list) else []
            weapon_index = CombatService._coerce_int(weapon_index, -1)
            if not 0 <= weapon_index < len(weapons):
                raise ValidationError('Атака Аномальной души не найдена')
            weapon = weapons[weapon_index] if isinstance(weapons[weapon_index], dict) else {}
            attack_name = str(weapon.get('name') or '').strip()
            object_profiles = {
                'бросок': {
                    'damage_multiplier': 50, 'base_cost': 6,
                    'difficulty': 14, 'damage_type': 'crushing',
                },
                'огненный снаряд': {
                    'damage_multiplier': 40, 'base_cost': 5,
                    'difficulty': 12, 'damage_type': 'thermal',
                    'burning': True,
                },
                'электрический снаряд': {
                    'damage_multiplier': 25, 'base_cost': 3,
                    'difficulty': 16, 'damage_type': 'electric',
                    'maximum_weight': 5,
                },
                'химический снаряд': {
                    'damage_multiplier': 75, 'base_cost': 2,
                    'difficulty': 12, 'damage_type': 'chemical',
                    'maximum_range': 5, 'armor_damage_multiplier': 2,
                },
            }
            object_profile = object_profiles.get(attack_name.casefold())
            if not object_profile:
                raise ValidationError('Выбранная атака не использует объект окружения')
            target = LocationCharacter.query.filter_by(
                location_id=location_id,
                character_id=target_character_id,
            ).first()
            if not target or target.id == character.id:
                raise ValidationError('Выберите цель атаки')
            target_distance = math.hypot(
                target.pos_x - character.pos_x,
                target.pos_y - character.pos_y,
            )
            if (
                object_profile.get('maximum_range') is not None
                and target_distance > object_profile['maximum_range']
            ):
                raise ValidationError(
                    f"Дальность этой атаки ограничена {object_profile['maximum_range']} метрами"
                )
            thrown_object, object_weight = CombatService._nearest_throwable_object(
                location_id,
                target,
                maximum_weight=object_profile.get('maximum_weight'),
            )
            if not thrown_object:
                raise ValidationError('Рядом с целью нет подходящего объекта для броска')
            special_action_cost = object_profile['base_cost'] + math.ceil(object_weight)
            mutant_action_details = {
                'kind': 'object_attack',
                'attack_name': attack_name,
                'weapon_index': weapon_index,
                'target': target,
                'target_character_id': target.character_id,
                'target_name': target.character.name if target.character else 'Цель',
                'target_distance': target_distance,
                'object': thrown_object,
                'object_id': thrown_object.id,
                'object_name': thrown_object.name or thrown_object.type,
                'object_weight': object_weight,
                'profile': object_profile,
                'action_points': special_action_cost,
            }
        elif action_key == 'mutant_anomaly_shield':
            weapons = data.get('weapons') if isinstance(data.get('weapons'), list) else []
            if not any(
                str(item.get('name') or '').strip().casefold() == 'щит'
                for item in weapons if isinstance(item, dict)
            ):
                raise ValidationError('Этому мутанту недоступен Аномальный щит')
            special_action_cost = 3
            mutant_action_details = {
                'kind': 'anomaly_shield',
                'physical_protection': 80,
                'action_points': 3,
            }
        elif action_key == 'mutant_steal_device':
            if not CombatService._mutant_has_trait(data, 'Вор'):
                raise ValidationError('Этому мутанту недоступна кража устройств')
            target = LocationCharacter.query.filter_by(
                location_id=location_id,
                character_id=target_character_id,
            ).first()
            if not target or target.id == character.id:
                raise ValidationError('Выберите цель кражи')
            if not CombatService._is_adjacent(character, target):
                raise ValidationError('Для кражи нужно находиться на соседней клетке')
            target_data = (
                target.character.data
                if target.character and isinstance(target.character.data, dict)
                else {}
            )
            device = CombatService._inventory_item_at_path(target_data, item_path)
            if not CombatService._is_stealable_electronic(device):
                raise ValidationError('Можно украсть только Детектор, Рацию, ПНВ или КПК')
            special_action_cost = 2
            mutant_action_details = {
                'kind': 'steal_device',
                'target': target,
                'target_character_id': target.character_id,
                'target_name': target.character.name if target.character else 'Цель',
                'item_path': list(item_path or []),
                'item_name': device.get('name') or 'Устройство',
                'action_points': 2,
            }
        elif action_key == 'mutant_clone':
            clone_trait = next((
                trait for trait in CombatService._mutant_traits(data)
                if 'Клонирование' in trait
            ), None)
            if not clone_trait:
                raise ValidationError('Этому мутанту недоступно клонирование')
            limit_match = re.search(r'До\s+(\d+)\s+коп', clone_trait, re.IGNORECASE)
            psy_match = re.search(r'наносит\s+(\d+)\s+Пси', clone_trait, re.IGNORECASE)
            clone_limit = int(limit_match.group(1)) if limit_match else 1
            clone_psy_damage = int(psy_match.group(1)) if psy_match else 0
            active_clones = [
                item for item in CombatService._unique_location_characters(
                    LocationCharacter.query.filter_by(location_id=location_id).all()
                )
                if item.character
                and isinstance(item.character.data, dict)
                and item.character.data.get('basic', {}).get('psi_clone_source_id') == character.character_id
                and CombatService._location_character_condition(item)['state'] != 'dead'
            ]
            if len(active_clones) >= clone_limit:
                raise ValidationError(f'Одновременно доступно не больше {clone_limit} копий')
            blocked, _ = CombatService._build_movement_map(
                location, character.character_id,
            )
            spawn_tile = next((
                (character.pos_x + dx, character.pos_y + dy)
                for dx, dy in ((0, 1), (1, 0), (0, -1), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1))
                if (
                    0 <= character.pos_x + dx < location.grid_width
                    and 0 <= character.pos_y + dy < location.grid_height
                    and (character.pos_x + dx, character.pos_y + dy) not in blocked
                )
            ), None)
            if not spawn_tile:
                raise ValidationError('Рядом нет свободной клетки для копии')
            special_action_cost = 1
            mutant_action_details = {
                'kind': 'clone',
                'clone_limit': clone_limit,
                'clone_psy_damage': clone_psy_damage,
                'spawn_tile': spawn_tile,
                'action_points': 1,
            }
        if action_key == 'escape_anomaly':
            _, _, _, active_anomaly = CombatService._active_anomaly(character)
            if not active_anomaly:
                raise ValidationError('The character is not trapped in an anomaly')
            target_x = CombatService._coerce_int(target_x, -1)
            target_y = CombatService._coerce_int(target_y, -1)
            if max(abs(target_x - character.pos_x), abs(target_y - character.pos_y)) != 1:
                raise ValidationError('Choose an adjacent exit tile')
            if not (0 <= target_x < location.grid_width and 0 <= target_y < location.grid_height):
                raise ValidationError('Choose a valid exit tile')
            blocked_tiles, climb_tiles = CombatService._build_movement_map(
                location, character.character_id,
            )
            if (target_x, target_y) in blocked_tiles or climb_tiles.get((target_x, target_y), 0) > 0:
                raise ValidationError('The exit tile is blocked')
            if CombatService._anomalies_at_tile(location, target_x, target_y):
                raise ValidationError('Choose a tile outside an anomaly')
            profile = anomaly_profile(active_anomaly.get('key')) or {}
            skill_path = (
                'skills.physical.will'
                if profile.get('category') == 'psi'
                else 'skills.physical.agility'
            )
            check = CombatService._narrative_skill_check(data, skill_path)
            medicine_bonus = CombatService._consumable_stat_value_bonus(data, 'anomaly_escape_roll')
            check['modifier'] += medicine_bonus
            check['total'] += medicine_bonus
            check['medicine_bonus'] = medicine_bonus
            if background_is(data,'Трюкач'):
                check['modifier']+=4;check['total']+=4;check['trickster_bonus']=4
            long_jump_bonus = (
                4 if CombatService._mutant_has_trait(data, 'Длинный прыжок') else 0
            )
            if long_jump_bonus:
                check['modifier'] = CombatService._coerce_int(
                    check.get('modifier'), 0,
                ) + long_jump_bonus
                check['total'] = CombatService._coerce_int(
                    check.get('total'), 0,
                ) + long_jump_bonus
                check['mutant_long_jump_bonus'] = long_jump_bonus
            difficulty_key = (
                'secondary_dc'
                if active_anomaly.get('key') == 'kapkan'
                and CombatService._coerce_int(active_anomaly.get('rounds'), 0) >= 1
                else 'dc'
            )
            difficulty = max(1, CombatService._coerce_int(
                profile.get(difficulty_key), active_anomaly.get('dc', 10),
            ))
            from app.services.equipment_modifications import worn_rule
            difficulty=max(1,difficulty-worn_rule(data,'anomaly_escape_reduction'))
            margin = difficulty - check['total']
            success = check['roll'] == 20 or (check['roll'] != 1 and margin <= 0)
            if success:
                fraction, exits, outcome = 0.0, True, 'success'
            elif check['roll'] == 1:
                fraction, exits, outcome = 1.25, False, 'critical_failure'
            elif margin < 4:
                fraction, exits, outcome = 0.25, True, 'partial_exit'
            elif margin < 6:
                fraction, exits, outcome = 0.5, False, 'failure'
            else:
                fraction, exits, outcome = 1.0, False, 'severe_failure'
            check.update({'difficulty': difficulty, 'success': success, 'margin': margin})
            anomaly_details = {
                'key': active_anomaly.get('key'), 'name': active_anomaly.get('name'),
                'category': profile.get('category'), 'check': check, 'outcome': outcome,
                'fraction': fraction, 'exits': exits, 'target_x': target_x, 'target_y': target_y,
                'condition': str(attribute_choice or '').strip().lower() or None,
            }
            special_action_cost = 3
        elif action_key == 'place_gunpoint':
            if not CombatService._can_end_turn_for_character(
                character, user_id, is_gm=is_gm,
            ):
                raise PermissionDenied('You do not control this character')
            target = LocationCharacter.query.filter_by(
                location_id=location_id,
                character_id=target_character_id,
            ).first()
            if not target or target.id == character.id:
                raise ValidationError('Choose another character')
            if not CombatService._is_adjacent(character, target):
                raise ValidationError('Цель должна находиться на соседней клетке')
            if target_zone not in HIT_ZONES:
                raise ValidationError('Choose a body part')
            _, _, gunpoint_weapon_index, gunpoint_weapon = CombatService._gunpoint_weapon(
                character,
            )
            if CombatService._weapon_loaded_shots(gunpoint_weapon) <= 0:
                raise ValidationError('The weapon is not loaded')
            failed_round = CombatService._coerce_int(
                combat_meta.get('gunpointFailedRound'), 0,
            )
            if failed_round == current_round:
                raise ValidationError('A failed gunpoint attempt cannot be repeated this round')
            agility_bonus = CombatService._skill_modifier(
                data, 'skills.physical.agility',
            )
            melee_bonus = CombatService._skill_modifier(
                data, 'skills.physical.melee',
            )
            weapon_modifier = CombatService._gunpoint_weapon_difficulty_modifier(
                gunpoint_weapon,
            )
            base_difficulty = 15 if target_zone == 'head' else 12
            difficulty = max(
                1,
                base_difficulty - agility_bonus - melee_bonus + weapon_modifier-2*trait_count(data,'Живой щит'),
            )
            from app.services.brigadier_order import boost_roll
            _rolls,roll,order_bonus=boost_roll(data,[])
            success = roll == 20 or (roll != 1 and roll+order_bonus >= difficulty)
            special_action_cost = 3 if target_zone == 'head' else 2
            gunpoint_details = {
                'target_location_character_id': target.id,
                'target_character_id': target.character_id,
                'target_name': target.character.name if target.character else None,
                'target_zone': target_zone,
                'weapon_index': gunpoint_weapon_index,
                'weapon_name': gunpoint_weapon.get('name'),
                'round': current_round,
                'roll': roll,
                'difficulty': difficulty,
                'success': success,
                'agility_bonus': agility_bonus,
                'melee_bonus': melee_bonus,
                'weapon_difficulty_modifier': weapon_modifier,
                'action_points': special_action_cost,
            }
        elif action_key in {'flamethrower_fire','flamethrower_service'}:
            from app.services.flamethrower import prepare,service_plan
            flame_details=(prepare(character,weapon_index,fire_mode,target_x,target_y,target_character_id,floor_level=target_floor_level,is_gm=is_gm)
                if action_key=='flamethrower_fire' else service_plan(character,weapon_index,item_path,equipment_slot,equipment_operation))
            special_action_cost=flame_details['action_points']
            if action_key=='flamethrower_fire':
                from app.services.sniper_position import require_reposition
                require_reposition(character,state)
        elif action_key == 'explosive_attack':
            source = str(explosive_source or '').strip().lower()
            mode = str(explosive_fire_mode or 'unaimed').strip().lower()
            fuse_mode = str(explosive_fuse_mode or 'normal').strip().lower()
            if source not in {'hand', 'underbarrel', 'weapon'}:
                raise ValidationError("Unknown explosive source")
            if source!='hand':
                from app.services.sniper_position import require_reposition
                require_reposition(character,state)
            if source!='hand' and facial_profile(data)['eyes']==2:raise ValidationError('Нельзя стрелять при потере обоих глаз')
            if mode not in {'unaimed', 'aimed'}:
                raise ValidationError("Unknown grenade launcher fire mode")
            if fuse_mode not in {'normal', 'delay'}:
                raise ValidationError("Unknown grenade fuse mode")
            if source != 'hand' and fuse_mode != 'normal':
                raise ValidationError("Only a hand-thrown grenade can be delayed")
            if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (target_x,target_y)):
                raise ValidationError("Choose a valid target point")
            target_x,target_y = float(target_x),float(target_y)
            if not (0 <= target_x <= location.grid_width-1 and 0 <= target_y <= location.grid_height-1):
                raise ValidationError("Choose a valid target point")
            target_floor_level=(character.floor_level or 0) if target_floor_level is None else target_floor_level
            if type(target_floor_level) is not int:
                raise ValidationError('Некорректный этаж точки попадания')
            from app.services.explosion_geometry import landing_level
            landing_objects=LocationObject.query.filter_by(location_id=location_id).all()
            from app.services.location_navigation import level_of
            available_levels={0,character.floor_level or 0}|{level_of(o) for o in landing_objects}
            if target_floor_level not in available_levels:
                raise ValidationError('На выбранном этаже нет поверхности приземления')
            target_floor_level=landing_level(location,landing_objects,target_x,target_y,target_floor_level)
            if target_floor_level is None:
                raise ValidationError('На выбранном этаже нет поверхности приземления')
            if not CombatService._is_in_facing_arc(character, target_x, target_y):
                raise ValidationError("The target point is outside the character's field of view")

            weapons = data.get('weapons') if isinstance(data.get('weapons'), list) else []
            weapon_range = 0
            weapon_accuracy = 0
            launcher_strength = {'accuracy_penalty': 0}
            launcher_ergonomics = {'accuracy_modifier': 0}
            launcher_movement = {'difficulty_penalty': 0, 'disadvantage': False}
            launcher_jam = None
            if source == 'hand':
                from app.services.inventory_access import calculate_inventory_access

                explosive_item = CombatService._inventory_item_at_path(data, item_path)
                access = calculate_inventory_access(data, item_path, explosive_item)
                retrieval_cost = int(access['retrieval_action_points'])
                use_discount = int(access['use_action_discount'])
                special_action_cost = max(0, 2 - use_discount) + retrieval_cost
                if fuse_mode == 'delay':
                    special_action_cost += 2
            else:
                weapon_index = CombatService._coerce_int(weapon_index, -1)
                if weapon_index < 0 or weapon_index >= len(weapons):
                    raise ValidationError("Weapon not found")
                if not held(character,weapon_index):
                    raise ValidationError("Draw this weapon first")
                from app.services.body_transport import ensure_firearm_allowed
                ensure_firearm_allowed(character)
                explosive_weapon = weapons[weapon_index]
                weapon_attributes = CombatService._template_attributes(explosive_weapon)
                CombatService._weapon_durability(explosive_weapon)
                launcher_jam = (
                    explosive_weapon.get('jam')
                    if isinstance(explosive_weapon.get('jam'), dict)
                    else None
                )
                if launcher_jam and launcher_jam.get('blocks_fire'):
                    raise ValidationError("Clear the weapon jam before firing")
                launcher_strength = CombatService._weapon_strength_profile(
                    character, explosive_weapon,
                )
                launcher_ergonomics = CombatService._weapon_ergonomics_profile(
                    character, explosive_weapon, weapon_index,
                )
                launcher_movement = CombatService._shooting_movement_modifiers(
                    character.movement_mode_this_turn, None,character_data=data,
                )
                if source == 'underbarrel':
                    explosive_module = next((
                        module for module in (explosive_weapon.get('installedModules') or [])
                        if isinstance(module, dict)
                        and (module.get('attributes') or {}).get('type') == 'grenade_launcher'
                    ), None)
                    if not explosive_module or not explosive_module.get('loadedGrenade'):
                        raise ValidationError("The underbarrel grenade launcher is not loaded")
                    if (explosive_module.get('attributes') or {}).get('assault_rifle_only') and not re.search(r'штурмов|assault',CombatService._weapon_subcategory(explosive_weapon)):
                        raise ValidationError('Подствольный гранатомёт доступен только для штурмовых винтовок')
                    explosive_item = explosive_module.get('loadedGrenade')
                    module_attributes = explosive_module.get('attributes') or {}
                    weapon_range = CombatService._coerce_float(module_attributes.get('range'), 0)
                    weapon_accuracy = CombatService._coerce_int(
                        module_attributes.get(
                            'accuracy',
                            explosive_weapon.get('accuracy', weapon_attributes.get('accuracy')),
                        ),
                        0,
                    )
                else:
                    template = CombatService._weapon_template(explosive_weapon)
                    subcategory = str(
                        (template.subcategory if template else None)
                        or explosive_weapon.get('subcategory')
                        or ''
                    ).lower()
                    if '\u0433\u0440\u0430\u043d\u0430\u0442\u043e\u043c' not in subcategory:
                        raise ValidationError("The selected weapon is not a grenade launcher")
                    magazine = explosive_weapon.get('installedMagazine') or {}
                    stacks = magazine.get('ammo') if isinstance(magazine, dict) else None
                    if not isinstance(stacks, list):
                        stacks = explosive_weapon.get('fixedAmmo')
                    explosive_item = next((
                        stack for stack in reversed(stacks or [])
                        if isinstance(stack, dict) and CombatService._coerce_int(stack.get('quantity'), 0) > 0
                    ), None)
                    if not explosive_item:
                        raise ValidationError("The grenade launcher is not loaded")
                    weapon_range = CombatService._coerce_float(
                        explosive_weapon.get('range', weapon_attributes.get('range')), 0,
                    )
                    weapon_accuracy = CombatService._coerce_int(
                        explosive_weapon.get('accuracy', weapon_attributes.get('accuracy')), 0,
                    )
                special_action_cost = 2 if mode == 'unaimed' else 4
                from app.services.weapon_modules import weapon_stat
                from app.services.equipment_modifications import rule as modification_rule
                weapon_accuracy=weapon_stat(explosive_weapon,'accuracy',weapon_accuracy)
                if mode=='aimed':weapon_accuracy+=modification_rule(explosive_weapon,'aimed_accuracy')
                if source == 'weapon':
                    explosive_fire_rate_state = CombatService._validate_weapon_fire_rate(
                        combat_meta, current_round, weapon_index, explosive_weapon, 1,
                    )

            explosive_profile = CombatService._explosive_profile(explosive_item)
            if not explosive_profile:
                raise ValidationError("This explosive does not have a damage profile yet")
            if source=='weapon':
                from app.services.equipment_modifications import explosive_profile as modified_explosive_profile
                explosive_profile=modified_explosive_profile(explosive_weapon,explosive_profile)
            explosive_profile['radius']=explosive_profile.get('radius',0)+character_passives(data)['grenade_radius_bonus']
            if source!='hand' and background_is(data,'Гренадир'):
                weapon_range+=5
                if CombatService._posture_key(character) in {'sitting','prone'}:weapon_accuracy+=1
                special_action_cost=trait_action_cost(data,'attack',special_action_cost,weapon_type='grenade_launcher',aimed=mode=='aimed')
            if fuse_mode=='delay' and explosive_profile.get('fuse','instant') not in {'round','turn_end'}:
                raise ValidationError('Мгновенный запал нельзя дополнительно задержать')
            requested_distance = CombatService._point_distance(
                character.pos_x, character.pos_y, target_x, target_y,
            )
            impact_x, impact_y, airburst = CombatService._clamp_projectile_point(
                character.pos_x,
                character.pos_y,
                target_x,
                target_y,
                CombatService._coerce_float(
                    explosive_profile.get('projectile_range'), 0,
                ) + (
                    artifact_passive_profile(data)['throw_range_bonus']
                    if source == 'hand' else 0
                ),
            )
            target_proxy = type('PointTarget', (), {
                'pos_x': impact_x, 'pos_y': impact_y, 'posture': 'standing',
                'floor_level': target_floor_level,
            })()
            sight = CombatService._cover_analysis(location_id, character, target_proxy)
            if target_floor_level!=(character.floor_level or 0):
                blocking_ids={z.get('object_id') for z in (sight.get('zones') or {}).values()}
                if sight.get('grade')=='full' and any(o.id in blocking_ids and o.type in {'floor','ceiling','roof','hatch'} for o in LocationObject.query.filter_by(location_id=location_id).all()):
                    raise ValidationError('Траектория перекрыта целым межэтажным перекрытием')
            no_direct_sight = not sight.get('targetable', True)
            if no_direct_sight and mode == 'aimed':
                raise ValidationError("Aimed grenade launcher fire requires direct line of sight")

            actual_distance = CombatService._point_distance(
                character.pos_x, character.pos_y, impact_x, impact_y,
            )
            if target_floor_level!=(character.floor_level or 0):
                from app.services.explosion_geometry import distance as spatial_distance
                actual_distance=spatial_distance(location,LocationObject.query.filter_by(location_id=location_id).all(),character.pos_x,character.pos_y,character.floor_level or 0,target_proxy)
            disadvantage = no_direct_sight if source != 'hand' else False
            if source == 'hand':
                throw_check = CombatService._hand_throw_difficulty(
                    location_id, character, data, impact_x, impact_y, actual_distance)
                difficulty = throw_check['difficulty']
                roll_kind = 'throwing'
            else:
                shooting_bonus = CombatService._base_skill_modifier(
                    data, 'skills.physical.shooting',
                )
                condition_modifier = CombatService._health_roll_modifier(
                    data, 'skills.physical.shooting',
                )
                difficulty = 12 - shooting_bonus - weapon_accuracy - condition_modifier
                difficulty += CombatService._equipment_accuracy_penalty(data)
                from app.services.weapon_modules import uses_nvg
                from app.services.equipment_passives import darkness_modifier
                if CombatService._is_dark_at_position(location, impact_x, impact_y):
                    difficulty -= darkness_modifier(data, 'accuracy_penalty')
                difficulty += launcher_strength['accuracy_penalty']
                difficulty += CombatService._coerce_int(
                    launcher_jam.get('accuracy_penalty') if launcher_jam else 0,
                    0,
                )
                difficulty -= POSTURES[CombatService._posture_key(character)]['shooting_bonus']
                if weapon_range > 0 and actual_distance <= weapon_range:
                    difficulty -= CombatService._coerce_int(
                        launcher_ergonomics.get('accuracy_modifier'), 0,
                    )
                difficulty += launcher_movement['difficulty_penalty']
                disadvantage = bool(
                    disadvantage
                    or launcher_movement['disadvantage']
                    or CombatService._has_roll_disadvantage(
                        data, 'skills.physical.shooting',
                        darkness=not uses_nvg(data) and CombatService._is_dark_at_position(location, impact_x, impact_y),
                    )
                    or (launcher_jam and launcher_jam.get('shooting_disadvantage'))
                )
                if mode == 'unaimed':
                    difficulty += max(0, 4 - trait_count(data, 'Мастер вскидывания'))
                if weapon_range > 0 and actual_distance > weapon_range:
                    difficulty += 2
                    if actual_distance > weapon_range + 10:
                        disadvantage = True
                difficulty = max(1, round(difficulty))
                roll_kind = 'shooting'
            weather=weather_at(location,character)
            difficulty-=weather['accuracy']
            disadvantage=disadvantage or weather['accuracy_disadvantage']
            explosive_details = {
                'weather':weather,
                'source': source,
                'fire_mode': mode if source != 'hand' else None,
                'fuse_mode': fuse_mode if source == 'hand' else None,
                'weapon_index': weapon_index if source != 'hand' else None,
                'fire_rate': (
                    explosive_fire_rate_state.get('fire_rate')
                    if explosive_fire_rate_state else None
                ),
                'item_name': explosive_profile['name'],
                'profile': explosive_profile,
                'target': {'x': target_x, 'y': target_y},
                'impact': {'x': impact_x, 'y': impact_y},
                'requested_distance': round(requested_distance, 2),
                'actual_distance': round(actual_distance, 2),
                'projectile_range': explosive_profile.get('projectile_range'),
                'airburst': airburst,
                'floor_level': target_floor_level,
                'roll_kind': roll_kind,
                'difficulty': difficulty,
                'disadvantage': disadvantage,
                'direct_sight': not no_direct_sight,
                'obstacle_difficulty': (
                    throw_check['obstacle'] if source == 'hand' else 0
                ),
            }
        elif action_key=='prosthesis':
            from app.services.prostheses import plan
            equipment_action_details=plan(data,equipment_operation,equipment_slot,item_path)
            special_action_cost=equipment_action_details['action_points']
        elif action_key == 'change_equipment':
            equipment_action_details = CombatService.equipment_action_details(
                data,
                equipment_operation,
                equipment_slot,
                item_path=item_path,
                retrieval_action_points=inventory_retrieval_action_points,
                in_combat=True,
            )
            special_action_cost = equipment_action_details['action_points']
        elif action_key == 'change_equipment_module':
            from app.services.equipment_module import module_action_details

            equipment_module_details = module_action_details(
                data,
                module_operation,
                module_target_path,
                module_slot_type,
                item_path=item_path,
                selection=module_selection,
                in_combat=True,
            )
            special_action_cost = equipment_module_details['action_points']
        elif action_key == 'recover_concussion':
            if not any(e['type']=='concussion' and e.get('active',True) for e in normalize_effect_list(health.get('effects') or [])):
                raise ValidationError('У персонажа нет контузии')
        elif action_key=='extinguish_fire':
            if not any(e['type']=='burning' and e.get('active',True) for e in normalize_effect_list(health.get('effects') or [])):
                raise ValidationError('Персонаж не горит')
        elif action_key=='prepare_water':
            from app.services.burning import water_item
            from app.services.inventory_access import calculate_inventory_access
            water,_=water_item(data,item_path)
            if not CombatService._has_usable_free_hand(character):raise ValidationError('Нет свободной руки для воды')
            special_action_cost=max(1,int(calculate_inventory_access(data,item_path,water)['retrieval_action_points']))
        elif action_key=='pour_water':
            from app.services.burning import water_item
            held=combat_meta.get('heldWaterItem') or {}
            if not held.get('path'):raise ValidationError('Сначала возьмите воду в руку')
            water,_=water_item(data,held['path'])
            if water.get('id')!=held.get('id'):raise ValidationError('Вода в руке перемещена или заменена')
            water_target=character if not target_character_id else LocationCharacter.query.filter_by(location_id=location_id,character_id=target_character_id).first()
            if not water_target:raise NotFoundError('Цель обливания не найдена')
            from app.services.burning import water_target_available
            if not water_target_available(character,water_target):
                raise ValidationError('Цель обливания недоступна')
        elif action_key == 'narrative_action':
            action_name = ' '.join(str(narrative_action_name or '').split())
            if not action_name or len(action_name) > 200:
                raise ValidationError("Action name must contain from 1 to 200 characters")
            narrative_cost = CombatService._coerce_int(action_points, -1)
            if not 0 <= narrative_cost <= 30:
                raise ValidationError("Action point cost must be between 0 and 30")
            roll_required = narrative_roll_required is True
            skill_path = str(narrative_skill_path or '').strip()
            if roll_required and skill_path not in CombatService.NARRATIVE_SKILLS:
                raise ValidationError("Choose a valid skill")
            difficulty = CombatService._coerce_int(narrative_difficulty, 10)
            if roll_required and not 1 <= difficulty <= 40:
                raise ValidationError("Difficulty must be between 1 and 40")
            special_action_cost = narrative_cost
            narrative_action_details = {
                'name': action_name,
                'action_points': narrative_cost,
                'roll_required': roll_required,
                'skill_path': skill_path if roll_required else None,
                'skill_label': CombatService.NARRATIVE_SKILLS.get(skill_path) if roll_required else None,
                'difficulty': difficulty if roll_required else None,
                'check': None,
            }
        elif action_key == 'must_do_it':
            if CombatService._is_mutant_character(data):
                combat_meta.pop('mustDoRetry', None)
                raise ValidationError("Мутанты не могут использовать «Должен это сделать»")
            retry = combat_meta.get('mustDoRetry')
            if not isinstance(retry, dict):
                action_name = str(narrative_action_name or '').strip()
                skill_path = str(narrative_skill_path or '').strip()
                difficulty = CombatService._coerce_int(narrative_difficulty, 0)
                if not action_name:
                    raise ValidationError("Describe the check approved by the GM")
                if skill_path not in CombatService.NARRATIVE_SKILLS:
                    raise ValidationError("Choose a valid skill")
                if not 1 <= difficulty <= 40:
                    raise ValidationError("Difficulty must be between 1 and 40")
                retry = {
                    'kind': 'manual',
                    'name': action_name,
                    'skill_path': skill_path,
                    'skill_label': CombatService.NARRATIVE_SKILLS[skill_path],
                    'difficulty': difficulty,
                    'created_round': current_round,
                }
            usage_profile = CombatService._must_do_usage_profile(data, state)
            if usage_profile['remaining'] <= 0:
                raise ValidationError(
                    "The Must Do It limit for this ten-minute interval has been reached"
                )
            special_action_cost = 0
            must_do_details = {
                **retry,
                'will_bonus': usage_profile['will_bonus'],
                'use_limit': usage_profile['limit'],
                'uses_before': usage_profile['used'],
            }
            must_do_details.pop('attack_details', None)
            if retry.get('kind') == 'attack':
                stored_attack = retry.get('attack_details')
                if not isinstance(stored_attack, dict):
                    raise ValidationError("The failed attack can no longer be repeated")
                if stored_attack.get('suspected_position'):
                    raise ValidationError('Этот способ стрельбы больше недоступен: выберите укрытие')
                attack_details = deepcopy(stored_attack)
                attack_details['must_do_retry'] = True
                fire_mode = attack_details.get('fire_mode')
                attack_type = attack_details.get('attack_type')
                target_zone = attack_details.get('target_zone')
                target_character_id = attack_details.get('target_character_id')
                target_character_ids = attack_details.get('target_character_ids')
                single_fire = fire_mode in {'unaimed', 'rapid', 'aimed'}
        elif action_key == 'console_ally':
            target = LocationCharacter.query.filter_by(
                location_id=location_id, character_id=target_character_id,
            ).first()
            if not target or target.id == character.id:
                raise ValidationError("Choose another character")
            CombatService.ensure_character_can_act(target)
            if not CombatService._is_adjacent(character, target):
                raise ValidationError("Союзник должен находиться на соседней клетке")
            target_data = target.character.data if isinstance(target.character.data, dict) else {}
            target_health = target_data.setdefault('health', {})
            target_meta = target_health.setdefault('combatMeta', {})
            lobby = location.lobby
            current_minute = (lobby.game_day or 1) * 1440 + (lobby.game_time_minutes or 0)
            last_minute = CombatService._coerce_int(target_meta.get('lastConsoledAt'), -100000)
            if current_minute - last_minute < 60:
                raise ValidationError("This character has already been consoled during the last hour")
            special_action_cost = 3
            consolation_details = {
                'target': target,
                'target_data': target_data,
                'current_minute': current_minute,
            }
        if action_key == 'recover_from_shock':
            health = data.setdefault('health', {})
            pain_level = max(0, CombatService._coerce_int(health.get('painLevel'), 0))
            if pain_level >= 10:
                raise ValidationError("Pain must be reduced below 10 before recovering from pain shock")
            meta = health.setdefault('combatMeta', {})
            if CombatService._coerce_int(meta.get('shockRecoveryRound'), 0) == current_round:
                raise ValidationError("An attempt to regain consciousness has already been made this turn")
            meta['shockRecoveryRound'] = current_round
            medicine_bonus = CombatService._coerce_int(meta.get('willShockBonus'), 0)
            difficulty, will_bonus = CombatService._pain_shock_recovery_difficulty(
                data, medicine_bonus
            )
            from app.services.equipment_care import consume_reanimation
            reanimation=consume_reanimation(data)
            if reanimation:
                rolls,roll,order_bonus=[],None,0
                success=True
            else:
                advantage = bool(meta.get('willShockAdvantage'))
                from app.services.brigadier_order import boost_roll
                rolls,roll,order_bonus=boost_roll(data,[],advantage=advantage)
                success = roll == 20 or (roll != 1 and roll+order_bonus >= difficulty)
            if success:
                health['effects'] = [
                    effect for effect in normalize_effect_list(health.get('effects') or [])
                    if effect.get('type') != 'shock'
                ]
                meta['painShockRecoveredRound'] = current_round
                meta['painShockRecovered'] = True
                meta.pop('willShockBonus', None)
                meta.pop('willShockAdvantage', None)
            character.posture = 'prone'
            character.cover_object_id = None
            character.weapon_braced = False
            character.braced_weapon_index = None
            character.character.data = data
            flag_modified(character.character, 'data')
            CombatService._sync_location_effects_from_character(character)
            special_action_cost = 0
            melee_action_details = {
                'kind': 'recover_from_shock',
                'rolls': rolls,
                'roll': roll,
                'will_bonus': will_bonus,
                'medicine_bonus': medicine_bonus,
                'difficulty': difficulty,
                'total': roll,
                'success': success,
                'reanimation_protocol': reanimation,
                'ends_turn': True,
            }
        if (
            character.grappled_by_id
            and action_key not in {
                'grapple_escape', 'grapple_desperate_attack', 'recover_from_shock'
            }
        ):
            raise ValidationError("A grappled character can only escape or make a desperate attack")
        if (
            character.melee_block_round == current_round
            and action_key != 'melee_block'
        ):
            character.melee_block_round = None
            character.melee_block_effectiveness = 0

        if action_key == 'melee_swing':
            if character.melee_swing_round == current_round:
                raise ValidationError("Swing can only be prepared once per turn")
            character.melee_swing_round = current_round
            melee_action_details = {'prepared': True, 'damage_bonus_percent': 25}

        if action_key == 'melee_block':
            block_cost = CombatService._coerce_int(action_points, 0)
            if block_cost not in {1, 2, 3, 4}:
                raise ValidationError("Block must cost from 1 to 4 action points")
            block_base = {1: 2, 2: 3, 3: 4, 4: 7}[block_cost]
            strength_bonus = max(
                0,
                CombatService._skill_modifier(
                    data, 'skills.physical.strength', include_pain=False
                ),
            )
            character.melee_block_round = current_round
            character.melee_block_effectiveness = block_base + strength_bonus+character_passives(data)['block_efficiency']
            from app.services.weapon_grip import block_bonus
            character.melee_block_effectiveness+=block_bonus(character)
            combat_meta['meleeBlockArm'] = (
                target_zone if target_zone in {'left_arm', 'right_arm'} else 'right_arm'
            )
            combat_meta['meleeBlockCounterattack'] = 'counterattack' in str(payment or '').lower()
            special_action_cost = trait_action_cost(data,'block',block_cost)
            melee_action_details = {
                'block_cost': block_cost,
                'effectiveness': character.melee_block_effectiveness,
                'block_arm': combat_meta['meleeBlockArm'],
                'counterattack': combat_meta['meleeBlockCounterattack'],
            }

        targeted_melee_actions = {'melee_disarm', 'melee_shove', 'grapple'}
        target = None
        if action_key in targeted_melee_actions:
            target = LocationCharacter.query.filter_by(
                location_id=location_id,
                character_id=target_character_id,
            ).first()
            if not target or target.id == character.id:
                raise ValidationError("Target character not found")
            if not CombatService._is_adjacent(character, target):
                raise ValidationError("Цель атаки ближнего боя должна находиться на соседней клетке")
            if not balance_state(character):
                character.facing_x = 0 if target.pos_x == character.pos_x else (1 if target.pos_x > character.pos_x else -1)
                character.facing_y = 0 if target.pos_y == character.pos_y else (1 if target.pos_y > character.pos_y else -1)
            CombatService._sync_grapple_facing(character)

        if action_key == 'melee_disarm':
            if not CombatService._has_free_hand(character):
                raise ValidationError("A free hand is required to take an item")
            actor_bonus = CombatService._skill_modifier(
                data, 'skills.physical.melee'
            )
            target_data = target.character.data if target.character else {}
            target_bonus = CombatService._skill_modifier(
                target_data, 'skills.physical.melee'
            )
            from app.services.brigadier_order import boost_roll
            _rolls,roll,order_bonus=boost_roll(data,[]);actor_bonus+=order_bonus
            difficulty = 8 - actor_bonus + target_bonus
            if target.drawn_weapon_index is not None:
                difficulty += 4
                from app.services.equipment_modifications import worn_rule
                difficulty += worn_rule(target_data,'snatch_difficulty_bonus')
            success = roll == 20 or (roll != 1 and roll >= difficulty)
            removed_weapon_index = target.drawn_weapon_index if success else None
            transferred_weapon = None
            if success and removed_weapon_index is not None:
                target_weapons = (
                    target_data.get('weapons')
                    if isinstance(target_data.get('weapons'), list)
                    else []
                )
                if 0 <= removed_weapon_index < len(target_weapons):
                    transferred_weapon = target_weapons.pop(removed_weapon_index)
                    actor_weapons = data.setdefault('weapons', [])
                    actor_weapons.append(transferred_weapon)
                    character.drawn_weapon_index = len(actor_weapons) - 1
                    character.character.data = data
                    target.character.data = target_data
                    flag_modified(character.character, 'data')
                    flag_modified(target.character, 'data')
                target.drawn_weapon_index = None
                CombatService._clear_aim(target)
            elif success:
                target.drawn_weapon_index = None
                CombatService._clear_aim(target)
            melee_action_details = {
                'roll': roll,
                'difficulty': difficulty,
                'success': success,
                'removed_weapon_index': removed_weapon_index,
                'transferred_item': (
                    transferred_weapon.get('name')
                    if isinstance(transferred_weapon, dict)
                    else None
                ),
            }

        if action_key == 'melee_shove':
            target_data = target.character.data if target.character else {}
            charge_bonus = (
                3
                if (
                    character.movement_mode_this_turn in {'run', 'sprint'}
                    and (character.movement_distance_this_turn or 0)-(character.correction_distance_this_turn or 0) >= 3
                )
                else 0
            )
            if charge_bonus:
                special_action_cost = 1
            actor_roll = CombatService._opposed_roll(
                data, 'skills.physical.strength'
            )
            actor_roll['total'] += charge_bonus
            defender_roll = CombatService._opposed_roll(
                target_data, 'skills.physical.strength'
            )
            success = (
                actor_roll['critical_success']
                or (
                    not actor_roll['critical_failure']
                    and actor_roll['total'] > defender_roll['total']
                )
            )
            push_distance = 3 if success and actor_roll['total'] - defender_roll['total'] >= 5 else (1 if success else 0)
            moved = 0
            if push_distance:
                dx = 0 if target.pos_x == character.pos_x else (1 if target.pos_x > character.pos_x else -1)
                dy = 0 if target.pos_y == character.pos_y else (1 if target.pos_y > character.pos_y else -1)
                blocked, _ = CombatService._build_movement_map(
                    location, target.character_id
                )
                for step in range(1, push_distance + 1):
                    destination = (target.pos_x + dx, target.pos_y + dy)
                    if (
                        destination in blocked
                        or not (0 <= destination[0] < location.grid_width)
                        or not (0 <= destination[1] < location.grid_height)
                    ):
                        break
                    target.pos_x, target.pos_y = destination
                    moved = step
            melee_action_details = {
                'attacker': actor_roll,
                'defender': defender_roll,
                'charge_bonus': charge_bonus,
                'success': success,
                'distance': moved,
                'target_fell': success,
                'attacker_fell': False,
            }
            if success:
                target.posture = 'prone'
            elif defender_roll['total'] - actor_roll['total'] >= 10:
                dx = 0 if target.pos_x == character.pos_x else (1 if target.pos_x > character.pos_x else -1)
                dy = 0 if target.pos_y == character.pos_y else (1 if target.pos_y > character.pos_y else -1)
                destination = (target.pos_x + dx, target.pos_y + dy)
                blocked, _ = CombatService._build_movement_map(
                    location, character.character_id
                )
                if (
                    destination not in blocked
                    and 0 <= destination[0] < location.grid_width
                    and 0 <= destination[1] < location.grid_height
                ):
                    character.pos_x, character.pos_y = destination
                character.posture = 'prone'
                melee_action_details['attacker_fell'] = True

        if action_key == 'grapple':
            target_data = (
                target.character.data
                if target.character and isinstance(target.character.data, dict)
                else {}
            )
            if CombatService._mutant_has_trait(target_data, 'Не может быть целью Захвата'):
                raise ValidationError('Это существо нельзя взять в захват')
            if not CombatService._has_usable_free_hand(character):
                raise ValidationError("At least one free hand is required for a grapple")
            if character.grapple_target_id or character.grappled_by_id:
                raise ValidationError("Character is already in a grapple")
            if target.grapple_target_id or target.grappled_by_id:
                raise ValidationError("Target is already in a grapple")
            target_data = target.character.data if target.character else {}
            paths = (
                ('skills.physical.strength',)
                if attribute_choice == 'strength'
                else (
                    ('skills.physical.agility',)
                    if attribute_choice == 'agility'
                    else ('skills.physical.strength', 'skills.physical.agility')
                )
            )
            behind = CombatService._is_behind(character, target)
            actor_roll = CombatService._opposed_roll(
                data,
                'skills.physical.melee',
                paths,
            )
            if behind:
                extra = random.randint(1, 20)
                actor_roll['rolls'].append(extra)
                actor_roll['roll'] = max(actor_roll['roll'], extra)
                actor_roll['total'] = actor_roll['roll'] + actor_roll['bonus']
            defender_roll = CombatService._opposed_roll(
                target_data,
                'skills.physical.melee',
                ('skills.physical.strength', 'skills.physical.agility'),
            )
            success = (
                actor_roll['critical_success']
                or (
                    not actor_roll['critical_failure']
                    and actor_roll['total'] > defender_roll['total']
                )
            )
            if success:
                character.grapple_target_id = target.id
                target.grappled_by_id = character.id
            melee_action_details = {
                'attacker': actor_roll,
                'defender': defender_roll,
                'from_behind': behind,
                'success': success,
            }

        if action_key in {
            'grapple_escape', 'grapple_release', 'grapple_strengthen',
            'grapple_choke', 'grapple_pain_hold', 'grapple_desperate_attack',
            'grapple_live_shield',
        }:
            holder = (
                LocationCharacter.query.filter_by(
                    id=character.grappled_by_id,
                    location_id=location_id,
                ).first()
                if character.grappled_by_id
                else character
            )
            captive = (
                character
                if character.grappled_by_id
                else LocationCharacter.query.filter_by(
                    id=character.grapple_target_id,
                    location_id=location_id,
                ).first()
            )
            if not holder or not captive or holder.grapple_target_id != captive.id:
                raise ValidationError("Character is not in the required grapple state")
            if action_key == 'grapple_release':
                if holder.id != character.id:
                    raise ValidationError("Only the holder can release the grapple")
                CombatService._clear_grapple(holder, captive)
                melee_action_details = {'released': True}
            elif action_key == 'grapple_escape':
                if captive.id != character.id:
                    raise ValidationError("Only the captive can escape")
                holder_data = holder.character.data if holder.character else {}
                captive_roll = CombatService._opposed_roll(
                    data,
                    'skills.physical.melee',
                    ('skills.physical.strength', 'skills.physical.agility'),
                    disadvantage=bool(holder.grapple_strengthened),
                )
                holder_roll = CombatService._opposed_roll(
                    holder_data,
                    'skills.physical.melee',
                    ('skills.physical.strength', 'skills.physical.agility'),
                )
                success = captive_roll['total'] > holder_roll['total']
                if success:
                    CombatService._clear_grapple(holder, captive)
                melee_action_details = {
                    'captive': captive_roll,
                    'holder': holder_roll,
                    'success': success,
                }
            elif action_key == 'grapple_desperate_attack':
                if captive.id != character.id:
                    raise ValidationError("Only the captive can use a desperate attack")
                target_character_id = holder.character_id
                attack_type = 'grapple_desperate'
                attack_details = {
                    'weapon_index': -1,
                    'attack_type': attack_type,
                    'action_points': 3,
                    'round_number': state.round_number,
                    'target_character_id': holder.character_id,
                    'target_distance': 1,
                    'melee': True,
                    'melee_disadvantage': True,
                    'hit_difficulty': (
                        12 - CombatService._skill_modifier(
                            data, 'skills.physical.melee'
                        )
                    ),
                }
            elif holder.id != character.id:
                raise ValidationError("Only the holder can use this action")
            elif action_key == 'grapple_strengthen':
                holder.grapple_strengthened = True
                melee_action_details = {'strengthened': True}
            elif action_key == 'grapple_live_shield':
                holder.grapple_live_shield = True
                CombatService._sync_grapple_facing(holder, captive)
                melee_action_details = {
                    'live_shield': True,
                    'cover_grade': 'three_quarters',
                }
            elif action_key == 'grapple_choke':
                holder.grapple_choke_rounds = (holder.grapple_choke_rounds or 0) + 1
                captive_data = captive.character.data if captive.character else {}
                from app.services.brigadier_order import boost_roll
                _rolls,will_roll,order_bonus=boost_roll(captive_data,[])
                will_bonus = CombatService._skill_modifier(
                    captive_data, 'skills.physical.will'
                )+order_bonus
                difficulty = 10 + 2 * holder.grapple_choke_rounds
                success = will_roll == 20 or (
                    will_roll != 1 and will_roll + will_bonus >= difficulty
                )
                if not success and captive.character:
                    health = captive_data.setdefault('health', {})
                    apply_effect_to_health(health, {
                        'type': 'unconscious',
                        'name': 'Без сознания',
                        'remaining': random.randint(1, 6) + 1,
                        'duration_unit': 'round',
                        'source': 'grapple_choke',
                    })
                    captive.character.data = captive_data
                    flag_modified(captive.character, 'data')
                melee_action_details = {
                    'roll': will_roll,
                    'bonus': will_bonus,
                    'difficulty': difficulty,
                    'success': success,
                }
            elif action_key == 'grapple_pain_hold':
                captive_data = captive.character.data if captive.character else {}
                from app.services.brigadier_order import boost_roll
                _rolls,will_roll,order_bonus=boost_roll(captive_data,[])
                will_bonus = CombatService._skill_modifier(
                    captive_data, 'skills.physical.will'
                )+order_bonus
                total = will_roll + will_bonus
                failed_by = max(0, 14 - total)
                if failed_by and captive.character:
                    health = captive_data.setdefault('health', {})
                    health.setdefault('combatMeta', {})['injuryRound'] = current_round
                    apply_effect_to_health(health, {'type':'pain','value':3,'source':'grapple'},character_data=captive_data)
                    if failed_by > 5:
                        apply_effect_to_health(health, {
                            'type': 'fracture',
                            'name': 'Перелом руки',
                            'area': 'arm',
                            'source': 'grapple_pain_hold',
                        })
                    if failed_by > 10:
                        apply_effect_to_health(health, {
                            'type': 'pain_shock',
                            'name': 'Болевой шок',
                            'source': 'grapple_pain_hold',
                        })
                    captive.character.data = captive_data
                    flag_modified(captive.character, 'data')
                melee_action_details = {
                    'roll': will_roll,
                    'bonus': will_bonus,
                    'difficulty': 14,
                    'failed_by': failed_by,
                    'pain_added': 3 if failed_by else 0,
                    'fracture': failed_by > 5,
                    'pain_shock': failed_by > 10,
                }
        if action_key == 'take_cover':
            cover_object = LocationObject.query.filter_by(
                id=target_object_id,
                location_id=location_id,
            ).first()
            if not cover_object:
                raise ValidationError("Cover object not found")
            if not CombatService._is_cover_object(cover_object):
                raise ValidationError("Object cannot be used as cover")
            footprint = CombatService._object_footprint_tiles(cover_object)
            distance = min(
                (
                    max(abs(character.pos_x - tile_x), abs(character.pos_y - tile_y))
                    for tile_x, tile_y in footprint
                ),
                default=999,
            )
            if distance > 1:
                raise ValidationError("Character must be adjacent to cover")
            height = CombatService._object_height(cover_object)
            if height <= 1.05 and CombatService._posture_key(character) == 'standing':
                raise ValidationError("Sit down before taking low cover")
            character.cover_object_id = cover_object.id
            character.weapon_braced = False
            character.braced_weapon_index = None
            cover_details = {
                'occupied': True,
                'object_id': cover_object.id,
                'height': height,
                **CombatService._cover_profile(cover_object),
            }

        if action_key == 'leave_cover':
            character.cover_object_id = None
            character.weapon_braced = False
            character.braced_weapon_index = None
            cover_details = {'occupied': False}

        if action_key == 'brace_weapon':
            if character.drawn_weapon_index is None:
                raise ValidationError("Draw a weapon first")
            cover_object = db.session.get(LocationObject, character.cover_object_id)
            if not cover_object or cover_object.location_id != location_id:
                raise ValidationError("Take cover first")
            payment_key = str(payment or '').lower()
            costs = {
                'action': ('action_points_current', 1),
                'free': ('free_actions_current', 1),
                'movement': ('movement_points_current', 3),
            }
            if payment_key not in costs:
                raise ValidationError("Choose action, free, or movement payment")
            field, cost = costs[payment_key]
            if getattr(character, field) < cost:
                raise ValidationError("Not enough resources")
            setattr(character, field, getattr(character, field) - cost)
            character.weapon_braced = True
            character.braced_weapon_index = character.drawn_weapon_index
            drawn_weapon = (character.data.get('weapons') or [])[character.drawn_weapon_index]
            bipod = next(
                (
                    module for module in (drawn_weapon.get('installedModules') or [])
                    if isinstance(module, dict)
                    and module.get('slotType') == 'handguard'
                    and (
                        module.get('bipod')
                        or (module.get('attributes') or {}).get('bipod')
                        or module.get('name') == 'Сошки'
                    )
                ),
                None,
            )
            brace_ergonomics_bonus = (50 if background_is(data,'Пост принял') else 75) if bipod else 10
            brace_details = {
                'weapon_index': character.drawn_weapon_index,
                'payment': payment_key,
                'cost': cost,
                'accuracy_bonus': 1,
                'ergonomics_bonus': brace_ergonomics_bonus,
            }
        if action_key == 'change_posture':
            target_posture = str(posture or '').lower()
            options = CombatService._posture_change_options(character, target_posture)
            selected_payment = str(payment or 'movement').lower()
            selected = next(
                (option for option in options if option['resource'] == selected_payment),
                None,
            )
            if not selected:
                raise ValidationError("Invalid posture payment method")
            if selected_payment == 'movement':
                if character.movement_points_current < selected['cost']:
                    raise ValidationError("Not enough movement points")
                character.movement_points_current -= selected['cost']
            else:
                if character.action_points_current < selected['cost']:
                    raise ValidationError("Not enough action points")
                character.action_points_current -= selected['cost']
            source_posture = CombatService._posture_key(character)
            character.posture = target_posture
            character.weapon_braced = False
            character.braced_weapon_index = None
            if target_posture == 'standing' and character.cover_object_id:
                occupied_cover = db.session.get(LocationObject, character.cover_object_id)
                if occupied_cover and CombatService._object_height(occupied_cover) <= 1.05:
                    character.cover_object_id = None
            posture_details = {
                'from': source_posture,
                'to': target_posture,
                **selected,
            }

        if action_key == 'change_facing':
            if CombatService._coerce_int(character.facing_changed_round, 0) == current_round:
                raise ValidationError("A character can turn only once per round")
            facing_details = CombatService._facing_change_options(character, facing_x, facing_y)
            selected_payment = str(payment or '').lower()
            selected = next(
                (option for option in facing_details['options'] if option['payment'] == selected_payment),
                None,
            )
            if not selected:
                raise ValidationError("Choose a valid payment method for turning")
            for field, key in (
                ('action_points_current', 'action_points'),
                ('free_actions_current', 'free_actions'),
                ('movement_points_current', 'movement_points'),
            ):
                if getattr(character, field) < selected[key]:
                    raise ValidationError("Not enough resources")
            character.action_points_current -= selected['action_points']
            character.free_actions_current -= selected['free_actions']
            character.movement_points_current -= selected['movement_points']
            character.facing_x = facing_details['to_x']
            character.facing_y = facing_details['to_y']
            character.facing_changed_round = current_round
            facing_details['payment'] = selected_payment

        if action_key == 'draw_weapon':
            weapons = (character.character.data or {}).get('weapons') or []
            weapon_index = CombatService._coerce_int(weapon_index, -1)
            if weapon_index < 0 or weapon_index >= len(weapons):
                raise ValidationError("Weapon not found")
            if attribute_choice=='second_hand':
                from app.services.weapon_grip import validate_secondary
                validate_secondary(character,weapon_index)
                draw_grip=None
            else:
                from app.services.weapon_grip import draw_plan
                draw_grip=draw_plan(data,weapons[weapon_index],attribute_choice,extra_occupied=bool(character.grapple_target_id))
                if draw_grip:deferred_request['attribute_choice']='both_hands' if draw_grip['hands']==2 else draw_grip['hand']+'_hand'
            ergonomics_profile = CombatService._weapon_ergonomics_profile(
                character,
                weapons[weapon_index],
                weapon_index,
            )
            draw_cost = ergonomics_profile['draw_action_points']
            special_action_cost = draw_cost
            draw_details = {
                'weapon_index': weapon_index,
                'secondary': attribute_choice=='second_hand',
                'grip':draw_grip,
                'action_points': draw_cost,
                'ergonomics': ergonomics_profile,
            }

        if action_key == 'load_ammunition':
            if isinstance(loading, dict) and loading.get('weapon_index') is not None:
                from app.services.body_transport import ensure_firearm_allowed
                ensure_firearm_allowed(character)
            from app.services.ammo_loading import validate_loading
            validate_loading(data, loading, combat=True)
            special_action_cost = loading['action_points']
            if not resumed_paid_action:
                if character.free_actions_current < loading['free_actions']:
                    raise ValidationError('Не хватает СД')
                character.free_actions_current -= loading['free_actions']

        if action_key == 'reload_weapon':
            from app.services.inventory_access import calculate_inventory_access

            weapons = (character.character.data or {}).get('weapons') or []
            weapon_index = CombatService._coerce_int(weapon_index, -1)
            if weapon_index < 0 or weapon_index >= len(weapons):
                raise ValidationError("Weapon not found")
            validate_magazine_selection(data, weapon_index, item_path, magazine_template_id, magazine_selection)
            magazine_template = db.session.get(
                ItemTemplate,
                CombatService._coerce_int(magazine_template_id, 0),
            )
            if not magazine_template or magazine_template.category != 'magazine':
                raise ValidationError("Magazine not found")
            magazine_attributes = (
                magazine_template.attributes
                if isinstance(magazine_template.attributes, dict)
                else {}
            )
            candidate_weapon = dict(weapons[weapon_index] or {})
            candidate_weapon['installedMagazine'] = {
                'templateId': magazine_template.id,
                'ergonomics': magazine_attributes.get('ergonomics', 0),
            }
            ergonomics_profile = CombatService._weapon_ergonomics_profile(
                character,
                candidate_weapon,
                weapon_index,
            )
            base_reload_cost = max(
                0,
                CombatService._coerce_int(magazine_attributes.get('reload_time_od'), 0),
            )
            from app.services.equipment_modifications import rule as modification_rule
            reload_cost = max(
                0,
                base_reload_cost + ergonomics_profile['reload_action_points_modifier']+modification_rule(weapons[weapon_index],'reload_ap_delta'),
            )
            access = calculate_inventory_access(data, item_path)
            retrieval_cost = int(access['retrieval_action_points'])
            use_discount = int(access['use_action_discount'])
            from app.services.equipment_modifications import worn_rule
            belt_discount = int(worn_rule(data,'belt_reload_ap_reduction')) if item_path[:3]==['equipment','belt','pouches'] else 0
            reload_cost = trait_action_cost(data,'reload',max(0, reload_cost - use_discount - belt_discount)) + retrieval_cost
            special_action_cost = reload_cost
            reload_details = {
                'weapon_index': weapon_index,
                'magazine_template_id': magazine_template.id,
                'base_action_points': base_reload_cost,
                'ergonomics_modifier': ergonomics_profile['reload_action_points_modifier'],
                'inventory_retrieval_action_points': retrieval_cost,
                'inventory_use_action_discount': use_discount,
                'belt_modification_discount': belt_discount,
                'action_points': reload_cost,
            }

        if action_key == 'reload_underbarrel':
            from app.services.inventory_access import calculate_inventory_access

            weapons = data.get('weapons') if isinstance(data.get('weapons'), list) else []
            weapon_index = CombatService._coerce_int(weapon_index, -1)
            if weapon_index < 0 or weapon_index >= len(weapons):
                raise ValidationError("Weapon not found")
            launcher = next((
                module for module in (weapons[weapon_index].get('installedModules') or [])
                if isinstance(module, dict)
                and (module.get('attributes') or {}).get('type') == 'grenade_launcher'
            ), None)
            if not launcher:
                raise ValidationError("Underbarrel grenade launcher is not installed")
            if (launcher.get('attributes') or {}).get('assault_rifle_only') and not re.search(r'штурмов|assault',CombatService._weapon_subcategory(weapons[weapon_index])):
                raise ValidationError('Подствольный гранатомёт доступен только для штурмовых винтовок')
            if launcher.get('loadedGrenade'):
                raise ValidationError("Underbarrel grenade launcher is already loaded")
            grenade = CombatService._inventory_item_at_path(data, item_path)
            if not CombatService._explosive_profile(grenade):
                raise ValidationError("Selected ammunition is not a supported grenade")
            launcher_caliber = CombatService._normalize_caliber(
                (launcher.get('attributes') or {}).get('caliber')
            )
            grenade_attributes = CombatService._template_attributes(grenade)
            grenade_caliber = CombatService._normalize_caliber(
                grenade_attributes.get('caliber') or grenade.get('caliber')
            )
            if launcher_caliber and grenade_caliber and launcher_caliber != grenade_caliber:
                raise ValidationError("Grenade caliber does not match the launcher")
            access = calculate_inventory_access(data, item_path, grenade)
            retrieval_cost = int(access['retrieval_action_points'])
            use_discount = int(access['use_action_discount'])
            special_action_cost = trait_action_cost(data,'reload',max(0,5-use_discount)) + retrieval_cost
            underbarrel_reload_details = {
                'weapon_index': weapon_index,
                'item_path': list(item_path or []),
                'item_name': grenade.get('name'),
                'inventory_retrieval_action_points': retrieval_cost,
                'inventory_use_action_discount': use_discount,
                'action_points': special_action_cost,
            }

        if action_key == 'clear_weapon_jam':
            weapons = (character.character.data or {}).get('weapons') or []
            weapon_index = CombatService._coerce_int(weapon_index, -1)
            if weapon_index < 0 or weapon_index >= len(weapons):
                raise ValidationError("Weapon not found")
            if not held(character,weapon_index):
                raise ValidationError("Draw this weapon first")
            weapon = weapons[weapon_index] or {}
            CombatService._weapon_durability(weapon)
            jam = weapon.get('jam')
            if not isinstance(jam, dict):
                raise ValidationError("Weapon is not jammed")
            if jam.get('repair_required'):
                raise ValidationError("This malfunction requires weapon repair")
            shooting = CombatService._skill_value(data, 'skills.physical.shooting')
            reduction = 2 if shooting >= 20 else (1 if shooting >= 15 else 0)
            clear_cost = max(0, CombatService._coerce_int(jam.get('fix_ap'), 0) - reduction)
            special_action_cost = clear_cost
            clear_jam_details = {
                'weapon_index': weapon_index,
                'result': jam.get('result'),
                'label': jam.get('label'),
                'base_action_points': CombatService._coerce_int(jam.get('fix_ap'), 0),
                'skill_reduction': reduction,
                'action_points': clear_cost,
            }

        if action_key == 'aim':
            weapons = (character.character.data or {}).get('weapons') or []
            weapon_index = CombatService._coerce_int(weapon_index, -1)
            if weapon_index < 0 or weapon_index >= len(weapons):
                raise ValidationError("Weapon not found")
            if not held(character,weapon_index):
                raise ValidationError("Draw this weapon first")
            target = LocationCharacter.query.filter_by(
                location_id=location_id,
                character_id=target_character_id,
            ).first()
            if not target or target.id == character.id:
                raise ValidationError("Aim target not found")
            if not CombatService._is_in_facing_arc(character, target.pos_x, target.pos_y):
                raise ValidationError("The target is outside the character's field of view")
            from app.services.portable_lights import shooting as portable_shooting
            if portable_shooting(character,target,'aimed')['aimed_blocked']:
                raise ValidationError('Нельзя прицельно стрелять через ПНВ в направленный на вас свет')
            aim_details = {
                'weapon_index': weapon_index,
                'target_character_id': target_character_id,
                'accuracy_bonus': (
                    CombatService._coerce_int(character.aim_accuracy_bonus, 0) + 1
                    if character.aimed_target_character_id == target_character_id
                    and character.aimed_weapon_index == weapon_index
                    else 1
                ),
            }

        if action_key == 'attack' and not fire_mode:
            weapons = (character.character.data or {}).get('weapons') or []
            attack_type_key = str(attack_type or '').strip().lower().replace('ё', 'е')
            circular_attack = 'круг' in attack_type_key
            melee_targets = []
            if circular_attack:
                selected_ids = list(dict.fromkeys(target_character_ids or []))
                if not selected_ids and target_character_id:
                    selected_ids = [target_character_id]
                if not 1 <= len(selected_ids) <= 3:
                    raise ValidationError("A circular attack requires 1 to 3 targets")
                target_lookup = {
                    item.character_id: item
                    for item in LocationCharacter.query.filter(
                        LocationCharacter.location_id == location_id,
                        LocationCharacter.character_id.in_(selected_ids),
                    ).all()
                }
                melee_targets = [
                    target_lookup[target_id]
                    for target_id in selected_ids
                    if target_id in target_lookup
                ]
                if (
                    len(melee_targets) != len(selected_ids)
                    or any(target.id == character.id for target in melee_targets)
                ):
                    raise ValidationError("Circular attack target not found")
                if any(
                    not CombatService._is_adjacent(character, target)
                    for target in melee_targets
                ):
                    raise ValidationError("All circular attack targets must be adjacent")
                target = melee_targets[0]
                target_character_id = target.character_id
                target_character_ids = selected_ids
            else:
                if not target_character_id:
                    raise ValidationError("Target character is required")
                target = LocationCharacter.query.filter_by(
                    location_id=location_id,
                    character_id=target_character_id,
                ).first()
                if not target or target.id == character.id:
                    raise ValidationError("Target character not found")
                melee_targets = [target]
            weapon_index = CombatService._coerce_int(weapon_index, -1)
            weapon = {}
            if attack_type_key == 'unarmed':
                weapon_index = -1
            else:
                if weapon_index < 0 or weapon_index >= len(weapons):
                    raise ValidationError("Weapon not found")
                weapon = weapons[weapon_index] or {}
                if (
                    not held(character,weapon_index)
                    and not CombatService._template_attributes(weapon).get('natural_weapon')
                ):
                    raise ValidationError("Draw this weapon first")
                if attack_type_key == 'firearm_butt':
                    template = (
                        db.session.get(ItemTemplate, weapon.get('templateId'))
                        if weapon.get('templateId') else None
                    )
                    if template and template.category != 'weapon':
                        raise ValidationError("Only a firearm can use a butt attack")
            distance = CombatService._character_distance(character,target)
            if not circular_attack and not CombatService._is_adjacent(character,target):
                raise ValidationError("Цель атаки ближнего боя должна находиться на соседней клетке")
            if (
                CombatService._mutant_has_trait(data, 'Миролюбивый')
                and not combat_meta.get('mutantProvoked')
            ):
                raise ValidationError(
                    'Миролюбивый мутант не может атаковать первым'
                )
            if not balance_state(character):
                character.facing_x = 0 if target.pos_x == character.pos_x else (1 if target.pos_x > character.pos_x else -1)
                character.facing_y = 0 if target.pos_y == character.pos_y else (1 if target.pos_y > character.pos_y else -1)
            profile = (
                CombatService._virtual_melee_profile(
                    attack_type_key,
                    weapon,
                    character.character.data or {},
                )
                or CombatService._weapon_damage_profile(weapon, attack_type)
            )
            melee_bonus = CombatService._skill_modifier(
                character.character.data or {}, 'skills.physical.melee'
            )
            prosthesis_accuracy=CombatService._template_attributes(weapon)
            if prosthesis_accuracy.get('prosthesis_zone'):
                melee_bonus-=prosthesis_accuracy.get('prosthesis_efficiency',0)
            melee_bonus += max(
                0,
                CombatService._coerce_int(
                    combat_meta.get('mutantRageAccuracy'), 0,
                ),
            )
            accuracy = CombatService._coerce_int(profile.get('accuracy'), 0)
            axe=str(weapon.get('name') or '').strip().casefold()=='топор'
            if background_is(data,'Пожарный') and not axe and attack_type_key not in {'unarmed','firearm_butt'}:accuracy-=1
            if background_is(data,'Фехтовальщик') and weapon_index is not None and int(weapon_index)>=0:
                weight_class=str(profile.get('weight_class') or '').lower().replace('ё','е')
                if 'тяж' in weight_class or weight_class in {'heavy','heavy_plus'}:accuracy-=2
            payment_tokens = {
                token
                for token in re.split(r'[^a-z_]+', str(payment or '').strip().lower())
                if token
            }
            aimed_melee = 'aimed' in payment_tokens
            paid_automatic_back_attack = 'back_auto' in payment_tokens
            if circular_attack and aimed_melee:
                raise ValidationError("A circular attack cannot be aimed")
            mutant_profile_data = (
                data.get('mutant') if isinstance(data.get('mutant'), dict) else {}
            )
            if aimed_melee and any(
                str(trait).startswith('Неприцельные атаки')
                for trait in (mutant_profile_data.get('traits') or [])
            ):
                raise ValidationError("This mutant cannot make aimed attacks")
            swing_prepared = character.melee_swing_round == current_round
            if aimed_melee and not swing_prepared:
                raise ValidationError("Prepare a swing before an aimed melee attack")
            if aimed_melee and target_zone not in HIT_ZONES:
                raise ValidationError("Choose a valid body part for an aimed melee attack")
            target_profile = CombatService._melee_target_profile(
                character,
                target,
                melee_bonus,
                accuracy,
                aimed_melee,
                target_zone,
                circular_attack,
            )
            if paid_automatic_back_attack and (
                circular_attack or not target_profile['from_behind']
            ):
                raise ValidationError("Automatic melee hit is only available against a target from behind")
            melee_cost = CombatService._melee_action_cost(profile, attack_type_key)
            if background_is(data,'Пожарный') and axe:melee_cost=max(1,melee_cost-1)
            if CombatService._template_attributes(weapon).get('natural_weapon'):
                cost_profile = {
                    **profile,
                    'action_points': CombatService._template_attributes(weapon).get(
                        'action_points', profile.get('action_points'),
                    ),
                    'raw_effect': CombatService._template_attributes(weapon).get(
                        'raw_effect', '',
                    ),
                }
                melee_cost = CombatService._mutant_first_attack_cost(
                    character, cost_profile, current_round,
                )
            if paid_automatic_back_attack:
                melee_cost += 2
            fencer='fencer' in payment_tokens
            if fencer:
                if not background_is(data,'Фехтовальщик') or combat_meta.get('fencerUsed') or weapon_index is None or int(weapon_index)<0 or attack_type_key=='unarmed' or CombatService._template_attributes(weapon).get('natural_weapon'):
                    raise ValidationError('Приём Фехтовальщика недоступен')
                melee_cost=max(1,melee_cost-2)
            attack_details = {
                'weapon_index': weapon_index,
                'attack_type': attack_type,
                'action_points': melee_cost,
                'round_number': state.round_number,
                'target_character_id': target_character_id,
                'target_character_ids': target_character_ids if circular_attack else None,
                'target_distance': distance,
                'melee': True,
                'fencer':fencer,
                'circular_attack': circular_attack,
                'hit_difficulty': target_profile['difficulty'],
                'aimed_melee': aimed_melee,
                'from_behind': target_profile['from_behind'],
                'target_prone': target_profile['target_prone'],
                'target_unconscious': target_profile['target_unconscious'],
                'automatic_hit': bool(
                    target_profile['automatic_hit'] or paid_automatic_back_attack
                    or mutant_profile_data.get('automatic_attacks')
                ),
                'paid_automatic_back_attack': paid_automatic_back_attack,
                'block_penalty': target_profile['block_penalty'],
                'block_arm': target_profile['block_arm'],
                'block_counterattack': target_profile['block_counterattack'],
                'aimed_penalty': target_profile['aimed_penalty'],
                'swing_bonus': swing_prepared,
                'melee_advantage': target_profile['advantage'],
                'melee_bonus': melee_bonus,
                'weapon_accuracy': accuracy,
            }

        def prepare_firearm(weapon_index,shot_count,action_points,volley_count,target_character_ids,area_center_x,area_center_y,*,paired_fire=False):
            from app.services.sniper_position import require_reposition
            require_reposition(character,state)
            if fire_mode not in {'unaimed', 'rapid', 'aimed', 'burst', 'suppression', 'area'}:
                raise ValidationError("Unknown fire mode")
            weapons = (character.character.data or {}).get('weapons') or []
            weapon_index = CombatService._coerce_int(weapon_index, -1)
            if weapon_index < 0 or weapon_index >= len(weapons):
                raise ValidationError("Weapon not found")
            if not held(character,weapon_index):
                raise ValidationError("Draw this weapon first")
            weapon = weapons[weapon_index] or {}
            if paired_fire:
                from app.services.weapon_grip import ensure_usable,melee,revolver
                ensure_usable(character,weapon)
                if melee(weapon) or CombatService._weapon_class(weapon)=='grenade_launcher':
                    raise ValidationError('Общая стрельба доступна для двух огнестрельных оружий')
                if revolver(weapon) and fire_mode in {'burst','area','suppression'}:
                    raise ValidationError('При парном хвате револьвером нельзя стрелять очередью')
            CombatService._weapon_durability(weapon)
            active_jam = weapon.get('jam') if isinstance(weapon.get('jam'), dict) else None
            active_jam_effects = CombatService._weapon_jam_effects(weapon)
            if active_jam_effects['blocks_fire']:
                raise ValidationError("Clear the weapon jam before firing")
            if weapon.get('requiresManualCycle'):
                raise ValidationError("Cycle the weapon action before firing")
            profile = weapon.get('fireModes') or (weapon.get('attributes') or {}).get('fire_modes')
            if not profile and weapon.get('templateId'):
                template = db.session.get(ItemTemplate, weapon.get('templateId'))
                profile = (template.attributes or {}).get('fire_modes') if template else None
            profile = profile or {}
            from app.services.equipment_modifications import fire_profile as modified_fire_profile,rule as modification_rule
            profile=modified_fire_profile(weapon,profile,CombatService._weapon_fire_rate(weapon))
            ergonomics_profile = CombatService._weapon_ergonomics_profile(
                character,
                weapon,
                weapon_index,
            )
            strength_profile = CombatService._weapon_strength_profile(
                character,
                weapon,
            )
            shots = max(1, CombatService._coerce_int(shot_count, 1))
            volley_count = CombatService._coerce_int(volley_count, 1)
            single_options = profile.get('single_shot_options') or [1]
            supports_burst = bool(profile.get('supports_burst'))
            machine_gun = bool(profile.get('machine_gun_burst'))
            burst_size = CombatService._coerce_int(profile.get('burst_size'), 0)

            if fire_mode == 'area':
                expected_area_shots = CombatService._area_fire_shot_count(profile)
                if (
                    volley_count != 2
                    or expected_area_shots <= 0
                    or shots != expected_area_shots
                ):
                    raise ValidationError(
                        "Area fire requires two weapon bursts or 10 machine gun shots"
                    )

            single_fire = fire_mode in {'unaimed', 'rapid', 'aimed'}
            if facial_profile(data)['eyes']==2:raise ValidationError('Нельзя стрелять при потере обоих глаз')
            requested_action_points = CombatService._coerce_int(action_points, 0)
            expected_action_points = {
                'unaimed': 1 if CombatService._is_pistol_weapon(weapon) else 2,
                'rapid': 1,
                'aimed': ergonomics_profile['aimed_shot_action_points'],
                'burst': CombatService._burst_action_points(weapon),
                'area': 5,
            }.get(fire_mode)
            if fire_mode == 'suppression':
                if requested_action_points not in {3, 5}:
                    raise ValidationError("Suppression costs 3 or 5 action points")
                expected_action_points = requested_action_points
            if fire_mode=='burst' and background_is(data,'Полицейский') and CombatService._weapon_class(weapon) in {'pistol','submachine_gun'}:
                expected_action_points=max(1,expected_action_points-1)
            from app.services.sniper_trauma import declared
            sniper_mode=declared(data,weapon,fire_mode,attribute_choice)
            if sniper_mode=='precision':expected_action_points+=1
            if not paired_fire and requested_action_points != expected_action_points:
                raise ValidationError("Invalid action point cost")
            allowed_volley_counts = {1}
            if fire_mode == 'area':
                allowed_volley_counts = {2}
            elif fire_mode == 'suppression' and expected_action_points == 5:
                allowed_volley_counts = {1, 2}
            if volley_count not in allowed_volley_counts:
                raise ValidationError("Invalid volley count")
            if fire_mode == 'rapid' and character.rapid_fire_round == state.round_number:
                raise ValidationError("Rapid fire can only be used once per turn")
            if fire_mode == 'aimed' and (
                character.aimed_target_character_id != target_character_id or
                character.aimed_weapon_index != weapon_index
            ):
                raise ValidationError("Aiming is required before an aimed shot")
            if fire_mode == 'aimed' and target_zone not in HIT_ZONES:
                raise ValidationError("Choose a valid body part for an aimed shot")
            if single_fire and shots not in single_options:
                raise ValidationError("Unsupported single-fire option")
            if not single_fire and not supports_burst:
                raise ValidationError("Weapon does not support automatic fire")
            if not single_fire and not machine_gun and shots != burst_size * volley_count:
                raise ValidationError("Invalid burst size")
            if not single_fire and machine_gun and (
                shots < 2 * volley_count or shots % volley_count != 0
            ):
                raise ValidationError("Invalid machine gun burst size")
            if not single_fire and machine_gun and background_is(data,'Пост принял') and shots<10*volley_count:
                raise ValidationError('Предыстория «Пост принял»: минимум 10 выстрелов в пулемётной очереди')
            fire_rate_state = CombatService._validate_weapon_fire_rate(
                combat_meta, current_round, weapon_index, weapon, shots,data,
            )
            target_object = None
            if target_x is not None or target_y is not None:
                raise ValidationError('Для стрельбы по скрытой цели выберите укрытие, а не запомненную клетку')
            if fire_mode == 'suppression':
                target_object = LocationObject.query.filter_by(
                    id=target_object_id,
                    location_id=location_id,
                ).first()
                if not target_object:
                    raise ValidationError("Cover object is required")
            elif fire_mode == 'area':
                target_character_ids = list(dict.fromkeys(target_character_ids or []))
                if not 1 <= len(target_character_ids) <= 3:
                    raise ValidationError("Area fire requires 1 to 3 targets")
                targets = LocationCharacter.query.filter(
                    LocationCharacter.location_id == location_id,
                    LocationCharacter.character_id.in_(target_character_ids),
                ).all()
                if len(targets) != len(target_character_ids):
                    raise ValidationError("Area fire target not found")
                area_center_x = CombatService._coerce_int(area_center_x, -1)
                area_center_y = CombatService._coerce_int(area_center_y, -1)
                if not (
                    0 <= area_center_x < location.grid_width and
                    0 <= area_center_y < location.grid_height
                ):
                    raise ValidationError("Area fire center is invalid")
                if any(
                    abs(target.pos_x - area_center_x) > 2 or abs(target.pos_y - area_center_y) > 2
                    for target in targets
                ):
                    raise ValidationError("Area fire targets must fit inside a 5 by 5 area")
            elif not target_character_id:
                target_object = LocationObject.query.filter_by(
                    id=target_object_id,
                    location_id=location_id,
                ).first()
                if not target_object or not CombatService._is_cover_object(target_object):
                    raise ValidationError("Target character or cover is required")

            magazine = weapon.get('installedMagazine') or {}
            ammo_stacks = magazine.get('ammo')
            if isinstance(ammo_stacks, list):
                available_ammo = sum(
                    max(0, CombatService._coerce_int(stack.get('quantity'), 0))
                    for stack in ammo_stacks
                    if isinstance(stack, dict)
                )
            else:
                available_ammo = max(0, CombatService._coerce_int(weapon.get('ammo'), 0))
            if available_ammo < shots:
                raise ValidationError("Not enough ammo")
            range_target = None
            if target_character_id:
                range_target = LocationCharacter.query.filter_by(
                    location_id=location_id,
                    character_id=target_character_id,
                ).first()
                if not range_target or range_target.id == character.id:
                    raise ValidationError("Target character not found")
            target_distance = (
                CombatService._character_distance(character,range_target)
                if range_target
                else (
                    max(
                        abs(character.pos_x - target_object.tile_x),
                        abs(character.pos_y - target_object.tile_y),
                    )
                    if target_object else None
                )
            )
            if fire_mode == 'area':
                facing_target_x, facing_target_y = area_center_x, area_center_y
            elif target_object:
                facing_target_x, facing_target_y = target_object.tile_x, target_object.tile_y
            elif range_target:
                facing_target_x, facing_target_y = range_target.pos_x, range_target.pos_y
            else:
                facing_target_x, facing_target_y = None, None
            if (
                facing_target_x is not None
                and not CombatService._is_in_facing_arc(
                    character, facing_target_x, facing_target_y,
                )
            ):
                raise ValidationError("The target is outside the character's field of view")
            weapon_range = CombatService._coerce_int(
                weapon.get('range', (weapon.get('attributes') or {}).get('range')),
                0,
            )
            from app.services.weapon_modules import weapon_stat,conditional_accuracy,uses_nvg,installed
            weapon_range=max(0,weapon_stat(weapon,'range',weapon_range))
            accuracy_in_range = bool(
                target_distance is not None
                and weapon_range > 0
                and target_distance <= weapon_range
            )
            close_range_accuracy_bonus = CombatService._close_range_weapon_accuracy_bonus(
                weapon, target_distance,
            )
            sniper_close_range_penalty = CombatService._sniper_close_range_penalty(
                weapon, target_distance,
            )
            target_movement_mode = (
                range_target.movement_mode_this_turn if range_target else None
            )
            if fire_mode == 'aimed' and target_movement_mode == 'sprint' and not background_is(data, 'Снайпер'):
                raise ValidationError('An aimed shot cannot target a sprinting character')
            movement_modifiers = CombatService._shooting_movement_modifiers(
                character.movement_mode_this_turn,
                target_movement_mode,
                CombatService._movement_evasion_active(range_target),
                character_data=data,
            )
            movement_modifiers['difficulty_penalty'] += CombatService._target_evasion_trait_penalty(range_target)
            from app.services.portable_lights import shooting as portable_shooting
            portable_profile=portable_shooting(character,range_target,fire_mode) if range_target else {'lit':False,'accuracy':0,'aimed_blocked':False}
            if portable_profile['aimed_blocked']:
                raise ValidationError('Нельзя прицельно стрелять через ПНВ в направленный на вас свет')
            base_shooting_disadvantage = (
                CombatService._has_roll_disadvantage(
                    data, 'skills.physical.shooting',
                    darkness=not portable_profile['lit'] and not uses_nvg(data) and CombatService._is_dark_at_position(
                        location, facing_target_x, facing_target_y,
                    ),
                )
                or movement_modifiers['disadvantage']
            )
            if fire_mode=='aimed' and installed(weapon,'night_scope') and not uses_nvg(data):
                # Прицел снимает только темноту, остальные причины помехи сохраняются.
                base_shooting_disadvantage=(CombatService._has_roll_disadvantage(data,'skills.physical.shooting',darkness=False)
                                            or movement_modifiers['disadvantage'])
            if (
                range_target
                and CombatService._mutant_has_trait(
                    range_target.character.data if range_target.character else {},
                    'Полёт',
                )
                and CombatService._coerce_float(range_target.movement_distance_this_turn,0)
                    - CombatService._coerce_float(range_target.correction_distance_this_turn,0) > 0
                and range_target.movement_mode_this_turn != 'correction'
            ):
                base_shooting_disadvantage = True
            rapid_fire_accuracy_penalty = (
                CombatService._rapid_fire_accuracy_penalty(weapon)
                if fire_mode == 'rapid'
                else 0
            )
            if fire_mode=='rapid':
                if trait_count(data,'Беглый стрелок'):rapid_fire_accuracy_penalty=-2 if rapid_fire_accuracy_penalty<0 else 2
                if background_is(data,'Спринтер'):rapid_fire_accuracy_penalty*=2
            area_fire_accuracy_penalty = (
                CombatService._area_fire_accuracy_penalty(weapon)
                if fire_mode == 'area'
                else 0
            )
            attack_details = {
                'weapon_index': weapon_index,
                'fire_mode': fire_mode,
                'sniper_mode':sniper_mode,
                'shot_count': shots,
                'volley_count': volley_count,
                'machine_gun_burst': machine_gun,
                'fire_rate': fire_rate_state['fire_rate'],
                'shots_fired_before': fire_rate_state['fired'],
                'action_points': expected_action_points,
                'round_number': state.round_number,
                'target_character_id': target_character_id,
                'target_character_ids': target_character_ids,
                'target_object_id': target_object_id,
                'direct_cover_attack': bool(target_object and not target_character_id),
                'area_center_x': area_center_x,
                'area_center_y': area_center_y,
                'posture': CombatService._posture_key(character),
                'posture_shooting_bonus': POSTURES[CombatService._posture_key(character)]['shooting_bonus'],
                'posture_ergonomics_bonus': 0 if background_is(data,'Пост принял') else POSTURES[CombatService._posture_key(character)]['ergonomics_bonus'],
                'shooter_movement_mode': character.movement_mode_this_turn,
                'target_movement_mode': target_movement_mode,
                'movement_accuracy_penalty': movement_modifiers['difficulty_penalty'],
                'rapid_fire_accuracy_penalty': rapid_fire_accuracy_penalty,
                'area_fire_accuracy_penalty': area_fire_accuracy_penalty,
                'ergonomics': ergonomics_profile,
                'strength_requirement': strength_profile,
                'target_distance': target_distance,
                'weapon_range': weapon_range,
                'weapon_class': CombatService._weapon_class(weapon),
                'close_range_accuracy_bonus': close_range_accuracy_bonus,
                'sniper_close_range_penalty': sniper_close_range_penalty,
                'ergonomics_accuracy_applied': (
                    ergonomics_profile['accuracy_modifier']
                    if accuracy_in_range
                    else 0
                ),
                'artifact_accuracy_bonus': artifact_passive_profile(data)['accuracy'],
                'shooting_roll_modifier': CombatService._health_roll_modifier(
                    data,
                    'skills.physical.shooting',
                ),
                'shooting_disadvantage': (
                    base_shooting_disadvantage
                    or active_jam_effects['shooting_disadvantage']
                ),
                'base_shooting_disadvantage': base_shooting_disadvantage,
                'weapon_jam_before_shot': deepcopy(active_jam),
                'weapon_jams_before_shot': active_jam_effects['jams'],
                'weapon_jam_accuracy_penalty': active_jam_effects['accuracy_penalty'],
                'aim_accuracy_bonus': CombatService._aim_bonus_for_target(
                    character,
                    target_character_id,
                    weapon_index,
                ),
                'weapon_braced': bool(
                    character.weapon_braced
                    and character.braced_weapon_index == weapon_index
                ),
                'brace_accuracy_bonus': (
                    1
                    if character.weapon_braced and character.braced_weapon_index == weapon_index
                    else 0
                ),
                'brace_ergonomics_bonus': (
                    (50 if background_is(data,'Пост принял') else 75)
                    if any(
                        isinstance(module, dict)
                        and module.get('slotType') == 'handguard'
                        and (
                            module.get('bipod')
                            or (module.get('attributes') or {}).get('bipod')
                            or module.get('name') == 'Сошки'
                        )
                        for module in (weapon.get('installedModules') or [])
                    )
                    and (
                        character.posture == 'prone'
                        or (
                            character.weapon_braced
                            and character.braced_weapon_index == weapon_index
                        )
                    )
                    else (
                        10
                        if character.weapon_braced and character.braced_weapon_index == weapon_index
                        else 0
                    )
                ),
            }
            shooting_bonus = CombatService._skill_modifier(
                data, 'skills.physical.shooting'
            )
            weapon_accuracy = CombatService._coerce_int(
                weapon.get('accuracy', (weapon.get('attributes') or {}).get('accuracy')), 0
            )
            module_accuracy=conditional_accuracy(weapon,fire_mode=fire_mode,distance=target_distance,
                                                 posture=CombatService._posture_key(character),nvg=uses_nvg(data))
            weapon_accuracy=weapon_stat(weapon,'accuracy',weapon_accuracy)+module_accuracy
            if fire_mode=='aimed':weapon_accuracy+=modification_rule(weapon,'aimed_accuracy')
            if attack_details.get('weapon_braced') or strength_profile.get('ignored_by_bipod') or attack_details.get('aim_accuracy_bonus'):
                from app.services.equipment_modifications import worn_rule
                weapon_accuracy+=worn_rule(data,'supported_accuracy')
            attack_details['weapon_accuracy']=weapon_accuracy
            attack_details['module_conditional_accuracy']=module_accuracy
            hit_difficulty = 12 - shooting_bonus - weapon_accuracy
            weather=weather_at(location,character)
            hit_difficulty-=weather['accuracy']
            attack_details['weather']=weather
            if weather['accuracy_disadvantage']:
                attack_details['shooting_disadvantage']=attack_details['base_shooting_disadvantage']=True
            hit_difficulty-=portable_profile['accuracy']
            if portable_profile['lit'] and CombatService._is_dark_at_position(location,facing_target_x,facing_target_y) and not uses_nvg(data):
                hit_difficulty+=0 if portable_profile.get('improved') else 3
            attack_details['portable_light']=portable_profile
            hit_difficulty -= weapon_accuracy_bonus(data,weapon)
            hit_difficulty += CombatService._equipment_accuracy_penalty(data)
            if not portable_profile['lit'] and CombatService._is_dark_at_position(location, facing_target_x, facing_target_y):
                from app.services.equipment_passives import darkness_modifier
                if not (fire_mode=='aimed' and installed(weapon,'night_scope') and not uses_nvg(data)):
                    hit_difficulty -= darkness_modifier(data, 'accuracy_penalty')
            hit_difficulty += strength_profile['accuracy_penalty']
            from app.services.weapon_grip import firearm_penalty
            pair_penalty=firearm_penalty(character,weapon)
            hit_difficulty+=pair_penalty
            attack_details['paired_weapon_accuracy_penalty']=pair_penalty
            hit_difficulty += attack_details['weapon_jam_accuracy_penalty']
            hit_difficulty -= attack_details['ergonomics_accuracy_applied']
            hit_difficulty -= attack_details['artifact_accuracy_bonus']
            hit_difficulty -= attack_details['posture_shooting_bonus']
            hit_difficulty += CombatService._coerce_int(
                attack_details['aim_accuracy_bonus'] * -1, 0
            )
            hit_difficulty += movement_modifiers['difficulty_penalty']
            hit_difficulty += rapid_fire_accuracy_penalty
            hit_difficulty += area_fire_accuracy_penalty
            hit_difficulty -= close_range_accuracy_bonus
            hit_difficulty += sniper_close_range_penalty
            if target_distance is not None and weapon_range and target_distance > weapon_range:
                hit_difficulty += 2
                if target_distance > weapon_range + 10:
                    attack_details['shooting_disadvantage'] = True
                    attack_details['base_shooting_disadvantage'] = True
            if range_target:
                range_target_data = (
                    range_target.character.data
                    if range_target.character and isinstance(range_target.character.data, dict)
                    else {}
                )
                if target_zone=='head' and fire_mode in {'aimed','rapid'}:
                    from app.services.equipment_modifications import worn_rule
                    hit_difficulty+=worn_rule(range_target_data,'aimed_head_difficulty')
                if CombatService._mutant_camouflage_active(
                    range_target_data, current_round,
                ):
                    if fire_mode in {'aimed', 'rapid'}:
                        raise ValidationError(
                            'По замаскированному мутанту недоступен прицельный и беглый выстрел'
                        )
                    hit_difficulty += 8
                    attack_details['mutant_camouflage_penalty'] = 8
                mutant_target_modifier = CombatService._mutant_shooting_target_modifier(
                    range_target_data,
                )
                hit_difficulty -= mutant_target_modifier
                attack_details['mutant_target_accuracy_modifier'] = mutant_target_modifier
                cover_analysis = CombatService._cover_analysis(
                    location_id,
                    character,
                    range_target,
                )
                if not cover_analysis['targetable']:
                    cover_analysis['blind_fire'] = True
                    cover_analysis['targetable'] = True
                attack_details['cover'] = cover_analysis
                hit_difficulty += cover_analysis.get('accuracy_penalty', 0)
                if range_target.grapple_live_shield and range_target.grapple_target_id:
                    live_shield = CombatService._live_shield_target(range_target)
                    live_shield_zones = [
                        zone for zone in HIT_ZONES if zone != 'head'
                    ]
                    cover_analysis.update({
                        'grade': 'three_quarters',
                        'blocked_zones': list(dict.fromkeys(
                            list(cover_analysis.get('blocked_zones') or [])
                            + live_shield_zones
                        )),
                        'accuracy_penalty': 0,
                        'disadvantage': False,
                        'targetable': True,
                        'live_shield': True,
                        'live_shield_character_id': (
                            live_shield.character_id if live_shield else None
                        ),
                    })
                if fire_mode == 'aimed' and target_zone in cover_analysis['blocked_zones']:
                    raise ValidationError('Эта часть тела закрыта укрытием: прицельный выстрел невозможен')
            if fire_mode == 'aimed':
                hit_difficulty += CombatService._aimed_zone_difficulty_penalty(target_zone)
            if target_object and not target_character_id:
                attack_details['continuation_hit_difficulty'] = max(1, hit_difficulty)
                attack_details['continuation_hit_difficulty_without_weapon_jam'] = max(
                    1,
                    hit_difficulty - attack_details['weapon_jam_accuracy_penalty'],
                )
            attack_details['hit_difficulty'] = max(1, hit_difficulty)
            attack_details['hit_difficulty_without_weapon_jam'] = max(
                1,
                hit_difficulty - attack_details['weapon_jam_accuracy_penalty'],
            )
            attack_details['target_zone'] = target_zone if fire_mode == 'aimed' else None
            return attack_details

        if action_key=='attack' and fire_mode:
            paired_fire=attribute_choice=='paired_fire'
            if paired_fire:
                from app.services.weapon_grip import secondary_index
                if not paired or fire_mode=='aimed':raise ValidationError('Общая стрельба требует двух оружий и неприцельного режима')
                indices=[character.drawn_weapon_index,secondary_index(character)]
                prepared_attacks=[]
                for index in indices:
                    item=(data.get('weapons') or [])[index]
                    fire_profile=item.get('fireModes') or CombatService._template_attributes(item).get('fire_modes') or {}
                    from app.services.equipment_modifications import fire_profile as modified_fire_profile
                    fire_profile=modified_fire_profile(item,fire_profile,CombatService._weapon_fire_rate(item))
                    paired_shots=shot_count
                    if fire_mode in {'burst','suppression','area'} and not fire_profile.get('machine_gun_burst'):
                        paired_shots=CombatService._coerce_int(fire_profile.get('burst_size'),0)*CombatService._coerce_int(volley_count,1)
                    elif fire_profile.get('machine_gun_burst'):
                        paired_shots=(10 if fire_mode=='area' else max(CombatService._coerce_int(shot_count,1),
                            (10 if background_is(data,'Пост принял') else 2)*CombatService._coerce_int(volley_count,1))) if fire_mode in {'burst','suppression','area'} else shot_count
                    if fire_mode in {'area','suppression'} and not fire_profile.get('supports_area_fire' if fire_mode=='area' else 'supports_suppression'):
                        raise ValidationError('Общий режим недоступен одному из оружий')
                    details=prepare_firearm(index,paired_shots,action_points,volley_count,target_character_ids,area_center_x,area_center_y,paired_fire=True)
                    from app.services.weapon_grip import ignores_pair_penalties
                    if not ignores_pair_penalties(character,data['weapons'][index]):
                        details['shooting_disadvantage']=details['base_shooting_disadvantage']=True
                    details['paired_fire']=True
                    prepared_attacks.append(details)
                priced=min(prepared_attacks,key=lambda details:(details['ergonomics']['value'],-details['action_points']))
                special_action_cost=priced['action_points']
                if CombatService._coerce_int(action_points,0)!=special_action_cost:raise ValidationError('Неверная стоимость общей стрельбы')
                attack_details=prepared_attacks[0]
            else:
                attack_details=prepare_firearm(weapon_index,shot_count,action_points,volley_count,target_character_ids,area_center_x,area_center_y)
            single_fire=fire_mode in {'unaimed','rapid','aimed'}

        if action_key == 'convert_free_action_to_movement':
            gain = max(0, DEFAULT_CONVERSION_BASE - CombatService._movement_penalty(character))
            if character.free_actions_current >= 1:
                character.free_actions_current -= 1
            elif character.action_points_current >= 2:
                character.action_points_current -= 2
            else:
                raise ValidationError("Not enough action points")
            character.movement_points_max += gain
            from app.services.grenade_reaction import split_movement_gain
            character.movement_points_current += split_movement_gain(character,gain)
            CombatService._clear_aim(character)
        elif action_key in {'change_posture', 'change_facing'}:
            CombatService._clear_aim(character)
        elif action_key == 'stow_weapon':
            CombatService._set_active_weapon(character, None)
            CombatService._clear_aim(character)
        else:
            action_point_cost = (
                special_action_cost
                if special_action_cost is not None
                else (
                    attack_details['action_points']
                    if action_key == 'attack' and attack_details
                    else action['action_points']
                )
            )
            if action_key=='melee_swing':action_point_cost=trait_action_cost(data,'swing',action_point_cost)
            if not resumed_paid_action and character.action_points_current < action_point_cost:
                if not pending_action_id:
                    raise ValidationError("Not enough action points")
                paid_action_points = max(0, character.action_points_current)
                character.action_points_current = 0
                combat_meta['pendingAction'] = {
                    'id': str(pending_action_id),
                    'label': str(
                        narrative_action_details.get('name')
                        if action_key == 'narrative_action' and narrative_action_details
                        else (action.get('label') or action_key)
                    ),
                    'total_action_points': action_point_cost,
                    'remaining_action_points': action_point_cost - paid_action_points,
                }
                combat_meta.pop('completedPendingActionId', None)
                remember_action(character, combat_meta['pendingAction'], deferred_request)
                character.character.data = data
                flag_modified(character.character, 'data')
                character.last_action = db.func.now()
                db.session.commit()
                ended_state = CombatService.end_turn(
                    location_id,
                    user_id,
                    location_character_id=character.id,
                )
                return {
                    'character': CombatService._serialize_character(
                        character,
                        current_turn_id=ended_state.get('current_location_character_id'),
                    ),
                    'state': ended_state,
                    'action': action_key,
                    'pending_action': True,
                    'pending_action_id': str(pending_action_id),
                    'attack': None,
                    'aim': None,
                    'posture_change': None,
                    'draw_weapon': None,
                    'reload_weapon': None,
                    'reload_underbarrel': None,
                    'clear_weapon_jam': None,
                    'narrative_action': None,
                    'cover': None,
                    'brace_weapon': None,
                    'melee_action': None,
                    'equipment_change': None,
                    'module_change': None,
                }
            if not resumed_paid_action:
                character.action_points_current -= action_point_cost
            if action_key=='evade':
                combat_meta['standaloneEvasion']=max(0,5+CombatService._skill_modifier(data,'skills.physical.agility'))
                flag_modified(character.character,'data')
            if attack_details and attack_details.get('fencer'):combat_meta['fencerUsed']=True
            if order_plan:
                from app.services.brigadier_order import create
                narrative_action_details={'name':'Указание Бригадира','order':create(character,user_id,*order_plan),'roll_required':False}
            if action_key=='surgeon_reduction':
                from app.services.surgeon_reduction import apply
                narrative_action_details=apply(character,reduction_plan,user_id)
                data=character.character.data
                health=data.setdefault('health',{})
                combat_meta=health.setdefault('combatMeta',{})
            if action_key in {'jump','recover_jump_balance'}:
                from app.services.character_jump import execute_jump, recover_balance
                jump_details = (execute_jump(location,character,jump_details,current_round)
                    if action_key=='jump' else recover_balance(location,character,current_round))
                data = character.character.data
                health = data.setdefault('health',{})
                combat_meta = health.setdefault('combatMeta',{})
            if action_key == 'draw_weapon':
                if draw_details.get('secondary'):
                    from app.services.weapon_grip import take_secondary
                    take_secondary(character,weapon_index)
                else:
                    CombatService._set_active_weapon(character, weapon_index)
                    if draw_details.get('grip'):
                        meta=character.character.data.setdefault('health',{}).setdefault('combatMeta',{})
                        meta.update(weaponGripHands=draw_details['grip']['hands'],weaponPrimaryHand=draw_details['grip']['hand'])
                data = character.character.data
                health=data.setdefault('health',{})
                combat_meta=health.setdefault('combatMeta',{})
                CombatService._clear_aim(character)
            if action_key == 'throw_item':
                from app.services.thrown_items import execute as execute_throw
                thrown_details=execute_throw(location,character,data,thrown_details,current_round)
                character.character.data=data
                flag_modified(character.character,'data')
                CombatService._clear_aim(character)
            if action_key != 'attack' and combat_meta.pop('mutantRageAccuracy', None) is not None:
                character.character.data = data
                flag_modified(character.character, 'data')
            if CombatService._mutant_has_trait(data, 'Возможность разгона'):
                activity = combat_meta.setdefault('mutantTurnActivity', {
                    'round': current_round, 'moved': False, 'other': False,
                })
                activity['other'] = True
                combat_meta.pop('mutantAccelerationActive', None)
                character.character.data = data
                flag_modified(character.character, 'data')
            if action_key != 'mutant_ambush' and combat_meta.pop('mutantAmbushActive', None):
                character.character.data = data
                flag_modified(character.character, 'data')
            if action_key == 'attack' and combat_meta.pop('mutantCamouflageActive', None):
                combat_meta.pop('mutantCamouflageUntilRound', None)
                character.character.data = data
                flag_modified(character.character, 'data')
            if action_key == 'mutant_jump' and mutant_action_details:
                character.pos_x = mutant_action_details['target_x']
                character.pos_y = mutant_action_details['target_y']
                character.cover_object_id = None
                character.weapon_braced = False
                character.braced_weapon_index = None
                CombatService._clear_aim(character)
            elif action_key == 'mutant_ambush' and mutant_action_details:
                combat_meta['mutantAmbushActive'] = True
                character.character.data = data
                flag_modified(character.character, 'data')
            elif action_key == 'mutant_battle_cry' and mutant_action_details:
                for opponent in CombatService._unique_location_characters(
                    LocationCharacter.query.filter_by(location_id=location_id).all()
                ):
                    if (
                        opponent.id == character.id
                        or not opponent.character
                        or not CombatService._are_opponents(character, opponent)
                        or math.hypot(
                            opponent.pos_x - character.pos_x,
                            opponent.pos_y - character.pos_y,
                        ) > 15
                        or CombatService._location_character_condition(opponent)['state'] == 'dead'
                    ):
                        continue
                    opponent_data = (
                        opponent.character.data
                        if isinstance(opponent.character.data, dict) else {}
                    )
                    if CombatService._deafness_level(opponent_data) >= 90:
                        continue
                    modifier = CombatService._skill_modifier(
                        opponent_data, 'skills.physical.will',
                    )
                    roll = random.randint(1, 20)
                    total = roll + modifier
                    success = roll == 20 or (roll != 1 and total >= 15)
                    stress = None
                    if not success:
                        stress = CombatService.apply_stress_trigger(
                            opponent, 1, trigger='mutant_battle_cry',
                        )
                    mutant_action_details['targets'].append({
                        'character_id': opponent.character_id,
                        'name': opponent.character.name,
                        'roll': roll, 'modifier': modifier, 'total': total,
                        'difficulty': 15, 'success': success, 'stress': stress,
                    })
                CombatService._mark_mutant_attack_used(character, current_round)
            elif action_key == 'mutant_camouflage' and mutant_action_details:
                combat_meta['mutantCamouflageActive'] = True
                combat_meta['mutantCamouflageUsedRound'] = current_round
                combat_meta['mutantCamouflageUntilRound'] = mutant_action_details['until_round']
                character.character.data = data
                flag_modified(character.character, 'data')
            elif action_key == 'mutant_psy_attack' and mutant_action_details:
                target = mutant_action_details.pop('target')
                target_data = target.character.data if target.character and isinstance(target.character.data, dict) else {}
                attack_name = mutant_action_details['attack_name'].casefold()
                psy_amount = 20 if attack_name == 'пси-удар' else (10 if attack_name == 'зомбирование' else 0)
                mutant_action_details['psy_state'] = CombatService._increase_psy_state(
                    target_data, psy_amount, attack_name, loc_char=target,
                ) if psy_amount else None
                difficulty = 15 if attack_name in {'пси-удар', 'приказ смерти'} else 18
                save = CombatService._mutant_saving_throw(
                    target, 'skills.physical.will', difficulty,
                )
                mutant_action_details['save'] = save
                if attack_name == 'пси-удар' and not save['success']:
                    apply_effect_to_health(target_data.setdefault('health', {}), {
                        'type': 'concussion', 'name': 'Контузия',
                        'remaining': 1, 'tick': 'turn_end',
                        'source': 'psy_strike', 'roll_modifier': -3,
                    })
                    mutant_action_details['dropped'] = CombatService._apply_fall_or_drop(
                        target, target_data, 'right_arm',
                    )
                elif attack_name == 'зомбирование' and not save['success']:
                    health = target_data.setdefault('health', {})
                    meta = health.setdefault('combatMeta', {})
                    meta['zombificationPreviousController'] = target.controlled_by
                    meta['zombificationController'] = location.lobby.gm_id
                    target.controlled_by = location.lobby.gm_id
                    apply_effect_to_health(health, {
                        'type': 'zombification', 'name': 'Зомбирование',
                        'source': 'psyionik', 'active': True,
                    })
                elif attack_name == 'приказ смерти':
                    effects = normalize_effect_list(
                        target_data.setdefault('health', {}).get('effects') or []
                    )
                    if not any(
                        item.get('active', True) and item.get('type') == 'zombification'
                        for item in effects
                    ):
                        raise ValidationError('Приказ смерти действует только на Зомбированного')
                    if not save['success']:
                        apply_effect_to_health(target_data['health'], {
                            'type': 'death_order', 'name': 'Приказ смерти',
                            'source': 'psyionik', 'active': True,
                        })
                target.character.data = target_data
                flag_modified(target.character, 'data')
                CombatService._sync_location_effects_from_character(target)
            elif action_key == 'mutant_psy_roar' and mutant_action_details:
                combat_participant_ids = set(state.turn_order or [])
                for opponent in CombatService._unique_location_characters(
                    LocationCharacter.query.filter_by(location_id=location_id).all()
                ):
                    if (
                        opponent.id == character.id
                        or opponent.id not in combat_participant_ids
                        or not opponent.character
                        or not CombatService._are_opponents(character, opponent)
                        or CombatService._location_character_condition(opponent)['state'] == 'dead'
                    ):
                        continue
                    opponent_data = opponent.character.data if isinstance(opponent.character.data, dict) else {}
                    psy_state = CombatService._increase_psy_state(
                        opponent_data, 7, 'psy_roar', loc_char=opponent,
                    )
                    save = CombatService._mutant_saving_throw(
                        opponent, 'skills.physical.will', 16,
                    )
                    if not save['success']:
                        bonus_psy = CombatService._increase_psy_state(
                            opponent_data, 3, 'psy_roar_failed_save', loc_char=opponent,
                        )
                        apply_effect_to_health(opponent_data.setdefault('health', {}), {
                            'type': 'psy_roar_disadvantage',
                            'name': 'Последствия Пси-рёва',
                            'remaining': 1,
                            'tick': 'turn_end',
                            'source': 'psy_roar',
                            'disadvantage_skills': [
                                'skills.physical.awareness',
                                'skills.physical.will',
                                'skills.physical.shooting',
                            ],
                        })
                    else:
                        bonus_psy = None
                    opponent.character.data = opponent_data
                    flag_modified(opponent.character, 'data')
                    mutant_action_details['targets'].append({
                        'character_id': opponent.character_id,
                        'name': opponent.character.name,
                        'psy_state': psy_state,
                        'bonus_psy_state': bonus_psy,
                        'save': save,
                    })
            elif action_key == 'mutant_psy_sleep' and mutant_action_details:
                target = mutant_action_details.pop('target')
                target_data = target.character.data if target.character and isinstance(target.character.data, dict) else {}
                save = CombatService._mutant_saving_throw(
                    target, 'skills.physical.will', 10,
                )
                mutant_action_details['save'] = save
                if not save['success']:
                    margin = max(1, 10 - save['total'])
                    if margin < 4:
                        effect = {
                            'remaining': 1, 'tick': 'turn_end',
                            'duration_label': '1 раунд',
                        }
                    elif margin < 7:
                        effect = {
                            'remaining': 10, 'time_unit': 'minute',
                            'tick': 'time_elapsed', 'duration_label': '10 минут',
                        }
                    else:
                        effect = {
                            'remaining': 1, 'time_unit': 'hour',
                            'tick': 'time_elapsed', 'duration_label': '1 час',
                        }
                    apply_effect_to_health(target_data.setdefault('health', {}), {
                        'type': 'unconsciousness', 'name': 'Пси-сон',
                        'source': 'psy_cat', **effect,
                    })
                    target.posture = 'prone'
                    mutant_action_details['duration'] = effect['duration_label']
                target.character.data = target_data
                flag_modified(target.character, 'data')
                CombatService._sync_location_effects_from_character(target)
            elif action_key == 'mutant_object_attack' and mutant_action_details:
                target = mutant_action_details.pop('target')
                thrown_object = mutant_action_details.pop('object')
                object_profile = mutant_action_details.pop('profile')
                save = CombatService._mutant_saving_throw(
                    target, 'skills.physical.agility',
                    object_profile['difficulty'],
                )
                mutant_action_details['save'] = save
                mutant_action_details['damage'] = 0
                if not save['success']:
                    base_damage = (
                        object_profile['damage_multiplier']
                        * mutant_action_details['object_weight']
                    )
                    damage_profile = {
                        'damage': base_damage,
                        'armor_piercing': 0,
                        'damage_type': object_profile['damage_type'],
                        'effective_range': 0,
                    }
                    if object_profile['damage_type'] == 'crushing':
                        attack_result = CombatService._resolve_attack(
                            target,
                            character,
                            {
                                'weapon': {
                                    'name': mutant_action_details['attack_name'],
                                    'attributes': damage_profile,
                                },
                                'fire_mode': 'mutant_object',
                                'hit_difficulty': 1,
                                'automatic_firearm_hit': True,
                                'target_distance': mutant_action_details['target_distance'],
                                'round_number': current_round,
                            },
                            forced_roll=20,
                            profile_override=damage_profile,
                            profile_adjusted=True,
                            ignore_cover=True,
                            ignore_live_shield=True,
                        )
                    else:
                        zone = CombatService._random_hit_zone(random.randint(1, 20))
                        target_data = (
                            target.character.data
                            if target.character and isinstance(target.character.data, dict)
                            else {}
                        )
                        protection = CombatService._target_elemental_protection(
                            target_data, object_profile['damage_type'], zone,**({'target':target} if background_is(target_data,'Стойкий') else {}),
                        )
                        final_damage = max(0, base_damage * (1 - protection / 100))
                        attack_result = CombatService._apply_attack_damage(
                            target,
                            final_damage,
                            zone,
                            damage_profile,
                            round_number=current_round,
                            allow_bleeding=False,
                            stress_trigger='anomalous_soul_projectile',
                            attacker=character,
                        )
                        attack_result.update({
                            'zone': zone,
                            'base_damage': base_damage,
                            'damage': final_damage,
                            'protection': protection,
                            'hit': True,
                            'automatic_hit': True,
                        })
                        if object_profile.get('armor_damage_multiplier', 1) > 1:
                            attack_result['armor_damage'] = (
                                CombatService._apply_anomaly_equipment_damage(
                                    target_data,
                                    base_damage,
                                    object_profile,
                                )
                            )
                    mutant_action_details['attack_result'] = attack_result
                    mutant_action_details['damage'] = attack_result.get('damage', 0)
                    if object_profile.get('burning'):
                        target_data = (
                            target.character.data
                            if target.character and isinstance(target.character.data, dict)
                            else {}
                        )
                        apply_effect_to_health(target_data.setdefault('health', {}), {
                            'type': 'burning', 'name': 'Горение',
                            'remaining': 3, 'tick': 'turn_end',
                            'source': 'anomalous_soul_projectile',
                        })
                        target.character.data = target_data
                        flag_modified(target.character, 'data')
                thrown_object.tile_x = target.pos_x
                thrown_object.tile_y = target.pos_y
                properties = dict(thrown_object.properties or {})
                properties['weight_kg'] = mutant_action_details['object_weight']
                thrown_object.properties = properties
                flag_modified(thrown_object, 'properties')
                mutant_action_details['object_event'] = 'updated'
                CombatService._sync_location_effects_from_character(target)
            elif action_key == 'mutant_anomaly_shield' and mutant_action_details:
                combat_meta['mutantAnomalyShield'] = True
                character.character.data = data
                flag_modified(character.character, 'data')
            elif action_key == 'mutant_steal_device' and mutant_action_details:
                target = mutant_action_details.pop('target')
                target_data = (
                    target.character.data
                    if target.character and isinstance(target.character.data, dict)
                    else {}
                )
                stolen = CombatService._take_inventory_item(
                    target_data,
                    mutant_action_details['item_path'],
                    1,
                )
                data.setdefault('inventory', {}).setdefault('backpack', []).append(stolen)
                target.character.data = target_data
                character.character.data = data
                flag_modified(target.character, 'data')
                flag_modified(character.character, 'data')
                mutant_action_details['stolen_item'] = {
                    'name': stolen.get('name') or mutant_action_details['item_name'],
                    'quantity': stolen.get('quantity', 1),
                }
            elif action_key == 'mutant_clone' and mutant_action_details:
                clone_data = deepcopy(data)
                clone_data.setdefault('basic', {})['psi_clone_source_id'] = character.character_id
                clone_data['basic']['psi_clone_display_name']=CombatService._display_name(character.character)
                clone_data['basic']['is_psi_clone'] = True
                clone_data['basic']['psi_clone_damage'] = mutant_action_details['clone_psy_damage']
                clone_data['health'] = {
                    'current': 1,
                    'max': 1,
                    'effects': [],
                    'zones': {
                        key: {'current': 1, 'max': 1}
                        for key in ('leftArm', 'rightArm', 'leftLeg', 'rightLeg', 'chest', 'abdomen', 'head')
                    },
                }
                mutant_data = clone_data.get('mutant') if isinstance(clone_data.get('mutant'), dict) else {}
                mutant_data['traits'] = [
                    trait for trait in (mutant_data.get('traits') or [])
                    if 'Клонирование' not in str(trait)
                ]
                variant = mutant_data.get('variant') if isinstance(mutant_data.get('variant'), dict) else None
                if variant:
                    variant['traits'] = [
                        trait for trait in (variant.get('traits') or [])
                        if 'Клонирование' not in str(trait)
                    ]
                for clone_weapon in clone_data.get('weapons') or []:
                    if not isinstance(clone_weapon, dict):
                        continue
                    clone_weapon['damage'] = 0
                    clone_weapon['armor_piercing'] = 0
                    attributes = clone_weapon.setdefault('attributes', {})
                    attributes['damage'] = 0
                    attributes['armor_piercing'] = 0
                    attributes['psi_clone_attack'] = mutant_action_details['clone_psy_damage']
                clone_number = 1 + sum(
                    1 for item in LocationCharacter.query.filter_by(location_id=location_id).all()
                    if item.character and isinstance(item.character.data, dict)
                    and item.character.data.get('basic', {}).get('psi_clone_source_id') == character.character_id
                )
                clone_character = LobbyCharacter(
                    lobby_id=location.lobby_id,
                    owner_id=location.lobby.gm_id,
                    name=f'{character.character.name} — копия {clone_number}',
                    data=clone_data,
                    visible_to=[],
                    editable_to=[],
                )
                db.session.add(clone_character)
                db.session.flush()
                clone = LocationCharacter(
                    location_id=location_id,
                    character_id=clone_character.id,
                    pos_x=mutant_action_details['spawn_tile'][0],
                    pos_y=mutant_action_details['spawn_tile'][1],
                    controlled_by=character.controlled_by,
                    team_name=character.team_name,
                    team_color=character.team_color,
                    hp_zones={
                        'head': {'current': 1, 'max': 1},
                        'chest': {'current': 1, 'max': 1},
                        'abdomen': {'current': 1, 'max': 1},
                        'left_arm': {'current': 1, 'max': 1},
                        'right_arm': {'current': 1, 'max': 1},
                        'left_leg': {'current': 1, 'max': 1},
                        'right_leg': {'current': 1, 'max': 1},
                    },
                    effects=[],
                )
                db.session.add(clone)
                db.session.flush()
                profile = CombatService._combat_profile(clone)
                clone.initiative_bonus = profile['initiative_bonus']
                clone.initiative_roll = character.initiative_roll
                clone.initiative_total = character.initiative_total
                clone.action_points_max = profile['action_points']
                clone.action_points_current = profile['action_points']
                clone.free_actions_max = profile['free_actions']
                clone.free_actions_current = profile['free_actions']
                clone.movement_points_max = profile['movement_points']
                clone.movement_points_current = profile['movement_points']
                if clone.id not in state.turn_order:
                    insert_at = state.turn_order.index(character.id) + 1
                    state.turn_order.insert(insert_at, clone.id)
                mutant_action_details['clone'] = CombatService._serialize_character(
                    clone,
                    current_turn_id=state.current_location_character_id,
                    combat_state=state,
                )
                mutant_action_details.pop('spawn_tile', None)
            if action_key == 'clear_weapon_jam' and clear_jam_details:
                weapons = data.get('weapons') or []
                cleared_weapon = weapons[clear_jam_details['weapon_index']]
                jams = CombatService._weapon_jams(cleared_weapon)
                if jams:
                    jams.pop()
                CombatService._set_weapon_jams(cleared_weapon, jams)
                character.character.data = data
                flag_modified(character.character, 'data')
            if action_key == 'load_ammunition':
                from app.services.ammo_loading import load_ammunition
                reload_details = load_ammunition(data, loading, combat=True)
                if loading.get('weapon_index') is None:
                    CombatService._set_active_weapon(character, None)
                    CombatService._clear_aim(character)
                character.character.data = data
                flag_modified(character.character, 'data')
            if action_key == 'reload_weapon' and reload_details:
                installed = install_magazine(data, weapon_index, item_path, magazine_template_id, magazine_selection)
                reload_details['installed_magazine_id'] = installed.get('id')
                reload_details['ammo'] = installed['currentAmmo']
                character.character.data = data
                flag_modified(character.character, 'data')
            if action_key == 'reload_underbarrel' and underbarrel_reload_details:
                weapons = data.get('weapons') or []
                weapon = weapons[underbarrel_reload_details['weapon_index']]
                launcher = next(
                    module for module in (weapon.get('installedModules') or [])
                    if isinstance(module, dict)
                    and (module.get('attributes') or {}).get('type') == 'grenade_launcher'
                )
                grenade = CombatService._take_inventory_item(
                    data, underbarrel_reload_details['item_path'], 1,
                )
                grenade['quantity'] = 1
                launcher['loaded'] = True
                launcher['loadedGrenade'] = grenade
                character.character.data = data
                flag_modified(character.character, 'data')
            if action_key=='prosthesis' and equipment_action_details:
                from app.services.prostheses import apply
                equipment_action_details=apply(data,equipment_action_details)
                character.character.data=data;flag_modified(character.character,'data')
            if action_key == 'change_equipment' and equipment_action_details:
                equipment_action_details = CombatService.apply_equipment_action(
                    data,
                    equipment_action_details,
                )
                character.character.data = data
                flag_modified(character.character, 'data')
            if action_key == 'change_equipment_module' and equipment_module_details:
                from app.services.equipment_module import apply_module_action

                equipment_module_details = apply_module_action(
                    data,
                    equipment_module_details,
                )
                character.character.data = data
                flag_modified(character.character, 'data')
            if action_key == 'place_gunpoint' and gunpoint_details:
                combat_meta.pop('gunpoint', None)
                if gunpoint_details['success']:
                    combat_meta.pop('gunpointFailedRound', None)
                    combat_meta['gunpoint'] = {
                        key: deepcopy(gunpoint_details[key])
                        for key in (
                            'target_location_character_id',
                            'target_character_id',
                            'target_name',
                            'target_zone',
                            'weapon_index',
                            'weapon_name',
                            'round',
                        )
                    }
                else:
                    combat_meta['gunpointFailedRound'] = current_round
                character.character.data = data
                flag_modified(character.character, 'data')
            if action_key == 'escape_anomaly' and anomaly_details:
                exposure = CombatService._apply_anomaly_exposure(
                    character,
                    active_anomaly,
                    anomaly_details['fraction'],
                    apply_category_effect=anomaly_details['fraction'] >= 0.5,
                    round_number=current_round,
                    exiting=anomaly_details['exits'],
                    escape_margin=max(0, anomaly_details['check']['margin']),
                    condition=anomaly_details.get('condition'),
                )
                anomaly_details['exposure'] = exposure
                data, _, combat_meta, _ = CombatService._active_anomaly(character)
                if anomaly_details['exits']:
                    character.pos_x = anomaly_details['target_x']
                    character.pos_y = anomaly_details['target_y']
                    CombatService._leave_anomaly(character)
                    CombatService._clear_aim(character)
                else:
                    active_anomaly['rounds'] = max(
                        0, CombatService._coerce_int(active_anomaly.get('rounds'), 0),
                    ) + 1
                    combat_meta['activeAnomaly'] = active_anomaly
                character.character.data = data
                flag_modified(character.character, 'data')
            if action_key == 'narrative_action' and narrative_action_details:
                if narrative_action_details['roll_required']:
                    help_bonus = CombatService._consume_help_advantage(
                        character, narrative_action_details['skill_path']
                    )
                    narrative_action_details['check'] = CombatService._narrative_skill_check(
                        data,
                        narrative_action_details['skill_path'],
                        advantage=bool(help_bonus),
                        darkness=CombatService._is_dark_at_character(location, character),
                        roll_bonus=CombatService._coerce_int((help_bonus or {}).get('roll_bonus'),0),
                        weather=weather_at(location,character),
                    )
                    if help_bonus:
                        narrative_action_details['help'] = help_bonus
                    check = narrative_action_details['check']
                    check['difficulty'] = narrative_action_details['difficulty']
                    check['success'] = not check.get('automatic_failure') and check['total'] >= check['difficulty']
                    CombatService._apply_stress_check_consequences(
                        data, narrative_action_details['skill_path'], check['success'],
                    )
                    if (
                        not check['success']
                        and not CombatService._is_mutant_character(data)
                    ):
                        usage_profile = CombatService._must_do_usage_profile(data, state)
                        combat_meta['mustDoRetry'] = {
                            'name': narrative_action_details['name'],
                            'skill_path': narrative_action_details['skill_path'],
                            'difficulty': narrative_action_details['difficulty'],
                            'created_round': current_round,
                            'will_bonus': usage_profile['will_bonus'],
                            'use_limit': usage_profile['limit'],
                            'uses_used': usage_profile['used'],
                            'uses_remaining': usage_profile['remaining'],
                        }
                    character.character.data = data
                    flag_modified(character.character, 'data')
            if action_key == 'must_do_it' and must_do_details:
                usage_profile = CombatService._must_do_usage_profile(data, state)
                usage_profile['usage']['used'] = usage_profile['used'] + 1
                CombatService.apply_stress_trigger(
                    character, 1, trigger='must_do_it', check_manifestation=False,
                )
                must_do_details['uses_used'] = usage_profile['used'] + 1
                must_do_details['uses_remaining'] = max(
                    0, usage_profile['limit'] - usage_profile['used'] - 1,
                )
                combat_meta.pop('mustDoRetry', None)
                if must_do_details.get('kind') != 'attack':
                    check = CombatService._narrative_skill_check(
                        data, must_do_details['skill_path'],
                        darkness=CombatService._is_dark_at_character(location, character),
                        weather=weather_at(location,character),
                    )
                    check['difficulty'] = CombatService._coerce_int(
                        must_do_details['difficulty'], 1,
                    )
                    check['success'] = not check.get('automatic_failure') and check['total'] >= check['difficulty']
                    CombatService._apply_stress_check_consequences(
                        data, must_do_details['skill_path'], check['success'],
                    )
                    must_do_details['check'] = check
                    medical_retry = must_do_details.get('medical_retry')
                    server_medical_retry = (
                        must_do_details.get('kind') == 'medical'
                        and isinstance(medical_retry, dict)
                        and medical_retry.get('server_authoritative') is True
                    )
                    if (
                        check['success']
                        and server_medical_retry
                    ):
                        from app.services.medical_procedure import MedicalProcedureService
                        must_do_details['medical_result'] = MedicalProcedureService.apply_retry_success(
                            character, medical_retry,
                        )
                        must_do_details.pop('medical_retry', None)
                        data = character.character.data
                        health = data.setdefault('health', {})
                        combat_meta = health.setdefault('combatMeta', {})
                    if server_medical_retry:
                        must_do_details.pop('medical_retry', None)
                    if not check['success']:
                        must_do_details['stress_manifestation'] = CombatService.apply_stress_trigger(
                            character, 0, trigger='must_do_it_failure', force_manifest=True,
                        )
                character.character.data = data
                flag_modified(character.character, 'data')
            if action_key == 'console_ally' and consolation_details:
                target = consolation_details.pop('target')
                target_data = consolation_details.pop('target_data')
                target_health = target_data.setdefault('health', {})
                target_meta = target_health.setdefault('combatMeta', {})
                target_stress = max(0, CombatService._coerce_int(target_health.get('stress'), 0))
                charisma_bonus = CombatService._skill_modifier(
                    data, 'skills.social.charisma', include_pain=False,
                )
                difficulty = max(1, 10 + target_stress - charisma_bonus)
                check = CombatService._narrative_skill_check(
                    target_data, 'skills.physical.will',
                )
                success = check['roll'] == 20 or (check['roll'] != 1 and check['total'] >= difficulty)
                check.update({'difficulty': difficulty, 'success': success})
                target_meta['lastConsoledAt'] = consolation_details['current_minute']
                if success:
                    stress_result = CombatService.apply_stress_trigger(
                        target, -1, trigger='consolation', check_manifestation=False,
                    )
                else:
                    stress_result = CombatService.apply_stress_trigger(
                        target, 0, trigger='failed_consolation', force_manifest=True,
                    )
                consolation_details.update({
                    'target_character_id': target.character_id,
                    'target_name': target.character.name,
                    'check': check,
                    'stress': stress_result,
                })
                target.character.data = target_data
                flag_modified(target.character, 'data')
            if action_key == 'attack' and attack_details:
                help_skill = (
                    'skills.physical.melee'
                    if attack_details.get('melee')
                    else 'skills.physical.shooting'
                )
                help_bonus = CombatService._consume_help_advantage(character, help_skill)
                if help_bonus:
                    for details in prepared_attacks or [attack_details]:
                        details['help'] = help_bonus
                        for key in ('hit_difficulty','hit_difficulty_without_weapon_jam'):
                            if key in details:details[key]-=CombatService._coerce_int(help_bonus.get('roll_bonus'),0)
                        advantage_key = 'melee_advantage' if details.get('melee') else 'shooting_advantage'
                        details[advantage_key] = True
            if action_key == 'attack' and attack_details and fire_mode == 'rapid':
                character.rapid_fire_round = state.round_number
            if action_key == 'aim' and aim_details:
                character.aimed_target_character_id = aim_details['target_character_id']
                character.aimed_weapon_index = aim_details['weapon_index']
                character.aim_accuracy_bonus = aim_details['accuracy_bonus']
            elif action_key == 'attack' and attack_details:
                if attack_details.get('melee') and attack_details.get('swing_bonus'):
                    character.melee_swing_round = None
                selected_target = attack_details.get('target_character_id')
                if (
                    selected_target != character.aimed_target_character_id
                    or weapon_index != character.aimed_weapon_index
                ):
                    CombatService._clear_aim(character)
            else:
                CombatService._clear_aim(character)

        if action_key in {'attack','explosive_attack','flamethrower_fire'}:
            from app.services.character_rules import harm_check
            ids=set((attack_details or {}).get('target_character_ids') or [])
            if target_character_id:ids.add(target_character_id)
            if flame_details and background_is(data,'Истинный пацифист'):
                ids.update(person.character_id for person in LocationCharacter.query.filter_by(location_id=location_id).all()
                    if person.id!=character.id and person.floor_level==character.floor_level
                    and math.hypot(person.pos_x-flame_details['x'],person.pos_y-flame_details['y'])<=13)
            if explosive_details and background_is(data,'Истинный пацифист'):
                from app.services.explosion_geometry import distance as blast_distance
                blast_objects=LocationObject.query.filter_by(location_id=location_id).all()
                center=explosive_details['target'];radius=explosive_details['profile'].get('radius',0)
                ids.update(person.character_id for person in LocationCharacter.query.filter_by(location_id=location_id).all()
                    if person.character and blast_distance(location,blast_objects,center['x'],center['y'],explosive_details['floor_level'],person)<=radius)
            checks=[]
            for identity in ids:
                intended=LocationCharacter.query.filter_by(location_id=location_id,character_id=identity).first()
                if intended:
                    check=harm_check(character,intended,not (attack_details or {}).get('melee') and (not explosive_details or explosive_details['source']!='hand'))
                    if check:checks.append(check)
            if any(not check['success'] for check in checks):
                if resumed_paid_action:combat_meta.pop('completedPendingActionId',None)
                character.character.data=data;flag_modified(character.character,'data');db.session.commit()
                return {'action':action_key,'attack':None,'explosive':None,'character':CombatService._serialize_character(character),
                    'state':CombatService._serialize_state(location,state),'narrative_action':{'name':'Воля перед нанесением вреда','roll_required':True,'checks':checks,'blocked_by_will':True}}

        if action_key in {'flamethrower_fire','flamethrower_service'}:
            from app.services.flamethrower import fire,service
            narrative_action_details=fire(character,state,flame_details) if action_key=='flamethrower_fire' else service(character,flame_details)
        if action_key=='place_cover':
            from app.services.crafted_covers import apply as apply_cover
            obj,created,removed=apply_cover(character,data,cover_placement,attribute_choice)
            character.character.data=data;flag_modified(character.character,'data')
            narrative_action_details={'name':action['label'],'action_points':special_action_cost,'roll_required':False,
                'ground_object_id':obj.id,'ground_object_event':'deleted' if removed else 'created' if created else 'updated'}
        if action_key=='trap':
            from app.services.traps import execute as execute_trap
            narrative_action_details=execute_trap(character,data,attribute_choice,user_id,state)
            character.character.data=data;flag_modified(character.character,'data')
        if action_key=='prepare_water':
            from app.services.burning import water_item
            water,litres=water_item(data,item_path)
            water.setdefault('id',uuid.uuid4().hex)
            combat_meta['heldWaterItem']={'id':water['id'],'path':list(item_path),'litres':litres,'name':water.get('name','Вода')}
            character.character.data=data;flag_modified(character.character,'data')
            narrative_action_details={'name':f'Вода взята в руку: {litres:g} л','action_points':special_action_cost,'roll_required':False}
        elif action_key=='pour_water':
            from app.services.burning import pour
            litres=pour(character,water_target)
            CombatService._sync_location_effects_from_character(water_target)
            narrative_action_details={'name':f'Обливание водой: {litres:g} л, горение −{2*litres:g} раунда','action_points':2,'roll_required':False}
        elif action_key=='extinguish_fire':
            from app.services.burning import extinguish_difficulty
            difficulty=extinguish_difficulty(data);roll=random.randint(1,20)
            from app.services.brigadier_order import boost_roll
            _rolls,roll,order_bonus=boost_roll(data,[roll])
            check={'roll':roll,'total':roll+order_bonus,'difficulty':difficulty,'success':roll+order_bonus>=difficulty}
            if check['success']:health['effects']=[e for e in normalize_effect_list(health.get('effects') or []) if e['type']!='burning']
            narrative_action_details={'name':'Тушить себя','action_points':3,'roll_required':True,'difficulty':difficulty,'check':check}
            character.character.data=data;flag_modified(character.character,'data')
            CombatService._sync_location_effects_from_character(character)
        if action_key=='recover_concussion':
            check=CombatService._narrative_skill_check(data,'skills.physical.will',
                advantage=bool(CombatService._consume_help_advantage(character,'skills.physical.will')))
            check.update(difficulty=12,success=check['total']>=12)
            if check['success']:
                data['health']['effects']=[e for e in normalize_effect_list(data['health'].get('effects') or []) if e['type']!='concussion']
            CombatService._apply_stress_check_consequences(data,'skills.physical.will',check['success'])
            narrative_action_details={'name':'Преодолеть контузию','action_points':2,'roll_required':True,
                'skill_path':'skills.physical.will','skill_label':'Воля','difficulty':12,'check':check}
            character.character.data=data
            flag_modified(character.character,'data')
            CombatService._sync_location_effects_from_character(character)
        if explosive_details:
            combat_meta.pop('grenadirMustExplode',None)
            explosive_details['profile']['source_character_id']=character.character_id
            source = explosive_details['source']
            # Броски выполняются только после полной оплаты, в том числе многоходовой.
            if source=='hand' and explosive_details['fuse_mode']=='delay':
                check=CombatService._narrative_skill_check(data,'skills.other.tactics',
                    advantage=CombatService._consume_help_advantage(character,'skills.other.tactics'))
                check.update(difficulty=13,success=check['total']>=13)
                CombatService._apply_stress_check_consequences(data,'skills.other.tactics',check['success'])
                original=explosive_details['profile'].get('fuse','instant')
                if check['success']:
                    explosive_details['profile']['fuse']={'round':'turn_end','turn_end':'instant'}.get(original,original)
                explosive_details['delay_check']=check
                explosive_details['original_fuse']=original
                explosive_details['delay_attempted']=True
            disadvantage=explosive_details['disadvantage']
            from app.services.brigadier_order import boost_roll
            rolls,roll,order_bonus=boost_roll(data,[],disadvantage=disadvantage)
            difficulty=explosive_details['difficulty']
            success=(roll+order_bonus>=difficulty if source=='hand' else roll==20 or (roll!=1 and roll+order_bonus>=difficulty))
            failure=0 if success else max(1 if source!='hand' else 0,difficulty-roll-order_bonus)
            explosive_details.update(rolls=rolls,roll=roll,success=success,failure=failure)
            if not success:
                x,y=CombatService._scatter_point(explosive_details['impact']['x'],explosive_details['impact']['y'],
                    failure,location.grid_width,location.grid_height,location=location,floor_level=explosive_details['floor_level'])
                explosive_details['impact']={'x':x,'y':y}
            if source=='hand' and roll==1 and background_is(data,'Глаз-Алмаз'):
                dx=explosive_details['target']['x']-character.pos_x;dy=explosive_details['target']['y']-character.pos_y
                length=math.hypot(dx,dy)
                if length<=1e-9:dx,dy,length=character.facing_x or 1,character.facing_y or 0,1
                explosive_details['impact']={'x':character.pos_x+dx/length,'y':character.pos_y+dy/length}
                explosive_details['floor_level']=character.floor_level or 0;explosive_details['airburst']=False
                explosive_details['diamond_fumble']=True;explosive_details['success']=False
                profile=explosive_details['profile'];profile['fuse']='instant'
                for key in ('fragment','blast_base','blast_falloff'):
                    if key in profile:profile[key]*=.5
                if profile.get('effect') in {'gas','fire','smoke','smoke_growing'} or profile.get('area_effect'):
                    profile['half_duration']=True
                    for key in ('burn_rounds','direct_burn_rounds','incendiary_rounds'):
                        if profile.get(key):profile[key]=max(1,math.floor(profile[key]/2))
            if not explosive_details.get('airburst'):
                from app.services.explosion_geometry import landing_level
                landed=landing_level(location,LocationObject.query.filter_by(location_id=location_id).all(),
                    explosive_details['impact']['x'],explosive_details['impact']['y'],explosive_details['floor_level'])
                if landed is not None:explosive_details['floor_level']=landed
            if source == 'weapon':
                explosive_details['shots_fired_this_round'] = CombatService._record_weapon_shots(
                    combat_meta,
                    current_round,
                    explosive_details['weapon_index'],
                    1,
                )
            if source == 'hand':
                CombatService._take_inventory_item(data, item_path, 1)
            elif source == 'underbarrel':
                explosive_module['loaded'] = False
                explosive_module['loadedGrenade'] = None
            else:
                CombatService._consume_weapon_ammo(explosive_weapon, 1)
                explosive_details['weapon_wear'] = CombatService._weapon_use_wear(
                    explosive_weapon,
                    fire_mode='unaimed' if explosive_details['fire_mode'] == 'unaimed' else 'aimed',
                    shot_count=1,
                    ammo_profile=explosive_details['profile'],
                )
                explosive_details['weapon_jam'] = CombatService._roll_character_weapon_jam(
                    data,explosive_weapon,
                    explosive_details.get('roll'),
                )
            fuse = str(explosive_details['profile'].get('fuse') or 'instant')
            if fuse == 'instant':
                explosive_details['explosion'] = CombatService.resolve_explosion(
                    location_id,
                    explosive_details['impact']['x'],
                    explosive_details['impact']['y'],
                    explosive_details['profile'],
                    round_number=current_round,
                    **({'floor_level':explosive_details['floor_level']} if explosive_details['floor_level'] else {}),
                )
                area = explosive_details['explosion'].get('area')
                if isinstance(area, dict):
                    areas = list(state.area_effects or [])
                    areas.append(area)
                    state.area_effects = areas
                explosive_details['detonated'] = not explosive_details['explosion'].get('awaiting_gm',False)
            else:
                event = {
                    'id': uuid.uuid4().hex,
                    'item_name': explosive_details['item_name'],
                    'x': explosive_details['impact']['x'],
                    'y': explosive_details['impact']['y'],
                    **({'floor_level':explosive_details['floor_level']} if explosive_details['floor_level'] else {}),
                    'profile': explosive_details['profile'],
                    'actor_id': character.id,
                    'trigger': 'short_fuse' if fuse == 'turn_end' else 'actor_turn_start',
                    'round': current_round + (1 if fuse == 'round' else 0),
                }
                if fuse=='turn_end':
                    from app.services.grenade_reaction import prepare_event
                    prepare_event(state,event)
                pending = list(state.pending_explosives or [])
                pending.append(event)
                state.pending_explosives = pending
                explosive_details['pending'] = {
                    'id': event['id'],
                    'trigger': event['trigger'],
                    'round': event['round'],
                }
                explosive_details['detonated'] = False
            explosive_details['action_points'] = special_action_cost
            if source in {'weapon','underbarrel'}:
                from app.services.noise import apply as apply_noise
                explosive_details['noise_targets']=apply_noise(location,character.pos_x,character.pos_y,character.floor_level or 0,
                    CombatService._template_attributes(explosive_weapon).get('noise',explosive_weapon.get('noise',0)))
            latest=character.character.data
            if latest is not data and isinstance(latest,dict) and isinstance(latest.get('health'),dict):data['health']=latest['health']
            character.character.data = data
            flag_modified(character.character, 'data')
            CombatService._clear_aim(character)

        def resolve_prepared_attack(attack_details):
            resolved_hits=[]
            if action_key == 'attack':
                # A retry belongs only to the most recent failed check. A new
                # attack replaces the previous opportunity even when it hits.
                combat_meta.pop('mustDoRetry', None)
            if not attack_details.get('melee'):
                attack_details['requested_shot_count'] = max(
                    1, CombatService._coerce_int(
                        attack_details.get('shot_count'), 1,
                    ),
                )
            if attack_details.get('melee'):
                if attack_details.get('circular_attack'):
                    circular_targets = [
                        LocationCharacter.query.filter_by(
                            location_id=location_id,
                            character_id=target_id,
                        ).first()
                        for target_id in (attack_details.get('target_character_ids') or [])
                    ]
                    for target in (item for item in circular_targets if item):
                        target_details = dict(attack_details)
                        target_details['target_character_id'] = target.character_id
                        circular_profile = CombatService._melee_target_profile(
                            character,
                            target,
                            target_details['melee_bonus'],
                            target_details['weapon_accuracy'],
                            False,
                            None,
                            True,
                        )
                        target_details.update({
                            'from_behind': circular_profile['from_behind'],
                            'target_prone': circular_profile['target_prone'],
                            'target_unconscious': circular_profile['target_unconscious'],
                            'automatic_hit': circular_profile['automatic_hit'],
                            'block_penalty': circular_profile['block_penalty'],
                            'aimed_penalty': circular_profile['aimed_penalty'],
                            'melee_advantage': circular_profile['advantage'],
                            'hit_difficulty': circular_profile['difficulty'],
                        })
                        resolved_hits.append(CombatService._resolve_attack(
                            target,
                            character,
                            target_details,
                            melee=True,
                            attack_type=attack_type,
                        ))
                else:
                    target = LocationCharacter.query.filter_by(
                        location_id=location_id,
                        character_id=attack_details['target_character_id'],
                    ).first()
                    resolved_hits.append(CombatService._resolve_attack(
                        target, character, attack_details,
                        melee=True,
                        attack_type=attack_type,
                        aimed_zone=target_zone,
                    ))
                if (
                    action_key == 'grapple_desperate_attack'
                    and resolved_hits[-1].get('hit')
                    and resolved_hits[-1].get('damage', 0) >= 10
                    and target.character
                ):
                    target_data = target.character.data if isinstance(target.character.data, dict) else {}
                    health = target_data.setdefault('health', {})
                    apply_effect_to_health(health, {'type':'pain','value':3,'source':'grapple'},character_data=target_data)
                    target.character.data = target_data
                    flag_modified(target.character, 'data')
            elif fire_mode == 'suppression':
                suppressed_targets = LocationCharacter.query.filter_by(
                    location_id=location_id,
                    cover_object_id=attack_details.get('target_object_id'),
                ).all()
                attack_details['suppressed_characters'] = []
                for suppressed in suppressed_targets:
                    if suppressed.id == character.id or not suppressed.character:
                        continue
                    stress_result = CombatService.apply_stress_trigger(
                        suppressed, 2 if background_is(data,'Пост принял') else 1, trigger='suppression',
                    )
                    attack_details['suppressed_characters'].append({
                        'character_id': suppressed.character_id,
                        'name': suppressed.character.name,
                        'stress': stress_result,
                    })
            else:
                targets = []
                if attack_details.get('direct_cover_attack'):
                    cover = db.session.get(
                        LocationObject, attack_details.get('target_object_id')
                    )
                    if cover:
                        cover_shot_states = []
                        cover_jams = []
                        cover_weapon = (data.get('weapons') or [])[attack_details['weapon_index']]
                        requested_shots = attack_details['requested_shot_count']
                        for shot_index in range(requested_shots):
                            jam_effects = CombatService._weapon_jam_effects(cover_weapon)
                            if jam_effects['blocks_fire']:
                                break
                            shot_details = dict(attack_details)
                            shot_details['continuation_hit_difficulty'] = max(
                                1,
                                CombatService._coerce_float(
                                    attack_details.get(
                                        'continuation_hit_difficulty_without_weapon_jam'
                                    ),
                                    attack_details.get('continuation_hit_difficulty', 1),
                                ) + jam_effects['accuracy_penalty'] + (
                                    math.floor(shot_index * 0.5)
                                    if attack_details.get('machine_gun_burst')
                                    else 0
                                ),
                            )
                            result = CombatService._resolve_cover_attack(
                                location_id, cover, character, shot_details
                            )
                            jam = CombatService._roll_character_weapon_jam(
                                data,cover_weapon, result.get('roll'),
                            )
                            state_item = {
                                'shot_number': shot_index + 1,
                                'accuracy_penalty': jam_effects['accuracy_penalty'],
                                'shooting_disadvantage': jam_effects['shooting_disadvantage'],
                                'jams_before_shot': deepcopy(jam_effects['jams']),
                                'jam_after_shot': deepcopy(jam),
                            }
                            cover_shot_states.append(state_item)
                            result['shot_number'] = shot_index + 1
                            result['weapon_jam_after_shot'] = deepcopy(jam)
                            resolved_hits.append(result)
                            if isinstance(jam, dict) and jam.get('triggered'):
                                cover_jams.append({
                                    'shot_number': shot_index + 1,
                                    **deepcopy(jam),
                                })
                                if jam.get('blocks_fire'):
                                    break
                        attack_details['shot_count'] = len(resolved_hits)
                        attack_details['shot_jam_states'] = cover_shot_states
                        attack_details['weapon_jams'] = cover_jams
                        attack_details['weapon_jam'] = cover_jams[-1] if cover_jams else None
                        attack_details['stopped_by_jam'] = len(resolved_hits) < requested_shots
                elif fire_mode == 'area':
                    targets = [
                        LocationCharacter.query.filter_by(
                            location_id=location_id, character_id=target_id
                        ).first()
                        for target_id in (target_character_ids or [])
                    ]
                    targets = [item for item in targets if item]
                else:
                    target = LocationCharacter.query.filter_by(
                        location_id=location_id,
                        character_id=target_character_id,
                    ).first()
                    if target:
                        targets = [target]
                if attack_details.get('direct_cover_attack'):
                    pass
                elif fire_mode == 'area':
                    from app.services.equipment_modifications import rule as modification_rule
                    area_weapon=data['weapons'][attack_details['weapon_index']]
                    area_ammo,_=CombatService._ranged_damage_profile(area_weapon)
                    independent=modification_rule(area_weapon,'buckshot_all_targets') and CombatService._is_buckshot_profile(area_ammo)
                    if independent:
                        for area_target in targets:
                            resolved_hits.append(CombatService._resolve_attack(area_target,character,attack_details,aimed_zone=target_zone))
                        attack_details['area_hit_count']=sum(bool(hit.get('hit')) for hit in resolved_hits)
                        attack_details['independent_area_checks']=True
                    primary = targets[0] if targets and not independent else None
                    if primary:
                        first = CombatService._resolve_attack(
                            primary, character, {**attack_details,'post_bonus_available':background_is(data,'Пост принял')}, aimed_zone=target_zone
                        )
                        resolved_hits.append(first)
                        if first.get('hit'):
                            hit_count = CombatService._area_fire_hit_count(
                                first.get('roll'),
                                first.get('difficulty'),
                                attack_details.get('shot_count', 1),
                                first.get('stress_check_modifier'),
                            )
                            if background_is(data,'Пост принял'):
                                ordinary=0 if first.get('post_bonus_hit') else hit_count
                                hit_count=min(attack_details.get('shot_count',1),ordinary+attack_details.get('volley_count',1))
                                attack_details['post_bonus_hits']=hit_count-ordinary
                            extra_hits = hit_count - 1
                            for index in range(extra_hits):
                                target = targets[(index + 1) % len(targets)]
                                extra_hit = CombatService._resolve_attack(
                                    target, character, attack_details,
                                    aimed_zone=target_zone, forced_roll=20
                                )
                                extra_hit['roll'] = first.get('roll')
                                extra_hit['rolls'] = first.get('rolls')
                                extra_hit['shared_area_roll'] = True
                                resolved_hits.append(extra_hit)
                            attack_details['area_hit_count'] = hit_count
                        else:
                            attack_details['area_hit_count'] = 0
                    if not attack_details.get('must_do_retry'):
                        attack_details['area_stressed_characters'] = []
                        for opponent in LocationCharacter.query.filter_by(
                            location_id=location_id,
                        ).all():
                            if (
                                not opponent.character
                                or not CombatService._are_opponents(character, opponent)
                                or abs(opponent.pos_x - attack_details['area_center_x']) > 2
                                or abs(opponent.pos_y - attack_details['area_center_y']) > 2
                                or CombatService._character_condition(
                                    opponent.character.data
                                    if isinstance(opponent.character.data, dict)
                                    else {}
                                )['state'] == 'dead'
                            ):
                                continue
                            stress_result = CombatService.apply_stress_trigger(
                                opponent, 1, trigger='area_fire',
                            )
                            attack_details['area_stressed_characters'].append({
                                'character_id': opponent.character_id,
                                'name': opponent.character.name,
                                'stress': stress_result,
                            })
                elif attack_details.get('must_do_retry') and single_fire:
                    target = targets[0] if targets else None
                    if target:
                        first = CombatService._resolve_attack(
                            target, character, attack_details, aimed_zone=target_zone,
                        )
                        resolved_hits.append(first)
                        if first.get('hit'):
                            for _ in range(1, attack_details.get('shot_count', 1)):
                                extra_hit = CombatService._resolve_attack(
                                    target, character, attack_details,
                                    aimed_zone=target_zone, forced_roll=20,
                                )
                                extra_hit['roll'] = first.get('roll')
                                extra_hit['rolls'] = first.get('rolls')
                                extra_hit['shared_retry_roll'] = True
                                resolved_hits.append(extra_hit)
                else:
                    resolved_hits.extend(CombatService._resolve_shot_sequence(
                        targets,
                        character,
                        attack_details,
                        aimed_zone=target_zone,
                        share_hit_roll=(
                            single_fire and attack_details['shot_count'] > 1
                        ),
                    ))
            attack_details['results'] = resolved_hits
            attack_details['hits'] = sum(1 for item in resolved_hits if item.get('hit'))
            if fire_mode and not attack_details.get('must_do_retry'):
                fired_weapon = (data.get('weapons') or [])[attack_details['weapon_index']]
                weapon_noise,noise_targets=CombatService._apply_fired_noise(location,character,fired_weapon,resolved_hits,attack_details.get('shot_count',0))
                attack_details['noise'] = weapon_noise
                attack_details['noise_targets']=noise_targets
                attack_details['frightened_mutants'] = (
                    CombatService._resolve_mutant_fear_from_noise(
                        location, character, weapon_noise, current_round,
                    )
                )
            evaporated_clones = CombatService._evaporate_dead_psi_clones(
                location_id, state,
            )
            if evaporated_clones:
                attack_details['evaporated_clones'] = evaporated_clones
            if action_key == 'attack':
                CombatService._mark_mutant_attack_used(character, current_round)
            attack_details['damage_total'] = sum(
                item.get('combined_damage', item.get('damage', 0))
                for item in resolved_hits
            )
            if (
                action_key == 'attack'
                and not CombatService._is_mutant_character(data)
                and resolved_hits
                and not any(
                item.get('hit') for item in resolved_hits
                )
            ):
                retryable_attack = bool(
                    (
                        attack_details.get('melee')
                        and not attack_details.get('circular_attack')
                    )
                    or (
                        attack_details.get('fire_mode')
                        in {'unaimed', 'rapid', 'aimed', 'burst', 'area'}
                        and not attack_details.get('direct_cover_attack')
                    )
                )
                if retryable_attack:
                    retry_attack_details = deepcopy(attack_details)
                    if attack_details.get('fire_mode') != 'area':
                        retry_attack_details['target_character_id'] = (
                            resolved_hits[0].get('target_character_id')
                        )
                        retry_attack_details['target_character_ids'] = None
                    retry_attack_details['_must_do_replaced_jams'] = [
                        {
                            'id': jam.get('id'),
                            'durability_loss_applied': max(
                                0,
                                CombatService._coerce_int(
                                    jam.get('durability_loss_applied'), 0,
                                ),
                            ),
                        }
                        for jam in (attack_details.get('weapon_jams') or [])
                        if isinstance(jam, dict) and jam.get('id')
                    ]
                    for key in (
                        'results', 'weapon_wear', 'weapon_jams', 'weapon_jam',
                        'shot_jam_states', 'area_stressed_characters',
                    ):
                        retry_attack_details.pop(key, None)
                    usage_profile = CombatService._must_do_usage_profile(data, state)
                    target_names = ', '.join(dict.fromkeys(
                        str(item.get('target_name') or '')
                        for item in resolved_hits if item.get('target_name')
                    ))
                    combat_meta['mustDoRetry'] = {
                        'kind': 'attack',
                        'name': (
                            f"Повторить атаку по {target_names}"
                            if target_names else 'Повторить атаку'
                        ),
                        'difficulty': resolved_hits[0].get('difficulty'),
                        'created_round': current_round,
                        'will_bonus': usage_profile['will_bonus'],
                        'use_limit': usage_profile['limit'],
                        'uses_used': usage_profile['used'],
                        'uses_remaining': usage_profile['remaining'],
                        'attack_details': retry_attack_details,
                    }
            if action_key == 'attack':
                character.character.data = data
                flag_modified(character.character, 'data')
            if (
                action_key == 'must_do_it'
                and must_do_details
                and must_do_details.get('kind') == 'attack'
            ):
                first_result = resolved_hits[0] if resolved_hits else {}
                if not attack_details.get('melee'):
                    weapon_index = CombatService._coerce_int(
                        attack_details.get('weapon_index'), -1,
                    )
                    weapons = data.get('weapons') if isinstance(data.get('weapons'), list) else []
                    retry_weapon = weapons[weapon_index] if 0 <= weapon_index < len(weapons) else None
                    replaced_jams = attack_details.pop('_must_do_replaced_jams', [])
                    if isinstance(retry_weapon, dict) and isinstance(replaced_jams, list):
                        replacement_jam = CombatService._replace_must_do_weapon_jams(
                            retry_weapon, replaced_jams, first_result.get('roll'),character_data=data,
                        )
                        attack_details['weapon_jam'] = replacement_jam
                        attack_details['weapon_jams'] = (
                            [replacement_jam]
                            if isinstance(replacement_jam, dict)
                            and replacement_jam.get('triggered')
                            else []
                        )
                        if resolved_hits:
                            resolved_hits[0]['weapon_jam_after_shot'] = deepcopy(
                                replacement_jam,
                            )
                retry_success = any(item.get('hit') for item in resolved_hits)
                must_do_details['check'] = {
                    'roll': first_result.get('roll'),
                    'rolls': first_result.get('rolls'),
                    'total': first_result.get('total'),
                    'difficulty': first_result.get(
                        'difficulty', must_do_details.get('difficulty'),
                    ),
                    'success': retry_success,
                }
                if not retry_success:
                    must_do_details['stress_manifestation'] = CombatService.apply_stress_trigger(
                        character, 0, trigger='must_do_it_failure', force_manifest=True,
                    )
            if attack_details.get('circular_attack'):
                health = data.setdefault('health', {})
                health.setdefault('combatMeta', {})['circularAttackRound'] = current_round
                character.character.data = data
                flag_modified(character.character, 'data')
            elif not attack_details.get('melee'):
                health = data.setdefault('health', {})
                health.setdefault('combatMeta', {})['firedRound'] = current_round
                character.character.data = data
                flag_modified(character.character, 'data')

            weapon_index_for_wear = CombatService._coerce_int(
                attack_details.get('weapon_index'), -1
            )
            weapons = data.get('weapons') if isinstance(data.get('weapons'), list) else []
            if (
                0 <= weapon_index_for_wear < len(weapons)
                and not attack_details.get('must_do_retry')
            ):
                used_weapon = weapons[weapon_index_for_wear]
                if attack_details.get('melee'):
                    if str(attack_details.get('attack_type') or '').strip().lower() == 'firearm_butt':
                        attack_details['weapon_wear'] = CombatService._weapon_use_wear(
                            used_weapon, butt=True
                        )
                else:
                    attack_details['shots_fired_this_round'] = CombatService._record_weapon_shots(
                        combat_meta,
                        current_round,
                        weapon_index_for_wear,
                        attack_details.get('shot_count', 1),
                    )
                    ammo_profile, _ = CombatService._ranged_damage_profile(used_weapon)
                    CombatService._consume_weapon_ammo(
                        used_weapon, attack_details.get('shot_count', 1)
                    )
                    attack_details['weapon_wear'] = CombatService._weapon_use_wear(
                        used_weapon,
                        fire_mode=attack_details.get('fire_mode'),
                        shot_count=attack_details.get('shot_count', 1),
                        volley_count=attack_details.get('volley_count', 1),
                        ammo_profile=ammo_profile,
                    )
                    extra_loss = sum(
                        sum(
                            max(0, CombatService._coerce_int(
                                jam.get('extra_wear_per_shot'), 0,
                            ))
                            for jam in (state_item.get('jams_before_shot') or [])
                            if isinstance(jam, dict)
                        )
                        for state_item in (attack_details.get('shot_jam_states') or [])
                        if isinstance(state_item, dict)
                    )
                    if extra_loss:
                        attack_details['spring_wear'] = CombatService._apply_weapon_wear(
                            used_weapon, extra_loss
                        )
                    if CombatService._manual_cycle_type(used_weapon):
                        used_weapon['requiresManualCycle'] = True
                    attack_details['ammo_remaining'] = CombatService._coerce_int(
                        used_weapon.get('ammo'), 0
                    )
                character.character.data = data
                flag_modified(character.character, 'data')

            return attack_details

        if attack_details:
            if prepared_attacks:
                resolved=[resolve_prepared_attack(details) for details in prepared_attacks]
                results=[{**hit,'weapon_index':details['weapon_index']} for details in resolved for hit in details.get('results') or []]
                attack_details={**resolved[0],'paired_fire':True,'paired_attacks':resolved,'results':results,
                    'action_points':special_action_cost,'hits':sum(bool(hit.get('hit')) for hit in results),
                    'shot_count':sum(details.get('shot_count',0) for details in resolved),
                    'damage_total':sum(details.get('damage_total',0) for details in resolved)}
            else:attack_details=resolve_prepared_attack(attack_details)

        if character.cover_object_id and character.cover_object_id!=(combat_meta.get('sniperOldCover') or {}).get('id'):
            combat_meta.pop('sniperOldCover',None)
        evaporated_action_clones = CombatService._evaporate_dead_psi_clones(
            location_id, state,
        )
        if evaporated_action_clones:
            if isinstance(explosive_details, dict):
                explosive_details['evaporated_clones'] = evaporated_action_clones
            elif isinstance(mutant_action_details, dict):
                mutant_action_details['evaporated_clones'] = evaporated_action_clones
        CombatService._release_invalid_grapples(location_id)
        CombatService._release_invalid_gunpoints(location_id)
        if resumed_paid_action:
            combat_meta.pop('completedPendingActionId', None)
            character.character.data = data
            flag_modified(character.character, 'data')
        if explosive_details and explosive_details['profile'].get('fuse')=='turn_end':
            from app.services.grenade_reaction import begin
            explosive_details['reaction_detonations']=begin(character,state)
        character.last_action = db.func.now()
        db.session.commit()
        if getattr(character,'_trap_end_turn',False) and state.current_location_character_id==character.id:
            CombatService.end_turn(location_id,user_id,location_character_id=character.id)
        if action_key == 'recover_from_shock':
            ended_state = CombatService.end_turn(
                location_id,
                user_id,
                location_character_id=character.id,
            )
            return {
                'character': CombatService._serialize_character(
                    character,
                    current_turn_id=ended_state.get('current_location_character_id'),
                ),
                'state': ended_state,
                'action': action_key,
                'attack': None,
                'aim': None,
                'posture_change': None,
                'facing_change': None,
                'draw_weapon': None,
                'reload_weapon': None,
                'reload_underbarrel': None,
                'clear_weapon_jam': None,
                'narrative_action': None,
                'cover': None,
                'brace_weapon': None,
                'melee_action': melee_action_details,
                'equipment_change': equipment_action_details,
                'module_change': equipment_module_details,
            }
        return {
            'character': CombatService._serialize_character(character, current_turn_id=state.current_location_character_id),
            'state': CombatService._serialize_state(location, state),
            'action': action_key,
            'attack': attack_details,
            'explosive': explosive_details,
            'thrown_item': thrown_details,
            'aim': aim_details,
            'posture_change': posture_details,
            'facing_change': facing_details,
            'draw_weapon': draw_details,
            'reload_weapon': reload_details,
            'reload_underbarrel': underbarrel_reload_details,
            'clear_weapon_jam': clear_jam_details,
            'narrative_action': narrative_action_details,
            'must_do_it': must_do_details,
            'consolation': consolation_details,
            'cover': cover_details,
            'brace_weapon': brace_details,
            'melee_action': melee_action_details,
            'equipment_change': equipment_action_details,
            'module_change': equipment_module_details,
            'gunpoint': gunpoint_details,
            'anomaly': anomaly_details,
            'mutant_action': mutant_action_details,
            'jump': jump_details,
        }

    @staticmethod
    @journaled
    def move_character(
        location_id,
        user_id,
        character_id,
        new_x,
        new_y,
        special_action=None,
        object_id=None,
        climb_mode=None,
        movement_mode=None,
        evasion=None,
        floor_level=None,
        climb_payment=None,
    ):
        location = CombatService._get_location(location_id)
        is_gm = CombatService._ensure_access(location, user_id)
        CombatService._release_invalid_grapples(location_id)
        state = LocationCombatState.query.filter_by(location_id=location_id).first()
        character = LocationCharacter.query.filter_by(
            character_id=character_id,
            location_id=location_id,
        ).order_by(
            LocationCharacter.last_action.desc().nullslast(),
            LocationCharacter.id.desc(),
        ).first()
        if not character:
            raise NotFoundError("Character not in location")
        from app.services.explosion_choice import require_resolved
        require_resolved(state)

        from app.services.sniper_trauma import require_resolved as require_trauma
        require_trauma(state)
        from app.services.character_interaction import CharacterInteractionService
        if CharacterInteractionService.movement_locked(character.id):
            raise ValidationError("Character cannot move while treatment is in progress")

        if not is_gm and character.controlled_by not in (None, user_id):
            raise PermissionDenied("Permission denied")
        CombatService.ensure_character_can_act(character)
        from app.services.character_jump import require_balance
        require_balance(character)
        if character.grappled_by_id:
            raise ValidationError("A grappled character cannot move independently")
        if (((character.character.data or {}).get('health') or {}).get('combatMeta') or {}).get('trappedNet'):
            raise ValidationError('Сначала освободитесь из сети или разрежьте её')

        from app.services.body_transport import validate_move, move_plan, finish_move
        transported_body = validate_move(character)

        grapple_captive = (
            LocationCharacter.query.filter_by(
                id=character.grapple_target_id,
                location_id=location_id,
            ).first()
            if character.grapple_target_id
            else None
        )
        if character.grapple_target_id and not grapple_captive:
            character.grapple_target_id = None
            character.grapple_strengthened = False
            character.grapple_choke_rounds = 0
            character.grapple_live_shield = False

        if state and state.status == 'active' and not is_gm and state.current_location_character_id != character.id:
            raise PermissionDenied("It is not this character's turn")

        _, _, _, active_anomaly = CombatService._active_anomaly(character)
        if state and state.status == 'active' and active_anomaly and special_action != 'climb':
            raise ValidationError("Use the anomaly escape action to move")

        character._movement_fall = None
        character._trap_end_turn=False
        character._movement_slip_checks = []
        character._movement_slip_stopped = False
        if special_action == 'drop_down':
            from app.services.body_transport import companion
            if companion(character):
                raise ValidationError('Сначала опустите тело: совместный прыжок вниз пока не поддерживается.')
            if grapple_captive:
                raise ValidationError('Release the grapple before jumping down')
            if CombatService._posture_key(character) != 'standing':
                raise ValidationError('Stand up before jumping down')
            portal = LocationObject.query.filter_by(id=object_id,location_id=location_id).first() if type(object_id) is int else None
            if not portal:
                raise NotFoundError('Opening not found')
            if not is_gm and (portal.properties or {}).get('gm_hidden'):
                raise PermissionDenied('Hidden opening cannot be used')
            if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (new_x,new_y)) or floor_level is not None and type(floor_level) is not int:
                raise ValidationError('Invalid jump landing')
            plan = CombatService._object_drop_plan(location,character,portal)
            if not plan:
                raise ValidationError('No clear downward passage or landing')
            landing = plan['landing']
            if abs(new_x-landing['x'])>1e-8 or abs(new_y-landing['y'])>1e-8 or floor_level is not None and floor_level != landing['level']:
                raise ValidationError('Invalid jump landing')
            approach = sum(math.hypot(b['x']-a['x'],b['y']-a['y']) for a,b in zip(plan['surfaces'],plan['surfaces'][1:]))
            travelled = approach+(plan['height'] if plan['height']<=2+1e-9 else 0)
            CombatService._validate_special_movement_distance(character,location,state,travelled)
            if state and state.status == 'active':
                if character.action_points_current < 1:
                    raise ValidationError('Not enough action points')
                character.action_points_current -= 1
                character.movement_distance_this_turn = (character.movement_distance_this_turn or 0)+travelled
            previous_position = (character.pos_x,character.pos_y)
            character.pos_x,character.pos_y,character.floor_level = landing['x'],landing['y'],landing['level']
            character._movement_surfaces = plan['surfaces']
            character.cover_object_id = None
            character.weapon_braced = False
            character.braced_weapon_index = None
            CombatService._clear_aim(character)
            character._movement_fall = (None if plan['height'] <= 2+1e-9 else
                CombatService.resolve_fall(character,plan['height'],round_number=max(0,state.round_number or 0) if state else 0))
            if active_anomaly:
                CombatService._leave_anomaly(character)
            if state and state.status == 'active':
                anomalies = CombatService._anomalies_at_tile(location,character.pos_x,character.pos_y,floor_level=character.floor_level)
                if anomalies:
                    CombatService._enter_anomaly(character,anomalies[0],max(1,state.round_number or 1),previous_position=previous_position)
            character.last_action = db.func.now()
            CombatService._apply_periodic_health_effects(character,phase='movement_end')
            CombatService._sync_location_effects_from_character(character)
            CombatService._release_invalid_gunpoints(location_id)
            from app.services.traps import after_landing
            after_landing(character,state)
            db.session.commit()
            if getattr(character,'_trap_end_turn',False) and state and state.status=='active' and state.current_location_character_id==character.id:
                CombatService.end_turn(location_id,user_id,location_character_id=character.id)
            return character,0,CombatService.get_state(location_id,user_id)

        if special_action in {'climb','climb_up','descend'}:
            from app.services.body_transport import companion
            if companion(character):
                raise ValidationError('Сначала опустите тело: совместное перелезание пока не поддерживается.')
            if grapple_captive:
                raise ValidationError("A character cannot climb while holding a grapple")
            if CombatService._posture_key(character) != 'standing':
                raise ValidationError("Stand up before climbing")
            if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (new_x,new_y)):
                raise ValidationError('Некорректная точка перемещения')
            target_level = (character.floor_level or 0) if floor_level is None else floor_level
            if type(target_level) is not int or not (0<=new_x<=location.grid_width-1 and 0<=new_y<=location.grid_height-1):
                raise ValidationError('Некорректная точка перемещения')
            if special_action=='climb':
                obj = LocationObject.query.filter_by(id=object_id,location_id=location_id).first() if type(object_id) is int else None
                if not obj or (obj.properties or {}).get('gm_hidden') and not is_gm:
                    raise NotFoundError('Препятствие недоступно')
                plan = CombatService._object_climb_plan(location,character,obj)
            else:
                plan = CombatService._vertical_movement_plan(location,character,new_x,new_y,target_level)
            if not plan or plan['special_action']!=special_action:
                raise ValidationError('Нет допустимого подъёма, спуска или места приземления')
            landing = plan['path'][-1]
            if abs(new_x-landing[0])>1e-8 or abs(new_y-landing[1])>1e-8 or target_level!=plan['floor_level']:
                raise ValidationError('Точка приземления изменилась. Обновите предпросмотр')
            CombatService._validate_special_movement_distance(character,location,state,plan['distance'])
            climb_cost = 0
            if special_action=='descend':
                if state and state.status=='active':
                    if character.action_points_current<1:
                        raise ValidationError('Для спуска требуется 1 ОД')
                    character.action_points_current-=1
            else:
                profile = CombatService._climb_payment_profile(character.character.data,plan['climbs'])
                climb_cost = CombatService._pay_climb(character,profile,climb_payment,state)
            previous_position = (character.pos_x, character.pos_y)
            character.pos_x, character.pos_y = landing
            character.floor_level = plan['floor_level']
            character._movement_surfaces = plan['surfaces']
            character.cover_object_id = None
            character.weapon_braced = False
            character.braced_weapon_index = None
            if state and state.status=='active':
                character.movement_distance_this_turn = (character.movement_distance_this_turn or 0)+plan['distance']
            if special_action=='descend' and -plan['height_change']>2+1e-9:
                character._movement_fall = CombatService.resolve_fall(character,-plan['height_change'],round_number=state.round_number if state else 0)
            anomaly_changed = False
            if not state or state.status != 'active':
                anomaly_changed = CombatService._sync_anomaly_after_relocation(
                    location, character, previous_position,
                )
            CombatService._clear_aim(character)
            character.last_action = db.func.now()
            CombatService._apply_periodic_health_effects(character, phase='movement_end')
            CombatService._sync_location_effects_from_character(character)
            CombatService._release_invalid_gunpoints(location_id)
            from app.services.traps import after_landing
            after_landing(character,state)
            db.session.commit()
            if getattr(character,'_trap_end_turn',False) and state and state.status=='active' and state.current_location_character_id==character.id:
                CombatService.end_turn(location_id,user_id,location_character_id=character.id)
            state_payload = (
                CombatService._serialize_state(location, state) if state else
                CombatService.get_state(location_id, user_id) if anomaly_changed else None
            )
            return character, climb_cost, state_payload

        try:
            if isinstance(new_x,bool) or isinstance(new_y,bool):
                raise ValueError()
            new_x = float(new_x)
            new_y = float(new_y)
            if not math.isfinite(new_x) or not math.isfinite(new_y):
                raise ValueError()
        except (TypeError, ValueError):
            raise ValidationError("Invalid coordinates")

        if not (0 <= new_x <= location.grid_width-1 and 0 <= new_y <= location.grid_height-1):
            raise ValidationError("Out of bounds")

        companion_offset = (
            (
                grapple_captive.pos_x - character.pos_x,
                grapple_captive.pos_y - character.pos_y,
            )
            if grapple_captive
            else None
        )
        ignored_character_ids = (
            [character.character_id, grapple_captive.character_id]
            if grapple_captive
            else None
        )
        target_level = (getattr(character, 'floor_level', 0) or 0) if floor_level is None else floor_level
        if isinstance(target_level, bool) or not isinstance(target_level, int):
            raise ValidationError('Invalid floor level')
        path = CombatService._find_movement_path(
            location,
            character.pos_x,
            character.pos_y,
            new_x,
            new_y,
            character.character_id,
            ignored_character_ids=ignored_character_ids,
            companion_offset=companion_offset,
            end_level=target_level,
            movement_mode=(movement_mode or 'walk') if state and state.status=='active' else None,
        )
        if not path:
            raise ValidationError("Path is blocked")

        from app.services.traps import path_traps
        stopper=next((trap for trap in path_traps(character,path) if trap.recipe_id in {'recipe-m4','recipe-m5','recipe-m13'}),None)
        if stopper:
            new_x,new_y,target_level=stopper.x,stopper.y,stopper.level
            path=CombatService._find_movement_path(location,character.pos_x,character.pos_y,new_x,new_y,character.character_id,
                ignored_character_ids=ignored_character_ids,companion_offset=companion_offset,end_level=target_level,
                movement_mode=(movement_mode or 'walk') if state and state.status=='active' else None)
            if not path:raise ValidationError('Путь недоступен')
        entered_anomaly = None
        if state and state.status == 'active':
            anomaly_candidates = CombatService._anomaly_candidates(location)
            for step_index, (step_x, step_y) in enumerate((path.get('path') or [])[1:], 1):
                anomalies = CombatService._anomalies_at_tile(
                    location, step_x, step_y, anomaly_candidates,
                    floor_level=path.get('surfaces', [{}] * len(path['path']))[step_index].get('level', getattr(character, 'floor_level', 0) or 0),
                )
                if not anomalies:
                    continue
                entered_anomaly = anomalies[0]
                new_x, new_y = step_x, step_y
                path = CombatService._find_movement_path(
                    location,
                    character.pos_x,
                    character.pos_y,
                    new_x,
                    new_y,
                    character.character_id,
                    ignored_character_ids=ignored_character_ids,
                    companion_offset=companion_offset,
                    end_level=next((n['level'] for n in path.get('surfaces', []) if n['x'] == new_x and n['y'] == new_y), target_level),
                )
                if not path:
                    raise ValidationError("Path to the anomaly is blocked")
                break

        cost = path['cost']
        climb_cost = path.get('climb_cost', 0)
        if state and state.status == 'active':
            movement_mode = str(movement_mode or '').lower()
            movement_mode = CombatService._effective_movement_mode(character, path, movement_mode)
            mode = MOVEMENT_MODES.get(movement_mode)
            if not mode:
                raise ValidationError("Choose a movement mode")
            posture = CombatService._posture_key(character)
            character_data=character.character.data if character.character else {}
            posture_profile = CombatService._validate_posture_movement(posture, movement_mode,character_data)
            sprinter_walk=CombatService._sprinter_walk(character_data,movement_mode,posture)

            current_round = max(1, state.round_number or 1)
            if not sprinter_walk and CombatService._strenuous_movement_is_blocked(
                character, movement_mode, current_round
            ):
                raise ValidationError("Running and sprinting are blocked by exhaustion")
            character_data = (
                character.character.data
                if character.character and isinstance(character.character.data, dict)
                else {}
            )
            CombatService._validate_equipment_movement(character_data, movement_mode)
            if (
                movement_mode == 'sprint'
                and CombatService._mutant_has_trait(character_data, 'Медлительный')
            ):
                raise ValidationError('Этот мутант не может спринтовать')
            if (
                movement_mode == 'sprint'
                and CombatService._disabled_limb_penalties(character_data)['sprint_blocked']
            ):
                raise ValidationError("Sprinting is unavailable with a disabled leg")

            route_cost = CombatService._movement_route_cost(path, movement_mode, posture,character_data)
            if movement_mode == 'backward_sideways':
                CombatService._validate_backward_sideways_path(character, path)
            if CombatService._mutant_has_trait(character_data, 'Полёт'):
                terrain_distance = route_cost['distance']
                divisor = mode['movement_divisor']
                flight_cost = (
                    0
                    if divisor is None
                    else terrain_distance * posture_profile['movement_multiplier'] / divisor
                )
                route_cost['movement_points'] = flight_cost + route_cost['climb_cost']
            distance = route_cost['distance']
            movement_cost = route_cost['movement_points']
            climb_action_points = 0
            if route_cost['climb_cost']>0:
                if not isinstance(climb_payment,str) or climb_payment not in {'movement','action'}:
                    raise ValidationError('Выберите оплату перелезания: ОП или ОД')
                climbs = path.get('climbs') or [{'base_cost':route_cost['climb_cost'],
                    'category':'high' if route_cost['climb_cost']>=12 else 'low'}]
                climb_profile = CombatService._climb_payment_profile(character_data,climbs)
                movement_cost -= route_cost['climb_cost']
                if climb_payment=='movement':
                    movement_cost += climb_profile['movement_points']
                else:
                    climb_action_points = climb_profile['action_points']
            if distance <= 0 and not (movement_mode in {'run','sprint'} and not character.movement_mode_this_turn):
                return character, 0, CombatService._serialize_state(location, state)

            used_mode = character.movement_mode_this_turn
            if used_mode == 'correction':
                used_mode = None
            correction_used = character.correction_distance_this_turn or 0
            if used_mode and used_mode != movement_mode and movement_mode != 'correction':
                raise ValidationError("Movement modes cannot be mixed in one turn")
            selecting_mode = correction_used <= 1e-9 if movement_mode == 'correction' else not used_mode
            evading = CombatService._movement_evasion_active(character) if used_mode else evasion is True
            if used_mode and evasion is not None and bool(evasion) != evading:
                raise ValidationError('Уклонение нельзя менять после начала движения в этом ходу')
            if evading and movement_mode not in {'walk', 'run', 'sprint'}:
                raise ValidationError('Уклонение доступно при ходьбе, беге и спринте')
            mode_action_points = (mode['action_points'] if selecting_mode else 0)+climb_action_points
            if sprinter_walk:mode_action_points=climb_action_points
            artifact_profile = artifact_passive_profile(character_data)
            if selecting_mode and not sprinter_walk and movement_mode in {'run', 'sprint'}:
                mode_action_points += artifact_profile['strenuous_action_cost']
            mode_free_actions = mode['free_actions'] if selecting_mode else 0
            used_distance = character.movement_distance_this_turn or 0
            penalty = CombatService._movement_penalty(character)
            from app.services.slippery_movement import resolve_route, saved_state
            slip_state = saved_state(character)
            limit_mode = slip_state.get('forced_from', movement_mode) if movement_mode == 'walk' else movement_mode
            max_distance = CombatService._movement_distance_limit(limit_mode, posture, evading, penalty,character_data)
            if movement_mode == 'correction':
                if correction_used+distance > 3+1e-9:
                    raise ValidationError('Корректировка ограничена 3 метрами за ход')
                # Без выбранного основного режима корректировка доступна на 3 м
                # независимо от штрафа. При выборе режима её путь уже входит в общий счётчик.
                max_distance = (CombatService._movement_distance_limit(slip_state.get('forced_from', used_mode),posture,evading,penalty,character_data)
                                if used_mode else max(3,used_distance+3-correction_used))
            if used_distance + distance > max_distance + 1e-9:
                raise ValidationError(
                    f"{mode['label']} distance is limited to {max_distance} meters per turn"
                )
            if character.action_points_current < mode_action_points:
                raise ValidationError("Not enough action points")
            if character.free_actions_current < mode_free_actions:
                raise ValidationError("Not enough free actions")

            if movement_cost > character.movement_points_current + 1e-9:
                raise ValidationError("Not enough movement points")
            from app.services.character_rules import before_move
            before_move(character,path,movement_mode)
            requested_mode = movement_mode
            if not sprinter_walk and movement_mode in {'run', 'sprint'} and (path.get('slippery', True) or slip_state or path.get('slippery_start')):
                resolved = resolve_route(location, character, path, movement_mode, posture, climb_payment, max_distance)
                path, movement_cost, distance = resolved['path'], resolved['cost'], resolved['distance']
                new_x, new_y = resolved['point']['x'], resolved['point']['y']
                movement_mode = resolved['mode']
                mode_action_points += resolved['climb_action_points'] - climb_action_points
                character._movement_slip_checks = resolved['checks']
                character._movement_slip_stopped = not resolved['complete']
                character_data = character.character.data
                character_data.setdefault('health', {}).setdefault('combatMeta', {})['slipperyMovement'] = resolved['state']
                if not resolved['complete']:
                    entered_anomaly = None
                    end_surface = (path.get('surfaces') or [resolved['point']])[-1]
                    if end_surface['height'] > resolved['point']['height'] + 1e-8:
                        character_data['health']['combatMeta']['jumpSupport'] = {
                            'position':[new_x,new_y,end_surface['level']], 'height':end_surface['height']}

            if movement_cost <= character.movement_points_current+1e-9:
                character.movement_points_current = max(0,round(character.movement_points_current-movement_cost,9))
            else:
                raise ValidationError("Not enough movement points")

            character.action_points_current -= mode_action_points
            character.free_actions_current -= mode_free_actions
            character_data.setdefault('health', {}).setdefault('combatMeta', {})['movementEvasion'] = evading
            character.character.data = character_data
            flag_modified(character.character, 'data')
            if movement_mode == 'correction':
                character.correction_distance_this_turn = correction_used + distance
            else:
                character.movement_mode_this_turn = movement_mode
            character.movement_distance_this_turn = used_distance + distance

            breathless_immunity = CombatService._consumable_stat_value_bonus(character_data, 'breathless_immunity') > 0
            if requested_mode == 'run' and not sprinter_walk and not breathless_immunity:
                superior_runner = CombatService._mutant_has_trait(
                    character_data, 'Превосходный бегун',
                )
                ideal_runner = CombatService._mutant_has_trait(
                    character_data, 'Идеальный бегун',
                )
                receives_breathlessness = False if ideal_runner else (
                    random.randint(1, 100) <= 50 if superior_runner and selecting_mode else not superior_runner
                )
                avoidance=character_passives(character_data)['breathless_avoid_chance']
                if receives_breathlessness and avoidance and random.randint(1,100)<=avoidance:receives_breathlessness=False
                if receives_breathlessness:
                    character.strenuous_movement_blocked_until_round = max(
                        character.strenuous_movement_blocked_until_round or 0,
                        current_round + 1 + artifact_profile['breath_duration_bonus'],
                    )
            elif requested_mode == 'sprint' and not breathless_immunity:
                avoidance=character_passives(character_data)['breathless_avoid_chance']
                if not avoidance or random.randint(1,100)>avoidance:
                    character.strenuous_movement_blocked_until_round = max(
                        character.strenuous_movement_blocked_until_round or 0,
                        current_round + 2 + artifact_profile['breath_duration_bonus'],
                    )
            cost = movement_cost

        from app.services.character_rules import record_approach
        if state and state.status=='active':record_approach(character,path,cost)
        previous_x, previous_y = character.pos_x, character.pos_y
        transported_plan = move_plan(character, path)
        character.pos_x = new_x
        character.pos_y = new_y
        character.floor_level = path['surfaces'][-1]['level'] if path.get('surfaces') else target_level
        finish_move(character, transported_plan, location)
        character._transported_body = transported_body
        from app.services.character_rules import refresh_nearby
        refresh_nearby(location_id)
        character._movement_surfaces = path.get('surfaces', [])
        anomaly_changed = False
        if not state or state.status != 'active':
            anomaly_changed = CombatService._sync_anomaly_after_relocation(
                location, character, (previous_x, previous_y),
            )
        if entered_anomaly:
            CombatService._enter_anomaly(
                character,
                entered_anomaly,
                max(1, state.round_number or 1),
                previous_position=(previous_x, previous_y),
            )
        if grapple_captive and companion_offset:
            grapple_captive.floor_level = character.floor_level
            grapple_captive.pos_x = new_x + companion_offset[0]
            grapple_captive.pos_y = new_y + companion_offset[1]
            grapple_captive.cover_object_id = None
            grapple_captive.weapon_braced = False
            grapple_captive.braced_weapon_index = None
            CombatService._clear_aim(grapple_captive)
        # Перемещение не заменяет отдельное действие поворота и не раскрывает
        # сектор за спиной бесплатным изменением взгляда.
        path_tiles = path.get('path') or []
        character._movement_mode = movement_mode
        CombatService._sync_grapple_facing(character)
        character.cover_object_id = None
        character.weapon_braced = False
        character.braced_weapon_index = None
        CombatService._clear_aim(character)
        moved_data = (
            character.character.data
            if character.character and isinstance(character.character.data, dict)
            else {}
        )
        moved_meta = moved_data.setdefault('health', {}).setdefault('combatMeta', {})
        if state and state.status == 'active' and CombatService._mutant_has_trait(
            moved_data, 'Возможность разгона',
        ):
            activity = moved_meta.setdefault('mutantTurnActivity', {
                'round': max(1, state.round_number or 1),
                'moved': False,
                'other': False,
            })
            activity['moved'] = True
            character.character.data = moved_data
            flag_modified(character.character, 'data')
        if moved_meta.pop('mutantAmbushActive', None):
            character.character.data = moved_data
            flag_modified(character.character, 'data')
        if moved_meta.pop('mutantRageAccuracy', None) is not None:
            character.character.data = moved_data
            flag_modified(character.character, 'data')
        CombatService._queue_opportunity_attacks(
            location_id, character, path_tiles, state,
        )
        CombatService._refresh_ugly_appearance_penalties(location_id)
        character.last_action = db.func.now()
        CombatService._apply_periodic_health_effects(character, phase='movement_end')
        CombatService._sync_location_effects_from_character(character)
        CombatService._release_invalid_gunpoints(location_id)
        from app.services.traps import after_move
        after_move(character,path,state)
        db.session.commit()

        state_payload = (
            CombatService._serialize_state(location, state) if state else
            CombatService.get_state(location_id, user_id) if anomaly_changed or transported_body else None
        )
        if getattr(character,'_trap_end_turn',False) and state and state.status=='active' and state.current_location_character_id==character.id:
            state_payload=CombatService.end_turn(location_id,user_id,location_character_id=character.id)
        return character, cost, state_payload

    @staticmethod
    @journaled
    def end_combat(location_id, user_id):
        location = CombatService._get_location(location_id)
        CombatService._ensure_access(location, user_id)
        if location.lobby.gm_id != user_id:
            raise PermissionDenied("Only GM can end combat")

        state = LocationCombatState.query.filter_by(location_id=location_id).first()
        if not state:
            raise ValidationError("Combat is not active")

        from app.services.sniper_trauma import require_resolved
        require_resolved(state)
        state.status = 'idle'
        from app.services.sniper_position import expire as expire_sniper_position
        expire_sniper_position(location_id,state)
        state.round_number = 0
        state.turn_index = 0
        state.turn_order = []
        state.current_location_character_id = None
        state.reaction_pending_location_character_id = None
        state.reaction_return_location_character_id = None
        state.pending_explosives = []
        state.area_effects = []

        loc_chars = LocationCharacter.query.filter_by(location_id=location_id).all()
        for loc_char in loc_chars:
            from app.services.portable_lights import advance as advance_portable_lights
            if advance_portable_lights(loc_char.character.data or {},battle_end=True):flag_modified(loc_char.character,'data')
            loc_char.initiative_roll = None
            cancel_actor_actions(loc_char)
            loc_char.initiative_total = None
            loc_char.movement_points_current = 0
            loc_char.movement_mode_this_turn = None
            loc_char.movement_distance_this_turn = 0
            loc_char.correction_distance_this_turn = 0
            loc_char.strenuous_movement_blocked_until_round = 0
            loc_char.melee_swing_round = None
            loc_char.melee_block_round = None
            loc_char.melee_block_effectiveness = 0
            loc_char.grapple_target_id = None
            loc_char.grappled_by_id = None
            loc_char.grapple_strengthened = False
            loc_char.grapple_choke_rounds = 0
            loc_char.grapple_live_shield = False
            loc_char.facing_changed_round = None
            CombatService._set_active_weapon(loc_char, loc_char.drawn_weapon_index)
            CombatService._clear_aim(loc_char)
            character = getattr(loc_char, 'character', None)
            if character and isinstance(character.data, dict):
                data = character.data
                from app.services.character_traits import carried_weapons
                for weapon in carried_weapons(data):
                    if isinstance(weapon, dict):
                        lost=max(0,CombatService._coerce_int(weapon.pop('_combatDurabilityLoss',0),0))
                        if lost and trait_count(data,'Протёрт до блеска') and CombatService._weapon_class(weapon)=='machine_gun':
                            current,maximum=CombatService._weapon_durability(weapon)
                            weapon['durability']=min(maximum,current+lost//2)
                            CombatService._weapon_durability(weapon)
                        weapon.pop('_usedInCurrentCombat', None)
                health = data.get('health') if isinstance(data.get('health'), dict) else {}
                meta = health.setdefault('combatMeta', {})
                for key in ('consumableModifiers', 'bleedingModifiers'):
                    values = meta.get(key)
                    if isinstance(values, list):
                        meta[key] = [value for value in values if not (
                            isinstance(value, dict)
                            and (value.get('scope') == 'combat' or value.get('note') == 'hematogen')
                        )]
                effects = normalize_effect_list(health.get('effects') or [])
                untreated = [effect for effect in effects if effect.get('type') == 'untreated_wound']
                meta['untreatedWoundsAfterCombat'] = len(untreated)
                health['effects'] = [
                    effect for effect in effects
                    if effect.get('scope') != 'combat'
                    and (effect.get('type') != 'shock' or effect.get('source') == 'pepper_three_head_hits')
                ]
                for key in ('pepperHeadHits', 'pepperSkipTurns', 'pepperSkipTurnActive'):
                    meta.pop(key, None)
                meta.pop('shockRecoveryRound', None)
                meta.pop('movementEvasion', None)
                meta.pop('slipperyMovement', None)
                meta.pop('grenadeReaction',None)
                meta.pop('painShockRecoveredRound', None)
                meta.pop('painShockRecovered', None)
                meta.pop('firedRound', None)
                meta.pop('injuryRound', None)
                meta.pop('reactionReserve', None)
                meta.pop('mutantPackRollBonus', None)
                meta.pop('mutantRageAccuracy', None)
                meta.pop('mutantCamouflageActive', None)
                meta.pop('mutantCamouflageUntilRound', None)
                meta.pop('reactionActive', None)
                meta.pop('gunpoint', None)
                meta.pop('gunpointFailedRound', None)
                sync_health_derived_statuses(health)
                data['health'] = health
                character.data = data
                flag_modified(character, 'data')
            CombatService._sync_location_effects_from_character(loc_char)

        db.session.commit()
        return CombatService._serialize_state(location, state)
