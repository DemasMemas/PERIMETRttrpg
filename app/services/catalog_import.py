"""Дополнительные разделы книги снаряжения; исходные числа не заменяются балансными догадками."""
from datetime import datetime, timedelta
import re

from app.services.equipment_importer import _normalize_text as text, _as_float as number, _as_int


def excel_fraction(value):
    raw=text(value).replace(',', '.')
    try:
        serial=float(raw)
        if serial.is_integer() and 20000<=serial<=80000:
            date=datetime(1899,12,30)+timedelta(days=int(serial))
            raw=f'{date.day}/{date.month}'
    except ValueError:pass
    return raw


def volume(value):
    raw=excel_fraction(value)
    if re.fullmatch(r'\d+(?:\.\d+)?/\d+(?:\.\d+)?',raw):
        a,b=map(float,raw.split('/'))
        return a/b if b else 0
    return number(raw)


def template(name,category,price,weight,size,description='',subcategory=None,attributes=None):
    return {'name':text(name),'category':category,'subcategory':subcategory,'item_class':None,
        'price':_as_int(price),'weight':number(weight),'volume':volume(size),'description':text(description),
        'attributes':{'import_source':'equipment_workbook',**(attributes or {})},'compatible_ids':[]}


def misc_templates(rows):
    section=None;result=[]
    sections={'Взлом и ловушки','Выживание и Лагерь','Инструменты','Вообще другое','Материалы для создания','Материалы для создания и ремонта','Ускоренная перезарядка','Батарейки'}
    for row in rows:
        name=text(row.get('A'))
        if name in sections and not text(row.get('C')):section=name;continue
        if not section or not name or not re.fullmatch(r'[\d\s.,]+',text(row.get('C'))):continue
        if name=='Аккумуляторы Экзоскелета':continue  # Профиль заряда импортируется существующим обработчиком.
        attrs={'source_section':section,'source_size':excel_fraction(row.get('D'))}
        description=text(row.get('E'));category='misc'
        if section=='Инструменты':
            category='tool'
            charges=re.search(r'(\d+)\s*/\s*\1\s*$',name)
            if charges:attrs.update(uses=int(charges[1]),max_uses=int(charges[1]))
        if section.startswith('Материалы'):
            category='crafting_material'
            attrs['craft_material']=True
        if name.casefold()=='батарейки':
            category='device'
            attrs.update(slot_type='battery',uses=1,power=100)
        if 'бинокль' in name.casefold():attrs['scouting_bonus']=25 if 'дальномером' in name.casefold() else 15
        litres=re.search(r'объем\s*([\d.,]+)\s*литр',name,re.I)
        if litres:attrs['liquid_capacity_liters']=number(litres[1])
        if name=='КПК':attrs.update(uses_battery=True,battery_duration_seconds=200*3600,battery_drain_percent_per_hour=.5)
        if row.get('D') in (None,'','---','-'):attrs.setdefault('unspecified_fields',[]).append('volume')
        item=template(name,category,row.get('C'),row.get('B'),row.get('D'),description,
                      'battery' if attrs.get('slot_type')=='battery' else section,attrs)
        result.append(item)
    # КПК и книги находятся в отдельных таблицах, не в основной колонке цен.
    if any(text(r.get('G'))=='КПК' for r in rows):
        result.append(template('КПК','misc',1500,.25,1,'КПК для связи и хранения электронных книг. Включённый КПК расходует 0,5% заряда батарейки за игровой час.','Электроника',{'uses_battery':True,'battery_duration_seconds':200*3600,'battery_drain_percent_per_hour':.5}))
    mode=None
    for row in rows:
        name=text(row.get('A'));price=text(row.get('B'))
        if name=='Книги навыков':mode='skill';continue
        if name=='Книги для улучшения понимания снаряжения':mode='equipment';continue
        if not mode or not name or not price.isdigit() or not text(row.get('C')):continue
        attrs={'book_kind':mode,'book_subject':name,'reading_seconds':14400,'unspecified_fields':['weight','volume']}
        if mode=='skill':
            attrs.update(training_extra_xp=1,skill_level_limit=16)
            description='Электронная книга. Чтение занимает 4 часа. При получении очка навыка за обучение даёт ещё одно; действует до уровня навыка 16.'
        else:
            attrs.update(salvage_bonus=25,unlocks_modifications=True)
            description='Электронная книга. Чтение занимает 4 часа. Позволяет модифицировать снаряжение указанного вида и даёт +25 к его разбору.'
        result.append(template(row['C'],'misc',price,0,0,description,'Книги',attrs))
    return result


