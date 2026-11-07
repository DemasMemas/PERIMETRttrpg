// Form controls for editing the same structured profile used by catalogue consumables.
const LABELS = {
    direct: 'Немедленные и специальные правила', effects: 'Действующие и отложенные эффекты',
    modifiers: 'Временные модификаторы', addiction: 'Зависимость',
    status_removals: 'Снимаемые состояния', status_additions: 'Добавляемые состояния',
    requirements: 'Требования', notes: 'Примечания каталога',
    hp: 'ОЗ', hp_max_delta: 'Максимум ОЗ', hp_per_turn: 'ОЗ за ход',
    radiation_delta: 'Радиация', radiation_filter_percent: 'Защита от входящей радиации, %',
    radiation_filter_capacity: 'Ёмкость радиационного фильтра', radiation_reduction_ceiling: 'Предел снижения радиации',
    stress_delta: 'Стресс', stress_advantage: 'Преимущество против проявления стресса',
    stress_attack_block_chance: 'Шанс не получить стресс от атаки, %',
    stress_in_combat_delta: 'Стресс в бою', stress_safe_delta: 'Стресс вне боя',
    pain_delta: 'Боль', pain_block_turns: 'Блок боли, раундов',
    exhaustion_delta: 'Истощение', intoxication_delta: 'Опьянение',
    infection_delta: 'Заражение', temperature_delta: 'Температура', psy_delta: 'Пси-состояние',
    duration: 'Длительность', duration_phase: 'Когда уменьшается длительность',
    remaining: 'Осталось', tick: 'Событие/фаза', time_unit: 'Единица времени',
    value: 'Величина', type: 'Тип эффекта', name: 'Название эффекта',
    adjustments: 'Изменения при срабатывании', onExpire: 'Изменения по окончании',
    on_expire: 'Изменения по окончании', activate_effects: 'Эффекты после задержки',
    field: 'Состояние', delta: 'Изменение', min: 'Минимум', max: 'Максимум',
    stat: 'Характеристика', action_points_cost: 'Стоимость применения, ОД',
    treatment_action_points: 'Стоимость лечения, ОД', medical_difficulty: 'Сложность Медицины',
    application_form: 'Форма применения', applications: 'Варианты лечения',
    bleeding_stop_light_cost: 'Зарядов на слабое кровотечение',
    bleeding_stop_medium_cost: 'Зарядов на среднее кровотечение',
    bleeding_stop_type: 'Тип кровотечения', blood_collection: 'Забор крови',
    blood_type_test: 'Определение группы крови', filter_charges: 'Заряды фильтра',
    requires_gas_mask: 'Требует противогаз', requires_fire: 'Требует огонь',
    requires_water_fraction: 'Требует воды, доля бутылки', water_or_alcohol: 'Можно заменить алкоголем',
    requires_items: 'Требуемые предметы', quantity: 'Количество',
    water_source: 'Можно использовать как воду', is_alcohol: 'Считается алкоголем',
    blood_packet: 'Это пакет крови',
    satisfy_water: 'Утоляет жажду', satisfy_food: 'Утоляет голод', satisfy_sleep: 'Сон',
    nutrition: 'Питание', bleeding_modifier_scope: 'Область снижения кровотечения',
    key: 'Ключ зависимости', label: 'Название зависимости', chance: 'Шанс зависимости, %',
    multipliers: 'Множители повторных доз', kind: 'Вид',
    action_points_delta: 'Дополнительные ОД', action_points_duration: 'Длительность бонуса ОД',
    movement_points_delta: 'Дополнительное передвижение',
    affects_all_limbs: 'Влияет на все конечности', agility_delta: 'Ловкость',
    accuracy_delta: 'Точность', strength_delta: 'Сила', will_delta: 'Воля',
    weight_delta: 'Штраф от веса', psy_defense_delta: 'Пси-защита',
    bleeding_modifier_delta: 'Изменение тяжести кровотечений',
    blood_compatibility_required: 'Проверять совместимость крови',
    blood_stage_delta: 'Изменение кровопотери', blocked_pain_return_fraction: 'Доля вернувшейся боли',
    catastrophic_limb_surgery: 'Вид операции на конечности', clear_breathless: 'Снять одышку',
    close_area_bleeding: 'Закрыть кровотечение зоны', cure_fracture: 'Лечить перелом',
    deafness: 'Глухота', delay: 'Задержка', delayed_limb_treatment_minutes: 'Лечение конечности через, мин',
    exhaustion_if_no_water: 'Истощение без воды', exhaustion_on_expire: 'Истощение после окончания',
    external_use_only: 'Только наружное применение', fracture_duration_turns: 'Фиксация перелома, раундов',
    fracture_restore_health: 'Восстановить ОЗ при фиксации', fracture_splint: 'Шина для перелома',
    head_lethal: 'Смертельно для головы', infection_block_chance: 'Шанс остановить заражение, %',
    infection_block_days: 'Блок заражения, дней', invalid_limb_damage: 'Урон неправильной конечности',
    limb_only: 'Только конечность', limit_scope: 'Период ограничения',
    med_bonus: 'Бонус Медицины', minimum_limb_health: 'Минимум ОЗ конечности',
    not_consumed: 'Не расходуется', organ_toughness_multiplier: 'Живучесть органов, множитель',
    post_duration_hp_delta: 'ОЗ после окончания', post_duration_exhaustion_delta: 'Истощение после окончания',
    post_duration_pain_delta: 'Боль после окончания',
    infection_multiplier: 'Множитель прогресса заражения', temporary_limb_health_moves: 'Восстановление конечности: перемещений',
    post_duration_modifiers: 'Модификаторы после окончания', radiation_filter_capacity: 'Ёмкость фильтра',
    filter_install_action_points: 'Установка фильтра, ОД',
    requires_infusion_tool: 'Требует капельницу', requires_injury: 'Требует травму',
    requires_shock: 'Требует болевой шок', rest_heal_multiplier: 'Лечение на отдыхе, множитель',
    restore_full_body_part: 'Восстановить часть тела полностью',
    restore_limb_health: 'ОЗ восстановленной конечности', restore_missing_part: 'Восстановить утраченную часть',
    sleep_block_hours: 'Без сна, часов', sleep_duration: 'Сон, длительность',
    special_limb_treatment: 'Особое лечение конечности', stop_all_bleeding: 'Остановить все кровотечения',
    suppress_limb_trauma: 'Подавить травму конечности', surgical_kit: 'Хирургический набор',
    target_body_part: 'Выбор части тела', target_required: 'Нужна цель',
    temporary_limb_health_minutes: 'Временные ОЗ конечности, мин',
    temporary_limb_health_turns: 'Временные ОЗ конечности, раундов',
    tourniquet: 'Жгут', treatment_time_uses_medicine: 'Время зависит от Медицины',
    use_limit: 'Лимит применений', uses: 'Зарядов', vision_awareness_bonus: 'Бонус зрительной Внимательности',
    will_shock_advantage: 'Преимущество Воли против шока', will_shock_bonus: 'Бонус Воли против шока',
    withdrawal_check_difficulty_reduction: 'Снижение СЛ ломки',
    withdrawal_support_delay_minutes: 'Задержка поддержки ломки, мин',
    withdrawal_support_duration_minutes: 'Длительность поддержки ломки, мин',
    wound_treatment: 'Обработка раны', exclusive_group: 'Группа несовместимых препаратов',
    applications: 'Варианты применения', action_points: 'Стоимость варианта, ОД',
    max_stage: 'Максимальная стадия', internal: 'Внутреннее кровотечение',
    treated: 'Рана обработана', all: 'Все поражения', allow_weakening: 'Можно ослабить',
    item_uses: 'Зарядов на применение', medicine_bonus: 'Бонус Медицины',
    remaining_seconds: 'Осталось секунд', remaining_capacity: 'Остаток ёмкости',
    max_hours: 'Максимум часов', active: 'Активен', trigger: 'Условие срабатывания',
    source: 'Источник', note: 'Примечание', capacity: 'Ёмкость',
};

