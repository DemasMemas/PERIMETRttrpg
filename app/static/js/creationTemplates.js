// Шаблон хранит только решения мастера, без готового листа и наград истории.
const FORMAT = 'perimeter-character-plan';
const FIELDS = ['backgroundId', 'backgroundWeapon', 'favoredMutant', 'specializations',
    'raises', 'lowers', 'finalRaises', 'traits', 'cashPoints', 'zoneHistory', 'purchases'];

export function characterPlanFile(plan) {
    const clean = Object.fromEntries(FIELDS.filter(key => Object.hasOwn(plan, key)).map(key => [key, plan[key]]));
    return JSON.stringify({ format: FORMAT, version: 1, plan: clean }, null, 2);
}

export function readCharacterPlanFile(text, rules) {
    if (text.length > 256 * 1024) throw Error('Файл шаблона слишком большой.');
    let file;
    try { file = JSON.parse(text); } catch { throw Error('Не удалось прочитать файл шаблона.'); }
    if (file?.format !== FORMAT || file.version !== 1 || !file.plan || Array.isArray(file.plan) || typeof file.plan !== 'object') {
        throw Error('Выберите шаблон, сохранённый в мастере создания персонажа.');
    }
    const plan = JSON.parse(characterPlanFile(file.plan)).plan;
    if (!rules.backgrounds.some(entry => entry.available && entry.id === plan.backgroundId)) throw Error('Предыстория шаблона недоступна.');
    if (!Array.isArray(plan.specializations) || plan.specializations.some(key => !rules.skillOrder.includes(key))) throw Error('В шаблоне есть неизвестный навык.');
    for (const stage of ['raises', 'lowers', 'finalRaises']) {
        if (!plan[stage] || Array.isArray(plan[stage]) || typeof plan[stage] !== 'object'
            || Object.entries(plan[stage]).some(([key, value]) => !rules.skillOrder.includes(key) || !Number.isInteger(value) || value < 0 || value > (stage === 'lowers' ? 5 : 19))) {
            throw Error('Некорректное распределение навыков в шаблоне.');
        }
    }
    if (!Array.isArray(plan.traits) || plan.traits.length > 3 || plan.traits.some(choice => !choice || !rules.traits.some(entry => entry.id === choice.id))) throw Error('В шаблоне есть недоступные черты.');
    if (!Array.isArray(plan.purchases) || plan.purchases.some(item => !item || !rules.startingPurchaseTemplates.some(entry => entry.id === item.templateId) || !Number.isInteger(item.quantity) || item.quantity < 1 || item.quantity > 10000)) throw Error('Снаряжение шаблона недоступно в текущем каталоге.');
    if (!Number.isInteger(plan.cashPoints) || plan.cashPoints < 0 || typeof plan.zoneHistory !== 'boolean') throw Error('Некорректные параметры шаблона.');
    return plan;
}