def cosmetic_templates(rows):
    result=[];section=None;headphone_size=4
    for row in rows:
        name=text(row.get('A'))
        if name in {'Наушники','Перчатки','Очки','Бижутерия'}:section=name;continue
        if not section or not name or name=='Название':continue
        if section=='Наушники' and re.fullmatch(r'[\d.,]+',text(row.get('E'))):
            if row.get('F'):headphone_size=volume(row['F'])
            attrs={'deafening_coef':number(row.get('B'),1),'noise_absorption':number(row.get('C')),'awareness_bonus':number(row.get('D')),'unspecified_fields':['weight']}
            desc='При расчёте оглушения сначала вычитается поглощение шума, затем применяется коэффициент. Бонус Внимательности действует только на слух.'
            result.append(template(name,'headphones',row['E'],0,headphone_size,desc,None,attrs))
        elif section=='Перчатки' and re.fullmatch(r'[\d.,]+',text(row.get('J'))):
            attrs={'charisma_bonus':number(row.get('B')),'unspecified_fields':['weight']}
            if name=='Рабочие':attrs['craft_difficulty_modifier']=-5
            if name in {'Военные','Военные М2'}:attrs['anomaly_protection_bonus']=5 if name.endswith('М2') else 3
            if name=='Медицинские':attrs.update(patient_infection_multiplier=.5,sterile_duration_seconds=3600)
            if name=='Обвязки на руки':attrs.update(unarmed_damage_factor=15,unarmed_damage_minimum=25)
            result.append(template(name,'gloves',row['J'],0,row.get('K'),row.get('C'),'Перчатки',attrs))
        elif section=='Очки' and re.fullmatch(r'[\d.,]+',text(row.get('F'))):
            attrs={'charisma_bonus':number(row.get('C')),'flash_protection':number(text(row.get('D')).replace('х','').replace('x',''),1),'eye_physical_protection':number(row.get('E'))*100,'unspecified_fields':['weight']}
            if attrs['eye_physical_protection']:
                attrs.update(incompatible_with_gasmask=True,incompatible_with_visor=True)
            if name == 'Сварочные защитные очки':
                attrs['welding_glasses'] = True
            desc=text(row.get('H'))
            if attrs['eye_physical_protection']:
                desc='Нельзя носить вместе с противогазом или шлемом с забралом.'
            result.append(template(name,'glasses',row['F'],0,row.get('G'),desc,'Очки',attrs))
        elif section=='Бижутерия' and re.fullmatch(r'[\d.,]+',text(row.get('C'))):
            kind='ring' if 'кольцо' in name.casefold() else 'bracelet' if 'браслет' in name.casefold() else 'earrings' if 'серьги' in name.casefold() else 'necklace'
            result.append(template(name,'jewelry',row['C'],0,0,'Украшение.',kind,{'charisma_bonus':number(row.get('B')),'jewelry_kind':kind,'unspecified_fields':['weight','volume']}))
    return result


def storage_templates(rows):
    """Правая таблица «Прочее»: рюкзаки и кейсы с разными внутренними объёмами."""
    result = []
    section = None
    allowed = []
    for row in rows:
        name = text(row.get('G'))
        if name == 'Рюкзаки':
            section = 'backpack'
            continue
        if name == 'Кейсы':
            section = 'case'
            continue
        if section == 'case':
            if name.startswith('Медицинские.'):
                allowed = ['med']
            elif name.startswith('Патронные.'):
                allowed = ['ammo', 'grenade']
            elif name.startswith('Магазинные.'):
                allowed = ['magazine']
            elif name.startswith('Оружейные.'):
                allowed = ['weapon', 'melee_weapon', 'ammo', 'grenade', 'magazine']
            elif name.startswith(('Вещевые.', 'Сейфы с защитой')):
                allowed = []
        if not name or not re.fullmatch(r'\d+', text(row.get('H'))):
            continue
        if section == 'backpack':
            match = re.match(r'\d+', text(row.get('I')))
            if not match:
                continue
            attrs = {'limit': int(match[0]), 'weight_reduction': 0,
                     'unspecified_fields': ['weight', 'volume']}
            reduction = re.search(r'штраф к весу на (\d+)', text(row.get('J')))
            if reduction:
                attrs['weight_reduction'] = int(reduction[1])
            result.append(template(name, section, row['H'], 0, 0, row.get('J'), 'Рюкзаки', attrs))
        elif section == 'case' and re.fullmatch(r'\d+', text(row.get('I'))):
            attrs = {'capacity': _as_int(row['I']), 'allowed_categories': list(allowed),
                     'access_action_points': _as_int(re.sub(r'\D', '', text(row.get('L'))))}
            if name.startswith('Сейф'):
                attrs.update(anomaly_protected=True, radiation_shielded=True)
            desc = 'Доступ к содержимому: %s ОД.' % attrs['access_action_points']
            result.append(template(name, 'container', row['H'], re.sub(r'[^\d.,]', '', text(row.get('K'))),
                                   row.get('J'), desc, 'Кейсы', attrs))
    # Пояс описан текстом в отдельной ячейке, а не строкой общей таблицы.
    if any(text(row.get('G')) == 'Пояс' for row in rows):
        result.append(template('Пояс', 'belt', 500, 0, 0,
            'Позволяет носить один противогаз или шлем и до двух подсумков.', None,
            {'pouches': [], 'max_pouches': 2, 'unspecified_fields': ['weight', 'volume']}))
    return result


