const EFFECT_TYPE_META = {
    burning: {label:'Горение',group:'injury'},
    concussion: {label:'Контузия',group:'injury'},
    // `generic` is a legacy/fallback record. New manual entries use `custom`.
    generic: { label: 'Неопределённый эффект', group: 'technical' },
    custom: { label: 'Пользовательский эффект', group: 'status' },
    heal: { label: 'Лечение', group: 'medical' },
    regeneration: { label: 'Регенерация', group: 'medical' },
    temporary_exhaustion: {label:'Временное истощение',group:'medical'},
    temporary_hp_max: {label:'Временный максимум ОЗ',group:'medical'},
    sleep_support: {label:'Поддержка без сна',group:'medical'},
    amputation: {label:'Ампутация',group:'injury'},
    anomaly_compulsion: {label:'Зов аномалии',group:'mental'},
    anomaly_electric_penalty: {label:'Электрошок',group:'status'},
    anomaly_will_penalty: {label:'Подавление Воли',group:'mental'},
    circular_attack_recovery: {label:'Восстановление после кругового удара',group:'status'},
    death_order: {label:'Приказ смерти',group:'mental'},
    mutant_poison: {label:'Яд мутанта',group:'injury'},
    psy_roar_disadvantage: {label:'Последствия пси-рёва',group:'mental'},
    zombification: {label:'Зомбирование',group:'mental'},
    radiation: { label: 'Радиация', group: 'medical' },
    bleeding: { label: 'Кровотечение', group: 'injury' },
    bleeding_external_light: { label: 'Кровотечение внешнее лёгкое', group: 'injury' },
    bleeding_external_medium: { label: 'Кровотечение внешнее среднее', group: 'injury' },
    bleeding_external_severe: { label: 'Кровотечение внешнее сильное', group: 'injury' },
    bleeding_external_extreme: { label: 'Кровотечение внешнее экстремальное', group: 'injury' },
    bleeding_internal_light: { label: 'Кровотечение внутреннее лёгкое', group: 'injury' },
    bleeding_internal_medium: { label: 'Кровотечение внутреннее среднее', group: 'injury' },
    bleeding_internal_severe: { label: 'Кровотечение внутреннее сильное', group: 'injury' },
    bleeding_internal_extreme: { label: 'Кровотечение внутреннее экстремальное', group: 'injury' },
    pain: { label: 'Боль', group: 'injury' },
    exhaustion: { label: 'Истощение', group: 'need' },
    stress: { label: 'Стресс', group: 'mental' },
    stress_effect: { label: 'Эффект стресса', group: 'mental' },
    stress_resistance: { label: 'Сопротивление стрессу', group: 'mental' },
    stress_stupor: { label: 'Ступор', group: 'mental' },
    phobia: { label: 'Фобия', group: 'mental' },
    intoxication: { label: 'Опьянение', group: 'need' },
    infection: { label: 'Заражение', group: 'disease' },
    fracture: { label: 'Перелом', group: 'injury' },
    fracture_fixed: { label: 'Зафиксированный перелом', group: 'injury' },
    fracture_unfixed: { label: 'Незафиксированный перелом', group: 'injury' },
    fracture_sequela: { label: 'Постоянный штраф после перелома', group: 'injury' },
    mangled_limb: { label: 'Искореженная конечность', group: 'injury' },
    temporary_limb_restoration: { label: 'Временное восстановление конечности', group: 'medical' },
    delayed_limb_treatment: { label: 'Отложенное лечение конечности', group: 'medical' },
    organ_loss: { label: 'Повреждённый орган', group: 'injury' },
    organ_failure: { label: 'Смертельное повреждение органа', group: 'critical' },
    shock: { label: 'Шок', group: 'injury' },
    unconsciousness: { label: 'Без сознания', group: 'critical' },
    critical_condition: { label: 'Критическое состояние', group: 'critical' },
    death: { label: 'Смерть', group: 'critical' },
    blindness: { label: 'Слепота', group: 'sense' },
    deafness: { label: 'Глухота', group: 'sense' },
    sleep: { label: 'Сон', group: 'critical' },
    radiation_treatment: { label: 'Выведение радиации', group: 'medical' },
    blood_recovery: { label: 'Восстановление кровопотери', group: 'medical' },
    periodic_adjustment: { label: 'Периодический эффект', group: 'status' },
    delayed_adjustment: { label: 'Отложенный эффект', group: 'status' },
    deferred_adjustment: { label: 'Отложенный эффект', group: 'status' },
    delayed_treatment: { label: 'Ожидание действия препарата', group: 'medical' },
    next_rest_healing: { label: 'Лечение на следующем отдыхе', group: 'medical' },
    untreated_wound: { label: 'Необработанная рана', group: 'injury' },
    tourniquet: { label: 'Наложен жгут', group: 'medical' },
    blood_loss_freeze: { label: 'Стабилизация кровопотери', group: 'medical' },
    bleeding_prevention: { label: 'Блок новых кровотечений', group: 'medical' },
    infection_growth_block: { label: 'Блок нарастания заражения', group: 'medical' },
    analgesia: { label: 'Обезболивание', group: 'medical' },
    stimulant_crash: { label: 'Последствие стимулятора', group: 'medical' },
    radiation_filter: { label: 'Защита от входящей радиации', group: 'medical' },
    temperature_control: { label: 'Контроль температуры', group: 'medical' },
    limb_trauma_suppression: { label: 'Подавление травмы конечности', group: 'medical' },
    pain_block: { label: 'Блок новых уровней боли', group: 'medical' },
    addiction_withdrawal: { label: 'Ломка', group: 'need' },
    withdrawal_support: { label: 'Поддержка при ломке', group: 'medical' },
    withdrawal_support_pending: { label: 'Поддержка при ломке: задержка', group: 'medical' },
};

