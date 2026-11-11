"""Понятные описания поверх исходных правил; численные профили не меняются."""
import re


# Уточнения автора о периодических эффектах имеют приоритет над краткими ячейками книги.
CONSUMABLE_DESCRIPTIONS = {
    'стимулятор сова': 'Ампула. Закрывает действие сна. На следующие сутки: +2 истощения. На 4 часа даёт +3 к визуальной Внимательности и точности в темноте; бонус не работает в ПНВ. На свету: −3 к Внимательности.',
    'стимулятор сова-н': 'Ампула. Закрывает действие сна. На 8 часов даёт +5 к визуальной Внимательности и точности в темноте; бонус не работает в ПНВ.',
    'морфин': 'Ампула. Начинает действовать через ход. В каждый из трёх ходов действия снимает 3 уровня боли и блокирует новую боль; заблокированная боль не возвращается. При применении: −1 истощение, −2 стресса. Через час: +2 истощения. Бонус медикамента +1.',
    'стимулятор полукровка': 'Ампула. Уменьшает прогресс заражения крови на 50 процентных пунктов и блокирует его развитие на 3 дня.',
    'стимулятор воля': 'Ампула. На 3 хода блокирует новую боль и стресс от выстрелов и ранений. После окончания действия возвращает всю заблокированную боль и добавляет 1 истощение.',
    'стимулятор воля-н': 'Ампула. На 5 ходов блокирует новую боль и стресс от выстрелов и ранений. После окончания действия возвращает половину заблокированной боли.',
    'подавитель эмоций': 'Ампула. Действует 6 перемещений. Даёт преимущество на проверки против эффектов стресса; каждое повышение стресса блокируется с вероятностью 50%. При применении: −25 Пси-состояния.',
    'стимулятор шумодав': 'Ампула. На 3 перемещения лишает слуха и даёт +4 к проверкам зрительной внимательности.',
    'борщевик': 'Ампула. Каждый ход в течение 10 ходов восстанавливает 25 ОЗ и добавляет 5 опьянения.',
    'самопальный стимпак': 'Ампула. Каждый ход в течение 2 ходов восстанавливает 100 ОЗ и добавляет 15 опьянения.',
    'регенеративный стимулятор альфа': 'Ампула. Восстанавливает 50 ОЗ каждый ход в течение 8 ходов. В первые 3 хода добавляет по 1 истощению; всего 3.',
    'регенеративный стимулятор бета': 'Ампула. Восстанавливает 75 ОЗ каждый ход в течение 10 ходов. В первые 2 хода добавляет по 1 истощению; всего 2.',
    'регенеративный стимулятор звезда': 'Ампула. Восстанавливает 100 ОЗ каждый ход в течение 10 ходов. При применении добавляет 1 истощение.',
    'военный стимпак': 'Ампула. Каждый ход в течение 2 ходов восстанавливает 200 ОЗ и снижает тяжесть кровотечений на 2.',
    'научный стимпак': 'Ампула. Каждый ход в течение 3 ходов восстанавливает 250 ОЗ и снимает 5 радиации.',
    'препарат радист': 'Ампула. Блокирует 100% получаемой при перемещении радиации: до 100 единиц суммарно, не дольше суток. В первые 3 хода добавляет по 1 истощению; всего 3.',
    'пакет крови': 'Снижает стадию кровопотери каждый раунд в течение 4 ходов. Нужна совместимая группа крови и капельница либо заряд набора восстановления конечностей. Капельница продлевает действие на 1 ход и не расходуется. Доплата к цене: группа I — 0 ₽, II — 50 ₽, III — 125 ₽, IV — 500 ₽.',
}


def _name(value):
    return re.sub(r'["«»]', '', str(value or '')).strip().casefold()


def clean_description(value):
    value = re.sub(r'\s+', ' ', str(value or '')).strip()
    if value.casefold() in {'нет', '-', '---'}:
        return ''
    value = value.replace('трамва', 'травма').replace('коэффицент', 'коэффициент')
    value = value.replace('Останаливает', 'Останавливает').replace('предназачен', 'предназначен')
    value = re.sub(r'Штраф перемещения -(\d+)', r'Снижает штраф перемещения на \1', value)
    value = re.sub(r'Штраф к весу -(\d+)', r'Снижает штраф нагрузки на \1', value)
    value = re.sub(r'Штраф к весу \+(\d+)', r'Повышает штраф нагрузки на \1', value)
    sentences, seen = [], set()
    for part in re.split(r'(?<=[.!?])\s+', value):
        key = part.strip().rstrip('.!?').casefold()
        if key and key not in seen:
            sentences.append(part.strip())
            seen.add(key)
    result = ' '.join(sentences)
    return result + ('.' if result and result[-1] not in '.!?' else '')


def glove_description(item):
    if item.get('category') != 'gloves':
        return None
    name = _name(item.get('name'))
    if name == 'обвязки на руки':
        return 'Обвязки усиливают удары руками. Урон удара равен 15 × бонус Силы, минимум 25.'
    if name in {'военные м2', 'военные перчатки м2', 'перчатки военные м2'}:
        return 'Пока перчатки надеты, дают +5% к аномальной защите и +1 к броскам Харизмы.'
    return None


