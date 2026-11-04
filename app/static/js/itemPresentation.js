import { effectSummary, effectDurationLabel, effectConsequences, getEffectTypeOptions } from './effects.js';
import { profileFieldLabel } from './consumableProfileEditor.js';

const labels = {
    damage:'Урон', accuracy:'Точность', penetration:'Бронепробитие', armor_piercing:'Бронепробитие',
    range:'Дальность', ergonomics:'Эргономика', caliber:'Калибр', capacity:'Вместимость',
    max_durability:'Максимальная прочность', movement_penalty:'Штраф перемещения',
    accuracy_penalty:'Штраф точности', ergonomics_penalty:'Штраф эргономики',
    charisma_bonus:'Бонус Харизмы', charismaBonus:'Бонус Харизмы', uses:'Использований',
    noise:'Шум', required_strength:'Требуемая Сила', deafeningCoef:'Коэффициент оглушения',
    min_strength:'Требуемая Сила', magazine_size:'Ёмкость магазина',
    armor_class:'Класс брони', protection_class:'Класс защиты',
    strength:'Сила', agility:'Ловкость', will:'Воля', psy_defense:'Пси-защита',
    movement_points:'ОП', action_points:'ОД',
    noise_absorption:'Поглощение шума', blindnessResistance:'Защита от ослепления',
};
const protectionLabels = {physical:'Физическая',chemical:'Химическая',thermal:'Термическая',
    electric:'Электрическая',radiation:'Радиационная',psy:'Пси'};
const statLabels = {weight:'Штраф нагрузки'};
const categories = {weapon:'Огнестрельное оружие',melee_weapon:'Холодное оружие',ammo:'Патроны',
    consumable:'Расходник',armor:'Броня',helmet:'Шлем',gas_mask:'Противогаз',artifact:'Артефакт',
    backpack:'Рюкзак',vest:'Разгрузка',belt:'Пояс',misc:'Предмет',magazine:'Магазин',
    weapon_module:'Оружейный модуль',gas_mask_module:'Модуль противогаза',detector:'Детектор',jewelry:'Аксессуар'};
const compact = text => String(text || '').replace(/\s+/g, ' ').trim();
const key = text => compact(text).toLocaleLowerCase('ru').replace(/[−–—]/g,'-').replace(/[\s.,:;•%()]/g, '');
const percent = value => `${Number(value) <= 1 && Number(value) > 0 ? Math.round(Number(value) * 100) : value}%`;
const readable = value => typeof value === 'string' && !/^[a-z][a-z0-9_:.-]*$/.test(value);
const concepts = {
    hp:'(?:хп|оз|здоров)', pain_delta:'бол', stress_delta:'стресс', exhaustion_delta:'истощ',
    radiation_delta:'радиац', intoxication_delta:'опьян', infection_delta:'заражен',
    strength_delta:'сил', agility_delta:'ловк', accuracy_delta:'точност', will_delta:'вол',
    psy_delta:'пси', nutrition:'питани', hydration:'вод',
};
const subcategories={scope:'Прицел',barrel:'Ствол',handguard:'Цевьё',filter:'Фильтр',nvg:'ПНВ',battery:'Батарея'};
function normalizedRuleText(description) {
    return description.toLowerCase().replace(/[−–—]/g, '-').replace(/(\d),(\d)/g, '$1.$2')
        .replace(/([+-]?\d+)\s*\/\s*(\d+)/g, (text,a,b) => Number(b) ? String(Number(a)/Number(b)) : text);
}
function rulePattern(field, value) {
    const concept = concepts[field];
    if (!concept || !Number.isFinite(Number(value))) return null;
    const number = String(Math.abs(Number(value))).replace('.', '\\.');
    const sign = Number(value) < 0 ? '-' : '\\+';
    return new RegExp(`(?:${sign}${number}\\s*(?:уров[а-яё]*\\s*)?${concept}[а-яё]*|${concept}[а-яё]*\\s*[:=]?\\s*${sign}${number}(?![\\d.]))`, 'giu');
}
function describedRule(description, field, value) {
    return rulePattern(field,value)?.test(normalizedRuleText(description)) || false;
}
function numericDescriptionCovered(description, direct) {
    let rest = normalizedRuleText(description);
    for (const [field,value] of Object.entries(direct || {})) {
        const pattern = rulePattern(field,value);
        if (pattern) rest = rest.replace(pattern,'');
    }
    if (direct?.uses) rest = rest.replace(new RegExp(`\\b${Number(direct.uses)}\\s*использован[а-яё]*`, 'giu'),'');
    return description && !rest.replace(/[\s.,;:]/g,'');
}
function repeatsDirectEffect(effect,direct) {
    const field = {healing:'hp',pain:'pain_delta',stress:'stress_delta',radiation:'radiation_delta',
        intoxication:'intoxication_delta',exhaustion:'exhaustion_delta'}[effect?.type];
    return field && direct?.[field] != null && Number(direct[field]) === Number(effect.value)
        && (effect.tick || 'manual') === 'manual' && effect.remaining == null
        && !effect.note && !effect.onExpire && !effect.on_expire && !effect.adjustments;
}