const clone = value => JSON.parse(JSON.stringify(value));
const label = key => LABELS[key] || String(key);
// Публичные подсказки не должны выводить неизвестные служебные ключи.
export const profileFieldLabel = key => LABELS[key] || null;

export function profileComponentOptions(template) {
    const profile = template?.attributes?.consumable || {};
    const options = [{ section: 'whole', index: null, label: 'Весь профиль' }];
    for (const key of Object.keys(profile.direct || {}).sort()) {
        options.push({ section: 'direct', index: key, label: `Правило: ${label(key)}` });
    }
    for (const [index, effect] of (profile.effects || []).entries()) {
        if (effect?.source === 'direct') continue;
        options.push({ section: 'effects', index, label: `Эффект: ${effect.name || effect.type}` });
    }
    for (const [index, modifier] of (profile.modifiers || []).entries()) {
        options.push({ section: 'modifiers', index, label: `Модификатор: ${label(modifier.stat)}` });
    }
    if ('addiction' in profile) options.push({ section: 'addiction', index: null, label: 'Зависимость' });
    for (const section of ['status_removals', 'status_additions', 'requirements']) {
        for (const [index, value] of (profile[section] || []).entries()) {
            options.push({ section, index, label: `${label(section)}: ${value}` });
        }
    }
    return options;
}