def describe_template(item):
    gloves = glove_description(item)
    if gloves:
        return gloves
    category = item['category']
    attrs = item.get('attributes') or {}
    original = clean_description(attrs.get('source_description', item.get('description')))
    if _name(item['name']) in {'фонарик','улучшенный фонарик','налобный фонарик','налобный фонарь'} and category!='weapon_module':
        original=re.sub(r'Зона действия фонарика\s*[-—:]\s*конус с высотой\s*\d+\s*и основанием\s*\d+\.?', '', original,flags=re.I)
        original=re.sub(r'Конус света:\s*\d+\s*м,?\s*ширина на конце\s*\d+\s*м\.?', '', original,flags=re.I)
        return clean_description('Конус света: 20 м, ширина на конце 5 м. '+original)
    clauses = []
    if category == 'consumable' and _name(item['name']) in CONSUMABLE_DESCRIPTIONS:
        return CONSUMABLE_DESCRIPTIONS[_name(item['name'])]
    if attrs.get('book_kind'):
        subject = clean_description(attrs.get('book_subject')).rstrip('.')
        if attrs['book_kind'] == 'skill':
            section = f" Раздел: {attrs['book_skill_name']}." if attrs.get('book_skill_name') else ''
            return f'{subject}.{section} Чтение занимает 4 часа. При обучении по этой книге дополнительное очко навыка +1; действует до уровня навыка 16.'
        return f'Электронная книга: {subject}. Чтение занимает 4 часа. Разрешает модификации этого вида снаряжения и даёт +25 к его разбору.'
    if category == 'weapon':
        clauses.append('Встроенный магазин.' if attrs.get('fixedMagazine') else 'Сменный магазин.')
        modes = attrs.get('fire_modes') or {}
        options = ['Одиночная стрельба']
        if modes.get('duplex_size'):
            options.append('дуплет')
        if modes.get('supports_burst'):
            options.append('стрельба очередью')
        clauses.append(', '.join(options) + '.')
    elif category == 'magazine':
        if attrs.get('isLoader') or attrs.get('loadingDevice'):
            clauses.append('Приспособление для зарядки. Само по себе не заменяет установленный магазин.')
        names = attrs.get('compatible_weapon_names') or []
        if names:
            clauses.append('Совместимость: ' + ', '.join(names) + '.')
            original = ''  # В исходной ячейке здесь находится тот же список без подписи.
    elif category == 'ammo':
        if re.search(r'12[.,]7[хx×]55', item['name'], re.I):
            original = ('При непробитии наносит четверть урона; вариант ЭП — десятую часть. '
                        'При полном пробитии проводятся две проверки дополнительной травмы.')
        variants = {'bp': 'БП', 'ep': 'ЭП', 'ubp': 'УБП', 'rip': 'RIP', 'incendiary': 'зажигательный',
                    'explosive': 'разрывной', 'flashbang': 'светошумовой', 'gas': 'газовый', 'smoke': 'дымовой'}
        supported = [variants[key] for key in attrs.get('ammo_variants') or [] if key in variants]
        if supported:
            clauses.append('Доступные варианты: ' + ', '.join(supported) + '.')
        if not original and not supported:
            clauses.append('Боеприпас калибра ' + str(attrs.get('caliber') or item['name']) + '.')
    elif category == 'melee_weapon':
        if attrs.get('armor_piercing') is not None:
            original = re.sub(r'^\d+(?:[.,]\d+)?% Бронебойность\.\s*', '', original)
    elif category == 'backpack':
        clauses.append('Вещи из рюкзака достаются за 2 ОД.')
        if attrs.get('weight_reduction'):
            clauses.append('Снижает штраф нагрузки, пока надет.')
            original = ''
    elif category == 'vest' and isinstance(attrs.get('pouches'), list) and attrs['pouches']:
        pouches = {}
        for pouch in attrs['pouches']:
            key = pouch['name'], pouch['capacity']
            pouches[key] = pouches.get(key, 0) + 1
        composition = ', '.join(f'{name.lower()} ×{count} (вместимость {capacity:g})'
                                for (name, capacity), count in pouches.items())
        clauses.append('В комплекте подсумки: ' + composition + '.')
    elif category == 'vest' and attrs.get('loader_attachment'):
        clauses.append('Разгрузка с креплением для подавача.')
    elif category == 'glasses' and not original:
        clauses.append('Снижает получаемое ослепление.')
    elif category in {'armor', 'helmet'}:
        names = {'face': 'лицо', 'eyes': 'глаза', 'ears': 'уши', 'head': 'голова', 'crown': 'темя',
                 'forehead': 'лоб', 'back': 'затылок', 'back_head': 'затылок', 'nape': 'затылок',
                 'torso': 'торс', 'arms': 'руки', 'legs': 'ноги'}
        zones = [names[key] for key in attrs.get('protection_zones') or [] if key in names]
        if zones:
            clauses.append('Закрывает: ' + ', '.join(zones) + '.')
    elif category == 'gas_mask' and not original:
        clauses.append('Для защиты от радиации и газа необходим установленный фильтр с оставшимися зарядами.')
    elif category == 'consumable' and not original and str(item.get('subcategory') or '').casefold() == 'продукты':
        clauses.append('Продукт питания.')
    if original:
        clauses.append(original)
    return clean_description(' '.join(clauses))