const TYPE_ALIASES = {
    burning:'burning','горение':'burning',
    concussion:'concussion','контузия':'concussion',
    custom: 'custom',
    'пользовательский эффект': 'custom',
    organ_failure: 'organ_failure',
    heal: 'heal',
    healing: 'heal',
    лечение: 'heal',
    radiation: 'radiation',
    radiaton: 'radiation',
    radiation_reduction: 'radiation',
    bleed: 'bleeding',
    bleeding: 'bleeding',
    кровотечение: 'bleeding',
    external_bleeding: 'bleeding_external_light',
    internal_bleeding: 'bleeding_internal_light',
    bleeding_external_light: 'bleeding_external_light',
    bleeding_external_medium: 'bleeding_external_medium',
    bleeding_external_severe: 'bleeding_external_severe',
    bleeding_external_extreme: 'bleeding_external_extreme',
    bleeding_internal_light: 'bleeding_internal_light',
    bleeding_internal_medium: 'bleeding_internal_medium',
    bleeding_internal_severe: 'bleeding_internal_severe',
    bleeding_internal_extreme: 'bleeding_internal_extreme',
    pain: 'pain',
    боль: 'pain',
    exhaustion: 'exhaustion',
    истощение: 'exhaustion',
    stress: 'stress',
    stress_effect: 'stress_effect',
    stress_resistance: 'stress_resistance',
    stress_stupor: 'stress_stupor',
    phobia: 'phobia',
    стресс: 'stress',
    intoxication: 'intoxication',
    опьянение: 'intoxication',
    infection: 'infection',
    заражение: 'infection',
    fracture: 'fracture',
    перелом: 'fracture',
    fracture_fixed: 'fracture_fixed',
    fixed_fracture: 'fracture_fixed',
    'зафиксированный перелом': 'fracture_fixed',
    'фиксированный перелом': 'fracture_fixed',
    fracture_unfixed: 'fracture_unfixed',
    'незафиксированный перелом': 'fracture_unfixed',
    fracture_sequela: 'fracture_sequela',
    'последствие незафиксированного перелома': 'fracture_sequela',
    shock: 'shock',
    шок: 'shock',
    pain_shock: 'shock',
    'болевой шок': 'shock',
    unconsciousness: 'unconsciousness',
    unconscious: 'unconsciousness',
    безсознания: 'unconsciousness',
    critical_condition: 'critical_condition',
    критическоесостояние: 'critical_condition',
    death: 'death',
    dead: 'death',
    смерть: 'death',
    мертв: 'death',
    blindness: 'blindness',
    blind: 'blindness',
    слепота: 'blindness',
    deafness: 'deafness',
    deaf: 'deafness',
    глухота: 'deafness',
    sleep: 'sleep',
    сон: 'sleep',
    regeneration: 'regeneration',
    regen: 'regeneration',
    регенерация: 'regeneration',
    amputation: 'amputation',
    ампутация: 'amputation',
    mangled_limb: 'mangled_limb',
    'искореженная конечность': 'mangled_limb',
    organloss: 'organ_loss',
    organ_loss: 'organ_loss',
    потеряоргана: 'organ_loss',
    потеря_органа: 'organ_loss',
};

[
    'radiation_treatment', 'blood_recovery', 'periodic_adjustment', 'delayed_adjustment',
    'deferred_adjustment', 'delayed_treatment', 'next_rest_healing', 'untreated_wound',
    'tourniquet', 'blood_loss_freeze', 'bleeding_prevention', 'infection_growth_block',
    'analgesia', 'stimulant_crash', 'radiation_filter', 'temperature_control',
    'limb_trauma_suppression',
    'temporary_limb_restoration',
    'delayed_limb_treatment',
    'pain_block',
    'addiction_withdrawal',
    'withdrawal_support',
    'withdrawal_support_pending',
].forEach(type => { TYPE_ALIASES[type] = type; });

const STATUS_EFFECT_TYPES = new Set([
    'bleeding',
    'pain',
    'exhaustion',
    'stress',
    'intoxication',
    'infection',
    'fracture',
    'fracture_fixed',
    'shock',
    'unconsciousness',
    'critical_condition',
    'death',
    'blindness',
    'deafness',
    'sleep',
    'amputation',
    'mangled_limb',
    'organ_loss',
    'organ_failure',
    'bleeding_external_light',
    'bleeding_external_medium',
    'bleeding_external_severe',
    'bleeding_external_extreme',
    'bleeding_internal_light',
    'bleeding_internal_medium',
    'bleeding_internal_severe',
    'bleeding_internal_extreme',
]);

const ORGAN_LABELS = {
    heart: 'Сердце', rightLung: 'Правое лёгкое', leftLung: 'Левое лёгкое',
    rightKidney: 'Правая почка', leftKidney: 'Левая почка', stomach: 'Желудок',
    liver: 'Печень', brain: 'Мозг', skull: 'Череп', spine: 'Позвоночник',
    rightEye: 'Правый глаз', leftEye: 'Левый глаз',
    rightEar: 'Правое ухо', leftEar: 'Левое ухо', nose: 'Нос', jaw: 'Челюсть',
};

const BLEEDING_STAGE_ORDER = ['normal', 'light', 'medium', 'severe', 'critical', 'fatal'];
const BLEEDING_STAGE_PENALTIES = {
    normal: 0,
    light: 1,
    medium: 2,
    severe: 3,
    critical: 4,
};

const BLEEDING_EFFECT_RULES = {
    bleeding: { severity: 1, kind: 'external', stage: 'light', areas: ['wound'] },
    bleeding_external_light: { severity: 1, kind: 'external', stage: 'light', areas: ['wound'] },
    bleeding_external_medium: { severity: 3, kind: 'external', stage: 'medium', areas: ['wound'] },
    bleeding_external_severe: { severity: 5, kind: 'external', stage: 'severe', areas: ['wound'] },
    bleeding_external_extreme: { severity: 8, kind: 'external', stage: 'extreme', areas: ['wound'] },
    bleeding_internal_light: { severity: 1, kind: 'internal', stage: 'light', areas: ['wound'] },
    bleeding_internal_medium: { severity: 3, kind: 'internal', stage: 'medium', areas: ['wound'] },
    bleeding_internal_severe: { severity: 5, kind: 'internal', stage: 'severe', areas: ['wound'] },
    bleeding_internal_extreme: { severity: 8, kind: 'internal', stage: 'extreme', areas: ['wound'] },
};

