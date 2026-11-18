import { widgetValue, itemBinding, resolveWidgetReference, weaponMasteryLabels } from './characterWidgets.js';

// This is the agreed list of pinnable views, not a second character model.
// Every view is projected from the normal, authorized character sheet.
export function characterWidgetBlocks(root, data) {
    const result = [];
    const add = (block, title, element, extra = {}) => {
        if (element) result.push({ reference: { kind: 'block', block, title, ...extra }, element, nodes: [element] });
    };
    const q = selector => root.querySelector(selector);
    const field = name => [...root.querySelectorAll('[name]')].find(control => control.name === name);
    add('health-pool', 'Общий пул ОЗ', q('.health-field-pool'));
    add('health-status', 'Состояние', q('.health-vitals-grid'));
    add('blood-check', 'Проверка кровопотери', q('[data-sheet-panel="blood-check"]'));
    add('infection', 'Заражение крови', q('[data-sheet-panel="infection"]'));
    add('needs', 'Еда и вода', q('.health-needs-panel'));
    add('organs', 'Повреждённые органы', q('[data-sheet-panel="organs"]'));
    add('health-summary', 'Активные эффекты и состояния', q('[data-widget-block="health-summary"]')?.parentElement);
    add('effects', 'Эффекты', q('[data-widget-block="effects"]'));
    add('addictions', 'Зависимости', q('[data-sheet-panel="addictions"]'));
    const zones = { head: 'Голова', chest: 'Грудь', abdomen: 'Живот', leftArm: 'Левая рука', rightArm: 'Правая рука', leftLeg: 'Левая нога', rightLeg: 'Правая нога' };
    Object.entries(zones).forEach(([key, title]) => add(`zone:${key}`, title,
        field(`health.zones.${key}.current`)?.closest('.zone-item-vertical'), { path: `health.zones.${key}` }));
    root.querySelectorAll('[data-widget-effect-index]').forEach(element => {
        const path = `health.effects.${element.dataset.widgetEffectIndex}`;
        const effect = widgetValue(data, path);
        add('effect', element.querySelector('select')?.selectedOptions[0]?.textContent || element.querySelector('strong')?.textContent || 'Эффект', element,
            { path, binding: itemBinding(data, path) });
    });
    const seenSkills = new Set();
    root.querySelectorAll('[onclick^="window.rollSkill("]').forEach(control => {
        const match = control.getAttribute('onclick').match(/rollSkill\('([^']+)', '([^']+)'\)/);
        if (!match || seenSkills.has(match[1])) return;
        seenSkills.add(match[1]);
        add(`skill:${match[1]}`, match[2], control.parentElement);
    });
    Object.entries(weaponMasteryLabels).forEach(([key, label]) => {
        add(`weapon-mastery:${key}`, `Владение: ${label}`, field(`skills.specialized.${key}.level`)?.parentElement);
    });
    root.querySelectorAll('[data-weapon-index]').forEach(element => {
        const index = Number(element.dataset.weaponIndex), path = `weapons.${index}`;
        const weapon = widgetValue(data, path);
        add('weapon', index === -1 ? 'Кулаки' : weapon?.name || 'Оружие', element,
            { path, binding: index >= 0 ? itemBinding(data, path) : null });
    });
    const labels = { helmet: 'Шлем', gasMask: 'Противогаз', armor: 'Броня', detector: 'Детектор', pda: 'Модификации КПК' };
    Object.entries(labels).forEach(([key, title]) => {
        const group = q(`[data-equipment-group="${key}"]`);
        const path = `equipment.${key}`;
        const extra = key === 'pda' ? {} : { path, binding: itemBinding(data, path) };
        add(`equipment:${key}`, title, group, extra);
        if (group?.querySelector('h4')) result.at(-1).anchor = group.querySelector('h4');
        if (!['helmet', 'gasMask', 'armor'].includes(key)) return;
        add(`protection:${key}`, `${title}: защита`, group?.querySelector('.equipment-protection-block'), extra);
        add(`coverage:${key}`, `${title}: зоны защиты`, group?.querySelector('.equipment-zones-block'), extra);
        add(`modifications:${key}`, `${title}: модификации`, group?.querySelector('.modifications-block'), extra);
        add(`slots:${key}`, `${title}: устройства и модули`, group?.querySelector('[data-widget-slots]'), extra);
        add(`filters:${key}`, `${title}: автозамена фильтров`, field(`${path}.autoReplaceFilters`)?.closest('label'), extra);
        if (key === 'armor') add('armor-containers', 'Контейнеры брони', group?.querySelector('[data-widget-block="armor-containers"]'), extra);
    });
    const cosmetics = { headphones: 'Наушники', glasses: 'Очки', gloves: 'Перчатки', mask: 'Маски', jewelry: 'Бижутерия' };
    Object.entries(cosmetics).forEach(([key, title]) => {
        const heading = [...(q('[data-equipment-group="cosmetic"]')?.querySelectorAll('h5') || [])].find(element => element.textContent.trim() === title);
        add(`cosmetic:${key}`, title, key === 'jewelry' ? heading?.parentElement : heading?.parentElement.parentElement,
            key === 'jewelry' ? {} : { path: `equipment.${key}`, binding: itemBinding(data, `equipment.${key}`) });
        if (heading && key !== 'jewelry') {
            result.at(-1).anchor = heading.parentElement;
            result.at(-1).inlinePin = true;
        }
    });
    const inventory = { 'inventory-weight': 'Вес и перемещение', pockets: 'Карманы', belt: 'Пояс', vest: 'Разгрузка', backpack: 'Рюкзак' };
    Object.entries(inventory).forEach(([key, title]) => add(key, title, q(`[data-widget-block="${key}"]`)));
    root.querySelectorAll('#sheet-tab-inventory [data-path]').forEach(element => {
        const path = element.dataset.path.split(',').join('.');
        const item = widgetValue(data, path);
        if (!item || Array.isArray(item)) return;
        const extra = { path, binding: itemBinding(data, path) };
        add('inventory-item', item.name || 'Предмет', element, extra);
    });
    add('money', 'Деньги', field('inventory.money')?.parentElement);
    return result;
}