def devices_templates(rows):
    """Приборы не превращаются в расходники без своего игрового обработчика."""
    result = []
    detector_section = False
    container_kind = None
    for row in rows:
        left = text(row.get('G'))
        name = text(row.get('L'))
        if name == 'Детектор Аномалий':
            detector_section = True
            container_kind = None
        if name.startswith('Контейнеры для использования'):
            detector_section = False
            container_kind = 'equipped'
        elif name.startswith('Контейнеры для хранения'):
            container_kind = 'storage'
        if detector_section and name and re.fullmatch(r'\d+', text(row.get('M'))):
            protection = text(row.get('P'))
            attrs = {'avoidance_chance': _as_int(row.get('N')), 'unspecified_fields': ['volume']}
            if protection == 'Да':
                attrs['anomaly_immunities'] = ['electrical', 'chemical', 'thermal', 'gravity']
            elif protection == 'Электр. и хим.':
                attrs['anomaly_immunities'] = ['electrical', 'chemical']
            result.append(template(name, 'detector', row['M'], 1, 0,
                'Шанс избежать аномалии: %s.' % attrs['avoidance_chance'], 'Аномалии', attrs))
        if container_kind and name and re.fullmatch(r'\d+', text(row.get('M'))):
            if not text(row.get('N')):
                continue
            attrs = {'radiation_limit': _as_int(row.get('N')), 'artifact_container_kind': container_kind,
                     'unspecified_fields': ['volume']}
            container_name = f'Контейнер для артефактов {name.strip()}' if re.fullmatch(r'\d+ ур', name.strip()) else name
            result.append(template(container_name, 'container', row['M'], 2.5, 0,
                'Радиация артефакта до %s. Положить артефакт: 4 ОД; вынуть: 8 ОД.' % attrs['radiation_limit'],
                'Использование' if container_kind == 'equipped' else 'Хранение', attrs))
        if left in {'Силок', 'Рыбак'} and text(row.get('I')):
            attrs = {'artifact_count': _as_int(row.get('L')), 'field_rank_offset': -1 if left == 'Силок' else 0}
            result.append(template(left, 'misc', row['I'], row.get('H'), row.get('K'),
                'Стационарный генератор артефактов. Устанавливается возле аномального поля и срабатывает во время Выброса.',
                'Генераторы артефактов', attrs))
        elif ('генератор' in left.casefold() or left.startswith('Генератор ')) and re.fullmatch(r'\d+', text(row.get('I'))) and text(row.get('J')):
            attrs = {'artifact_generation_bonus': _as_int(row['J']), 'consumed_on_use': True}
            if not row.get('K'):
                attrs['unspecified_fields'] = ['volume']
            result.append(template(left, 'misc', row['I'], row.get('H'), row.get('K'),
                'Одноразовый генератор артефактов. Бросается в центр аномалии. Бонус генератора: %+d.' % attrs['artifact_generation_bonus'],
                'Генераторы артефактов', attrs))
        elif left in {'Очки ПНВ-1п', 'Монокуляр Сидр-1г', 'ПНВ Supernova'}:
            penalty = _as_int(re.search(r'-?\d+', text(row.get('H')))[0])
            result.append(template(left, 'device', row.get('J'), 1, 1,
                f'Штрафы точности и визуальной Внимательности в темноте: {penalty}.', None,
                {'slot_type': 'nvg', 'night_vision': True, 'accuracy_penalty': penalty, 'awareness_penalty': penalty,
                 'electrical_anomaly_immune': left == 'ПНВ Supernova', 'source_name': left}))
    return result


POUCH_TYPES = {'Вещевой': [], 'Медицинский': ['med'], 'Магазинный': ['magazine'],
               'Патронный': ['ammo'], 'Гранатный': ['grenade'], 'Подсумок для бронеплит': ['armor_plate']}