const EFFECT_IMPACT_RULES = {
    generic: { areas: [], requiresMedicineCheck: false, treatment: 'manual' },
    custom: { areas: [], requiresMedicineCheck: false, treatment: 'manual' },
    heal: { areas: ['whole_body'], requiresMedicineCheck: false, treatment: 'oral_or_medical' },
    regeneration: { areas: ['whole_body'], requiresMedicineCheck: false, treatment: 'medical' },
    radiation: { areas: ['whole_body'], requiresMedicineCheck: true, treatment: 'medical' },
    bleeding: { areas: ['wound'], requiresMedicineCheck: true, treatment: 'medical' },
    bleeding_external_light: { areas: ['wound'], requiresMedicineCheck: true, treatment: 'medical' },
    bleeding_external_medium: { areas: ['wound'], requiresMedicineCheck: true, treatment: 'medical' },
    bleeding_external_severe: { areas: ['wound'], requiresMedicineCheck: true, treatment: 'medical' },
    bleeding_external_extreme: { areas: ['wound'], requiresMedicineCheck: true, treatment: 'medical' },
    bleeding_internal_light: { areas: ['wound'], requiresMedicineCheck: true, treatment: 'medical' },
    bleeding_internal_medium: { areas: ['wound'], requiresMedicineCheck: true, treatment: 'medical' },
    bleeding_internal_severe: { areas: ['wound'], requiresMedicineCheck: true, treatment: 'medical' },
    bleeding_internal_extreme: { areas: ['wound'], requiresMedicineCheck: true, treatment: 'medical' },
    pain: { areas: ['whole_body'], requiresMedicineCheck: true, treatment: 'medical' },
    exhaustion: { areas: ['whole_body'], requiresMedicineCheck: true, treatment: 'medical' },
    stress: { areas: ['whole_body', 'mind'], requiresMedicineCheck: true, treatment: 'medical' },
    intoxication: { areas: ['whole_body'], requiresMedicineCheck: true, treatment: 'medical' },
    infection: { areas: ['whole_body'], requiresMedicineCheck: true, treatment: 'medical' },
    fracture: { areas: ['limb'], requiresMedicineCheck: true, treatment: 'medical' },
    fracture_fixed: { areas: ['limb'], requiresMedicineCheck: true, treatment: 'medical' },
    shock: { areas: ['whole_body'], requiresMedicineCheck: true, treatment: 'medical' },
    unconsciousness: { areas: ['whole_body'], requiresMedicineCheck: true, treatment: 'medical' },
    critical_condition: { areas: ['whole_body'], requiresMedicineCheck: true, treatment: 'medical' },
    death: { areas: ['whole_body'], requiresMedicineCheck: false, treatment: 'none' },
    blindness: { areas: ['eyes', 'vision', 'head'], requiresMedicineCheck: true, treatment: 'medical' },
    deafness: { areas: ['ears', 'hearing', 'head'], requiresMedicineCheck: true, treatment: 'medical' },
    sleep: { areas: ['whole_body', 'mind'], requiresMedicineCheck: false, treatment: 'rest' },
    amputation: { areas: ['missing_limb'], requiresMedicineCheck: true, treatment: 'medical' },
    mangled_limb: { areas: ['limb'], requiresMedicineCheck: true, treatment: 'surgery' },
    organ_loss: { areas: ['missing_organ'], requiresMedicineCheck: true, treatment: 'medical' },
    addiction_withdrawal: { areas: ['whole_body', 'mind'], requiresMedicineCheck: false, treatment: 'withdrawal' },
    withdrawal_support: { areas: ['whole_body', 'mind'], requiresMedicineCheck: false, treatment: 'medical' },
    withdrawal_support_pending: { areas: ['whole_body', 'mind'], requiresMedicineCheck: false, treatment: 'medical' },
};

function toInt(value, fallback = 0) {
    const parsed = Number.parseInt(value, 10);
    return Number.isFinite(parsed) ? parsed : fallback;
}

function clamp(value, min = 0, max = null) {
    let result = value;
    if (min !== null) result = Math.max(min, result);
    if (max !== null) result = Math.min(max, result);
    return result;
}

function canonicalType(type, name = '') {
    const raw = String(type || name || '').trim().toLowerCase();
    if (!raw) return 'generic';
    if (TYPE_ALIASES[raw]) return TYPE_ALIASES[raw];
    if (EFFECT_TYPE_META[raw]) return raw;

    const compact = raw.replace(/[\s_-]+/g, '');
    if (TYPE_ALIASES[compact]) return TYPE_ALIASES[compact];

    if (raw.includes('внутрен') && raw.includes('кров')) {
        if (raw.includes('экстрем')) return 'bleeding_internal_extreme';
        if (raw.includes('сильн')) return 'bleeding_internal_severe';
        if (raw.includes('средн')) return 'bleeding_internal_medium';
        if (raw.includes('легк') || raw.includes('лёгк')) return 'bleeding_internal_light';
        return 'bleeding_internal_light';
    }
    if (raw.includes('внешн') && raw.includes('кров')) {
        if (raw.includes('экстрем')) return 'bleeding_external_extreme';
        if (raw.includes('сильн')) return 'bleeding_external_severe';
        if (raw.includes('средн')) return 'bleeding_external_medium';
        if (raw.includes('легк') || raw.includes('лёгк')) return 'bleeding_external_light';
        return 'bleeding_external_light';
    }
    if (raw.includes('кров')) return 'bleeding';
    if (raw.includes('радиа')) return 'radiation';
    if (raw.includes('истощ')) return 'exhaustion';
    if (raw.includes('стресс')) return 'stress';
    if (raw.includes('опьян')) return 'intoxication';
    if (raw.includes('зафикс') || raw.includes('фиксир') || raw.includes('fixed fracture')) return 'fracture_fixed';
    if (raw.includes('перелом')) return 'fracture';
    if (raw.includes('боль')) return 'pain';
    if (raw.includes('шок')) return 'shock';
    if (raw.includes('критичес')) return 'critical_condition';
    if (raw.includes('смерт') || raw.includes('мертв')) return 'death';
    if (raw.includes('слеп')) return 'blindness';
    if (raw.includes('глух')) return 'deafness';
    if (raw.includes('зараж')) return 'infection';
    if (raw.includes('лечение') || raw.includes('heal')) return 'heal';
    if (raw.includes('реген')) return 'regeneration';
    if (raw.includes('ампута')) return 'amputation';
    if (raw.includes('орган') && raw.includes('потер')) return 'organ_loss';

    return 'generic';
}

function getEffectMeta(type) {
    return EFFECT_TYPE_META[type] || EFFECT_TYPE_META.generic;
}

