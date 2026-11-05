"""Полный каталог из книги и принятых игровых правил; без изменения исходного XLSX."""
from copy import deepcopy
import json
from pathlib import Path

from app.services.equipment_importer import parse_equipment_templates, _normalize_equipment_name


def _json_keys(value):
    if isinstance(value, dict):
        return {str(key): _json_keys(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_json_keys(child) for child in value]
    return value


WATER_VARIANTS = (
    ('Болотная вода', 10, 2),
    ('Дождевая вода', 5, 1),
    ('Радиоактивная вода', 3, 0.5),
)


def additional_water_templates(entries):
    """Утверждённые виды воды используют тару и число порций обычной воды."""
    base = next(item for item in entries if item['category'] == 'consumable' and item['name'] == 'Вода')
    result = []
    for name, radiation, exhaustion in WATER_VARIANTS:
        item = deepcopy(base)
        item['name'] = name
        item['price'] = 0
        item['description'] = (f'+{radiation} радиации, +{exhaustion:g} уровня истощения, '
                               '-10 опьянения при питье. 3 использования.')
        attrs = item['attributes']
        attrs['catalog_name'] = name
        attrs.pop('source_order', None)
        attrs['not_for_sale'] = True
        attrs['unspecified_fields'] = ['price']
        attrs['raw_description'] = item['description']
        attrs['source_description'] = item['description']
        profile = attrs['consumable']
        profile['direct']['radiation_delta'] = radiation
        profile['direct']['exhaustion_delta'] = exhaustion
        profile['effects'] = [effect for effect in profile['effects'] if effect['type'] not in {'generic'}]
        for effect in profile['effects']:
            if effect['type'] == 'radiation':
                effect['value'] = radiation
            elif effect['type'] == 'exhaustion':
                effect['value'] = exhaustion
        attrs['effects'] = deepcopy(profile['effects'])
        result.append(item)
    return result


def parse_catalog(workbook_path):
    from app.services.consumable_importer import parse_consumable_templates_v2
    from app.services.portable_lights import missing_templates as missing_lights
    from app.services.weapon_modules import builtin_templates as weapon_modules
    from app.services.catalog_import import explosive_templates
    from app.services.equipment_importer import _read_sheet_rows
    entries = [*parse_equipment_templates(Path(workbook_path)),
               *parse_consumable_templates_v2(Path(workbook_path)),
               *explosive_templates(_read_sheet_rows(Path(workbook_path), 'Магазины и Патроны')), *weapon_modules()]
    entries.extend(missing_lights(entries))
    from app.services.flamethrower import templates as flame_templates
    entries.extend(flame_templates())
    creation_path = Path(__file__).resolve().parents[1] / 'data/creation_rules.json'
    if creation_path.is_file():
        from app.services.catalog_import import template
        creation=json.loads(creation_path.read_text(encoding='utf-8'))
        known_names={_normalize_equipment_name(item['name']) for item in entries}
        for material in creation.get('salvage_materials',[]):
            if _normalize_equipment_name(material['name']) in known_names:continue
            entries.append(template(material['name'],'crafting_material',material['price'] or 0,material.get('weight',0),material.get('volume',1),
                'Компонент для создания и разбора предметов.','Материалы для создания и ремонта',
                {'import_source':'creation_workbook','unspecified_fields':(['weight'] if 'weight' not in material else [])+(['price'] if material['price'] is None else [])}))
        for bench in creation['workbenches']:
            entries.append(template(bench['name'], 'tool', bench['price'], 0, 0,
                'Рабочий станок. Ремонт и модификация занимают вдвое меньше времени; имеется расширенный набор инструментов. Аренда: %s ₽/ч.' % bench['hourly_rent'],
                'Станки', {'craft_workbench': True, 'uses': 50, 'max_uses': 50, 'hourly_rent': bench['hourly_rent'],
                           'unspecified_fields': ['weight', 'volume']}))
    entries.extend(additional_water_templates(entries))
    from app.services.cooked_meat import templates as cooked_meat_templates
    entries.extend(cooked_meat_templates())
    return prepare_catalog(entries)


def prepare_catalog(entries):
    """Снимок содержит игровые данные, но не полные строки исходного документа."""
    result = []
    known = set()
    for original in entries:
        item = deepcopy(original)
        item.pop('id', None)
        key = item['category'], _normalize_equipment_name(item['name'])
        if key in known:
            continue
        known.add(key)
        attrs = item['attributes'] = _json_keys(item.get('attributes') or {})
        attrs.pop('raw_row', None)
        missing=set(attrs.get('unspecified_fields') or [])
        if item['category']=='backpack':
            item['weight']=0
            item['volume']=attrs.get('limit',item.get('volume',1))
            missing.difference_update({'weight','volume'})
        elif item['category']=='armor_plate':
            item['volume']=4
            missing.discard('volume')
        elif 'volume' in missing:
            item['volume']=1
            missing.discard('volume')
        if attrs.get('book_kind') or item['category'] in {'gloves','glasses','jewelry','headphones'}:
            item['weight']=0
            missing.discard('weight')
        if missing:attrs['unspecified_fields']=sorted(missing)
        else:attrs.pop('unspecified_fields',None)
        from app.services.catalog_descriptions import describe_template
        attrs.setdefault('source_description', item.get('description') or '')
        item['description'] = describe_template(item)
        result.append(item)
    return result


def load_catalog(path=None):
    source = Path(path) if path else Path(__file__).resolve().parents[1] / 'data' / 'equipment_catalog.json'
    payload = json.loads(source.read_text(encoding='utf-8'))
    if payload.get('version') != 1 or not isinstance(payload.get('templates'), list):
        raise ValueError('Неизвестный формат каталога снаряжения')
    entries = payload['templates']
    from app.services.cooked_meat import templates as cooked_meat_templates
    return prepare_catalog([*entries, *additional_water_templates(entries), *cooked_meat_templates()])


def shifted_helmet_updates(previous,canonical):
    """Исправляет только доказуемый старый сдвиг столбцов; ручные поля не заменяются."""
    from app.services.equipment_importer import _normalize_text,_as_int,_as_float
    attrs=previous.get('attributes') or {};row=attrs.get('raw_row') or {}
    if previous.get('category') not in {'helmet','gas_mask'} or attrs.get('import_source')!='equipment_workbook':return None
    if _as_int(row.get('N'))<100 or _normalize_text(row.get('M')) not in {'1','2','3','Н','H'}:return None
    if str(previous.get('item_class'))!=_normalize_text(row.get('N')) or previous.get('price')!=_as_int(row.get('O')) or previous.get('volume')!=_as_float(row.get('J')):return None
    expected_charisma='1.5' if str(previous.get('name','')).casefold()=='ушанка' else _normalize_text(row.get('M'))
    if (str(attrs.get('accuracy_penalty'))!=_normalize_text(row.get('K')) or str(attrs.get('ergonomics_penalty'))!=_normalize_text(row.get('L'))
        or str(attrs.get('charisma_bonus'))!=expected_charisma or attrs.get('movement_penalty')!=_as_int(row.get('P'))):return None
    corrected=deepcopy(attrs)
    for key in ('accuracy_penalty','ergonomics_penalty','charisma_bonus','movement_penalty','integrated_visor','soft_headwear','modification_category'):
        corrected[key]=deepcopy(canonical['attributes'].get(key))
    return {'item_class':canonical['item_class'],'price':canonical['price'],'volume':canonical['volume'],'attributes':corrected}
