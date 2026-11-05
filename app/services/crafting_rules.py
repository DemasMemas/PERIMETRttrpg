"""Однозначная часть создания: требования, сложность, время и потери при неудаче."""
from copy import deepcopy
from functools import lru_cache
import json
from math import isfinite
from pathlib import Path

from app.services.exceptions import ValidationError


def resolve_creation(quote,*,roll=None,rng=None):
    if quote.get('automatic_success'):return {'roll':None,'success':True,'automatic':True}
    if roll is None:
        import random
        roll=(rng or random.SystemRandom()).randint(1,100)
    if type(roll) is not int or not 1<=roll<=100:raise ValidationError('Для создания нужен результат к100 от 1 до 100')
    return {'roll':roll,'success':roll>quote['difficulty'],'automatic':False}


@lru_cache(maxsize=1)
def _catalog():
    return json.loads((Path(__file__).resolve().parents[1] / 'data/creation_rules.json').read_text(encoding='utf-8'))


def catalog():
    return deepcopy(_catalog())


def skill_profile(level):
    if type(level) not in {int, float} or not isfinite(level) or level < 1:
        raise ValidationError('Для создания нужен действительный уровень навыка не ниже 1')
    for minimum, modifier, automatic_below, random_loss, chosen_loss, all_loss in [
        (20, -30, 40, 0, 0, False), (17, -25, 25, 0, 1, False),
        (15, -20, None, 1, 0, False), (13, -10, None, 1, 1, False),
        (10, 0, None, 2, 0, False), (8, 10, None, 3, 1, False),
        (5, 20, None, 5, 0, False), (2, 30, None, 0, 0, True), (1, 50, None, 0, 0, True),
    ]:
        if level >= minimum:
            return {'difficulty_modifier': modifier, 'automatic_below': automatic_below,
                    'automatic_allowed': level >= 5,
                    'failure_loss': {'random': random_loss, 'chosen': chosen_loss, 'all': all_loss}}


def creation_quote(level, difficulty, *, difficulty_modifier=0, base_automatic=False):
    profile = skill_profile(level)
    if type(difficulty) not in {int, float} or not isfinite(difficulty) or difficulty < 0:
        raise ValidationError('Исходная сложность должна быть неотрицательным числом')
    if type(difficulty_modifier) not in {int, float} or not isfinite(difficulty_modifier):
        raise ValidationError('Неверный модификатор сложности')
    threshold = profile['automatic_below']
    automatic = profile['automatic_allowed'] and (base_automatic or threshold is not None and difficulty < threshold)
    return {**profile, 'original_difficulty': difficulty,
            'difficulty': difficulty + profile['difficulty_modifier'] + difficulty_modifier,
            'automatic_success': bool(automatic)}


def modification_quote(kind, level, item_class, engineering, *, double_charges=False, material_value=0, workbench=False):
    if kind not in {'weapon', 'armor'} or type(level) is not int or level not in {1, 2, 3}:
        raise ValidationError('Неизвестный вид или уровень модификации')
    class_factor={'Н':4,'H':4,'Экзоскелет':6}.get(item_class) if isinstance(item_class,str) else item_class
    if type(class_factor) is not int or class_factor not in {1, 2, 3, 4, 6}:
        raise ValidationError('Для этого класса снаряжения пока не определена цена установки')
    if type(material_value) not in {int, float} or not isfinite(material_value) or material_value < 0:
        raise ValidationError('Стоимость материалов должна быть неотрицательной')
    required = ({'weapon': [10, 13, 17], 'armor': [11, 14, 18]}[kind])[level - 1]
    reduction = -25 if double_charges else 0
    if kind == 'armor':
        reduction -= int(material_value // 150)
    if type(engineering) not in {int, float} or not isfinite(engineering) or engineering < 1:
        raise ValidationError('Неверный уровень Инженерии')
    original = [50, 75, 80][level - 1]
    quote = creation_quote(engineering,original,difficulty_modifier=reduction)
    return {**quote, 'required_engineering': required, 'skill_sufficient': engineering >= required,
            'tool_grade': ['simplified', 'standard', 'advanced'][level - 1],
            'tool_uses': 20 if double_charges else 10,
            'duration_seconds': [1800, 3600, 7200][level - 1] // (2 if workbench else 1),
            'professional_price': 5000 * class_factor * level}
