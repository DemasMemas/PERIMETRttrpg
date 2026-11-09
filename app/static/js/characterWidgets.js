import { Server } from './api.js';
import { showNotification } from './utils.js';
import { initDraggablePanels } from './ui_interactions.js';
import { characterWidgetBlocks } from './characterWidgetBlocks.js';

// Preferences contain references and presentation only. Character data stays on
// the existing API/sheet path; no character snapshot is persisted here.
const actions = new Set(['rollSkill', 'useWeaponFromEquipment', 'chooseMachineGunBurst', 'cycleWeaponFromEquipment',
    'aimWeaponFromEquipment', 'drawWeaponFromEquipment', 'useMeleeAttack',
    'equipMagazineToWeapon', 'reloadFixedMagazine', 'unloadFixedMagazine',
    'reloadGrenadeLauncher', 'fireGrenadeLauncher', 'clearWeaponJam']);

export const weaponMasteryLabels = {
    pistols: 'Пистолеты', shotguns: 'Дробовики', smgs: 'ПП', assaultRifles: 'Штурмовые',
    sniperRifles: 'Снайперские', grenadeLaunchers: 'Гранатометы', machineGuns: 'Пулеметы',
};

export function parseWidgetAction(text) {
    const match = String(text || '').trim().match(/^(?:window\.)?(\w+)\((.*)\);?$/);
    if (!match || !actions.has(match[1])) return null;
    const args = [];
    let rest = match[2].trim();
    while (rest) {
        const token = rest.match(/^(?:'([^'\\]*)'|"([^"\\]*)"|(-?\d+(?:\.\d+)?)|(true|false))/);
        if (!token) return null;
        args.push(token[1] ?? token[2] ?? (token[3] !== undefined ? Number(token[3]) : token[4] === 'true'));
        rest = rest.slice(token[0].length).trim();
        if (rest && (!rest.startsWith(',') || rest === ',')) return null;
        rest = rest.slice(rest.startsWith(',') ? 1 : 0).trim();
    }
    return { name: match[1], args };
}

export function widgetValue(data, path) {
    const parts = String(path || '').split('.');
    if (parts.some(part => ['__proto__', 'constructor', 'prototype'].includes(part))) return undefined;
    return parts.reduce((value, key) => value?.[key], data);
}

function canonicalWidgetReference(reference) {
    return reference.kind === 'block' && ['inventory-magazine', 'inventory-container'].includes(reference.block)
        ? { ...reference, block: 'inventory-item' } : reference;
}

export function widgetReferenceIdentity(reference) {
    return JSON.stringify([canonicalWidgetReference(reference).block, reference.binding?.identity || reference.path || null]);
}

export function itemBinding(data, path) {
    const match = path.match(/^(health\.effects\.\d+|weapons\.\d+|inventory\.(?:pockets|backpack)\.\d+(?:\.contents\.\d+)*|equipment\.[^.]+(?:\.pouches\.\d+(?:\.contents\.\d+)*)?)/);
    if (!match) return null;
    const item = widgetValue(data, match[1]);
    if (!item || typeof item !== 'object') return null;
    const identity = item.id != null ? { id: item.id }
        : Object.fromEntries((match[1].startsWith('health.effects.') ? ['type', 'area', 'name', 'source'] : ['templateId', 'name', 'model', 'category'])
            .filter(key => item[key] != null).map(key => [key, item[key]]));
    return Object.keys(identity).length ? { root: match[1], identity } : null;
}

export function resolveWidgetReference(reference, data) {
    reference = canonicalWidgetReference(reference);
    const binding = reference.binding;
    if (!binding) return reference;
    const matches = item => item && Object.entries(binding.identity).every(([key, value]) => item[key] === value);
    let root = binding.root;
    if (root.startsWith('inventory.') || /^equipment\.[^.]+\.pouches\./.test(root)) {
        const candidates = [];
        const visit = (value, path) => {
            if (!value || typeof value !== 'object') return;
            if (!Array.isArray(value) && matches(value)) candidates.push(path);
            Object.entries(value).forEach(([key, child]) => visit(child, `${path}.${key}`));
        };
        visit(data.inventory, 'inventory'); visit(data.equipment, 'equipment');
        if (candidates.length !== 1) return null;
        root = candidates[0];
    } else if (root.startsWith('health.effects.')) {
        const indices = (data.health?.effects || []).map((item, index) => matches(item) ? index : -1).filter(index => index >= 0);
        if (indices.length !== 1) return null;
        root = `health.effects.${indices[0]}`;
    } else if (root.startsWith('weapons.')) {
        const indices = (data.weapons || []).map((item, index) => matches(item) ? index : -1).filter(index => index >= 0);
        if (indices.length !== 1) return null; // Never redirect an ambiguous or removed item.
        root = `weapons.${indices[0]}`;
    } else if (!matches(widgetValue(data, root))) return null;
    const result = { ...reference };
    if (result.path) result.path = result.path.replace(binding.root, root);
    if (result.action && root.startsWith('weapons.')) {
        result.action = { ...result.action, args: [Number(root.split('.')[1]), ...result.action.args.slice(1)] };
    }
    return result;
}

