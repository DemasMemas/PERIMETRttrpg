"""Чтение игровых рецептов и модификаций; неоднозначные поля сохраняются как данные."""
import re
from pathlib import Path

from app.services.equipment_importer import _read_sheet_rows, _normalize_text as text, _as_float as number


def duration_seconds(value):
    raw = text(value).casefold()
    if not raw:
        return None
    match = re.search(r'(\d+(?:[.,]\d+)?)\s*(секунд|минут|час|сут)', raw)
    if not match:
        return None
    return int(number(match[1]) * {'секунд': 1, 'минут': 60, 'час': 3600, 'сут': 86400}[match[2]])


def components(value):
    result = []
    for part in re.split(r',\s*(?![^()]*\))', text(value)):
        if not part:
            continue
        uses = re.fullmatch(r'(.+?)\.\s*(\d+)\s+Использования', part, re.I)
        if uses:
            result.append({'name': uses[1].strip(), 'quantity': int(uses[2]), 'source_text': part.strip()})
            continue
        fuel_liters=re.fullmatch(r'(Топливо)\s*\(\s*(\d+(?:[.,]\d+)?)\s*л\s*\)',part,re.I)
        if fuel_liters:
            result.append({'name':fuel_liters[1], 'quantity':number(fuel_liters[2]),
                           'source_text':part.strip(),'liquid_liters':True})
            continue
        filter_charges=re.fullmatch(r'(Фильтр для противогаза)\s*\(\s*(\d+)\s*заряд(?:ов|а)?\s*\)',part,re.I)
        if filter_charges:
            result.append({'name':filter_charges[1], 'quantity':int(filter_charges[2]),
                           'source_text':part.strip(),'filter_charges':True})
            continue
        if '/' in part:
            alternatives=[]
            for option in part.split('/'):
                option=option.strip()
                fuel_liters=re.fullmatch(r'(Топливо)\s*\(\s*(\d+(?:[.,]\d+)?)\s*л\s*\)',option,re.I)
                if fuel_liters:
                    alternatives.append({'name':fuel_liters[1], 'quantity':number(fuel_liters[2]),
                                         'liquid_liters':True})
                    continue
                count=re.search(r'\(\s*(\d+)\s*\)\s*$',option)
                if not count and re.search(r'\([^)]*\)\s*$',option):
                    alternatives=[]
                    break
                alternatives.append({'name':option[:count.start()].strip() if count else option,
                    'quantity':int(count[1]) if count else 1})
            if len(alternatives)>1:
                result.append({'name':'/'.join(option['name'] for option in alternatives),
                    'quantity':1,'alternatives':alternatives,'source_text':part.strip()})
                continue
        count = re.search(r'\(\s*(\d+)\s*\)\s*$', part)
        result.append({'name': part[:count.start()].strip() if count else part.strip(),
                       'quantity': int(count[1]) if count else 1, 'source_text': part.strip()})
    return result


def recipe(cell, name, ingredients, difficulty, bench, duration, skill, group, **extra):
    raw = text(difficulty)
    if not name or re.fullmatch(r'\d+(?:\.\d+)?', text(name)) or not ingredients or not (raw == 'Автоуспех' or re.fullmatch(r'\d+(?:\.\d+)?(?:/\d+)*', raw)):
        return None
    output = re.search(r'\(\s*(\d+)\s*\)\s*$|\s+(\d+)\s*шт\.?\s*$', text(name), re.I)
    label = text(name)
    result = {'id': 'recipe-' + cell.lower(), 'name': label, 'skill': 'other.engineering' if skill=='engineering' else skill, 'group': group,
              'output_name': re.sub(r'\s*\(Инженерия\)\s*$','',label[:output.start()].strip() if output else label),
              'output_count': int(output[1] or output[2]) if output else 1,
              'components': components(ingredients), 'components_text': text(ingredients),
              'difficulty_options': [number(v) for v in raw.split('/')] if raw != 'Автоуспех' else [],
              'automatic': raw == 'Автоуспех', 'tools_text': text(bench), 'duration_text': text(duration),
              'duration_seconds': duration_seconds(duration), **extra}
    active = re.search(r'для старта.*?(\d+\s*(?:секунд|минут|час)\w*)', text(duration), re.I)
    if active:
        result['active_seconds'] = duration_seconds(active[1])
    return result


