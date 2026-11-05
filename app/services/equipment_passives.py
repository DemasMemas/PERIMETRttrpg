"""Пассивные свойства надетых предметов, включая их старые полевые имена."""


def _item(data, slot):
    equipment = (data or {}).get('equipment') or {}
    item = equipment.get(slot) if isinstance(equipment, dict) else None
    if not isinstance(item, dict) or item.get('protectionDisabled'):
        return {}
    return item


def _worn(data, slot):
    item = _item(data, slot)
    if not item:
        return {}, {}
    from app.services.combat import CombatService
    return item, CombatService._template_attributes(item)


def glove_protection(data):
    from app.services.combat import CombatService
    _, attrs = _worn(data, 'gloves')
    return max(0, CombatService._coerce_float(attrs.get('anomaly_protection_bonus'), 0))


def flash_multiplier(data):
    from app.services.combat import CombatService
    item, attrs = _worn(data, 'glasses')
    from app.services.equipment_modifications import worn_items,rule
    result=max(0, CombatService._coerce_float(item.get('flashProtection', attrs.get('flash_protection')), 1))
    for gear in worn_items(data):result*=max(0,1-rule(gear,'flash_reduction'))
    return result


def eye_protection(data):
    from app.services.combat import CombatService
    item, attrs = _worn(data, 'glasses')
    return max(0, min(100, CombatService._protection_percent(
        item.get('eyePhysicalProtection', attrs.get('eye_physical_protection')), 0)))


def unarmed_damage(data, strength_bonus):
    item, attrs = _worn(data, 'gloves')
    wrapped = attrs.get('unarmed_damage_factor') or (str(item.get('name', '')).strip().casefold() == 'обвязки на руки')
    return max(25, 15 * strength_bonus) if wrapped else max(10, 10 * strength_bonus)


def night_vision_modifier(data, stat):
    from app.services.combat import CombatService
    from app.services.weapon_modules import active_nvg_module
    module = active_nvg_module(data)
    if module is None:
        return 0
    attrs = CombatService._template_attributes(module)
    value = module.get(stat, attrs.get(stat, 0))
    # Старые ручные профили могли хранить величину штрафа без знака.
    return -abs(CombatService._coerce_float(value, 0))


def darkness_modifier(data, stat):
    """Численный штраф темноты; слух и прочие причины помехи не затрагиваются."""
    from app.services.combat import CombatService, character_passives
    from app.services.weapon_modules import uses_nvg
    if character_passives(data)['no_darkness_penalty']:return 0
    if uses_nvg(data):return night_vision_modifier(data,stat)
    bonus_stat='darkness_awareness' if stat=='awareness_penalty' else 'darkness_accuracy'
    return -5+CombatService._consumable_stat_value_bonus(data,bonus_stat)


def validate_glasses(data, previous=None):
    from app.services.combat import CombatService
    from app.services.exceptions import ValidationError
    equipment = (data or {}).get('equipment') or {}
    if not isinstance(equipment, dict):
        return
    if previous is not None:
        def identity(value):
            return [(slot, _item(value, slot).get('id'), _item(value, slot).get('templateId'),
                     _item(value, slot).get('name'),
                     [(module.get('templateId'), module.get('name'), module.get('slotType'))
                      for module in _item(value, slot).get('installedModules') or [] if isinstance(module, dict)])
                    for slot in ('glasses', 'helmet', 'gasMask', 'armor')]
        if identity(previous) == identity(data):
            return  # Старый несовместимый комплект не мешает сохранить другие поля листа.
    glasses, attrs = _worn(data, 'glasses')
    if not glasses:
        return
    mask = _item(data, 'gasMask')
    if attrs.get('incompatible_with_gasmask') and (mask.get('templateId') or mask.get('name')):
        raise ValidationError('Защитные очки нельзя носить вместе с противогазом. Сначала снимите один из предметов.')
    if attrs.get('incompatible_with_visor'):
        helmet = _item(data, 'helmet')
        armor = _item(data, 'armor')
        integrated = CombatService._integrated_helmet_from_armor(armor, CombatService._equipment_template(armor))
        if (helmet and CombatService._helmet_blocks_gas_mask(helmet, CombatService._equipment_template(helmet))
                or integrated and CombatService._helmet_blocks_gas_mask(integrated)):
            raise ValidationError('Защитные очки нельзя носить со шлемом с забралом. Сначала снимите очки или шлем.')