export function readWidgetPreferences(storage, key) {
    try {
        const saved = JSON.parse(storage.getItem(key));
        const pins = Array.isArray(saved) ? saved.filter(pin => pin && Number.isSafeInteger(pin.characterId)
            && pin.characterId > 0 && typeof pin.id === 'string' && /^[\w-]{1,80}$/.test(pin.id)
            && typeof pin.title === 'string' && Array.isArray(pin.references) && pin.references.length <= 80
            && pin.references.every(ref => ref && typeof ref.title === 'string'
                && (ref.kind === 'block' ? typeof ref.block === 'string' && ref.block.length < 120 && !['loadout', 'inventory-actions'].includes(ref.block)
                    : ref.kind === 'action' ? actions.has(ref.action?.name) && Array.isArray(ref.action.args)
                    : ['field', 'ammo', 'readout'].includes(ref.kind) && typeof ref.path === 'string')
                && (!ref.binding || typeof ref.binding.root === 'string' && ref.binding.identity
                    && typeof ref.binding.identity === 'object'))).slice(0, 50) : [];
        const seenItems = new Set();
        return pins.flatMap(pin => {
            if (pin.references.length === 1 && pin.references[0].block === 'weapon-mastery') {
                return Object.entries(weaponMasteryLabels).map(([key, label], index) => ({
                    ...pin, id: index === 0 ? pin.id : `${pin.id.slice(0, 55)}-${key}`, title: `Владение: ${label}`,
                    references: [{ kind: 'block', block: `weapon-mastery:${key}`, title: `Владение: ${label}` }],
                }));
            }
            return [{ ...pin, references: pin.references.map(canonicalWidgetReference) }];
        }).filter(pin => {
            if (pin.references.length !== 1 || pin.references[0].block !== 'inventory-item' && !pin.references[0].block?.startsWith('weapon-mastery:')) return true;
            const identity = JSON.stringify([pin.characterId, widgetReferenceIdentity(pin.references[0])]);
            if (seenItems.has(identity)) return false;
            seenItems.add(identity); return true;
        }).slice(0, 50);
    } catch { return []; }
}

export function getWidgetSource(root, reference) {
    if (reference.kind === 'field') {
        return [...root.querySelectorAll('input[name],select[name],textarea[name]')]
            .find(control => control.name === reference.path);
    }
    if (reference.kind === 'action') {
        const identity = action => {
            const args = [...action.args];
            // These arguments are prices rendered by the current sheet, not
            // the identity of a pinned attack. Always take their fresh values.
            if (action.name === 'useWeaponFromEquipment') args[3] = null;
            if (action.name === 'chooseMachineGunBurst') args[2] = null;
            return JSON.stringify([action.name, args]);
        };
        return [...root.querySelectorAll('[onclick]')].find(control => {
            const action = parseWidgetAction(control.getAttribute('onclick'));
            return action && identity(action) === identity(reference.action);
        });
    }
    return null;
}