function getBleedingRule(type) {
    return Object.prototype.hasOwnProperty.call(BLEEDING_EFFECT_RULES, type)
        ? BLEEDING_EFFECT_RULES[type]
        : null;
}

function getBleedingStageValue(stage) {
    const index = BLEEDING_STAGE_ORDER.indexOf(String(stage || 'normal').toLowerCase());
    return index >= 0 ? index : 0;
}

function getBleedingState(health = {}) {
    const effects = normalizeEffectList(Array.isArray(health.effects) ? health.effects : []);
    const combatMeta = health.combatMeta || {};
    const breakdown = {
        external: { light: 0, medium: 0, severe: 0, extreme: 0, total: 0 },
        internal: { light: 0, medium: 0, severe: 0, extreme: 0, total: 0 },
    };
    const effectDetails = [];
    let totalSeverity = 0;

    effects.forEach((effect) => {
        const rule = getBleedingRule(effect.type);
        if (!rule || effect.active === false || effect.closed || effect.suppressed) return;
        const stacks = Math.max(1, toInt(effect.stacks, 1));
        const baseSeverity = Math.max(1, toInt(effect.value, rule.severity || 1));
        const resolvedSeverity = Math.max(rule.severity || 1, baseSeverity) * stacks;
        const group = rule.kind || 'external';
        const stage = rule.stage || 'light';
        if (!breakdown[group]) return;
        breakdown[group][stage] = (breakdown[group][stage] || 0) + stacks;
        breakdown[group].total += resolvedSeverity;
        totalSeverity += resolvedSeverity;
        effectDetails.push({
            id: effect.id,
            type: effect.type,
            name: effect.name || getEffectMeta(effect.type).label,
            kind: group,
            stage,
            severity: resolvedSeverity,
            stacks,
            area: effect.area || null,
            treated: Boolean(effect.treated),
        });
    });

    const bloodStage = String(health.blood || health.bloodStage || 'normal').toLowerCase();
    const stagePenalty = getBleedingStageValue(bloodStage);
    const rawSeverity=totalSeverity;
    let severityModifierTotal=0,modifierTotal=0;
    if(Array.isArray(combatMeta.bleedingModifiers))for(const item of combatMeta.bleedingModifiers){
        if(item?.remaining!=null&&Number(item.remaining)<=0)continue;
        const value=toInt(item?.value??item,0);
        if(item?.affects_severity||item?.note==='hematogen')severityModifierTotal+=value;
        else modifierTotal+=value;
    }else modifierTotal=toInt(combatMeta.bleedingModifierTotal??health.bleedingModifierTotal??0,0);
    totalSeverity=Math.max(0,rawSeverity+severityModifierTotal);
    return {
        baseDifficulty: 5,
        totalSeverity,
        rawSeverity,severityModifierTotal,
        bloodStage,
        stagePenalty,
        modifierTotal,
        difficulty: Math.max(0, 5 + totalSeverity - stagePenalty + modifierTotal),
        breakdown,
        effects: effectDetails,
    };
}

export function syncHealthDerivedStatuses(health = {}) {
    health.painLevel=Math.max(Number(health.painLevel)||0,Math.min(5,Math.max(0,Number(health.combatMeta?.toughNut?.minimumPain)||0)));
    if (!health || typeof health !== 'object') return health;
    const effects = normalizeEffectList(health.effects || []);
    health.combatMeta = health.combatMeta || {};
    // Проверка смерти и срок отключки принадлежат серверу; просмотр листа не бросает кости.
    const fatalTotalHealth = Number(health.max) > 0 && Number(health.current) <= 0;
    const fatalBrainHealth = health.organs?.brain && Number(health.organs.brain.current) <= 0;
    const fatalSkullHealth = health.organs?.skull && Number(health.organs.skull.current) <= 0;
    if ((fatalTotalHealth || fatalBrainHealth || fatalSkullHealth) && !effects.some(effect => effect.type === 'death')) {
        effects.push(normalizeEffect({
            type: 'death',
            name: 'Смерть',
            source: fatalBrainHealth
                ? 'zero_brain_health'
                : (fatalSkullHealth ? 'zero_skull_health' : 'zero_total_health'),
            tick: 'manual',
        }));
    }
    health.effects = effects;
    const bleeding = getBleedingState(health);
    health.bleeding = bleeding;
    health.bleedingSeverity = bleeding.totalSeverity;
    health.bleedingDifficulty = bleeding.difficulty;
    health.bleedingStagePenalty = bleeding.stagePenalty;
    health.bleedingModifierTotal = bleeding.modifierTotal;
    health.bloodStage = bleeding.bloodStage;
    health.bleedingEffects = bleeding.effects;
    return health;
}

export function getEffectTypeOptions() {
    return Object.entries(EFFECT_TYPE_META).map(([value, meta]) => ({
        value,
        label: meta.label,
        group: meta.group,
    }));
}