def parse_recipes(rows):
    result = []
    column_m = 'traps'
    medicine = False
    advanced = False
    for index, row in enumerate(rows, 1):
        index = row.get('__row__', index)
        # Адрес строки берётся из полного списка XML, включая пустые строки.
        def add(cell, *args, **kwargs):
            if cell in row.get('__merged__', []):
                return
            value = recipe(f'{cell}{index}', *args, **kwargs)
            if value:
                # Автор подтвердил обычные каталожные результаты этих двух рецептов.
                value['output_name']={'recipe-m29':'Отвертка','recipe-m30':'Кусачки'}.get(value['id'],value['output_name'])
                if value['id']=='recipe-m22':value['name']=value['output_name']='Мешок с песком'
                result.append(value)
        add('F', row.get('F'), row.get('G'), row.get('H'), row.get('I'), '20 минут',
            'other.engineering', 'Электроника')
        name_m = text(row.get('M'))
        if name_m.startswith('Укрепления.'):
            column_m = 'fortifications'
        elif name_m.startswith('Инструменты.'):
            column_m = 'tools'
        if column_m == 'tools':
            add('M', name_m, row.get('N'), row.get('O'), row.get('P'), row.get('Q'),
                'other.engineering', 'Инструменты', requires_tool_crafting=True)
        else:
            add('M', name_m, row.get('N'), row.get('P'), row.get('Q') if column_m == 'traps' else '',
                row.get('S') if column_m == 'traps' else row.get('R'),
                'other.engineering' if '(Инженерия)' in name_m else 'other.survival',
                'Ловушки' if column_m == 'traps' else 'Укрепления',
                result_text=text(row.get('O')))
        grenade_components=text(row.get('W'))
        bench='Смесительный станок'
        if re.search(r'\.\s*Станок не нужен\s*$',grenade_components,re.I):
            grenade_components=re.sub(r'\.\s*Станок не нужен\s*$','',grenade_components,flags=re.I)
            bench='Не нужен'
        add('V', row.get('V'), grenade_components, row.get('X'), bench, '30 минут',
            'other.engineering', 'Боеприпасы')
        name_aa = text(row.get('AA'))
        if name_aa.startswith('Медицина.'):
            medicine = True
        if name_aa == 'Продвинутая медицина':
            advanced = True
        add('AA', name_aa, row.get('AB'), row.get('AC'), row.get('AD'), row.get('AE'),
            'other.medicine' if medicine else 'other.survival',
            'Продвинутая медицина' if advanced else 'Медицина' if medicine else 'Продукты')
    approved_components = {
        'recipe-aa21': 'Химические материалы (2), Сахар',
        'recipe-aa31': 'Металлические детали, Марля, Бумага',
        'recipe-aa39': 'Медицинские материалы (2)',
    }
    for item in result:
        if item['id']=='recipe-aa12':
            item['components'][0].update(name='Загрязнённая вода',mixed_water=True,
                source_text='Болотная, дождевая или радиоактивная вода: всего 10 порций')
            item['components_text']='Болотная, дождевая или радиоактивная вода (10 порций в сумме), Фильтр для противогаза (10 зарядов)'
        if item['id'] in approved_components:
            item['components_text'] = approved_components[item['id']]
            item['components'] = components(item['components_text'])
    # ВОГ-17 удалён автором из системы; не подменяется другим боеприпасом.
    return [item for item in result if not re.search(r'вог[\s-]*17',item['name'],re.I)]


def parse_weapon_modifications(rows):
    result = []
    family = None
    for index, row in enumerate(rows, 1):
        index = row.get('__row__', index)
        name = text(row.get('A'))
        if name in {'Пистолеты', 'Дробовики', 'Пистолеты-пулеметы', 'Штурмовые винтовки и карабины',
                    'Снайперские винтовки', 'Гранатометы', 'Пулемёты'}:
            family = name
        match = re.fullmatch(r'Модификации (\d) уровня', name)
        if not family or not match:
            continue
        for column in ('B', 'C'):
            if column in row.get('__merged__', []):
                continue
            description = text(row.get(column))
            if description and not re.fullmatch(r'\d+(?:\.\d+)?', description):
                result.append({'id': f'weapon-{column.lower()}{index}', 'kind': 'weapon', 'family': family,
                               'level': int(match[1]), 'description': description})
    return result


