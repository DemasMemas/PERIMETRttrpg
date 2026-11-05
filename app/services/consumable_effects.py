from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.services.consumable_catalog_rules import (
    BLEEDING_CATALOG_RULES, EARLY_CATALOG_RULES,
    EVERYDAY_CATALOG_RULES, LATE_CATALOG_RULES,
    MEDICAL_CATALOG_RULES, SURGERY_POST_CATALOG_RULES,
    SURGERY_PRE_CATALOG_RULES,
    apply_catalog_rules, correct_owl_profile,
)


def _normalize(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return default


def _parse_signed(text: str, pattern: str) -> Optional[float]:
    text = re.sub(r'([+-])\s+(?=\d)', r'\1', text)
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    sign = -1 if match.group("sign") == "-" else 1
    raw = match.group("value").replace(",", ".")
    if "/" in raw:
        left, right = raw.split("/", 1)
        try:
            return sign * (float(left) / float(right))
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    try:
        return sign * float(raw)
    except (TypeError, ValueError):
        return None


def _parse_duration(text: str) -> Optional[int]:
    for pattern in [
        r"действует\s+(\d+)\s*(?:ход|хода|ходов)",
        r"работает\s+(\d+)\s*(?:ход|хода|ходов)",
        r"действует\s+(\d+)\s*минут",
        r"действует\s+(\d+)\s*час",
        r"действует\s+(\d+)\s*дн",
        r"срабатывает\s+через\s+(\d+)\s*(?:ход|хода|ходов|минут|час)",
        r"на\s+(\d+)\s*(?:ход|хода|ходов|минут|час|дн)",
        r"(?:на\s+)?(\d+)\s*(?:перемещени[еяй]|передвижени[еяй])",
    ]:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _to_int(match.group(1), 0)
    return None


def _without_movement_duration(text):
    # Тире между подписью срока и числом — пунктуация, а не знак бонуса к движению.
    return re.sub(r'\b(?:длительность|действует|длится|работает)\s*[-–—:=]?\s*\d+\s*'
                  r'(?:перемещени[еяй]|передвижени[еяй])\b', '', text, flags=re.I)


def remove_stale_movement_modifiers(character_data, lobby_id=None):
    """Уже введённая официальная ампула также не должна оставлять ложный бонус срока."""
    meta=((character_data or {}).get('health') or {}).get('combatMeta') or {}
    modifiers=meta.get('consumableModifiers')
    if not isinstance(modifiers,list) or not any(isinstance(m,dict) and m.get('stat')=='movement_points' for m in modifiers):return
    from flask import has_app_context
    if not has_app_context():return
    from app.services.item_template_lookup import resolve_item_template, LOCAL_TEMPLATE_ID_OFFSET
    from app.models.lobby_templates import LobbyItemTemplate
    from app.extensions import db
    retained=[]
    for modifier in modifiers:
        if not isinstance(modifier,dict) or modifier.get('stat')!='movement_points':
            retained.append(modifier);continue
        match=re.fullmatch(r'consumable-template:(\d+)',str(modifier.get('source') or ''))
        template_id=int(match[1]) if match else 0
        template=(db.session.get(LobbyItemTemplate,template_id-LOCAL_TEMPLATE_ID_OFFSET)
                  if template_id>=LOCAL_TEMPLATE_ID_OFFSET and lobby_id is None
                  else resolve_item_template({'templateId':template_id},lobby_id))
        attrs=(template.attributes or {}) if template else {}
        description=(template.description or '') if template else ''
        official=(attrs.get('catalog_name') and attrs.get('raw_description')==description
                  and not (attrs.get('consumable') or {}).get('editor_version'))
        if official and _without_movement_duration(description)!=description:
            corrected=parse_consumable_effects(attrs['catalog_name']+'. '+description)
            if 'movement_points_delta' not in corrected['direct']:continue
        retained.append(modifier)
    meta['consumableModifiers']=retained


def darkness_modifiers_from_description(description: str) -> List[Dict[str, Any]]:
    """Parse conditional vision/shooting modifiers independently of item names."""
    lower = _normalize(description).lower()
    duration = re.search(r"(?:длится|действует|на)\s+(\d+)\s*(час(?:а|ов)?|минут(?:ы|у)?|ход(?:а|ов)?)\b", lower)
    if not duration:
        return []
    amount = int(duration.group(1))
    unit = duration.group(2)
    if unit.startswith("час"):
        remaining, tick = amount * 60, "time_elapsed"
    elif unit.startswith("минут"):
        remaining, tick = amount, "time_elapsed"
    else:
        remaining, tick = amount, "turn_end"
    modifiers = []

    def add(stat, value, note):
        modifiers.append({
            "stat": stat, "value": value, "remaining": remaining,
            "tick": tick, "note": note,
        })

    for match in re.finditer(
        r"([+-]\s*\d+)\s*(?:на|к)\s*броск\w*\s+внимательности\s+(в\s+темноте|на\s+свету)",
        lower,
    ):
        stat = "darkness_awareness" if "темноте" in match.group(2) else "light_awareness"
        add(stat, int(match.group(1).replace(" ", "")),
            "Внимательность в темноте" if stat == "darkness_awareness" else "Внимательность на свету")
    immunity = re.search(r"убирает\s+помех\w*\s+на\s+броск[^.]*в\s+темноте", lower)
    if immunity:
        if "внимательност" in immunity.group():
            add("darkness_visual_immunity", 1, "Зрение в темноте")
        if "точност" in immunity.group() or "стрельб" in immunity.group():
            add("darkness_shooting_immunity", 1, "Стрельба в темноте")
    return modifiers


def with_darkness_modifiers(profile: Dict[str, Any], description: str) -> Dict[str, Any]:
    """Backfill previously imported templates without changing their other rules."""
    result = {**profile, "modifiers": list(profile.get("modifiers") or [])}
    existing = {modifier.get("stat") for modifier in result["modifiers"] if isinstance(modifier, dict)}
    result["modifiers"].extend(
        modifier for modifier in darkness_modifiers_from_description(description)
        if modifier["stat"] not in existing and not ('darkness_accuracy' in existing and modifier['stat'].endswith('_immunity'))
    )
    return result


def normalize_owl_modifiers(character_data):
    """Сохраняет остаток срока уже действующей официальной Совы при обновлении правил."""
    from flask import has_app_context
    from app.extensions import db
    if not has_app_context():return
    meta=((character_data.get('health') or {}).get('combatMeta') or {})
    modifiers=meta.get('consumableModifiers')
    if not isinstance(modifiers,list):return
    sources={m.get('source') for m in modifiers if isinstance(m,dict) and m.get('stat') in {'darkness_visual_immunity','darkness_shooting_immunity'}}
    from app.services.item_template_lookup import resolve_item_template
    for source in sources:
        match=re.fullmatch(r'consumable-template:(\d+)',str(source))
        if not match:continue
        from app.models.lobby_templates import LobbyItemTemplate
        from app.services.item_template_lookup import LOCAL_TEMPLATE_ID_OFFSET
        template_id=int(match[1])
        template=(db.session.get(LobbyItemTemplate,template_id-LOCAL_TEMPLATE_ID_OFFSET)
                  if template_id>=LOCAL_TEMPLATE_ID_OFFSET
                  else resolve_item_template({'templateId':template_id}))
        if not template:continue
        attrs=template.attributes or {};name=str(attrs.get('catalog_name') or template.name).casefold()
        official=attrs.get('catalog_name') and attrs.get('raw_description')==template.description
        if not official or 'стимулятор сова' not in name or (attrs.get('consumable') or {}).get('editor_version'):continue
        selected=[m for m in modifiers if isinstance(m,dict) and m.get('source')==source and m.get('stat') in {'darkness_awareness','light_awareness','darkness_visual_immunity','darkness_shooting_immunity'}]
        if not selected:continue
        example=next((m for m in selected if m.get('stat')=='darkness_awareness'),selected[0])
        improved='стимулятор сова-н' in name
        modifiers[:]=[m for m in modifiers if not any(m is old for old in selected)]
        for stat,value in [('darkness_awareness',5 if improved else 3),('darkness_accuracy',5 if improved else 3),*([] if improved else [('light_awareness',-3)])]:
            modifiers.append({**example,'stat':stat,'value':value,'note':{'darkness_awareness':'Внимательность в темноте без ПНВ','darkness_accuracy':'Точность в темноте без ПНВ','light_awareness':'Внимательность на свету'}[stat]})


def enrich_imported_consumable(attributes, description):
    """Дополнить старый импорт без записи каталога и без замены настроенных значений."""
    from copy import deepcopy
    current = attributes.get('consumable')
    if (not isinstance(current, dict) or current.get('editor_version') or not attributes.get('catalog_name')
            or attributes.get('raw_description') != description):
        return current
    parsed = parse_consumable_effects(str(attributes['catalog_name']) + '. ' + str(description or ''))
    result = deepcopy(current)
    old_direct = result.get('direct') or {}
    structural = {'duration','duration_phase','medical_difficulty','application_form','uses'}
    result['direct'] = {**parsed.get('direct',{}), **old_direct}
    if set(old_direct) <= structural and not result.get('modifiers'):
        result['direct'].update(parsed.get('direct') or {})
    name = str(attributes['catalog_name']).casefold()
    if ('movement_points_delta' not in parsed['direct']
            and _without_movement_duration(description) != description):
        result['direct'].pop('movement_points_delta', None)
        result['modifiers'] = [m for m in result.get('modifiers') or [] if m.get('stat') != 'movement_points']
        result['effects'] = [e for e in result.get('effects') or []
                             if e.get('type') != 'movement_points' and not (
                                 e.get('source') == 'direct' and str(e.get('name') or '').startswith(('ОП ', 'Штраф перемещения ')))]
    if name in {'стимулятор орёл','стимулятор орел','военный стимулятор пчела'}:
        result['direct'].pop('post_duration_exhaustion_delta', None)
        result['direct']['exhaustion_delta'] = 1
        result['effects'] = [e for e in result.get('effects') or [] if e.get('type') != 'exhaustion']
    if 'жаропонижающее средство' in name and 'мороз' in name:
        result['direct'].pop('temperature_delta', None)
        result['effects'] = [e for e in result.get('effects') or [] if e.get('type') != 'temperature_control']
    if parsed['direct'].get('filter_install_action_points') is not None:
        result['direct'].pop('action_points_delta', None)
        result['modifiers'] = [m for m in result.get('modifiers') or [] if m.get('stat') != 'action_points']
        result['effects'] = [e for e in result.get('effects') or [] if not str(e.get('name') or '').startswith('ОД ')]
    if name == 'стимулятор воля':
        result['direct'].pop('exhaustion_delta', None)
        result['effects'] = [e for e in result.get('effects') or [] if e.get('type') != 'exhaustion']
    if 'полукровка' in name:
        result['direct'].pop('infection_multiplier',None)
        result['direct']['infection_delta']=-50
    if name=='морфин':
        result['effects']=[effect for effect in result.get('effects') or [] if effect.get('type')!='delayed_treatment']
    # Уточнения автора к официальному каталогу; пользовательские профили выше исключены.
    periodic_corrections = {
        'борщевик': ('intoxication_delta', {'intoxication', 'periodic_adjustment'}),
        'самопальный стимпак': ('intoxication_delta', {'intoxication', 'periodic_adjustment'}),
        'регенеративный стимулятор альфа': ('exhaustion_delta', {'exhaustion', 'temporary_exhaustion', 'periodic_adjustment'}),
        'регенеративный стимулятор бета': ('exhaustion_delta', {'exhaustion', 'temporary_exhaustion', 'periodic_adjustment'}),
        'препарат "радист"': ('exhaustion_delta', {'exhaustion', 'temporary_exhaustion', 'periodic_adjustment'}),
        'научный стимпак': ('radiation_delta', {'radiation', 'radiation_treatment'}),
        'стимулятор шумодав': (None, {'deafness'}),
    }
    correction = periodic_corrections.get(name.replace('«', '"').replace('»', '"'))
    if correction:
        field, kinds = correction
        if field:
            result['direct'].pop(field, None)
        result['effects'] = [effect for effect in result.get('effects') or [] if effect.get('type') not in kinds]
        if name == 'стимулятор шумодав':
            result['status_additions'] = [status for status in result.get('status_additions') or [] if status != 'deafness']
    if ('питательный стимулятор' in name and old_direct.get('pain_delta') == 3
            or name == 'стимулятор гора' and old_direct.get('pain_delta') == 1):
        result['direct']['pain_delta'] = -1
    if parsed['direct'].get('temporary_limb_health_moves'):
        result['direct'].pop('temporary_limb_health_minutes', None)
    if re.search(r'(?:\d+|одно)\s+(?:перемещени|передвижени)', description, re.I):
        result['direct']['duration_phase'] = 'movement_end'
    for field in ('effects','modifiers','status_additions','status_removals','requirements','notes'):
        result.setdefault(field, [])
        for entry in parsed.get(field) or []:
            if field == 'modifiers':
                duplicate = any(m.get('stat') == entry.get('stat') for m in result[field])
            elif field == 'effects':
                duplicate = any(e.get('type') == entry.get('type') and e.get('name') == entry.get('name')
                                for e in result[field])
            else:
                duplicate = entry in result[field]
            if not duplicate:
                result[field].append(deepcopy(entry))
    result, _ = correct_owl_profile(result, result['direct'], attributes['catalog_name'])
    result['catalog_rules_version'] = '2026-11-04-duration'
    return result


def _effect(effect_type: str, value: Any = 0, *, remaining: Optional[int] = None, tick: str = "manual", **extra: Any) -> Dict[str, Any]:
    payload = {
        "type": effect_type,
        "name": extra.pop("name", effect_type),
        "value": _to_float(value, 0),
        "tick": tick,
        "active": True,
    }
    if remaining is not None:
        payload["remaining"] = max(0, _to_int(remaining, 0))
    payload.update(extra)
    return payload


def parse_consumable_effects(description: str) -> Dict[str, Any]:
    text = _normalize(description)
    lower = text.lower()
    profile: Dict[str, Any] = {
        "direct": {},
        "effects": [],
        "modifiers": [],
        "status_removals": [],
        "status_additions": [],
        "requirements": [],
        "notes": [],
    }

    def add_effect(effect_type: str, value: Any = 0, **extra: Any) -> None:
        profile["effects"].append(_effect(effect_type, value, **extra))

    def add_modifier(stat: str, value: Any, remaining: Optional[int] = None, note: str = "") -> None:
        profile["modifiers"].append({
            "stat": stat,
            "value": _to_float(value, 0),
            "remaining": None if remaining is None else max(0, _to_int(remaining, 0)),
            "note": note,
        })

    def remove_statuses(*items: str) -> None:
        for item in items:
            if item and item not in profile["status_removals"]:
                profile["status_removals"].append(item)

    def add_summary_effect(effect_type: str, value: Any = 0, *, name: Optional[str] = None, remaining: Optional[int] = None, note: str = "") -> None:
        profile["effects"].append(_effect(effect_type, value, name=name or effect_type, remaining=remaining, source="direct", note=note))

    def add_summary_label(label: str, *, value: Any = 0, remaining: Optional[int] = None, note: str = "") -> None:
        add_summary_effect("generic", value, name=label, remaining=remaining, note=note)

    duration = _parse_duration(lower)
    if duration is not None and re.search(r'\d+\s+перемещени[еяй]', lower):
        profile["direct"]["duration_phase"] = "movement_end"

    water_fraction_match = re.search(
        r"(\d+)\s*/\s*(\d+)[^.]{0,48}\u0431\u0443\u0442\u044b\u043b\u043a[^\s]*\s+"
        r"\u0432\u043e\u0434\u044b",
        lower,
    )
    if water_fraction_match:
        numerator = _to_float(water_fraction_match.group(1), 0)
        denominator = _to_float(water_fraction_match.group(2), 0)
        if numerator > 0 and denominator > 0:
            profile["direct"]["requires_water_fraction"] = numerator / denominator
            sentence_start = max(0, lower.rfind(".", 0, water_fraction_match.start()) + 1)
            sentence_end = lower.find(".", water_fraction_match.end())
            sentence = lower[sentence_start:sentence_end if sentence_end >= 0 else len(lower)]
            if "\u0430\u043b\u043a\u043e\u0433\u043e\u043b" in sentence:
                profile["direct"]["water_or_alcohol"] = True

    # HP and regeneration
    hp = _parse_signed(lower, r"(?P<sign>[+-])(?P<value>\d+(?:[.,]\d+)?)\s*хп(?:\s*в\s*ход)?")
    if hp is not None:
        if "в ход" in lower:
            duration = _parse_duration(lower)
            profile["direct"]["hp_per_turn"] = hp
            add_effect("regeneration", abs(hp), remaining=duration)
        else:
            profile["direct"]["hp"] = hp
            if hp > 0:
                add_effect("heal", abs(hp))
            elif hp < 0:
                add_effect("damage", abs(hp))

    # Numeric modifiers
    mapping = [
        ("radiation", r"(?P<sign>[+-])(?P<value>\d+(?:[.,]\d+)?(?:/\d+)?)\s*радиац"),
        ("intoxication", r"(?P<sign>[+-])(?P<value>\d+(?:[.,]\d+)?)\s*опьянен"),
        ("stress", r"(?P<sign>[+-])(?P<value>\d+(?:[.,]\d+)?(?:/\d+)?)\s*(?:уров(?:ень|ня|ней|ни)\s*)?стресса"),
        ("exhaustion", r"(?P<sign>[+-])(?P<value>\d+(?:[.,]\d+)?(?:/\d+)?)\s*(?:уров(?:ень|ня|ней|ни)\s*)?истощени"),
        ("pain", r"(?P<sign>[+-])(?P<value>\d+(?:[.,]\d+)?)\s*(?:уров(?:ень|ня|ней|ни)\s*)?боли"),
        ("action_points", r"(?P<sign>[+-])(?P<value>\d+(?:[.,]\d+)?)\s*од\b"),
        ("movement_points", r"(?P<sign>[+-])(?P<value>\d+(?:[.,]\d+)?)\s*(?:к\s*)?(?:перемещени|движени)"),
        ("strength", r"(?P<sign>[+-])(?P<value>\d+(?:[.,]\d+)?)\s*(?:к\s*)?сил[ые]"),
        ("agility", r"(?P<sign>[+-])(?P<value>\d+(?:[.,]\d+)?)\s*(?:к\s*)?ловк"),
        ("accuracy", r"(?P<sign>[+-])(?P<value>\d+(?:[.,]\d+)?)\s*(?:к\s*)?точност"),
        ("weight", r"(?P<sign>[+-])(?P<value>\d+(?:[.,]\d+)?)\s*(?:к\s*)?вес"),
        ("temperature", r"(?P<sign>[+-])(?P<value>\d+(?:[.,]\d+)?)\s*(?:градус|температур)"),
    ]
    for stat, pattern in mapping:
        value = _parse_signed(_without_movement_duration(lower) if stat == 'movement_points' else lower, pattern)
        if value is None:
            continue
        profile["direct"][f"{stat}_delta"] = value
        if stat in {"strength", "agility", "accuracy", "weight", "temperature"}:
            add_modifier(stat, value, remaining=duration)
        elif stat == "action_points":
            add_modifier("action_points", value, remaining=duration)
        elif stat == "movement_points":
            add_modifier("movement_points", value, remaining=duration)
        else:
            add_effect(stat, value)

    # Durations and delays
    if duration is not None:
        profile["direct"]["duration"] = duration
    if "срабатывает через" in lower:
        profile["direct"]["delay"] = _parse_duration(lower)

    if "ампула" in lower or "капельница" in lower:
        profile["direct"]["medical_difficulty"] = 4
        profile["direct"]["application_form"] = "injectable"

    apply_catalog_rules(profile, lower, EARLY_CATALOG_RULES)

    if "тяжесть кровотечений" in lower and "1/3 бутылки воды" in lower:
        profile["direct"]["bleeding_modifier_delta"] = -2
        profile["direct"]["bleeding_modifier_scope"] = "combat"
        profile["direct"]["requires_water_fraction"] = 1 / 3
        profile["direct"]["exhaustion_if_no_water"] = 1
        profile["notes"].append("hematogen_bleeding_reduction")
    if re.search(r"выход\w* из болевого шока", lower) and "не тратится" in lower:
        profile["direct"]["will_shock_bonus"] = 2
        profile["direct"]["will_shock_advantage"] = True
        profile["direct"]["not_consumed"] = True
        profile["notes"].append("ammonia_shock_bonus")
    if "восстановить конечность" in lower and "1 здоровье" in lower and "4 раунда" in lower:
        profile["direct"]["fracture_splint"] = True
        profile["direct"]["fracture_restore_health"] = 1
        profile["direct"]["fracture_duration_turns"] = 4
        profile["notes"].append("splint")
    if "останов" in lower and "заражени" in lower:
        percent_match = re.search(r"(\d+(?:[.,]\d+)?)\s*%\s*прогресса заражения крови", lower)
        if percent_match:
            profile["direct"]["infection_delta"] = -_to_float(percent_match.group(1), 0)
        if "заражение крови на сутки" in lower or "заражение крови на 3 дня" in lower:
            profile["direct"]["infection_block"] = True

    uses_match = re.search(r"(\d+)\s*использован", lower)
    if uses_match:
        profile["direct"]["uses"] = _to_int(uses_match.group(1), 1)

    action_points_match = re.search(r"время использования\s*[-–—:]\s*(\d+)\s*од", lower)
    if action_points_match:
        profile["direct"]["action_points_cost"] = _to_int(action_points_match.group(1), 1)

    pain_increase_match = re.search(r"усиливает боль на\s*(\d+)\s*уров", lower)
    if pain_increase_match:
        profile["direct"]["pain_delta"] = _to_int(pain_increase_match.group(1), 0)

    # Visible summaries for direct-only effects so they are not empty in the UI
    direct = profile["direct"]
    if direct.get("hp") is not None:
        hp_value = _to_float(direct["hp"], 0)
        if hp_value > 0:
            add_summary_label(f"Лечение +{abs(hp_value):g}")
        elif hp_value < 0:
            add_summary_label(f"Урон {abs(hp_value):g}")
    if direct.get("hp_per_turn") is not None:
        hp_turn = _to_float(direct["hp_per_turn"], 0)
        add_summary_label(f"Регенерация {abs(hp_turn):g}/ход", remaining=direct.get("duration"))
    for key, label in [
        ("radiation_delta", "Радиация"),
        ("intoxication_delta", "Опьянение"),
        ("stress_delta", "Стресс"),
        ("stress_in_combat_delta", "Стресс в бою"),
        ("stress_safe_delta", "Стресс вне боя"),
        ("exhaustion_delta", "Истощение"),
        ("pain_delta", "Боль"),
        ("infection_delta", "Заражение"),
        ("psy_delta", "Пси-состояние"),
        ("temperature_delta", "Температура"),
        ("strength_delta", "Сила"),
        ("agility_delta", "Ловкость"),
        ("accuracy_delta", "Точность"),
        ("weight_delta", "Вес"),
        ("will_delta", "Воля"),
        ("psy_defense_delta", "Пси-защита"),
        ("action_points_delta", "ОД"),
        ("movement_points_delta", "ОП"),
        ("med_bonus", "Бонус медикамента"),
    ]:
        if direct.get(key) is not None:
            value = direct[key]
            if isinstance(value, (int, float)):
                add_summary_label(f"{label} {value:g}")
            else:
                add_summary_label(label)
    if direct.get("bleeding_modifier_delta") is not None:
        add_summary_label(f"Тяжесть кровотечений {direct['bleeding_modifier_delta']:g}")
    if direct.get("bleeding_stop_light_cost") is not None or direct.get("bleeding_stop_medium_cost") is not None:
        if direct.get("bleeding_stop_medium_cost") is not None:
            add_summary_label("Останавливает кровотечение до среднего")
        else:
            add_summary_label("Останавливает кровотечение")
    if direct.get("fracture_splint"):
        add_summary_label("Шина")
    if direct.get("blood_type_test"):
        add_summary_label("Определение группы крови")
    if direct.get("rest_heal_multiplier") is not None:
        add_summary_label(f"Отдых x{direct['rest_heal_multiplier']:g}")
    if direct.get("nutrition") is not None:
        add_summary_label(f"Питание +{direct['nutrition']:g}")
    if direct.get("filter_charges") is not None:
        # Цена замены фильтра — операция с модулем, а не штраф к ОД персонажа.
        replacement = re.search(r'замена фильтра[^.]*?[-−–—]\s*(\d+)\s*од', lower)
        if replacement:
            direct['filter_install_action_points'] = int(replacement.group(1))
            direct.pop('action_points_delta', None)
            profile['modifiers'] = [m for m in profile['modifiers'] if m.get('stat') != 'action_points']
            profile['effects'] = [e for e in profile['effects'] if not str(e.get('name') or '').startswith('ОД ')]
        add_summary_label(f"Фильтр {direct['filter_charges']:g}")
    if direct.get("duration") is not None:
        add_summary_label(f"Длительность {direct['duration']:g}")
    if direct.get("delay") is not None:
        add_summary_label(f"Задержка {direct['delay']:g}")
    if direct.get("uses") is not None:
        add_summary_label(f"Использований {direct['uses']:g}")
    if direct.get("not_consumed"):
        add_summary_label("Не расходуется")
    if direct.get("requires_water_fraction") is not None:
        fraction = direct["requires_water_fraction"]
        if isinstance(fraction, (int, float)) and fraction > 0:
            add_summary_label(f"Требует воды {fraction:g}")
    if direct.get("requires_gas_mask"):
        add_summary_label("Требует противогаз")
    if direct.get("medical_difficulty") is not None or direct.get("application_form") is not None:
        add_summary_label("Мед. применение")
    if direct.get("addiction_block_hours") is not None:
        add_summary_label(f"Блок зависимостей {direct['addiction_block_hours']:g} ч")
    if direct.get("pain_block_turns") is not None or direct.get("stress_block_turns") is not None:
        add_summary_label("Блок боли/стресса")
    if direct.get("sleep_block_hours") is not None:
        add_summary_label(f"Блок сна {direct['sleep_block_hours']:g} ч")
    if direct.get("will_shock_bonus") is not None:
        add_summary_label(f"Выход из шока +{direct['will_shock_bonus']:g}")
    if direct.get("will_shock_advantage"):
        add_summary_label("Преимущество на шок")
    if direct.get("fracture_restore_health") is not None or direct.get("fracture_duration_turns") is not None:
        add_summary_label("Шина / перелом")
    if direct.get("infection_block"):
        add_summary_label("Блок заражения")
    if not profile["effects"] and profile["direct"]:
        for key, value in direct.items():
            if value is None:
                continue
            if key in {"uses", "duration", "delay", "not_consumed", "requires_gas_mask", "requires_water_fraction", "infection_block"}:
                continue
            if isinstance(value, bool):
                if value:
                    add_summary_label(key.replace("_", " ").title())
            elif isinstance(value, (int, float)):
                add_summary_label(f"{key.replace('_', ' ')} {value:g}")
            else:
                add_summary_label(f"{key.replace('_', ' ')}")

    # Statuses and special cases
    if "снимает 75% прогресса заражения крови" in lower:
        profile["direct"]["infection_delta"] = -75
    elif "снимает 50% прогресса заражения крови" in lower:
        profile["direct"]["infection_delta"] = -50
    elif "снимает 25% прогресса заражения крови" in lower:
        profile["direct"]["infection_delta"] = -25
    elif "снимает 5% прогресса заражения крови" in lower:
        profile["direct"]["infection_delta"] = -5

    if "уменьшает стадию кровопотери каждый раунд" in lower:
        add_effect("blood_recovery", 1, remaining=duration, tick="turn_end")
    if "останавливает ухудшение стадий кровопотери" in lower:
        add_effect("bleeding_block", 1, remaining=duration, note="freeze_blood_stage")
    if "блокирует появление новых кровотечений" in lower:
        add_effect("bleeding_block", 1, remaining=duration, note="block_new_bleeds")

    if "останавливает слабое кровотечение" in lower:
        remove_statuses("bleeding", "bleeding_external_light")
    if "останавливает среднее кровотечение" in lower:
        remove_statuses("bleeding", "bleeding_external_light", "bleeding_external_medium")
    if "останавливает сильное кровотечение" in lower:
        remove_statuses("bleeding", "bleeding_external_light", "bleeding_external_medium", "bleeding_external_severe")
    if "останавливает все кровотечения" in lower:
        remove_statuses(
            "bleeding",
            "bleeding_external_light",
            "bleeding_external_medium",
            "bleeding_external_severe",
            "bleeding_external_extreme",
            "bleeding_internal_light",
            "bleeding_internal_medium",
            "bleeding_internal_severe",
            "bleeding_internal_extreme",
        )
    if "останавливает слабое внутреннее кровотечение" in lower:
        remove_statuses("bleeding_internal_light")
    if "останавливает среднее внутреннее кровотечение" in lower:
        remove_statuses("bleeding_internal_light", "bleeding_internal_medium")
    if "останавливает сильное внутреннее кровотечение" in lower:
        remove_statuses("bleeding_internal_light", "bleeding_internal_medium", "bleeding_internal_severe")

    if 'состояние "сна"' in lower:
        profile["status_additions"].append("sleep")
    if "теряете слух" in lower:
        profile["status_additions"].append("deafness")
    if "убирает одышку" in lower:
        profile["notes"].append("breathless_clear")

    multiplier_match = re.search(r"(?:прокачка|бонус|усиление)\s+(сил[ыя]|ловк[а-я]*|точност[ьи]|выносливост[ьи])\s*x\s*(\d+(?:[.,]\d+)?)", lower)
    if multiplier_match:
        stat_name = multiplier_match.group(1)
        multiplier = _to_float(multiplier_match.group(2), 1)
        stat_map = {
            "силы": "strength_multiplier",
            "сила": "strength_multiplier",
            "ловкости": "agility_multiplier",
            "ловкость": "agility_multiplier",
            "точности": "accuracy_multiplier",
            "точность": "accuracy_multiplier",
            "выносливости": "endurance_multiplier",
        }
        profile["modifiers"].append({
            "stat": stat_map.get(stat_name, "generic_multiplier"),
            "value": multiplier,
            "remaining": duration,
            "note": "multiplier",
        })

    if "вдвое больше хп" in lower:
        profile["modifiers"].append({
            "stat": "rest_heal_multiplier",
            "value": 2,
            "remaining": duration,
            "note": "rest_bonus",
        })

    for fragment in [
        "нужна 1/3 бутылки воды",
        "нужна 2/3 бутылки воды",
        "нужны спички",
        "нужна 1 порция любого алкоголя",
        "требуется капельница",
        "не считается едой",
        "считается едой и водой",
        "считается водой",
        "материал для крафта",
        "лимит 1",
        "не предназачен для внутреннего использования",
    ]:
        if fragment in lower:
            profile["requirements"].append(fragment)

    if "бонус медикамента" in lower:
        bonus_match = re.search(r"бонус медикамента\s*([+-]?\d+)", lower)
        if bonus_match:
            profile["direct"]["med_bonus"] = _to_int(bonus_match.group(1), 0)

    if "без него рад. защита противогаза не работает" in lower:
        profile["requirements"].append("requires_gas_mask")

    _apply_canonical_consumable_rules(profile, lower)
    if (profile['direct'].get('affects_all_limbs')
            and re.search(r'(?:\d+|одно)\s+(?:перемещени|передвижени)', lower)):
        profile['direct'].pop('temporary_limb_health_minutes', None)
        profile['direct']['temporary_limb_health_moves'] = _parse_duration(lower) or 1
    return with_darkness_modifiers(profile, text)


def _apply_canonical_consumable_rules(profile: Dict[str, Any], lower: str) -> None:
    """Apply rules whose wording is too contextual for the generic parser."""
    direct = profile["direct"]
    if re.search(r'(?:\d+|одно)\s+(?:перемещени|передвижени)', lower):
        direct["duration_phase"] = "movement_end"

    def effect(effect_type: str, value: Any = 0, **extra: Any) -> Dict[str, Any]:
        payload = _effect(effect_type, value, **extra)
        profile["effects"].append(payload)
        return payload

    def remove_effect_types(*types: str) -> None:
        blocked = set(types)
        profile["effects"] = [item for item in profile["effects"] if item.get("type") not in blocked]

    regeneration_match = re.search(r"\+(\d+(?:[.,]\d+)?)\s*здоровья\s+в\s+ход", lower)
    if regeneration_match:
        regeneration = _to_float(regeneration_match.group(1), 0)
        duration_match = re.search(r"действует\s+(\d+)\s*ход", lower)
        regeneration_duration = _to_int(duration_match.group(1), 1) if duration_match else direct.get("duration")
        direct["hp_per_turn"] = regeneration
        effect("regeneration", regeneration, name=f"Регенерация {regeneration:g}/ход",
               remaining=regeneration_duration, tick="turn_end")

    apply_catalog_rules(profile, lower, EVERYDAY_CATALOG_RULES)

    apply_catalog_rules(profile, lower, BLEEDING_CATALOG_RULES)
    if direct.get("applications"):
        direct["requires_injury"] = True
        profile["status_removals"] = [
            status for status in profile["status_removals"]
            if not str(status).startswith("bleeding")
        ]

    apply_catalog_rules(profile, lower, MEDICAL_CATALOG_RULES)

    # Legacy catalogue overrides are data; new profiles can express these effects directly.
    apply_catalog_rules(profile, lower, LATE_CATALOG_RULES)
    from app.services.consumable_catalog_rules import correct_owl_profile
    corrected, _ = correct_owl_profile(profile, direct, lower)
    if corrected is not profile:
        profile.clear()
        profile.update(corrected)
        direct = profile['direct']
    apply_catalog_rules(profile, lower, SURGERY_PRE_CATALOG_RULES)
    surgery_match = re.search(r"восстанавливает (?:часть тела|утерянный орган или искореженную конечность).*?она имеет\s+(\d+)\s+здоров", lower)
    if surgery_match:
        direct["restore_limb_health"] = _to_int(surgery_match.group(1), 1)
        action_match = re.search(r"время использования\s*-\s*(\d+)\s*од", lower)
        direct["action_points_cost"] = _to_int(action_match.group(1), 1) if action_match else 1
        direct["restore_missing_part"] = True
    apply_catalog_rules(profile, lower, SURGERY_POST_CATALOG_RULES)

    visible_fields = {
        "radiation_delta": "Радиация", "intoxication_delta": "Опьянение",
        "exhaustion_delta": "Истощение", "stress_delta": "Стресс", "pain_delta": "Боль",
        "action_points_delta": "ОД", "weight_delta": "Штраф к весу",
        "infection_delta": "Заражение", "temperature_delta": "Температура",
        "strength_delta": "Сила", "accuracy_delta": "Точность", "will_delta": "Воля",
        "psy_delta": "Пси-состояние", "rest_heal_multiplier": "Лечение на отдыхе x",
        "nutrition": "Закрывает потребность в еде", "bleeding_modifier_delta": "Тяжесть кровотечений",
        "hp": "Здоровье", "hp_per_turn": "Здоровье за ход", "restore_limb_health": "Здоровье части тела",
        "action_points_cost": "Стоимость ОД", "fracture_splint": "Лечение перелома",
        "cure_fracture": "Излечивает перелом", "close_area_bleeding": "Закрывает кровотечения в части тела",
        "blood_collection": "Создаёт пакет крови", "stop_all_bleeding": "Останавливает все кровотечения",
        "satisfy_sleep": "Закрывает потребность во сне", "radiation_filter_percent": "Защита от входящей радиации %",
    }
    existing_names = {str(item.get("name") or "") for item in profile["effects"]}
    for key, label in visible_fields.items():
        if key not in direct:
            continue
        value = direct[key]
        name = label if isinstance(value, bool) else f"{label} {value:g}" if isinstance(value, (int, float)) else label
        if name not in existing_names:
            profile["effects"].append(_effect("generic", value if isinstance(value, (int, float)) else 0,
                                              name=name, source="direct", tick="manual"))
            existing_names.add(name)
    if direct.get("applications") and "Выбор конкретной травмы" not in existing_names:
        profile["effects"].append(_effect("generic", 0, name="Выбор конкретной травмы", source="direct", tick="manual"))
    elif direct.get("requires_injury") and "Требует выбрать травму" not in existing_names:
        profile["effects"].append(_effect("generic", 0, name="Требует выбрать травму", source="direct", tick="manual"))