export function normalizeEffect(raw = {}) {
    if (raw === null || raw === undefined) {
        return createEffectDraft('generic');
    }
    if (typeof raw === 'string') {
        return createEffectDraft(canonicalType(raw, raw), { name: raw });
    }
    if (typeof raw !== 'object') {
        return createEffectDraft('generic', { value: raw });
    }

    const name = String(raw.name || raw.label || '').trim();
    const source = raw.source || raw.origin || null;
    let type = canonicalType(raw.type || raw.kind || raw.effectType, name);
    if(type==='generic'&&name.toLocaleLowerCase()==='контузия')type='concussion';
    if(type==='generic'&&name.toLocaleLowerCase()==='горение')type='burning';
    if (source === 'stress_manifestation' && type === 'generic') type = 'stress_effect';
    const value = raw.value ?? raw.amount ?? raw.power ?? 0;
    const duration = raw.duration ?? raw.turns ?? raw.remaining ?? null;

    const normalized = {
        id: raw.id || null,
        type,
        name: name || getEffectMeta(type).label,
        value: Number.isFinite(Number(value)) ? Number(value) : 0,
        duration: duration === null || duration === '' ? null : toInt(duration, null),
        remaining: raw.remaining === undefined || raw.remaining === null || raw.remaining === '' ? null : toInt(raw.remaining, null),
        stacks: raw.stacks === undefined || raw.stacks === null ? 1 : Math.max(1, toInt(raw.stacks, 1)),
        source,
        note: raw.note || raw.description || '',
        tick: raw.tick || raw.tickPhase || 'manual',
        scope: raw.scope || 'character',
        active: raw.active !== false,
        area: raw.area || raw.zone || raw.bodyPart || raw.target || null,
    };
    if(type==='concussion')normalized.rollPenalty=3;
    if(type==='deafness')normalized.value=Math.max(0,Number(raw.value??raw.amount??0)||0);
    Object.entries(raw).forEach(([key, entryValue]) => {
        if (!['type', 'kind', 'effectType', 'turns', 'tickPhase', 'zone', 'bodyPart', 'target'].includes(key)
            && normalized[key] === undefined) {
            normalized[key] = entryValue;
        }
    });
    if(type==='burning'){
        normalized.tick='round_end';normalized.damage_per_round=normalized.damage_per_round||50;
        normalized.thermal_threshold??=35;
    }
    if (['', 'общий', 'generic', 'неопределенный эффект', 'неопределённый эффект'].includes(String(normalized.name || '').trim().toLowerCase()) && type !== 'generic') {
        normalized.name = getEffectMeta(type).label;
    }
    if (type === 'organ_loss') {
        if (normalized.area) {
            normalized.name = `Повреждённый орган: ${ORGAN_LABELS[normalized.area] || normalized.area}`;
        }
        normalized.treatment_window_seconds = Math.max(
            0,
            Number(raw.treatment_window_seconds ?? normalized.treatment_window_seconds ?? 3600),
        );
        normalized.treatment_window_expired = Boolean(
            raw.treatment_window_expired || normalized.treatment_window_seconds <= 0,
        );
    }
    const maxHours = toInt(normalized.max_hours, 0);
    if (normalized.remaining === null && maxHours > 0) {
        normalized.remaining = maxHours;
        normalized.duration = normalized.duration ?? maxHours;
        normalized.time_unit = 'hour';
    }
    return normalized;
}

function isLegacyAdditionalTraumaEffect(effect) {
    if (!effect || typeof effect !== 'object' || Array.isArray(effect)) return false;
    const rawType = String(effect.type || effect.kind || effect.effectType || '').trim().toLowerCase();
    if (!['additional_trauma', 'generic'].includes(rawType)) return false;
    const outcomeKeys = ['fracture', 'bleeding', 'pain', 'shock', 'organ', 'fall_or_drop'];
    const hasOwn = key => Object.prototype.hasOwnProperty.call(effect, key);
    return hasOwn('chance_roll')
        && hasOwn('roll')
        && outcomeKeys.filter(hasOwn).length >= 3;
}

export function normalizeEffectList(list) {
    if (!Array.isArray(list)) return [];
    return list.filter(effect => !isLegacyAdditionalTraumaEffect(effect)).map(normalizeEffect);
}