def rig_templates(rows):
    result = []
    mode = None
    ready = None
    for row in rows:
        name = text(row.get('A'))
        if name == 'Модульные разгрузки':
            mode = 'modular'
        elif text(row.get('B')) == 'Подсумки':
            mode = 'ready'
            ready = None
        elif name == 'Название' and text(row.get('B')) == 'Защита':
            mode = 'plates'
            ready = None
        elif 'подсумок' in name.casefold() and row.get('C') and row.get('D') and mode is None:
            sizes = excel_fraction(row.get('B')).split('/')
            prices = text(row.get('C')).split('/')
            mounts = excel_fraction(row.get('D')).split('/')
            if not (len(sizes) == len(prices) == len(mounts)):
                raise ValueError(f'Несогласованные варианты подсумка: {name}')
            kind = next((key for key in POUCH_TYPES if name.startswith(key)), None)
            if not kind:
                continue
            for size, price, mount in zip(sizes, prices, mounts):
                attrs = {'capacity': number(size), 'mount_volume': number(mount),
                         'allowed_categories': POUCH_TYPES[kind], 'unspecified_fields': ['weight']}
                if kind == 'Подсумок для бронеплит':
                    attrs['slots'] = [{'type': 'armor_plate', 'label': 'Бронеплита', 'maxItems': 1}]
                result.append(template(f'{name.rstrip(" -")} — {size}', 'pouch', price, 0, mount,
                                       row.get('E'), kind, attrs))
        elif mode == 'modular' and name.startswith('Система ') and row.get('D'):
            description = text(row.get('C'))
            match = re.search(r'занимают ([\d.,]+)', description)
            key = next((key for key in POUCH_TYPES if description.casefold().startswith(key.casefold()[:-2])), None)
            if 'бронеплит' in description.casefold():
                key = 'Подсумок для бронеплит'
            attrs = {'total_capacity': _as_int(row.get('B')), 'pouches': [], 'modular': True,
                     'unspecified_fields': ['volume']}
            if match:
                attrs['pouch_volume_multiplier'] = number(match[1])
                attrs['discount_pouch_type'] = key
            result.append(template(name, 'vest', row['D'], 0, 0, description, 'Модульные разгрузки', attrs))
        elif mode == 'ready':
            if name and name != 'Название' and re.fullmatch(r'\d+', text(row.get('D'))):
                ready = template(name, 'vest', row['D'], 0, row.get('E'), '', 'Готовые разгрузки',
                                 {'pouches': [], 'modular': False})
                result.append(ready)
            pouch = text(row.get('B'))
            if ready and pouch:
                kind = next((key for key in POUCH_TYPES if pouch.startswith(key)), None)
                if kind and re.fullmatch(r'\d+', text(row.get('C'))):
                    count = re.search(r'(\d+) шт', pouch)
                    for _ in range(int(count[1]) if count else 1):
                        ready['attributes']['pouches'].append({'name': kind, 'capacity': number(row['C']),
                            'internalVolume': number(row['C']), 'allowed_categories': POUCH_TYPES[kind], 'contents': []})
                elif 'подавач' in pouch.casefold():
                    ready['attributes']['loader_attachment'] = True
        elif mode == 'plates' and name.startswith('Бронеплита '):
            material = text(row.get('C')).split('/')
            if len(material) != 3:
                raise ValueError(f'Не удалось прочитать бронеплиту: {name}')
            attrs = {'slot_type': 'armor_plate', 'protection': {'physical': round(number(row.get('B')) * 100, 6)},
                     'durability': number(material[1]), 'max_durability': number(material[1]),
                     'material': material[0].strip(), 'unspecified_fields': ['volume']}
            item = template(name, 'armor_plate', row['D'], re.sub(r'[^\d.,]', '', material[2]), 0,
                            'Устанавливается в подсумок для бронеплит. Защита не складывается с бронёй: используется наибольшая.',
                            material[0].strip(), attrs)
            item['item_class'] = text(row.get('E')).removeprefix('Броня ')
            result.append(item)
    return result


def additional_templates(misc_rows, cosmetic_rows, rigs_rows=()):
    return [*misc_templates(misc_rows), *storage_templates(misc_rows),
            *devices_templates(misc_rows), *cosmetic_templates(cosmetic_rows), *rig_templates(rigs_rows)]


def explosive_templates(rows):
    from app.services.combat import CombatService, EXPLOSIVE_PROFILES
    result = []
    for row in rows:
        name = text(row.get('L'))
        key = CombatService._explosive_key(name)
        if not key or not re.fullmatch(r'\d+', text(row.get('M'))):
            continue
        profile = EXPLOSIVE_PROFILES[key]
        attrs = {'explosive_key': key, 'activation_time': text(row.get('T')),
                 'unspecified_fields': ['weight']}
        caliber = profile.get('projectile_caliber') or profile.get('caliber')
        is_projectile = bool(profile.get('projectile_range'))
        if caliber:
            attrs['caliber'] = caliber
        attrs['effective_range'] = profile.get('projectile_range')
        item = template(name, 'grenade', row['M'], 0, .5 if is_projectile else (2 if key in {'rdg6', 'screen'} else 1),
                        row.get('V'), 'Подствольные' if is_projectile else 'Ручные', attrs)
        item['item_class'] = text(row.get('N')) or None
        result.append(item)
    return result
