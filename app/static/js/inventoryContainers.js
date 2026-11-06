// Вместимость подсумка задаётся его моделью, отдельно от внешнего объёма.
export function applyPouchTemplate(pouch, template) {
    const attributes = structuredClone(template.attributes || {});
    const capacity = attributes.capacity ?? attributes.limit ?? template.volume ?? 0;
    Object.assign(pouch, {
        type: template.id, name: template.name, capacity, internalVolume: capacity,
        subcategory: template.subcategory,
        weight: template.weight ?? 0, volume: template.volume ?? 0, attributes,
        allowed_categories: structuredClone(attributes.allowed_categories || []),
    });
    delete pouch.allowedCategories;
    return pouch;
}

export function vestPouches(template) {
    return structuredClone(template.attributes?.pouches || []).map(pouch => ({
        ...pouch,
        internalVolume: pouch.internalVolume ?? pouch.capacity ?? pouch.attributes?.capacity ?? 0,
        contents: pouch.contents || [],
    }));
}

export function returnPouchContents(data, pouches) {
    const items = (pouches || []).flatMap(pouch => Array.isArray(pouch?.contents) ? pouch.contents : []);
    if (!items.length) return;
    data.inventory ||= {};
    data.inventory.backpack ||= [];
    data.inventory.backpack.push(...items);
    for (const pouch of pouches) if (Array.isArray(pouch?.contents)) pouch.contents = [];
}

export function applyVestTemplate(data, template) {
    data.equipment ||= {};
    const previous = data.equipment.vest || {};
    returnPouchContents(data, previous.pouches);
    data.equipment.vest = {
        ...previous, templateId: template.id, model: String(template.id), name: template.name,
        attributes: structuredClone(template.attributes || {}), description: template.description || '',
        price: template.price ?? 0,
        weight: template.weight ?? 0, volume: template.volume ?? 0,
        pouches: vestPouches(template), totalCapacity: template.attributes?.total_capacity ?? 0,
    };
}

export function vestMountVolume(vest, templates = []) {
    const attrs = vest.attributes || {};
    return (vest.pouches || []).reduce((total, pouch) => {
        const template = templates.find(item => Number(item.id) === Number(pouch.type));
        const base = Number(pouch.attributes?.mount_volume ?? pouch.volume
            ?? template?.attributes?.mount_volume ?? template?.volume ?? 0);
        const kind = pouch.subcategory || template?.subcategory || pouch.name || '';
        const discount = attrs.discount_pouch_type;
        const multiplier = discount && kind.startsWith(discount) ? Number(attrs.pouch_volume_multiplier ?? 1) : 1;
        return total + (Number.isFinite(base) && Number.isFinite(multiplier) ? Math.max(0, base * multiplier) : 0);
    }, 0);
}

export function pouchCapacity(pouch, belt = null) {
    const base = Number(pouch?.internalVolume ?? pouch?.capacity ?? pouch?.attributes?.capacity ?? 0);
    if (!Number.isFinite(base) || base < 0) return 0;
    if (!belt || belt.protectionDisabled || pouch?.subcategory !== 'Медицинский') return base;
    const factor = (belt.modifications || [])
        .filter(record => record?.ruleId === 'armor-g66')
        .reduce((maximum, record) => Math.max(maximum, Number(record.rules?.medical_pouch_capacity_multiplier) || 1), 1);
    return Math.floor(base * factor);
}