export function createEffectDraft(type = 'generic', overrides = {}) {
    const effectType = canonicalType(type, overrides.name || '');
    const meta = getEffectMeta(effectType);
    const result = {
        id: overrides.id || `effect_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
        type: effectType,
        name: overrides.name || meta.label,
        value: overrides.value ?? 0,
        duration: overrides.duration ?? null,
        remaining: overrides.remaining ?? (overrides.duration ?? null),
        stacks: overrides.stacks ?? 1,
        source: overrides.source || null,
        area: overrides.area || overrides.zone || overrides.bodyPart || overrides.target || null,
        note: overrides.note || '',
        tick: overrides.tick || 'manual',
        scope: overrides.scope || 'character',
        active: overrides.active !== false,
    };
    if (effectType === 'organ_loss') {
        result.treatment_window_seconds = overrides.treatment_window_seconds ?? 3600;
        result.treatment_window_expired = overrides.treatment_window_expired ?? false;
    }
    return result;
}

function upsertStatusEffect(health, effect) {
    if (!Array.isArray(health.effects)) {
        health.effects = [];
    }

    const existingIndex = health.effects.findIndex(item => {
        const current = normalizeEffect(item);
        if (effect.id && current.id === effect.id) return true;
        if (String(effect.type || '').startsWith('bleeding_')) return false;
        return current.type === effect.type
            && (current.source || null) === (effect.source || null)
            && (current.area || null) === (effect.area || null);
    });

    if (existingIndex >= 0) {
        const current = normalizeEffect(health.effects[existingIndex]);
        health.effects[existingIndex] = {
            ...current,
            ...effect,
            stacks: Math.max(current.stacks || 1, effect.stacks || 1),
            remaining: effect.remaining !== null ? effect.remaining : current.remaining,
            active: effect.active !== false && current.active !== false,
        };
    } else {
        health.effects.push(effect);
    }
}

function adjustHealthField(health, field, delta, min = 0, max = null) {
    const current = toInt(health[field], 0);
    health[field] = clamp(current + delta, min, max);
}

function distributeZoneHealing(health, amount) {
    let remaining = Math.max(0, Math.floor(Number(amount) || 0));
    const temporaryCaps = new Map(normalizeEffectList(health.effects || [])
        .filter(effect => effect.type === 'temporary_limb_restoration'
            && effect.health_cap != null
            && effect.active !== false
            && (effect.remaining == null || Number(effect.remaining) > 0))
        .map(effect => [String(effect.area || ''), Math.max(1, Number(effect.health_cap || 1))]));
    const zones = Object.entries(health.zones || {})
        .filter(([, zone]) => zone && typeof zone === 'object');
    zones.forEach(([area, zone]) => {
        const maximum = Math.min(
            Math.max(0, Math.round(Number(zone.max || 0))),
            temporaryCaps.get(area) ?? Number.POSITIVE_INFINITY
        );
        zone.current = Math.min(maximum, Math.max(0, Math.round(Number(zone.current || 0))));
    });
    while (remaining > 0) {
        const damaged = zones.filter(([area, zone]) => Number(zone.current || 0) > 0
            && Number(zone.current || 0) < Math.min(
                Number(zone.max || 0),
                temporaryCaps.get(area) ?? Number.POSITIVE_INFINITY
            ));
        if (!damaged.length) break;
        const share = Math.floor(remaining / damaged.length);
        if (share === 0) {
            damaged.slice(0, remaining).forEach(([, zone]) => {
                zone.current += 1;
            });
            break;
        }
        let applied = 0;
        damaged.forEach(([area, zone]) => {
            const current = Number(zone.current || 0);
            const maximum = Math.min(
                Number(zone.max || 0),
                temporaryCaps.get(area) ?? Number.POSITIVE_INFINITY
            );
            const healed = Math.min(share, maximum - current);
            zone.current = current + healed;
            zone.destructionDamage = Math.max(0, maximum - zone.current);
            applied += healed;
        });
        if (applied <= 0) break;
        remaining -= applied;
    }
}

function healHealthAndZones(health, amount) {
    adjustHealthField(health, 'current', amount, 0, health.max ?? null);
    distributeZoneHealing(health, amount);
}

export function applyEffectToHealth(healthInput = {}, rawEffect = {}) {
    const health = healthInput;
    const effect = normalizeEffect(rawEffect);
    const signedValue = Number(effect.value);
    const magnitude = Math.abs(Number.isFinite(signedValue) ? signedValue : toInt(effect.value, 0));
    const activeEffects = normalizeEffectList(health.effects || []);
    if (effect.type === 'concussion') {
        effect.remaining = Math.max(effect.remaining || 0, ...activeEffects.filter(e => e.type === 'concussion' && e.active !== false).map(e => e.remaining || 0)) || null;
        health.effects = [...activeEffects.filter(e => e.type !== 'concussion'), effect];
        syncHealthDerivedStatuses(health);
        return { health, effect, applied: true, summary: effect.type };
    }
    if (String(effect.type || '').startsWith('bleeding_')
        && activeEffects.some(item => item.type === 'bleeding_prevention' && item.active !== false)) {
        return { health, effect, applied: false, summary: 'bleeding_blocked' };
    }

    if (effect.type === 'heal') {
        healHealthAndZones(health, magnitude);
        return { health, effect, applied: true, summary: `heal:${magnitude}` };
    }

    if (effect.type === 'regeneration') {
        upsertStatusEffect(health, effect);
        syncHealthDerivedStatuses(health);
        return { health, effect, applied: true, summary: `regeneration:${magnitude}` };
    }

    if (effect.type === 'radiation') {
        adjustHealthField(health, 'radiation', Number.isFinite(signedValue) ? signedValue : -magnitude, 0, null);
        return { health, effect, applied: true, summary: `radiation:-${magnitude}` };
    }

    if (effect.type === 'pain') {
        if (signedValue > 0 && activeEffects.some(item => item.blocks_new_pain && item.active !== false)) {
            health.combatMeta = health.combatMeta || {};
            if (activeEffects.some(item => item.blocks_new_pain && item.active !== false
                && (item.type === 'pain_block' || 'return_fraction' in item))) {
                health.combatMeta.blockedPain = Number(health.combatMeta.blockedPain || 0) + signedValue;
            }
            return { health, effect, applied: false, summary: 'pain_blocked' };
        }
        adjustHealthField(health, 'painLevel', Number.isFinite(signedValue) ? signedValue : magnitude, 0, 10);
        health.combatMeta = health.combatMeta || {};
        health.combatMeta.painIncreased = true;
        return { health, effect, applied: true, summary: `pain:+${magnitude}` };
    }

    if (effect.type === 'exhaustion') {
        adjustHealthField(health, 'exhaustion', Number.isFinite(signedValue) ? signedValue : magnitude, 0, 10);
        return { health, effect, applied: true, summary: `exhaustion:+${magnitude}` };
    }

    if (effect.type === 'stress') {
        adjustHealthField(health, 'stress', Number.isFinite(signedValue) ? signedValue : magnitude, 0, 10);
        return { health, effect, applied: true, summary: `stress:+${magnitude}` };
    }

    if (effect.type === 'intoxication') {
        adjustHealthField(health, 'intoxication', Number.isFinite(signedValue) ? signedValue : magnitude, 0, null);
        return { health, effect, applied: true, summary: `intoxication:+${magnitude}` };
    }

    if (effect.type === 'infection') {
        adjustHealthField(health, 'infection', Number.isFinite(signedValue) ? signedValue : magnitude, 0, 100);
        syncHealthDerivedStatuses(health);
        return { health, effect, applied: true, summary: `infection:+${magnitude}` };
    }

    if (effect.type === 'bleeding' || effect.type === 'fracture' || effect.type === 'fracture_fixed' || effect.type === 'shock' || effect.type === 'unconsciousness' || effect.type === 'critical_condition' || effect.type === 'death' || effect.type === 'blindness' || effect.type === 'deafness' || effect.type === 'bleeding_external_light' || effect.type === 'bleeding_external_medium' || effect.type === 'bleeding_external_severe' || effect.type === 'bleeding_external_extreme' || effect.type === 'bleeding_internal_light' || effect.type === 'bleeding_internal_medium' || effect.type === 'bleeding_internal_severe' || effect.type === 'bleeding_internal_extreme') {
        upsertStatusEffect(health, effect);
        syncHealthDerivedStatuses(health);
        return { health, effect, applied: true, summary: effect.type };
    }

    upsertStatusEffect(health, effect);
    syncHealthDerivedStatuses(health);
    return { health, effect, applied: true, summary: effect.type };
}

export function isAlcoholConsumable(item = {}) {
    const direct = item?.attributes?.consumable?.direct || {};
    if (item?.attributes?.is_alcohol === true
        || item?.attributes?.alcohol === true
        || direct.is_alcohol === true) return true;
    if (Object.keys(direct).length) return false;
    const section = String(item?.attributes?.section || item?.subcategory || '').trim().toLowerCase();
    if (section === 'продукты' && Number(direct.intoxication_delta || 0) > 0) return true;
    return /водка|самогон|вино|пиво|алкогол/i.test(String(item?.name || ''));
}

export function normalizeCharacterEffects(characterData = {}) {
    if (!characterData || typeof characterData !== 'object') return characterData;
    if (characterData.health && Array.isArray(characterData.health.effects)) {
        characterData.health.effects = normalizeEffectList(characterData.health.effects);
        syncHealthDerivedStatuses(characterData.health);
    }
    if (Array.isArray(characterData.effects)) {
        characterData.effects = normalizeEffectList(characterData.effects);
    }
    return characterData;
}

export function tickEffect(effect, phase = 'turn_end') {
    const normalized = normalizeEffect(effect);
    if (!normalized.active) return normalized;
    if (normalized.tick === 'manual' || (normalized.tick && normalized.tick !== phase)) {
        return normalized;
    }

    if (normalized.remaining !== null && normalized.remaining !== undefined) {
        normalized.remaining = Math.max(0, toInt(normalized.remaining, 0) - 1);
    }

    return normalized;
}

export function advanceTimedEffects(health = {}, effectsInput = [], elapsedSeconds = 0, includeTurnEffects = false) {
    const unitSeconds = { second: 1, minute: 60, movement: 600, hour: 3600 };
    const survivors = [];
    const activated = [];
    const curedFractureAreas = new Set();
    const adjust = (entry) => {
        if (!entry?.field) return;
        const min = entry.min ?? 0;
        const max = entry.max ?? null;
        let value = Number(health[entry.field] || 0) + Number(entry.delta || 0);
        if (min !== null) value = Math.max(Number(min), value);
        if (max !== null) value = Math.min(Number(max), value);
        health[entry.field] = value;
    };

    normalizeEffectList(effectsInput).forEach(effect => {
        if (effect.type === 'fracture') {
            const regular = Math.max(0, Number(effect.regular_fixation_seconds ?? 1800) - Math.max(0, Number(elapsedSeconds) || 0));
            const hinged = Math.max(0, Number(effect.hinged_fixation_seconds ?? 1800) - Math.max(0, Number(elapsedSeconds) || 0));
            effect.regular_fixation_seconds = regular;
            effect.hinged_fixation_seconds = hinged;
            effect.regular_fixation_expired = regular <= 0;
            if (hinged <= 0) {
                const consequenceRoll = 1 + Math.floor(Math.random() * 100);
                effect.type = 'fracture_unfixed';
                effect.name = 'Незафиксированный перелом';
                effect.tick = 'manual';
                effect.fixation_consequence_roll = consequenceRoll;
                effect.permanent_penalty = consequenceRoll <= 50;
                if (effect.permanent_penalty) {
                    activated.push({
                        type: 'fracture_sequela',
                        name: 'Постоянный штраф после перелома',
                        area: effect.area,
                        source: 'unfixed_fracture',
                        tick: 'manual',
                    });
                }
            }
            survivors.push(effect);
            return;
        }
        if (effect.treatment_window_seconds != null) {
            const treatmentWindow = Math.max(
                0,
                Number(effect.treatment_window_seconds || 0) - Math.max(0, Number(elapsedSeconds) || 0)
            );
            effect.treatment_window_seconds = treatmentWindow;
            effect.treatment_window_expired = treatmentWindow <= 0;
        }
        let unit = String(effect.time_unit || '').toLowerCase();
        if (effect.remaining == null && Number(effect.max_hours || 0) > 0) {
            effect.remaining = Number(effect.max_hours);
            effect.time_unit = 'hour';
            unit = 'hour';
        }
        if (!unit) {
            unit = {
                time_elapsed: 'minute',
                movement_end: 'movement',
                hour_start: 'hour',
            }[effect.tick] || '';
        }
        if (includeTurnEffects && !unit && effect.tick === 'turn_end') {
            unit = 'second';
            effect.seconds_per_unit ??= 6;
        }
        if (!unitSeconds[unit] || effect.remaining == null) {
            survivors.push(effect);
            return;
        }
        const secondsPerUnit = Math.max(0.001, Number(effect.seconds_per_unit || unitSeconds[unit]));
        const remainingSeconds = Number(
            effect.remaining_seconds ?? (Number(effect.remaining) * secondsPerUnit)
        ) - Math.max(0, Number(elapsedSeconds) || 0);
        if (remainingSeconds > 0) {
            effect.remaining_seconds = remainingSeconds;
            effect.remaining = Math.max(1, Math.ceil(remainingSeconds / secondsPerUnit));
            survivors.push(effect);
            return;
        }
        (effect.onExpire || effect.on_expire || []).forEach(adjust);
        if (['delayed_adjustment', 'delayed_treatment', 'deferred_adjustment'].includes(effect.type)) {
            (effect.adjustments || []).forEach(adjust);
        }
        if (effect.type === 'temporary_limb_restoration' && effect.restore_on_expire !== false) {
            const zone = health.zones?.[effect.area];
            if (zone && typeof zone === 'object') {
                zone.current = Math.min(
                    Number(zone.current || 0),
                    Number(effect.previous_health || 0)
                );
            }
        }
        if (effect.type === 'delayed_limb_treatment') {
            const area = String(effect.area || '');
            if (effect.cure_fracture && area) curedFractureAreas.add(area);
            const zone = health.zones?.[area];
            if (zone && typeof zone === 'object' && effect.restore_limb_health != null) {
                zone.current = Math.min(
                    Number(zone.max ?? effect.restore_limb_health),
                    Math.max(0, Number(effect.restore_limb_health) || 0)
                );
            }
        }
        activated.push(...(effect.activate_effects || effect.activateEffects || []).filter(
            entry => entry && typeof entry === 'object'
        ));
    });
    const modifiers = health.combatMeta?.consumableModifiers;
    if (Array.isArray(modifiers)) {
        health.combatMeta.consumableModifiers = modifiers.filter(modifier => {
            if (!modifier || typeof modifier !== 'object' || modifier.remaining == null) return true;
            let unit = String(modifier.time_unit || '').toLowerCase();
            const tick = String(modifier.tick || 'turn_end');
            if (!unit) {
                unit = {
                    turn_end: 'second',
                    time_elapsed: 'minute',
                    movement_end: 'movement',
                    hour_start: 'hour',
                }[tick] || '';
            }
            if (!unitSeconds[unit]) return true;
            const secondsPerUnit = Math.max(
                0.001,
                Number(modifier.seconds_per_unit || (tick === 'turn_end' ? 6 : unitSeconds[unit]))
            );
            const remainingSeconds = Number(
                modifier.remaining_seconds ?? (Number(modifier.remaining) * secondsPerUnit)
            ) - Math.max(0, Number(elapsedSeconds) || 0);
            if (remainingSeconds <= 0) return false;
            modifier.remaining_seconds = remainingSeconds;
            modifier.remaining = Math.max(1, Math.ceil(remainingSeconds / secondsPerUnit));
            return true;
        });
    }
    health.effects = curedFractureAreas.size
        ? survivors.filter(effect => !(
            ['fracture', 'fracture_fixed', 'fracture_unfixed', 'fracture_sequela'].includes(effect.type)
            && curedFractureAreas.has(String(effect.area || ''))
        ))
        : survivors;
    activated.forEach(effect => applyEffectToHealth(health, effect));
    syncHealthDerivedStatuses(health);
    return normalizeEffectList(health.effects || []);
}

export function getEffectImpact(effect) {
    const normalized = normalizeEffect(effect);
    const rule = EFFECT_IMPACT_RULES[normalized.type] || EFFECT_IMPACT_RULES.generic;
    const areas = new Set(rule.areas || []);
    if (normalized.area) areas.add(normalized.area);
    const bleedingRule = getBleedingRule(normalized.type);
    return {
        type: normalized.type,
        name: normalized.name,
        areas: Array.from(areas),
        requiresMedicineCheck: !!rule.requiresMedicineCheck,
        treatment: rule.treatment || 'manual',
        severity: bleedingRule ? Math.max(bleedingRule.severity || 1, toInt(normalized.value, 0)) : null,
        bleedingKind: bleedingRule ? bleedingRule.kind : null,
    };
}

export function summarizeEffectImpact(effect) {
    const impact = getEffectImpact(effect);
    const areas = impact.areas.length ? impact.areas.join(', ') : 'нет';
    const treatment = impact.requiresMedicineCheck ? 'требует Медицины' : 'без проверки';
    const bleedingPart = impact.bleedingKind ? `${impact.bleedingKind} ${impact.severity || 1}` : '';
    return `${impact.name}${bleedingPart ? `: ${bleedingPart}` : ''}${areas ? `, ${areas}` : ''} (${treatment})`;
}

export function applyPeriodicEffectsToHealth(healthInput = {}, effectsInput = [], phase = 'turn_end') {
    const health = healthInput;
    const effects = normalizeEffectList(effectsInput);
    const applied = [];
    effects.forEach((rawEffect) => {
        const effect = normalizeEffect(rawEffect);
        if (!effect.active) return;
        if (effect.tick && effect.tick !== 'manual' && effect.tick !== phase) return;

        const magnitude = Math.abs(toInt(effect.value, 0));
        if (effect.type === 'regeneration') {
            healHealthAndZones(health, magnitude);
            applied.push({ type: 'regeneration', delta: magnitude });
        }
    });
    syncHealthDerivedStatuses(health);
    return { health, effects, applied };
}

export function effectSummary(effect) {
    const normalized = normalizeEffect(effect);
    const name = !normalized.name || normalized.name === normalized.type
        ? getEffectMeta(normalized.type).label : normalized.name;
    const parts = [name];
    const bleedingRule = getBleedingRule(normalized.type);
    if (bleedingRule) parts.push(`тяжесть ${Math.max(bleedingRule.severity, Number(normalized.value) || 0) * Math.max(1, Number(normalized.stacks) || 1)}`);
    const valueInName = new RegExp(`(^|[^\\d])${String(Math.abs(normalized.value)).replace('.', '\\.')}([^\\d]|$)`).test(name);
    if (normalized.value && !valueInName && !bleedingRule) {
        if (['blindness','deafness'].includes(normalized.type)) parts.push(`${normalized.value} очков`);
        else parts.push(`${normalized.value > 0 ? '+' : ''}${normalized.value}`);
    }
    const duration = effectDurationLabel(normalized);
    if (duration) parts.push(duration);
    if (normalized.active === false) parts.push('не действует');
    else if (normalized.remaining != null && normalized.remaining !== '' && Number(normalized.remaining) <= 0) parts.push('завершён');
    return parts.join(' · ');
}

export function effectDurationLabel(effect) {
    if (effect.remaining == null || effect.remaining === '') return '';
    const seconds = Number(effect.remaining_seconds);
    const unit = effect.time_unit || ({time_elapsed:'minute', hour_start:'hour', movement_end:'movement'})[effect.tick];
    if (effect.remaining_seconds != null && Number.isFinite(seconds)) {
        if (seconds < 60) return `осталось ${Math.max(0, Math.ceil(seconds))} с`;
        if (seconds < 3600) return `осталось ${Math.ceil(seconds / 60)} мин`;
        const totalMinutes = Math.ceil(seconds / 60);
        const hours = Math.floor(totalMinutes / 60), minutes = totalMinutes % 60;
        return `осталось ${hours} ч${minutes ? ` ${minutes} мин` : ''}`;
    }
    const labels = {second:'с', minute:'мин', hour:'ч', movement:'переходов'};
    const suffix = labels[unit] || ({turn_end:'ходов',round_end:'раундов',day_start:'дней'})[effect.tick];
    return suffix ? `осталось ${effect.remaining} ${suffix}` : `срок: ${effect.remaining}`;
}

export function effectConsequences(effect, depth = 0) {
    const labels = {exhaustion:'Истощение',painLevel:'Боль',stress:'Стресс',radiation:'Радиация',
        intoxication:'Опьянение',current:'ОЗ',max:'Максимум ОЗ',infection:'Заражение',temperature:'Температура'};
    const lines = [];
    for (const [changes, caption] of [[effect.onExpire || effect.on_expire,'После окончания'],
        [effect.adjustments,effect.type === 'periodic_adjustment' ? 'При срабатывании' : 'После задержки']]) {
        for (const change of changes || []) {
            if (labels[change.field]) lines.push(`${caption}: ${labels[change.field]} ${Number(change.delta)>0?'+':''}${change.delta}`);
        }
    }
    if (effect.death_on_expire) lines.push('После окончания срока: смерть');
    const stats = {accuracy:'Точность',agility:'Ловкость',strength:'Сила',will:'Воля',awareness:'Внимательность',melee:'Ближний бой'};
    for (const next of effect.activate_modifiers || []) if (stats[next.stat]) {
        lines.push(`После окончания: ${stats[next.stat]} ${Number(next.value)>0?'+':''}${next.value} · ${effectDurationLabel(next)}`);
    }
    if (depth < 2) for (const next of effect.activate_effects || effect.activateEffects || []) {
        if (next && typeof next === 'object') lines.push(`После задержки: ${effectSummary(next)}`, ...effectConsequences(next, depth + 1));
    }
    return [...new Set(lines)];
}