export function itemTooltipModel(template = {}, item = {}) {
    const attrs = {...template.attributes, ...item.attributes};
    const currentProfile = template.attributes?.consumable;
    const oldImportedCopy = currentProfile?.catalog_rules_version && !item.attributes?.consumable?.editor_version
        && template.attributes?.catalog_name && template.attributes.catalog_name === item.attributes?.catalog_name
        && template.attributes.raw_description === item.attributes.raw_description
        && !item.attributes?.consumable?.catalog_rules_version;
    const profile = (oldImportedCopy ? currentProfile : attrs.consumable) || {};
    const rawDescription = compact(item.description || template.description || attrs.raw_description || attrs.notes);
    const description = numericDescriptionCovered(rawDescription,profile.direct) ? '' : rawDescription;
    const metrics = [], rules = [], seen = new Set();
    const add = (list, text) => {
        text = compact(text);
        if (!text || seen.has(key(text))) return;
        seen.add(key(text)); list.push(text);
    };
    const category = item.category || template.category;
    const subcategory=template.subcategory || item.subcategory;
    add(metrics, subcategories[subcategory] || (/[а-яё]/i.test(subcategory || '')?subcategory:null)
        || template.categoryDisplay || categories[category] || 'Предмет');
    for (const [field, caption, unit] of [['weight','Вес',' кг'],['volume','Объём','']]) {
        const value = item[field] ?? template[field];
        if (value != null) add(metrics, `${caption}: ${value}${unit}`);
    }
    for (const [field, caption] of Object.entries(labels)) {
        const value = item[field] ?? attrs[field];
        if (value == null || value === '' || value === false || typeof value === 'object') continue;
        if (['uses','max_durability'].includes(field) && (field === 'uses' ? item.uses : item.durability) != null) continue;
        add(metrics, `${caption}: ${['penetration','armor_piercing'].includes(field) ? percent(value) : value}`);
    }
    if (item.durability != null) add(metrics, `Прочность: ${item.durability}${item.maxDurability != null ? ` / ${item.maxDurability}` : attrs.max_durability != null ? ` / ${attrs.max_durability}` : ''}`);
    if (item.uses != null) add(metrics, `Использований: ${item.uses}${item.maxUses != null ? ` / ${item.maxUses}` : ''}`);
    if (item.lightPower?.uses_left!=null) add(metrics,`Батарейка: ${item.lightPower.uses_left} / 10`);
    const water = profile.direct?.water_source || /^(вода|water)$/i.test(item.name || template.name || '');
    if (water) {
        const container = /канистр|кастрюл|ведро|бочк/i.test(item.name || template.name || '');
        const capacity = Number(attrs.water_litres ?? item.water_litres ?? (container?0:1));
        const maximum = Math.max(1, Number(item.maxUses ?? attrs.uses ?? profile.direct?.uses ?? 1));
        const remaining = Number(item.uses ?? maximum);
        if (capacity > 0) add(metrics, `Запас воды: ${Number((capacity * remaining / maximum).toFixed(2)).toLocaleString('ru')} л`);
    }
    if (item.ammo != null && typeof item.ammo !== 'object') add(metrics, `Патроны: ${item.ammo}`);
    if (attrs.bloodTypeKnown != null || attrs.blood_packet || profile.direct?.blood_packet) {
        const group = Number(attrs.bloodType ?? attrs.blood_type);
        add(metrics, `Группа крови: ${attrs.bloodTypeKnown && [1,2,3,4].includes(group) ? `${group} группа` : 'неизвестна'}`);
    }
    const protection = item.protection || attrs.protection || template.protection || {};
    const gasMask = category === 'gas_mask' || attrs.requires_filter || attrs.is_gas_mask;
    const emptyFilter = gasMask && Array.isArray(item.installedModules) && !item.installedModules.some(module => {
        const attributes = module.attributes || {};
        return (module.slotType === 'filter' || attributes.slot_type === 'filter' || module.category === 'gas_mask_module')
            && Number(module.durability ?? attributes.durability ?? attributes.filter_charges ?? 0) > 0;
    });
    for (const [field, caption] of Object.entries(protectionLabels)) {
        if (protection[field] != null) add(metrics, field === 'radiation' && emptyFilter
            ? `${caption} защита: 0% · нет действующего фильтра (базовая ${percent(protection[field])})`
            : `${caption} защита: ${percent(protection[field])}`);
    }
    // Название/описание может быть коротким, а профиль — дополнен правилами каталога.
    if (description && !profile.editor_version) add(rules, description);
        for (const [field, value] of Object.entries(profile.direct || {})) {
            if (field === 'water_source' && water) continue;
            if (field === 'uses' && (item.uses != null || attrs.uses != null)) continue;
            const caption = profileFieldLabel(field);
            if (!caption || value == null || value === false || typeof value === 'object') continue;
            if (!profile.editor_version && describedRule(description, field, value)) continue;
            if (field === 'duration') {
                add(rules, `Длительность: ${effectDurationLabel({remaining:value,tick:profile.direct.duration_phase || 'turn_end'}).replace(/^осталось /, '')}`);
                continue;
            }
            if (typeof value === 'string' && !readable(value)) continue;
            add(rules, typeof value === 'boolean' ? caption : `${caption}: ${field.endsWith('_delta') && Number(value) > 0 ? '+' : ''}${value}`);
        }
        for (const effect of [...(profile.effects || []), ...(attrs.effects || [])]) {
            if (effect?.source !== 'direct' && !repeatsDirectEffect(effect,profile.direct)) {
                add(rules, effectSummary(effect));
                effectConsequences(effect).forEach(text=>add(rules,text));
            }
        }
        for (const modifier of profile.modifiers || []) {
            const caption = statLabels[modifier.stat] || labels[modifier.stat] || profileFieldLabel(modifier.stat);
            if (caption) add(rules, effectSummary({...modifier, type:'custom', name:caption}));
        }
        if (description && !rules.some(text => key(text) === key(description))) add(rules, description);
        for (const [field, caption] of [['status_removals','Снимает'],['status_additions','Добавляет']]) {
            for (const value of profile[field] || []) {
                const text = getEffectTypeOptions().find(option => option.value === value)?.label;
                if (text || readable(value)) add(rules, `${caption}: ${text || value}`);
            }
        }
    for (const [field, caption] of [['positive_effect','Положительное свойство'],['negative_effect','Отрицательное свойство'],['special_property','Особое свойство']]) {
        const text = compact(attrs[field]);
        if (text && text.toLowerCase() !== 'нет' && !key(description).includes(key(text))) add(rules, `${caption}: ${text}`);
    }
    for (const module of [...(item.installedModules || []), ...(item.modifications || [])]) {
        if (module?.name) add(rules, `Установлено: ${module.name}`);
    }
    return {name:item.name || item.model || template.name || 'Предмет', metrics, rules};
}

export function tooltipPosition(rect, width, height, viewport) {
    const gap = 12, edge = 8;
    const boundedWidth = Math.min(width, viewport.width - edge * 2);
    let left, top, maxHeight = viewport.height - edge * 2;
    if (rect.right + gap + boundedWidth <= viewport.width - edge) {
        left = rect.right + gap; top = rect.top;
    } else if (rect.left - gap - boundedWidth >= edge) {
        left = rect.left - gap - boundedWidth; top = rect.top;
    } else {
        left = Math.max(edge, Math.min(rect.left, viewport.width - boundedWidth - edge));
        const below = viewport.height - rect.bottom - gap - edge, above = rect.top - gap - edge;
        if (below >= Math.min(height, 180) || below >= above) {
            top = rect.bottom + gap; maxHeight = Math.max(40, below);
        } else {
            maxHeight = Math.max(40, above); top = rect.top - gap - Math.min(height, maxHeight);
        }
    }
    top = Math.max(edge, Math.min(top, viewport.height - Math.min(height, maxHeight) - edge));
    return {left, top, maxHeight};
}