export function findWidgetBlock(root, data, reference) {
    const resolved = resolveWidgetReference(reference, data);
    if (!resolved) return null;
    return characterWidgetBlocks(root, data).find(entry => entry.reference.block === resolved.block
        && (entry.reference.path || '') === (resolved.path || '')) || null;
}

function sourceControls(entry) {
    return entry.nodes.flatMap(node => [...(node.matches('input,select,textarea,button,[onclick]') ? [node] : []), ...node.querySelectorAll('input,select,textarea,button,[onclick]')])
        .filter(control => !control.closest('.sheet-pin,.sheet-pin-menu'));
}

export function blockControlKey(entry, control) {
    const inventoryItem = control.closest?.('[data-widget-identity]');
    const relative = text => {
        if (inventoryItem?.dataset.path) {
            const path = inventoryItem.dataset.path.split(',').join('.');
            const identity = `$inventory:${inventoryItem.dataset.widgetIdentity}`;
            text = text.replaceAll(path, identity).replaceAll(path.split('.').join(','), identity);
        }
        if (entry.reference.path) {
            text = text.replaceAll(entry.reference.path, '$item').replaceAll(entry.reference.path.split('.').join(','), '$item');
            const index = entry.reference.path.split('.').at(-1);
            if (/^-?\d+$/.test(index)) text = text.replace(new RegExp(`^(?:window\\.)?(\\w+\\()${index}(?=[,)])`), '$1$item');
        }
        return text;
    };
    if (control.name) return `field:${relative(control.name)}`;
    if (control.dataset.widgetKey) {
        return `control:${inventoryItem?.dataset.widgetIdentity || ''}:${control.dataset.widgetKey}`;
    }
    if (control.dataset.equipmentOpen) return `slot:${control.dataset.equipmentOpen}`;
    if (control.id) return `id:${control.id}`;
    if (control.getAttribute('onclick')) {
        let action = relative(control.getAttribute('onclick'));
        // Action prices are derived by the sheet and may change between renders.
        action = action.replace(/(useWeaponFromEquipment\([^,]+,[^,]+,[^,]+),[^)]+/, '$1,$cost')
            .replace(/(chooseMachineGunBurst\([^,]+,[^,]+),[^)]+/, '$1,$cost');
        return `click:${action}`;
    }
    return `${control.tagName}:${control.type || ''}:${control.textContent.trim()}:${sourceControls(entry).indexOf(control)}`;
}

export function widgetBlockControl(root, data, reference) {
    const entry = findWidgetBlock(root, data, reference);
    if (!entry) return null;
    if (reference.block.startsWith('skill:') && !reference.controlKey.startsWith('click:window.rollSkill(')) return null;
    if (reference.block.startsWith('weapon-mastery:') && !reference.controlKey.startsWith('click:addWeaponXp(')) return null;
    const matches = sourceControls(entry).filter(control => blockControlKey(entry, control) === reference.controlKey);
    if (matches.length === 1) return matches[0];
    // The skill label and dice icon intentionally invoke the same check.
    if (matches.length && reference.block.startsWith('skill:')
        && matches.every(control => control.getAttribute('onclick') === matches[0].getAttribute('onclick'))) return matches[0];
    return null;
}