let controller;
export function initCharacterWidgets(lobbyId, bridge) {
    if (controller) return controller;
    const key = `character-widgets:${lobbyId}:${localStorage.getItem('user_id')}`;
    let pins = readWidgetPreferences(localStorage, key).flatMap(pin => {
        if (pin.references.every(ref => ref.kind === 'block')) return [pin];
        // Upgrade the previous field pins to the agreed whole blocks.
        const first = pin.references[0];
        let block = null, path, binding = first?.binding;
        const field = first?.path || '';
        if (/^health\.(current|max)$/.test(field)) block = 'health-pool';
        else if (/^health\.(blood|stress|painLevel|exhaustion|radiation)$/.test(field)) block = 'health-status';
        else if (/^health\.(food|water)/.test(field)) block = 'needs';
        else if (/^health\.zones\.[^.]+/.test(field)) { path = field.split('.').slice(0,3).join('.'); block = 'zone:' + path.split('.')[2]; }
        else if (binding?.root.startsWith('weapons.')) { block = 'weapon'; path = binding.root; }
        else if (/^equipment\.[^.]+/.test(field)) { path = field.split('.').slice(0,2).join('.'); block = 'equipment:' + path.split('.')[1]; }
        else if (first?.action?.name === 'rollSkill') block = 'skill:' + first.action.args[0];
        else if (field === 'inventory.money') block = 'money';
        if (!block) return [];
        return [{ ...pin, references: [{ kind: 'block', block, path, binding, title: pin.title }] }];
    }).filter(pin => pin.references.every(ref => !['loadout', 'inventory-actions'].includes(ref.block)));
    const records = new Map(), pending = new Set(), refreshAgain = new Set(), joined = new Set();
    let busy = false, socket;
    const views = new Map(), viewVersions = new Map();
    let work = Promise.resolve();
    const serialize = task => { const next = work.then(task); work = next.catch(() => {}); return next; };
    const host = document.createElement('div'); host.id = 'character-widgets';
    document.querySelector('.ui-overlay').appendChild(host);
    const save = () => {
        try { localStorage.setItem(key, JSON.stringify(pins)); } catch { showNotification('Не удалось сохранить расположение виджетов'); }
    };
    const subscribe = () => {
        socket = bridge.socket();
        if (!socket?.connected) return;
        const ids = new Set(pins.map(pin => pin.characterId));
        ids.forEach(id => {
            if (!joined.has(id)) {
                joined.add(id);
                socket.emit('join_character', { token: localStorage.getItem('access_token'), character_id: id });
            }
        });
        joined.forEach(id => {
            if (!ids.has(id) && !(Number(bridge.context().id) === id && document.body.classList.contains('character-sheet-open'))) {
                socket.emit('leave_character', { token: localStorage.getItem('access_token'), character_id: id });
                joined.delete(id);
                records.delete(id);
                viewVersions.delete(id);
            }
        });
    };
    const refresh = async id => {
        if (!pins.some(pin => pin.characterId === id)) return;
        if (pending.has(id)) { refreshAgain.add(id); return; }
        pending.add(id);
        try {
            const record = await Server.getCharacter(id);
            records.set(id, record);
            await serialize(async () => {
                const legacy = pins.filter(pin => pin.characterId === id && pin.references.length === 1
                    && pin.references[0].block === 'equipment:pda');
                for (const pin of legacy) {
                    const reference = bridge.legacyPda(record.data || {}, pin.references[0]);
                    if (!reference) continue;
                    const identity = widgetReferenceIdentity(reference);
                    if (pins.some(other => other.characterId === id && other.references.length === 1
                        && widgetReferenceIdentity(other.references[0]) === identity)) {
                        pins = pins.filter(other => other !== pin);
                    } else {
                        pin.references = [reference];
                        pin.title = reference.title;
                    }
                    save();
                }
                const selected = pins.filter(pin => pin.characterId === id);
                const version = JSON.stringify([record.data, record.can_edit, selected.map(pin => pin.id), window.locationCombatState, Boolean(window.isLocationActive)]);
                if (viewVersions.get(id) === version) return;
                const projected = await bridge.views(id, selected.map(pin => pin.references[0]), record);
                if (projected) {
                    selected.forEach((pin, index) => views.set(pin.id, projected[index]));
                    viewVersions.set(id, version);
                }
            });
        } catch {
            records.delete(id);
            viewVersions.delete(id);
            pins.filter(pin => pin.characterId === id).forEach(pin => views.delete(pin.id));
        }
        finally {
            pending.delete(id); render();
            if (refreshAgain.delete(id)) void refresh(id);
        }
    };
    const refreshAll = () => { subscribe(); new Set(pins.map(pin => pin.characterId)).forEach(refresh); };
    const run = async (pin, reference, value) => {
        if (busy) return;
        busy = true; render();
        try { await serialize(() => bridge.run(pin.characterId, reference, value)); }
        catch (error) { showNotification(error.message || 'Действие не выполнено'); }
        finally { busy = false; await refresh(pin.characterId); render(); }
    };
    function render() {
        host.querySelectorAll('[data-pin-id]').forEach(panel => {
            if (!pins.some(pin => pin.id === panel.dataset.pinId)) { views.delete(panel.dataset.pinId); panel._disposeDrag?.(); panel.remove(); }
        });
        pins.forEach((pin, index) => {
            let panel = [...host.children].find(element => element.dataset.pinId === pin.id);
            if (!panel) {
                panel = document.createElement('section'); panel.className = 'draggable-panel character-widget';
                panel.id = `character-widget-${pin.id}`; panel.dataset.pinId = pin.id; panel.dataset.layoutPending = 'true';
                panel.style.left = `${285 + index % 3 * 24}px`; panel.style.top = `${100 + index % 8 * 44}px`;
                const header = document.createElement('div'); header.className = 'panel-header';
                const title = document.createElement('span'); title.textContent = pin.title;
                const fold = document.createElement('span'); fold.className = 'toggle-btn'; fold.textContent = '▼';
                const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×';
                remove.title = 'Открепить'; remove.setAttribute('aria-label', `Открепить: ${pin.title}`);
                remove.onclick = () => {
                    pins = pins.filter(item => item.id !== pin.id); save(); render(); subscribe();
                };
                header.append(title, fold, remove);
                const content = document.createElement('div'); content.className = 'panel-content';
                panel.append(header, content); host.appendChild(panel);
            }
            panel.classList.toggle('character-widget-inventory',
                pin.references.some(ref => /^(inventory-(item|container|magazine)|pockets|belt|vest|backpack)$/.test(ref.block)));
            panel.querySelector('.panel-header > span').textContent = pin.title;
            panel.querySelector('.panel-header > button').setAttribute('aria-label', `Открепить: ${pin.title}`);
            const content = panel.querySelector('.panel-content');
            const record = records.get(pin.characterId);
            // Preserve an input currently being edited during a remote refresh.
            if (record?.can_edit === true && content.contains(document.activeElement) && !busy) return;
            content.replaceChildren();
            if (!record) { content.textContent = pending.has(pin.characterId) ? 'Загрузка…' : 'Персонаж недоступен'; return; }
            const name = document.createElement('button'); name.type = 'button'; name.className = 'widget-character-name'; name.textContent = record.name;
            name.onclick = () => window.openCharacterSheet(pin.characterId);
            content.appendChild(name);
            pin.references.forEach(original => {
                if (original.block === 'equipment:pda') {
                    const message = document.createElement('span');
                    message.textContent = 'Закрепите нужный КПК из инвентаря';
                    content.appendChild(message); return;
                }
                const reference = resolveWidgetReference(original, record.data || {});
                if (!reference) {
                    const message = document.createElement('span'); message.textContent = `${original.title}: предмет недоступен`;
                    content.appendChild(message); return;
                }
                if (reference.kind === 'block') {
                    const view = views.get(pin.id)?.cloneNode(true);
                    if (!view) {
                        const message = document.createElement('p');
                        message.textContent = views.has(pin.id) ? 'Блок недоступен' : 'Загрузка…';
                        content.appendChild(message); return;
                    }
                    view.querySelectorAll('[data-widget-control]').forEach(control => {
                        if (control.matches('input,select,textarea,button')) control.disabled ||= busy || record.can_edit !== true && !control.hasAttribute('data-sheet-control');
                    });
                    const invoke = (event, field) => {
                        const control = event.target.closest('[data-widget-control]');
                        if (!control || control.disabled || control.readOnly || !view.contains(control)) return;
                        if (control.matches('input,select,textarea') !== field) return;
                        if (event.target.closest('.sheet-pin')) return;
                        event.preventDefault(); event.stopPropagation();
                        void run(pin, { ...original, controlKey: control.dataset.widgetControl }, field ? control.type === 'checkbox' ? control.checked : control.value : undefined);
                    };
                    view.addEventListener('click', event => invoke(event, false));
                    view.addEventListener('change', event => invoke(event, true));
                    view.addEventListener('keydown', event => {
                        if (event.target.matches('[role="button"]') && ['Enter', ' '].includes(event.key)) invoke(event, false);
                    });
                    content.appendChild(view); return;
                }
                if (reference.kind === 'action') {
                    const button = document.createElement('button'); button.type = 'button'; button.textContent = reference.title;
                    button.disabled = busy || record.can_edit !== true;
                    button.onclick = () => run(pin, original);
                    content.appendChild(button); return;
                }
                const row = document.createElement('label'); row.className = 'widget-field';
                const label = document.createElement('span'); label.textContent = reference.title; row.appendChild(label);
                if (reference.kind === 'ammo' || reference.kind === 'readout') {
                    const weapon = widgetValue(record.data, reference.path);
                    const output = document.createElement('output'); output.textContent = String(reference.kind === 'ammo' ? bridge.ammoCount(weapon || {}) : weapon ?? 0); row.appendChild(output);
                } else {
                    const control = document.createElement(reference.control === 'select' ? 'select' : 'input');
                    if (control.tagName === 'SELECT') (reference.options || []).forEach(option => {
                        const element = document.createElement('option'); element.value = option.value; element.textContent = option.label; control.appendChild(element);
                    });
                    else control.type = reference.type === 'checkbox' ? 'checkbox' : reference.type === 'number' ? 'number' : 'text';
                    const raw = widgetValue(record.data, reference.path);
                    control.value = raw == null ? '' : String(reference.percent ? Math.round(Number(raw) * (Math.abs(Number(raw)) <= 1 ? 100 : 1)) : raw);
                    control.checked = Boolean(raw); control.setAttribute('aria-label', reference.title);
                    for (const attr of ['min', 'max', 'step']) if (reference[attr]) control.setAttribute(attr, reference[attr]);
                    control.disabled = busy || record.can_edit !== true || reference.readOnly;
                    control.addEventListener('change', () => run(pin, original, control.type === 'checkbox' ? control.checked : control.value));
                    row.appendChild(control);
                }
                content.appendChild(row);
            });
            if (panel.dataset.layoutPending === 'true' && views.get(pin.id)) {
                panel.dataset.layoutPending = 'false';
                let saved;
                try { saved = JSON.parse(localStorage.getItem('panelStates') || '{}')[panel.id]; } catch { /* default placement */ }
                if (!saved?.position) {
                    const start = window.innerWidth > 900 ? 285 : 15;
                    const columns = Math.max(1, Math.min(3, Math.floor((window.innerWidth - start - 15) / 346)));
                    const bottoms = Array(columns).fill(95);
                    [...host.children].filter(other => other !== panel && other.dataset.layoutColumn !== undefined).forEach(other => {
                        const column = Number(other.dataset.layoutColumn);
                        const span = Number(other.dataset.layoutSpan || 1);
                        for (let offset = 0; offset < span && column + offset < columns; offset++) {
                            bottoms[column + offset] = Math.max(bottoms[column + offset], other.getBoundingClientRect().bottom);
                        }
                    });
                    const span = Math.min(columns, Math.ceil(panel.getBoundingClientRect().width / 346));
                    const levels = bottoms.slice(0, columns - span + 1).map((_, column) => Math.max(...bottoms.slice(column, column + span)));
                    const column = levels.indexOf(Math.min(...levels)); panel.dataset.layoutColumn = String(column);
                    panel.dataset.layoutSpan = String(span);
                    panel.style.left = `${start + column * 346}px`;
                    panel.style.top = `${Math.min(levels[column] + 10, Math.max(95, window.innerHeight - 100))}px`;
                }
            }
        });
        initDraggablePanels();
    }
    const pin = (references, title) => {
        const context = bridge.context();
        if (!context.id || !references.length) return;
        const identity = JSON.stringify([Number(context.id), references.map(widgetReferenceIdentity)]);
        const existing = pins.find(item => JSON.stringify([item.characterId, item.references.map(widgetReferenceIdentity)]) === identity);
        if (existing) {
            pins = pins.filter(item => item !== existing); save(); render(); subscribe(); return false;
        }
        if (pins.length >= 50) { showNotification('Можно закрепить до 50 виджетов'); return; }
        pins.push({ id: crypto.randomUUID?.() || `pin-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`, characterId: Number(context.id), title, references });
        save(); render(); refreshAll(); return true;
    };
    let decorateFrame = null;
    const decorate = () => {
        const context = bridge.context(); if (!context.data || !document.body.classList.contains('character-sheet-open')) return;
        const root = document.getElementById('sheet-content');
        const groups = new Map();
        characterWidgetBlocks(root, context.data).forEach(entry => {
            const anchor = entry.anchor || entry.element;
            if (!groups.has(anchor)) groups.set(anchor, []);
            groups.get(anchor).push(entry);
        });
        groups.forEach((entries, anchor) => {
            if (anchor.dataset.pinBlockDecorated) return;
            anchor.dataset.pinBlockDecorated = 'true';
            anchor.classList.add('sheet-pinnable-block');
            const button = document.createElement('button'); button.type = 'button'; button.className = 'sheet-pin';
            if (entries.some(entry => entry.inlinePin)) {
                anchor.classList.add('sheet-inline-pin-anchor');
                button.classList.add('sheet-pin-inline');
            }
            button.dataset.sheetControl = '';
            button.dataset.pinBlock = entries[0].reference.block;
            button.dataset.pinBlocks = entries.map(entry => entry.reference.block).join(' ');
            const pinIcon = '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="m8 3 8 0-1 7 4 4v2h-6v6h-2v-6H5v-2l4-4z" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>';
            const checkIcon = '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="m5 12 4 4L19 6" fill="none" stroke="currentColor" stroke-width="2"/></svg>';
            const selected = entry => pins.some(item => item.characterId === Number(context.id)
                && widgetReferenceIdentity(item.references[0]) === widgetReferenceIdentity(entry.reference));
            const sync = () => {
                const active = entries.some(selected);
                button.innerHTML = active ? checkIcon : pinIcon;
                button.title = entries.length > 1 ? 'Закрепить блок' : `${selected(entries[0]) ? 'Открепить' : 'Закрепить'}: ${entries[0].reference.title}`;
                button.setAttribute('aria-label', button.title);
                button.setAttribute('aria-pressed', String(active));
            };
            sync();
            button.onclick = event => {
                event.preventDefault(); event.stopPropagation();
                if (entries.length === 1) { pin([entries[0].reference], entries[0].reference.title); sync(); return; }
                anchor.querySelector(':scope > .sheet-pin-menu')?.remove();
                const menu = document.createElement('div'); menu.className = 'sheet-pin-menu'; menu.setAttribute('role', 'menu');
                entries.forEach(entry => {
                    const option = document.createElement('button'); option.type = 'button'; option.dataset.sheetControl = '';
                    option.dataset.pinBlock = entry.reference.block;
                    option.textContent = `${selected(entry) ? '✓ ' : ''}${entry.reference.title}`;
                    option.onclick = event => { event.preventDefault(); event.stopPropagation(); pin([entry.reference], entry.reference.title); menu.remove(); sync(); };
                    menu.appendChild(option);
                });
                anchor.appendChild(menu);
            };
            (anchor.matches('details') ? anchor.querySelector(':scope > summary') || anchor : anchor).appendChild(button);
        });
    };
    const observer = new MutationObserver(() => {
        if (decorateFrame !== null) return;
        decorateFrame = requestAnimationFrame(() => { decorateFrame = null; decorate(); });
    });
    observer.observe(document.getElementById('sheet-content'), { childList: true, subtree: true });
    document.addEventListener('character-sheet-opened', decorate);
    document.addEventListener('character-sheet-closed', () => { joined.clear(); refreshAll(); });
    window.addEventListener('focus', refreshAll);
    window.addEventListener('combat-state-updated', refreshAll);
    window.addEventListener('storage', event => { if (event.key === key) { pins = readWidgetPreferences(localStorage, key); render(); refreshAll(); } });
    // Revalidate access even after the server removes a revoked subscription.
    setInterval(() => { if (pins.length && document.visibilityState === 'visible') refreshAll(); }, 15000);
    controller = { refresh: refreshAll, decorate };
    const connect = () => {
        socket = bridge.socket(); if (!socket) return;
        socket.on('character_data_updated', data => refresh(Number(data.character_id)));
        socket.on('character_updated', refreshAll);
        socket.on('connect', () => { joined.clear(); refreshAll(); });
        refreshAll();
    };
    // initSocket follows setCurrentLobbyId in the existing startup sequence.
    queueMicrotask(connect); render();
    return controller;
}