export function copyProfileComponent(target, template, component) {
    const source = template?.attributes?.consumable;
    if (!source || !component) return target;
    if (component.section === 'whole') {
        const copied = clone(source);
        copied.editor_version = 2;
        copied.effects = (copied.effects || []).filter(effect => effect?.source !== 'direct');
        return copied;
    }
    if (component.section === 'direct') {
        target.direct ||= {};
        target.direct[component.index] = clone(source.direct[component.index]);
    } else if (component.section === 'addiction') {
        target.addiction = clone(source.addiction);
    } else if (Array.isArray(source[component.section])) {
        target[component.section] ||= [];
        target[component.section].push(clone(source[component.section][component.index]));
    }
    target.editor_version = 2;
    return target;
}

export function renderConsumableProfileTree(root, profile) {
    root.replaceChildren();
    const redraw = () => renderConsumableProfileTree(root, profile);

    function appendNode(parent, key, value, holder, depth) {
        if (depth > 7 || key === 'editor_version') return;
        const row = document.createElement('div');
        row.className = 'consumable-profile-node';
        parent.appendChild(row);
        const title = document.createElement('label');
        title.textContent = Array.isArray(holder) ? `${Number(key) + 1}. ${value?.name || value?.type || ''}` : label(key);
        row.appendChild(title);
        if (value !== null && typeof value === 'object') {
            const details = document.createElement('details');
            details.open = depth <= 1;
            const summary = document.createElement('summary');
            summary.textContent = title.textContent;
            title.remove();
            details.appendChild(summary);
            row.appendChild(details);
            const entries = Array.isArray(value) ? value.entries() : Object.entries(value);
            for (const [childKey, childValue] of entries) appendNode(details, childKey, childValue, value, depth + 1);
        } else {
            const input = document.createElement('input');
            input.className = 'form-control';
            if (typeof value === 'boolean') {
                input.type = 'checkbox';
                input.checked = value;
                input.onchange = () => { holder[key] = input.checked; };
            } else {
                input.type = typeof value === 'number' ? 'number' : 'text';
                if (input.type === 'number') input.step = 'any';
                input.value = value ?? '';
                input.onchange = () => {
                    const next = input.type === 'number' ? Number(input.value) : input.value;
                    if (input.type !== 'number' || Number.isFinite(next)) holder[key] = next;
                };
            }
            row.appendChild(input);
        }
        if (depth > 0) {
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'btn btn-sm btn-danger';
            remove.textContent = '×';
            remove.title = `Удалить: ${label(key)}`;
            remove.onclick = () => { if (Array.isArray(holder)) holder.splice(Number(key), 1); else delete holder[key]; redraw(); };
            row.appendChild(remove);
        }
    }

    for (const section of ['direct', 'effects', 'modifiers', 'status_removals', 'status_additions', 'requirements', 'addiction', 'notes']) {
        if (section in profile) appendNode(root, section, profile[section], profile, 0);
    }
    const addRequirement = document.createElement('button');
    addRequirement.type = 'button';
    addRequirement.className = 'btn btn-sm btn-secondary';
    addRequirement.textContent = 'Добавить требуемый предмет';
    addRequirement.onclick = () => {
        profile.direct ||= {};
        profile.direct.requires_items ||= [];
        profile.direct.requires_items.push({ name: '', quantity: 1 });
        redraw();
    };
    root.appendChild(addRequirement);
}