def parse_armor_modifications(rows):
    result = []
    families = {'Лёгкая', 'Средняя', 'Тяжелая', 'Аномальная', 'Экзоскелет', 'Ушанка', 'Противогазы',
                'Забрала', 'Шлема без забрала', 'Шлема с забралом', 'Шлема-противогазы', 'Пояс'}
    branches = {'Физический', 'Аномальный', 'Удобство', 'Упрочнение', 'Пафос', 'Защита',
                'Эргономичность', 'Полное погружение', 'Многозадачность', 'Полевой медик',
                'Исследователь', 'Шериф', 'Стрелок', 'Инженер'}
    for name_column, description_column in [('A', 'B'), ('D', 'E'), ('G', 'H')]:
        family = branch = None
        level = 0
        for index, row in enumerate(rows, 1):
            index = row.get('__row__', index)
            name = text(row.get(name_column))
            if name_column in row.get('__merged__', []):
                continue
            if name in families:
                family, branch, level = name, None, 0
                continue
            if name in branches:
                branch, level = name, 0
                continue
            description = text(row.get(description_column))
            if not family or not branch or not name or not description or description == 'Эффект' or name.startswith('Модификац'):
                continue
            level += 1
            convenience = branch in {'Удобство', 'Пафос', 'Эргономичность'} or family == 'Пояс'
            result.append({'id': f'armor-{name_column.lower()}{index}', 'kind': 'belt' if family == 'Пояс' else 'armor',
                'family': family, 'branch': branch, 'level': 1 if convenience else level,
                'name': name, 'description': description, 'scientist_only': family == 'Экзоскелет'})
    return result


def parse_salvage(rows):
    table={row.get('__row__'):row for row in rows}
    bands=[(30,'AH'),(45,'AI'),(60,'AJ'),(75,'AK'),(90,'AL'),(99,'AM'),(104,'AN'),(109,'AO'),(149,'AP'),(None,'AQ')]
    result={}
    labels={'91-99':5,'100-104':6,'105-109':7}
    for row_id,kind in ((3,'armor'),(4,'weapon'),(5,'ammo'),(6,'grenade'),(7,'electronics')):
        output=[]
        for maximum,column in bands:
            raw=text(table[row_id].get(column));materials=[]
            for part in components(raw) if raw not in {'','---'} else []:
                inherited=re.fullmatch(r'Лут из (\d+-\d+)',part['name'])
                if inherited:materials.extend(dict(c) for c in output[labels[inherited[1]]]['materials'])
                else:materials.append(part)
            totals={}
            for material in materials:totals[material['name']]=totals.get(material['name'],0)+material['quantity']
            output.append({'maximum':maximum,'source_text':raw,'materials':[{'name':name,'quantity':quantity} for name,quantity in totals.items()]})
        result[kind]=output
    return result