export function projectWidgetBlock(entry) {
    const source = entry.element;
    const clone = source.cloneNode(true);
    const originals = [source, ...source.querySelectorAll('*')], copies = [clone, ...clone.querySelectorAll('*')];
    copies.forEach((element, index) => {
        const original = originals[index];
        if (original.matches('input,select,textarea,button,[onclick]')) element.dataset.widgetControl = blockControlKey(entry, original);
        if (original.matches('input,textarea,select')) {
            element.value = original.value; element.checked = original.checked;
            if (element.tagName === 'SELECT') [...element.options].forEach((option, i) => { option.selected = original.options[i].selected; option.defaultSelected = option.selected; });
        }
        if (original.matches('select[name$=".templateId"]') && original.value) element.dataset.itemTemplateId = original.value;
        for (const attr of [...element.attributes]) {
            if (/^on/i.test(attr.name) || ['id', 'name', 'for', 'draggable'].includes(attr.name)) element.removeAttribute(attr.name);
        }
        element.style.removeProperty('min-width'); element.style.removeProperty('width');
        if (element.tagName === 'DETAILS') element.open = true;
        if (element.getAttribute('type') === 'submit') element.type = 'button';
        if (original.matches('[onclick]') && !element.matches('button,input,select,textarea')) {
            element.setAttribute('role', 'button'); element.tabIndex = 0;
        }
    });
    clone.querySelectorAll('.sheet-pin,.sheet-block-pin,.sheet-pin-menu').forEach(element => element.remove());
    clone.querySelectorAll('.sheet-pinnable-field').forEach(element => element.classList.remove('sheet-pinnable-field'));
    clone.querySelectorAll('.container-contents').forEach(element => { element.style.display = 'block'; });
    // Item rows are also used inside backpacks and pouches. Add labels to the
    // projected view only; the normal sheet keeps its shared column headers.
    const items = [...(clone.matches('[data-path]') ? [clone] : []), ...clone.querySelectorAll('[data-path]')];
    items.forEach(item => {
        const row = item.firstElementChild;
        if (!row) return;
        row.classList.add('widget-inventory-row');
        row.firstElementChild?.classList.add('widget-inventory-name');
        for (const [key, title] of [['weight', 'Вес, кг'], ['volume', 'Объём'], ['quantity', 'Количество']]) {
            const control = [...row.querySelectorAll('[data-widget-key]')].find(element => element.dataset.widgetKey === key);
            if (!control) continue;
            const target = key === 'quantity' ? control.parentElement : control;
            const label = document.createElement(key === 'quantity' ? 'div' : 'label'); label.className = 'widget-inventory-field';
            const caption = document.createElement('span'); caption.textContent = title;
            target.before(label); label.append(caption, target);
            control.setAttribute('aria-label', title);
        }
    });
    clone.querySelectorAll('[data-volume-info]').forEach(element => { element.textContent = `Заполнено: ${element.textContent.replace('📦', '').trim()}`; });
    clone.querySelectorAll('div').forEach(element => {
        if (element.children.length === 5 && [...element.children].map(child => child.textContent.trim()).join('|') === 'Название|Вес|Объём|Кол-во|') element.remove();
    });
    const key = entry.reference.block;
    if (key === 'health-status') clone.querySelector('.health-field-pool')?.remove();
    if (key === 'organs') {
        clone.querySelectorAll('.zone-item-vertical').forEach(zone => {
            const inputs = zone.querySelectorAll('input');
            if (Number(inputs[0]?.value) >= Number(inputs[1]?.value)) zone.remove();
        });
        if (!clone.querySelector('.zone-item-vertical')) {
            const note = document.createElement('p'); note.textContent = 'Повреждённых органов нет'; clone.appendChild(note);
        }
    }
    if (key.startsWith('skill:')) {
        clone.querySelectorAll('button').forEach(button => button.remove());
        const checks = clone.querySelectorAll('[data-widget-control^="click:"]');
        [...checks].slice(1).forEach(control => control.remove());
        clone.querySelectorAll('span').forEach(span => { if (span.textContent.includes('Опыт:')) span.remove(); });
        clone.querySelectorAll('input').forEach(control => { control.readOnly = true; delete control.dataset.widgetControl; });
    }
    if (key.startsWith('weapon-mastery:')) clone.querySelectorAll('select').forEach(control => { control.disabled = true; delete control.dataset.widgetControl; });
    clone.classList.remove('sheet-tab-content', 'sheet-tab');
    clone.style.removeProperty('display');
    clone.classList.add('widget-block-view');
    return clone;
}