def parse_creation_catalog(workbook_path):
    source = Path(workbook_path)
    rows = _read_sheet_rows(source, 'Создание', include_row_numbers=True)
    pda_rows = _read_sheet_rows(source, 'Модификации КПК', include_row_numbers=True)
    pda = [{'id': f"pda-b{row.get('__row__', index)}", 'kind': 'pda', 'difficulty_group': text(row.get('A')),
            'name': text(row.get('B')), 'description': text(row.get('C'))}
           for index, row in enumerate(pda_rows, 1) if text(row.get('A')) in {'Низкая', 'Средняя', 'Высокая', 'Очень высокая'}
           and row.get('B') and row.get('__row__', index) < 28]
    pda_overrides = {
        'pda-b21': ('Очень высокая', 'Подменяет имя отправителя в эфире, в том числе именем другого аккаунта.'),
        'pda-b22': ('Высокая', 'При наличии образцов голоса позволяет имитировать речь человека. Применяется в сцене с ГМом.'),
    }
    for rule in pda:
        if rule['id'] in pda_overrides:
            rule['difficulty_group'], rule['description'] = pda_overrides[rule['id']]
    flame_recipes=[]
    for kind,weapon,tank,capacity,row in [('jet','Струйный огнемёт','Бак для струйного огнемета',8,37),('reactive','Реактивный огнемёт','Баллон для реактивного огнемета',16,46)]:
        flame_recipes.append(recipe(f'flame-{row}',weapon,'Металлические детали (15), Резина (6), Клей, Зажигалка, Датчик' if kind=='jet' else 'Металлические детали (25), Резина (10), Клей, Зажигалка, Датчик',65 if kind=='jet' else 70,'Станок Оружейника','1 час','engineering','Огнемёты'))
        flame_recipes.append(recipe(f'flame-{38 if kind=="jet" else 42}',tank+' (пустой)','Металлические детали (8), Резина (3)' if kind=='jet' else 'Металлические детали (5), Резина',60 if kind=='jet' else 50,'Станок Оружейника','1 час','engineering','Огнемёты'))
        for index,fuel in enumerate(('Бензин','Фосфорное топливо','Напалм')):
            ingredients=f'{tank} (пустой), Топливо(1 Литр) ({5 if kind=="jet" else 8})'+('',', Химические материалы',', Химические материалы (3)' if kind=='jet' else ', Химические материалы (5)')[index]
            if kind=='reactive' and index==1:ingredients+= ' (2)'
            entry=recipe(f'flame-{39+index if kind=="jet" else 43+index}',tank+f' ({fuel})',ingredients,
                (20,40,40)[index] if kind=='jet' else (25,45,45)[index],
                'Смесительный станок / Смесительные инструменты' if index==2 else 'Не нужен',('5 минут','10 минут','30 минут')[index],'engineering','Огнемёты')
            entry['components'][0]['whole_tank']=kind;flame_recipes.append(entry)
    flame_recipes.append(recipe('flame-47','Бак сжатого воздуха','Металлические детали (5)',60,'Станок Оружейника','1 час','engineering','Огнемёты'))
    for tool,duration in [('Ручной насос','5 минут'),('Электрический насос','30 секунд')]:
        entry=recipe('air-pump-'+('manual' if tool=='Ручной насос' else 'electric'),'Бак сжатого воздуха','Бак сжатого воздуха','Автоуспех',tool,duration,'engineering','Огнемёты')
        entry['name']='Накачка: '+tool;entry['maintenance']=True;entry['components'][0]['whole_air']=True;flame_recipes.append(entry)
    salvage=parse_salvage(rows)
    material_names=sorted({m['name'] for bands in salvage.values() for band in bands for m in band['materials']})
    material_definitions=[]
    approved_materials={'Плата':(1000,1,.5),'Запал':(350,.25,.1),
                        'Нитроглицерин':(1350,.5,.5),'Взрыватель':(1000,.25,.1)}
    for name in material_names:
        price=None
        for source_row in rows:
            for name_column,price_column in [('F','J'),('M','R')]:
                output_name=re.sub(r'\s*\(Инженерия\)\s*$','',text(source_row.get(name_column)))
                if output_name==name and number(source_row.get(price_column))>0:price=int(number(source_row[price_column]))
        if name in approved_materials:
            approved_price,volume,weight=approved_materials[name]
            material_definitions.append({'name':name,'price':approved_price,'volume':volume,'weight':weight})
        else:material_definitions.append({'name':name,'price':price})
    return {'version': 1, 'recipes': [*parse_recipes(rows),*flame_recipes],'salvage':salvage,'salvage_materials':material_definitions,
            'modifications': [*parse_weapon_modifications(_read_sheet_rows(source, 'Модификации оружия', include_row_numbers=True)),
                              *parse_armor_modifications(_read_sheet_rows(source, 'Модификации брони и шлемов', include_row_numbers=True)), *pda],
            'workbenches': [{'name': text(row['A']), 'price': int(number(row['B'])), 'hourly_rent': int(number(row['C'])),
                            'uses': 50} for row in rows if row.get('A') and row.get('B') and row.get('C')
                           and number(row['B']) > 1000 and number(row['C']) > 0]}
