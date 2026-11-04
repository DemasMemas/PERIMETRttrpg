import { itemTooltipModel, tooltipPosition } from './itemPresentation.js';
import { renderPortableLights,usesNightVision } from './portableLights.js';
import { renderProstheses,prostheticLimb } from './prostheses.js';
import { pairedFireOptions } from './pairedFire.js';
import { hearingModifier } from './hearingRules.js';
import { passiveTraits, traitCount, backgroundIs, addLearningXp, backpackItemVolume, pocketCapacity,facialProfile,carriedWeaponEntries,ruleText } from './characterTraits.js';
// static/js/characterSheet.js
/*
 * ===================================================================
 *                    ЛИСТ ПЕРСОНАЖА (characterSheet.js)
 * ===================================================================
 *
 * ОГЛАВЛЕНИЕ:
 * 1. Состояние и утилиты (переменные, escapeHtml, setValueByPath, scheduleAutoSave)
 * 2. Рендеринг листа и переключение вкладок (renderCharacterSheet, switchSheetTab)
 * 3. Вкладка "Основное" (renderBasicTab) + предыстория, контейнеры
 * 4. Вкладка "Навыки" (renderSkillsTab) + особые черты
 * 5. Вкладка "Экипировка" (renderEquipmentTab) + оружие, броня, шлем, противогаз
 * 6. Вкладка "Инвентарь" (renderInventoryTab) + карманы, пояс, разгрузка, рюкзак
 * 7. Вкладка "Заметки" (renderNotesTab)
 * 8. Вкладка "Настройки" (renderSettingsTab) + видимость, удаление
 * 9. Публичные функции (openCharacterSheet, closeCharacterSheet, export/import)
 * 10. Вспомогательные функции для UI (добавление/удаление предметов, оружия, модификаций)
 * 11. Модальные окна создания кастомных шаблонов (сохранение в БД)
 * 12. Вкладка "Здоровье" (renderHealthTab)
 */

import { Server } from './api.js';
import { showNotification } from './utils.js';
import { lobbyParticipants } from './ui.js';
import { getSocket } from './socketHandlers.js';
import { consumeChangedSheetInputs, rememberCharacterSnapshots } from './characterPersistence.js';
import { diffCharacterData, pathTouches } from './characterSheetSync.js';
import { isWorldDarkTime } from './darknessRules.js';
import { copyProfileComponent, profileComponentOptions, renderConsumableProfileTree } from './consumableProfileEditor.js';
import { applyEffectToHealth, createEffectDraft, effectSummary, effectDurationLabel, effectConsequences, getEffectTypeOptions, isAlcoholConsumable, normalizeCharacterEffects, normalizeEffectList, getEffectImpact, syncHealthDerivedStatuses } from './effects.js';
import { initCharacterWidgets, getWidgetSource, resolveWidgetReference, parseWidgetAction } from './characterWidgets.js';
import { characterWidgetBlocks, findWidgetBlock, projectWidgetBlock, widgetBlockControl } from './characterWidgetBlocks.js';

// ========== 1. СОСТОЯНИЕ И УТИЛИТЫ ==========
let currentCharacterId = null;
let currentCharacterData = null;
let currentCharacterCanEdit = false;
let sheetSaveBlocked = false;
let medicalOperationInProgress = false;
let medicalRemoteUpdate = null;
let pendingSheetRemoteUpdate = null;
let remoteSheetUpdateQueue = Promise.resolve();
let autoSaveTimer = null;
let stressAdjustmentPending = false;
const pendingManualFieldPaths = new Set();
const AUTO_SAVE_DELAY = 500;
const pendingWeaponJamActions = new Map();
let equipmentRenderVersion = 0;
let equipmentOpenPanels = new Set();
let sheetSocketHandler = null;
let widgetOperation = false;
let widgetContext = false;

// ========== DRAG-AND-DROP ==========
let draggedItem = null;
let draggedItemPath = null;

function isDescendant(containerPath, itemPath) {
    if (containerPath.length >= itemPath.length) return false;
    for (let i = 0; i < containerPath.length; i++) {
        if (containerPath[i] !== itemPath[i]) return false;
    }
    return true;
}

let vestTemplateEditorPouches = [];

// Кеш шаблонов для текущей комнаты
let templatesCache = {};
let currentLobbyId = null;
export function setCurrentLobbyId(id) {
    if (currentLobbyId !== id) allTemplatesCache = null;
    currentLobbyId = id;
    initCharacterWidgets(id, {
        socket: getSocket,
        context: () => ({ id: currentCharacterId, data: currentCharacterData }),
        ammoCount: getWeaponAmmoCount,
        run: runCharacterWidget,
        views: characterWidgetViews,
    });
}

async function characterWidgetViews(characterId, references, character) {
    const foreground = document.body.classList.contains('character-sheet-open');
    if (foreground && Number(currentCharacterId) !== Number(characterId)) return null;
    if (!foreground && (widgetOperation || medicalOperationInProgress || widgetDialogOpen())) return null;
    if (!foreground) widgetOperation = true;
    try {
        if (!foreground) {
            if (autoSaveTimer) await forceSyncCharacter();
            if (currentCharacterId) await Server.waitForCharacterSave(currentCharacterId);
            await remoteSheetUpdateQueue;
            await openCharacterSheet(characterId, 'basic', { background: true, snapshot: true, character });
        }
        const root = document.getElementById('sheet-content');
        return references.map(reference => {
            const entry = findWidgetBlock(root, currentCharacterData, reference);
            return entry ? projectWidgetBlock(entry) : null;
        });
    } finally { if (!foreground) widgetOperation = false; }
}

async function invokeWidgetControl(source, value) {
    if (source.matches('input,select,textarea')) {
        if (source.type === 'checkbox') source.checked = Boolean(value);
        else source.value = String(value ?? '');
        if (!source.checkValidity()) throw new Error(source.validationMessage || 'Некорректное значение');
        for (const type of ['input', 'change']) {
            const handler = source[`on${type}`], event = new Event(type, { bubbles: true });
            source[`on${type}`] = null;
            try { source.dispatchEvent(event); if (handler) await handler.call(source, event); }
            finally { source[`on${type}`] = handler; }
        }
        const path = source.name || '';
        if (path === 'health.current' || /^health\.zones\.[^.]+\.(current|max)$/.test(path) || /^skills\./.test(path)) pendingManualFieldPaths.add(path);
        await forceSyncCharacter();
        return;
    }
    // Observe the promise of the existing global handler even when its inline
    // onclick does not return it. No stored script or argument is evaluated.
    const name = source.getAttribute('onclick')?.match(/^(?:window\.)?(\w+)\(/)?.[1];
    const original = name && window[name];
    const promises = [];
    let wrapper;
    if (typeof original === 'function') {
        wrapper = function(...args) { const result = original.apply(this, args); promises.push(result); return result; };
        window[name] = wrapper;
    }
    const handler = source.onclick;
    let returned;
    if (handler) source.onclick = function(event) { returned = handler.call(this, event); return returned; };
    try { source.click(); }
    finally {
        if (handler) source.onclick = handler;
        if (wrapper && window[name] === wrapper) window[name] = original;
    }
    await Promise.all([returned, ...promises]);
    if (autoSaveTimer) await forceSyncCharacter();
}

function widgetDialogOpen() {
    return [...document.querySelectorAll('.modal')].some(modal => modal.id !== 'character-sheet-modal'
        && getComputedStyle(modal).display !== 'none' && modal.getClientRects().length > 0);
}

async function runCharacterWidget(characterId, original, value) {
    if (widgetOperation || medicalOperationInProgress || widgetDialogOpen()
        || document.body.classList.contains('character-sheet-open')) throw new Error('Сначала завершите текущее действие');
    widgetOperation = true;
    try {
        if (autoSaveTimer) await forceSyncCharacter();
        if (currentCharacterId) await Server.waitForCharacterSave(currentCharacterId);
        await openCharacterSheet(characterId, 'basic', { background: true });
        if (Number(currentCharacterId) !== Number(characterId) || sheetSaveBlocked) {
            throw new Error('Персонаж недоступен для изменения');
        }
        if (original.kind === 'block') {
            const source = widgetBlockControl(document.getElementById('sheet-content'), currentCharacterData, original);
            if (!source || source.disabled || source.readOnly || !currentCharacterCanEdit && !source.hasAttribute('data-sheet-control')) throw new Error('Этот элемент сейчас недоступен');
            widgetContext = true;
            if (source.dataset.equipmentOpen) {
                const panel = source.dataset.equipmentOpen;
                widgetOperation = false;
                await openCharacterSheet(characterId, 'equipment');
                document.querySelector(`#sheet-content [data-equipment-open="${CSS.escape(panel)}"]`)?.click();
                return;
            }
            await invokeWidgetControl(source, value);
            await Server.waitForCharacterSave(characterId);
            if (sheetSaveBlocked) throw new Error('Изменение не сохранено. Откройте лист персонажа.');
            return;
        }
        if (!currentCharacterCanEdit) throw new Error('Персонаж недоступен для изменения');
        const reference = resolveWidgetReference(original, currentCharacterData);
        const source = reference && getWidgetSource(document.getElementById('sheet-content'), reference);
        if (!source || source.disabled || source.readOnly) throw new Error('Этот элемент сейчас недоступен. Проверьте лист персонажа.');
        widgetContext = true;
        if (reference.kind === 'field') {
            if (source.type === 'checkbox') source.checked = Boolean(value);
            else source.value = String(value ?? '');
            if (!source.checkValidity()) throw new Error(source.validationMessage || 'Некорректное значение');
            if (source.oninput) await source.oninput(new Event('input', { bubbles: true }));
            if (source.onchange) await source.onchange(new Event('change', { bubbles: true }));
            if (reference.path === 'health.current' || /^health\.zones\.[^.]+\.(current|max)$/.test(reference.path) || /^skills\./.test(reference.path)) {
                pendingManualFieldPaths.add(reference.path);
            }
            await forceSyncCharacter();
            if (sheetSaveBlocked) throw new Error('Изменение не сохранено. Откройте лист персонажа.');
        } else {
            // Resolve only an action currently rendered by the authorized sheet.
            // Stored preferences never execute JavaScript or supply action arguments.
            const action = parseWidgetAction(source.getAttribute('onclick'));
            if (!action || typeof window[action.name] !== 'function') throw new Error('Действие недоступно');
            await window[action.name](...action.args);
            if (autoSaveTimer) await forceSyncCharacter();
        }
        await Server.waitForCharacterSave(characterId);
    } finally { widgetOperation = false; }
}

const skillCategories = [
    { label: 'Сила', path: 'physical.strength' },
    { label: 'Ловкость', path: 'physical.agility' },
    { label: 'Воля', path: 'physical.will' },
    { label: 'Внимательность', path: 'physical.awareness' },
    { label: 'Ближний бой', path: 'physical.melee' },
    { label: 'Стрельба', path: 'physical.shooting' },
    { label: 'Харизма', path: 'social.charisma' },
    { label: 'Бартер', path: 'social.barter' },
    { label: 'Убеждение', path: 'social.persuasion' },
    { label: 'Обман', path: 'social.deception' },
    { label: 'Устрашение', path: 'social.intimidation' },
    { label: 'Медицина', path: 'other.medicine' },
    { label: 'Инженерия', path: 'other.engineering' },
    { label: 'Скрытность', path: 'other.stealth' },
    { label: 'Тактика', path: 'other.tactics' },
    { label: 'Выживание', path: 'other.survival' }
];
const MATERIAL_OPTIONS = [
    'Текстиль',
    'Композит',
    'Кевлар',
    'Плита'
];
const MATERIAL_COEFFICIENTS = {
    'Текстиль': 0.5,
    'Композит': 1,
    'Кевлар': 1.5,
    'Плита': 2
};

// Универсальная загрузка шаблонов по категории
async function loadTemplatesForLobby(category, subcategory = null) {
    if (!currentLobbyId) throw new Error('Lobby ID not set');
    const cacheKey = `${currentLobbyId}_${category}_${subcategory || ''}`;
    if (templatesCache[cacheKey]) return templatesCache[cacheKey];

    const data = await Server.getLobbyTemplates(currentLobbyId, category, subcategory);
    const all = [
        ...data.global.map(t => ({ ...t, source: 'global' })),
        ...data.local.map(t => ({ ...t, id: t.id + 1_000_000, source: 'local' }))
    ];
    templatesCache[cacheKey] = all;
    return all;
}

function clearTemplatesCache(category) {
    if (category) {
        const keyPattern = `${currentLobbyId}_${category}`;
        Object.keys(templatesCache).forEach(key => {
            if (key.startsWith(keyPattern)) delete templatesCache[key];
        });
    } else {
        Object.keys(templatesCache).forEach(key => {
            if (key.startsWith(currentLobbyId + '_')) delete templatesCache[key];
        });
    }
}

let allTemplatesCache = null;

export function getCategoryDisplay(cat) {
    const map = {
        'weapon': 'Оружие',
        'melee_weapon': 'Оружие ближнего боя',
        'armor': 'Броня',
        'helmet': 'Шлемы',
        'gas_mask': 'Противогазы',
        'detector': 'Детекторы',
        'container': 'Контейнеры',
        'consumable': 'Расходники',
        'crafting_material': 'Материалы',
        'artifact': 'Артефакты',
        'backpack': 'Рюкзаки',
        'vest': 'Разгрузки',
        'pouch': 'Подсумки',
        'weapon_module': 'Оружейные модули',
        'magazine': 'Магазины',
        'ammo': 'Патроны',
        'gas_mask_module': 'Фильтры противогазов',
        'exoskeleton_module': 'Прочее',
        'helmet_module': 'Модули шлемов',
        'visor': 'Забрала',
        'belt': 'Пояс',
        'grenade': 'Гранаты',
        'device': 'Приборы',
        'armor_plate': 'Бронеплиты',
        'headphones': 'Наушники',
        'glasses': 'Очки',
        'gloves': 'Перчатки',
        'jewelry': 'Бижутерия',
        'tool': 'Инструменты',
        'food': 'Еда',
        'other': 'Прочее',
        'misc': 'Прочее'
    };
    return map[cat] || cat;
}

function formatAmmoPenetration(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) return '0%';
    if (num <= 1) return `${Math.round(num * 100)}%`;
    return `${Math.round(num)}%`;
}

function normalizeAmmoVariant(value) {
    const text = String(value || '').trim().toLowerCase();
    if (!text || text === 'нет' || text === 'обычные' || text === 'standard') return null;
    if (text.includes('убп')) return 'ubp';
    if (text === 'rip' || text.includes('rip')) return 'rip';
    if (text.includes('бронеб') || text === 'бп' || text === 'bp') return 'bp';
    if (text.includes('экспанс') || text === 'эп' || text === 'ep') return 'ep';
    if (text.includes('разрыв') || text.includes('взрыв')) return 'explosive';
    if (text.includes('зажиг')) return 'incendiary';
    if (text.includes('светошум')) return 'flashbang';
    if (text.includes('дым')) return 'smoke';
    if (text.includes('газ')) return 'gas';
    return text;
}

function getAmmoVariantLabel(value) {
    const variant = normalizeAmmoVariant(value);
    const map = {
        bp: 'БП',
        ep: 'ЭП',
        ubp: 'УБП',
        rip: 'RIP',
        explosive: 'Взрывные',
        incendiary: 'Зажигательные',
        flashbang: 'Светошумовые',
        smoke: 'Дымовые',
        gas: 'Газовые'
    };
    return variant ? (map[variant] || String(value)) : 'Обычные';
}

function normalizeAmmoVariants(value) {
    if (Array.isArray(value)) {
        return Array.from(new Set(value.map(normalizeAmmoVariant).filter(Boolean)));
    }
    if (!value) return [];
    return String(value)
        .split(/[,/;\n]+/)
        .map(part => normalizeAmmoVariant(part))
        .filter(Boolean)
        .filter((variant, index, array) => array.indexOf(variant) === index);
}

function getAmmoVariantLabels(value) {
    const variants = normalizeAmmoVariants(value);
    if (!variants.length) return 'Обычные';
    return variants.map(getAmmoVariantLabel).join(', ');
}

function getItemCaliber(item) {
    const isLoadingDevice = item?.category === 'magazine'
        && (
            item?.attributes?.isLoader === true
            || item?.attributes?.loadingDevice === true
            || /лента|спидлоадер/i.test(String(item?.name || ''))
        );
    if (isLoadingDevice) {
        const loadedAmmo = Array.isArray(item?.ammo) ? item.ammo.find(entry => entry?.quantity > 0) : null;
        const value = item?.attributes?.caliber
            ?? loadedAmmo?.attributes?.caliber
            ?? loadedAmmo?.caliber
            ?? '';
        return normalizeBaseCaliberText(value);
    }
    const value = item?.attributes?.caliber
        ?? item?.caliber
        ?? (item?.category === 'grenade' ? item?.name : null)
        ?? item?.subcategory
        ?? item?.attributes?.ammo_group
        ?? item?.attributes?.magazine_caliber
        ?? '';
    return normalizeBaseCaliberText(value);
}

function normalizeCaliberText(value) {
    return String(value || '')
        .trim()
        .toLowerCase()
        .replace(/^граната\s*/u, '')
        .replace(/аср/g, 'acp')
        .replace(/[×*хХ]/g, 'x')
        .replace(/\s+/g, '')
        .replace(/\./g, '')
        .replace(/[^a-zа-яё0-9x+-]/gu, '');
}

const AMMO_SLOT_LIMITS = new Map([
    ['9x18', 50], ['9x19', 50], ['9x21', 50], ['45acp', 50],
    ['545x39', 50], ['556x45', 50], ['18x45', 50], ['57x28', 50],
    ['762x25', 50], ['12x70', 25], ['762x39', 25], ['762x51', 25],
    ['762x54', 25], ['127x55', 25], ['9x39', 25], ['сп-4', 25], ['sp-4', 25],
]);

function getAmmoSlotLimit(item) {
    if (String(item?.category || '').toLowerCase() !== 'ammo') return null;
    return AMMO_SLOT_LIMITS.get(getItemCaliber(item)) || null;
}

function splitAmmoItemIntoSlotStacks(item, requestedQuantity = null) {
    if (String(item?.category || '').toLowerCase() !== 'ammo') return [item];
    const quantity = Math.max(0, Math.floor(Number(
        requestedQuantity ?? item?.quantity ?? 0
    ) || 0));
    const limit = getAmmoSlotLimit(item);
    if (!limit || quantity <= limit) {
        item.quantity = quantity;
        if (limit) updateAmmoWeight(item);
        return quantity > 0 ? [item] : [];
    }
    const stacks = [];
    let remaining = quantity;
    while (remaining > 0) {
        const stack = stacks.length === 0
            ? item
            : {
                ...item,
                id: generateItemId(),
                attributes: { ...(item.attributes || {}) },
                contents: Array.isArray(item.contents) ? [...item.contents] : [],
            };
        stack.quantity = Math.min(limit, remaining);
        updateAmmoWeight(stack);
        stacks.push(stack);
        remaining -= stack.quantity;
    }
    return stacks;
}

function normalizeAmmoSlotsInItemList(items) {
    if (!Array.isArray(items)) return;
    const normalized = [];
    items.forEach(item => {
        if (!item || typeof item !== 'object') {
            normalized.push(item);
            return;
        }
        if (Array.isArray(item.contents)) normalizeAmmoSlotsInItemList(item.contents);
        normalized.push(...splitAmmoItemIntoSlotStacks(item));
    });
    items.splice(0, items.length, ...normalized);
}

function normalizeCharacterAmmoSlots(data) {
    normalizeAmmoSlotsInItemList(data?.inventory?.pockets);
    normalizeAmmoSlotsInItemList(data?.inventory?.backpack);
    (data?.equipment?.belt?.pouches || []).forEach(pouch => normalizeAmmoSlotsInItemList(pouch?.contents));
    (data?.equipment?.vest?.pouches || []).forEach(pouch => normalizeAmmoSlotsInItemList(pouch?.contents));
}

function addAmmoToInventorySlots(targetItems, ammoItem, quantity) {
    if (!Array.isArray(targetItems) || !ammoItem) return;
    let remaining = Math.max(0, Math.floor(Number(quantity) || 0));
    const limit = getAmmoSlotLimit(ammoItem);
    const matching = targetItems.filter(item =>
        item?.category === 'ammo' && getAmmoStackKey(item) === getAmmoStackKey(ammoItem)
    );
    for (const stack of matching) {
        if (remaining <= 0) break;
        const capacity = limit ? Math.max(0, limit - Number(stack.quantity || 0)) : remaining;
        const moved = Math.min(remaining, capacity);
        stack.quantity = Number(stack.quantity || 0) + moved;
        updateAmmoWeight(stack);
        remaining -= moved;
    }
    while (remaining > 0) {
        const stack = {
            ...ammoItem,
            id: generateItemId(),
            attributes: { ...(ammoItem.attributes || {}) },
            contents: [],
            quantity: Math.min(limit || remaining, remaining),
        };
        updateAmmoWeight(stack);
        targetItems.push(stack);
        remaining -= stack.quantity;
    }
}

function normalizeBaseCaliberText(value) {
    const normalized = normalizeCaliberText(value);
    const numericCaliber = normalized.match(/^(\d+(?:\.\d+)?x\d+)/i);
    if (numericCaliber) return numericCaliber[1];
    return normalized;
}

const AMMO_VARIANT_MODIFIERS = {
    bp: { damagePercent: 100, penetrationDeltaPercent: 20 },
    ep: { damagePercent: 150, penetrationDeltaPercent: -15 },
    ubp: { damagePercent: 100, penetrationDeltaPercent: 35 },
    rip: { damagePercent: 300, penetrationDeltaPercent: -50 },
    explosive: { damagePercent: 300, penetrationDeltaPercent: -5 },
    incendiary: { damagePercent: 100, penetrationDeltaPercent: 0 },
    flashbang: { damagePercent: 100, penetrationDeltaPercent: -100 },
    smoke: { damagePercent: 0, penetrationDeltaPercent: -100 },
    gas: { damagePercent: 0, penetrationDeltaPercent: -100 }
};
const WEAPON_SUBCATEGORY_ORDER = [
    'Пистолеты',
    'Дробовики',
    'Пистолеты-пулеметы',
    'Штурмовые винтовки и карабины',
    'Снайперские винтовки',
    'Гранатометы',
    'Пулемёты',
    'Оружие ближнего боя',
];
const ITEM_CATEGORY_ORDER = [
    'Оружие', 'Оружие ближнего боя', 'Броня', 'Шлемы', 'Противогазы',
    'Магазины', 'Патроны', 'Гранаты', 'Оружейные модули', 'Бронеплиты',
    'Рюкзаки', 'Разгрузки', 'Пояс', 'Подсумки', 'Контейнеры',
    'Расходники', 'Приборы', 'Детекторы', 'Фильтры противогазов',
    'Модули шлемов', 'Артефакты', 'Материалы', 'Инструменты', 'Прочее',
];
const CONSUMABLE_SECTION_ORDER = [
    'Продукты', 'Кровь', 'Обезболивающее', 'Стимуляторы',
    'Восстановление здоровья', 'Радиация', 'Травмы', 'Прочее',
];

function compareRussianNames(left, right) {
    return String(left?.name || left || '').localeCompare(
        String(right?.name || right || ''),
        'ru',
        { sensitivity: 'base', numeric: true },
    );
}

function compareByFixedOrder(left, right, order) {
    const leftIndex = order.indexOf(left);
    const rightIndex = order.indexOf(right);
    if (leftIndex !== -1 || rightIndex !== -1) {
        if (leftIndex === -1) return 1;
        if (rightIndex === -1) return -1;
        return leftIndex - rightIndex;
    }
    return compareRussianNames(left, right);
}

function compareTemplatesBySourceOrder(left, right) {
    const leftOrder = Number(left?.attributes?.source_order);
    const rightOrder = Number(right?.attributes?.source_order);
    const leftHasOrder = Number.isFinite(leftOrder);
    const rightHasOrder = Number.isFinite(rightOrder);
    if (leftHasOrder || rightHasOrder) {
        if (!leftHasOrder) return 1;
        if (!rightHasOrder) return -1;
        if (leftOrder !== rightOrder) return leftOrder - rightOrder;
    }
    const leftId = Number(left?.id);
    const rightId = Number(right?.id);
    if (Number.isFinite(leftId) && Number.isFinite(rightId) && leftId !== rightId) {
        return leftId - rightId;
    }
    return 0;
}

function buildItemTooltipHtml(template, item = {}) {
    const firearm = (item.category || template?.category) === 'weapon' || item.type === 'firearm';
    const instance = firearm && Object.keys(item).length ? {...item, ammo:getWeaponAmmoCount(item)} : item;
    const model = itemTooltipModel(template || {}, instance);
    return `<strong class="item-tooltip-name">${escapeHtml(model.name)}</strong>
        <div class="item-tooltip-metrics">${model.metrics.map(text => `<span>${escapeHtml(text)}</span>`).join('')}</div>
        ${model.rules.length ? `<div class="item-tooltip-rules">${model.rules.map(text => `<p>${escapeHtml(text)}</p>`).join('')}</div>` : ''}`;
}

function initializeDelayedItemTooltips() {
    if (document._itemTooltipBound) return;
    document._itemTooltipBound = true;
    let timer = null;
    let hideTimer = null;
    let activeAnchor = null;
    let lastPointerDown = 0;
    let pointer = { x: 0, y: 0 };
    const tooltip = document.createElement('div');
    tooltip.id = 'delayed-item-tooltip';
    tooltip.setAttribute('role', 'tooltip');
    tooltip.style.cssText = 'display:none;position:fixed;z-index:20000;pointer-events:auto;width:max-content;max-width:min(400px,calc(100vw - 16px));max-height:calc(100vh - 16px);overflow:auto;overscroll-behavior:contain;padding:10px 12px;border:1px solid rgba(190,180,125,.45);border-radius:7px;background:rgba(18,20,17,.97);color:#ddd;box-shadow:0 12px 30px rgba(0,0,0,.55);';
    document.body.appendChild(tooltip);

    const hide = () => {
        clearTimeout(hideTimer);
        clearTimeout(timer);
        timer = null;
        activeAnchor = null;
        tooltip.classList.remove('equipment-slot-tooltip');
        tooltip.style.display = 'none';
    };
    const position = () => {
        if (!activeAnchor?.isConnected) return hide();
        const group = activeAnchor.closest('#pockets-container, #backpack-container, .container-contents');
        const rect = (group || activeAnchor).getBoundingClientRect();
        tooltip.style.maxHeight = `${window.innerHeight - 16}px`;
        const point = tooltipPosition(rect, tooltip.offsetWidth || 360, tooltip.offsetHeight || 180,
            {width:window.innerWidth, height:window.innerHeight});
        tooltip.style.left = `${point.left}px`;
        tooltip.style.top = `${point.top}px`;
        tooltip.style.maxHeight = `${point.maxHeight}px`;
    };
    const findAnchor = target => target?.closest?.('[data-item-template-id], [data-item-tooltip], select[name$=".templateId"], .loadout-slot');
    const schedule = anchor => {
        if (anchor) clearTimeout(hideTimer);
        if (!anchor || anchor === activeAnchor) return;
        hide();
        activeAnchor = anchor;
        timer = setTimeout(async () => {
            if (activeAnchor !== anchor || !anchor.isConnected) return;
            if (anchor.classList.contains('loadout-slot')) {
                const content = anchor.querySelector('.loadout-slot-tooltip');
                if (!content) return;
                tooltip.innerHTML = content.innerHTML;
                tooltip.classList.add('equipment-slot-tooltip');
                tooltip.style.display = 'block';
                position();
                return;
            }
            const templateId = Number(anchor.dataset.itemTemplateId || anchor.value);
            const templates = await getAllItemTemplates().catch(() => []);
            const template = templates.find(item => Number(item.id) === templateId);
            if (activeAnchor !== anchor || !anchor.isConnected) return;
            const path = anchor.dataset.path?.split(',');
            const item = path ? characterValueAtPath(currentCharacterData, path.join('.')) : null;
            if (!template && !item) return;
            tooltip.innerHTML = buildItemTooltipHtml(template, item || {});
            tooltip.classList.remove('equipment-slot-tooltip');
            tooltip.style.display = 'block';
            position();
        }, anchor.classList.contains('loadout-slot') ? 500 : 1000);
    };
    document.addEventListener('pointermove', (event) => {
        if (tooltip.contains(event.target)) return;
        pointer = { x: event.clientX, y: event.clientY };
        schedule(findAnchor(event.target));
        if (tooltip.style.display !== 'none') position();
    }, true);
    document.addEventListener('pointerover', (event) => {
        schedule(findAnchor(event.target));
    }, true);
    document.addEventListener('pointerout', (event) => {
        const anchor = findAnchor(event.target);
        if (!anchor || anchor !== activeAnchor) return;
        if (anchor.contains(event.relatedTarget) || tooltip.contains(event.relatedTarget)) return;
        hideTimer = setTimeout(hide, 180);
    }, true);
    document.addEventListener('focusin', event => {
        const anchor = findAnchor(event.target);
        if (!anchor || performance.now() - lastPointerDown < 500) return;
        const rect = anchor.getBoundingClientRect();
        pointer = { x: rect.right, y: rect.top };
        schedule(anchor);
    }, true);
    document.addEventListener('focusout', event => {
        if (event.target === activeAnchor) hide();
    }, true);
    document.addEventListener('keydown', event => { if (event.key === 'Escape') hide(); });
    document.addEventListener('pointerdown', event => {
        if (tooltip.contains(event.target)) return;
        lastPointerDown = performance.now();
        hide();
    }, true);
    tooltip.addEventListener('pointerenter', () => clearTimeout(hideTimer));
    tooltip.addEventListener('pointerleave', event => { if (!activeAnchor?.contains(event.relatedTarget)) hide(); });
    document.addEventListener('scroll', event => { if (!tooltip.contains(event.target)) hide(); }, true);
    window.addEventListener('resize', hide);
}

initializeDelayedItemTooltips();

function getAmmoVariantStats(baseDamage, basePenetration, variant) {
    const normalized = normalizeAmmoVariant(variant);
    const modifier = normalized ? AMMO_VARIANT_MODIFIERS[normalized] : null;
    if (!modifier) {
        return {
            damage: Math.max(0, Math.round(Number(baseDamage) || 0)),
            penetration: Math.max(0, Math.round((Number(basePenetration) || 0) * 100)),
        };
    }
    const damagePercent = Number.isFinite(Number(modifier.damagePercent)) ? Number(modifier.damagePercent) : 100;
    const penetrationDeltaPercent = Number.isFinite(Number(modifier.penetrationDeltaPercent)) ? Number(modifier.penetrationDeltaPercent) : 0;
    const damage = Math.max(0, Math.round((Number(baseDamage) || 0) * damagePercent / 100));
    const basePercent = Math.round((Number(basePenetration) || 0) * 100);
    const penetration = Math.max(0, Math.round(basePercent + penetrationDeltaPercent));
    return { damage, penetration };
}

function applyAmmoVariantToItem(item, template, variant) {
    if (!item || item.category !== 'ammo') return item;
    const normalized = normalizeAmmoVariant(variant);
    const baseDamage = Number(template?.attributes?.damage ?? item.attributes?.damage ?? item.damage ?? 0);
    const basePenetration = Number(template?.attributes?.penetration ?? item.attributes?.penetration ?? item.penetration ?? 0);
    const stats = getAmmoVariantStats(baseDamage, basePenetration, normalized);
    if (!item.attributes) item.attributes = {};
    item.attributes.ammo_variant = normalized || null;
    item.attributes.damage = stats.damage;
    item.attributes.penetration = stats.penetration / 100;
    item.damage = stats.damage;
    item.penetration = stats.penetration / 100;
    const baseName = template?.name || item.name || 'Патрон';
    item.name = normalized ? `${baseName} (${getAmmoVariantLabel(normalized)})` : baseName;
    return item;
}

window.closeInventoryTemplatePicker = function() {
    const modal = document.getElementById('inventory-template-picker-modal');
    if (modal) modal.style.display = 'none';
};

function bindBackdropClose(modal, closeFn) {
    if (!modal || modal._backdropCloseBound) return;
    const content = modal.querySelector('.modal-content');
    if (content) {
        ['pointerdown', 'mousedown', 'click'].forEach((eventName) => {
            content.addEventListener(eventName, (e) => e.stopPropagation());
        });
    }
    modal.addEventListener('mousedown', (e) => {
        if (e.target === modal) closeFn();
    });
    modal._backdropCloseBound = true;
}

async function getAllItemTemplates(forceRefresh = false) {
    if (!forceRefresh && allTemplatesCache) return allTemplatesCache;

    const categories = [
        'weapon', 'armor', 'helmet', 'gas_mask', 'detector', 'container',
        'consumable', 'crafting_material', 'artifact', 'backpack', 'vest', 'pouch',
        'weapon_module', 'magazine', 'ammo', 'gas_mask_module', 'helmet_module', 'visor', 'belt',
        'exoskeleton_module', 'grenade', 'device', 'armor_plate', 'melee_weapon',
        'headphones', 'glasses', 'gloves', 'jewelry', 'tool'
    ];

    let all = [];
    for (const cat of categories) {
        try {
            const templates = await loadTemplatesForLobby(cat);
            const normalizedTemplates = cat === 'ammo'
                ? templates.filter(t => t.source === 'global')
                : templates;
            all = all.concat(normalizedTemplates.map(t => ({
                ...t,
                categoryDisplay: getCategoryDisplay(cat),
                // Для удобства: вытаскиваем вес и объём из attributes или корня
                effectiveWeight: t.attributes?.weight !== undefined ? t.attributes.weight : (t.weight || 0),
                effectiveVolume: t.attributes?.volume !== undefined ? t.attributes.volume : (t.volume || 0)
            })));
        } catch (e) {
            console.warn(`Failed to load ${cat} templates`, e);
        }
    }
    allTemplatesCache = all;
    return all;
}

function clearAllTemplatesCache() {
    allTemplatesCache = null;
    const categories = ['weapon', 'armor', 'helmet', 'gas_mask', 'detector', 'container',
                        'consumable', 'crafting_material', 'artifact', 'modification', 'backpack', 'vest', 'pouch',
                        'weapon_module', 'ammo', 'exoskeleton_module', 'tool'];
    categories.forEach(cat => clearTemplatesCache(cat));
}

function getRequiredXp(level) {
    // Цена определяется достигаемым значением: 12 → 13 стоит 3 XP.
    return Math.max(1, Number(level) + 1 - 10);
}

function getSkillByPath(path) {
    const parts = path.split('.');
    let skill = currentCharacterData.skills;
    for (const part of parts) {
        if (!skill) return null;
        skill = skill[part];
    }
    return skill;
}

function checkAndLevelUpSkill(skill, skillPath) {
    while (true) {
        const required = getRequiredXp(skill.base);
        if (skill.xp >= required) {
            skill.xp -= required;
            skill.base = Number(skill.base) + 1;
            showNotification(`Навык ${skillPath.split('.').pop()} повышен до ${skill.base}!`, 'system');
        } else {
            break;
        }
    }
}

window.addSkillXpFromPoints = function(skillPath) {
    if (!currentCharacterCanEdit || sheetSaveBlocked) return;
    updateDataFromFields();
    if (!currentCharacterData.skills) currentCharacterData.skills = {};
    let skill = getSkillByPath(skillPath);
    if (!skill) return;

    let freePoints = currentCharacterData.skills.skillPoints ?? 30;
    if (freePoints <= 0) {
        showNotification('Нет свободных очков навыков');
        return;
    }

    currentCharacterData.skills.skillPoints = freePoints - 1;

    addLearningXp(currentCharacterData,skillPath,skill);

    checkAndLevelUpSkill(skill, skillPath);

    renderSkillsTab(currentCharacterData);
    scheduleAutoSave();
};

window.addSkillXpFromUse = function(skillPath) {
    if (!currentCharacterCanEdit || sheetSaveBlocked) return;
    updateDataFromFields();
    let skill = getSkillByPath(skillPath);
    if (!skill) return;

    addLearningXp(currentCharacterData,skillPath,skill);
    checkAndLevelUpSkill(skill, skillPath);

    renderSkillsTab(currentCharacterData);
    scheduleAutoSave();
};

window.addWeaponXp = function(weaponKey) {
    if (!currentCharacterCanEdit || sheetSaveBlocked) return;
    updateDataFromFields();
    if (!currentCharacterData.skills) currentCharacterData.skills = {};
    if (!currentCharacterData.skills.specialized) currentCharacterData.skills.specialized = {};

    let weapon = currentCharacterData.skills.specialized[weaponKey];
    if (!weapon) {
        weapon = { level: 'unfamiliar', xp: 0 };
        currentCharacterData.skills.specialized[weaponKey] = weapon;
    }

    if (weapon.level === 'professional') {
        showNotification('Уже профессионал, дальше не развивается');
        return;
    }

    const required = weapon.level === 'unfamiliar' ? 5 : 25;
    weapon.xp = (weapon.xp || 0) + 1;

    // Проверка на повышение (возможно, сразу несколько)
    while (true) {
        const currentLevel = weapon.level;
        const need = currentLevel === 'unfamiliar' ? 5 : (currentLevel === 'familiar' ? 25 : 0);
        if (need === 0) break;
        if (weapon.xp >= need) {
            weapon.xp -= need;
            if (currentLevel === 'unfamiliar') {
                weapon.level = 'familiar';
                showNotification(`Владение ${weaponKey} повышено до Знаком!`, 'system');
            } else if (currentLevel === 'familiar') {
                weapon.level = 'professional';
                showNotification(`Владение ${weaponKey} повышено до Профессионал!`, 'system');
            }
        } else {
            break;
        }
    }

    renderSkillsTab(currentCharacterData);
    scheduleAutoSave();
};

window.setWeaponLevel = function(weaponKey, newLevel) {
    if (!currentCharacterCanEdit || sheetSaveBlocked) return;
    updateDataFromFields();
    if (!currentCharacterData.skills) currentCharacterData.skills = {};
    if (!currentCharacterData.skills.specialized) currentCharacterData.skills.specialized = {};

    let weapon = currentCharacterData.skills.specialized[weaponKey];
    if (!weapon) weapon = {};

    weapon.level = newLevel;
    weapon.xp = 0; // при ручной смене уровня сбрасываем накопленный прогресс

    currentCharacterData.skills.specialized[weaponKey] = weapon;
    renderSkillsTab(currentCharacterData);
    scheduleAutoSave();
};

// ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
function escapeHtml(unsafe) {
    if (unsafe === undefined || unsafe === null) return '';
    return String(unsafe)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function setValueByPath(obj, path, value) {
    const parts = path.split('.');
    let current = obj;
    for (let i = 0; i < parts.length - 1; i++) {
        const part = parts[i];
        const index = parseInt(part, 10);
        if (!isNaN(index) && part === index.toString()) {
            if (!Array.isArray(current)) current = [];
            if (typeof current[index] !== 'object' || current[index] === null) {
                current[index] = {};
            }
            current = current[index];
        } else {
            if (typeof current[part] !== 'object' || current[part] === null) {
                current[part] = {};
            }
            current = current[part];
        }
    }
    const lastPart = parts[parts.length - 1];
    const lastIndex = parseInt(lastPart, 10);
    if (!isNaN(lastIndex) && lastPart === lastIndex.toString()) {
        if (!Array.isArray(current)) current = [];
        current[lastIndex] = value;
    } else {
        current[lastPart] = value;
    }
}

function updateDataFromFields() {
    if (!currentCharacterData) currentCharacterData = {};
    const form = document.getElementById('character-sheet-form');
    if (!form) return;

    const inputs = consumeChangedSheetInputs(form);
    inputs.forEach(input => {
        const name = input.getAttribute('name');
        if (!name) return;
        if (name.startsWith('skills.') && input.type === 'number' && !validSkillField(input)) return;
        let value;
        if (input.type === 'checkbox') {
            value = input.checked;
        } else if (input.type === 'number') {
            value = input.value === '' ? null : parseFloat(input.value);
        } else if (input.dataset?.nullableNumber === 'true') {
            const normalized = String(input.value || '').trim();
            value = (!normalized || normalized === '—' || normalized === '-' || normalized === '–') ? null : parseFloat(normalized);
        } else {
            value = input.value;
        }
        if (input.dataset?.protectionPercent === 'true' && value !== null) {
            value = Math.round(value) / 100;
        }
        if (input.dataset?.transientBonus && value !== null) {
            value -= Number(input.dataset.transientBonus) || 0;
        }
        // Преобразование для templateId и подобных полей
        if (name.endsWith('templateId') || name.endsWith('Id')) {
            value = value === '' ? null : Number(value);
        }
        if (/^skills\.(physical|social|other)\.[^.]+\.base$/.test(name)
            && Number(characterValueAtPath(currentCharacterData, name)) !== value) {
            const xpPath = name.replace(/\.base$/, '.xp');
            setValueByPath(currentCharacterData, xpPath, 0);
            setValueByPath(currentCharacterData,name.replace(/\.base$/,'.learningRemainder'),0);
            pendingManualFieldPaths.add(xpPath);
            pendingManualFieldPaths.add(name);
        }
        const effectField = name.match(/^health\.effects\.(\d+)\.(type|remaining|tick)$/);
        if (effectField) {
            const effect = currentCharacterData.health?.effects?.[Number(effectField[1])];
            if (effect && effectField[2] === 'type' && effect.type !== value) {
                effect.name = getEffectTypeOptions().find(option => option.value === value)?.label || 'Эффект';
            }
            if (effect && effectField[2] === 'remaining' && effect.remaining_seconds != null) {
                const scale = ({hour:3600,minute:60,second:1,movement:600})[effect.time_unit]
                    || (effect.tick === 'time_elapsed' ? 60 : effect.tick === 'turn_end' ? 6 : 1);
                if (value == null) delete effect.remaining_seconds;
                else effect.remaining_seconds = Math.max(0, Number(value) * scale);
            }
        }
        setValueByPath(currentCharacterData, name, value);
        if (effectField?.[2] === 'tick') {
            const effect = currentCharacterData.health.effects[Number(effectField[1])];
            const unit = ({time_elapsed:'minute',hour_start:'hour',movement_end:'movement'})[value];
            delete effect.remaining_seconds;
            delete effect.time_unit;
            if (unit) {
                effect.time_unit = unit;
                if (effect.remaining != null) effect.remaining_seconds = Number(effect.remaining) * ({minute:60,hour:3600,movement:600})[unit];
            }
        }
        const zoneMatch = name.match(/^health\.zones\.([^.]+)\.(current|max)$/);
        if (zoneMatch && pendingManualFieldPaths.has(name)) {
            setValueByPath(currentCharacterData, `health.zones.${zoneMatch[1]}.destructionDamage`, 0);
        }
    });
    normalizeCharacterEffects(currentCharacterData);
    if (currentCharacterData.health) {
        if (Array.isArray(currentCharacterData.health.effects)) {
            currentCharacterData.health.effects.forEach(effect => {
                if (effect && effect.remaining !== null && effect.remaining !== undefined) {
                    effect.duration = effect.remaining;
                }
            });
        }
        syncHealthDerivedStatuses(currentCharacterData.health);
    }
}

function trackManualCharacterField(event) {
    const path = event?.target?.getAttribute?.('name') || '';
    if (!event?.isTrusted || !(
        path === 'health.current'
        || /^health\.zones\.[^.]+\.(current|max)$/.test(path)
        || /^skills\./.test(path)
    )) return;
    pendingManualFieldPaths.add(path);
}

function consumeManualFieldPaths() {
    const paths = [...pendingManualFieldPaths];
    pendingManualFieldPaths.clear();
    return paths;
}

function validSkillField(input) {
    const value = Number(input.value);
    if (String(input.value).trim() === '' || !Number.isFinite(value) || Math.abs(value) > Number.MAX_SAFE_INTEGER) return false;
    if (/\.(base|xp)$/.test(input.name) || /^skills\.(skillPoints|specializations)$/.test(input.name)) {
        return Number.isSafeInteger(value) && (!/\.(xp|skillPoints|specializations)$/.test(input.name) || value >= 0);
    }
    return true;
}

function handleManualFormEdit(event) {
    const input = event.target, path = input?.getAttribute?.('name') || '';
    if (!currentCharacterCanEdit || sheetSaveBlocked) return;
    // Выбор модели применяется отдельной кнопкой, до этого сохранять нечего.
    if (input?.id?.startsWith('weapon-model-select-')) return;
    if (path.startsWith('skills.') && input.type === 'number') {
        if (!validSkillField(input)) {
            if (event.type === 'change') {
                input.value = input.defaultValue = String(characterValueAtPath(currentCharacterData, path) ?? 0);
                showNotification('Введите корректное числовое значение навыка.', 'system');
            }
            return;
        }
        trackManualCharacterField(event);
        updateDataFromFields();
        const skillPath = path.match(/^skills\.((?:physical|social|other)\.[^.]+)\./)?.[1];
        if (skillPath) {
            const skill = getSkillByPath(skillPath);
            const progress = document.querySelector(`[data-skill-progress="${CSS.escape(skillPath)}"]`);
            if (skill && progress) progress.textContent = `Опыт: ${skill.xp || 0}/${getRequiredXp(skill.base)}`;
        }
        if (event.type === 'change') forceSyncCharacter();
        else scheduleAutoSave(200);
        return;
    }
    trackManualCharacterField(event);
    scheduleAutoSave();
}

function renderCreatedByPlayerBadge(item) {
    return item?.createdByPlayer
        ? '<span class="created-by-player-badge" title="Предмет добавлен игроком" style="display:inline-block; margin-left:8px; padding:2px 6px; border-radius:4px; font-size:11px; color:#d8c58a; border:1px solid rgba(216,197,138,.45);">Создано игроком</span>'
        : '';
}

function protectionPercentValue(value) {
    const numeric = Number(value) || 0;
    const percent = Math.abs(numeric) <= 1 ? numeric * 100 : numeric;
    return Math.round(percent);
}

function formatProtectionPercent(value) {
    return `${protectionPercentValue(value)}%`;
}

const expandedInventoryContainers = new Set();

function inventoryContainerKey(item, path) {
    return `${currentCharacterId}:${item?.id ? `id:${item.id}` : `path:${path.join(',')}`}`;
}

function setInventoryContainerOpen(contents, toggle, key, open) {
    contents.style.display = open ? 'block' : 'none';
    contents.dataset.containerStateKey = key;
    if (toggle) toggle.textContent = open ? '▼' : '▶';
    if (open) expandedInventoryContainers.add(key);
    else expandedInventoryContainers.delete(key);
}

function captureSheetViewState() {
    const root = document.getElementById('character-sheet-modal');
    if (!root) return null;
    const active = root.contains(document.activeElement) ? document.activeElement : null;
    const controls = active?.id
        ? root.querySelectorAll(`[id="${CSS.escape(active.id)}"]`)
        : active?.name
            ? root.querySelectorAll(`[name="${CSS.escape(active.name)}"]`)
            : [];
    let selection = null;
    if (active && ('selectionStart' in active)) {
        try {
            selection = [active.selectionStart, active.selectionEnd, active.selectionDirection];
        } catch (_) {
            // Numeric inputs do not support text selection.
        }
    }
    return {
        active,
        activeId: active?.id || null,
        activeName: active?.name || null,
        activeIndex: Array.from(controls).indexOf(active),
        selection,
        pendingWeaponModels: [...root.querySelectorAll('select[data-weapon-instance]')]
            .map(select => ({ id: select.id, instance: select.dataset.weaponInstance, value: select.value })),
        details: [...root.querySelectorAll('details')].map(detail => {
            const tab = detail.closest('[id^="sheet-tab-"]');
            return {
                panel: detail.dataset.equipmentPanel || null,
                sheetPanel: detail.dataset.sheetPanel || null,
                id: detail.id || null,
                tab: tab?.id || null,
                index: [...(tab || root).querySelectorAll('details')].indexOf(detail),
                open: detail.open,
            };
        }),
        openContainers: [...root.querySelectorAll('[data-container-path]')]
            .filter(container => container.style.display !== 'none')
            .map(container => container.getAttribute('data-container-path')),
    };
}

function restoreSheetViewState(state) {
    const root = document.getElementById('character-sheet-modal');
    if (!root || !state) return;
    for (const entry of state.pendingWeaponModels || []) {
        const select = document.getElementById(entry.id);
        if (select?.dataset.weaponInstance === entry.instance
            && [...select.options].some(option => option.value === entry.value)) select.value = entry.value;
    }
    for (const entry of state.details) {
        const scope = entry.tab ? document.getElementById(entry.tab) : root;
        const detail = entry.panel
            ? [...(scope?.querySelectorAll('details[data-equipment-panel]') || [])]
                .find(item => item.dataset.equipmentPanel === entry.panel)
            : entry.sheetPanel
                ? [...(scope?.querySelectorAll('details[data-sheet-panel]') || [])]
                    .find(item => item.dataset.sheetPanel === entry.sheetPanel)
            : entry.id
                ? document.getElementById(entry.id)
                : scope?.querySelectorAll('details')[entry.index];
        if (detail) detail.open = entry.open;
    }
    for (const path of state.openContainers) {
        const container = [...root.querySelectorAll('[data-container-path]')]
            .find(item => item.getAttribute('data-container-path') === path);
        if (!container) continue;
        container.style.display = 'block';
        if (container.dataset?.containerStateKey) {
            expandedInventoryContainers.add(container.dataset.containerStateKey);
        }
        const toggle = container.parentElement?._toggleIcon
            || container.parentElement?.querySelector('.container-toggle');
        if (toggle) toggle.textContent = '▼';
    }
    if (!state.active || (!state.activeId && !state.activeName)) return;
    if (document.activeElement !== state.active
        && document.activeElement !== document.body
        && root.contains(document.activeElement)) return;
    let control = state.active.isConnected ? state.active : null;
    if (!control) {
        const matches = state.activeId
            ? root.querySelectorAll(`[id="${CSS.escape(state.activeId)}"]`)
            : root.querySelectorAll(`[name="${CSS.escape(state.activeName)}"]`);
        control = matches[state.activeIndex >= 0 ? state.activeIndex : 0];
    }
    if (!control || control.disabled) return;
    if (document.activeElement !== control) control.focus({ preventScroll: true });
    if (state.selection && typeof control.setSelectionRange === 'function') {
        try {
            control.setSelectionRange(...state.selection);
        } catch (_) {
            // Numeric inputs do not support text selection.
        }
    }
}

async function refreshSavedSheet(characterId) {
    if (Number(currentCharacterId) !== Number(characterId)) return;
    const viewState = captureSheetViewState();
    await Promise.all([
        renderInventoryTab(currentCharacterData),
        renderEquipmentTab(currentCharacterData),
    ]);
    if (Number(currentCharacterId) !== Number(characterId)) return;
    const activeTab = document.querySelector('#sheet-tabs .tab-btn.active')?.dataset.tab;
    if (activeTab === 'health') refreshHealthPanel();
    else if (activeTab === 'basic') await renderBasicTab(currentCharacterData);
    else if (activeTab === 'skills') renderSkillsTab(currentCharacterData);
    else if (activeTab === 'settings') renderSettingsTab(currentCharacterData);
    else if (activeTab === 'notes') renderNotesTab(currentCharacterData);
    applySheetEditPermissions();
    restoreSheetViewState(viewState);
}

function applySheetEditPermissions(root = document.getElementById('character-sheet-modal')) {
    if (currentCharacterCanEdit || !root) return;
    root.querySelectorAll('input, select, textarea').forEach(control => {
        control.disabled = true;
    });
    root.querySelectorAll('button').forEach(button => {
        if (
            !button.classList.contains('tab-btn')
            && !button.classList.contains('close')
            && !button.hasAttribute('data-sheet-control')
        ) {
            button.disabled = true;
        }
    });
}

function characterValueAtPath(data, path) {
    return path.split('.').reduce(
        (value, part) => value && typeof value === 'object' ? value[part] : undefined,
        data,
    );
}

function setRemoteControlValue(control, rawValue) {
    let value = rawValue;
    if (control.dataset?.protectionPercent === 'true' && value !== null && value !== undefined) {
        value = protectionPercentValue(value);
    }
    if (control.dataset?.transientBonus && value !== null && value !== undefined) {
        value = Number(value || 0) + Number(control.dataset.transientBonus || 0);
    }
    if (control.type === 'checkbox') {
        control.checked = Boolean(value);
        control.defaultChecked = control.checked;
        return;
    }
    if (control.type === 'radio') {
        control.checked = String(control.value) === String(value ?? '');
        control.defaultChecked = control.checked;
        return;
    }
    const displayValue = value === null || value === undefined ? '' : String(value);
    control.value = displayValue;
    if (control.tagName === 'SELECT') {
        Array.from(control.options).forEach(option => {
            option.defaultSelected = option.selected;
        });
    } else {
        control.defaultValue = displayValue;
    }
}

function patchRemoteScalarFields(data, paths) {
    const form = document.getElementById('character-sheet-form');
    if (!form) return new Set(paths);
    const controlsByName = new Map();
    form.querySelectorAll('input[name], select[name], textarea[name]').forEach(control => {
        const name = control.getAttribute('name');
        if (!controlsByName.has(name)) controlsByName.set(name, []);
        controlsByName.get(name).push(control);
    });
    const missing = new Set();
    paths.forEach(path => {
        const controls = controlsByName.get(path);
        if (!controls?.length) {
            missing.add(path);
            return;
        }
        const value = characterValueAtPath(data, path);
        controls.forEach(control => setRemoteControlValue(control, value));
    });
    return missing;
}

async function applyRemoteSheetData(remoteData, characterId = currentCharacterId) {
    if (!remoteData || Number(currentCharacterId) !== Number(characterId)) return false;
    if (Number(remoteData._revision) < Number(currentCharacterData?._revision)) return false;

    const previousData = currentCharacterData || {};
    for (const key of ['ownerId', 'ownerUsername', 'visible_to', 'editable_to']) {
        if (remoteData[key] === undefined && previousData[key] !== undefined) {
            remoteData[key] = previousData[key];
        }
    }
    normalizeCharacterEffects(remoteData);
    const changes = diffCharacterData(previousData, remoteData);
    currentCharacterData = remoteData;

    const allPaths = [...changes.scalarPaths, ...changes.structuralPaths];
    if (!allPaths.length) return true;
    const viewState = captureSheetViewState();
    const missingFields = patchRemoteScalarFields(remoteData, changes.scalarPaths);
    const touches = root => allPaths.some(path => pathTouches(path, root));
    const structuralTouches = root => changes.structuralPaths.some(path => pathTouches(path, root));
    const missingTouches = root => [...missingFields].some(path => pathTouches(path, root));
    const tasks = [];
    const rerenderBasic = structuralTouches('basic') || missingTouches('basic')
        || touches('health') && backgroundIs(remoteData,'Бригадир')
        || backgroundIs(remoteData,'Один для бандитов\nВторой для мутантов') && ['weapons','equipment','inventory','ruleChoices'].some(touches);

    if ((touches('health') || touches('skills.physical.will')) && !rerenderBasic) {
        refreshHealthPanel(remoteData);
    }
    if (rerenderBasic) tasks.push(renderBasicTab(remoteData));
    if (
        structuralTouches('skills')
        || missingTouches('skills')
        || changes.scalarPaths.some(path => /^skills\..+\.(base|xp)$/.test(path))
        || touches('features')
    ) tasks.push(renderSkillsTab(remoteData));
    if (touches('equipment') || touches('weapons') || touches('activeWeaponIndex') || touches('inventory')
        || touches('health.prostheses') || touches('health.combatMeta.portableLights')) {
        tasks.push(renderEquipmentTab(remoteData));
    }
    if (
        structuralTouches('inventory')
        || changes.scalarPaths.some(path => pathTouches(path, 'inventory') && path !== 'inventory.money')
        || touches('equipment')
        || changes.scalarPaths.some(path => pathTouches(path, 'skills.physical.strength'))
    ) tasks.push(renderInventoryTab(remoteData));
    if (touches('ownerId') || touches('ownerUsername') || touches('visible_to') || touches('editable_to')) {
        renderSettingsTab(remoteData);
    }
    if (missingTouches('notes')) renderNotesTab(remoteData);

    await Promise.all(tasks);
    if (Number(currentCharacterId) !== Number(characterId)) return false;
    applySheetEditPermissions();
    restoreSheetViewState(viewState);
    return true;
}

function queueRemoteSheetData(remoteData, characterId = currentCharacterId) {
    const task = remoteSheetUpdateQueue
        .catch(() => false)
        .then(() => applyRemoteSheetData(remoteData, characterId));
    remoteSheetUpdateQueue = task;
    return task;
}

function rememberPendingSheetRemoteUpdate(data) {
    if (!data || typeof data !== 'object') return;
    if (
        !pendingSheetRemoteUpdate
        || Number(data._revision) > Number(pendingSheetRemoteUpdate._revision)
    ) {
        pendingSheetRemoteUpdate = data;
    }
}

async function applyPendingSheetRemoteUpdate(characterId) {
    if (Number(currentCharacterId) !== Number(characterId)) return false;
    if (autoSaveTimer || Server.isCharacterSaving(characterId) || sheetSaveBlocked) return false;
    const remote = pendingSheetRemoteUpdate;
    pendingSheetRemoteUpdate = null;
    if (!remote || Number(remote._revision) < Number(currentCharacterData?._revision)) return false;
    return queueRemoteSheetData(remote, characterId);
}

function persistCurrentSheet() {
    if (medicalOperationInProgress) return;
    if (!currentCharacterCanEdit || !currentCharacterId || !currentCharacterData) return;
    updateDataFromFields();
    const characterId = currentCharacterId;
    const draft = currentCharacterData;
    return Server.updateCharacter(characterId, {
        data: draft, _manual_fields: consumeManualFieldPaths(),
    }).then(async () => {
        const appliedRemote = await applyPendingSheetRemoteUpdate(characterId);
        if (!appliedRemote && currentCharacterData === draft && !autoSaveTimer && !Server.isCharacterSaving(characterId)) {
            return refreshSavedSheet(characterId);
        }
    }).catch(error => {
        if (Number(currentCharacterId) !== Number(characterId)) return;
        if (autoSaveTimer) clearTimeout(autoSaveTimer);
        autoSaveTimer = null;
        currentCharacterCanEdit = false;
        sheetSaveBlocked = true;
        showNotification('Сохранение остановлено: ' + error.message
            + ' Ваша копия остаётся в листе: её можно экспортировать. Затем переоткройте лист.', 'system');
    });
}

function scheduleAutoSave(delay = AUTO_SAVE_DELAY) {
    if (!currentCharacterCanEdit) return;
    if (autoSaveTimer) clearTimeout(autoSaveTimer);
    autoSaveTimer = setTimeout(() => {
        autoSaveTimer = null;
        persistCurrentSheet();
    }, delay);
}

function forceSyncCharacter() {
    if (autoSaveTimer) clearTimeout(autoSaveTimer);
    autoSaveTimer = null;
    return persistCurrentSheet();
}

// ========== УНИВЕРСАЛЬНАЯ МОДЕЛЬ ПРЕДМЕТА ==========
function generateItemId() {
    return 'item_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
}

// ========== УНИВЕРСАЛЬНЫЕ ФУНКЦИИ РАБОТЫ СО СЛОТАМИ ==========

// Получить доступные слоты предмета на основе его шаблона.
function getItemSlots(item) {
    if (!item) return [];
    if (Array.isArray(item.attributes?.slots)) return item.attributes.slots;
    const templateId = item.templateId || item.type; // подсумки используют type
    if (!templateId) return [];
    const cachedGroups = [
        allTemplatesCache,
        ...Object.entries(templatesCache)
            .filter(([key]) => key.startsWith(`${currentLobbyId}_`))
            .map(([, templates]) => templates),
    ];
    for (const templates of cachedGroups) {
        if (!Array.isArray(templates)) continue;
        const template = templates.find(candidate => candidate.id == templateId);
        if (template) return template.attributes?.slots || [];
    }
    return [];
}

function getEffectiveTorsoProtection() {
    const eq = currentCharacterData.equipment || {};
    const vest = eq.vest;
    if (!vest || !vest.pouches) return null;

    const allTemplates = allTemplatesCache || [];
    const platePouches = vest.pouches.filter(pouch => {
        if (!pouch.type) return false;
        const template = allTemplates.find(t => t.id === pouch.type);
        const hasSlot = template?.attributes?.slots?.some(s => s.type === 'armor_plate');
        return hasSlot;
    });

    if (platePouches.length === 0) return null;

    const frontPouch = platePouches[0];
    const backPouch = platePouches[1] || null;

    const getPlateProtection = (pouch) => {
        if (!pouch) return null;
        const installedPlate = (pouch.installedModules || []).find(m => m.slotType === 'armor_plate');
        if (!installedPlate) return null;
        return getPlateEffectiveProtection(installedPlate);
    };

    const front = getPlateProtection(frontPouch);
    const back = getPlateProtection(backPouch);

    return { front, back };
}

function getPlateEffectiveProtection(plate) {
    if (!plate || plate.durability <= 0) return 0;
    let protection = plate.attributes?.protection?.physical || 0;
    const stage = plate.stage || 1;
    if (stage >= 3) protection = Math.floor(protection * 0.9);
    if (stage >= 4) protection = Math.floor(protection * 0.9); // от текущего значения
    if (stage >= 5) protection = Math.floor(protection * 0.75);
    return protection;
}

// Восстановить предмет по сохранённому пути.
function restoreItemToPath(item, path) {
    let parent = currentCharacterData;
    for (let i = 0; i < path.length - 1; i++) {
        const key = path[i];
        if (Array.isArray(parent) && typeof key === 'number') parent = parent[key];
        else if (typeof parent === 'object' && key in parent) parent = parent[key];
        else return false;
    }
    const lastKey = path[path.length - 1];
    if (Array.isArray(parent)) {
        parent.splice(lastKey, 0, item);
        return true;
    }
    return false;
}

// Добавить предмет в рюкзак (fallback).
function addToBackpack(item) {
    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
    currentCharacterData.inventory.backpack.push(item);
}

// Универсальная установка модуля в слот.
function universalInstallModule(targetItem, targetPath, moduleItem, modulePath, slotType) {
    if (!targetItem.installedModules) targetItem.installedModules = [];

    if (slotType === 'filter') {
        const charges = Number(
            moduleItem.attributes?.consumable?.direct?.filter_charges
            ?? moduleItem.attributes?.filter_charges
            ?? moduleItem.maxDurability
            ?? moduleItem.durability
            ?? 0
        );
        moduleItem.category = 'gas_mask_module';
        moduleItem.subcategory = 'filter';
        moduleItem.attributes = {
            ...(moduleItem.attributes || {}),
            slot_type: 'filter',
            durability: charges,
            max_durability: charges,
        };
        moduleItem.durability = charges;
        moduleItem.maxDurability = charges;
    } else if (slotType === 'exoskeleton_battery') {
        moduleItem.category = 'exoskeleton_module';
        moduleItem.subcategory = 'battery';
        moduleItem.attributes = {
            ...(moduleItem.attributes || {}),
            slot_type: 'exoskeleton_battery',
            charge_days: Number(moduleItem.attributes?.charge_days) || 1,
            remaining_days: Number(moduleItem.attributes?.remaining_days) || 1,
        };
    }

    // Обработка стопки
    if (moduleItem.quantity > 1) {
        moduleItem = { ...moduleItem, quantity: 1 };
        const originalItem = getItemByPath(modulePath);
        if (originalItem) {
            originalItem.quantity -= 1;
            if (originalItem.category === 'ammo') updateAmmoWeight(originalItem);
        }
    } else {
        if (!removeItemByPath(modulePath)) return false;
    }

    // Замена старого модуля – всегда в рюкзак
    const existingIndex = targetItem.installedModules.findIndex(m => m.slotType === slotType);
    if (existingIndex !== -1) {
        const oldMod = targetItem.installedModules[existingIndex];
        targetItem.installedModules.splice(existingIndex, 1);
        if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
        if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
        currentCharacterData.inventory.backpack.push(oldMod);
    }

    moduleItem.sourcePath = modulePath;
    moduleItem.slotType = slotType;
    targetItem.installedModules.push(moduleItem);
    if (slotType === 'exoskeleton_battery') targetItem.powered = true;
    return true;
}

/**
 * Создаёт экземпляр предмета из шаблона
 * @param {Object} template - шаблон предмета из getLobbyTemplates
 * @param {number} quantity - количество (для стакающихся)
 * @returns {Object} экземпляр Item
 */
function createItemFromTemplate(template, quantity = 1, options = {}) {
    const isWeapon = ['weapon', 'melee_weapon'].includes(template.category);
    const templateMaxDurability = template.attributes?.max_durability
        ?? template.attributes?.durability
        ?? (isWeapon ? 100 : null);
    const templateDurability = template.attributes?.durability
        ?? (isWeapon ? templateMaxDurability : null);
    const item = {
        id: generateItemId(),
        templateId: template.id,
        name: template.name,
        category: template.category,
        subcategory: template.subcategory,
        quantity: quantity,
        uses: template.attributes?.uses ?? null,
        maxUses: template.attributes?.uses ?? null,
        weight: template.weight || 0,
        volume: template.volume || 0,
        price: template.price || 0,
        attributes: { ...template.attributes },
        durability: templateDurability,
        maxDurability: templateMaxDurability,
        installedModules: [],
        contents: [],
        isContainer: template.category === 'container' || template.category === 'backpack' || template.category === 'pouch',
        isEquippable: ['weapon', 'armor', 'helmet', 'gas_mask', 'device', 'detector', 'backpack'].includes(template.category),
        isStackable: ['consumable', 'crafting_material', 'artifact', 'ammo'].includes(template.category)
    };
    if (options.createdByPlayer) item.createdByPlayer = true;

    if (template.category === 'magazine') {
        item.caliber = template.attributes?.caliber || template.subcategory || null;
        item.emptyWeight = template.attributes?.emptyWeight || 0;
        item.loadedWeight = template.attributes?.loadedWeight || 0;
        item.ammo = [];
        Object.defineProperty(item, 'currentAmmo', {
            get() { return this.ammo.reduce((sum, a) => sum + a.quantity, 0); },
            enumerable: true
        });
        item.weight = item.emptyWeight;
        item.isLoader = template.attributes?.isLoader || false;
    }

    if (template.category === 'ammo') {
        item.caliber = template.attributes?.caliber || template.subcategory || null;
        // Начальный вес пачки патронов
        const qty = item.quantity;
        if (qty === 0) {
            item.weight = 0;
        } else {
            const singleVolume = item.volume || 0.02;
            const occupiedVolume = singleVolume * qty;
            item.weight = (occupiedVolume < 0.5) ? 0.1 : 0.25;
        }
        item.damage = template.attributes?.damage ?? 0;
        item.penetration = template.attributes?.penetration ?? 0;
    }

    if (template.category === 'armor_plate') {
        initArmorStagedDurability(item, template);
    }

    return item;
}

function helmetHasVisor(helmet, template = null) {
    const templateSlots = template?.attributes?.slots || [];
    const name = String(template?.name || helmet?.name || '').trim().toLowerCase();
    return Boolean(template?.attributes?.integrated_visor)
        || (template?.attributes?.protection_zones || []).includes('face')
        || name.startsWith('шлем ')
        || templateSlots.some(slot => slot?.type === 'visor')
        || (helmet?.installedModules || []).some(module => module?.slotType === 'visor');
}

const INTEGRATED_HELMET_ARMORS = new Set([
    'костюм химзащиты',
    'комбинезон купол',
    'комбинезон купол м',
    'комбинезон купол-м',
    'комбинезон гроб',
    'экзоскелет',
]);

function armorHasIntegratedHelmet(armor, template = null) {
    const name = String(template?.name || armor?.name || '').trim().toLowerCase().replace(/ё/g, 'е');
    return Boolean(template?.attributes?.integrated_helmet)
        || (template?.attributes?.protection_zones || []).includes('head')
        || INTEGRATED_HELMET_ARMORS.has(name);
}

function getIntegratedHelmetProfile(armor, template = null) {
    const name = String(template?.name || armor?.name || '').trim().toLowerCase().replace(/ё/g, 'е');
    const profiles = {
        'костюм химзащиты': { physical: 0, charismaPenalty: 2, accuracyPenalty: 2 },
        'комбинезон купол': { physical: 0.1, charismaPenalty: 3, accuracyPenalty: 3 },
        'комбинезон купол м': { physical: 0.35, charismaPenalty: 2, accuracyPenalty: 3 },
        'комбинезон купол-м': { physical: 0.35, charismaPenalty: 2, accuracyPenalty: 3 },
        'комбинезон гроб': { physical: 0.4, charismaPenalty: 4, accuracyPenalty: 4 },
    };
    if (name === 'экзоскелет') {
        const armorPhysical = protectionPercentValue(armor?.protection?.physical) / 100;
        return {
            physical: Math.max(0, armorPhysical - 0.1),
            charismaPenalty: 0,
            accuracyPenalty: 2,
        };
    }
    return profiles[name] || null;
}

function getIntegratedHelmetName(armor, template = null) {
    const configuredName = template?.attributes?.integrated_helmet_name
        || armor?.attributes?.integrated_helmet_name;
    if (configuredName) return configuredName;
    const name = String(template?.name || armor?.name || '').trim().toLowerCase().replace(/ё/g, 'е');
    const names = {
        'костюм химзащиты': 'Шлем Костюма Химзащиты',
        'комбинезон купол': 'Шлем Купол',
        'комбинезон купол м': 'Шлем Купол-М',
        'комбинезон купол-м': 'Шлем Купол-М',
        'комбинезон гроб': 'Шлем ГРОБ',
        'экзоскелет': 'Шлем Экзоскелета',
    };
    return names[name] || `${armor.name} · встроенный шлем`;
}

function syncIntegratedArmorHelmet(armor, template = null) {
    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    const currentHelmet = currentCharacterData.equipment.helmet;
    if (!armorHasIntegratedHelmet(armor, template)) {
        if (currentHelmet?.integratedWithArmor) delete currentCharacterData.equipment.helmet;
        return;
    }
    const profile = getIntegratedHelmetProfile(armor, template) || {
        physical: 0,
        charismaPenalty: 0,
        accuracyPenalty: 0,
    };
    currentCharacterData.equipment.helmet = {
        templateId: `integrated:${armor.templateId || 'armor'}`,
        integratedWithArmor: true,
        sourceArmorTemplateId: armor.templateId,
        name: getIntegratedHelmetName(armor, template),
        material: armor.material,
        protection: {
            physical: profile.physical,
            chemical: 0,
            thermal: 0,
            electric: 0,
            radiation: 0,
        },
        durability: armor.durability,
        maxDurability: armor.maxDurability,
        stage: armor.stage,
        condition: armor.condition,
        stageDurability: armor.stageDurability,
        currentStageDurability: armor.currentStageDurability,
        accuracyPenalty: profile.accuracyPenalty,
        ergonomicsPenalty: 0,
        charismaBonus: -profile.charismaPenalty,
        modifications: [],
        installedModules: [],
    };
}

function isGasMaskHelmet(template) {
    const name = String(template?.name || '').trim().toLowerCase();
    return Boolean(template?.attributes?.requires_filter)
        || name.includes('противогазо-шлем');
}

function notifyHelmetGasMaskConflict() {
    showNotification('Нельзя одновременно надеть противогаз и шлем с забралом или противогазо-шлем');
}

async function canEquipHelmetWithCurrentGasMask(template, helmet) {
    if (currentCharacterData.equipment?.helmet?.integratedWithArmor) {
        showNotification('Сначала снимите броню со встроенным шлемом');
        return false;
    }
    if (!currentCharacterData.equipment?.gasMask?.templateId) return true;
    if (isGasMaskHelmet(template) || helmetHasVisor(helmet, template)) {
        notifyHelmetGasMaskConflict();
        return false;
    }
    return true;
}

async function canEquipGasMaskWithCurrentHelmet() {
    const helmet = currentCharacterData.equipment?.helmet;
    if (!helmet?.templateId) return true;
    if (helmet.integratedWithArmor) {
        notifyHelmetGasMaskConflict();
        return false;
    }
    const templates = await loadTemplatesForLobby('helmet');
    const template = templates.find(item => Number(item.id) === Number(helmet.templateId));
    if (isGasMaskHelmet(template) || helmetHasVisor(helmet, template)) {
        notifyHelmetGasMaskConflict();
        return false;
    }
    return true;
}

function createItemFromTemplateSelection(template, quantity = 1, ammoVariant = null, options = {}) {
    const newItem = createItemFromTemplate(template, quantity, options);
    if (template.category === 'ammo') {
        applyAmmoVariantToItem(newItem, template, ammoVariant);
        updateAmmoWeight(newItem);
    }
    return newItem;
}

function migrateOldItemToNew(oldItem) {
    if (oldItem.id) return oldItem;

    return {
        id: generateItemId(),
        templateId: oldItem.templateId || null,
        name: oldItem.name,
        category: oldItem.category || 'misc',
        quantity: oldItem.quantity || 1,
        weight: oldItem.weight || 0,
        volume: oldItem.volume || 0,
        price: oldItem.price || 0,
        attributes: oldItem.attributes || {},
        durability: oldItem.durability,
        maxDurability: oldItem.maxDurability,
        installedModules: [],
        contents: oldItem.contents || [], // <-- важно
        isContainer: oldItem.category === 'container' || oldItem.category === 'backpack' || oldItem.category === 'pouch',
        isEquippable: ['weapon', 'armor', 'helmet', 'gas_mask', 'backpack'].includes(oldItem.category),
        isStackable: ['consumable', 'crafting_material', 'artifact'].includes(oldItem.category)
    };
}

function migratePouchesToNewFormat() {
    const eq = currentCharacterData.equipment;
    if (!eq) return;

    // Пояс
    if (eq.belt?.pouches && Array.isArray(eq.belt.pouches)) {
        eq.belt.pouches = eq.belt.pouches.map(pouch => {
            // Если содержимое — строка, превращаем в пустой массив
            if (typeof pouch.contents === 'string') {
                return {
                    ...pouch,
                    contents: [],
                    isContainer: true,
                    capacity: pouch.capacity || 0
                };
            }
            // Уже массив или отсутствует
            return {
                ...pouch,
                contents: pouch.contents || [],
                isContainer: true
            };
        });
    }

    // Разгрузка (пока не трогаем, но для будущего)
    if (eq.vest?.pouches && Array.isArray(eq.vest.pouches)) {
        eq.vest.pouches = eq.vest.pouches.map(pouch => {
            if (typeof pouch.contents === 'string') {
                return {
                    ...pouch,
                    contents: [],
                    isContainer: true,
                    capacity: pouch.capacity || 0
                };
            }
            return {
                ...pouch,
                contents: pouch.contents || [],
                isContainer: true
            };
        });
    }
}

// Рекурсивно вычисляет общий вес предмета с учётом содержимого
function getTotalWeight(item) {
    if (!item || typeof item !== 'object') return 0;
    if(item.attributes?.prosthesis_id)return 0;
    let baseWeight = item.attributes?.prosthesis?.weight ?? item.weight ?? 0;
    if (item.category === 'magazine') {
        const currentAmmo = Number(item.currentAmmo) || (Array.isArray(item.ammo)
            ? item.ammo.reduce((sum, stack) => sum + (Number(stack?.quantity) || 0), 0)
            : 0);
        baseWeight = (currentAmmo > 0) ? (item.loadedWeight || 0) : (item.emptyWeight || 0);
    } else if (item.category === 'ammo') {
        const qty = item.quantity || 0;
        if (qty === 0) return 0;
        const singleVolume = item.volume || 0.02;
        const occupiedVolume = singleVolume * qty;
        return (occupiedVolume < 0.5) ? 0.1 : 0.25;
    }
    if(traitCount(currentCharacterData,'Да не такой и тяжелый')&&getWeaponClass(item)==='grenade_launcher')baseWeight=Math.max(0,baseWeight-3);
    let total = baseWeight * (item.quantity || 1);
    if (item.contents && Array.isArray(item.contents)) {
        total += item.contents.reduce((sum, sub) => sum + getTotalWeight(sub), 0);
    }
    if (item.installedModules && Array.isArray(item.installedModules)) {
        total += item.installedModules.reduce((sum, mod) => sum + getTotalWeight(mod), 0);
    }
    return total;
}

function getSkillEffectiveValue(data, skillPath) {
    const parts = skillPath.split('.');
    let skill = data?.skills;
    for (const part of parts) {
        if (!skill || typeof skill !== 'object') return 0;
        skill = skill[part];
    }
    const base = Number(skill?.base);
    const bonus = Number(skill?.bonus) || 0;
    const statName = skillPath.split('.').pop();
    const modifiers = data?.health?.combatMeta?.consumableModifiers;
    const temporaryValue = Array.isArray(modifiers)
        ? modifiers.reduce((sum, modifier) => {
            if (!modifier || ![statName, `${statName}_delta`].includes(modifier.stat)) return sum;
            if (modifier.remaining !== undefined && Number(modifier.remaining) <= 0) return sum;
            return sum + (Number(modifier.value) || 0);
        }, 0)
        : 0;
    const equipmentValue = skillPath === 'physical.strength'
        ? getExoskeletonPowerProfile(data).strengthLevelBonus
        : 0;
    return (Number.isFinite(base) ? base : 0) + bonus + temporaryValue + equipmentValue
        +(skillPath==='social.charisma'?facialProfile(data).charismaLevel:0)
        +(backgroundIs(data,'Пожарный')&&data.health?.combatMeta?.firefighterNearby&&['physical.strength','physical.agility'].includes(skillPath)?2:0);
}

function getSkillRollModifier(data, skillPath) {
    const effectiveValue = getSkillEffectiveValue(data, skillPath);
    return Math.floor((effectiveValue - 10) / 2)
        +(skillPath==='social.charisma'?facialProfile(data).charismaBonus:0)
        + getHealthRollModifier(data, skillPath);
}

function getIntoxicationProfile(data) {
    const health = data?.health || {};
    const value = Math.max(0, Number(health.intoxication) || 0);
    const forcedExtreme = (health.effects || []).some(effect =>
        effect?.active !== false
        && effect?.forced_intoxication_stage === 'extreme'
        && (effect.remaining == null || Number(effect.remaining) > 0)
    );
    if (value >= 100) return { stage: 'Смертельное', accuracy: -5, agility: -8, charisma: -5, movement: 5 };
    if (value >= 96) return { stage: 'Околосмертельное', accuracy: -5, agility: -8, charisma: -5, movement: 5 };
    if (value >= 81 || forcedExtreme) return { stage: 'Экстремальное', accuracy: -5, agility: -8, charisma: -5, movement: 5 };
    if (value >= 46) return { stage: 'Тяжёлое', accuracy: -3, agility: -5, charisma: -2, movement: 3 };
    if (value >= 31) return { stage: 'Среднее', accuracy: -1, agility: -3, charisma: 3, movement: 0 };
    if (value >= 16) return { stage: 'Лёгкое', accuracy: -1, agility: 0, charisma: 1, movement: 0 };
    return { stage: 'Нет', accuracy: 0, agility: 0, charisma: 0, movement: 0 };
}

function getHealthRollModifier(data, skillPath, options = {}) {
    const health = data?.health || {};
    let modifier = 0;
    if(['shooting','melee','medicine','engineering','strength','lockpicking','climbing','search'].includes(skillPath.split('.').at(-1)))
        modifier+=['leftArm','rightArm'].reduce((sum,zone)=>sum+(prostheticLimb(data,zone)?.efficiency||0),0);
    if(skillPath==='physical.agility')modifier+=['leftLeg','rightLeg'].reduce((sum,zone)=>sum+(prostheticLimb(data,zone)?.efficiency||0),0);
    if(['physical.shooting','physical.melee','physical.agility'].includes(skillPath))modifier+=facialProfile(data)[skillPath.split('.').at(-1)]||0;
    if (options.includePain !== false) modifier -= Math.max(0,(Number(health.painLevel)||0)-passiveTraits(data).painIgnored-passiveTraits(data).painReduction);
    modifier -= Number(health.exhaustion) || 0;
    const bleeding = health.bleeding || {};
    modifier -= Number(bleeding.totalSeverity ?? health.bleedingSeverity) || 0;

    const temperature = Number(health.temperature);
    if (temperature >= 30 && temperature <= 33) modifier -= 7;
    else if (temperature >= 38 && temperature <= 39) modifier -= 3;
    else if (temperature >= 40 && temperature < 41) modifier -= 7;

    const intoxication = getIntoxicationProfile(data);
    if (['physical.accuracy', 'physical.shooting'].includes(skillPath)) modifier += intoxication.accuracy;
    else if (skillPath === 'physical.agility') modifier += intoxication.agility;
    else if (skillPath === 'social.charisma') modifier += intoxication.charisma;
    if(['physical.accuracy','physical.shooting'].includes(skillPath)&&backgroundIs(data,'Дядя Толик'))modifier-=2;
    if(skillPath==='physical.agility'&&backgroundIs(data,'Трюкач'))modifier-=Math.floor((Number(health.stress)||0)/3);
    if(skillPath==='social.charisma'&&backgroundIs(data,'Прогульщик')&&(Number(health.intoxication)||0)<75)modifier+=Math.floor((Number(health.intoxication)||0)/10);

    Object.values(health.zones || {}).forEach(zone => {
        if (!zone || typeof zone !== 'object') return;
        const penalties = zone.penalties || {};
        modifier -= Number(zone.rollPenalty ?? zone.roll_penalty ?? zone.skillPenalty ?? 0) || 0;
        modifier -= Number(penalties.all ?? penalties.roll ?? 0) || 0;
        modifier -= Number(penalties[skillPath] ?? penalties[`skills.${skillPath}`] ?? 0) || 0;
        modifier -= Number(skillPath.startsWith('physical.') ? penalties.physical : penalties.other) || 0;
    });

    const activeEffects = (Array.isArray(health.effects) ? health.effects : []).filter(effect =>
        effect && effect.active !== false && (effect.remaining == null || Number(effect.remaining) > 0)
    );
    const suppressedFractureAreas = new Set(activeEffects
        .filter(effect => effect.suppress_fracture)
        .map(effect => String(effect.area || '').toLowerCase().replaceAll('_','')));
    if(['physical.shooting','physical.accuracy'].includes(skillPath)&&activeEffects.some(e=>e.type==='burning'))modifier-=3;
    activeEffects.forEach(effect => {
        const penalties = effect.modifiers || {};
        modifier -= Number(effect.rollPenalty ?? effect.roll_penalty ?? effect.skillPenalty ?? 0) || 0;
        modifier -= Number(penalties.all ?? 0) || 0;
        modifier -= Number(penalties[skillPath] ?? 0) || 0;
        modifier -= Number(skillPath.startsWith('physical.') ? penalties.physical : penalties.other) || 0;
        if (effect.type === 'stimulant_crash') {
            modifier -= Number(effect.phase_penalty ?? effect.value ?? 0) || 0;
        } else if (skillPath.startsWith('physical.') && ['fracture', 'fracture_fixed', 'fracture_unfixed'].includes(effect.type)) {
            const area = String(effect.area || '').toLowerCase().replaceAll('_','');
            if (suppressedFractureAreas.has(area)) return;
            if (area.includes('arm') || area.includes('hand')) {
                modifier -= effect.type === 'fracture_fixed' ? 1 : 2;
            }
        } else if (skillPath.startsWith('physical.') && effect.type === 'fracture_sequela') {
            const area = String(effect.area || '').toLowerCase().replaceAll('_','');
            if (area.includes('arm') || area.includes('hand')) modifier -= 1;
        }
    });

    if (skillPath === 'physical.will') {
        const psyState = Number(health.psyState ?? health.psy_state) || 0;
        modifier -= psyState >= 10 ? 1 : 0;
    }
    return modifier;
}

function hasHealthRollDisadvantage(data, skillPath) {
    const psyState = Number(data?.health?.psyState ?? data?.health?.psy_state) || 0;
    return (
        skillPath === 'physical.shooting' && psyState >= 30
    ) || (
        skillPath === 'physical.will' && psyState >= 40
    );
}

function getDeafnessLevel(data) {
    const health = data?.health || {};
    const values = [Number(health.deafness) || 0];
    const psyState = Number(health.psyState ?? health.psy_state) || 0;
    if (psyState >= 40) values.push(100);
    else if (psyState >= 20) values.push(50);
    (Array.isArray(health.effects) ? health.effects : []).forEach(effect => {
        if (effect?.active === false || effect?.type !== 'deafness') return;
        values.push(Number(effect.value) || 0);
    });
    return Math.max(0, ...values);
}

function getConsumableStatValue(data, statName) {
    const modifiers = data?.health?.combatMeta?.consumableModifiers;
    if (!Array.isArray(modifiers)) return 0;
    return modifiers.reduce((total, modifier) => {
        if (!modifier || ![statName, `${statName}_delta`].includes(modifier.stat)) return total;
        if (modifier.remaining !== undefined && Number(modifier.remaining) <= 0) return total;
        return total + (Number(modifier.value) || 0);
    }, 0);
}

function getAwarenessModeChoices(data, darkness = false) {
    const headphones = Number(
        data?.equipment?.headphones?.awarenessBonus
        ?? data?.equipment?.headphones?.awareness_bonus,
    ) || 0;
    const vision = getConsumableStatValue(data, 'vision_awareness')
        + getConsumableStatValue(data, darkness ? 'darkness_awareness' : 'light_awareness');
    const deafness = getDeafnessLevel(data);
    const hearingPenalty=hearingModifier(deafness);
    const signed = value => `${value >= 0 ? '+' : ''}${value}`;
    const visualParts = [
        vision ? `препараты ${signed(vision)}` : '',
    ].filter(Boolean);
    const hearingParts = [
        headphones ? `наушники ${signed(headphones)}` : '',
        hearingPenalty ? `глухота ${signed(hearingPenalty)}` : '',
    ].filter(Boolean);
    return [
        {
            label: `Зрение: ${signed(vision)}${visualParts.length ? ` (${visualParts.join(', ')})` : ''}`,
            mode: 'visual',
        },
        {
            label: deafness >= 90
                ? `Слух: невозможно (глухота ${deafness})`
                : `Слух: ${signed(headphones+hearingPenalty)}${hearingParts.length ? ` (${hearingParts.join(', ')})` : ''}${deafness ? `, глухота ${deafness}` : ''}`,
            mode: 'hearing',
            disabled: deafness >= 90,
        },
    ];
}

function getWeightPerMovementPenalty(data) {
    const strength = data?.skills?.physical?.strength;
    const effectiveStrength = strength
        ? getSkillEffectiveValue(data, 'physical.strength')
        : 10;
    const capacityModifier = Math.floor((effectiveStrength - 10) / 2);
    return Math.max(0.5, 5 * (1 + capacityModifier * 0.1));
}

function getExoskeletonPowerProfile(data) {
    const armor = data?.equipment?.armor || {};
    const armorName = String(armor.name || '').trim().toLowerCase().replaceAll('ё', 'е');
    const isExoskeleton = Boolean(
        armorName === 'экзоскелет'
        || armor.isExoskeleton
        || armor.attributes?.is_exoskeleton
    );
    const battery = (armor.installedModules || []).find(module =>
        module?.slotType === 'exoskeleton_battery'
        || module?.attributes?.slot_type === 'exoskeleton_battery'
    );
    const powered = Boolean(
        isExoskeleton
        && battery
        && Number(battery.attributes?.remaining_days) > 0
    );
    return {
        isExoskeleton,
        powered,
        strengthLevelBonus: powered ? 8 : 0,
    };
}

function calculateCarriedWeight(data) {
    const inv = data?.inventory || {};
    const eq = data?.equipment || {};
    const items = [];
    if (Array.isArray(inv.backpack)) items.push(...inv.backpack);
    if (Array.isArray(inv.pockets)) items.push(...inv.pockets);
    for (const group of ['belt', 'vest']) {
        const pouches = Array.isArray(eq[group]?.pouches) ? eq[group].pouches : [];
        pouches.forEach((pouch) => {
            if (Array.isArray(pouch?.contents)) items.push(...pouch.contents);
        });
    }
    if (Array.isArray(data?.weapons)) items.push(...data.weapons);
    for(const parts of Object.values(data?.health?.prostheses || {}))items.push(...Object.values(parts));
    return items.reduce((sum, item) => sum + getTotalWeight(item), 0);
}

function getMovementPenaltyBreakdown(data, suppliedWeight = null) {
    const eq = data?.equipment || {};
    const armor = eq.armor || {};
    const totalWeight = suppliedWeight ?? calculateCarriedWeight(data);
    const weightPerPenalty = getWeightPerMovementPenalty(data);
    const rawWeightPenalty = Math.floor(totalWeight / weightPerPenalty);
    const backpackReduction = Math.max(0, Number(eq.backpack?.attributes?.weight_reduction) || 0);
    let weightPenalty = Math.max(0, Math.floor(rawWeightPenalty*passiveTraits(data).weightMultiplier) - backpackReduction+getConsumableStatValue(data,'weight'));
    let armorPenalty = Number(armor.movementPenalty ?? armor.movement_penalty) || 0;
    const helmet = eq.helmet || {};
    const helmetPenalty = helmet.integratedWithArmor
        ? 0
        : (Number(helmet.movementPenalty ?? helmet.movement_penalty) || 0);
    const exoskeleton = getExoskeletonPowerProfile(data);
    const isExoskeleton = exoskeleton.isExoskeleton;
    const powered = exoskeleton.powered;
    if (isExoskeleton) armor.powered = powered;
    if (powered) {
        armorPenalty = 5;
        weightPenalty = 0;
    }
    const activeModifiers = data?.health?.combatMeta?.consumableModifiers;
    const temporary = Array.isArray(activeModifiers)
        ? activeModifiers.reduce((sum, modifier) => {
            if (!modifier || !['movement_points', 'movement_points_delta', 'generic', 'generic_multiplier'].includes(modifier.stat)) {
                return sum;
            }
            if (modifier.remaining !== undefined && Number(modifier.remaining) <= 0) return sum;
            return sum + (Number(modifier.value) || 0);
        }, 0)
        : 0;
    const zones = data?.health?.zones || {};
    const activeHealthEffects = (Array.isArray(data?.health?.effects) ? data.health.effects : []).filter(effect =>
        effect && effect.active !== false && (effect.remaining == null || Number(effect.remaining) > 0)
    );
    const suppressedFractureAreas = new Set(activeHealthEffects
        .filter(effect => effect.suppress_fracture)
        .map(effect => String(effect.area || '').toLowerCase().replaceAll('_','')));
    const fractureInjuries = activeHealthEffects.reduce((sum, effect) => {
        if (!['fracture', 'fracture_fixed', 'fracture_unfixed', 'fracture_sequela'].includes(effect.type)) return sum;
        const area = String(effect.area || '').toLowerCase().replaceAll('_','');
        if (suppressedFractureAreas.has(area)) return sum;
        if (!area.includes('leg') && !area.includes('foot')) return sum;
        if (effect.type === 'fracture_sequela') return sum + 1;
        return sum + (effect.type === 'fracture_fixed' ? 2 : 3);
    }, 0);
    const catastrophicByArea = new Map(activeHealthEffects
        .filter(effect => ['mangled_limb', 'amputation'].includes(effect.type))
        .map(effect => [String(effect.area || ''), effect.type]));
    const injuries = ['leftLeg', 'rightLeg'].reduce((sum, key) => {
        const prosthesis=prostheticLimb(data,key);
        if(prosthesis)return sum-prosthesis.efficiency;
        const injuryType = catastrophicByArea.get(key);
        if (injuryType === 'amputation') return sum + 6;
        if (injuryType === 'mangled_limb') return sum + 5;
        return sum + (zones[key] && Number(zones[key].current) <= 0 ? 3 : 0);
    }, fractureInjuries);
    const intoxicationPenalty = getIntoxicationProfile(data).movement;
    return {
        total: armorPenalty + helmetPenalty + weightPenalty + temporary + injuries + intoxicationPenalty+passiveTraits(data).movementPenalty,
        totalWeight,
        weightPerPenalty,
        rawWeightPenalty,
        backpackReduction,
        weightPenalty,
        armorPenalty,
        helmetPenalty,
        temporary,
        injuries,
        intoxicationPenalty,
        poweredExoskeleton: powered,
    };
}

function applyModifier(base, mod) {
    if (mod === undefined || mod === null || mod === '') return base;
    const str = String(mod).trim();
    if (str.startsWith('=')) {
        return parseInt(str.substring(1)) || 0;
    }
    return base + (parseInt(str) || 0);
}

function getEffectiveWeaponStats(weapon) {
    const base = {
        accuracy: weapon.accuracy ?? weapon.attributes?.accuracy ?? 0,
        noise: weapon.noise ?? weapon.attributes?.noise ?? 0,
        range: weapon.range ?? weapon.attributes?.range ?? 0,
        ergonomics: weapon.ergonomics ?? weapon.attributes?.ergonomics ?? 0
    };
    if (!weapon.installedModules) return base;
    weapon.installedModules.forEach(mod => {
        const m = mod.modifiers || mod.attributes?.modifiers || {};
        base.accuracy = applyModifier(base.accuracy, m.accuracy);
        base.noise = applyModifier(base.noise, m.noise);
        base.range = applyModifier(base.range, m.range);
        base.ergonomics = applyModifier(base.ergonomics, m.ergonomics);
        const combatState = window.locationCombatState;
        const weaponIndex = (currentCharacterData.weapons || []).indexOf(weapon);
        const isBraced = Boolean(
            combatState?.current_character?.character_id === currentCharacterId
            && combatState.current_character.weapon_braced
            && combatState.current_character.braced_weapon_index === weaponIndex
        );
        const isProne = combatState?.current_character?.posture === 'prone';
        if (mod.slotType === 'handguard' && (mod.attributes?.bipod || mod.bipod || mod.name === 'Сошки') && (isBraced || isProne)) {
            base.ergonomics += backgroundIs(currentCharacterData, 'Пост принял') ? 50 : 75;
        }
    });
    return base;
}


//Рекурсивно вычисляет общий объём предмета с учётом содержимого
function getTotalVolume(item) {
    let total = item.volume * item.quantity;
    if (item.contents && item.contents.length) {
        total += item.contents.reduce((sum, subItem) => sum + getTotalVolume(subItem), 0);
    }
    return total;
}

function calculateBackpackTotals(items) {
    let totalWeight = 0;
    let totalVolume = 0;
    items.forEach(item => {
        totalWeight += getTotalWeight(item);
        totalVolume += getTotalVolume(item);
    });
    return { totalWeight, totalVolume };
}

function calculatePouchUsedVolume(pouch) {
    if (!pouch.contents) return 0;
    return pouch.contents.reduce((sum, item) => sum + getTotalVolume(item), 0);
}

window.updatePouchField = function(pathStr, field, value) {
    const path = pathStr.split(',').map(p => isNaN(p) ? p : parseInt(p));
    let obj = currentCharacterData;
    for (let i = 0; i < path.length - 1; i++) obj = obj[path[i]];
    const pouch = obj[path[path.length - 1]];
    if (!pouch) return;
    pouch[field] = value;
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
};

window.removePouchItem = function(pathStr) {
    const path = pathStr.split(',').map(p => isNaN(p) ? p : parseInt(p));
    let obj = currentCharacterData;
    for (let i = 0; i < path.length - 2; i++) obj = obj[path[i]];
    const parentArray = obj[path[path.length - 2]];
    const index = path[path.length - 1];
    if (Array.isArray(parentArray)) {
        parentArray.splice(index, 1);
    }
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
};

// ========== 2. РЕНДЕРИНГ ЛИСТА И ВКЛАДКИ ==========
async function renderCharacterSheet(characterName, data) {
    document.getElementById('character-sheet-name').textContent = characterName;
    const tabsContainer = document.getElementById('sheet-tabs');
    const contentContainer = document.getElementById('sheet-content');
    tabsContainer.innerHTML = '';
    contentContainer.innerHTML = '';

    const tabs = [
        { id: 'basic', title: 'Основное' },
        { id: 'skills', title: 'Навыки' },
        { id: 'equipment', title: 'Экипировка' },
        { id: 'inventory', title: 'Инвентарь' },
        { id: 'settings', title: 'Настройки' },
        { id: 'notes', title: 'Заметки' }
    ];

    tabs.forEach((tab, index) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `tab-btn ${index === 0 ? 'active' : ''}`;
        btn.dataset.tab = tab.id;
        btn.textContent = tab.title;
        btn.onclick = () => switchSheetTab(tab.id);
        tabsContainer.appendChild(btn);

        const contentDiv = document.createElement('div');
        contentDiv.id = `sheet-tab-${tab.id}`;
        contentDiv.className = `sheet-tab-content ${index === 0 ? 'active' : ''}`;
        contentContainer.appendChild(contentDiv);
    });

    await renderBasicTab(data);
    renderSkillsTab(data);
    await renderEquipmentTab(data);
    await renderInventoryTab(data);
    renderNotesTab(data);
    renderSettingsTab(data);

    const form = document.getElementById('character-sheet-form');
    if (form && form.dataset.autosaveInitialized !== 'true') {
        form.addEventListener('input', handleManualFormEdit);
        form.addEventListener('change', handleManualFormEdit);
        form.dataset.autosaveInitialized = 'true';
    }
}

function switchSheetTab(tabId) {
    document.querySelectorAll('#sheet-tabs .tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabId);
    });
    document.querySelectorAll('.sheet-tab-content').forEach(div => {
        div.classList.toggle('active', div.id === `sheet-tab-${tabId}`);
    });
}

// ========== УНИВЕРСАЛЬНАЯ ФУНКЦИЯ ЗАПОЛНЕНИЯ ОБЪЕКТА ИЗ ШАБЛОНА ==========
function applyTemplateToObject(obj, template, mapping) {
    // mapping: { 'путь.в.obj': 'путь.в.attributes' }
    for (const [targetPath, sourcePath] of Object.entries(mapping)) {
        const value = sourcePath.split('.').reduce((o, p) => o?.[p], template.attributes);
        if (value !== undefined) {
            setValueByPath(obj, targetPath, value);
        }
    }
    // Копируем базовые поля
    if (template.name) obj.name = template.name;
    if (template.price !== undefined) obj.price = template.price;
    if (template.weight !== undefined) obj.weight = template.weight;
    if (template.volume !== undefined) obj.volume = template.volume;
    obj.templateId = template.id;
}

// ========== 3. ВКЛАДКА "ОСНОВНОЕ" ==========
async function renderBasicTab(data) {
    const container = document.getElementById('sheet-tab-basic');
    const basic = data.basic || {};
    const bg = basic.background || {};
    const inv = data.inventory || {};

    // Загружаем шаблоны предысторий
    let backgroundTemplates = [];
    try {
        backgroundTemplates = await loadTemplatesForLobby('background');
    } catch (e) {
        console.error('Failed to load templates', e);
    }

    const skillBonuses = Array.isArray(bg.skillBonuses) ? bg.skillBonuses : [];
    let skillBonusesHtml = '';
    skillBonuses.forEach((bonus, index) => {
        skillBonusesHtml += `
            <div style="display: flex; gap: 5px; align-items: center; margin-bottom: 5px;">
                <select name="basic.background.skillBonuses.${index}.skill" class="form-control" style="flex:2;">
                    ${skillCategories.map(cat => `<option value="${cat.path}" ${bonus.skill === cat.path ? 'selected' : ''}>${cat.label}</option>`).join('')}
                </select>
                <input type="number" class="form-control number-input" name="basic.background.skillBonuses.${index}.bonus" value="${bonus.bonus || 0}" style="width: 60px;" placeholder="Бонус">
                <button type="button" class="btn btn-sm btn-danger" onclick="removeBackgroundSkillBonus(${index})">✕</button>
            </div>
        `;
    });

    // Левая колонка
    let leftHtml = `
        <div style="display: flex; gap: 20px; flex-wrap: wrap; align-items: center; margin-bottom: 15px;">
            <div style="flex: 2; min-width: 200px;">
                <label>Имя</label>
                <input type="text" class="form-control" name="basic.name" value="${escapeHtml(basic.name || '')}" style="width:100%;">
            </div>
            <div style="width: 100px;">
                <label>Возраст</label>
                <input type="number" class="form-control number-input" name="basic.age" value="${basic.age ?? ''}" style="width:100%;">
            </div>
            <div style="min-width: 200px;">
                <label>Организация</label>
                <input type="text" class="form-control" name="basic.organization" value="${escapeHtml(basic.organization || '')}" style="width:100%;">
            </div>
        </div>

        <hr>
        <h4>Предыстория</h4>
        ${backgroundIs(data,'Бригадир')?`<button type="button" class="btn btn-secondary brigadier-order-btn" onclick="performCharacterTraitAction('brigadier_order')">Дать указание союзнику</button>`:''}
        ${traitCount(data,'Истинное зрение')?'<button type="button" class="btn btn-secondary true-sight-btn" onclick="activateTrueSight()">Истинное зрение: различить оригиналы и копии</button>':''}
        ${backgroundIs(data,'Хирург') ? '<button type="button" class="btn btn-secondary surgeon-menu-btn" onclick="openSurgeonMenu()">Вправить конечность без набора · 2 ОД</button>' : ''}
        ${data.health?.combatMeta?.brigadierOrder?'<p>Принято указание: следующий бросок +3'+(data.health.combatMeta.brigadierOrder.advantage?' и Преимущество':'')+'</p>':''}
        ${backgroundIs(data,'Бригадир')?Object.entries(data.health?.combatMeta?.brigadierRefusals||{}).map(([id,entry])=>`<div style="margin:8px 0"><span>Отказ от указания · персонаж №${Number(id)}</span> ${entry.choice_pending?`<button class="btn btn-secondary" type="button" onclick="resolveBrigadierRefusal(${Number(id)},'stress')">Получить 1 стресс</button><button class="btn btn-secondary" type="button" onclick="resolveBrigadierRefusal(${Number(id)},'provoke')">Провоцировать</button>`:entry.must_provoke?`<button class="btn btn-secondary" type="button" onclick="resolveBrigadierRefusal(${Number(id)},'provoked')">Провокация разыграна</button>`:''}</div>`).join(''):''}
        ${backgroundIs(data,'Один для бандитов\nВторой для мутантов') ? `<div class="target-weapon-choices" style="display:grid;gap:8px;margin-bottom:12px;">${[['human','Против людей: +2, против мутантов: −5'],['mutant','Против мутантов: +2, против людей: −5']].map(([key,label])=>`<label>${label}<select class="form-control" data-target-weapon="${key}" onchange="setBackgroundTargetWeapon('${key}',this.value)"><option value="">Не выбрано</option>${carriedWeaponEntries(data).map(({weapon,path},index)=>`<option value="${escapeHtml(JSON.stringify({path,id:weapon.id||null,name:weapon.name}))}" ${weapon.id && data.ruleChoices?.targetWeapons?.[key]===weapon.id?'selected':''}>${escapeHtml(weapon.name||'Оружие')} · ${index+1}</option>`).join('')}</select></label>`).join('')}<small>Можно переназначить. Бонус действует только у владельца этой предыстории.</small></div>` : ''}
        <div><strong>Метки персонажа</strong><p>${['Военный','Бандит','Торговец','Проходил военную службу'].map(tag=>`<label style="margin-right:12px"><input type="checkbox" ${basic.tags?.includes(tag)?'checked':''} ${window.isGM?'':'disabled'} onchange="setCharacterRuleTag('${tag}',this.checked)"> ${tag}</label>`).join('')}</p>${(basic.tags||[]).filter(t=>!['Военный','Бандит','Торговец','Проходил военную службу'].includes(t)).map(t=>`<span class="badge">${escapeHtml(t)}</span>`).join(' ')}</div>
        ${data.creation?.zoneHistory?`<details ${data.creation.zoneHistory.settled?'':'open'}><summary>Жизнь в Зоне до игры: ${Number(data.creation.zoneHistory.months)} мес.</summary>${(data.creation.zoneHistory.events||[]).filter(event=>!data.creation.zoneHistory.presentationRequired||data.creation.zoneHistory.settled||event.confirmed).map(event=>`<div style="margin:10px 0"><strong>Месяц ${Number(event.month)}: ${escapeHtml(event.title)}</strong><p>${(event.details||[]).map(escapeHtml).join('<br>')}</p><p>Доход: ${(event.incomeOptions||[]).map(v=>Number(v).toLocaleString('ru-RU')).join(' или ')} руб.</p>${(event.pending||[]).map(text=>`<p>${escapeHtml(text)}</p>`).join('')}</div>`).join('')}${data.creation.zoneHistory.settled?'':'<p>Перед входом в мир завершите историю жизни в Зоне.</p>'}${data.creation.zoneHistory.settled?'':'<button type="button" class="btn" onclick="completeCreationHistory()">Продолжить историю</button>'}</details>`:''}
        <div style="margin-bottom: 10px;">
            <label>Название</label>
            <select name="basic.background.templateId" class="form-control" style="width:100%;" onchange="fillBackgroundFromTemplate(this)">
                <option value="">-- Выберите предысторию --</option>
                ${backgroundTemplates.map(t => `<option value="${t.id}" ${bg.templateId == t.id || bg.ruleId && bg.ruleId===t.attributes?.ruleId ? 'selected' : ''} ${t.attributes?.available===false?'disabled':''}>${escapeHtml(t.name)}</option>`).join('')}
            </select>
        </div>
        <div style="display: flex; gap: 10px; margin-bottom: 10px;">
            <div style="flex: 1;">
                <label>Плюсы</label>
                <textarea class="form-control" name="basic.background.pluses" rows="5" style="min-height: auto;">${escapeHtml(ruleText(bg.pluses))}</textarea>
            </div>
            <div style="flex: 1;">
                <label>Минусы</label>
                <textarea class="form-control" name="basic.background.minuses" rows="5" style="min-height: auto;">${escapeHtml(ruleText(bg.minuses))}</textarea>
            </div>
        </div>
        <div>
            <label>Бонусы к навыкам</label>
            <div id="background-skill-bonuses">
                ${skillBonusesHtml}
            </div>
            <button type="button" class="btn btn-sm" onclick="addBackgroundSkillBonus()">+ Добавить бонус навыка</button>
        </div>
        ${window.isGM ? `<button type="button" class="btn btn-sm btn-secondary" onclick="openCreateBackgroundTemplateModal()" style="margin-top:10px;">➕ Создать кастомную предысторию</button>` : ''}

        <div style="display: grid; grid-template-columns: 120px 1fr 1fr; gap: 20px; margin-bottom: 15px; align-items: start;">
            <!-- Деньги -->
            <div style="display: flex; flex-direction: column; gap: 5px;">
                <label class="money-label">Деньги</label>
                <input type="number" class="form-control number-input" name="inventory.money" value="${inv.money || 0}" style="width: 100px;">
            </div>
        </div>
        <button type="button" class="btn btn-secondary scout-territory-btn" onclick="performCharacterTraitAction('scout_location')">Осмотреть территорию</button>
    `;

    // Правая колонка со здоровьем
    let rightHtml = `<div id="health-right-column"></div>`;

    let html = `
        <div style="display: flex; gap: 20px;">
            <div style="flex: 1;">${leftHtml}</div>
            <div style="flex: 1;">${rightHtml}</div>
        </div>
    `;
    const viewState = captureSheetViewState();
    container.innerHTML = html;

    const healthContainer = document.getElementById('health-right-column');
    if(data.creation?.completed&&!window.isGM){
        container.querySelectorAll('[name^="basic.background."],button[onclick^="addBackground"],button[onclick^="removeBackground"]').forEach(el=>el.disabled=true);
    }
    renderHealthTab(data, healthContainer);
    restoreSheetViewState(viewState);
}

window.updateCharacterTraitContext = function(actors) {
    if(!currentCharacterData||!backgroundIs(currentCharacterData,'Пожарный'))return;
    const actor=actors.find(actor=>Number(actor.character_id)===Number(currentCharacterId));if(!actor)return;
    const meta=(currentCharacterData.health||(currentCharacterData.health={})).combatMeta||(currentCharacterData.health.combatMeta={});
    const active=Boolean(actor.trait_state?.firefighterNearby);if(Boolean(meta.firefighterNearby)===active)return;
    meta.firefighterNearby=active;if(document.querySelector('#sheet-tabs .tab-btn.active')?.dataset.tab==='skills')renderSkillsTab(currentCharacterData);
};

window.resolveBrigadierRefusal = async function(targetId,choice) {
    const id=currentCharacterId;
    try{await forceSyncCharacter();const scene=await import('./locationScene.js');
        const state=await Server.getLocationCombatState(window.currentLobbyId,scene.getCurrentLocationId());
        const actor=state.characters.find(actor=>Number(actor.character_id)===Number(id));
        if(!actor)throw new Error('Персонаж не размещён');
        const result=await Server.performLocationCombatAction(window.currentLobbyId,scene.getCurrentLocationId(),{location_character_id:actor.location_character_id,action_key:'brigadier_refusal',target_character_id:targetId,attribute_choice:choice});
        scene.setCombatState(result.state);const saved=await Server.getCharacter(id);queueRemoteSheetData(saved.data,id);
    }catch(error){showNotification(error.message,'system');}
};

window.performCharacterTraitAction = async function(kind) {
    const id=currentCharacterId;
    try{await forceSyncCharacter();if(id!==currentCharacterId)return;
        const {showTraitAction}=await import('./traitActions.js');
        await showTraitAction(id,currentCharacterData,kind);
    }catch(error){showNotification(error.message||'Действие недоступно','system');}
};

window.activateTrueSight = async function(){
    const characterId=currentCharacterId;
    if(!window.isLocationActive){showNotification('Нужно находиться в подлокации','system');return;}
    closeCharacterSheet();
    try{const scene=await import('./locationScene.js');await scene.enableTrueSight(characterId);}
    catch(error){showNotification(error.message||'Истинное зрение недоступно','system');}
};

window.openSurgeonMenu = async function(){
    const characterId=currentCharacterId;
    if(!window.isLocationActive){showNotification('Нужно находиться в подлокации','system');return;}
    closeCharacterSheet();
    const scene=await import('./locationScene.js');await scene.showMedicalConsumableMenu(characterId);
};

window.setBackgroundTargetWeapon = function(key,selection){
    if(!['human','mutant'].includes(key))return;
    const choices=currentCharacterData.ruleChoices||(currentCharacterData.ruleChoices={});
    const selected=choices.targetWeapons||(choices.targetWeapons={});
    if(selection==='')delete selected[key];
    else{
        const entry=JSON.parse(selection),weapon=carriedWeaponEntries(currentCharacterData).find(item=>JSON.stringify(item.path)===JSON.stringify(entry.path))?.weapon;
        if(!weapon||entry.id&&weapon.id!==entry.id||!entry.id&&weapon.name!==entry.name){showNotification('Снаряжение изменилось. Выберите оружие заново','system');renderBasicTab(currentCharacterData);return;}
        weapon.id ||= crypto.randomUUID();
        const other=key==='human'?'mutant':'human';
        if(selected[other]===weapon.id){showNotification('Выберите два разных экземпляра оружия','system');renderBasicTab(currentCharacterData);return;}
        selected[key]=weapon.id;
    }
    scheduleAutoSave();
};

window.setCharacterRuleTag = function(tag,enabled){
    if(!window.isGM)return;
    const basic=currentCharacterData.basic||(currentCharacterData.basic={}),tags=new Set(basic.tags||[]);
    if(enabled)tags.add(tag);else tags.delete(tag);
    basic.tags=[...tags];scheduleAutoSave();
};

window.completeCreationHistory = async function(){
    try{
        await forceSyncCharacter();
        const {completeCharacterHistory}=await import('./characterHistory.js');
        await completeCharacterHistory(currentLobbyId,currentCharacterId,currentCharacterData,async data=>{
            currentCharacterData=data;await renderCharacterSheet(data.basic?.name||'',data);
        });
    }catch(error){showNotification(error.message);}
};

window.fillBackgroundFromTemplate = async function(select) {
    const selectedId = parseInt(select.value, 10);
    if (isNaN(selectedId)) {
        // Пустой выбор — очищаем поля
        const plusesInput = document.querySelector('textarea[name="basic.background.pluses"]');
        const minusesInput = document.querySelector('textarea[name="basic.background.minuses"]');
        if (plusesInput) plusesInput.value = '';
        if (minusesInput) minusesInput.value = '';
        document.getElementById('background-skill-bonuses').innerHTML = '';
        if (currentCharacterData.basic?.background) {
            delete currentCharacterData.basic.background.templateId;
            delete currentCharacterData.basic.background.ruleId;
            delete currentCharacterData.basic.background.name;
            delete currentCharacterData.basic.background.pluses;
            delete currentCharacterData.basic.background.minuses;
            delete currentCharacterData.basic.background.skillBonuses;
        }
        ensureHealthMaximums(currentCharacterData);
        refreshHealthPanel();
        scheduleAutoSave();
        return;
    }

    const templates = await loadTemplatesForLobby('background');
    const template = templates.find(t => t.id === selectedId);
    if (!template) return;

    // Заполняем поля
    const plusesInput = document.querySelector('textarea[name="basic.background.pluses"]');
    const minusesInput = document.querySelector('textarea[name="basic.background.minuses"]');
    if (plusesInput) plusesInput.value = ruleText(template.attributes?.pluses);
    if (minusesInput) minusesInput.value = ruleText(template.attributes?.minuses);

    // Обновляем бонусы к навыкам
    const skillBonuses = template.attributes?.skillBonuses || [];
    const container = document.getElementById('background-skill-bonuses');
    container.innerHTML = '';
    skillBonuses.forEach((bonus, index) => {
        const div = document.createElement('div');
        div.style.display = 'flex';
        div.style.gap = '5px';
        div.style.alignItems = 'center';
        div.style.marginBottom = '5px';
        div.innerHTML = `
            <select name="basic.background.skillBonuses.${index}.skill" class="form-control" style="flex:2;">
                ${skillCategories.map(cat => `<option value="${cat.path}" ${bonus.skill === cat.path ? 'selected' : ''}>${cat.label}</option>`).join('')}
            </select>
            <input type="number" class="form-control number-input" name="basic.background.skillBonuses.${index}.bonus" value="${bonus.bonus || 0}" style="width: 60px;" placeholder="Бонус">
            <button type="button" class="btn btn-sm btn-danger" onclick="removeBackgroundSkillBonus(${index})">✕</button>
        `;
        container.appendChild(div);
    });

    // Сохраняем в данные
    if (!currentCharacterData.basic) currentCharacterData.basic = {};
    if (!currentCharacterData.basic.background) currentCharacterData.basic.background = {};
    currentCharacterData.basic.background.templateId = template.id;
    currentCharacterData.basic.background.name = template.name;
    currentCharacterData.basic.background.pluses = ruleText(template.attributes?.pluses);
    currentCharacterData.basic.background.minuses = ruleText(template.attributes?.minuses);
    currentCharacterData.basic.background.skillBonuses = skillBonuses;
    currentCharacterData.basic.background.ruleId = template.attributes?.ruleId;

    ensureHealthMaximums(currentCharacterData);
    refreshHealthPanel();
    scheduleAutoSave();
};

// ========== 11. МОДАЛЬНЫЕ ОКНА ШАБЛОНОВ ==========
window.openCreateBackgroundTemplateModal = function() {
    let modal = document.getElementById('create-background-template-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'create-background-template-modal';
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content" style="max-height: 80vh; overflow-y: auto;">
                <span class="close" onclick="document.getElementById('create-background-template-modal').style.display='none'">&times;</span>
                <h3>Создать кастомную предысторию</h3>
                <div class="form-group">
                    <label>Название</label>
                    <input type="text" id="background-name" class="form-control">
                </div>
                <div class="form-group">
                    <label>Плюсы</label>
                    <textarea id="background-pluses" class="form-control" rows="3"></textarea>
                </div>
                <div class="form-group">
                    <label>Минусы</label>
                    <textarea id="background-minuses" class="form-control" rows="3"></textarea>
                </div>
                <div class="form-group">
                    <label>Бонусы к навыкам</label>
                    <div id="background-skill-bonuses-container"></div>
                    <button type="button" class="btn btn-sm btn-primary" onclick="addBackgroundSkillBonusToModal()">+ Добавить бонус</button>
                </div>
                <div class="form-actions">
                    <button class="btn btn-primary" onclick="saveBackgroundTemplate()">Сохранить</button>
                    <button class="btn btn-secondary" onclick="document.getElementById('create-background-template-modal').style.display='none'">Отмена</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }
    // Очищаем список бонусов при открытии
    const container = document.getElementById('background-skill-bonuses-container');
    if (container) container.innerHTML = '';
    modal.style.display = 'flex';
};

window.addBackgroundSkillBonusToModal = function() {
    const container = document.getElementById('background-skill-bonuses-container');
    const div = document.createElement('div');
    div.className = 'skill-bonus-item';
    div.style.display = 'flex';
    div.style.gap = '5px';
    div.style.marginBottom = '5px';
    div.innerHTML = `
        <select class="form-control skill-select" style="flex:2;">
            ${skillCategories.map(cat => `<option value="${cat.path}">${cat.label}</option>`).join('')}
        </select>
        <input type="number" class="form-control number-input bonus-input" placeholder="Бонус" value="0" style="width: 80px;">
        <button type="button" class="btn btn-sm btn-danger" onclick="this.parentElement.remove()">✕</button>
    `;
    container.appendChild(div);
};

window.saveBackgroundTemplate = async function() {
    const name = document.getElementById('background-name').value;
    const pluses = document.getElementById('background-pluses').value;
    const minuses = document.getElementById('background-minuses').value;

    const skillBonuses = [];
    const items = document.querySelectorAll('#background-skill-bonuses-container .skill-bonus-item');
    items.forEach(item => {
        const skillSelect = item.querySelector('.skill-select');
        const bonusInput = item.querySelector('.bonus-input');
        if (skillSelect && bonusInput) {
            const skill = skillSelect.value;
            const bonus = parseInt(bonusInput.value) || 0;
            if (skill && bonus !== 0) {
                skillBonuses.push({ skill, bonus });
            }
        }
    });

    const attributes = { pluses, minuses, skillBonuses };
    const data = {
        name: name,
        category: 'background',
        subcategory: null,
        price: 0,
        weight: 0,
        volume: 0,
        attributes: attributes
    };

    try {
        await Server.createLobbyTemplate(currentLobbyId, data);
        clearTemplatesCache('background');
        clearAllTemplatesCache();
        await renderBasicTab(currentCharacterData);
        document.getElementById('create-background-template-modal').style.display = 'none';
        showNotification('Шаблон предыстории создан', 'success');
    } catch (err) {
        showNotification(err.message);
    }
};

window.addBackgroundSkillBonus = function() {
    updateDataFromFields();
    if (!currentCharacterData.basic) currentCharacterData.basic = {};
    if (!currentCharacterData.basic.background) currentCharacterData.basic.background = {};
    if (!Array.isArray(currentCharacterData.basic.background.skillBonuses)) {
        currentCharacterData.basic.background.skillBonuses = [];
    }
    currentCharacterData.basic.background.skillBonuses.push({ skill: 'physical.strength', bonus: 0 });
    renderBasicTab(currentCharacterData);
    scheduleAutoSave();
};

window.removeBackgroundSkillBonus = function(index) {
    updateDataFromFields();
    if (!currentCharacterData.basic?.background?.skillBonuses) return;
    currentCharacterData.basic.background.skillBonuses.splice(index, 1);
    renderBasicTab(currentCharacterData);
    scheduleAutoSave();
};

window.openCreateModuleTemplateModal = function(template = null) {
    let modal = document.getElementById('create-module-template-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'create-module-template-modal';
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content" style="max-height: 80vh; overflow-y: auto;">
                <span class="close" onclick="document.getElementById('create-module-template-modal').style.display='none'">&times;</span>
                <h3>${template ? 'Редактировать' : 'Создать'} модуль</h3>
                <input type="hidden" id="module-template-id">
                <div class="form-group"><label>Название</label><input type="text" id="module-name" class="form-control"></div>
                <div class="form-group"><label>Тип слота</label><select id="module-slot-type" class="form-control"><option value="scope">Прицел</option><option value="barrel">Ствол</option><option value="handguard">Цевье</option></select></div>
                <div class="form-group" id="module-caliber-group"><label>Калибр</label><input type="text" id="module-caliber" class="form-control"></div>
                <h4>Модификаторы</h4>
                <div class="form-group"><label>Эргономика</label><input type="text" id="module-ergonomics" class="form-control" value="0"></div>
                <div class="form-group"><label>Точность</label><input type="text" id="module-accuracy" class="form-control" value="0"></div>
                <div class="form-group"><label>Дальность</label><input type="text" id="module-range" class="form-control" value="0"></div>
                <div class="form-group"><label>Шум</label><input type="text" id="module-noise" class="form-control" value="0"></div>
                <div class="form-group"><label>Объём</label><input type="number" id="module-volume" class="form-control number-input" value="0" step="0.1"></div>
                <div class="form-group"><label>Вес</label><input type="number" id="module-weight" class="form-control number-input" value="0.5" step="0.1"></div>
                <div class="form-group"><label>Цена</label><input type="number" id="module-price" class="form-control number-input" value="0"></div>
                <div class="form-actions"><button class="btn btn-primary" onclick="saveModuleTemplate()">Сохранить</button><button class="btn btn-secondary" onclick="document.getElementById('create-module-template-modal').style.display='none'">Отмена</button></div>
            </div>`;
        document.body.appendChild(modal);
    }
    const caliberGroup = document.getElementById('module-caliber-group');
    const slotSelect = document.getElementById('module-slot-type');
    slotSelect.onchange = () => caliberGroup.style.display = slotSelect.value === 'barrel' ? 'block' : 'none';
    if (template) {
        document.getElementById('module-template-id').value = template.id;
        document.getElementById('module-name').value = template.name || '';
        document.getElementById('module-slot-type').value = template.attributes?.slot_type || 'scope';
        caliberGroup.style.display = template.attributes?.slot_type === 'barrel' ? 'block' : 'none';
        document.getElementById('module-caliber').value = template.attributes?.caliber || '';
        document.getElementById('module-ergonomics').value = template.attributes?.modifiers?.ergonomics || '0';
        document.getElementById('module-accuracy').value = template.attributes?.modifiers?.accuracy || '0';
        document.getElementById('module-range').value = template.attributes?.modifiers?.range || '0';
        document.getElementById('module-noise').value = template.attributes?.modifiers?.noise || '0';
        document.getElementById('module-volume').value = template.volume || 0;
        document.getElementById('module-weight').value = template.weight || 0.5;
        document.getElementById('module-price').value = template.price || 0;
    } else {
        document.getElementById('module-template-id').value = '';
    }
    modal.style.display = 'flex';
};

window.saveModuleTemplate = async function() {
    const id = document.getElementById('module-template-id').value;
    const name = document.getElementById('module-name').value.trim();
    if (!name) { showNotification('Введите название'); return; }
    const slotType = document.getElementById('module-slot-type').value;
    const caliber = slotType === 'barrel' ? document.getElementById('module-caliber').value.trim() : null;
    const modifiers = {
        ergonomics: document.getElementById('module-ergonomics').value.trim(),
        accuracy: document.getElementById('module-accuracy').value.trim(),
        range: document.getElementById('module-range').value.trim(),
        noise: document.getElementById('module-noise').value.trim()
    };
    const data = {
        name, category: 'weapon_module', subcategory: slotType,
        price: parseInt(document.getElementById('module-price').value) || 0,
        weight: parseFloat(document.getElementById('module-weight').value) || 0.5,
        volume: parseFloat(document.getElementById('module-volume').value) || 0,
        attributes: { slot_type: slotType, caliber, modifiers }
    };
    try {
        if (id) await Server.updateLobbyTemplate(currentLobbyId, id, data);
        else await Server.createLobbyTemplate(currentLobbyId, data);
        clearTemplatesCache('weapon_module'); clearAllTemplatesCache();
        document.getElementById('create-module-template-modal').style.display = 'none';
        showNotification(id ? 'Модуль обновлён' : 'Модуль создан', 'success');
        if (typeof loadTemplatesForManager === 'function') {
            const active = document.querySelector('#templates-modal .tab-btn.active')?.dataset.cat;
            if (active === 'weapon_module') loadTemplatesForManager('weapon_module');
        }
    } catch (e) { showNotification(e.message); }
};

window.openCreateMagazineTemplateModal = function(template = null) {
    let modal = document.getElementById('create-magazine-template-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'create-magazine-template-modal';
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content" style="max-height: 80vh; overflow-y: auto;">
                <span class="close" onclick="document.getElementById('create-magazine-template-modal').style.display='none'">&times;</span>
                <h3>${template ? 'Редактировать' : 'Создать'} магазин</h3>
                <input type="hidden" id="magazine-template-id">
                <div class="form-group"><label>Название</label><input type="text" id="magazine-name" class="form-control"></div>
                <div class="form-group"><label>Калибр</label><input type="text" id="magazine-caliber" class="form-control" placeholder="например, 5.45x39"></div>
                <div class="form-group"><label>Ёмкость</label><input type="number" id="magazine-capacity" class="form-control number-input" value="30"></div>
                <div class="form-group"><label>Вес пустого</label><input type="number" id="magazine-empty-weight" class="form-control number-input" value="0" step="0.1"></div>
                <div class="form-group"><label>Вес снаряжённого</label><input type="number" id="magazine-loaded-weight" class="form-control number-input" value="0" step="0.1"></div>
                <div class="form-group"><label>Объём</label><input type="number" id="magazine-volume" class="form-control number-input" value="0" step="0.1"></div>
                <div class="form-group"><label>Цена</label><input type="number" id="magazine-price" class="form-control number-input" value="0"></div>
                <div class="form-group"><label><input type="checkbox" id="magazine-is-loader"> Это спидлоадер / лента</label></div>
                <div class="form-group">
                    <label>Совместимые ID оружия (через запятую)</label>
                    <input type="text" id="magazine-compatible-weapons" class="form-control" placeholder="Например: 100,102">
                </div>
                <div class="form-actions"><button class="btn btn-primary" onclick="saveMagazineTemplate()">Сохранить</button><button class="btn btn-secondary" onclick="document.getElementById('create-magazine-template-modal').style.display='none'">Отмена</button></div>
            </div>`;
        document.body.appendChild(modal);
    }
    if (template) {
        document.getElementById('magazine-template-id').value = template.id;
        document.getElementById('magazine-name').value = template.name || '';
        document.getElementById('magazine-caliber').value = template.attributes?.caliber || '';
        document.getElementById('magazine-capacity').value = template.attributes?.capacity || 30;
        document.getElementById('magazine-empty-weight').value = template.attributes?.emptyWeight || 0;
        document.getElementById('magazine-loaded-weight').value = template.attributes?.loadedWeight || 0;
        document.getElementById('magazine-volume').value = template.volume || 0;
        document.getElementById('magazine-price').value = template.price || 0;
        document.getElementById('magazine-is-loader').checked = template.attributes?.isLoader || false;
        const compatible = template.attributes?.compatible_weapons || [];
        document.getElementById('magazine-compatible-weapons').value = compatible.join(',');
    } else {
        document.getElementById('magazine-template-id').value = '';
    }
    modal.style.display = 'flex';
};

window.saveMagazineTemplate = async function() {
    const id = document.getElementById('magazine-template-id').value;
    const name = document.getElementById('magazine-name').value.trim();
    if (!name) { showNotification('Введите название'); return; }

    const compatibleStr = document.getElementById('magazine-compatible-weapons').value.trim();
    const compatible_weapons = compatibleStr ? compatibleStr.split(',').map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n)) : [];

    const data = {
        name, category: 'magazine', subcategory: null,
        price: parseInt(document.getElementById('magazine-price').value) || 0,
        volume: parseFloat(document.getElementById('magazine-volume').value) || 0,
        weight: 0,
        attributes: {
            caliber: document.getElementById('magazine-caliber').value.trim(),
            capacity: parseInt(document.getElementById('magazine-capacity').value) || 30,
            emptyWeight: parseFloat(document.getElementById('magazine-empty-weight').value) || 0,
            loadedWeight: parseFloat(document.getElementById('magazine-loaded-weight').value) || 0,
            isLoader: document.getElementById('magazine-is-loader').checked,
            compatible_weapons: compatible_weapons
        }
    };
    try {
        if (id) await Server.updateLobbyTemplate(currentLobbyId, id, data);
        else await Server.createLobbyTemplate(currentLobbyId, data);
        clearTemplatesCache('magazine'); clearAllTemplatesCache();
        document.getElementById('create-magazine-template-modal').style.display = 'none';
        showNotification(id ? 'Магазин обновлён' : 'Магазин создан', 'success');
        if (typeof loadTemplatesForManager === 'function') {
            const active = document.querySelector('#templates-modal .tab-btn.active')?.dataset.cat;
            if (active === 'magazine') loadTemplatesForManager('magazine');
        }
    } catch (e) { showNotification(e.message); }
};

window.openCreateAmmoTemplateModal = function(template = null) {
    let modal = document.getElementById('create-ammo-template-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'create-ammo-template-modal';
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content" style="max-height: 80vh; overflow-y: auto;">
                <span class="close" onclick="document.getElementById('create-ammo-template-modal').style.display='none'">&times;</span>
                <h3>${template ? 'Редактировать' : 'Создать'} патроны</h3>
                <input type="hidden" id="ammo-template-id">
                <div class="form-group"><label>Название</label><input type="text" id="ammo-name" class="form-control"></div>
                <div class="form-group"><label>Калибр</label><input type="text" id="ammo-caliber" class="form-control" placeholder="например, 5.45x39"></div>
                <div class="form-group">
                    <label>Вариация патрона</label>
                    <select id="ammo-variant" class="form-control">
                        <option value="">Обычные</option>
                        <option value="bp">БП</option>
                        <option value="ep">ЭП</option>
                        <option value="ubp">УБП</option>
                        <option value="rip">RIP</option>
                        <option value="explosive">Взрывные</option>
                        <option value="incendiary">Зажигательные</option>
                        <option value="flashbang">Светошумовые</option>
                        <option value="smoke">Дымовые</option>
                        <option value="gas">Газовые</option>
                    </select>
                    <small style="display:block; margin-top:4px; opacity:0.75;">Выбирается только один вариант.</small>
                </div>
                <div class="form-group"><label>Категория покупки</label><input type="text" id="ammo-purchase-category" class="form-control" placeholder="например, боевые, охотничьи"></div>
                <div class="form-group"><label>Урон</label><input type="number" id="ammo-damage" class="form-control number-input" value="0"></div>
                <div class="form-group"><label>Пробитие, %</label><input type="number" id="ammo-penetration" class="form-control number-input" value="0" min="0" max="100"></div>
                <div class="form-group"><label>Дальность</label><input type="number" id="ammo-range" class="form-control number-input" value="0"></div>
                <div class="form-group"><label>Примечания</label><textarea id="ammo-notes" class="form-control" rows="3"></textarea></div>
                <div class="form-group"><label>Цена</label><input type="number" id="ammo-price" class="form-control number-input" value="0"></div>
                <div class="form-group"><label>Вес</label><input type="number" id="ammo-weight" class="form-control number-input" value="0" step="0.1"></div>
                <div class="form-group"><label>Объём</label><input type="number" id="ammo-volume" class="form-control number-input" value="0" step="0.1"></div>
                <div class="form-actions"><button class="btn btn-primary" onclick="saveAmmoTemplate()">Сохранить</button><button class="btn btn-secondary" onclick="document.getElementById('create-ammo-template-modal').style.display='none'">Отмена</button></div>
            </div>`;
        document.body.appendChild(modal);
    }
    if (template) {
        document.getElementById('ammo-template-id').value = template.id;
        document.getElementById('ammo-name').value = template.name || '';
        document.getElementById('ammo-caliber').value = template.attributes?.caliber || '';
        document.getElementById('ammo-variant').value = normalizeAmmoVariant(template.attributes?.ammo_variant || template.attributes?.ammo_kind || template.attributes?.special_version || template.attributes?.effect) || '';
        document.getElementById('ammo-purchase-category').value = template.attributes?.purchase_category || '';
        document.getElementById('ammo-damage').value = template.attributes?.damage ?? 0;
        document.getElementById('ammo-penetration').value = formatAmmoPenetration(template.attributes?.penetration ?? 0).replace('%', '');
        document.getElementById('ammo-range').value = template.attributes?.range ?? 0;
        document.getElementById('ammo-notes').value = template.attributes?.notes || '';
        document.getElementById('ammo-price').value = template.price || 0;
        document.getElementById('ammo-weight').value = template.weight || 0;
        document.getElementById('ammo-volume').value = template.volume || 0;
    } else {
        document.getElementById('ammo-template-id').value = '';
        document.getElementById('ammo-name').value = '';
        document.getElementById('ammo-caliber').value = '';
        document.getElementById('ammo-variant').value = '';
        document.getElementById('ammo-purchase-category').value = '';
        document.getElementById('ammo-damage').value = 0;
        document.getElementById('ammo-penetration').value = 0;
        document.getElementById('ammo-range').value = 0;
        document.getElementById('ammo-notes').value = '';
        document.getElementById('ammo-price').value = 0;
        document.getElementById('ammo-weight').value = 0;
        document.getElementById('ammo-volume').value = 0;
    }
    modal.style.display = 'flex';
};

window.saveAmmoTemplate = async function() {
    const id = document.getElementById('ammo-template-id').value;
    const name = document.getElementById('ammo-name').value.trim();
    if (!name) { showNotification('Введите название'); return; }

    const caliber = document.getElementById('ammo-caliber').value.trim();
    const ammoVariant = document.getElementById('ammo-variant').value.trim();
    const purchaseCategory = document.getElementById('ammo-purchase-category').value.trim();
    const notes = document.getElementById('ammo-notes').value.trim();
    const penetrationPercent = parseFloat(document.getElementById('ammo-penetration').value);
    const penetration = Number.isFinite(penetrationPercent) ? Math.max(0, penetrationPercent) / 100 : 0;

    const data = {
        name,
        category: 'ammo',
        subcategory: caliber || ammoVariant || null,
        item_class: purchaseCategory || null,
        price: parseInt(document.getElementById('ammo-price').value) || 0,
        weight: parseFloat(document.getElementById('ammo-weight').value) || 0,
        volume: parseFloat(document.getElementById('ammo-volume').value) || 0,
        attributes: {
            caliber,
            ammo_variant: ammoVariant || null,
            purchase_category: purchaseCategory,
            damage: parseInt(document.getElementById('ammo-damage').value) || 0,
            penetration,
            range: parseInt(document.getElementById('ammo-range').value) || 0,
            notes: notes || null
        }
    };

    try {
        if (id) await Server.updateLobbyTemplate(currentLobbyId, id, data);
        else await Server.createLobbyTemplate(currentLobbyId, data);
        clearTemplatesCache('ammo');
        clearAllTemplatesCache();
        document.getElementById('create-ammo-template-modal').style.display = 'none';
        showNotification(id ? 'Патроны обновлены' : 'Патроны созданы', 'success');
        if (typeof loadTemplatesForManager === 'function') {
            const active = document.querySelector('#templates-modal .tab-btn.active')?.dataset.cat;
            if (active === 'ammo') loadTemplatesForManager('ammo');
        }
    } catch (e) { showNotification(e.message); }
};

// ----- МЕНЕДЖЕР ШАБЛОНОВ -----
window.openTemplatesManager = function() {
    document.getElementById('templates-modal').style.display = 'flex';
    loadTemplatesForManager('weapon');

    // Обработчики вкладок
    document.querySelectorAll('#templates-modal .tab-btn').forEach(btn => {
        btn.onclick = () => {
            document.querySelectorAll('#templates-modal .tab-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            loadTemplatesForManager(btn.dataset.cat);
        };
    });
};

window.closeTemplatesManager = function() {
    document.getElementById('templates-modal').style.display = 'none';
};

function getTemplateManagerActionButtons(category) {
    const buttons = [];
    if (category === 'magazine') {
        buttons.push(`<button type="button" class="btn btn-sm btn-primary" onclick="openCreateMagazineTemplateModal()">➕ Создать магазин</button>`);
    }
    return buttons.join('');
}

function renderTemplateManagerCards(category, templates) {
    const isAmmo = category === 'ammo';
    const showActions = category !== 'ammo';
    const gridStyle = 'display:grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 10px;';
    let html = `<div style="${gridStyle}">`;
    templates.forEach(t => {
        const attrs = t.attributes || {};
        const tags = [];
        if (isAmmo) {
            if (attrs.caliber) tags.push(`Калибр: ${escapeHtml(String(attrs.caliber))}`);
            if (attrs.damage !== undefined && attrs.damage !== null) tags.push(`Урон: ${escapeHtml(String(attrs.damage))}`);
            if (attrs.penetration !== undefined && attrs.penetration !== null) tags.push(`Пробитие: ${escapeHtml(formatAmmoPenetration(attrs.penetration))}`);
            if (attrs.range !== undefined && attrs.range !== null) tags.push(`Дальность: ${escapeHtml(String(attrs.range))}`);
            const ammoVariants = attrs.ammo_variants?.length ? attrs.ammo_variants : (attrs.ammo_variant ? [attrs.ammo_variant] : []);
            if (ammoVariants.length) tags.push(`Вариации: ${escapeHtml(getAmmoVariantLabels(ammoVariants))}`);
            if (attrs.purchase_category) tags.push(`Категория: ${escapeHtml(String(attrs.purchase_category))}`);
        } else {
            if (attrs.caliber) tags.push(`Калибр: ${escapeHtml(String(attrs.caliber))}`);
            if (attrs.capacity !== undefined && attrs.capacity !== null) tags.push(`Ёмкость: ${escapeHtml(String(attrs.capacity))}`);
            if (attrs.reload_time_od !== undefined && attrs.reload_time_od !== null) tags.push(`Перезарядка: ${escapeHtml(String(attrs.reload_time_od))} ОД`);
            if (attrs.isLoader) tags.push('Спидлоадер/лента');
            if (Array.isArray(attrs.compatible_weapons) && attrs.compatible_weapons.length) tags.push(`Оружие: ${escapeHtml(String(attrs.compatible_weapons.length))}`);
        }
        html += `
            <div style="border:1px solid rgba(255,255,255,0.12); border-radius:10px; padding:12px; background: rgba(0,0,0,0.18);">
                <div style="display:flex; justify-content:space-between; gap:10px; align-items:flex-start;">
                    <div>
                        <div style="font-weight:700; font-size:15px; line-height:1.2;">${escapeHtml(t.name)}</div>
                        <div style="opacity:0.7; font-size:12px; margin-top:2px;">ID ${t.id}${t.subcategory ? ` • ${escapeHtml(t.subcategory)}` : ''}</div>
                    </div>
                    <div style="font-size:12px; opacity:0.7;">${escapeHtml(category === 'ammo' ? 'Патроны' : 'Магазин')}</div>
                </div>
                <div style="display:flex; flex-wrap:wrap; gap:6px; margin-top:10px;">
                    ${tags.map(tag => `<span style="font-size:12px; padding:3px 7px; border-radius:999px; background: rgba(255,255,255,0.08);">${tag}</span>`).join('')}
                </div>
                ${showActions ? `<div style="display:flex; gap:8px; margin-top:12px;">
                    <button class="btn-sm btn-primary" onclick="editTemplate(${t.id}, '${category}')">✏️</button>
                    <button class="btn-sm btn-danger" onclick="deleteTemplate(${t.id}, '${category}')">🗑️</button>
                </div>` : '<div style="margin-top:12px; font-size:12px; opacity:0.7;">Глобальный шаблон</div>'}
            </div>`;
    });
    html += '</div>';
    return html;
}

async function loadTemplatesForManager(category) {
    const container = document.getElementById('templates-list');
    const actionsContainer = document.getElementById('templates-manager-actions');
    if (actionsContainer) actionsContainer.innerHTML = getTemplateManagerActionButtons(category);
    container.innerHTML = 'Загрузка...';
    try {
        const data = await Server.getLobbyTemplates(currentLobbyId, category);
        const templates = category === 'ammo'
            ? [...(data.global || [])]
            : data.local;
        if (!templates.length) {
            container.innerHTML = '<p>Нет кастомных шаблонов</p>';
            return;
        }
        const uniqueTemplates = [];
        const seenIds = new Set();
        templates.forEach(t => {
            if (!t || seenIds.has(t.id)) return;
            seenIds.add(t.id);
            uniqueTemplates.push(t);
        });
        if (category === 'ammo' || category === 'magazine') {
            container.innerHTML = renderTemplateManagerCards(category, uniqueTemplates);
            return;
        }
        let html = '<table style="width:100%"><tr><th>ID</th><th>Название</th><th>Подкатегория</th><th></th></tr>';
        uniqueTemplates.forEach(t => {
            html += `<tr><td>${t.id}</td><td>${escapeHtml(t.name)}</td><td>${t.subcategory || ''}</td>
            <td>
                <button class="btn-sm btn-primary" onclick="editTemplate(${t.id}, '${category}')">✏️</button>
                <button class="btn-sm btn-danger" onclick="deleteTemplate(${t.id}, '${category}')">🗑️</button>
            </td></tr>`;
        });
        html += '</table>';
        container.innerHTML = html;
    } catch(e) {
        container.innerHTML = '<p class="error">Ошибка загрузки</p>';
    }
}

window.deleteTemplate = async function(id, category) {
    if (!confirm('Удалить шаблон?')) return;
    try {
        await Server.deleteLobbyTemplate(currentLobbyId, id);
        clearTemplatesCache(category);
        clearAllTemplatesCache();
        showNotification('Шаблон удалён', 'success');
        loadTemplatesForManager(category);
    } catch(e) {
        showNotification(e.message);
    }
};

window.editTemplate = async function(templateId, category) {
    // Загружаем шаблоны напрямую с сервера, минуя кеш с миллионом
    const data = await Server.getLobbyTemplates(currentLobbyId, category);
    const template = data.local.find(t => t.id === templateId);
    if (!template) { showNotification('Шаблон не найден'); return; }

    switch (category) {
        case 'weapon': openCreateWeaponTemplateModal(null, template); break;
        case 'armor': openCreateArmorTemplateModal(template); break;
        case 'helmet': openCreateHelmetTemplateModal(template); break;
        case 'gas_mask': openCreateGasMaskTemplateModal(template); break;
        case 'backpack': openCreateBackpackTemplateModal(template); break;
        case 'vest': openCreateVestTemplateModal(template); break;
        case 'weapon_module': openCreateModuleTemplateModal(template); break;
        case 'magazine': openCreateMagazineTemplateModal(template); break;
        case 'ammo': showNotification('Патроны — глобальные шаблоны только для просмотра'); break;
        case 'melee_weapon': openCreateMeleeWeaponTemplateModal(template); break; // если будет модалка
        default: showNotification('Редактирование не поддерживается');
    }
};

function normalizeDailyNeeds(rawNeeds = {}) {
    const needs = rawNeeds && typeof rawNeeds === 'object' ? rawNeeds : {};
    const missedDays = needs.missedDays && typeof needs.missedDays === 'object' ? needs.missedDays : {};
    const sleptToday = needs.sleptToday === true || String(needs.sleptToday).toLowerCase() === 'true';
    return {
        day: Math.max(1, Number(needs.day || 1)),
        mealsToday: Math.max(0, Math.min(3, Number(needs.mealsToday || 0))),
        drinksToday: Math.max(0, Math.min(3, Number(needs.drinksToday || 0))),
        sleptToday,
        missedDays: Object.fromEntries(['food', 'water', 'sleep'].map(key => {
            const days = Number(missedDays[key]);
            return [key, Number.isFinite(days) ? Math.max(0, Math.floor(days)) : 0];
        })),
        lastDay: needs.lastDay && typeof needs.lastDay === 'object' ? needs.lastDay : null,
    };
}

const BASE_HEALTH_MAXIMUMS = {
    max: 700,
    zones: {
        head: 50,
        chest: 150,
        abdomen: 120,
        leftArm: 90,
        rightArm: 90,
        leftLeg: 100,
        rightLeg: 100,
    },
};
const BASE_ORGAN_MAXIMUMS = {
    heart: 20, rightLung: 40, leftLung: 40,
    rightKidney: 25, leftKidney: 25, stomach: 25, liver: 20,
    rightEye: 15, leftEye: 15, nose: 20, jaw: 20,
    rightEar: 20, leftEar: 20, brain: 1, skull: 50, spine: 1,
};

function hasMountainBackground(data) {
    const background = data?.basic?.background || {};
    const name = String(background.name || '').trim().toLocaleLowerCase('ru');
    const pluses = String(background.pluses || '').trim().toLocaleLowerCase('ru');
    return name === '\u0433\u043e\u0440\u0430'
        || (
            pluses.includes('\u043e\u0431\u0449\u0435\u0435 \u0437\u0434\u043e\u0440\u043e\u0432\u044c\u0435 \u0443\u0432\u0435\u043b\u0438\u0447\u0435\u043d\u043e \u043d\u0430 20%')
            && pluses.includes('\u0437\u0434\u043e\u0440\u043e\u0432\u044c\u0435 \u0440\u0443\u043a \u0438 \u043d\u043e\u0433 \u0443\u0432\u0435\u043b\u0438\u0447\u0435\u043d\u043e \u043d\u0430 35')
        );
}

function ensureHealthMaximums(data) {
    const health = data.health || (data.health = {});
    const temperature = Number(health.temperature);
    if (!Number.isFinite(temperature) || temperature <= 0) {
        health.temperature = 36;
    }
    const mountain = hasMountainBackground(data);
    const profileName = mountain ? 'mountain' : 'base';
    const profile = {
        max: mountain ? 840 : BASE_HEALTH_MAXIMUMS.max,
        zones: { ...BASE_HEALTH_MAXIMUMS.zones },
    };
    if (mountain) {
        ['leftArm', 'rightArm', 'leftLeg', 'rightLeg'].forEach((key) => {
            profile.zones[key] += 35;
        });
    }

    const scaleCurrent = (current, oldMax, newMax) => {
        const currentNumber = Number(current);
        const oldMaxNumber = Number(oldMax);
        if (!Number.isFinite(currentNumber)) return newMax;
        if (!Number.isFinite(oldMaxNumber) || oldMaxNumber <= 0) {
            return Math.min(newMax, Math.max(0, currentNumber));
        }
        return newMax * Math.min(1, Math.max(0, currentNumber / oldMaxNumber));
    };

    const profileChanged = health.maximumProfile !== profileName;
    if (profileChanged || Number(health.max) !== profile.max) {
        health.current = scaleCurrent(health.current, health.max, profile.max);
        health.max = profile.max;
    }

    const zones = health.zones || (health.zones = {});
    Object.entries(profile.zones).forEach(([key, newMax]) => {
        const zone = zones[key] || (zones[key] = {});
        if (profileChanged || Number(zone.max) !== newMax) {
            zone.current = scaleCurrent(zone.current, zone.max, newMax);
            zone.max = newMax;
        }
    });
    const organs = health.organs || (health.organs = {});
    Object.entries(BASE_ORGAN_MAXIMUMS).forEach(([key, newMax]) => {
        const organ = organs[key] || (organs[key] = {});
        if (!Number.isFinite(Number(organ.max)) || Number(organ.max) <= 0) {
            organ.current = scaleCurrent(organ.current, organ.max, newMax);
            organ.max = newMax;
        }
    });
    health.maximumProfile = profileName;
    return health;
}

// ========== 12. ВКЛАДКА "ЗДОРОВЬЕ" ==========
function isStressManifestationEffect(effect) {
    return (
        ['stress_effect', 'stress_stupor', 'phobia'].includes(String(effect?.type || ''))
        || effect?.source === 'stress_manifestation'
    );
}

const INSTANT_HEALTH_EFFECTS = new Set(['heal','radiation','pain','exhaustion','stress','intoxication','infection']);

function renderHealthTab(data, container = null) {
    const targetContainer = container || document.getElementById('sheet-tab-health');
    if (!targetContainer) return;

    const health = ensureHealthMaximums(data);
    syncHealthDerivedStatuses(health);
    const zones = health.zones || {};
    const organs = health.organs || {};
    const bleeding = health.bleeding || {};
    const bleedingEffects = Array.isArray(bleeding.effects) ? bleeding.effects : [];
    const currentBlood = health.blood || health.bloodStage || 'normal';
    const willSkill = data.skills?.physical?.will || {};
    const willBase = Number.isFinite(Number(willSkill.base)) ? Number(willSkill.base) : 10;
    const willBonus = Math.floor((willBase - 10) / 2) + (Number.isFinite(Number(willSkill.bonus)) ? Number(willSkill.bonus) : 0);
    const bleedingModifierTotal = Number.isFinite(Number(bleeding.modifierTotal)) ? Number(bleeding.modifierTotal) : 0;
    const finalBleedingDc = Math.max(0, (bleeding.baseDifficulty ?? 5) + (bleeding.totalSeverity || 0) - (bleeding.stagePenalty || 0) + bleedingModifierTotal - willBonus);

    const bloodOptions = [
        { value: 'normal', label: 'Нормально' },
        { value: 'light', label: 'Легкая кровопотеря' },
        { value: 'medium', label: 'Средняя кровопотеря' },
        { value: 'severe', label: 'Сильная кровопотеря' },
        { value: 'critical', label: 'Критическая кровопотеря' },
        { value: 'fatal', label: 'Смертельная кровопотеря' }
    ];
    const bloodSelect = bloodOptions.map(opt =>
        `<option value="${opt.value}" ${currentBlood === opt.value ? 'selected' : ''}>${opt.label}</option>`
    ).join('');
    const bloodTypeKnown = Boolean(health.combatMeta?.bloodTypeKnown);
    const bloodTypeValue = health.combatMeta?.bloodType;
    const directStatusChips = [
        { key: 'radiation', label: 'Радиация', value: health.radiation, color: '#d88f4b' },
        { key: 'intoxication', label: 'Опьянение', value: health.intoxication, color: '#9bb8ff' },
        { key: 'painLevel', label: 'Боль', value: health.painLevel, color: '#ff8a8a' },
        { key: 'stress', label: 'Стресс', value: health.stress, color: '#d4a5ff' },
        { key: 'psyState', label: 'Пси-состояние', value: health.psyState ?? health.psy_state, color: '#72d6c9' },
        { key: 'exhaustion', label: 'Истощение', value: health.exhaustion, color: '#ffd67d' },
        { key: 'infection', label: 'Заражение', value: health.infection, color: '#86d48f' },
    ].filter(item => Number.isFinite(Number(item.value)) && Number(item.value) !== 0);
    const storedEffects = Array.isArray(health.effects) ? normalizeEffectList(health.effects) : [];
    const stressEffects = storedEffects
        .map((effect, index) => ({ effect, index }))
        .filter(({ effect }) => isStressManifestationEffect(effect) && effect?.active !== false);
    const disabledZoneChips = Object.entries(zones)
        .filter(([, zone]) => Number(zone?.max || 0) > 0 && Number(zone?.current) <= 0)
        .map(([area]) => `Выбита зона: ${getEffectAreaLabel(area)}`);
    const needs = normalizeDailyNeeds(health.needs);
    health.needs = needs;
    const modifierLabels = {
        strength: 'Сила', agility: 'Ловкость', accuracy: 'Точность', weight: 'Штраф нагрузки', movement_points:'Штраф перемещения', hp_max:'Максимум ОЗ',
        will: 'Воля', psy_defense: 'Пси-защита', vision_awareness: 'Внимательность (зрение)',
        darkness_awareness: 'Внимательность в темноте', light_awareness: 'Внимательность на свету',
        darkness_visual_immunity: 'Без помехи к зрению в темноте',
        darkness_shooting_immunity: 'Без помехи к стрельбе в темноте',
        action_points: 'ОД', organ_toughness_multiplier: 'Живучесть органов', rest_heal_multiplier: 'Лечение на отдыхе'
    };
    const modifierChips = (health.combatMeta?.consumableModifiers || []).filter(modifier => modifier.active !== false && (modifier.remaining == null || Number(modifier.remaining) > 0)).map(modifier => {
        const label = modifierLabels[modifier.stat] || 'Модификатор';
        const value = Number(modifier.value || 0);
        const duration = effectDurationLabel(modifier);
        const remaining = duration ? ` · ${duration}` : '';
        return `${label}: ${modifier.stat?.endsWith('_immunity') ? 'активно' : modifier.stat?.endsWith('_multiplier') ? `×${value}` : `${value >= 0 ? '+' : ''}${value}`}${remaining}`;
    });
    const visibleEffectChips = [...new Set([
        ...directStatusChips.map((item) => {
            const value = Number(item.value);
            return `${item.label}: ${value}`;
        }),
        ...disabledZoneChips,
        ...modifierChips,
        ...(Number(health.combatMeta?.willShockBonus) ? [`Следующая проверка Воли против шока: ${Number(health.combatMeta.willShockBonus)>0?'+':''}${health.combatMeta.willShockBonus}`] : []),
        ...(health.combatMeta?.willShockAdvantage ? ['Следующая проверка Воли против шока: преимущество'] : []),
    ])];
    const painReturnEffect = (health.effects || []).find(effect => effect.type === 'pain_block'
        && effect.active !== false && Number(effect.remaining) > 0);
    if (painReturnEffect) {
        const blocked = Number(health.combatMeta?.blockedPain || 0);
        visibleEffectChips.push(`Заблокировано боли: ${blocked}; после действия вернётся: ${blocked * Number(painReturnEffect.return_fraction ?? 1)}`);
    }
    const addictionRecords = Object.values(health.addictions?.records || {})
        .filter(record => record && record.active !== false);
    const currentGameDay = Math.max(1, Number(window.lobbyGameDay || 1));
    const addictionProtectionMinutes=Math.max(0,Number(health.combatMeta?.addictionBlockedUntilMinute||0)
        -currentGameDay*1440-Number(window.lobbyGameTimeMinutes??480));
    const addictionProtectionLabel=addictionProtectionMinutes>0
        ?`Получение новых зависимостей заблокировано: ещё ${Math.floor(addictionProtectionMinutes/60)} ч ${Math.ceil(addictionProtectionMinutes%60)} мин`:'';
    if(addictionProtectionLabel)visibleEffectChips.push(addictionProtectionLabel);
    const addictionHtml = addictionRecords.map((record) => {
        const withdrawalDays = Math.max(0, Number(record.withdrawal_days || 0));
        const remaining = Math.max(0, Number(record.withdrawal_remaining ?? 28));
        const checks = record.checks || {};
        const checksToday = Number(checks.day) === currentGameDay ? Number(checks.attempts || 0) : 0;
        const successesToday = Number(checks.day) === currentGameDay ? Number(checks.successes || 0) : 0;
        const satisfied = (record.satisfied_days || []).map(Number).includes(currentGameDay);
        const progress = Number(record.daily_progress?.day) === currentGameDay
            ? record.daily_progress
            : {};
        const progressText = record.kind === 'alcohol'
            ? `Опьянение за сутки: ${Math.min(50, Number(progress.intoxication || 0))}/50`
            : (record.kind === 'nicotine'
                ? `Выведено истощения табаком: ${Math.round(Number(progress.exhaustion_relief || 0)*1000)/1000}/1`
                : `Подходящий препарат сегодня: ${Math.min(1, Number(progress.uses || 0))}/1`);
        const status = withdrawalDays > 0
            ? `Ломка, неделя ${Math.max(1, Number(record.withdrawal_stage || 1))} · осталось ${remaining} дн.`
            : (satisfied ? 'Потребность на сегодня закрыта' : 'Зависимость активна');
        const checkButton = withdrawalDays > 0 && checksToday < 5 && currentCharacterCanEdit
            ? `<button type="button" class="btn btn-sm btn-warning" onclick="checkAddictionWithdrawal('${escapeHtml(record.key)}')">Проверка Воли</button>`
            : '';
        return `
            <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 10px;border:1px solid rgba(255,255,255,.08);border-radius:8px;">
                <div><strong>${escapeHtml(record.label || record.key)}</strong><div class="text-muted" style="font-size:12px;">${escapeHtml(status)}${withdrawalDays > 0 ? ` · проверки ${checksToday}/5, успехи ${successesToday}` : ''}<br>${escapeHtml(progressText)}</div></div>
                <div style="display:flex;gap:6px;align-items:center;">${checkButton}${window.isGM ? `<button type="button" class="btn btn-sm btn-danger" onclick="removeCharacterAddiction('${escapeHtml(record.key)}')">Удалить</button>` : ''}</div>
            </div>`;
    }).join('');
    const stressEffectsHtml = stressEffects.map(({ effect, index }) => {
        const status = effect.gmPending
            ? '\u041e\u0436\u0438\u0434\u0430\u0435\u0442 \u0440\u0435\u0448\u0435\u043d\u0438\u044f \u0413\u041c'
            : (effect.gmSkipped ? '\u041f\u0440\u043e\u043f\u0443\u0449\u0435\u043d \u0413\u041c' : '\u0410\u043a\u0442\u0438\u0432\u0435\u043d');
        const requirement = effect.requirement || effect.note || '';
        const remaining = effect.remaining !== null && effect.remaining !== undefined
            ? ` \u00b7 \u043e\u0441\u0442. ${effect.remaining}`
            : '';
        return `
            <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:10px;padding:8px 10px;border:1px solid rgba(212,165,255,.18);border-radius:8px;background:rgba(212,165,255,.035);">
                <div>
                    <strong>${escapeHtml(effect.name || '\u042d\u0444\u0444\u0435\u043a\u0442 \u0441\u0442\u0440\u0435\u0441\u0441\u0430')}</strong>
                    <div class="text-muted" style="font-size:12px;line-height:1.4;">${escapeHtml(status + remaining)}${requirement ? `<br>${escapeHtml(requirement)}` : ''}</div>
                </div>
                ${currentCharacterCanEdit ? `<button type="button" class="btn btn-sm btn-danger" onclick="endStressEffect(${index})" title="\u0417\u0430\u0432\u0435\u0440\u0448\u0438\u0442\u044c \u044d\u0444\u0444\u0435\u043a\u0442">\u0417\u0430\u0432\u0435\u0440\u0448\u0438\u0442\u044c</button>` : ''}
            </div>`;
    }).join('');

    let html = `
        <div class="health-tab">
            ${window.isGM ? '<div style="display:flex;justify-content:flex-end;margin-bottom:10px;"><button type="button" class="btn btn-sm btn-warning" onclick="healCharacterFully()">Вылечить персонажа</button></div>' : ''}
            <div class="health-vitals-grid">
                <div class="health-field health-field-pool">
                    <label>Общий пул ОЗ</label>
                    <div class="health-pool-inputs">
                        <input type="number" min="0" class="form-control" name="health.current" value="${Number(health.current || 0)}">
                        <span>/</span>
                        <input type="number" min="1" class="form-control" name="health.max" value="${Number(health.max || 700)}">
                    </div>
                </div>
                <div class="health-field health-field-blood">
                    <label>Кровь</label>
                    <select class="form-control" name="health.blood">${bloodSelect}</select>
                </div>
                <div class="health-field health-field-blood-type">
                    <label>Группа крови</label>
                    <div class="health-readonly-value">${bloodTypeKnown ? formatBloodType(bloodTypeValue) : 'Неизвестна'}</div>
                </div>
                <div class="health-field">
                    <label>Стресс</label>
                    <div class="health-stress-controls">
                        ${window.isGM ? '<button type="button" class="btn btn-sm btn-secondary" onclick="adjustCharacterStress(-1)" title="Снизить стресс на 1" aria-label="Снизить стресс">−</button>' : ''}
                        <div class="health-readonly-value" data-widget-readout="health.stress" data-widget-label="Стресс" style="min-width:42px;display:flex;align-items:center;justify-content:center;" title="Стресс изменяется игровыми событиями и действиями">${Number(health.stress || 0)}</div>
                        ${window.isGM ? '<button type="button" class="btn btn-sm btn-secondary" onclick="adjustCharacterStress(1)" title="Повысить стресс на 1" aria-label="Повысить стресс">+</button>' : ''}
                    </div>
                </div>
                <div class="health-field">
                    <label>Ур. боли</label>
                    <input type="number" class="form-control" name="health.painLevel" value="${health.painLevel || 0}">
                </div>
                <div class="health-field">
                    <label>Истощение</label>
                    <input type="number" class="form-control" name="health.exhaustion" value="${health.exhaustion || 0}">
                </div>
                <div class="health-field">
                    <label>Радиация</label>
                    <input type="number" class="form-control" name="health.radiation" value="${health.radiation || 0}">
                </div>
            </div>
            <details class="health-compact-panel health-blood-check" data-sheet-panel="blood-check">
                <summary>
                    <span>Проверка кровопотери</span>
                    <strong>Сложность ${finalBleedingDc}</strong>
                </summary>
                <div class="health-compact-content">
                    <span>База 5</span>
                    <span>Тяжесть +${bleeding.totalSeverity || 0}</span>
                    <span>Стадия -${bleeding.stagePenalty || 0}</span>
                    <span>Модификаторы ${bleedingModifierTotal >= 0 ? '+' : ''}${bleedingModifierTotal}</span>
                    <span>Воля ${willBonus >= 0 ? '+' : ''}${willBonus}</span>
                    <span class="health-compact-note">${bleedingEffects.length ? bleedingEffects.map(item => `${escapeHtml(item.name || item.type)} (${item.kind === 'internal' ? 'внутр.' : 'внешн.'} ${item.stage || 'light'})`).join(', ') : 'Активных кровотечений нет'}</span>
                </div>
            </details>
            <details class="health-compact-panel health-infection-panel" data-sheet-panel="infection" ${Number(health.infection || 0) > 0 || Number(health.infectionGrowthPerDay || 0) > 0 ? 'open' : ''}>
                <summary>
                    <span>Заражение крови</span>
                    <strong>${Number(health.infection || 0)}%</strong>
                </summary>
                <div class="health-infection-fields">
                    <div class="health-field">
                        <label>Текущее заражение</label>
                        <input type="number" min="0" max="100" class="form-control" name="health.infection" value="${health.infection || 0}">
                    </div>
                    <div class="health-field">
                        <label>Нарастание в сутки</label>
                        <input type="number" min="0" class="form-control" name="health.infectionGrowthPerDay" value="${health.infectionGrowthPerDay || 0}">
                    </div>
                </div>
            </details>
            <div style="margin: 0 0 14px 0; padding: 10px 12px; border-radius: 12px; background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.06);">
                <div data-widget-block="health-summary" style="font-size: 12px; opacity: 0.78; margin-bottom: 8px;">Показатели и модификаторы</div>
                <div style="display:flex; flex-wrap:wrap; gap:8px;">
                    ${visibleEffectChips.length ? visibleEffectChips.map((text) => `<span style="display:inline-flex; align-items:center; gap:6px; padding:5px 10px; border-radius:999px; background:rgba(255,255,255,0.07); border:1px solid rgba(255,255,255,0.08); font-size:12px;">${escapeHtml(text)}</span>`).join('') : '<span style="opacity:0.7; font-size:12px;">Числовых изменений нет; состояния показаны ниже</span>'}
                </div>
            </div>
            <details class="health-compact-panel" data-sheet-panel="addictions" ${addictionRecords.length || addictionProtectionLabel ? 'open' : ''}>
                <summary><span>Зависимости</span><strong>${addictionRecords.length}</strong></summary>
                <div style="display:grid;gap:7px;padding-top:8px;">
                    ${addictionProtectionLabel?`<span class="text-muted" style="font-size:12px;">${escapeHtml(addictionProtectionLabel)}</span>`:''}
                    ${addictionHtml || '<span class="text-muted" style="font-size:12px;">Активных зависимостей нет</span>'}
                </div>
            </details>
            ${stressEffects.length ? `
                <details class="health-compact-panel" data-sheet-panel="stress-effects" open>
                    <summary><span>\u042d\u0444\u0444\u0435\u043a\u0442\u044b \u0441\u0442\u0440\u0435\u0441\u0441\u0430</span><strong>${stressEffects.length}</strong></summary>
                    <div style="display:grid;gap:7px;padding-top:8px;">${stressEffectsHtml}</div>
                </details>` : ''}
            <div class="health-needs-panel">
                <div class="health-need-field"><label>Еда сегодня</label><input type="number" min="0" max="3" class="form-control" name="health.needs.mealsToday" value="${needs.mealsToday}"><small>${needs.mealsToday}/3</small></div>
                <div class="health-need-field"><label>Вода сегодня</label><input type="number" min="0" max="3" class="form-control" name="health.needs.drinksToday" value="${needs.drinksToday}"><small>${needs.drinksToday}/3</small></div>
            </div>
            <hr>
            <h4>Зоны тела</h4>
            <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; margin-bottom: 15px;">
                <div></div>
                <div class="zone-item-vertical">
                    <div class="zone-label">Голова</div>
                    <div class="zone-fields">
                        <input type="number" class="number-input" name="health.zones.head.current" value="${(zones.head || {}).current || 0}" placeholder="Тек">
                        <span class="slash">/</span>
                        <input type="number" class="number-input" name="health.zones.head.max" value="${(zones.head || {}).max || 100}" placeholder="Макс">
                    </div>
                </div>
                <div></div>

                <div class="zone-item-vertical">
                    <div class="zone-label">Правая рука</div>
                    <div class="zone-fields">
                        <input type="number" class="number-input" name="health.zones.rightArm.current" value="${(zones.rightArm || {}).current || 0}">
                        <span class="slash">/</span>
                        <input type="number" class="number-input" name="health.zones.rightArm.max" value="${(zones.rightArm || {}).max || 100}">
                    </div>
                </div>
                <div class="zone-item-vertical">
                    <div class="zone-label">Грудь</div>
                    <div class="zone-fields">
                        <input type="number" class="number-input" name="health.zones.chest.current" value="${(zones.chest || {}).current || 0}">
                        <span class="slash">/</span>
                        <input type="number" class="number-input" name="health.zones.chest.max" value="${(zones.chest || {}).max || 100}">
                    </div>
                </div>
                <div class="zone-item-vertical">
                    <div class="zone-label">Левая рука</div>
                    <div class="zone-fields">
                        <input type="number" class="number-input" name="health.zones.leftArm.current" value="${(zones.leftArm || {}).current || 0}">
                        <span class="slash">/</span>
                        <input type="number" class="number-input" name="health.zones.leftArm.max" value="${(zones.leftArm || {}).max || 100}">
                    </div>
                </div>

                <div class="zone-item-vertical">
                    <div class="zone-label">Правая нога</div>
                    <div class="zone-fields">
                        <input type="number" class="number-input" name="health.zones.rightLeg.current" value="${(zones.rightLeg || {}).current || 0}">
                        <span class="slash">/</span>
                        <input type="number" class="number-input" name="health.zones.rightLeg.max" value="${(zones.rightLeg || {}).max || 100}">
                    </div>
                </div>
                <div class="zone-item-vertical">
                    <div class="zone-label">Живот</div>
                    <div class="zone-fields">
                        <input type="number" class="number-input" name="health.zones.abdomen.current" value="${(zones.abdomen || {}).current || 0}">
                        <span class="slash">/</span>
                        <input type="number" class="number-input" name="health.zones.abdomen.max" value="${(zones.abdomen || {}).max || 100}">
                    </div>
                </div>
                <div class="zone-item-vertical">
                    <div class="zone-label">Левая нога</div>
                    <div class="zone-fields">
                        <input type="number" class="number-input" name="health.zones.leftLeg.current" value="${(zones.leftLeg || {}).current || 0}">
                        <span class="slash">/</span>
                        <input type="number" class="number-input" name="health.zones.leftLeg.max" value="${(zones.leftLeg || {}).max || 100}">
                    </div>
                </div>
            </div>
            <details class="health-compact-panel" data-sheet-panel="organs">
                <summary><span>Органы</span></summary>
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:8px;padding-top:8px;">
                    ${[
                        ['heart', 'Сердце'], ['rightLung', 'Правое лёгкое'], ['leftLung', 'Левое лёгкое'],
                        ['rightKidney', 'Правая почка'], ['leftKidney', 'Левая почка'], ['stomach', 'Желудок'],
                        ['liver', 'Печень'], ['brain', 'Мозг'], ['skull', 'Череп'], ['spine', 'Позвоночник'],
                        ['rightEye', 'Правый глаз'], ['leftEye', 'Левый глаз'], ['nose', 'Нос'],
                        ['jaw', 'Челюсть'], ['rightEar', 'Правое ухо'], ['leftEar', 'Левое ухо'],
                    ].map(([key, label]) => `
                        <div class="zone-item-vertical">
                            <div class="zone-label">${label}</div>
                            <div class="zone-fields">
                                <input type="number" class="number-input" name="health.organs.${key}.current" value="${Number((organs[key] || {}).current ?? BASE_ORGAN_MAXIMUMS[key])}">
                                <span class="slash">/</span>
                                <input type="number" class="number-input" name="health.organs.${key}.max" value="${Number((organs[key] || {}).max ?? BASE_ORGAN_MAXIMUMS[key])}" readonly>
                            </div>
                        </div>`).join('')}
                </div>
            </details>
    `;

    const effects = normalizeEffectList(Array.isArray(health.effects) ? health.effects : []);
    const effectTypeOptions = getEffectTypeOptions();
    const effectSourceLabels = {
        combat_damage: 'получение урона',
        disabled_body_zone: 'ОЗ части тела опустились до 0',
        disabled_organ: 'ОЗ органа опустились до 0',
        catastrophic_limb_damage: 'критическое повреждение конечности',
        zero_total_health: 'общий пул ОЗ опустился до 0',
        zero_brain_health: 'ОЗ мозга опустились до 0',
        zero_skull_health: 'ОЗ черепа опустились до 0',
        hit_disabled_vital_zone: 'повторное попадание в выбитую жизненно важную зону',
        maximum_pain: 'уровень боли достиг 10',
        fracture: 'перелом',
        direct: 'служебное описание действия предмета',
    };
    const effectRuleDescriptions = {
        shock: 'Блокирует все действия, кроме попытки очнуться один раз за раунд.',
        unconsciousness: 'Персонаж без сознания и не может действовать.',
        critical_condition: 'Персонаж в критическом состоянии и не может действовать.',
        death: 'Персонаж погиб и не может действовать.',
        fracture: 'Даёт штрафы перелома до фиксации или лечения.',
        fracture_fixed: 'Перелом зафиксирован: остаётся до завершения лечения, но штрафы снижены.',
        fracture_unfixed: 'Перелом не был зафиксирован вовремя и оставил последствия.',
        fracture_sequela: 'Постоянное последствие незафиксированного перелома.',
        mangled_limb: 'Конечность нельзя использовать; восстановление требует хирургического вмешательства.',
        amputation: 'Конечность утрачена и даёт соответствующие штрафы.',
        organ_loss: 'Орган повреждён или утрачен; для восстановления действует ограниченное окно лечения.',
        organ_failure: 'Смертельное повреждение органа: без лечения наступит смерть.',
        temporary_limb_restoration: 'Временно возвращает работоспособность части тела до окончания срока.',
        delayed_limb_treatment: 'Лечение части тела будет применено после истечения указанного срока.',
        bleeding_prevention: 'Не позволяет появляться новым кровотечениям, пока действует.',
        blood_loss_freeze: 'Отключает проверки кровопотери на время действия.',
        infection_growth_block: 'Останавливает ежедневное нарастание заражения крови на время действия.',
        pain_block: 'Не даёт получать новые уровни боли; последствия применятся после окончания эффекта.',
        analgesia: 'Обезболивающее действует до окончания срока.',
        radiation_filter: 'Снижает входящую радиацию на время действия.',
        temperature_control: 'Временно меняет или удерживает температуру тела.',
        limb_trauma_suppression: 'Временно подавляет штрафы травм конечностей.',
        sleep_support: 'Поддержка от стимулятора: можно обходиться без сна в указанный срок.',
        stress_resistance: 'Снижает воздействие стресса в соответствии с препаратом.',
        next_rest_healing: 'Усилит лечение во время следующего отдыха и затем исчезнет.',
        untreated_wound: 'Рана остановлена, но не обработана: после боя может привести к заражению.',
        tourniquet: 'Жгут останавливает кровотечения конечности, но накладывает её штрафы.',
        stress_effect: 'Проявление стресса. Выполните указанное требование по решению ГМа.',
        stress_stupor: 'Ступор блокирует действия на указанный срок или до предусмотренного правилами прекращения.',
        phobia: 'Постоянная фобия даёт помеху в ситуациях, связанных с её источником.',
    };
    const describeEffect = (effect) => {
        const templateId = String(effect.source || '').match(/^consumable-template:(\d+)/)?.[1];
        const sourceItem = templateId ? (allTemplatesCache || []).find(item => Number(item.id) === Number(templateId))?.name : null;
        const sourceName = String(effect.source || '').replace(/^consumable:/, '');
        const source = effectSourceLabels[effect.source] || sourceItem || (/^consumable-template:/.test(sourceName) ? 'применение предмета' : /[А-Яа-яЁё]/.test(sourceName) ? sourceName : '');
        const details = [];
        if (effect.requirement) details.push(`Требование: ${effect.requirement}`);
        if (INSTANT_HEALTH_EFFECTS.has(effect.type)) {
            details.push('Изменит показатель один раз после нажатия «Применить разово».');
        } else if (effect.type === 'generic') {
            details.push(effect.note || (effect.onExpire || effect.on_expire || effect.adjustments ? 'Изменения применяются в указанный срок.' : 'Дополнительное состояние; последствия указаны в описании ведущего.'));
        } else if (effect.type === 'custom') {
            details.push(effect.note || 'Последствия определяются описанием и решением ведущего.');
        } else if (effect.type === 'organ_loss') {
            details.push(
                effect.treatment_window_expired || Number(effect.treatment_window_seconds || 0) <= 0
                    ? 'Окно восстановления органа истекло.'
                    : `Возможно вылечить ещё: ${formatTreatmentWindow(effect.treatment_window_seconds)}.`,
            );
        } else if (effect.note) {
            details.push(effect.note);
        } else if (effectRuleDescriptions[effect.type]) {
            details.push(effectRuleDescriptions[effect.type]);
        } else if (effect.tick && effect.tick !== 'manual') {
            details.push(`Срабатывает: ${({turn_end:'в конце хода',round_end:'в конце раунда',day_start:'в начале суток',time_elapsed:'по игровому времени',hour_start:'в начале часа',movement_end:'после перехода'})[effect.tick] || 'по условию эффекта'}.`);
        } else {
            details.push('Правила применяются системой автоматически, если это предусмотрено типом эффекта.');
        }
        details.push(...effectConsequences(effect).map(text => `${text}.`));
        if (effect.type.startsWith('bleeding')) {
            const info = getBleedingInfo(effect);
            if (info) details.push('Учитывается при проверке кровопотери.');
        }
        if (effect.type === 'radiation_filter' && effect.remaining_capacity != null) {
            details.push(`Остаток фильтра: ${effect.remaining_capacity}${effect.capacity != null ? ` из ${effect.capacity}` : ''}.`);
        }
        if (source && effect.type !== 'organ_loss') details.push(`Источник: ${source}.`);
        return details.join(' ');
    };
    let effectsHtml = '';
    effects.forEach((effect, index) => {
        if (isStressManifestationEffect(effect)) return;
        const value = String(effect.type || '').startsWith('bleeding') ? getEffectImpact(effect).severity : effect.value || 0;
        const selectedType = effect.type || 'generic';
        const isBleedingEffect = String(effect.type || '').startsWith('bleeding');
        const hasRemaining = !isBleedingEffect && selectedType !== 'organ_loss' && !INSTANT_HEALTH_EFFECTS.has(selectedType);
        const remaining = effect.remaining ?? '';
        const isManagedEffect = ['addiction_withdrawal', 'withdrawal_support', 'withdrawal_support_pending'].includes(selectedType) || effect.source === 'deadly_intoxication';
        const managedDisabled = isManagedEffect ? 'disabled' : '';
        const fractureStatus = getFractureStatusText(effect, effects);
        const isCustomOrUnknown = ['custom', 'generic'].includes(selectedType);
        const visibleName = getEffectDisplayName(effect);
        if (selectedType === 'organ_loss') {
            const treatmentExpired = effect.treatment_window_expired
                || Number(effect.treatment_window_seconds || 0) <= 0;
            effectsHtml += `
                <div data-widget-effect-index="${index}" style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:7px;padding:10px 12px;border:1px solid rgba(180,92,70,.4);border-radius:8px;background:rgba(115,48,38,.13);">
                    <div style="min-width:0;">
                        <strong style="display:block;color:#e3c0ae;">${escapeHtml(visibleName)}</strong>
                        <span class="text-muted" style="display:block;margin-top:3px;font-size:12px;">${treatmentExpired
                            ? 'Окно восстановления органа истекло'
                            : `Возможно вылечить ещё: ${escapeHtml(formatTreatmentWindow(effect.treatment_window_seconds))}`}</span>
                    </div>
                    <button type="button" class="btn btn-sm btn-danger" onclick="removeEffect(${index})" title="Удалить состояние">×</button>
                </div>`;
            return;
        }
        effectsHtml += `
            <div data-widget-effect-index="${index}" class="health-effect-card${effect.active === false || effect.remaining != null && Number(effect.remaining) <= 0 ? ' is-inactive' : ''}">
                <div class="health-effect-caption">${INSTANT_HEALTH_EFFECTS.has(selectedType) ? 'Разовое изменение: ' : ''}${escapeHtml(effectSummary({...effect, name:visibleName, ...(INSTANT_HEALTH_EFFECTS.has(selectedType) ? {remaining:null,duration:null} : {})}))}${effect.area ? ` · ${escapeHtml(getEffectAreaLabel(effect.area))}` : ''}</div>
                <div class="text-muted health-effect-description">${escapeHtml(describeEffect(effect))}</div>
                ${INSTANT_HEALTH_EFFECTS.has(selectedType) && currentCharacterCanEdit ? `<button type="button" data-sheet-control class="btn btn-sm" onclick="applyManualNumericEffect(${index},this)">Применить разово</button>` : ''}
                <details class="health-effect-editor" data-sheet-panel="effect-editor-${effect.id || index}" ${isCustomOrUnknown && effect.name === 'Пользовательский эффект' ? 'open' : ''}><summary>Изменить эффект</summary><div class="health-effect-fields">
                <label>Тип<select class="form-control" name="health.effects.${index}.type" style="width:100%;" ${managedDisabled}>
                    ${effectTypeOptions.map(opt => `<option value="${opt.value}" ${opt.value === selectedType ? 'selected' : ''}>${escapeHtml(opt.label)}</option>`).join('')}
                </select>
                </label><label>Область<select class="form-control" name="health.effects.${index}.area" style="width:100%;" ${managedDisabled}>
                    ${[
                        ['', 'Всё тело'], ['head', 'Голова'], ['chest', 'Грудь'], ['abdomen', 'Живот'],
                        ['leftArm', 'Левая рука'], ['rightArm', 'Правая рука'],
                        ['leftLeg', 'Левая нога'], ['rightLeg', 'Правая нога'],
                        ['nose', 'Нос'], ['jaw', 'Челюсть'],
                        ['leftEar', 'Левое ухо'], ['rightEar', 'Правое ухо'],
                        ['leftEye', 'Левый глаз'], ['rightEye', 'Правый глаз'],
                        ['heart', 'Сердце'], ['rightLung', 'Правое лёгкое'], ['leftLung', 'Левое лёгкое'],
                        ['rightKidney', 'Правая почка'], ['leftKidney', 'Левая почка'],
                        ['stomach', 'Желудок'], ['liver', 'Печень'], ['brain', 'Мозг'], ['skull', 'Череп'],
                        ['spine', 'Позвоночник'], ['internalOrgan', 'Внутренний орган']
                    ].map(([key, label]) => `<option value="${key}" ${key === (effect.area || '') ? 'selected' : ''}>${label}</option>`).join('')}
                </select>
                </label><label>Значение<input type="number" class="form-control number-input" name="health.effects.${index}.value" value="${value}" step="any" ${managedDisabled}></label>
                ${hasRemaining ? `<label>Осталось${effect.time_unit ? ` (${({hour:'ч',minute:'мин',second:'с'})[effect.time_unit] || 'ед.'})` : effect.tick === 'round_end' ? ' (раундов)' : effect.tick === 'turn_end' ? ' (ходов)' : ''}<input type="text" class="form-control number-input" name="health.effects.${index}.remaining" value="${escapeHtml(remaining)}" placeholder="Бессрочно" data-nullable-number="true" ${managedDisabled}></label>` : '<div></div>'}
                ${INSTANT_HEALTH_EFFECTS.has(selectedType) ? '' : `<label>Срок уменьшается<select class="form-control" name="health.effects.${index}.tick" ${managedDisabled}>
                    ${[['manual','Только вручную'],['turn_end','В конце хода'],['round_end','В конце раунда'],['time_elapsed','По времени, минуты'],['hour_start','По времени, часы'],['day_start','В начале суток'],['movement_end','После перехода']].map(([key,text])=>`<option value="${key}" ${effect.tick === key ? 'selected' : ''}>${text}</option>`).join('')}
                </select></label>`}
                ${selectedType === 'fracture_unfixed' ? `<button type="button" class="btn btn-sm btn-warning" onclick="rebreakUnfixedFracture(${index})" title="Сломать повторно и лечить как обычный перелом">↻</button>` : ''}
                ${isManagedEffect ? '<span title="Системный эффект">⌁</span>' : `<button type="button" class="btn btn-sm btn-danger" onclick="removeEffect(${index})">×</button>`}
                ${isCustomOrUnknown ? `<input class="form-control" name="health.effects.${index}.name" value="${escapeHtml(visibleName)}" placeholder="Название эффекта" style="grid-column:1/-1;">` : ''}
                ${isCustomOrUnknown ? `<label style="grid-column:1/-1;">Описание<textarea class="form-control" name="health.effects.${index}.note" rows="2" placeholder="Что меняет этот эффект">${escapeHtml(effect.note || '')}</textarea></label>` : ''}
                </div></details>
                ${fractureStatus ? `<div class="text-muted" style="grid-column:1/-1;font-size:12px;">${escapeHtml(fractureStatus)}</div>` : ''}
            </div>
        `;
    });

    html += `
        <section data-widget-block="effects"><h4>Эффекты</h4>
        <div id="effects-container">
            ${effectsHtml}
        </div>
        <div style="display:flex; flex-wrap:wrap; gap:8px; margin-top:8px;">
            <button type="button" class="btn btn-sm" onclick="addEffect()">+ Добавить эффект</button>
            <button type="button" class="btn btn-sm btn-warning" onclick="applyQuickEffect('pain', 1, 2, 'Боль')">Боль</button>
            <button type="button" class="btn btn-sm btn-warning" onclick="applyQuickEffect('exhaustion', 1, 2, 'Истощение')">Истощение</button>
        </div></section>
        </div>
    `;

    targetContainer.innerHTML = html;
    if(backgroundIs(data,'Крепкий орешек')||health.combatMeta?.toughNut){
        const state=health.combatMeta?.toughNut||{uses:1,minimumPain:0};
        targetContainer.insertAdjacentHTML('afterbegin',`<section class="health-section"><h4>Крепкий орешек</h4><label>Оставшиеся спасения <input type="number" min="0" max="100" class="form-control" name="health.combatMeta.toughNut.uses" value="${Number(state.uses)||0}" ${window.isGM?'':'disabled'}></label><p>Постоянный минимум боли: ${Number(state.minimumPain)||0}. Запас пополняет ГМ; минимум боли от пополнения не сбрасывается.</p></section>`);
    }
}

function refreshHealthPanel(data = currentCharacterData) {
    const healthContainer = document.getElementById('health-right-column')
        || document.getElementById('sheet-tab-health');
    if (!healthContainer) return;
    const viewState = captureSheetViewState();
    renderHealthTab(data, healthContainer);
    restoreSheetViewState(viewState);
}

window.adjustCharacterStress = async function(amount) {
    if (
        stressAdjustmentPending
        || !window.isGM
        || !currentLobbyId
        || !currentCharacterId
        || ![-1, 1].includes(Number(amount))
    ) return;
    stressAdjustmentPending = true;
    try {
        const result = await Server.adjustCharacterStress(currentLobbyId, currentCharacterId, Number(amount));
        if (result?.data) {
            currentCharacterData = result.data;
            normalizeCharacterEffects(currentCharacterData);
            refreshHealthPanel();
        }
    } catch (error) {
        showNotification(error.message || 'Не удалось изменить стресс', 'system');
    } finally {
        stressAdjustmentPending = false;
    }
};

window.healCharacterFully = async function() {
    if (!window.isGM || !currentLobbyId || !currentCharacterId) return;
    if (!window.confirm('Восстановить базовые ОЗ тела, конечностей и органов и удалить все эффекты и кровотечения?')) return;
    try {
        await Server.waitForCharacterSave(currentCharacterId);
        const result = await Server.healCharacterFully(currentLobbyId, currentCharacterId);
        if (result?.data) {
            currentCharacterData = result.data;
            normalizeCharacterEffects(currentCharacterData);
            refreshHealthPanel();
        }
        showNotification('Персонаж вылечен', 'success');
    } catch (error) {
        showNotification(error.message || 'Не удалось вылечить персонажа', 'system');
    }
};

window.checkAddictionWithdrawal = async function(addictionKey) {
    if (!currentCharacterCanEdit || !currentCharacterId) return;
    try {
        updateDataFromFields();
        await Server.updateCharacter(currentCharacterId, { data: currentCharacterData });
        const response = await Server.checkCharacterWithdrawal(currentCharacterId, addictionKey);
        if (response?.data) {
            currentCharacterData = response.data;
            normalizeCharacterEffects(currentCharacterData);
            refreshHealthPanel();
        }
        const result = response?.result;
        if (result) {
            showNotification(
                `Ломка: d20 ${result.roll} против СЛ ${result.difficulty} — ${result.success ? 'успех' : 'провал'}${result.days_reduced ? `, срок сокращён на ${result.days_reduced} дн.` : ''}`,
                result.success ? 'success' : 'system',
            );
        }
    } catch (error) {
        showNotification(error.message || 'Не удалось выполнить проверку ломки');
    }
};

window.removeCharacterAddiction = async function(addictionKey) {
    if (!window.isGM || !currentCharacterId) return;
    if (!window.confirm('Удалить зависимость и связанную с ней ломку?')) return;
    try {
        updateDataFromFields();
        await Server.updateCharacter(currentCharacterId, { data: currentCharacterData });
        const response = await Server.removeCharacterAddiction(currentLobbyId, currentCharacterId, addictionKey);
        currentCharacterData = response.data;
        normalizeCharacterEffects(currentCharacterData);
        refreshHealthPanel();
    } catch (error) {
        showNotification(error.message || 'Не удалось удалить зависимость');
    }
};

window.addEffect = function() {
    updateDataFromFields();
    if (!currentCharacterData.health) currentCharacterData.health = {};
    if (!Array.isArray(currentCharacterData.health.effects)) {
        currentCharacterData.health.effects = [];
    }
    currentCharacterData.health.effects.push(createEffectDraft('custom'));
    refreshHealthPanel();
    scheduleAutoSave();
};

window.applyQuickEffect = function(type, value = 1, duration = 0, label = '') {
    updateDataFromFields();
    if (!currentCharacterData.health) currentCharacterData.health = {};
    if (!Array.isArray(currentCharacterData.health.effects)) {
        currentCharacterData.health.effects = [];
    }

    const lasting = duration > 0 && !INSTANT_HEALTH_EFFECTS.has(type);
    const effect = createEffectDraft(type, {
        name: label || type,
        value,
        duration: lasting ? duration : null,
        remaining: lasting ? duration : null,
        tick: lasting ? 'turn_end' : 'manual',
        active: true,
    });

    if (type === 'heal' || type === 'radiation' || type === 'pain' || type === 'exhaustion' || type === 'stress' || type === 'intoxication' || type === 'infection') {
        applyEffectToHealth(currentCharacterData.health, effect);
    } else {
        currentCharacterData.health.effects.push(effect);
    }

    refreshHealthPanel();

    normalizeCharacterEffects(currentCharacterData);
    scheduleAutoSave();
    const summary = effectSummary(effect);
    if (summary) showNotification(`Применено: ${summary}`, 'system');
};

window.applyManualNumericEffect = async function(index, button) {
    if (!currentCharacterCanEdit || sheetSaveBlocked || !button?.isConnected || button.disabled) return;
    button.disabled = true;
    updateDataFromFields();
    const effect = currentCharacterData?.health?.effects?.[index];
    if (!effect || !INSTANT_HEALTH_EFFECTS.has(effect.type)) return;
    currentCharacterData.health.effects.splice(index, 1);
    applyEffectToHealth(currentCharacterData.health, effect);
    normalizeCharacterEffects(currentCharacterData);
    refreshHealthPanel();
    try { await forceSyncCharacter(); }
    catch (error) { showNotification(error.message || 'Не удалось сохранить изменение', 'system'); }
};

window.removeEffect = function(index) {
    updateDataFromFields();
    if (!currentCharacterData.health?.effects) return;
    currentCharacterData.health.effects.splice(index, 1);
    refreshHealthPanel();
    scheduleAutoSave();
};

window.endStressEffect = function(index) {
    if (!currentCharacterCanEdit || !currentCharacterData?.health) return;
    updateDataFromFields();

    const effects = normalizeEffectList(currentCharacterData.health.effects || []);
    const effect = effects[Number(index)];
    if (!effect || !isStressManifestationEffect(effect) || effect.active === false) {
        showNotification('\u042d\u0444\u0444\u0435\u043a\u0442 \u0441\u0442\u0440\u0435\u0441\u0441\u0430 \u0443\u0436\u0435 \u0437\u0430\u0432\u0435\u0440\u0448\u0451\u043d', 'system');
        refreshHealthPanel();
        return;
    }

    effect.active = false;
    effect.gmPending = false;
    effect.endedManually = true;
    effect.endedAt = new Date().toISOString();
    currentCharacterData.health.effects = effects;
    syncHealthDerivedStatuses(currentCharacterData.health);
    refreshHealthPanel();
    forceSyncCharacter();
    showNotification('\u042d\u0444\u0444\u0435\u043a\u0442 \u0441\u0442\u0440\u0435\u0441\u0441\u0430 \u0437\u0430\u0432\u0435\u0440\u0448\u0451\u043d', 'success');
};

// ========== 4. ВКЛАДКА "НАВЫКИ" ==========
async function renderSkillsTab(data) {
    const container = document.getElementById('sheet-tab-skills');
    const skills = data.skills ||= {};
    const physical = skills.physical ||= {};
    const social = skills.social ||= {};
    const other = skills.other ||= {};
    const specialized = skills.specialized || {};

    const physicalSkills = [
        { key: 'strength', label: 'Сила' },
        { key: 'agility', label: 'Ловкость' },
        { key: 'will', label: 'Воля' },
        { key: 'awareness', label: 'Внимательность' },
        { key: 'melee', label: 'Ближний бой' },
        { key: 'shooting', label: 'Стрельба' }
    ];
    const socialSkills = [
        { key: 'charisma', label: 'Харизма' },
        { key: 'barter', label: 'Бартер' },
        { key: 'persuasion', label: 'Убеждение' },
        { key: 'deception', label: 'Обман' },
        { key: 'intimidation', label: 'Устрашение' }
    ];
    const otherSkills = [
        { key: 'medicine', label: 'Медицина' },
        { key: 'engineering', label: 'Инженерия' },
        { key: 'stealth', label: 'Скрытность' },
        { key: 'tactics', label: 'Тактика' },
        { key: 'survival', label: 'Выживание' }
    ];

    function renderSkillRow(label, base, bonus, xp, path) {
        const required = getRequiredXp(base);
        const transientBonus = (path==='physical.strength'&&getExoskeletonPowerProfile(data).powered?8:0)
            +(backgroundIs(data,'Пожарный')&&data.health?.combatMeta?.firefighterNearby&&['physical.strength','physical.agility'].includes(path)?2:0);
        return `
            <div style="display: flex; align-items: center; gap: 3px; margin-bottom: 5px; flex-wrap: wrap;">
                <span style="width: 125px; word-break: break-word; line-height: 1.3;" onclick="window.rollSkill('${path}', '${label}')" title="${label}">${label}</span>
                <input type="number" class="form-control number-input" name="skills.${path}.base" value="${base}" style="width: 55px;">
                <span>+</span>
                <input type="number" class="form-control number-input" name="skills.${path}.bonus" value="${Number(bonus || 0) + transientBonus}" data-transient-bonus="${transientBonus}" style="width: 55px;">
                <span data-skill-progress="${path}" style="font-size: 0.7rem;">Опыт: ${xp}/${required}</span>
                <button type="button" class="btn btn-sm btn-secondary" onclick="addSkillXpFromPoints('${path}')" style="padding: 2px 4px; font-size: 0.7rem;" title="Взять 1 свободное очко навыка">➕</button>
                <button type="button" class="btn btn-sm btn-secondary" onclick="addSkillXpFromUse('${path}')" style="padding: 2px 4px; font-size: 0.7rem;" title="Добавить опыт за использование">💡</button>
                <span style="cursor: pointer; font-size: 1.1em;" onclick="window.rollSkill('${path}', '${label}')">🎲</span>
            </div>
        `;
    }

    let html = `
        <div style="display: grid; grid-template-columns: repeat(4, minmax(320px, 1fr)); gap: 20px; margin-bottom: 20px;">
            <div>
                <h4>Физические</h4>
                ${physicalSkills.map(s => {
                    const skillObj = physical[s.key] ||= { base: 5, bonus: 0, xp: 0 };
                    return renderSkillRow(s.label, skillObj.base, skillObj.bonus, skillObj.xp || 0, `physical.${s.key}`);
                }).join('')}
            </div>
            <div>
                <h4>Социальные</h4>
                ${socialSkills.map(s => {
                    const skillObj = social[s.key] ||= { base: 5, bonus: 0, xp: 0 };
                    return renderSkillRow(s.label, skillObj.base, skillObj.bonus, skillObj.xp || 0, `social.${s.key}`);
                }).join('')}
            </div>
            <div>
                <h4>Прочие</h4>
                ${otherSkills.map(s => {
                    const skillObj = other[s.key] ||= { base: 5, bonus: 0, xp: 0 };
                    return renderSkillRow(s.label, skillObj.base, skillObj.bonus, skillObj.xp || 0, `other.${s.key}`);
                }).join('')}
            </div>
            <div>
                <h4>Владение оружием</h4>
                ${(() => {
                    const levelOptions = [
                        { value: 'unfamiliar', label: 'Не знаком' },
                        { value: 'familiar', label: 'Знаком' },
                        { value: 'professional', label: 'Профессионал' }
                    ];
                    const specLabels = {
                        pistols: 'Пистолеты',
                        shotguns: 'Дробовики',
                        smgs: 'ПП',
                        assaultRifles: 'Штурмовые',
                        sniperRifles: 'Снайперские',
                        grenadeLaunchers: 'Гранатометы',
                        machineGuns: 'Пулеметы'
                    };

                    function getRequiredXpForWeapon(level) {
                        if (level === 'unfamiliar') return 5;
                        if (level === 'familiar') return 25;
                        return 0; // professional
                    }

                    let specHtml = '';
                    for (const [key, label] of Object.entries(specLabels)) {
                        const current = specialized[key] || { level: 'unfamiliar', xp: 0 };
                        const level = current.level;
                        const xp = current.xp || 0;
                        const required = getRequiredXpForWeapon(level);
                        const progressDisplay = required > 0 ? `${xp}/${required}` : 'максимум';
                        const canAdd = level !== 'professional';
                        specHtml += `
                            <div style="display: flex; align-items: center; gap: 5px; margin-bottom: 5px; flex-wrap: wrap;">
                                <span style="width: 115px;">${label}</span>
                                <select name="skills.specialized.${key}.level" class="form-control" style="width: 100px;" onchange="setWeaponLevel('${key}', this.value)">
                                    ${levelOptions.map(opt => `<option value="${opt.value}" ${level === opt.value ? 'selected' : ''}>${opt.label}</option>`).join('')}
                                </select>
                                <span style="font-size: 0.7rem;">Прогресс: ${progressDisplay}</span>
                                ${canAdd ? `<button type="button" class="btn btn-sm btn-secondary" onclick="addWeaponXp('${key}')" style="padding: 2px 4px; font-size: 0.7rem;" title="Добавить прогресс за сражение">➕</button>` : ''}
                            </div>
                        `;
                    }
                    return specHtml;
                })()}
            </div>
        </div>
        <hr>
        <div style="display: flex; gap: 15px;">
            <div><label>Очки навыков</label><input type="number" class="form-control number-input" name="skills.skillPoints" value="${skills.skillPoints ?? 30}"></div>
            <div><label>Специализации</label><input type="number" class="form-control number-input" name="skills.specializations" value="${skills.specializations ?? 10}"></div>
        </div>
        ${window.isGM?'<button type="button" class="btn btn-secondary" onclick="openSessionReward()">Награда за сессию или цель</button>':''}
        <h4>Особые черты</h4>
        <div id="special-traits-container"></div>
        <button type="button" class="btn btn-sm" onclick="addSpecialTrait()" ${data.creation?.completed&&!window.isGM?'disabled':''}>+ Добавить</button>
        ${window.isGM ? `<button type="button" class="btn btn-sm btn-secondary" onclick="openCreateSpecialTraitTemplateModal()">➕ Создать кастом</button>` : ''}
    `;

    container.innerHTML = html;
    applySheetEditPermissions(container);
    const traitsRoot = document.getElementById('special-traits-container');
    // Основные навыки готовы сразу; ответ шаблонов не перерисовывает новые строки.
    let specialTraitTemplates = [];
    try {
        specialTraitTemplates = await loadTemplatesForLobby('special_trait');
    } catch (e) {
        console.error('Failed to load special trait templates', e);
    }
    if (document.getElementById('special-traits-container') !== traitsRoot) return;
    renderSpecialTraits(data.features?.specialTraits || [], specialTraitTemplates);
    if(data.creation?.completed&&!window.isGM)traitsRoot.querySelectorAll('input,select,textarea,button').forEach(el=>el.disabled=true);
    applySheetEditPermissions(container);
}

function renderSpecialTraits(traits, templates) {
    const container = document.getElementById('special-traits-container');
    if (!container) return;
    container.innerHTML = '';

    traits.forEach((trait, index) => {
        const selectedTemplateId = trait.templateId ? parseInt(trait.templateId, 10) : null;
        const optionsHtml = templates.map(t =>
            `<option value="${t.id}" ${selectedTemplateId === t.id || trait.ruleId && trait.ruleId===t.attributes?.ruleId ? 'selected' : ''}>${escapeHtml(t.name)}</option>`
        ).join('');

        const div = document.createElement('div');
        div.className = 'trait-item';
        div.innerHTML = `
            <div style="display: flex; gap: 5px; flex-wrap: wrap; align-items: center; margin-bottom: 5px;">
                <select name="features.specialTraits.${index}.templateId" class="form-control" style="min-width:150px; flex: 1;" onchange="fillTraitFromTemplate(this, ${index})">
                    <option value="">-- Выберите особую черту --</option>
                    ${optionsHtml}
                </select>
                <textarea class="form-control" name="features.specialTraits.${index}.effect" placeholder="Эффект" rows="3" style="flex: 2;">${escapeHtml(ruleText(trait.effect))}</textarea>
                <input type="number" class="form-control number-input" name="features.specialTraits.${index}.cost" value="${trait.cost || 0}" placeholder="Стоимость" style="width: 70px;">
                <button type="button" class="btn btn-sm btn-danger" onclick="removeSpecialTrait(${index})">✕</button>
            </div>
        `;
        if(trait.choices&&Object.keys(trait.choices).length){const note=document.createElement('p');note.textContent=Object.values(trait.choices).join(' · ');div.appendChild(note);}
        container.appendChild(div);
    });

    window.fillTraitFromTemplate = async function(select, index) {
        const selectedId = parseInt(select.value, 10);
        if (isNaN(selectedId)) {
            // Пустой выбор — очищаем поля
            const effectInput = document.querySelector(`[name="features.specialTraits.${index}.effect"]`);
            const costInput = document.querySelector(`[name="features.specialTraits.${index}.cost"]`);
            if (effectInput) effectInput.value = '';
            if (costInput) costInput.value = 0;
            if (currentCharacterData.features?.specialTraits?.[index]) {
                delete currentCharacterData.features.specialTraits[index].templateId;
                delete currentCharacterData.features.specialTraits[index].ruleId;
                delete currentCharacterData.features.specialTraits[index].choices;
                delete currentCharacterData.features.specialTraits[index].name;
                currentCharacterData.features.specialTraits[index].effect = '';
                currentCharacterData.features.specialTraits[index].cost = 0;
            }
            scheduleAutoSave();
            return;
        }

        const templates = await loadTemplatesForLobby('special_trait');
        const template = templates.find(t => t.id === selectedId);
        if (!template) return;

        const effectInput = document.querySelector(`[name="features.specialTraits.${index}.effect"]`);
        const costInput = document.querySelector(`[name="features.specialTraits.${index}.cost"]`);
        if (effectInput) effectInput.value = ruleText(template.attributes?.effect);
        if (costInput) costInput.value = template.attributes?.cost || 0;

        if (!currentCharacterData.features) currentCharacterData.features = {};
        if (!currentCharacterData.features.specialTraits) currentCharacterData.features.specialTraits = [];
        const trait = currentCharacterData.features.specialTraits[index];
        trait.name = template.name;
        trait.effect = ruleText(template.attributes?.effect);
        trait.cost = template.attributes?.cost || 0;
        trait.templateId = template.id;
        trait.ruleId = template.attributes?.ruleId;
        delete trait.choices;

        scheduleAutoSave();
    };
}

// Функция добавления новой особой черты (без шаблона)
window.openSessionReward = async function() {
    if(!window.isGM||!currentCharacterCanEdit)return;
    await forceSyncCharacter();
    const characterId=currentCharacterId;
    const {showSessionReward}=await import('./characterDevelopment.js');
    await showSessionReward(currentLobbyId,characterId,result=>{
        if(currentCharacterId!==characterId)return;
        currentCharacterData=result.data;renderSkillsTab(currentCharacterData);
        showNotification(`Начислено свободных очков: ${result.result.freePoints}; за обучение: ${result.result.trainingPoints}`,'success');
    });
};

window.addSpecialTrait = function() {
    updateDataFromFields();
    if (!currentCharacterData.features) currentCharacterData.features = {};
    if (!Array.isArray(currentCharacterData.features.specialTraits)) {
        currentCharacterData.features.specialTraits = [];
    }
    if(currentCharacterData.features.specialTraits.length>=3){showNotification('Можно иметь не более трёх особых черт','error');return;}
    currentCharacterData.features.specialTraits.push({ name: '', effect: '', cost: 0 });
    renderSkillsTab(currentCharacterData);
    scheduleAutoSave();
};

window.removeSpecialTrait = function(index) {
    updateDataFromFields();
    if (!currentCharacterData.features?.specialTraits) return;
    currentCharacterData.features.specialTraits.splice(index, 1);
    renderSkillsTab(currentCharacterData);
    scheduleAutoSave();
};

// Модальное окно для создания кастомной особой черты (GM)
window.openCreateSpecialTraitTemplateModal = function() {
    let modal = document.getElementById('create-special-trait-template-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'create-special-trait-template-modal';
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content" style="max-height: 80vh; overflow-y: auto;">
                <span class="close" onclick="document.getElementById('create-special-trait-template-modal').style.display='none'">&times;</span>
                <h3>Создать кастомный шаблон особой черты</h3>
                <div class="form-group">
                    <label>Название</label>
                    <input type="text" id="special-trait-name" class="form-control">
                </div>
                <div class="form-group">
                    <label>Эффект</label>
                    <input type="text" id="special-trait-effect" class="form-control">
                </div>
                <div class="form-group">
                    <label>Стоимость</label>
                    <input type="number" id="special-trait-cost" class="form-control number-input" value="0">
                </div>
                <div class="form-actions">
                    <button class="btn btn-primary" onclick="saveSpecialTraitTemplate()">Сохранить</button>
                    <button class="btn btn-secondary" onclick="document.getElementById('create-special-trait-template-modal').style.display='none'">Отмена</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }
    modal.style.display = 'flex';
};

window.saveSpecialTraitTemplate = async function() {
    const attributes = {
        effect: document.getElementById('special-trait-effect').value,
        cost: parseInt(document.getElementById('special-trait-cost').value) || 0
    };
    const data = {
        name: document.getElementById('special-trait-name').value,
        category: 'special_trait',
        subcategory: null,
        price: 0,
        weight: 0,
        volume: 0,
        attributes: attributes
    };

    try {
        await Server.createLobbyTemplate(currentLobbyId, data);
        clearTemplatesCache('special_trait');
        await renderSkillsTab(currentCharacterData);
        document.getElementById('create-special-trait-template-modal').style.display = 'none';
        showNotification('Шаблон особой черты создан', 'success');
    } catch (err) {
        showNotification(err.message);
    }
};

// ========== 5. ВКЛАДКА "ЭКИПИРОВКА" ==========
async function renderEquipmentTab(data) {
    const renderVersion = ++equipmentRenderVersion;
    const container = document.getElementById('sheet-tab-equipment');
    if (!container) return;
    equipmentOpenPanels = new Set(
        [...container.querySelectorAll('details[data-equipment-panel][open]')]
            .map(panel => panel.dataset.equipmentPanel)
    );

    const eq = data.equipment || {};
    if (armorHasIntegratedHelmet(eq.armor)) {
        syncIntegratedArmorHelmet(eq.armor);
    } else if (!armorHasIntegratedHelmet(eq.armor) && eq.helmet?.integratedWithArmor) {
        delete eq.helmet;
    }
    const helmet = eq.helmet || {};
    const gasMask = eq.gasMask || {};
    const armor = eq.armor || {};
    const weapons = data.weapons || [];

    if (!helmet.modifications) helmet.modifications = [];
    if (!gasMask.modifications) gasMask.modifications = [];
    if (!armor.modifications) armor.modifications = [];

    const materialOptions = ['Текстиль', 'Композит', 'Кевлар', 'Плита'];
    const conditionOptions = ['1. Целая', '2. Немного повреждена', '3. Повреждена', '4. Сильно повреждена', '5. Поломана'];

    let weaponTemplates = [], helmetTemplates = [], gasMaskTemplates = [], armorTemplates = [];
    let modificationTemplates = [];
    try {
        const firearmTemplates = await loadTemplatesForLobby('weapon');
        const meleeTemplates = await loadTemplatesForLobby('melee_weapon');
        weaponTemplates = [...firearmTemplates, ...meleeTemplates]
            .filter(t => window.isGM || t.source !== 'local');
        helmetTemplates = (await loadTemplatesForLobby('helmet')).filter(t => window.isGM || t.source !== 'local');
        gasMaskTemplates = (await loadTemplatesForLobby('gas_mask')).filter(t => window.isGM || t.source !== 'local');
        armorTemplates = (await loadTemplatesForLobby('armor')).filter(t => window.isGM || t.source !== 'local');
        modificationTemplates = await loadTemplatesForLobby('modification');
    } catch (e) {
        console.error('Failed to load templates', e);
    }
    // A newer socket/local update may arrive while templates are loading.
    if (renderVersion !== equipmentRenderVersion) return;
    const equippedHelmetTemplate = helmetTemplates.find(
        template => template.id == helmet.templateId
    );
    if (helmet.templateId && helmet.movementPenalty === undefined) {
        helmet.movementPenalty = Number(
            equippedHelmetTemplate?.attributes?.movement_penalty
        ) || 0;
    }

    const helmetModTemplates = modificationTemplates.filter(t => t.attributes?.type === 'helmet');
    const gasMaskModTemplates = modificationTemplates.filter(t => t.attributes?.type === 'gas_mask');
    const armorModTemplates = modificationTemplates.filter(t => t.attributes?.type === 'armor');
    const pdaModTemplates = modificationTemplates.filter(t => t.attributes?.type === 'pda');
    const weaponModuleTemplates = modificationTemplates.filter(t => t.attributes?.type === 'weapon_module' || t.attributes?.category === 'module');
    const weaponModTemplates = modificationTemplates.filter(t => t.attributes?.type === 'weapon_modification' || t.attributes?.category === 'modification');

    function groupByCategory(templates) {
        const grouped = {};
        templates.forEach(t => {
            const cat = t.subcategory || 'Прочее';
            if (!grouped[cat]) grouped[cat] = [];
            grouped[cat].push(t);
        });
        return grouped;
    }

    const groupedHelmetMods = groupByCategory(helmetModTemplates);
    const groupedGasMaskMods = groupByCategory(gasMaskModTemplates);
    const groupedArmorMods = groupByCategory(armorModTemplates);
    const groupedPdaMods = groupByCategory(pdaModTemplates);

    function protectionGrid(prefix, prot) {
        prot = prot || {};
        return `
            <div class="protection-grid">
                <div>Физ</div><div>Хим</div><div>Терм</div><div>Элек</div><div>Рад</div>
                <input type="number" step="1" class="number-input form-control" data-protection-percent="true" name="${prefix}.protection.physical" value="${protectionPercentValue(prot.physical)}">
                <input type="number" step="1" class="number-input form-control" data-protection-percent="true" name="${prefix}.protection.chemical" value="${protectionPercentValue(prot.chemical)}">
                <input type="number" step="1" class="number-input form-control" data-protection-percent="true" name="${prefix}.protection.thermal" value="${protectionPercentValue(prot.thermal)}">
                <input type="number" step="1" class="number-input form-control" data-protection-percent="true" name="${prefix}.protection.electric" value="${protectionPercentValue(prot.electric)}">
                <input type="number" step="1" class="number-input form-control" data-protection-percent="true" name="${prefix}.protection.radiation" value="${protectionPercentValue(prot.radiation)}">
            </div>
        `;
    }

    let html = `
        <div class="equipment-group" data-equipment-group="weapons">
            <div class="equipment-header"><h4>Оружие</h4></div>
            <div class="equipment-row" style="flex-direction:column;">
                <div id="weapons-container"></div>
                <button type="button" class="btn btn-sm" onclick="addWeapon()" style="align-self:flex-start;margin-top:10px;">+ Добавить оружие</button>
            </div>
        </div>

        <!-- Шлем -->
        <div class="equipment-group" data-equipment-group="helmet">
            <div class="equipment-row" style="display: flex; gap: 10px;">
                <div class="equipment-main-block helmet-main-block">
                    <div class="block-header">
                        <h4>Шлем ${renderCreatedByPlayerBadge(helmet)}</h4>
                        <div style="display: flex; gap: 10px;">
                            ${window.isGM ? `<button type="button" class="btn btn-sm btn-secondary" onclick="openCreateHelmetTemplateModal()">➕ Создать кастом</button>` : ''}
                            ${helmet.templateId && !helmet.integratedWithArmor ? `<button type="button" class="btn btn-sm btn-danger" onclick="unequipHelmet()">Снять</button>` : ''}
                        </div>
                    </div>
                    <div class="fields-container">
                        <div class="field-group field-name">
                            <label>Название</label>
                            <select name="equipment.helmet.templateId" class="form-control" onchange="fillHelmetFromPreset(this)">
                                <option value="">-- Выберите --</option>
                                ${helmet.integratedWithArmor ? `<option value="${helmet.templateId}" selected>${helmet.name}</option>` : ''}
                                ${helmetTemplates.map(t => `<option value="${t.id}" ${helmet.templateId == t.id ? 'selected' : ''}>${t.name} ${t.source === 'local' ? '(кастом)' : ''}</option>`).join('')}
                            </select>
                        </div>
                        <div class="field-group field-number field-durability">
                            <label>Прочность</label>
                            <input type="number" class="number-input form-control" name="equipment.helmet.durability" value="${helmet.durability || 0}">
                        </div>
                        <div class="field-group field-select field-stage">
                            <label>Стадия</label>
                            <select name="equipment.helmet.stage" class="form-control" onchange="updateArmorStageFromSelect(this, 'helmet')">
                                ${['1. Целая', '2. Немного повреждена', '3. Повреждена', '4. Сильно повреждена', '5. Поломана'].map((name, idx) =>
                                    `<option value="${idx+1}" ${helmet.stage == (idx+1) ? 'selected' : ''}>${name}</option>`
                                ).join('')}
                            </select>
                        </div>
                        <div class="field-group field-number field-stage-durability">
                            <label>Прочность стадии</label>
                            <input type="number" class="number-input form-control" name="equipment.helmet.currentStageDurability" value="${helmet.currentStageDurability ?? helmet.stageDurability ?? 0}" step="1" min="0">
                        </div>
                        <div class="field-group field-number field-stage-durability-max">
                            <label>Макс. прочность стадии</label>
                            <input type="number" class="number-input form-control" value="${calculateStageDurability(helmet.durability || 0, helmet.material || 'Текстиль')}" readonly disabled>
                        </div>
                        <div class="field-group field-select field-material">
                            <label>Материал</label>
                            <select name="equipment.helmet.material" class="form-control">
                                ${materialOptions.map(opt => `<option value="${opt}" ${helmet.material === opt ? 'selected' : ''}>${opt}</option>`).join('')}
                            </select>
                        </div>
                        <div class="field-group field-number"><label>Штраф Точности</label><input type="number" class="number-input form-control" name="equipment.helmet.accuracyPenalty" value="${helmet.accuracyPenalty || 0}"></div>
                        <div class="field-group field-number field-ergonomics-penalty"><label>Штраф Эргономики</label><input type="number" class="number-input form-control" name="equipment.helmet.ergonomicsPenalty" value="${helmet.ergonomicsPenalty || 0}"></div>
                        <div class="field-group field-number"><label>Бонус Харизмы</label><input type="number" class="number-input form-control" name="equipment.helmet.charismaBonus" value="${helmet.charismaBonus || 0}"></div>
                        <div class="field-group field-number field-movement-penalty"><label>Штраф перемещения</label><input type="number" class="number-input form-control" name="equipment.helmet.movementPenalty" value="${helmet.movementPenalty || 0}"></div>
                    </div>
                </div>
                <div class="equipment-protection-block" style="flex: 1;">
                    <div class="block-header"><h5>Защита</h5></div>
                    ${protectionGrid('equipment.helmet', helmet.protection)}
                </div>
                <div class="equipment-zones-block" style="flex: 1;">
                    <div class="block-header"><h5>Зоны защиты</h5></div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 5px; padding: 5px; text-align: center;">
                        ${(() => {
                            const template = helmetTemplates.find(t => t.id == helmet.templateId);
                            if (helmet.integratedWithArmor) return '<div style="grid-column:span 2;">Голова</div>';
                            if (!template?.attributes?.protection_zones?.length) return '<span style="color:#aaa; grid-column: span 2;">Не указаны</span>';
                            const zoneNames = { crown: 'Темя', back: 'Затылок', ears: 'Уши', face: 'Забрало' };
                            return template.attributes.protection_zones.map(z => `<div>${zoneNames[z] || z}</div>`).join('');
                        })()}
                    </div>
                </div>
            </div>
            ${renderSlotsUniversal(helmet, ['equipment', 'helmet']) ? `
                <div style="margin-top:10px; padding:8px; background:rgba(0,0,0,0.1); border-radius:4px;">
                    ${hasFilterSlot(helmet) ? `<label style="display:flex;align-items:center;gap:7px;margin-bottom:8px;cursor:pointer;"><input type="checkbox" name="equipment.helmet.autoReplaceFilters" ${helmet.autoReplaceFilters === true ? 'checked' : ''}> Автозамена фильтров</label>` : ''}
                    ${renderSlotsUniversal(helmet, ['equipment', 'helmet'])}
                </div>
            ` : ''}
            <div class="modifications-block">
                <div style="display:flex; align-items:center;">
                    <h5 style="margin:0;">Модификации шлема</h5>
                    <button type="button" class="btn btn-sm btn-secondary" onclick="addHelmetModification()" style="padding:2px 8px;">➕</button>
                </div>
                <div id="helmet-modifications-container">${renderHelmetModifications(helmet.modifications, groupedHelmetMods)}</div>
            </div>
        </div>

        <!-- Противогаз -->
        <div class="equipment-group" data-equipment-group="gasMask">
            <div class="equipment-row">
                <div class="equipment-main-block">
                    <div class="block-header">
                        <h4>Противогаз ${renderCreatedByPlayerBadge(gasMask)}</h4>
                        <div style="display: flex; gap: 10px;">
                            ${window.isGM ? `<button type="button" class="btn btn-sm btn-secondary" onclick="openCreateGasMaskTemplateModal()">➕ Создать кастом</button>` : ''}
                            ${gasMask.templateId ? `<button type="button" class="btn btn-sm btn-danger" onclick="unequipGasMask()">Снять</button>` : ''}
                        </div>
                    </div>
                    <div class="fields-container">
                        <div class="field-group field-name">
                            <label>Название</label>
                            <select name="equipment.gasMask.templateId" class="form-control" onchange="fillGasMaskFromPreset(this)">
                                <option value="">-- Выберите --</option>
                                ${gasMaskTemplates.map(t => `<option value="${t.id}" ${gasMask.templateId == t.id ? 'selected' : ''}>${t.name} ${t.source === 'local' ? '(кастом)' : ''}</option>`).join('')}
                            </select>
                        </div>
                        <div class="field-group field-number field-durability">
                            <label>Прочность</label>
                            <input type="number" class="number-input form-control" name="equipment.gasMask.durability" value="${gasMask.durability || 0}">
                        </div>
                        <div class="field-group field-number"><label>Штраф Точности</label><input type="number" class="number-input form-control" name="equipment.gasMask.accuracyPenalty" value="${gasMask.accuracyPenalty || 0}"></div>
                        <div class="field-group field-number field-ergonomics-penalty"><label>Штраф Эргономики</label><input type="number" class="number-input form-control" name="equipment.gasMask.ergonomicsPenalty" value="${gasMask.ergonomicsPenalty || 0}"></div>
                        <div class="field-group field-number"><label>Бонус Харизмы</label><input type="number" class="number-input form-control" name="equipment.gasMask.charismaBonus" value="${gasMask.charismaBonus || 0}"></div>
                    </div>
                </div>
                <div class="equipment-protection-block">
                    <div class="block-header"><h5>Защита</h5></div>
                    ${protectionGrid('equipment.gasMask', gasMask.protection)}
                </div>
            </div>
            ${renderSlotsUniversal(gasMask, ['equipment', 'gasMask']) ? `
                <div style="margin-top:10px; padding:8px; background:rgba(0,0,0,0.1); border-radius:4px;">
                    ${hasFilterSlot(gasMask) ? `<label style="display:flex;align-items:center;gap:7px;margin-bottom:8px;cursor:pointer;"><input type="checkbox" name="equipment.gasMask.autoReplaceFilters" ${gasMask.autoReplaceFilters === true ? 'checked' : ''}> Автозамена фильтров</label>` : ''}
                    ${renderSlotsUniversal(gasMask, ['equipment', 'gasMask'])}
                </div>
            ` : ''}
            <div class="modifications-block">
                <div style="display:flex; align-items:center;">
                    <h5 style="margin:0;">Модификации противогаза</h5>
                    <button type="button" class="btn btn-sm btn-secondary" onclick="addGasMaskModification()" style="padding:2px 8px;">➕</button>
                </div>
                <div id="gasMask-modifications-container">${renderGasMaskModifications(gasMask.modifications, groupedGasMaskMods)}</div>
            </div>
        </div>

        <!-- Броня -->
        <div class="equipment-group" data-equipment-group="armor">
            <div class="equipment-row" style="display: flex; gap: 10px;">
                <div class="equipment-main-block" style="flex: 2;">
                    <div class="block-header">
                        <h4>Броня ${renderCreatedByPlayerBadge(armor)}</h4>
                        <div style="display: flex; gap: 10px;">
                            ${window.isGM ? `<button type="button" class="btn btn-sm btn-secondary" onclick="openCreateArmorTemplateModal()">➕ Создать кастом</button>` : ''}
                            ${armor.templateId ? `<button type="button" class="btn btn-sm btn-danger" onclick="unequipArmor()">Снять</button>` : ''}
                        </div>
                    </div>
                    <div class="fields-container">
                        <div class="field-group field-name">
                            <label>Название</label>
                            <select name="equipment.armor.templateId" class="form-control" onchange="fillArmorFromPreset(this)">
                                <option value="">-- Выберите --</option>
                                ${armorTemplates.map(t => `<option value="${t.id}" ${armor.templateId == t.id ? 'selected' : ''}>${t.name} ${t.source === 'local' ? '(кастом)' : ''}</option>`).join('')}
                            </select>
                        </div>
                        <div class="field-group field-number field-durability">
                            <label>Прочность</label>
                            <input type="number" class="number-input form-control" name="equipment.armor.durability" value="${armor.durability || 0}">
                        </div>
                        <div class="field-group field-select field-stage">
                            <label>Стадия</label>
                            <select name="equipment.armor.stage" class="form-control" onchange="updateArmorStageFromSelect(this, 'armor')">
                                ${['1. Целая', '2. Немного повреждена', '3. Повреждена', '4. Сильно повреждена', '5. Поломана'].map((name, idx) =>
                                    `<option value="${idx+1}" ${armor.stage == (idx+1) ? 'selected' : ''}>${name}</option>`
                                ).join('')}
                            </select>
                        </div>
                        <div class="field-group field-number field-stage-durability">
                            <label>Прочность стадии</label>
                            <input type="number" class="number-input form-control" name="equipment.armor.currentStageDurability" value="${armor.currentStageDurability ?? armor.stageDurability ?? 0}" step="1" min="0">
                        </div>
                        <div class="field-group field-number field-stage-durability-max">
                            <label>Макс. прочность стадии</label>
                            <input type="number" class="number-input form-control" value="${calculateStageDurability(armor.durability || 0, armor.material || 'Текстиль')}" readonly disabled>
                        </div>
                        <div class="field-group field-select field-material">
                            <label>Материал</label>
                            <select name="equipment.armor.material" class="form-control">
                                ${materialOptions.map(opt => `<option value="${opt}" ${armor.material === opt ? 'selected' : ''}>${opt}</option>`).join('')}
                            </select>
                        </div>
                        <div class="field-group field-number field-movement-penalty"><label>Штраф перемещения</label><input type="number" class="number-input form-control" name="equipment.armor.movementPenalty" value="${armor.movementPenalty || 0}"></div>
                    </div>
                </div>
                <div class="equipment-protection-block" style="flex: 1;">
                    <div class="block-header"><h5>Защита</h5></div>
                    ${protectionGrid('equipment.armor', armor.protection)}
                </div>
                <div class="equipment-zones-block" style="flex: 1;">
                    <div class="block-header"><h5>Зоны защиты</h5></div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 5px; padding: 5px; text-align: center;">
                        ${(() => {
                            const template = armorTemplates.find(t => t.id == armor.templateId);
                            if (!template?.attributes?.protection_zones?.length) return '<span style="color:#aaa; grid-column: span 2;">Не указаны</span>';
                            const zoneNames = { torso: 'Торс', arms: 'Руки', legs: 'Ноги', head: 'Голова' };
                            return template.attributes.protection_zones.map(z => `<div>${zoneNames[z] || z}</div>`).join('');
                        })()}
                    </div>
                </div>
            </div>
            <!-- Слоты брони всегда занимают отдельную строку. -->
                ${renderSlotsUniversal(armor, ['equipment', 'armor']) ? `
                    <div class="equipment-slots-row">
                        ${renderSlotsUniversal(armor, ['equipment', 'armor'])}
                    </div>
                ` : ''}
                ${(armor.containers || []).length ? `
                <div class="armor-containers-row" data-widget-block="armor-containers">
                    ${(armor.containers || []).map((slot, idx) => `
                        <div style="border: 1px solid #666; border-radius: 4px; padding: 5px; width: 180px; background: rgba(0,0,0,0.2);">
                            <div style="display: flex; align-items: center; gap: 5px;">
                                <strong>${idx+1}:</strong>
                                ${slot.item ? `
                                    <span style="flex:1; font-size:0.9rem;">${escapeHtml(slot.item.name)}</span>
                                    <button type="button" class="btn btn-sm btn-danger" onclick="removeArmorContainerItem(${idx})" title="Извлечь">✕</button>
                                ` : `
                                    <button type="button" class="btn btn-sm btn-primary" onclick="addItemToArmorContainer(${idx})" style="width:100%;">Вставить</button>
                                `}
                            </div>
                            ${slot.item && slot.item.category === 'container' && slot.item.installedModules?.length ? `
                                <div style="margin-top: 5px; font-size: 0.75rem; color: #aaa; word-break: break-all;">
                                    📦 ${slot.item.installedModules.map(m => m.name).join(', ')}
                                </div>
                            ` : ''}
                        </div>
                    `).join('')}
                </div>
                ` : ''}
            <div class="modifications-block">
                <div style="display:flex; align-items:center;">
                    <h5 style="margin:0;">Модификации брони</h5>
                    <button type="button" class="btn btn-sm btn-secondary" onclick="addArmorModification()" style="padding:2px 8px;">➕</button>
                </div>
                <div id="armor-modifications-container">${renderArmorModifications(armor.modifications, groupedArmorMods)}</div>
            </div>
        </div>

        <!-- Детектор аномалий -->
        <div class="equipment-group" data-equipment-group="detector">
            <div class="equipment-row">
                <div class="equipment-main-block">
                    <div class="block-header">
                        <h4>Детектор аномалий</h4>
                        ${eq.detector?.templateId ? `<button type="button" class="btn btn-sm btn-danger" onclick="unequipDetector()">Снять</button>` : ''}
                    </div>
                    <div class="fields-container">
                        <div class="field-group field-name">
                            <label>Название</label>
                            <strong>${escapeHtml(eq.detector?.name || 'Не надет')}</strong>
                        </div>
                        <div class="field-group field-number">
                            <label>Бонус</label>
                            <input type="number" class="number-input form-control" name="equipment.detector.bonus" value="${eq.detector?.bonus || 0}">
                        </div>
                    </div>
                </div>
            </div>
            ${eq.detector ? renderSlotsUniversal(eq.detector, ['equipment', 'detector']) : ''}
        </div>

        <!-- Косметическая экипировка -->
        <div class="equipment-group" data-equipment-group="cosmetic">
            <h4>Косметическая экипировка</h4>
            <!-- Первая строка: наушники, очки, перчатки (3 колонки) -->
            <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin-bottom: 20px;">
                <!-- Наушники -->
                <div>
                    <div class="block-header" style="display: flex; justify-content: space-between; align-items: center;">
                        <h5>Наушники</h5>
                        ${eq.headphones?.templateId ? `<button type="button" class="btn btn-sm btn-danger" onclick="unequipHeadphones()">Снять</button>` : ''}
                    </div>
                    <div style="display: flex; flex-direction: column; gap: 5px;">
                        ${eq.headphones ? `
                            <div><strong>${escapeHtml(eq.headphones.name)}</strong></div>
                            <div><label>Коэф. оглушения</label> <input type="number" class="form-control number-input" name="equipment.headphones.deafeningCoef" value="${eq.headphones.deafeningCoef || 0}" step="0.1" style="width: 100%; min-width: 180px;"></div>
                            <div><label>Поглощение шума</label> <input type="number" class="form-control number-input" name="equipment.headphones.noiseAbsorption" value="${eq.headphones.noiseAbsorption || 0}" style="width: 100%; min-width: 180px;"></div>
                            <div><label>Бонус внимания (слух)</label> <input type="number" class="form-control number-input" name="equipment.headphones.awarenessBonus" value="${eq.headphones.awarenessBonus || 0}" style="width: 100%; min-width: 180px;"></div>
                        ` : '<em>Не надеты</em>'}
                    </div>
                </div>
                <!-- Очки -->
                <div>
                    <div class="block-header" style="display: flex; justify-content: space-between; align-items: center;">
                        <h5>Очки</h5>
                        ${eq.glasses?.templateId ? `<button type="button" class="btn btn-sm btn-danger" onclick="unequipGlasses()">Снять</button>` : ''}
                    </div>
                    <div style="display: flex; flex-direction: column; gap: 5px;">
                        ${eq.glasses ? `
                            <div><strong>${escapeHtml(eq.glasses.name)}</strong></div>
                            <div><label>Бонус харизмы</label> <input type="number" class="form-control number-input" name="equipment.glasses.charismaBonus" value="${eq.glasses.charismaBonus || 0}" step="0.1" style="width: 100%; min-width: 180px;"></div>
                            <div><label>Защита от ослепления</label> <input type="number" class="form-control number-input" name="equipment.glasses.flashProtection" value="${eq.glasses.flashProtection || 1}" step="0.05" style="width: 100%; min-width: 180px;"></div>
                            <div><label>Физ. защита глаз (%)</label> <input type="number" class="form-control number-input" name="equipment.glasses.eyePhysicalProtection" value="${eq.glasses.eyePhysicalProtection || 0}" style="width: 100%; min-width: 180px;"></div>
                        ` : '<em>Не надеты</em>'}
                    </div>
                </div>
                <!-- Перчатки -->
                <div>
                    <div class="block-header" style="display: flex; justify-content: space-between; align-items: center;">
                        <h5>Перчатки</h5>
                        ${eq.gloves?.templateId ? `<button type="button" class="btn btn-sm btn-danger" onclick="unequipGloves()">Снять</button>` : ''}
                    </div>
                    <div style="display: flex; flex-direction: column; gap: 5px;">
                        ${eq.gloves ? `
                            <div><strong>${escapeHtml(eq.gloves.name)}</strong></div>
                            <div><label>Бонус харизмы</label> <input type="number" class="form-control number-input" name="equipment.gloves.charismaBonus" value="${eq.gloves.charismaBonus || 0}" step="0.1" style="width: 100%; min-width: 180px;"></div>
                            <div><label>Эффект</label> <input type="text" class="form-control" name="equipment.gloves.effect" value="${escapeHtml(eq.gloves.effect || '')}" style="width: 100%; min-width: 180px;"></div>
                        ` : '<em>Не надеты</em>'}
                    </div>
                </div>
            </div>

            <!-- Вторая строка: бижутерия (5 слотов в строку с переносом) -->
            <div style="margin-top: 10px;">
                <h5>Бижутерия</h5>
                <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 15px;">
                    <!-- Кольцо -->
                    <div>
                        <div class="block-header" style="display: flex; justify-content: space-between; align-items: center;"><strong>Кольцо</strong> ${eq.ring?.templateId ? `<button type="button" class="btn btn-sm btn-danger" onclick="unequipRing()">Снять</button>` : ''}</div>
                        <div style="margin-top: 5px;">
                            ${eq.ring ? `
                                <div><strong>${escapeHtml(eq.ring.name)}</strong></div>
                                <div><label>Бонус харизмы</label> <input type="number" class="form-control number-input" name="equipment.ring.charismaBonus" value="${eq.ring.charismaBonus || 0}" step="0.1" style="width: 100%;"></div>
                            ` : '<em>Не надето</em>'}
                        </div>
                    </div>
                    <!-- Амулет -->
                    <div>
                        <div class="block-header" style="display: flex; justify-content: space-between; align-items: center;"><strong>Амулет/Цепочка</strong> ${eq.necklace?.templateId ? `<button type="button" class="btn btn-sm btn-danger" onclick="unequipNecklace()">Снять</button>` : ''}</div>
                        <div style="margin-top: 5px;">
                            ${eq.necklace ? `
                                <div><strong>${escapeHtml(eq.necklace.name)}</strong></div>
                                <div><label>Бонус харизмы</label> <input type="number" class="form-control number-input" name="equipment.necklace.charismaBonus" value="${eq.necklace.charismaBonus || 0}" step="0.1" style="width: 100%;"></div>
                            ` : '<em>Не надето</em>'}
                        </div>
                    </div>
                    <!-- Серьги -->
                    <div>
                        <div class="block-header" style="display: flex; justify-content: space-between; align-items: center;"><strong>Серьги</strong> ${eq.earrings?.templateId ? `<button type="button" class="btn btn-sm btn-danger" onclick="unequipEarrings()">Снять</button>` : ''}</div>
                        <div style="margin-top: 5px;">
                            ${eq.earrings ? `
                                <div><strong>${escapeHtml(eq.earrings.name)}</strong></div>
                                <div><label>Бонус харизмы</label> <input type="number" class="form-control number-input" name="equipment.earrings.charismaBonus" value="${eq.earrings.charismaBonus || 0}" step="0.1" style="width: 100%;"></div>
                            ` : '<em>Не надето</em>'}
                        </div>
                    </div>
                    <!-- Браслет 1 -->
                    <div>
                        <div class="block-header" style="display: flex; justify-content: space-between; align-items: center;"><strong>Браслет 1</strong> ${eq.bracelet1?.templateId ? `<button type="button" class="btn btn-sm btn-danger" onclick="unequipBracelet(1)">Снять</button>` : ''}</div>
                        <div style="margin-top: 5px;">
                            ${eq.bracelet1 ? `
                                <div><strong>${escapeHtml(eq.bracelet1.name)}</strong></div>
                                <div><label>Бонус харизмы</label> <input type="number" class="form-control number-input" name="equipment.bracelet1.charismaBonus" value="${eq.bracelet1.charismaBonus || 0}" step="0.1" style="width: 100%;"></div>
                            ` : '<em>Не надет</em>'}
                        </div>
                    </div>
                    <!-- Браслет 2 -->
                    <div>
                        <div class="block-header" style="display: flex; justify-content: space-between; align-items: center;"><strong>Браслет 2</strong> ${eq.bracelet2?.templateId ? `<button type="button" class="btn btn-sm btn-danger" onclick="unequipBracelet(2)">Снять</button>` : ''}</div>
                        <div style="margin-top: 5px;">
                            ${eq.bracelet2 ? `
                                <div><strong>${escapeHtml(eq.bracelet2.name)}</strong></div>
                                <div><label>Бонус харизмы</label> <input type="number" class="form-control number-input" name="equipment.bracelet2.charismaBonus" value="${eq.bracelet2.charismaBonus || 0}" step="0.1" style="width: 100%;"></div>
                            ` : '<em>Не надет</em>'}
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div class="equipment-group" data-equipment-group="pda">
            <div style="display:flex; align-items:center;">
                <h4 style="margin:0;">Модификации КПК</h4>
                <button type="button" class="btn btn-sm btn-secondary" onclick="addPdaItem()" style="padding:2px 8px;">➕</button>
            </div>
            <div id="pda-modifications-container">${renderPdaModifications(data.modifications?.pda?.items || [], groupedPdaMods)}</div>
        </div>
    `;

    const viewState = captureSheetViewState();
    container.innerHTML = html;
    await renderWeapons(weapons, weaponTemplates, weaponModuleTemplates, weaponModTemplates);
    renderEquipmentLoadout(container, data, weaponTemplates, helmetTemplates, gasMaskTemplates, armorTemplates);
    await renderPortableLights(container,currentCharacterId,data,window.operatePortableLight,
        ()=>renderVersion===equipmentRenderVersion,currentCharacterCanEdit&&!medicalOperationInProgress).catch(error=>console.warn('Не удалось загрузить фонари:',error));
    if(renderVersion===equipmentRenderVersion)renderProstheses(container,data,window.operateProsthesis,currentCharacterCanEdit&&!medicalOperationInProgress,currentCharacterId);
    restoreSheetViewState(viewState);
}

function equipmentModule(item, slotType) {
    const modules = Array.isArray(item?.installedModules) ? item.installedModules : [];
    return modules.find(module => (
        module?.slotType === slotType || module?.attributes?.slot_type === slotType
    ));
}

function equipmentStatusLines(equipment, helmetTemplates, gasMaskTemplates) {
    const lines = [];
    const supportsFilter = (item, templates) => Boolean(
        hasFilterSlot(item)
        || (item?.attributes?.slots || []).some(slot => slot?.type === 'filter')
        || templates.find(template => template.id == item?.templateId)?.attributes?.slots?.some(slot => slot?.type === 'filter')
    );
    const filterOwner = [equipment.gasMask, equipment.helmet]
        .find((item, index) => item?.templateId && supportsFilter(item, index === 0 ? gasMaskTemplates : helmetTemplates));
    if (filterOwner) {
        const filter = equipmentModule(filterOwner, 'filter');
        lines.push(filter
            ? `Фильтр: ${filter.durability ?? filter.attributes?.durability ?? 0}/${filter.maxDurability ?? filter.attributes?.max_durability ?? '?'} (${filterOwner.name || 'противогаз'})`
            : 'Фильтр: не установлен');
    }
    if (equipment.detector?.templateId) {
        const battery = equipmentModule(equipment.detector, 'battery');
        lines.push(battery
            ? `Детектор: заряд ${battery.attributes?.power ?? battery.power ?? '?'}%`
            : 'Детектор: батарея не установлена');
    }
    if (getExoskeletonPowerProfile({ equipment }).isExoskeleton) {
        const battery = equipmentModule(equipment.armor, 'exoskeleton_battery');
        const days = Number(battery?.attributes?.remaining_days) || 0;
        lines.push(battery ? `Экзоскелет: ${days > 0 ? `${days} сут. заряда` : 'аккумулятор разряжен'}` : 'Экзоскелет: аккумулятор не установлен');
    }
    return lines;
}

function equipmentSlotMetrics(item, kind, template = null) {
    if (!item) return [];
    const attributes = template?.attributes || item.attributes || {};
    const metrics = [];
    const add = (label, value, suffix = '') => {
        if (value !== undefined && value !== null && value !== '') metrics.push(`${label}: ${value}${suffix}`);
    };
    if (kind === 'weapon') {
        add('Прочность', item.durability ?? attributes.durability);
        if (item.category === 'melee_weapon' || template?.category === 'melee_weapon') {
            add('Урон', attributes.damage);
            add('Точность', attributes.accuracy);
        } else {
            add('Калибр', item.caliber ?? attributes.caliber);
            add('Патроны', getWeaponAmmoCount(item));
            add('Точность', item.accuracy ?? attributes.accuracy);
            add('Эргономика', item.ergonomics ?? attributes.ergonomics);
        }
    } else {
        add('Прочность', item.durability);
        if (item.protection) {
            add('Физ. защита', formatProtectionPercent(item.protection.physical || 0));
            add('Рад. защита', formatProtectionPercent(item.protection.radiation || 0));
        }
        if (kind === 'detector') add('Бонус при встрече с аномалией', item.bonus);
        if (kind === 'cosmetic') add('Бонус харизмы', item.charismaBonus);
    }
    return metrics;
}

function equipmentFigureAppearance(equipment, armorTemplates, helmetTemplates) {
    const armor = equipment.armor;
    const helmet = equipment.helmet;
    const armorTemplate = armorTemplates.find(template => template.id == armor?.templateId);
    const helmetTemplate = helmetTemplates.find(template => template.id == helmet?.templateId);
    const normalize = value => String(value || '').toLowerCase().replace(/ё/g, 'е').trim();
    const armorName = normalize(armor?.name || armorTemplate?.name);
    const armorClass = normalize([
        armorTemplate?.item_class, armorTemplate?.subcategory,
        armorTemplate?.attributes?.armor_class, armor?.item_class,
        armor?.subcategory, armor?.attributes?.armor_class,
    ].filter(Boolean).join(' '));
    let armorKind = 'none';
    if (armor?.templateId) {
        if (armor?.isExoskeleton || armorTemplate?.attributes?.is_exoskeleton || armorName.includes('экзоскелет')) {
            armorKind = 'exo';
        } else if (/аномальн|аномал/.test(armorClass) || /купол|гроб|химзащиты/.test(armorName)) {
            armorKind = 'anomalous';
        } else if (armorName === 'броня путника') {
            armorKind = 'medium';
        } else if (/тяжел/.test(armorClass) || /весы|страж|затвор|пшнх|литейщик/.test(armorName)) {
            armorKind = 'heavy';
        } else if (/легк/.test(armorClass) || /кожаная|бандитская/.test(armorName)) {
            armorKind = 'light';
        } else {
            armorKind = 'medium';
        }
    }
    let helmetKind = 'none';
    if (helmet?.templateId) {
        const helmetName = normalize(helmet.name || helmetTemplate?.name);
        const zones = helmetTemplate?.attributes?.protection_zones || helmet?.attributes?.protection_zones;
        const hasFilter = helmetTemplate?.attributes?.requires_filter
            || helmetTemplate?.attributes?.slots?.some(slot => slot.type === 'filter')
            || helmet?.attributes?.requires_filter;
        if (helmet.integratedWithArmor && armorKind === 'anomalous') {
            helmetKind = 'anomalous';
        } else if (hasFilter || helmetName.includes('противогазо')) {
            helmetKind = 'sealed';
        } else if (helmet.integratedWithArmor
            || helmet?.installedModules?.some(module => module.slotType === 'visor')
            || (Array.isArray(zones) ? zones.includes('face') : helmetTemplate?.attributes?.integrated_visor)) {
            helmetKind = 'visor';
        } else {
            helmetKind = 'open';
        }
    }
    return { armorKind, helmetKind };
}

function renderEquipmentFigure(equipment, armorTemplates, helmetTemplates) {
    const { armorKind, helmetKind } = equipmentFigureAppearance(equipment, armorTemplates, helmetTemplates);
    const occupied = item => Boolean(item?.templateId || item?.model);
    const flags = {
        'data-armor': armorKind,
        'data-helmet': helmetKind,
        'data-mask': occupied(equipment.gasMask),
        'data-vest': occupied(equipment.vest),
        'data-belt': occupied(equipment.belt),
        'data-backpack': occupied(equipment.backpack),
        'data-detector': occupied(equipment.detector),
        'data-headphones': occupied(equipment.headphones),
        'data-glasses': occupied(equipment.glasses),
        'data-gloves': occupied(equipment.gloves),
        'data-ring': occupied(equipment.ring),
        'data-necklace': occupied(equipment.necklace),
        'data-earrings': occupied(equipment.earrings),
        'data-bracelet1': occupied(equipment.bracelet1),
        'data-bracelet2': occupied(equipment.bracelet2),
    };
    const attributes = Object.entries(flags).map(([key, value]) => `${key}="${value}"`).join(' ');
    return `<div class="loadout-figure" ${attributes}>
        <svg class="loadout-figure-art" viewBox="0 0 180 306" role="img" aria-label="Внешний вид надетого снаряжения">
            <g class="loadout-art-backpack"><path d="M59 91 Q44 91 43 108 L43 164 Q44 174 55 174 L125 174 Q136 174 137 164 L137 108 Q136 91 121 91Z"/><path d="M49 111 L49 157 M131 111 L131 157"/></g>
            <g class="loadout-art-base">
                <path d="M77 69 L103 69 L106 83 L74 83Z" class="loadout-art-skin"/>
                <path d="M69 25 Q72 13 90 13 Q108 13 111 25 L109 46 Q106 62 90 65 Q74 62 71 46Z" class="loadout-art-skin"/>
                <path d="M64 79 Q75 73 90 76 Q105 73 116 79 L126 163 Q111 175 90 172 Q69 175 54 163Z"/>
                <path d="M63 83 L51 88 L38 146 L40 177 L52 180 L57 150 L72 103Z M117 83 L129 88 L142 146 L140 177 L128 180 L123 150 L108 103Z"/>
                <path d="M58 164 L88 170 L86 222 L78 284 L57 284 L55 226Z M122 164 L92 170 L94 222 L102 284 L123 284 L125 226Z"/>
                <path d="M55 283 L80 283 L79 292 L52 292Z M100 283 L125 283 L128 292 L101 292Z" class="loadout-art-boots"/>
            </g>
            <g class="loadout-art-hands"><path d="M40 174 L52 177 L51 187 L47 194 L43 193 L38 184Z M128 177 L140 174 L142 184 L137 193 L133 194 L129 187Z"/></g>
            <g class="loadout-art-face"><path d="M77 36 L84 36 M96 36 L103 36 M90 39 L88 47 L92 47 M84 54 Q90 57 96 54"/></g>
            <g class="loadout-art-armor loadout-art-armor-light"><path d="M63 82 L77 77 L90 94 L103 77 L117 82 L125 157 Q90 173 55 157Z"/><path d="M75 81 L89 108 L105 81 M89 108 L90 159" class="loadout-art-detail"/></g>
            <g class="loadout-art-armor loadout-art-armor-medium"><path d="M62 84 L76 78 L104 78 L118 84 L120 149 L109 163 L71 163 L60 149Z"/><path d="M74 91 L106 91 L110 135 L70 135Z M75 141 L105 141 L105 154 L75 154Z" class="loadout-art-plate"/><path d="M58 87 L70 82 L74 99 L59 105Z M122 87 L110 82 L106 99 L121 105Z" class="loadout-art-plate"/></g>
            <g class="loadout-art-armor loadout-art-armor-heavy"><path d="M56 83 L74 75 L106 75 L124 83 L132 115 L119 126 L119 160 L102 174 L78 174 L61 160 L61 126 L48 115Z"/><path d="M72 87 L108 87 L113 116 L105 145 L75 145 L67 116Z M74 149 L106 149 L106 166 L74 166Z M57 176 L84 179 L82 216 L56 220Z M96 179 L123 176 L124 220 L98 216Z" class="loadout-art-plate"/><path d="M44 115 L56 120 L51 152 L38 149Z M136 115 L124 120 L129 152 L142 149Z" class="loadout-art-plate"/></g>
            <g class="loadout-art-armor loadout-art-armor-anomalous"><path d="M65 81 L76 75 L104 75 L115 81 L127 160 L120 175 L120 279 L101 282 L94 184 L86 184 L79 282 L60 279 L60 175 L53 160Z"/><path d="M72 61 Q90 68 108 61 L114 86 L66 86Z" class="loadout-art-seal"/><path d="M69 88 L111 88 L116 151 L90 163 L64 151Z M64 174 L80 179 L76 273 M116 174 L100 179 L104 273 M90 88 L90 161" class="loadout-art-detail"/><circle cx="90" cy="114" r="9" class="loadout-art-core"/></g>
            <g class="loadout-art-armor loadout-art-armor-exo"><path d="M54 80 L70 73 L110 73 L126 80 L133 115 L117 127 L117 163 L105 179 L75 179 L63 163 L63 127 L47 115Z"/><path d="M69 87 L111 87 L113 139 L90 151 L67 139Z M72 151 L108 151 L106 168 L74 168Z" class="loadout-art-plate"/><path d="M47 119 L36 176 L44 182 L57 133 M133 119 L144 176 L136 182 L123 133 M61 177 L49 223 L52 279 L63 279 L68 222 M119 177 L131 223 L128 279 L117 279 L112 222" class="loadout-art-exo-rail"/><circle cx="58" cy="179" r="7"/><circle cx="122" cy="179" r="7"/><circle cx="59" cy="222" r="7"/><circle cx="121" cy="222" r="7"/></g>
            <g class="loadout-art-vest"><path d="M66 84 L77 79 L103 79 L114 84 L117 145 L105 154 L75 154 L63 145Z"/><path d="M76 82 L76 153 M104 82 L104 153 M77 119 L103 119" class="loadout-art-detail"/></g>
            <g class="loadout-art-belt"><path d="M58 158 Q90 168 122 158 L122 171 Q90 180 58 171Z"/><path d="M84 158 L96 158 L96 175 L84 175Z" class="loadout-art-plate"/></g>
            <g class="loadout-art-headphones"><path class="loadout-art-headband" d="M67 36 Q68 7 90 7 Q112 7 113 36"/><path d="M62 33 L69 33 L69 45 L62 45Z M111 33 L118 33 L118 45 L111 45Z"/></g>
            <g class="loadout-art-mask"><path d="M67 30 Q67 10 90 9 Q113 10 113 30 L111 50 Q108 64 90 70 Q72 64 69 50Z"/><circle cx="79" cy="37" r="8" class="loadout-art-lens"/><circle cx="101" cy="37" r="8" class="loadout-art-lens"/><path d="M90 45 L90 52" class="loadout-art-detail"/><circle cx="90" cy="57" r="9" class="loadout-art-filter"/><circle cx="90" cy="57" r="4" class="loadout-art-filter-center"/></g>
            <g class="loadout-art-helmet loadout-art-helmet-open"><path d="M64 37 Q63 7 90 7 Q117 7 116 37 L111 42 L110 32 L70 32 L69 42Z"/><path d="M66 38 L76 44 M114 38 L104 44" class="loadout-art-detail"/></g>
            <g class="loadout-art-helmet loadout-art-helmet-visor"><path d="M62 35 Q62 6 90 6 Q118 6 118 35 L113 55 L105 63 L75 63 L67 55Z"/><path d="M70 29 L110 29 L107 47 L73 47Z" class="loadout-art-lens"/><path d="M78 53 L102 53" class="loadout-art-detail"/></g>
            <g class="loadout-art-helmet loadout-art-helmet-sealed"><path d="M61 36 Q61 5 90 5 Q119 5 119 36 L113 58 L102 68 L78 68 L67 58Z"/><path d="M69 28 L111 28 L106 44 L74 44Z" class="loadout-art-lens"/><circle cx="90" cy="53" r="7"/><path d="M77 51 L83 58 M103 51 L97 58" class="loadout-art-detail"/></g>
            <g class="loadout-art-helmet loadout-art-helmet-anomalous"><path d="M90 5 C108 5 118 18 118 36 C118 56 108 70 90 70 C72 70 62 56 62 36 C62 18 72 5 90 5Z"/><ellipse cx="90" cy="37" rx="24" ry="28" class="loadout-art-visor-frame"/><ellipse cx="90" cy="37" rx="21" ry="25" class="loadout-art-visor-glass"/><path d="M74 28 Q76 19 83 16" class="loadout-art-glass-highlight"/></g>
            <g class="loadout-art-glasses"><path d="M73 32 L87 32 L85 42 L75 42Z M93 32 L107 32 L105 42 L95 42Z M87 35 L93 35"/></g>
            <g class="loadout-art-gloves"><path d="M39 174 L52 177 L51 186 L47 192 L43 191 L38 183Z M128 177 L141 174 L142 183 L137 191 L133 192 L129 186Z"/><path d="M39 176 L52 180 M128 180 L141 176" class="loadout-art-detail"/></g>
            <g class="loadout-art-detector"><path d="M45 115 L57 117 L55 140 L43 138Z"/><circle cx="49" cy="124" r="3" class="loadout-art-core"/></g>
            <g class="loadout-art-necklace"><path d="M75 69 Q90 89 105 69"/><path d="M90 87 L85 94 L90 101 L95 94Z"/></g>
            <g class="loadout-art-earrings"><circle cx="68" cy="51" r="3"/><circle cx="112" cy="51" r="3"/></g>
            <g class="loadout-art-bracelet1"><path d="M39 169 L53 172 L52 177 L39 174Z"/></g>
            <g class="loadout-art-bracelet2"><path d="M127 172 L141 169 L141 174 L128 177Z"/></g>
            <g class="loadout-art-ring"><path d="M43 186 L50 188 L49 191 L42 189Z"/></g>
        </svg>
    </div>`;
}

function renderEquipmentLoadout(container, data, weaponTemplates, helmetTemplates, gasMaskTemplates, armorTemplates) {
    const groups = [...container.querySelectorAll(':scope > .equipment-group[data-equipment-group]')];
    if (groups.length < 7) return;
    const equipment = data.equipment || {};
    const weapons = data.weapons || [];
    const weaponEntries = weapons.map((item, index) => ({
        item, index,
        template: weaponTemplates.find(template => template.id == item.templateId),
    }));
    const melee = weaponEntries.filter(entry => entry.template?.category === 'melee_weapon' || entry.item.category === 'melee_weapon');
    const ranged = weaponEntries.filter(entry => !melee.includes(entry));
    const slot = (label, item, panel, code, kind = 'cosmetic', template = null, status = '') => {
        const tooltipHtml = item ? buildItemTooltipHtml(template, item) : '<span>Нажмите, чтобы открыть слот</span>';
        return `<button type="button" class="loadout-slot ${item ? 'is-equipped' : 'is-empty'}" data-sheet-control data-equipment-open="${escapeHtml(panel)}" aria-label="${escapeHtml(`${label}: ${item?.name || item?.model || 'пусто'}`)}">
            <span class="loadout-slot-mark" aria-hidden="true">${escapeHtml(code)}</span>
            <span class="loadout-slot-text"><span class="loadout-slot-label">${escapeHtml(label)}</span><strong>${escapeHtml(item?.name || item?.model || 'Пусто')}</strong>${status ? `<small>${escapeHtml(status)}</small>` : ''}</span>
            <span class="loadout-slot-tooltip" role="tooltip">${tooltipHtml}</span>
        </button>`;
    };
    const weaponSlot = (label, entry, code, panel) => slot(
        label, entry?.item, entry ? `weapon-${entry.index}` : panel,
        code, 'weapon', entry?.template,
        entry && !(entry.template?.category === 'melee_weapon' || entry.item.category === 'melee_weapon')
            ? `${getWeaponAmmoCount(entry.item)} патр.` : ''
    );
    const cosmetics = [
        ['Наушники', 'headphones', 'Н'], ['Очки', 'glasses', 'О'],
        ['Перчатки', 'gloves', 'П'], ['Кольцо', 'ring', 'К'],
        ['Амулет', 'necklace', 'А'], ['Серьги', 'earrings', 'С'],
        ['Браслет I', 'bracelet1', 'I'], ['Браслет II', 'bracelet2', 'II'],
    ];
    const extraWeapons = [...ranged.slice(2), ...melee.slice(1)];
    const statusLines = equipmentStatusLines(equipment, helmetTemplates, gasMaskTemplates);
    const filterStatus = item => {
        const filter = equipmentModule(item, 'filter');
        return filter ? `Фильтр ${filter.durability ?? filter.attributes?.durability ?? 0}/${filter.maxDurability ?? filter.attributes?.max_durability ?? '?'}` : '';
    };
    const exoskeletonBattery = equipmentModule(equipment.armor, 'exoskeleton_battery');
    container.insertAdjacentHTML('afterbegin', `<section class="equipment-loadout" aria-label="Экипировка персонажа">
        <div class="loadout-heading"><div><span class="loadout-eyebrow">Снаряжение</span><h4>Экипировка персонажа</h4></div><span class="loadout-hint">Наведите для характеристик · нажмите для действий</span></div>
        <div class="loadout-main">
            <div class="loadout-column">
                ${slot('Шлем', equipment.helmet?.templateId ? equipment.helmet : null, 'helmet', 'Ш', 'helmet', null,
                    equipment.helmet?.integratedWithArmor ? 'Встроен в броню' : filterStatus(equipment.helmet))}
                ${slot('Противогаз', equipment.gasMask?.templateId ? equipment.gasMask : null, 'gasMask', 'Г', 'gasMask', null, filterStatus(equipment.gasMask))}
                ${slot('Броня', equipment.armor?.templateId ? equipment.armor : null, 'armor', 'Б', 'armor', null,
                    exoskeletonBattery ? `${Number(exoskeletonBattery.attributes?.remaining_days) || 0} сут. заряда` : '')}
                ${slot('Детектор', equipment.detector?.templateId ? equipment.detector : null, 'detector', 'Д', 'detector', null,
                    equipmentModule(equipment.detector, 'battery') ? `${equipmentModule(equipment.detector, 'battery').attributes?.power ?? '?'}% заряда` : '')}
                ${slot('Пояс', equipment.belt?.templateId || equipment.belt?.model ? equipment.belt : null, 'inventory', 'П')}
            </div>
            ${renderEquipmentFigure(equipment, armorTemplates, helmetTemplates)}
            <div class="loadout-column">
                ${weaponSlot('Оружие I', ranged[0], 'I', 'weapons')}
                ${weaponSlot('Оружие II', ranged[1], 'II', 'weapons')}
                ${weaponSlot('Ближний бой', melee[0], 'ББ', 'weapon--1')}
                ${slot('Разгрузка', equipment.vest?.templateId || equipment.vest?.model ? equipment.vest : null, 'inventory', 'Р')}
                ${slot('Рюкзак', equipment.backpack?.templateId ? equipment.backpack : null, 'inventory', 'РК')}
            </div>
        </div>
        ${extraWeapons.length ? `<div class="loadout-extra"><span>Дополнительное оружие</span>${extraWeapons.map(entry => weaponSlot('Оружие', entry, 'О', 'weapons')).join('')}</div>` : ''}
        <div class="loadout-cosmetics"><span class="loadout-section-label">Косметика</span><div class="loadout-cosmetics-grid">${cosmetics.map(([label, key, code]) => slot(label, equipment[key]?.templateId ? equipment[key] : null, 'cosmetic', code)).join('')}</div></div>
        ${statusLines.length ? `<div class="loadout-resources">${statusLines.map(line => `<span>${escapeHtml(line)}</span>`).join('')}</div>` : ''}
    </section>`);

    const panelLabels = {
        weapons: 'Оружие и действия', helmet: 'Шлем', gasMask: 'Противогаз',
        armor: 'Броня', detector: 'Детектор', cosmetic: 'Косметика', pda: 'Модификации КПК',
    };
    groups.forEach(group => {
        const key = group.dataset.equipmentGroup;
        const details = document.createElement('details');
        details.className = 'equipment-detail';
        details.dataset.equipmentPanel = key;
        details.open = equipmentOpenPanels.has(key) || (key === 'weapons' && weapons.some(weapon => !weapon?.templateId && !weapon?.model));
        const summary = document.createElement('summary');
        summary.textContent = panelLabels[key];
        group.before(details);
        details.append(summary, group);
    });
    container.querySelectorAll('#weapons-container > [data-weapon-index]').forEach(card => {
        const key = `weapon-${card.dataset.weaponIndex}`;
        const details = document.createElement('details');
        details.className = 'loadout-weapon-details';
        details.dataset.equipmentPanel = key;
        const index = Number(card.dataset.weaponIndex);
        const weapon = index >= 0 ? weapons[index] : null;
        details.open = equipmentOpenPanels.has(key) || (index >= 0 && !weapon?.templateId && !weapon?.model);
        const summary = document.createElement('summary');
        summary.textContent = weapon
            ? `${weapon.name || 'Оружие'}${melee.some(entry => entry.index === index) ? '' : ` · ${getWeaponAmmoCount(weapon)} патр.`}`
            : 'Кулаки';
        card.before(details);
        details.append(summary, card);
    });
    container.querySelectorAll('[data-equipment-open]').forEach(button => {
        button.addEventListener('click', () => {
            const key = button.dataset.equipmentOpen;
            if (key === 'inventory') {
                switchSheetTab('inventory');
                document.getElementById('sheet-tab-inventory')?.scrollTo({ top: 0, behavior: 'smooth' });
                return;
            }
            const weaponPanel = key.startsWith('weapon-')
                ? container.querySelector(`[data-equipment-panel="${key}"]`) : null;
            const panel = container.querySelector(`[data-equipment-panel="${weaponPanel ? 'weapons' : key}"]`);
            if (!panel) return;
            panel.open = true;
            if (weaponPanel) weaponPanel.open = true;
            (weaponPanel || panel).scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        });
    });
}

/**
 * Рекурсивно отрендерить слоты предмета и установленные модули.
 * @param {Object} item - предмет
 * @param {Array} itemPath - путь к предмету в данных (массив, например ['equipment','helmet'])
 * @param {number} depth - уровень вложенности (для отступов)
 * @returns {string} HTML
 */
function hasFilterSlot(item) {
    return getItemSlots(item).some(slot => slot?.type === 'filter')
        || (item?.installedModules || []).some(module => module?.slotType === 'filter');
}

function renderSlotsUniversal(item, itemPath, depth = 0) {
    if (!item) return '';
    const slots = getItemSlots(item);
    if (!slots.length) return '';

    const indent = depth * 20;
    let html = '';

    for (const slot of slots) {
        const installed = (item.installedModules || []).find(m => m.slotType === slot.type);

        if (installed) {
            let info = escapeHtml(installed.name);
            if (slot.type === 'filter') {
                const dur = installed.durability || 0;
                const maxDur = installed.maxDurability || 0;
                info = `${info} (прочность ${dur}/${maxDur})`;
            } else if (slot.type === 'nvg') {
                const acc = installed.attributes?.accuracy_penalty ?? installed.accuracy_penalty ?? 0;
                const aware = installed.attributes?.awareness_penalty ?? installed.awareness_penalty ?? 0;
                info = `${info} (точность ${acc}, вним. ${aware}`;
                const battery = (installed.installedModules || []).find(m => m.slotType === 'battery');
                if (battery) info += `, заряд ${battery.attributes?.power ?? '?'}%`;
                info += `)`;
            } else if (slot.type === 'visor') {
                const acc = installed.attributes?.accuracy_penalty ?? installed.accuracy_penalty ?? 0;
                const erg = installed.attributes?.ergonomics_penalty ?? installed.ergonomics_penalty ?? 0;
                const cha = installed.attributes?.charisma_penalty ?? installed.charisma_penalty ?? 0;
                const prot = installed.protection?.physical ?? 0;
                const dur = installed.durability ?? 0;
                const maxDur = installed.maxDurability ?? 0;
                info = `${info} (точность ${acc}, эргон. ${erg}, харизма ${cha}, прочность ${dur}/${maxDur}, физ. защита ${formatProtectionPercent(prot)})`;
            } else if (slot.type === 'battery') {
                const power = installed.attributes?.power;
                info = `${info} (заряд ${power !== undefined ? power : '?'}%)`;
            } else if (slot.type === 'exoskeleton_battery') {
                const days = Math.max(0, Number(installed.attributes?.remaining_days) || 0);
                info = days > 0 ? `${days} сут.` : 'Разряжен';
            } else if (slot.type === 'armor_plate') {
                const prot = installed.attributes?.protection?.physical ?? installed.protection?.physical ?? 0;
                const dur = installed.durability ?? 0;
                const maxDur = installed.maxDurability ?? 0;
                const stage = installed.stage || 1;
                const stageNames = ['1. Целая','2. Немного повреждена','3. Повреждена','4. Сильно повреждена','5. Поломана'];
                const stageText = stageNames[stage-1] || stage;
                const currentStageDur = installed.currentStageDurability ?? installed.stageDurability ?? 0;
                const maxStageDur = installed.stageDurability ?? 0;
                info = `${info} (Физ. защита ${formatProtectionPercent(prot)}. Стадия ${stageText}. Прочность стадии ${currentStageDur}/${maxStageDur})`;
            }

            const uninstallBtn = `<button type="button" class="btn btn-sm btn-danger" onclick="window.uninstallModuleFromSlot('${JSON.stringify(itemPath).replace(/"/g, '&quot;')}', '${slot.type}')">Снять</button>`;
            const configBtn = (slot.type === 'visor') ? `<button type="button" class="btn btn-sm btn-secondary" onclick="openVisorModificationsModal('${JSON.stringify(itemPath).replace(/"/g, '&quot;')}', '${slot.type}')">⚙️</button>` : '';

            html += `
                <div style="margin-left:${indent}px; display:flex; align-items:center; gap:10px; margin-top:5px;">
                    <span style="width:100px;">${slot.label}:</span>
                    <span style="flex:1;">${info}</span>
                    ${configBtn}
                    ${uninstallBtn}
                </div>
            `;

            const installedIndex = (item.installedModules || []).findIndex(m => m.slotType === slot.type);
            const subPath = itemPath.concat(['installedModules', installedIndex]);
            html += renderSlotsUniversal(installed, subPath, depth + 1);
        } else {
            const installBtn = `<button type="button" class="btn btn-sm btn-primary" onclick="window.installModuleFromSlot('${JSON.stringify(itemPath).replace(/"/g, '&quot;')}', '${slot.type}')">Установить</button>`;
            html += `
                <div style="margin-left:${indent}px; display:flex; align-items:center; gap:10px; margin-top:5px;">
                    <span style="width:100px;">${slot.label}:</span>
                    ${installBtn}
                </div>
            `;
        }
    }
    return `<div class="item-slots-container" data-widget-slots="${escapeHtml(itemPath.join('.'))}">${html}</div>`;
}

function renderBeltPouches(pouches, pouchTemplates) {
    if (!pouches || pouches.length === 0) return '<p>Нет подсумков</p>';
    let html = '';
    pouches.forEach((pouch, index) => {
        const options = pouchTemplates.map(t =>
            `<option value="${t.id}" ${pouch.type === t.id ? 'selected' : ''}>${t.name} (объём ${t.volume || 0})</option>`
        ).join('');
        html += `
            <div style="display: flex; gap: 10px; margin-bottom: 5px; align-items: center; flex-wrap: wrap;">
                <select name="equipment.belt.pouches.${index}.type" class="form-control" style="width: 150px;">
                    <option value="">-- Выберите подсумок --</option>
                    ${options}
                </select>
                <input type="number" class="form-control number-input" name="equipment.belt.pouches.${index}.capacity" value="${pouch.capacity || 0}" placeholder="Объём" style="width: 80px;">
                <input type="text" class="form-control" name="equipment.belt.pouches.${index}.contents" value="${escapeHtml(pouch.contents || '')}" placeholder="Содержимое" style="flex:1;">
                <button type="button" class="btn btn-sm btn-danger" onclick="removeBeltPouch(${index})">✕</button>
            </div>
        `;
    });
    return html;
}

function renderVestPouches(pouches, pouchTemplates, isBase, totalCapacity) {
    if (!pouches || pouches.length === 0) return '<p>Нет подсумков</p>';
    let usedCapacity = 0;
    let html = '';
    pouches.forEach((pouch, index) => {
        const pouchTemplate = pouchTemplates.find(t => t.id === pouch.type);
        const pouchVolume = pouchTemplate?.volume || pouch.capacity || 0;
        usedCapacity += pouchVolume;
        const options = pouchTemplates.map(t =>
            `<option value="${t.id}" ${pouch.type === t.id ? 'selected' : ''}>${t.name} (объём ${t.volume || 0})</option>`
        ).join('');
        html += `
            <div style="display: flex; gap: 10px; margin-bottom: 5px; align-items: center; flex-wrap: wrap;">
                <select name="equipment.vest.pouches.${index}.type" class="form-control" style="width: 150px;" ${!isBase ? 'disabled' : ''}>
                    <option value="">-- Выберите подсумок --</option>
                    ${options}
                </select>
                <input type="number" class="form-control number-input" name="equipment.vest.pouches.${index}.capacity" value="${pouch.capacity || pouchVolume}" placeholder="Объём" style="width: 80px;" ${!isBase ? 'disabled' : ''}>
                <input type="text" class="form-control" name="equipment.vest.pouches.${index}.contents" value="${escapeHtml(pouch.contents || '')}" placeholder="Содержимое" style="flex:1;" ${!isBase ? 'disabled' : ''}>
                ${isBase ? `<button type="button" class="btn btn-sm btn-danger" onclick="removeVestPouch(${index})">✕</button>` : ''}
            </div>
        `;
    });
    if (isBase) {
        const remaining = totalCapacity - usedCapacity;
        const remainingColor = remaining < 0 ? 'red' : 'inherit';
        html += `<div style="margin-top: 5px; color: ${remainingColor};">Использовано: ${usedCapacity} / ${totalCapacity} (осталось: ${remaining})</div>`;
    }
    return html;
}

function renderBeltModifications(mods, modTemplates) {
    if (!mods || mods.length === 0) return '<p>Нет модификаций</p>';
    let html = '';
    mods.forEach((mod, index) => {
        const options = modTemplates.map(t =>
            `<option value="${t.id}" ${mod.name === t.name ? 'selected' : ''}>${t.name}</option>`
        ).join('');
        html += `
            <div style="display: flex; gap: 5px; margin-bottom: 5px; align-items: center;">
                <select name="equipment.belt.modifications.${index}.name" class="form-control" style="width: 200px;">
                    <option value="">-- Выберите модификацию --</option>
                    ${options}
                </select>
                <input type="text" class="form-control" name="equipment.belt.modifications.${index}.description" value="${escapeHtml(mod.description || '')}" placeholder="Описание" style="flex:1;">
                <button type="button" class="btn btn-sm btn-danger" onclick="removeBeltModification(${index})">✕</button>
            </div>
        `;
    });
    return html;
}

function renderHelmetModifications(mods, groupedTemplates) {
    if (!mods || mods.length === 0) return '';
    let html = '';
    mods.forEach((mod, index) => {
        const options = Object.entries(groupedTemplates).map(([cat, items]) => `
            <optgroup label="${cat}">
                ${items.map(t => `<option value="${t.id}" ${mod.name === t.name ? 'selected' : ''}>${t.name}</option>`).join('')}
            </optgroup>
        `).join('');
        html += `
            <div style="display: flex; gap: 5px; margin-bottom: 5px; align-items: center;">
                <select name="equipment.helmet.modifications.${index}.name" class="form-control" style="width: 200px;">
                    <option value="">-- Выберите модификацию --</option>
                    ${options}
                </select>
                <input type="text" class="form-control" name="equipment.helmet.modifications.${index}.description" value="${escapeHtml(mod.description || '')}" placeholder="Описание" style="flex:1;">
                <button type="button" class="btn btn-sm btn-danger" onclick="removeHelmetModification(${index})">✕</button>
            </div>
        `;
    });
    return html;
}

function renderGasMaskModifications(mods, groupedTemplates) {
    if (!mods || mods.length === 0) return '';
    let html = '';
    mods.forEach((mod, index) => {
        const options = Object.entries(groupedTemplates).map(([cat, items]) => `
            <optgroup label="${cat}">
                ${items.map(t => `<option value="${t.id}" ${mod.name === t.name ? 'selected' : ''}>${t.name}</option>`).join('')}
            </optgroup>
        `).join('');
        html += `
            <div style="display: flex; gap: 5px; margin-bottom: 5px; align-items: center;">
                <select name="equipment.gasMask.modifications.${index}.name" class="form-control" style="width: 200px;">
                    <option value="">-- Выберите модификацию --</option>
                    ${options}
                </select>
                <input type="text" class="form-control" name="equipment.gasMask.modifications.${index}.description" value="${escapeHtml(mod.description || '')}" placeholder="Описание" style="flex:1;">
                <button type="button" class="btn btn-sm btn-danger" onclick="removeGasMaskModification(${index})">✕</button>
            </div>
        `;
    });
    return html;
}

function renderArmorModifications(mods, groupedTemplates) {
    if (!mods || mods.length === 0) return '';
    let html = '';
    mods.forEach((mod, index) => {
        const options = Object.entries(groupedTemplates).map(([cat, items]) => `
            <optgroup label="${cat}">
                ${items.map(t => `<option value="${t.id}" ${mod.name === t.name ? 'selected' : ''}>${t.name}</option>`).join('')}
            </optgroup>
        `).join('');
        html += `
            <div style="display: flex; gap: 5px; margin-bottom: 5px; align-items: center;">
                <select name="equipment.armor.modifications.${index}.name" class="form-control" style="width: 200px;">
                    <option value="">-- Выберите модификацию --</option>
                    ${options}
                </select>
                <input type="text" class="form-control" name="equipment.armor.modifications.${index}.description" value="${escapeHtml(mod.description || '')}" placeholder="Описание" style="flex:1;">
                <button type="button" class="btn btn-sm btn-danger" onclick="removeArmorModification(${index})">✕</button>
            </div>
        `;
    });
    return html;
}

function renderArmorContainers(containers) {
    if (!containers.length) return '<p>Нет контейнеров</p>';
    let html = '';
    containers.forEach((cont, idx) => {
        html += `
            <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 5px;">
                <input type="text" class="form-control" name="equipment.armor.containers.${idx}.effect"
                       value="${escapeHtml(cont.effect || '')}" placeholder="Содержимое" style="flex: 1;">
                <button type="button" class="btn btn-sm btn-danger" onclick="removeArmorContainer(${idx})">✕</button>
            </div>
        `;
    });
    return html;
}

function renderPdaModifications(items, groupedTemplates) {
    if (!items || items.length === 0) return '';
    let html = '';
    items.forEach((item, index) => {
        const options = Object.entries(groupedTemplates).map(([cat, items]) => `
            <optgroup label="${cat}">
                ${items.map(t => `<option value="${t.id}" ${item.name === t.name ? 'selected' : ''}>${t.name}</option>`).join('')}
            </optgroup>
        `).join('');
        html += `
            <div style="display: flex; gap: 5px; margin-bottom: 5px; align-items: center;">
                <select name="modifications.pda.items.${index}.name" class="form-control" style="width: 200px;">
                    <option value="">-- Выберите модификацию --</option>
                    ${options}
                </select>
                <input type="text" class="form-control" name="modifications.pda.items.${index}.effect" value="${escapeHtml(item.effect || '')}" placeholder="Эффект" style="flex:1;">
                <button type="button" class="btn btn-sm btn-danger" onclick="removePdaItem(${index})">✕</button>
            </div>
        `;
    });
    return html;
}

function getWeaponFireProfile(weapon, template) {
    const stored = weapon.fireModes || weapon.attributes?.fire_modes || template?.attributes?.fire_modes;
    if (stored) return stored;

    const raw = String(weapon.burst || template?.attributes?.burst || '').trim();
    const lower = raw.toLowerCase();
    const duplexMatch = lower.match(/одиночн\w*\s*-\s*(\d+)/);
    const burstMatch = lower.match(/очеред\w*\s*(\d+)/);
    const plainBurstMatch = lower.match(/^\s*(\d+)/);
    const duplexSize = duplexMatch ? Number(duplexMatch[1]) : null;
    const burstSize = burstMatch
        ? Number(burstMatch[1])
        : (!duplexSize && plainBurstMatch ? Number(plainBurstMatch[1]) : null);
    const machineGunBurst = lower.includes('пулеметн') || lower.includes('пулемётн');
    const supportsBurst = Boolean(burstSize || machineGunBurst);
    return {
        raw,
        single_shot_options: duplexSize ? [1, duplexSize] : [1],
        duplex_size: duplexSize,
        burst_size: burstSize,
        machine_gun_burst: machineGunBurst,
        supports_burst: supportsBurst,
        supports_suppression: supportsBurst,
        supports_area_fire: supportsBurst,
    };
}

function getWeaponClass(weapon, template = null) {
    const raw = String(
        template?.subcategory
        || weapon?.subcategory
        || weapon?.attributes?.subcategory
        || ''
    ).trim().toLowerCase().replaceAll('ё', 'е');
    if (raw.includes('пистолет') && raw.includes('пулемет')) return 'submachine_gun';
    if (raw.includes('пистолет')) return 'pistol';
    if (raw.includes('дробовик')) return 'shotgun';
    if (raw.includes('снайпер')) return 'sniper_rifle';
    if (raw.includes('пулемет')) return 'machine_gun';
    if (raw.includes('гранатомет')) return 'grenade_launcher';
    return 'other';
}

function getWeaponAmmoCount(weapon) {
    if (Array.isArray(weapon.installedMagazine?.ammo)) {
        return weapon.installedMagazine.ammo.reduce(
            (sum, stack) => sum + (Number(stack.quantity) || 0),
            0
        );
    }
    if (Array.isArray(weapon.fixedAmmo) && weapon.fixedAmmo.length) {
        return weapon.fixedAmmo.reduce((sum, stack) => sum + (Number(stack.quantity) || 0), 0);
    }
    return Math.max(0, Number(weapon.ammo) || 0);
}

function getManualCycleType(weapon, template = null) {
    const explicit = weapon?.manualCycle
        || weapon?.attributes?.manual_cycle
        || template?.attributes?.manual_cycle;
    if (explicit) return explicit;
    const name = String(weapon?.name || template?.name || '').toLowerCase();
    if (
        ['суслик', 'малинова', 'мачеха 51', 'свет-99', 'пылесос'].some(value => name.includes(value))
        || /(?:^|\s)ау(?:\s|$)/i.test(name)
    ) {
        return 'bolt';
    }
    if (
        ['гора б88', 'гора 580б2', 'ремень 787', 'спаситель 70', 'д-2', 'д2']
            .some(value => name.includes(value))
    ) {
        return 'pump';
    }
    return null;
}

function getCombatWeaponErgonomics(weaponIndex) {
    const current = window.locationCombatState?.current_character;
    if (!current || current.character_id !== currentCharacterId) return null;
    return (current.weapon_ergonomics || []).find(
        (profile) => Number(profile.weapon_index) === Number(weaponIndex)
    ) || null;
}

function isSelectedWeaponIndex(value, weaponIndex) {
    return value !== null
        && value !== undefined
        && Number(value) === Number(weaponIndex);
}

function secondaryWeaponIndex() {
    if (window.locationCombatState?.status==='active') {
        const actor=window.locationCombatState.characters?.find(c=>c.character_id===currentCharacterId);
        return actor?.secondary_weapon_index ?? null;
    }
    const identity=currentCharacterData?.health?.combatMeta?.secondaryWeaponId;
    const index=identity?(currentCharacterData.weapons || []).findIndex(weapon=>weapon.id===identity):-1;
    return index>=0 && !isSelectedWeaponIndex(currentCharacterData.activeWeaponIndex,index)?index:null;
}

function isHeldWeaponIndex(value,index) {
    return isSelectedWeaponIndex(value,index) || secondaryWeaponIndex()===index;
}

window.changeWeaponPair = async function(operation,weaponIndex) {
    if (medicalOperationInProgress || !currentCharacterCanEdit) return;
    const characterId=currentCharacterId;let locked=false,pending=false;
    try {
        if (autoSaveTimer) await forceSyncCharacter();
        await Server.waitForCharacterSave(characterId);
        if (currentCharacterId!==characterId || medicalOperationInProgress) return;
        if (sheetSaveBlocked) throw new Error('Переоткройте лист перед сменой оружия');
        medicalOperationInProgress=locked=true;medicalRemoteUpdate=null;
        const state=window.locationCombatState;
        if (state?.status==='active' && operation==='take') {
            const result=await Server.performLocationCombatAction(window.currentLobbyId,window.currentLocationId,{
                location_character_id:state.current_character.location_character_id,action_key:'draw_weapon',weapon_index:weaponIndex,
                attribute_choice:'second_hand',pending_action_id:`draw-second-${state.current_character.location_character_id}-${Date.now()}`});
            pending=Boolean(result.pending_action);
        } else {
            const result=await Server.changeWeaponPair(characterId,{operation,weapon_index:weaponIndex,character_revision:currentCharacterData._revision});
            currentCharacterData=result.data;
        }
        showNotification(pending?'Доставание второго оружия начато и ждёт доплаты ОД':operation==='take'?'Оружие взято во вторую руку':'Вторая рука освобождена',pending?'system':'success');
    } catch(error) { showNotification(error.message || 'Не удалось изменить пару оружия','system'); }
    finally {
        if (locked) {
            if (medicalRemoteUpdate?.character_id===Number(characterId) && Number(medicalRemoteUpdate.data?._revision)>Number(currentCharacterData?._revision)) currentCharacterData=medicalRemoteUpdate.data;
            medicalRemoteUpdate=null;medicalOperationInProgress=false;await refreshSavedSheet(characterId);
        }
    }
};

function formatActionPointModifier(value) {
    const numeric = Number(value) || 0;
    if (numeric === 0) return 'без изменений';
    return `${numeric > 0 ? '+' : ''}${numeric} ОД`;
}

window.openProsthesisSurgery = async function(characterId) {
    if(medicalOperationInProgress)return;
    if(autoSaveTimer)await forceSyncCharacter();
    await Server.waitForCharacterSave(characterId);
    closeCharacterSheet();
    const scene=await import('./locationScene.js');
    await scene.showMedicalConsumableMenu(characterId,characterId);
};

window.operateProsthesis = async function(payload) {
    if(medicalOperationInProgress || !currentCharacterCanEdit)return;
    const characterId=currentCharacterId;let locked=false;
    try {
        if(autoSaveTimer)await forceSyncCharacter();
        await Server.waitForCharacterSave(characterId);
        if(currentCharacterId!==characterId || medicalOperationInProgress)return;
        if(sheetSaveBlocked)throw new Error('Переоткройте лист перед действием с протезом');
        medicalOperationInProgress=locked=true;medicalRemoteUpdate=null;
        const state=window.locationCombatState;
        if(state?.status==='active'){
            if(!['install','remove'].includes(payload.operation))throw new Error('Обслуживание выполняется вне боя');
            const result=await Server.performLocationCombatAction(window.currentLobbyId,window.currentLocationId,{
                location_character_id:state.current_character.location_character_id,action_key:'prosthesis',
                equipment_operation:payload.operation,equipment_slot:payload.zone,item_path:payload.path,
                pending_action_id:`prosthesis-${characterId}-${Date.now()}`});
            showNotification(result.pending_action?'Установка начата и ждёт доплаты ОД':'Протез обновлён','success');
        }else{
            const result=await Server.operateProsthesis(characterId,{...payload,character_revision:currentCharacterData._revision});
            currentCharacterData=result.data;
            showNotification(payload.operation==='lubricate'?'Смазка начата: 5 минут игрового времени':'Протез обновлён','success');
        }
    }catch(error){showNotification(error.message || 'Не удалось выполнить действие с протезом','system');}
    finally{if(locked){
        if(medicalRemoteUpdate?.character_id===Number(characterId)&&Number(medicalRemoteUpdate.data?._revision)>Number(currentCharacterData?._revision))currentCharacterData=medicalRemoteUpdate.data;
        medicalOperationInProgress=false;medicalRemoteUpdate=null;await refreshSavedSheet(characterId);
    }}
};

window.operatePortableLight = async function(payload) {
    if (medicalOperationInProgress || !currentCharacterCanEdit) return;
    const characterId=currentCharacterId;let locked=false;
    try {
        if (autoSaveTimer) await forceSyncCharacter();
        await Server.waitForCharacterSave(characterId);
        if (currentCharacterId!==characterId || medicalOperationInProgress) return;
        if (sheetSaveBlocked) throw new Error('Переоткройте лист перед управлением фонарём');
        medicalOperationInProgress=locked=true;medicalRemoteUpdate=null;
        const result=await Server.operatePortableLight(characterId,{...payload,character_revision:currentCharacterData._revision});
        currentCharacterData=result.data;showNotification('Состояние фонаря обновлено','success');
    } catch (error) { showNotification(error.message || 'Не удалось изменить состояние фонаря','system'); }
    finally {
        if (locked) {
            if (medicalRemoteUpdate?.character_id===Number(characterId) && Number(medicalRemoteUpdate.data?._revision)>Number(currentCharacterData?._revision)) currentCharacterData=medicalRemoteUpdate.data;
            medicalRemoteUpdate=null;medicalOperationInProgress=false;await refreshSavedSheet(characterId);
        }
    }
};

window.changeWeaponGrip = async function(weaponIndex, hands, hand) {
    if (medicalOperationInProgress) return;
    const characterId = currentCharacterId;
    let locked = false;
    try {
        if (autoSaveTimer) await forceSyncCharacter();
        await Server.waitForCharacterSave(characterId);
        if (currentCharacterId !== characterId || medicalOperationInProgress) return;
        if (sheetSaveBlocked) throw new Error('Переоткройте лист перед сменой хвата');
        medicalOperationInProgress = locked = true;
        medicalRemoteUpdate = null;
        const result = await Server.changeWeaponGrip(characterId, {hands, ...(hand?{hand}:{}), weapon_index:weaponIndex, character_revision:currentCharacterData._revision});
        currentCharacterData = result.data;
        showNotification(hands === 1 ? `Оружие в ${hand==='left'?'левой':'правой'} руке` : 'Оружие удерживается двумя руками', 'success');
    } catch (error) { showNotification(error.message || 'Не удалось сменить хват', 'system'); }
    finally {
        if (locked) {
            if (medicalRemoteUpdate?.character_id === Number(characterId) && Number(medicalRemoteUpdate.data?._revision) > Number(currentCharacterData?._revision)) currentCharacterData = medicalRemoteUpdate.data;
            medicalRemoteUpdate = null; medicalOperationInProgress = false;
            await refreshSavedSheet(characterId);
        }
    }
};

function renderWeaponGripControl(weapon, index) {
    if (weapon.naturalWeapon || weapon.attributes?.natural_weapon) return '';
    const state = window.locationCombatState;
    const active = state?.status === 'active';
    const actor = state?.characters?.find(c => c.character_id === currentCharacterId)
        || (state?.current_character?.character_id === currentCharacterId ? state.current_character : null);
    const selected = active ? actor?.drawn_weapon_index : currentCharacterData?.activeWeaponIndex;
    const hands = currentCharacterData?.health?.combatMeta?.weaponGripHands === 1 ? 1 : 2;
    const secondary=secondaryWeaponIndex();
    const disabled = !currentCharacterCanEdit || active && state.current_character?.character_id !== currentCharacterId;
    const side=currentCharacterData?.health?.combatMeta?.weaponPrimaryHand==='left'?'left':'right';
    if (secondary===index) return `<span>В ${side==='left'?'правой':'левой'} руке</span><button type="button" class="btn btn-sm btn-secondary" ${disabled?'disabled':''} onclick="changeWeaponPair('stow',${index})">Убрать второе оружие</button>`;
    if (!isSelectedWeaponIndex(selected, index)) {
        const cost=active?` · ${getCombatWeaponErgonomics(index)?.draw_action_points??4} ОД`:'';
        const choices=[['right','Взять в правую руку'],['left','Взять в левую руку'],['both','Взять двумя руками']].map(([hand,label])=>
            `<button type="button" data-weapon-draw-hand="${hand}" data-weapon-draw-index="${index}" class="btn btn-sm btn-secondary" ${disabled?'disabled':''} onclick="drawWeaponFromEquipment(${index},'${hand}')">${label}${cost}</button>`).join('');
        return (selected!=null && hands===1 && secondary==null
            ? `<button type="button" data-weapon-pair-take="${index}" class="btn btn-sm btn-secondary" ${disabled?'disabled':''} onclick="changeWeaponPair('take',${index})">Взять во вторую руку</button>` : '')+choices;
    }
    if (secondary!=null) return '<span>По оружию в каждой руке</span>';
    return `<span data-weapon-grip-label="${index}">Хват: ${hands === 1 ? `${side==='left'?'левая':'правая'} рука` : 'двумя руками'}</span>`
        + [['right','Взять в правую руку'],['left','Взять в левую руку'],['both','Взять двумя руками']].map(([hand,label])=>
            `<button type="button" data-weapon-grip-hand="${hand}" ${hand===(hands===2?'right':'both')?`data-weapon-grip-change="${index}"`:''} class="btn btn-sm btn-secondary" ${disabled || (hand==='both'?hands===2:hands===1&&side===hand)?'disabled':''} onclick="changeWeaponGrip(${index},${hand==='both'?2:1},'${hand==='both'?side:hand}')">${label}${active?' · 1 ОД':''}</button>`).join('');
}

window.drawWeaponFromEquipment = async function(weaponIndex,hand) {
    if(medicalOperationInProgress||!currentCharacterCanEdit)return;
    const characterId=currentCharacterId;let locked=false;
    try {
        if(autoSaveTimer)await forceSyncCharacter();
        await Server.waitForCharacterSave(characterId);
        if(currentCharacterId!==characterId||medicalOperationInProgress)return;
        if(sheetSaveBlocked)throw new Error('Переоткройте лист перед доставанием оружия');
        medicalOperationInProgress=locked=true;medicalRemoteUpdate=null;
        const combatState=window.locationCombatState,actor=combatState?.current_character;
        if(combatState?.status!=='active'){
            const result=await Server.changeWeaponGrip(characterId,{draw:true,weapon_index:weaponIndex,
                hands:hand==='both'?2:hand?1:currentCharacterData.health?.combatMeta?.weaponGripHands===1?1:2,
                ...(hand?{hand:hand==='both'?'right':hand}:{}),character_revision:currentCharacterData._revision});
            currentCharacterData=result.data;showNotification('Оружие взято в руки','success');return;
        }
        if(actor?.character_id!==characterId)throw new Error('Достать оружие можно только в свой ход');
        const result = await Server.performLocationCombatAction(window.currentLobbyId, window.currentLocationId, {
            location_character_id: actor.location_character_id,
            action_key: 'draw_weapon',
            weapon_index: weaponIndex,
            ...(hand?{attribute_choice:hand==='both'?'both_hands':hand+'_hand'}:{}),
            pending_action_id: `draw-weapon-${actor.location_character_id}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
        });
        if (result?.pending_action) {
            showNotification('Доставание оружия начато. Оно окажется в руках после полной оплаты ОД.', 'system');
            return;
        }
        showNotification('Оружие подготовлено', 'success');
    } catch (error) {
        showNotification(error.message || 'Не удалось достать оружие', 'system');
    }
    finally{if(locked){
        if(medicalRemoteUpdate?.character_id===Number(characterId)&&Number(medicalRemoteUpdate.data?._revision)>Number(currentCharacterData?._revision))currentCharacterData=medicalRemoteUpdate.data;
        medicalRemoteUpdate=null;medicalOperationInProgress=false;await refreshSavedSheet(characterId);
    }}
};

window.rebreakUnfixedFracture = function(index) {
    updateDataFromFields();
    const health = currentCharacterData.health || (currentCharacterData.health = {});
    const effects = normalizeEffectList(health.effects || []);
    const fracture = effects[index];
    if (!fracture || fracture.type !== 'fracture_unfixed') return;
    const medicine = currentCharacterData.skills?.physical?.medicine || {};
    const medicineValue = Math.max(0, Number(medicine.base ?? medicine.value ?? 5) + Number(medicine.bonus || 0));
    const penaltyRemainsChance = Math.max(0, Math.min(100, 100 - 4 * medicineValue));
    const hadPermanentPenalty = Boolean(
        fracture.permanent_penalty
        || effects.some(effect => effect.type === 'fracture_sequela' && effect.area === fracture.area)
    );
    const roll = hadPermanentPenalty ? 1 + Math.floor(Math.random() * 100) : null;
    const penaltyRemains = hadPermanentPenalty && roll <= penaltyRemainsChance;
    health.effects = effects.filter((effect, effectIndex) => !(
        effectIndex === index
        || (effect.type === 'fracture_sequela' && effect.area === fracture.area)
    ));
    applyEffectToHealth(health, {
        type: 'fracture', name: 'Перелом', area: fracture.area,
        source: 'rebroken_fracture', tick: 'manual',
        regular_fixation_seconds: 1800, hinged_fixation_seconds: 1800,
    });
    if (penaltyRemains) {
        applyEffectToHealth(health, {
            type: 'fracture_sequela', name: 'Постоянный штраф после перелома',
            area: fracture.area, source: 'rebroken_fracture', tick: 'manual',
        });
    }
    applyEffectToHealth(health, {
        type: 'pain', value: 3, area: fracture.area, source: 'rebroken_fracture',
    });
    refreshHealthPanel();
    scheduleAutoSave();
    const resultMessage = hadPermanentPenalty
        ? `Повторный перелом выполнен. Постоянный штраф был: d100 ${roll} при шансе сохранения ${penaltyRemainsChance}%. ${penaltyRemains ? 'Штраф сохранился.' : 'Штраф устранён.'}`
        : 'Повторный перелом выполнен. Постоянного штрафа до процедуры не было; конечность снова имеет обычный перелом.';
    showNotification(resultMessage, penaltyRemains ? 'system' : 'success');
};

window.cycleWeaponFromEquipment = async function(weaponIndex) {
    if (secondaryWeaponIndex()!=null) {showNotification('Для затвора или помпы сначала освободите руку','system');return;}
    const weapon = currentCharacterData?.weapons?.[weaponIndex];
    if (!weapon?.requiresManualCycle) return;
    const readyIndex = window.locationCombatState?.status === 'active'
        ? window.locationCombatState?.current_character?.drawn_weapon_index
        : currentCharacterData?.activeWeaponIndex;
    if (!isHeldWeaponIndex(readyIndex, weaponIndex)) {
        showNotification('Сначала возьмите это оружие в руки', 'system');
        return;
    }
    const template = (allTemplatesCache || []).find(entry => entry.id == weapon.templateId);
    const cycleType = getManualCycleType(weapon, template);
    const specialization = weaponSpecializationKey(template);
    const level = currentCharacterData?.skills?.specialized?.[specialization]?.level || 'unfamiliar';
    const actionPoints = cycleType === 'pump' && level === 'professional' ? 0 : 1;
    try {
        const paid = await chooseAndSpendCombatPayment(
            cycleType === 'pump' ? 'Дослать патрон' : 'Передёрнуть затвор',
            [[{ actionPoints, freeActions: 0 }]]
        );
        if (!paid) return;
    } catch (error) {
        showNotification(error.message || 'Не хватает ОД', 'system');
        return;
    }
    weapon.requiresManualCycle = false;
    renderEquipmentTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification(cycleType === 'pump' ? 'Патрон дослан в патронник' : 'Затвор передёрнут', 'success');
};

function removeClearedWeaponJamLocally(weaponIndex) {
    const weapon = currentCharacterData?.weapons?.[weaponIndex];
    if (!weapon) return;

    if (Array.isArray(weapon.jams) && weapon.jams.length) {
        weapon.jams.pop();
        if (weapon.jams.length) weapon.jam = weapon.jams[weapon.jams.length - 1];
        else {
            delete weapon.jams;
            delete weapon.jam;
        }
        return;
    }
    delete weapon.jam;
    delete weapon.jams;
}

window.clearWeaponJam = async function(weaponIndex, options = {}) {
    const weapon = currentCharacterData?.weapons?.[weaponIndex];
    const jamLabel = weapon?.jam?.label || 'оружие снова готово';
    const combatState = window.locationCombatState;
    const actor = combatState?.current_character;
    if (!weapon?.jam || combatState?.status !== 'active' || !actor) return;
    if (actor.character_id !== currentCharacterId) {
        showNotification('Устранить клин можно только в свой ход', 'system');
        return;
    }
    const actionId = options.actionId
        || `clear-jam-${actor.location_character_id}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const payload = {
        location_character_id: actor.location_character_id,
        action_key: 'clear_weapon_jam',
        weapon_index: weaponIndex,
        ...(options.resume ? { resume_pending_action_id: actionId } : { pending_action_id: actionId }),
    };
    try {
        const result = await Server.performLocationCombatAction(
            window.currentLobbyId,
            window.currentLocationId,
            payload,
        );
        if (result?.pending_action) {
            pendingWeaponJamActions.set(result.pending_action_id, {
                characterId: currentCharacterId,
                weaponIndex,
            });
            showNotification('Устранение клина начато. Оставшиеся ОД спишутся в следующих ходах.', 'system');
            return;
        }
        removeClearedWeaponJamLocally(weaponIndex);
        await renderEquipmentTab(currentCharacterData);
        showNotification(`Клин устранён: ${jamLabel}`, 'success');
    } catch (error) {
        showNotification(error.message || 'Не удалось устранить клин', 'system');
    }
};

window.chooseMachineGunBurst = function(weaponIndex, fireMode, actionPoints = 3, volleyCount = 1) {
    const weapon = currentCharacterData?.weapons?.[weaponIndex];
    if (!weapon) return;
    const available = getWeaponAmmoCount(weapon);
    const minimum=backgroundIs(currentCharacterData,'Пост принял')?10:2;
    if (available < minimum) {
        showNotification(`Для пулемётной очереди нужно минимум ${minimum} патронов`);
        return;
    }
    const fireRate = Math.max(2, Number(weapon.fireRate) || available);
    const maximum = Math.min(Math.floor(available / volleyCount), fireRate);
    if (maximum < minimum) {
        showNotification(`Для действия нужно минимум ${minimum * volleyCount} патронов`);
        return;
    }
    const answer = window.prompt(`Длина очереди (${minimum}-${maximum})`, String(Math.min(Math.max(5,minimum), maximum)));
    if (answer === null) return;
    const shots = Number.parseInt(answer, 10);
    if (!Number.isInteger(shots) || shots < minimum || shots > maximum) {
        showNotification(`Укажите число от ${minimum} до ${maximum}`);
        return;
    }
    window.useWeaponFromEquipment(weaponIndex, fireMode, shots * volleyCount, actionPoints, volleyCount);
};

window.aimWeaponFromEquipment = function(weaponIndex) {
    const actorCharacterId = currentCharacterId;
    const combatState = window.locationCombatState;
    if (!combatState || combatState.status !== 'active') {
        showNotification('Прицеливание используется только в активном бою');
        return;
    }
    if (combatState.current_character?.character_id !== actorCharacterId) {
        showNotification('Сейчас не ход этого персонажа', 'system');
        return;
    }
    closeCharacterSheet();
    import('./locationScene.js').then((module) => {
        module.queueCombatActionFromSheet({
            actorCharacterId,
            actionKey: 'aim',
            weaponIndex,
            actionPoints: 1,
            targetType: 'character',
            source: 'sheet',
        });
    });
};

function renderRangedAttackButtons(weapon, template, index, disabled) {
    const profile = getWeaponFireProfile(weapon, template);
    const weaponClass = getWeaponClass(weapon, template);
    const disabledAttr = disabled ? 'disabled' : '';
    const jamBlocksFire = Boolean(weapon?.jam?.blocks_fire);
    const firingDisabledAttr = disabled || jamBlocksFire ? 'disabled' : '';
    const buttons = [];
    const ergonomics = getCombatWeaponErgonomics(index);
    const aimedActionPoints = ergonomics?.aimed_shot_action_points ?? 4;
    const combatState = window.locationCombatState;
    const drawnWeaponIndex = combatState?.current_character?.drawn_weapon_index;
    const persistentWeaponIndex = currentCharacterData?.activeWeaponIndex;
    const isCombatActive = combatState?.status === 'active';
    const weaponWeight = Number(weapon?.weight ?? template?.weight) || 0;
    const buttLabel = weaponWeight <= 1 ? 'Удар рукояткой' : 'Удар прикладом';
    const buttCost = weaponWeight <= 1 ? 2 : 3;
    const swingPrepared = combatState?.current_character?.melee_swing_round === combatState?.round_number;
    const buttButton = `
        <button type="button" class="btn btn-sm btn-primary" ${disabledAttr} onclick="useMeleeAttack(${index}, 'firearm_butt')">${buttLabel} · ${buttCost} ОД</button>
        <button type="button" class="btn btn-sm btn-warning" ${disabled || !swingPrepared ? 'disabled' : ''} title="${swingPrepared ? 'Выбрать часть тела' : 'Сначала выполните Замах'}" onclick="useMeleeAttack(${index}, 'firearm_butt', true)">Прицельный прикладом · ${buttCost} ОД</button>`;
    const requiresDraw = combatState?.status === 'active'
        && combatState.current_character?.character_id === currentCharacterId
        && !isHeldWeaponIndex(drawnWeaponIndex,index);
    if (requiresDraw) {
        const drawCost = ergonomics?.draw_action_points ?? 4;
        return `<button type="button" class="btn btn-sm btn-primary" ${disabledAttr} onclick="drawWeaponFromEquipment(${index})">Достать оружие · ${drawCost} ОД</button>`;
    }
    const isGrenadeLauncher = weaponClass === 'grenade_launcher';
    if (isGrenadeLauncher) {
        const inHands = isCombatActive && isHeldWeaponIndex(drawnWeaponIndex, index)
            ? '<button type="button" class="btn btn-sm btn-secondary" disabled>В руках</button>'
            : '';
        return `${inHands}${buttButton}
            <button type="button" class="btn btn-sm btn-success" ${firingDisabledAttr} onclick="useWeaponFromEquipment(${index}, 'unaimed', 1, 2)">Навскидку · 2 ОД · точность −4</button>
            <button type="button" class="btn btn-sm btn-warning" ${firingDisabledAttr} onclick="useWeaponFromEquipment(${index}, 'aimed', 1, 4)">Прицельный · 4 ОД</button>`;
    }
    if (!isCombatActive && weapon.requiresManualCycle && !isHeldWeaponIndex(persistentWeaponIndex, index)) {
        return `<button type="button" class="btn btn-sm btn-primary" onclick="drawWeaponFromEquipment(${index})">Взять в руки</button>`;
    }
    if (weapon.requiresManualCycle) {
        const cycleType = getManualCycleType(weapon, template);
        const specialization = weaponSpecializationKey(template);
        const level = currentCharacterData?.skills?.specialized?.[specialization]?.level || 'unfamiliar';
        const cycleCost = cycleType === 'pump' && level === 'professional' ? 0 : 1;
        return `${buttButton}<button type="button" class="btn btn-sm btn-warning" ${disabledAttr} onclick="cycleWeaponFromEquipment(${index})">${cycleType === 'pump' ? 'Дослать патрон' : 'Передёрнуть затвор'} · ${cycleCost} ОД</button>`;
    }
    if (!isCombatActive) {
        if (isHeldWeaponIndex(persistentWeaponIndex, index)) {
            buttons.push('<button type="button" class="btn btn-sm btn-secondary" disabled>В руках</button>');
        } else {
            buttons.push(`<button type="button" class="btn btn-sm btn-primary" onclick="drawWeaponFromEquipment(${index})">Взять в руки</button>`);
        }
    } else if (
        combatState.current_character?.character_id === currentCharacterId
        && isHeldWeaponIndex(drawnWeaponIndex, index)
    ) {
        buttons.push('<button type="button" class="btn btn-sm btn-secondary" disabled>В руках</button>');
    }
    buttons.push(buttButton);
    const singleOptions = Array.isArray(profile.single_shot_options) && profile.single_shot_options.length
        ? profile.single_shot_options
        : [1];
    const unaimedActionPoints = weaponClass === 'pistol' ? 1 : 2;

    buttons.push(`<button type="button" class="btn btn-sm btn-aim-action" ${firingDisabledAttr} onclick="aimWeaponFromEquipment(${index})">Прицеливание · 1 ОД</button>`);
    singleOptions.forEach((shots) => {
        const modeName = shots === 1
            ? ''
            : (shots === 2 ? 'Дуплет: ' : (shots === 4 ? 'Двойной дуплет: ' : `${shots} выстрела: `));
        buttons.push(`<button type="button" class="btn btn-sm btn-success" ${firingDisabledAttr} onclick="useWeaponFromEquipment(${index}, 'unaimed', ${shots}, ${unaimedActionPoints})">${modeName}Неприцельный · ${unaimedActionPoints} ОД</button>`);
        buttons.push(`<button type="button" class="btn btn-sm btn-success" ${firingDisabledAttr} onclick="useWeaponFromEquipment(${index}, 'rapid', ${shots}, 1)">${modeName}Беглый · 1 ОД</button>`);
        buttons.push(`<button type="button" class="btn btn-sm btn-success" ${firingDisabledAttr} onclick="useWeaponFromEquipment(${index}, 'aimed', ${shots}, ${aimedActionPoints})">${modeName}Прицельный · ${aimedActionPoints} ОД</button>`);
    });

    if (profile.supports_burst) {
        const machineGun = Boolean(profile.machine_gun_burst);
        const burstShots = Number(profile.burst_size) || 0;
        const burstActionPoints = Math.max(1,(weaponClass === 'submachine_gun' ? 2 : 3)-(backgroundIs(currentCharacterData,'Полицейский')&&['pistol','submachine_gun'].includes(weaponClass)?1:0));
        const action = (mode, actionPoints = 3, volleyCount = 1) => {
            if (machineGun && mode === 'area') {
                return `useWeaponFromEquipment(${index}, '${mode}', 10, ${actionPoints}, 2)`;
            }
            return machineGun
                ? `chooseMachineGunBurst(${index}, '${mode}', ${actionPoints}, ${volleyCount})`
                : `useWeaponFromEquipment(${index}, '${mode}', ${burstShots * volleyCount}, ${actionPoints}, ${volleyCount})`;
        };
        const burstLabel = machineGun ? 'Пулемётная очередь' : `Очередь x${burstShots}`;
        buttons.push(`<button type="button" class="btn btn-sm btn-warning" ${firingDisabledAttr} onclick="${action('burst', burstActionPoints)}">${burstLabel} · ${burstActionPoints} ОД</button>`);
        if (profile.supports_suppression) {
            buttons.push(`<button type="button" class="btn btn-sm btn-warning" ${firingDisabledAttr} onclick="${action('suppression', 3, 1)}">Подавление (1 очередь) · 3 ОД</button>`);
            buttons.push(`<button type="button" class="btn btn-sm btn-warning" ${firingDisabledAttr} onclick="${action('suppression', 5, 2)}">Подавление (2 очереди) · 5 ОД</button>`);
        }
        if (profile.supports_area_fire) {
            const areaShots = machineGun ? 10 : burstShots * 2;
            buttons.push(`<button type="button" class="btn btn-sm btn-warning" ${firingDisabledAttr} onclick="${action('area', 5, 2)}">По области · ${areaShots} выстр. · 5 ОД</button>`);
        }
    }
    const secondary=secondaryWeaponIndex();
    if(isCombatActive&&index===Number(drawnWeaponIndex)&&secondary!=null){
        const indices=[index,secondary],weapons=indices.map(value=>currentCharacterData.weapons[value]);
        const templates=weapons.map(item=>(allTemplatesCache||[]).find(candidate=>candidate.id==item.templateId));
        const options=pairedFireOptions(weapons,weapons.map((item,i)=>getWeaponFireProfile(item,templates[i])),
            weapons.map((item,i)=>getWeaponClass(item,templates[i])),indices.map(getCombatWeaponErgonomics),
            backgroundIs(currentCharacterData,'Полицейский'),backgroundIs(currentCharacterData,'Пост принял')?10:2);
        if(options.length)buttons.push('<span class="paired-fire-label">Два оружия одновременно · с помехой</span>',
            ...options.map(option=>`<button type="button" class="btn btn-sm btn-warning" data-paired-fire="${option.mode}" ${firingDisabledAttr} onclick="useWeaponFromEquipment(${index},'${option.mode}',${option.shots},${option.cost},${option.volleys},true)">${option.label} из двух · ${option.cost} ОД</button>`));
    }
    return buttons.join('');
}

function weaponAttachmentSlots(template) {
    const slots = Array.isArray(template?.attributes?.slots) ? template.attributes.slots : [
        { type: 'scope', label: 'Прицел', maxItems: 1 },
        { type: 'barrel', label: 'Ствол', maxItems: 1 },
        { type: 'handguard', label: 'Цевье', maxItems: 1 },
    ];
    const launcher = /гранатом[её]т|grenade.launcher/i.test(template?.subcategory || '');
    return slots.filter(slot => !launcher || slot.type !== 'barrel');
}

async function renderWeapons(weapons, weaponTemplates, moduleTemplates, weaponModTemplates) {
    const container = document.getElementById('weapons-container');
    if (!container) return;

    const groupedWeapons = {};
    weaponTemplates.forEach(t => {
        const cat = t.subcategory || 'Прочее';
        if (!groupedWeapons[cat]) groupedWeapons[cat] = [];
        groupedWeapons[cat].push(t);
    });
    Object.values(groupedWeapons).forEach(items => items.sort(compareTemplatesBySourceOrder));

    const groupedModules = {};
    moduleTemplates.forEach(t => {
        const cat = t.subcategory || 'Прочее';
        if (!groupedModules[cat]) groupedModules[cat] = [];
        groupedModules[cat].push(t);
    });

    const groupedMods = {};
    weaponModTemplates.forEach(t => {
        const cat = t.subcategory || 'Прочее';
        if (!groupedMods[cat]) groupedMods[cat] = [];
        groupedMods[cat].push(t);
    });

    const columns = [
        { key: 'name', label: 'Название', width: 200, type: 'text' },
        { key: 'accuracy', label: 'Точность', width: 60, type: 'number' },
        { key: 'noise', label: 'Шум', width: 40, type: 'number' },
        { key: 'caliber', label: 'Калибр', width: 90, type: 'text', readonly: true },
        { key: 'range', label: 'Дальность', width: 60, type: 'number' },
        { key: 'ergonomics', label: 'Эргономика', width: 70, type: 'number' },
        { key: 'minStrength', templateKey: 'min_strength', label: 'Мин. Сила', width: 70, type: 'number', readonly: true },
        { key: 'burst', label: 'Очередь', width: 75, type: 'text' },
        { key: 'durability', label: 'Прочность', width: 60, type: 'number' },
        { key: 'fireRate', label: 'Скорострельность', width: 105, type: 'number' },
        { key: 'weight', label: 'Вес', width: 50, type: 'number' }
    ];

    const weaponsHtml = [];
    const combatState = window.locationCombatState;
    const isCombatActive = Boolean(combatState?.status === 'active');
    const isCurrentTurn = Boolean(
        !isCombatActive
        || combatState?.current_character?.character_id === currentCharacterId
    );
    const strengthValue = getSkillEffectiveValue(currentCharacterData, 'physical.strength');
    const strengthBonus = Math.floor((strengthValue - 10) / 2);
    const fistDamage = Math.max(10, 10 * strengthBonus);
    weaponsHtml.push(`
        <div data-weapon-index="-1" style="border:1px solid var(--panel-border); padding:10px; margin-bottom:10px;">
            <div style="font-weight:bold; margin-bottom:5px;">Кулаки</div>
            <div style="display:grid; grid-template-columns:repeat(4, minmax(90px, 1fr)); gap:8px; margin-bottom:10px; background:rgba(0,0,0,0.1); padding:8px; border-radius:4px;">
                <div><strong>Урон:</strong> ${fistDamage}</div>
                <div><strong>Точность:</strong> 0</div>
                <div><strong>Бронебойность:</strong> 0%</div>
                <div><strong>Атака:</strong> Дробящая</div>
            </div>
            <button type="button" class="btn btn-sm btn-primary"
                ${isCombatActive && !isCurrentTurn ? 'disabled' : ''}
                onclick="useMeleeAttack(-1, 'unarmed')">Удар кулаком · 2 ОД</button>
            <button type="button" class="btn btn-sm btn-warning"
                ${isCombatActive && (!isCurrentTurn || combatState?.current_character?.melee_swing_round !== combatState?.round_number) ? 'disabled' : ''}
                title="Для прицельного удара сначала выполните Замах"
                onclick="useMeleeAttack(-1, 'unarmed', true)">Прицельный кулаком · 2 ОД</button>
        </div>
    `);
    for (let index = 0; index < weapons.length; index++) {
        const weapon = weapons[index];
        const modifications = Array.isArray(weapon.modifications) ? weapon.modifications : [];

        const template = weapon.templateId ? (weaponTemplates.find(t => t.id == weapon.templateId) || (allTemplatesCache || []).find(t => t.id == weapon.templateId)) : null;
        const isMelee = template?.category === 'melee_weapon' || weapon.category === 'melee_weapon';
        const combatErgonomics = isMelee ? null : getCombatWeaponErgonomics(index);

        let fieldsHtml = '';
        if (isMelee) {
            const attrs = template?.attributes || weapon.attributes || {};
            fieldsHtml = `
                <div style="font-weight: bold; margin-bottom: 5px;">${escapeHtml(weapon.name)}</div>
                <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-bottom: 10px; background: rgba(0,0,0,0.1); padding: 8px; border-radius: 4px;">
                    <div><strong>Урон:</strong> ${attrs.damage || 0}</div>
                    <div><strong>Точность:</strong> ${attrs.accuracy || 0}</div>
                    <div><strong>Бронебойность:</strong> ${attrs.armor_piercing || 0}%</div>
                    <div><strong>Кровотечение:</strong> ${attrs.bleeding || 'Нет'}</div>
                    <div><strong>Класс веса:</strong> ${attrs.weight_class || '—'}</div>
                    <div><strong>Размер:</strong> ${attrs.size || '—'}</div>
                    <div><strong>Вес:</strong> ${weapon.weight || template?.weight || 0} кг</div>
                    <div><strong>Прочность:</strong> ${weapon.durability || template?.attributes?.durability || 100}</div>
                </div>
            `;
        } else {
            fieldsHtml = '<div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px;">';
            const effectiveStats = (weapon.installedModules && weapon.installedModules.length > 0)
                ? getEffectiveWeaponStats(weapon)
                : null;

            columns.forEach(col => {
                const templateValue = template?.attributes?.[col.templateKey || col.key];
                const baseValue = col.key === 'caliber'
                    ? (templateValue || weapon.caliber || '')
                    : (
                        weapon[col.key] !== undefined
                            ? weapon[col.key]
                            : (templateValue ?? (col.type === 'number' ? 0 : ''))
                    );
                let effectiveValue = null;
                if (effectiveStats && (col.key === 'accuracy' || col.key === 'noise' || col.key === 'range' || col.key === 'ergonomics')) {
                    effectiveValue = effectiveStats[col.key];
                }
                if (col.key === 'ergonomics' && combatErgonomics) {
                    effectiveValue = combatErgonomics.value;
                }

                fieldsHtml += `
                    <div style="width: ${col.width}px;">
                        <div style="font-size: 0.75rem; color: var(--text-secondary); margin-bottom: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-align: center;">${col.label}</div>
                        ${col.type === 'number'
                            ? `<input type="number" class="form-control number-input" name="weapons.${index}.${col.key}" value="${baseValue}" style="width: 100%;"${col.readonly ? ' readonly' : ''}>`
                            : `<input type="text" class="form-control" name="weapons.${index}.${col.key}" value="${escapeHtml(baseValue)}" placeholder="${col.label}" style="width: 100%;"${col.readonly ? ' readonly' : ''}>`
                        }
                        ${effectiveValue !== null && effectiveValue !== baseValue ?
                            `<div style="font-size: 0.7rem; color: #4caf50; text-align: center;">${effectiveValue}</div>` : ''}
                    </div>
                `;
            });
            fieldsHtml += '</div>';
            const sizeOptions = template?.attributes?.size_options;
            if (Array.isArray(sizeOptions) && sizeOptions.length === 2) {
                fieldsHtml += `<div style="margin-bottom:8px;">Размер: ${sizeOptions.map(value => escapeHtml(String(value))).join(' / ')} · Объём: ${escapeHtml(String(weapon.volume ?? template.volume ?? sizeOptions[0]))}</div>`;
            }
            if (combatErgonomics) {
                fieldsHtml += `
                    <div style="display:flex; flex-wrap:wrap; gap:7px; margin:-2px 0 10px; font-size:12px;">
                        <span class="badge">Итоговая эргономика: ${combatErgonomics.value}</span>
                        <span class="badge">Достать: ${combatErgonomics.draw_action_points} ОД</span>
                        <span class="badge">Перезарядка: ${formatActionPointModifier(combatErgonomics.reload_action_points_modifier)}</span>
                        <span class="badge">Прицельный: ${combatErgonomics.aimed_shot_action_points} ОД</span>
                        <span class="badge">Точность в дальности: ${combatErgonomics.accuracy_modifier > 0 ? '+' : ''}${combatErgonomics.accuracy_modifier}</span>
                    </div>
                `;
            }
        }

        let modelBlock = '';
        if (!weapon.model && !isMelee) {
            if (!weapon.id) weapon.id = generateItemId();
            const options = Object.entries(groupedWeapons)
                .sort(([left], [right]) => compareByFixedOrder(left, right, WEAPON_SUBCATEGORY_ORDER))
                .map(([cat, items]) => `
                <optgroup label="${cat}">
                    ${items.map(t => `<option value="${t.id}">${t.name} ${t.source === 'local' ? '(кастом)' : ''}</option>`).join('')}
                </optgroup>
            `).join('');
            modelBlock = `
                <div style="display: flex; gap: 5px; align-items: center; margin-bottom: 10px; flex-wrap: wrap;">
                    <select id="weapon-model-select-${index}" data-weapon-instance="${escapeHtml(weapon.id)}" class="form-control" style="width: 200px;">
                        <option value="">-- Выберите модель --</option>
                        ${options}
                    </select>
                    <button type="button" class="btn btn-sm btn-primary" onclick="selectWeaponModel(${index})">Выбрать</button>
                    ${window.isGM ? `<button type="button" class="btn btn-sm btn-secondary" onclick="openCreateWeaponTemplateModal(${index})">➕ Создать кастом</button>` : ''}
                </div>
            `;
        }

        let slotsHtml = '';
        if (!isMelee && weapon.templateId) {
            const weaponTemplate = weaponTemplates.find(t => t.id == weapon.templateId);
            if (weaponTemplate) {
                const slots = weaponAttachmentSlots(weaponTemplate);
                const installed = Array.isArray(weapon.installedModules) ? weapon.installedModules : [];
                slotsHtml = `<div style="margin-top: 10px; padding: 8px; background: rgba(0,0,0,0.1); border-radius: 4px;"><strong>Слоты:</strong>`;
                slots.forEach(slot => {
                    const installedMod = installed.find(mod => mod.slotType === slot.type);
                    slotsHtml += `<div style="margin-left: 15px; display: flex; align-items: center; gap: 10px; margin-top: 5px;">`;
                    slotsHtml += `<span style="width: 100px;">${slot.label}:</span>`;
                    if (installedMod) {
                        slotsHtml += `<span style="flex:1;" title="${escapeHtml(installedMod.description || '')}">${escapeHtml(installedMod.name)}</span>`;
                        if (installedMod.attributes?.removable!==false && !installedMod.attributes?.permanent) {
                            slotsHtml += `<button type="button" class="btn btn-sm btn-danger" onclick="unequipModuleFromWeapon(${index}, '${slot.type}')">Снять</button>`;
                        } else slotsHtml += '<span class="badge">Несъёмный</span>';
                    } else {
                        slotsHtml += `<button type="button" class="btn btn-sm btn-primary" onclick="equipModuleToWeapon(${index}, '${slot.type}')">Установить</button>`;
                    }
                    slotsHtml += `</div>`;
                });
                slotsHtml += `</div>`;
            }
        }

        let magazineHtml = '';
        if (!isMelee && weapon.templateId) {
            const weaponTemplate = weaponTemplates.find(t => t.id == weapon.templateId);
            const hasFixedMagazine = weaponTemplate?.attributes?.fixedMagazine || false;

            if (hasFixedMagazine) {
                const maxAmmo = weaponTemplate.attributes?.magazine_size || 0;
                const fixedAmmo = Array.isArray(weapon.fixedAmmo)
                    ? weapon.fixedAmmo.filter(stack => Number(stack?.quantity) > 0)
                    : [];
                const stackedAmmo = fixedAmmo.reduce((sum, stack) => sum + Number(stack.quantity || 0), 0);
                const totalAmmo = fixedAmmo.length ? stackedAmmo : Math.max(0, Number(weapon.ammo || 0));
                const nextAmmo = fixedAmmo.length ? fixedAmmo[fixedAmmo.length - 1] : null;
                const ammoBreakdown = fixedAmmo.length
                    ? `<br><small>Состав: ${fixedAmmo.map(stack => `${formatAmmoStackLabel(stack)} (${stack.quantity})`).join(', ')}
                        <br>▶ Следующий: ${formatAmmoStackLabel(nextAmmo)}</small>`
                    : (totalAmmo > 0 ? '<br><small>Состав старых данных не указан</small>' : '');
                magazineHtml = `<div style="margin-top: 10px; padding: 8px; background: rgba(0,0,0,0.1); border-radius: 4px;">
                    <strong>Магазин:</strong>
                    <div style="margin-left: 15px; display: flex; align-items: center; gap: 10px; margin-top: 5px;">
                        <span>Несъёмный (${totalAmmo}/${maxAmmo})${ammoBreakdown}</span>
                        <button type="button" class="btn btn-sm btn-primary" onclick="reloadFixedMagazine(${index})">Зарядить</button>
                        <button type="button" class="btn btn-sm btn-danger" onclick="unloadFixedMagazine(${index})" ${totalAmmo <= 0 ? 'disabled' : ''}>Разрядить</button>
                    </div>
                </div>`;
            } else {
                const installedMag = weapon.installedMagazine;
                magazineHtml = `<div style="margin-top: 10px; padding: 8px; background: rgba(0,0,0,0.1); border-radius: 4px;">
                    <strong>Магазин:</strong>
                    <div style="margin-left: 15px; display: flex; align-items: center; gap: 10px; margin-top: 5px;">`;
                if (installedMag) {
                    const totalAmmo = installedMag.ammo ? installedMag.ammo.reduce((sum, a) => sum + a.quantity, 0) : 0;
                    let ammoBreakdown = '';
                    if (installedMag.ammo && installedMag.ammo.length > 0) {
                        const nextAmmo = installedMag.ammo[installedMag.ammo.length - 1];
                        ammoBreakdown = `<br><small>Состав: ${installedMag.ammo.map(a => `${formatAmmoStackLabel(a)} (${a.quantity})`).join(', ')}`;
                        if (nextAmmo) ammoBreakdown += `<br>▶ Следующий: ${formatAmmoStackLabel(nextAmmo)}`;
                        ammoBreakdown += '</small>';
                    }
                    magazineHtml += `<span>${escapeHtml(installedMag.name)} (${totalAmmo}/${installedMag.capacity || 30})${ammoBreakdown}</span>`;
                    magazineHtml += `<button type="button" class="btn btn-sm btn-danger" onclick="unequipMagazineFromWeapon(${index})">Снять</button>`;
                } else {
                    magazineHtml += `<button type="button" class="btn btn-sm btn-primary" onclick="equipMagazineToWeapon(${index})">Установить магазин</button>`;
                }
                magazineHtml += `</div></div>`;
            }
        }

        const combatState = window.locationCombatState;
        const isCombatActive = Boolean(combatState && combatState.status === 'active');
        const isCurrentTurn = Boolean(
            !isCombatActive ||
            combatState?.current_character?.character_id === currentCharacterId
        );
        const combatActionDisabled = isCombatActive && !isCurrentTurn;

        let jamHtml = '';
        if (!isMelee && weapon.jam) {
            const jam = weapon.jam;
            const jamCount = Array.isArray(weapon.jams) ? weapon.jams.length : 1;
            const shooting = getSkillEffectiveValue(currentCharacterData, 'physical.shooting');
            const reduction = shooting >= 20 ? 2 : (shooting >= 15 ? 1 : 0);
            const clearCost = Math.max(0, Number(jam.fix_ap || 0) - reduction);
            const repairText = jam.repair_required === 'full'
                ? 'Нужен ремонт до текущего максимума прочности с учётом штрафа ремонта'
                : (jam.repair_required === 'increase'
                    ? 'Нужно восстановить хотя бы 1 прочность'
                    : `Устранение: ${clearCost} ОД`);
            const clearButton = !jam.repair_required && isCombatActive
                ? `<button type="button" class="btn btn-sm btn-warning" ${combatActionDisabled ? 'disabled' : ''} onclick="clearWeaponJam(${index})">Устранить клин · ${clearCost} ОД</button>`
                : '';
            jamHtml = `
                <div style="margin:8px 0; padding:8px 10px; border:1px solid #8d5d2d; background:rgba(126,70,25,.18); border-radius:4px; display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
                    <strong>Клин ${Number(jam.result) || ''}: ${escapeHtml(jam.label || 'Неисправность')}</strong>
                    ${jamCount > 1 ? `<span>Активных неисправностей: ${jamCount}</span>` : ''}
                    <span>${repairText}</span>
                    ${clearButton}
                </div>`;
        }

        let attackButtonsHtml = '';
        if (isMelee) {
            const meleeAttributes = template?.attributes || weapon.attributes || {};
            const allowedAttacks = meleeAttributes.allowed_attacks || [];
            const weightClass = String(meleeAttributes.weight_class || weapon.weightClass || 'Тяжелое').toLowerCase();
            const drawnWeaponIndex = combatState?.current_character?.drawn_weapon_index;
            const activeWeaponIndex = isCombatActive ? drawnWeaponIndex : currentCharacterData?.activeWeaponIndex;
            const naturalWeapon = Boolean(weapon.naturalWeapon || meleeAttributes.natural_weapon);
            const handsButton = naturalWeapon
                ? '<button type="button" class="btn btn-sm btn-secondary" disabled>Врожденная атака</button>'
                : isHeldWeaponIndex(activeWeaponIndex, index)
                ? '<button type="button" class="btn btn-sm btn-secondary" disabled>В руках</button>'
                : `<button type="button" class="btn btn-sm btn-primary" ${combatActionDisabled ? 'disabled' : ''} onclick="drawWeaponFromEquipment(${index})">${isCombatActive ? 'Достать оружие' : 'Взять в руки'}</button>`;
            const swingPrepared = combatState?.current_character?.melee_swing_round === combatState?.round_number;
            attackButtonsHtml = handsButton + allowedAttacks.map((attackType) => {
                let meleeCost = Number(meleeAttributes.action_points) || getMeleeActionPointCost(weightClass, attackType);
                if(backgroundIs(currentCharacterData,'Пожарный')&&weapon.name?.trim().toLowerCase()==='топор')meleeCost=Math.max(1,meleeCost-1);
                const circular = String(attackType).toLowerCase().includes('круг');
                const aimedButton = circular
                    ? ''
                    : `<button type="button" class="btn btn-sm btn-warning" ${combatActionDisabled || !swingPrepared ? 'disabled' : ''} title="${swingPrepared ? 'Выбрать часть тела' : 'Сначала выполните Замах'}" onclick="useMeleeAttack(${index}, '${attackType}', true)">Прицельный: ${attackType.toLowerCase()} · ${meleeCost} ОД</button>`;
                return `<button type="button" class="btn btn-sm btn-primary" ${combatActionDisabled ? 'disabled' : ''} onclick="useMeleeAttack(${index}, '${attackType}')">${attackType} · ${meleeCost} ОД</button>${aimedButton}`;
            }).join('');
        } else {
            attackButtonsHtml = renderRangedAttackButtons(weapon, template, index, combatActionDisabled);
        }

        let grenadeLauncherHtml = '';
        if (!isMelee) {
            if (weapon.installedModules?.some(m=>m.attributes?.bayonet)) {
                attackButtonsHtml += `<button type="button" class="btn btn-sm btn-primary" ${combatActionDisabled?'disabled':''} onclick="useMeleeAttack(${index}, 'bayonet')">Колющий штыком · 2 ОД</button>`;
            }
            const launcher = weapon.installedModules?.find(m => m.slotType === 'handguard' && m.attributes?.type === 'grenade_launcher');
            if (launcher) {
                const isLoaded = launcher.loaded || false;
                if (isLoaded) {
                    grenadeLauncherHtml = `<button type="button" class="btn btn-sm btn-warning" ${combatActionDisabled ? 'disabled' : ''} onclick="fireGrenadeLauncher(${index})" style="margin-left: 5px;" title="${combatActionDisabled ? 'Сейчас не ход этого персонажа' : 'Выстрел из подствольника'}">💣 Выстрел ГП</button>`;
                } else {
                    grenadeLauncherHtml = `<button type="button" class="btn btn-sm btn-secondary" ${combatActionDisabled ? 'disabled' : ''} onclick="reloadGrenadeLauncher(${index})" style="margin-left: 5px;" title="${combatActionDisabled ? 'Сейчас не ход этого персонажа' : 'Зарядить подствольник'}">➕ Зарядить ГП</button>`;
                }
            }
        }

        let modificationsHtml = '';
        modifications.forEach((mod, mi) => {
            const modOptions = Object.entries(groupedMods).map(([cat, items]) => `
                <optgroup label="${cat}">
                    ${items.map(t => `<option value="${t.id}" ${mod.name === t.name ? 'selected' : ''}>${t.name}</option>`).join('')}
                </optgroup>
            `).join('');
            modificationsHtml += `
                <div style="display: flex; gap: 5px; margin-bottom: 3px; align-items: center;">
                    <select name="weapons.${index}.modifications.${mi}.name" class="form-control" style="width: 150px;">
                        <option value="">-- Выберите модификацию --</option>
                        ${modOptions}
                    </select>
                    <input type="text" class="form-control" name="weapons.${index}.modifications.${mi}.description" value="${escapeHtml(mod.description || '')}" placeholder="Описание" style="flex:1;">
                    <button type="button" class="btn btn-sm btn-danger" onclick="removeWeaponModification(${index}, ${mi})">✕</button>
                </div>
            `;
        });

        weaponsHtml.push(`
            <div data-weapon-index="${index}" ${weapon.templateId ? `data-item-template-id="${weapon.templateId}"` : ''} style="border:1px solid var(--panel-border); padding:10px; margin-bottom:10px;">
                ${renderCreatedByPlayerBadge(weapon)}
                ${modelBlock}
                ${jamHtml}
                <div class="loadout-weapon-actions">
                    ${renderWeaponGripControl(weapon, index)}
                    ${attackButtonsHtml}
                    ${grenadeLauncherHtml}
                </div>
                ${magazineHtml}
                <details class="loadout-weapon-stats" data-equipment-panel="weapon-stats-${index}" ${equipmentOpenPanels.has(`weapon-stats-${index}`) ? 'open' : ''}>
                    <summary>Характеристики и модули</summary>
                    ${fieldsHtml}
                    ${slotsHtml}
                    ${!isMelee ? `
                    <div style="margin-top:10px;">
                        <div style="display: flex; align-items: center;">
                            <label style="margin: 0;">Модификации</label>
                            <button type="button" class="btn btn-sm" onclick="addWeaponModification(${index})" title="Добавить модификацию" style="padding: 2px 8px;">➕</button>
                        </div>
                        <div id="modifications-${index}">${modificationsHtml}</div>
                    </div>
                    ` : ''}
                </details>
                ${weapon.naturalWeapon ? '' : `<div class="loadout-weapon-management"><button type="button" class="btn btn-sm btn-danger" onclick="unequipWeapon(${index})">Снять</button><button type="button" class="btn btn-sm btn-danger" onclick="removeWeapon(${index})">Удалить оружие</button></div>`}
            </div>
        `);
    }
    container.innerHTML = weaponsHtml.join('');
}

window.equipModuleToWeapon = async function(weaponIndex, slotType) {
    const weapon = currentCharacterData.weapons[weaponIndex];
    if (!weapon) return;
    if (!weapon.templateId) {
        showNotification('Оружие должно быть основано на шаблоне');
        return;
    }

    const weaponTemplates = await loadTemplatesForLobby('weapon');
    const weaponTemplate = weaponTemplates.find(t => t.id == weapon.templateId);
    if (!weaponTemplate || !weaponAttachmentSlots(weaponTemplate).some(slot => slot.type === slotType)) {
        showNotification('У этого оружия нет выбранного слота');
        return;
    }
    if ((weapon.installedModules || []).some(module => module.slotType === slotType)) {
        showNotification('Этот слот уже занят');
        return;
    }
    const weaponCaliber = getItemCaliber(weaponTemplate);
    await getAllItemTemplates();

    const inventoryModules = [];
    const collectModules = (items, path) => {
        if (!Array.isArray(items)) return;
        items.forEach((item, idx) => {
            if (item.category === 'weapon_module' && item.attributes?.slot_type === slotType) {
                // Проверка калибра для слота "Ствол"
                if (slotType === 'barrel' && weaponCaliber) {
                    const moduleCaliber = normalizeBaseCaliberText(item.attributes?.caliber ?? item.caliber ?? '');
                    if (moduleCaliber && moduleCaliber !== weaponCaliber) return;
                }
                inventoryModules.push({ item, path: path.concat(idx) });
            }
            if (item.contents) collectModules(item.contents, path.concat(idx, 'contents'));
        });
    };

    collectModules(currentCharacterData.inventory?.backpack, ['inventory', 'backpack']);
    collectModules(currentCharacterData.inventory?.pockets, ['inventory', 'pockets']);
    const beltPouches = currentCharacterData.equipment?.belt?.pouches || [];
    beltPouches.forEach((pouch, i) => collectModules(pouch.contents, ['equipment', 'belt', 'pouches', i, 'contents']));
    const vestPouches = currentCharacterData.equipment?.vest?.pouches || [];
    vestPouches.forEach((pouch, i) => collectModules(pouch.contents, ['equipment', 'vest', 'pouches', i, 'contents']));

    if (inventoryModules.length === 0) {
        showNotification('Нет подходящих модулей в инвентаре');
        return;
    }

    let modal = document.getElementById('module-select-modal');
    if (modal) modal.remove();
    modal = document.createElement('div');
    modal.id = 'module-select-modal';
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content">
            <span class="close" onclick="document.getElementById('module-select-modal').remove()">&times;</span>
            <h3>Выберите модуль</h3>
            <select id="module-select" class="form-control"></select>
            <div class="form-actions" style="margin-top:15px;">
                <button class="btn btn-primary" id="confirm-module-btn">Установить</button>
                <button class="btn btn-secondary" onclick="document.getElementById('module-select-modal').remove()">Отмена</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    const select = document.getElementById('module-select');
    inventoryModules.forEach((entry, idx) => {
        const opt = document.createElement('option');
        opt.value = idx;
        opt.textContent = `${entry.item.name} (${entry.item.weight} кг, ${entry.item.volume} л)`;
        select.appendChild(opt);
    });

    modal.querySelector('#confirm-module-btn').onclick = () => {
        const idx = select.value;
        if (idx === '') return;
        const selected = inventoryModules[idx];
        modal.remove();

        // Проверка совместимости для подствольного гранатомёта
        if (slotType === 'handguard' && selected.item.attributes?.type === 'grenade_launcher') {
            const allowedCategories = selected.item.attributes?.compatible_weapon_categories;
            const weaponSubcategory = weaponTemplate?.subcategory;
            if (!/штурмов|assault/i.test(weaponSubcategory || '')
                || (allowedCategories?.length && !allowedCategories.includes(weaponSubcategory))) {
                    showNotification('Этот подствольник можно установить только на штурмовые винтовки');
                    return;
            }
        }

        const source = getItemByPath(selected.path);
        if (source !== selected.item || currentCharacterData.weapons[weaponIndex] !== weapon
            || (weapon.installedModules || []).some(module => module.slotType === slotType)) {
            showNotification('Предметы изменились. Выберите модуль заново');
            return;
        }
        const installedModule = structuredClone(selected.item);
        if (slotType==='barrel' && installedModule.attributes?.caliber_specific) {
            if (!weaponCaliber) { showNotification('Укажите калибр оружия перед установкой модуля'); return; }
            // Первая установка задаёт калибр экземпляра; после снятия он сохраняется.
            installedModule.attributes.caliber=installedModule.attributes.caliber || weaponCaliber;
        }
        if (Number(source.quantity || 1) > 1) {
            source.quantity -= 1;
            installedModule.id = generateItemId();
        } else if (!removeItemByPath(selected.path)) {
            showNotification('Не удалось найти модуль в инвентаре');
            return;
        }

        if (!weapon.installedModules) weapon.installedModules = [];
        weapon.installedModules.push({
            ...installedModule,
            quantity: 1,
            slotType: slotType,
            modifiers: installedModule.modifiers || installedModule.attributes?.modifiers || {},
        });
        const sizeReduction=Number(installedModule.attributes?.weapon_size_reduction)||0;
        if (sizeReduction>0) {
            weapon.volume=Math.max(0,Number(weapon.volume ?? weaponTemplate.volume ?? 0)-sizeReduction);
            if (weapon.size!=null) weapon.size=Math.max(0,(Number(weapon.size)||0)-sizeReduction);
            if (weapon.attributes?.size!=null) weapon.attributes.size=Math.max(0,(Number(weapon.attributes.size)||0)-sizeReduction);
        }

        renderEquipmentTab(currentCharacterData);
        renderInventoryTab(currentCharacterData);
        scheduleAutoSave();
        forceSyncCharacter();
        showNotification('Модуль установлен', 'success');
    };

    modal.style.display = 'flex';
};

window.confirmEquipModule = function(weaponIndex, slotType) {
    const modal = document.getElementById('module-select-modal');
    const select = document.getElementById('module-select');
    const selected = modal._moduleList[select.value];
    if (!selected) return;

    const weapon = currentCharacterData.weapons[weaponIndex];
    if (!weapon.installedModules) weapon.installedModules = [];

    // Удаляем модуль из инвентаря по пути
    if (!removeItemByPath(selected.path)) {
        showNotification('Не удалось найти модуль в инвентаре');
        return;
    }

    // Добавляем в установленные
    weapon.installedModules.push({
        id: selected.item.id,
        templateId: selected.item.templateId,
        name: selected.item.name,
        slotType: slotType,
        modifiers: selected.item.attributes?.modifiers || {}
    });

    modal.style.display = 'none';
    renderEquipmentTab(currentCharacterData);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Модуль установлен', 'success');
};

window.unequipModuleFromWeapon = async function(weaponIndex, slotType) {
    const weapon = currentCharacterData.weapons[weaponIndex];
    if (!weapon || !weapon.installedModules) return;

    const modIndex = weapon.installedModules.findIndex(m => m.slotType === slotType);
    if (modIndex === -1) return;

    const installedMod = weapon.installedModules[modIndex];
    if (installedMod.attributes?.removable === false || installedMod.attributes?.permanent
        || /укорочение ствола/i.test(installedMod.name || '')) {
        showNotification('Этот модуль нельзя снять после установки');
        return;
    }

    let restoredItem;
    if (installedMod.templateId) {
        const templates = await loadTemplatesForLobby('weapon_module');
        const template = templates.find(t => t.id === installedMod.templateId);
        if (template) {
            restoredItem = createItemFromTemplate(template);
        } else {
            restoredItem = {
                id: installedMod.id || generateItemId(),
                templateId: installedMod.templateId,
                name: installedMod.name,
                category: 'weapon_module',
                weight: installedMod.weight || 0.5,
                volume: installedMod.volume || 0.2,
                quantity: 1,
                attributes: {
                    slot_type: slotType,
                    modifiers: installedMod.modifiers,
                    caliber: installedMod.caliber
                }
            };
        }
    } else {
        restoredItem = {
            id: installedMod.id || generateItemId(),
            name: installedMod.name,
            category: 'weapon_module',
            weight: installedMod.weight || 0.5,
            volume: installedMod.volume || 0.2,
            quantity: 1,
            attributes: {
                slot_type: slotType,
                modifiers: installedMod.modifiers
            }
        };
    }

    // Состояние конкретного модуля важнее исходного шаблона: патроны,
    // прочность, батареи и прочие вложения должны вернуться вместе с ним.
    restoredItem = { ...restoredItem, ...structuredClone(installedMod), category: 'weapon_module', quantity: 1 };
    delete restoredItem.slotType;
    delete restoredItem.sourcePath;
    weapon.installedModules.splice(modIndex, 1);
    const path = installedMod.sourcePath;
    let restored = false;
    if (path) restored = restoreItemToPath(restoredItem, path);
    if (!restored) {
        if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
        if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
        currentCharacterData.inventory.backpack.push(restoredItem);
    }

    renderEquipmentTab(currentCharacterData);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Модуль снят', 'success');
};

window.equipMagazineToWeapon = async function(weaponIndex) {
    const weapon = currentCharacterData.weapons[weaponIndex];
    if (!weapon) return;
    if (!weapon.templateId) { showNotification('Оружие должно быть основано на шаблоне'); return; }

    const weaponTemplates = await loadTemplatesForLobby('weapon');
    const weaponTemplate = weaponTemplates.find(t => t.id == weapon.templateId);
    const weaponCaliber = getItemCaliber(weaponTemplate);

    const inventoryMagazines = [];

    const collectMagazines = (items, path) => {
        if (!Array.isArray(items)) return;
        items.forEach((item, idx) => {
            if (
                item.category === 'magazine'
                && !isAmmoFeederTool(item)
                && !isAmmoLoadingDevice(item)
            ) {
                // Проверка калибра
                const itemCaliber = getItemCaliber(item);
                if (weaponCaliber && itemCaliber && itemCaliber !== weaponCaliber) return;
                // Проверка совместимости по списку оружий (если список не пуст)
                const compatible = getMagazineCompatibleWeaponIds(item);
                if (
                    compatible.length > 0
                    && !compatible.includes(Number(weapon.templateId))
                ) return;
                inventoryMagazines.push({ item, path: path.concat(idx) });
            }
            if (item.contents) collectMagazines(item.contents, path.concat(idx, 'contents'));
        });
    };

    collectMagazines(currentCharacterData.inventory?.backpack, ['inventory', 'backpack']);
    collectMagazines(currentCharacterData.inventory?.pockets, ['inventory', 'pockets']);
    const beltPouches = currentCharacterData.equipment?.belt?.pouches || [];
    beltPouches.forEach((pouch, i) => collectMagazines(pouch.contents, ['equipment', 'belt', 'pouches', i, 'contents']));
    const vestPouches = currentCharacterData.equipment?.vest?.pouches || [];
    vestPouches.forEach((pouch, i) => collectMagazines(pouch.contents, ['equipment', 'vest', 'pouches', i, 'contents']));

    if (inventoryMagazines.length === 0) {
        showNotification('Нет подходящих магазинов в инвентаре');
        return;
    }

    // Всегда удаляем старое модальное окно, если оно есть
    const oldModal = document.getElementById('magazine-select-modal');
    if (oldModal) oldModal.remove();

    const modal = document.createElement('div');
    modal.id = 'magazine-select-modal';
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content">
            <span class="close" onclick="document.getElementById('magazine-select-modal').remove()">&times;</span>
            <h3>Выберите магазин</h3>
            <select id="magazine-select" class="form-control"></select>
            <div id="magazine-reload-preview" style="margin-top:12px; padding:10px 12px; border:1px solid var(--border-color, #45483e); border-radius:6px; background:rgba(0,0,0,.18);"></div>
            <div class="form-actions">
                <button class="btn btn-primary" id="confirm-magazine-btn">Установить</button>
                <button class="btn btn-secondary" onclick="document.getElementById('magazine-select-modal').remove()">Отмена</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    const select = modal.querySelector('#magazine-select');
    inventoryMagazines.forEach((entry, idx) => {
        const opt = document.createElement('option');
        opt.value = idx;
        opt.textContent = `${entry.item.name} (${entry.item.ammo?.reduce((s,a)=>s+a.quantity,0) || 0} патр.)`;
        select.appendChild(opt);
    });

    const preview = modal.querySelector('#magazine-reload-preview');
    let previewVersion = 0;
    const updatePreview = async () => {
        const version = ++previewVersion;
        const selected = inventoryMagazines[select.value];
        if (!selected) {
            renderReloadPreview(preview, null, []);
            return;
        }
        const paymentRows = [];
        if (window.locationCombatState?.status === 'active') {
            const access = await calculateInventoryAccess(selected.item, selected.path);
            if (version !== previewVersion || !modal.isConnected) return;
            const profile = getCombatWeaponErgonomics(weaponIndex);
            const selectedMagazineErgonomics = Number(selected.item.attributes?.ergonomics || 0);
            const effectiveErgonomics = profile
                ? Number(profile.value || 0) - Number(profile.magazine_modifier || 0) + selectedMagazineErgonomics
                : selectedMagazineErgonomics;
            const baseCost = Math.max(0, Number(selected.item.attributes?.reload_time_od || 0));
            const rawUseCost = Math.max(
                0,
                baseCost + reloadErgonomicsModifier(effectiveErgonomics) - Number(access.useActionDiscount || 0),
            );
            const discount=traitCount(currentCharacterData,'Быстрые руки');
            const useCost=discount?Math.max(1,rawUseCost-discount):rawUseCost;
            const totalCost = useCost + Number(access.retrievalActionPoints || 0);
            paymentRows.push({ label: 'Смена магазина', payment: `${totalCost} ОД` });
        }
        renderReloadPreview(preview, selected.item, paymentRows);
    };
    select.addEventListener('change', updatePreview);
    await updatePreview();

    // Кнопка подтверждения использует актуальный weaponIndex и список
    modal.querySelector('#confirm-magazine-btn').onclick = async () => {
        const idx = select.value;
        if (idx === '') return;
        const selected = inventoryMagazines[idx];
        modal.remove();
        await confirmEquipMagazineDirect(weaponIndex, selected);
    };

    modal.style.display = 'flex';
};

async function confirmEquipMagazineDirect(weaponIndex, selected) {
    if (medicalOperationInProgress) return;
    const characterId = currentCharacterId;
    const weapon = currentCharacterData?.weapons?.[weaponIndex];
    if (!weapon || !selected?.item) return;
    const identity = item => Object.fromEntries(['id', 'templateId', 'name'].filter(key => item[key] !== undefined).map(key => [key, item[key]]));
    const selection = JSON.parse(JSON.stringify({ magazine: identity(selected.item), weapon: identity(weapon) }));
    let locked = false;
    const sheet = document.getElementById('character-sheet-modal');
    const wasInert = sheet?.inert || false;
    try {
        if (autoSaveTimer) await forceSyncCharacter();
        await Server.waitForCharacterSave(characterId);
        if (currentCharacterId !== characterId || medicalOperationInProgress) return;
        if (sheetSaveBlocked) throw new Error('Переоткройте лист перед перезарядкой');
        medicalOperationInProgress = locked = true;
        medicalRemoteUpdate = null;
        if (sheet) sheet.inert = true;
        const payload = {
            weapon_index: weaponIndex, item_path: selected.path,
            magazine_template_id: selected.item.templateId, magazine_selection: selection,
        };
        const state = window.locationCombatState;
        if (state?.status === 'active') {
            const actor = state.current_character;
            if (actor?.character_id !== characterId) throw new Error('Сменить магазин можно только в свой ход');
            const access = await calculateInventoryAccess(selected.item, selected.path);
            const result = await Server.performLocationCombatAction(window.currentLobbyId, window.currentLocationId, {
                ...payload, location_character_id: actor.location_character_id, action_key: 'reload_weapon',
                inventory_retrieval_action_points: access.retrievalActionPoints,
                inventory_use_action_discount: access.useActionDiscount,
                pending_action_id: `reload-${actor.location_character_id}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
            });
            // Installation is entirely server-side, including after a future turn's payment.
            const fresh = await Server.getCharacter(characterId);
            currentCharacterData = fresh.data;
            showNotification(result?.pending_action ? 'Перезарядка начата. Магазин будет установлен после полной оплаты ОД.' : 'Магазин установлен', 'system');
        } else {
            const result = await Server.installWeaponMagazine(characterId, { ...payload, character_revision: currentCharacterData._revision });
            currentCharacterData = result.data;
            showNotification('Магазин установлен', 'success');
        }
    } catch (error) {
        showNotification(error.message || 'Не удалось сменить магазин', 'system');
    } finally {
        if (locked) {
            if (medicalRemoteUpdate?.character_id === Number(characterId)
                && Number(medicalRemoteUpdate.data?._revision) > Number(currentCharacterData?._revision)) {
                currentCharacterData = medicalRemoteUpdate.data;
            }
            medicalRemoteUpdate = null;
            medicalOperationInProgress = false;
            if (sheet) sheet.inert = wasInert;
            await refreshSavedSheet(characterId);
        }
    }
}

window.reloadInstalledMagazine = async function(weaponIndex) {
    if (secondaryWeaponIndex()!=null) {showNotification('Для перезарядки сначала освободите руку','system');return;}
    const weapon = currentCharacterData.weapons?.[weaponIndex];
    if (!weapon?.installedMagazine) return;
    showNotification('Сначала снимите отъёмный магазин с оружия');
};

window.unequipMagazineFromWeapon = function(weaponIndex) {
    const weapon = currentCharacterData.weapons[weaponIndex];
    if (!weapon || !weapon.installedMagazine) return;

    const mag = weapon.installedMagazine;
    weapon.installedMagazine = null;

    const restoredItem = {
        id: mag.id,
        templateId: mag.templateId,
        name: mag.name,
        category: 'magazine',
        weight: mag.weight || 0,
        volume: mag.volume || 0.2,
        quantity: 1,
        ammo: mag.ammo ? mag.ammo.map(a => ({ ...a })) : [],
        emptyWeight: mag.emptyWeight || 0,
        loadedWeight: mag.loadedWeight || 0,
        attributes: {
            caliber: mag.caliber,
            capacity: mag.capacity,
            emptyWeight: mag.emptyWeight,
            loadedWeight: mag.loadedWeight,
            ergonomics: mag.ergonomics || 0,
            reload_time_od: mag.reloadTimeActionPoints || 0
        }
    };
    Object.defineProperty(restoredItem, 'currentAmmo', {
        get() { return this.ammo.reduce((sum, a) => sum + a.quantity, 0); },
        enumerable: true
    });
    updateMagazineWeight(restoredItem);

    const path = mag.sourcePath;
    let restored = false;
    if (path) restored = restoreItemToPath(restoredItem, path);
    if (!restored) {
        if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
        if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
        currentCharacterData.inventory.backpack.push(restoredItem);
    }

    renderEquipmentTab(currentCharacterData);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Магазин снят', 'success');
};

window.reloadFixedMagazine = async function(weaponIndex) {
    if (secondaryWeaponIndex()!=null) {showNotification('Для перезарядки сначала освободите руку','system');return;}
    const weapon = currentCharacterData.weapons[weaponIndex];
    if (!weapon) return;

    const weaponTemplates = await loadTemplatesForLobby('weapon');
    const weaponTemplate = weaponTemplates.find(t => t.id == weapon.templateId);
    if (!weaponTemplate || !weaponTemplate.attributes?.fixedMagazine) {
        showNotification('Это оружие использует сменные магазины');
        return;
    }

    const caliber = getItemCaliber(weaponTemplate);
    const maxAmmo = weaponTemplate.attributes?.magazine_size || 0;
    const currentAmmo = weapon.ammo || 0;
    const needed = maxAmmo - currentAmmo;
    if (needed <= 0) {
        showNotification('Магазин полон');
        return;
    }

    // 1. Собираем спидлоадеры
    const loaderItems = [];
    const collectLoaders = (items, path) => {
        if (!Array.isArray(items)) return;
        items.forEach((item, idx) => {
            if (
                isAmmoLoadingDevice(item)
                && isLoaderCompatible(item, caliber)
                && canLoadFixedWeaponFromDevice(weaponTemplate, item)
            ) {
                const total = item.ammo ? item.ammo.reduce((sum, a) => sum + a.quantity, 0) : 0;
                if (total > 0) {
                    loaderItems.push({ item, path: path.concat(idx) });
                }
            }
            if (item.contents) collectLoaders(item.contents, path.concat(idx, 'contents'));
        });
    };
    collectLoaders(currentCharacterData.inventory?.backpack, ['inventory', 'backpack']);
    collectLoaders(currentCharacterData.inventory?.pockets, ['inventory', 'pockets']);
    const beltPouches = currentCharacterData.equipment?.belt?.pouches || [];
    beltPouches.forEach((pouch, i) => collectLoaders(pouch.contents, ['equipment', 'belt', 'pouches', i, 'contents']));
    const vestPouches = currentCharacterData.equipment?.vest?.pouches || [];
    vestPouches.forEach((pouch, i) => collectLoaders(pouch.contents, ['equipment', 'vest', 'pouches', i, 'contents']));

    // 2. Собираем обычные патроны
    const ammoItems = [];
    const collectAmmo = (items, path) => {
        if (!Array.isArray(items)) return;
        items.forEach((item, idx) => {
            if (['ammo', 'grenade'].includes(item.category) && getItemCaliber(item) === caliber && item.quantity > 0) {
                ammoItems.push({ item, path: path.concat(idx) });
            }
            if (item.contents) collectAmmo(item.contents, path.concat(idx, 'contents'));
        });
    };
    collectAmmo(currentCharacterData.inventory?.backpack, ['inventory', 'backpack']);
    collectAmmo(currentCharacterData.inventory?.pockets, ['inventory', 'pockets']);
    beltPouches.forEach((pouch, i) => collectAmmo(pouch.contents, ['equipment', 'belt', 'pouches', i, 'contents']));
    vestPouches.forEach((pouch, i) => collectAmmo(pouch.contents, ['equipment', 'vest', 'pouches', i, 'contents']));

    if (loaderItems.length === 0 && ammoItems.length === 0) {
        showNotification(`Нет подходящих спидлоадеров или патронов калибра ${caliber}`);
        return;
    }

    // Создаём модальное окно с двумя секциями
    let modal = document.getElementById('fixed-reload-modal');
    if (modal) modal.remove();

    modal = document.createElement('div');
    modal.id = 'fixed-reload-modal';
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content" style="max-height: 80vh; overflow-y: auto;">
            <span class="close" onclick="document.getElementById('fixed-reload-modal').remove()">&times;</span>
            <h3>Выберите способ зарядки</h3>
            <div id="loader-section" style="margin-bottom:15px;">
                <h4>Спидлоадеры/ленты</h4>
                <select id="loader-select" class="form-control" size="3"></select>
            </div>
            <div id="ammo-section">
                <h4>Патроны</h4>
                <select id="fixed-ammo-select" class="form-control" size="5"></select>
            </div>
            <div id="fixed-reload-preview" style="margin-top:12px; padding:10px 12px; border:1px solid var(--border-color, #45483e); border-radius:6px; background:rgba(0,0,0,.18);"></div>
            <div class="form-actions" style="margin-top:15px;">
                <button class="btn btn-primary" id="confirm-fixed-reload-btn">Зарядить</button>
                ${window.locationCombatState?.status !== 'active' ? '<button class="btn btn-success" id="reload-fixed-full-btn">Зарядить до полного</button>' : ''}
                <button class="btn btn-secondary" onclick="document.getElementById('fixed-reload-modal').remove()">Отмена</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    const loaderSelect = modal.querySelector('#loader-select');
    const loaderSection = modal.querySelector('#loader-section');
    loaderSelect.innerHTML = '';
    if (loaderItems.length > 0) {
        loaderItems.forEach((entry, idx) => {
            const total = entry.item.ammo.reduce((sum, a) => sum + a.quantity, 0);
            const opt = document.createElement('option');
            opt.value = idx;
            opt.textContent = `${entry.item.name} (${total} патр.)`;
            loaderSelect.appendChild(opt);
        });
        loaderSection.style.display = 'block';
    } else {
        loaderSection.style.display = 'none';
    }

    const ammoSelect = modal.querySelector('#fixed-ammo-select');
    ammoSelect.innerHTML = '';
    ammoItems.forEach((entry, idx) => {
        const opt = document.createElement('option');
        opt.value = idx;
        opt.textContent = `${entry.item.name} (${ammoSourceCount(entry.item)} патр.)`;
        ammoSelect.appendChild(opt);
    });
    const preview = modal.querySelector('#fixed-reload-preview');
    let previewVersion = 0;
    const selectedReloadSource = () => {
        const loaderIndex = loaderSelect.value;
        const ammoIndex = ammoSelect.value;
        if (loaderIndex !== '' && loaderItems.length > 0) return loaderItems[loaderIndex];
        if (ammoIndex !== '' && ammoItems.length > 0) return ammoItems[ammoIndex];
        return null;
    };
    const updateReloadPreview = async () => {
        const version = ++previewVersion;
        const selected = selectedReloadSource();
        if (!selected) {
            renderReloadPreview(preview, null, []);
            return;
        }
        const paymentRows = [];
        if (window.locationCombatState?.status === 'active') {
            const state = currentCharacterData.combatMagazineLoading || {};
            const prepared = state.targetType === 'fixed' && Number(state.weaponIndex) === Number(weaponIndex);
            const sourceKey = selected.item.id || selected.path.join('.');
            const sameSource = prepared && state.sourceId === sourceKey;
            const plans = fixedMagazineLoadingPlans(weaponTemplate, selected.item, needed, prepared);
            const sourcePayments = sameSource
                ? null
                : await inventoryItemPreparationPayments(selected.item, selected.path, 'ammo');
            if (version !== previewVersion || !modal.isConnected) return;
            plans.forEach(plan => {
                const groups = [];
                if (!prepared && !plan.includesStart) {
                    groups.push([{ actionPoints: 1, freeActions: 0 }]);
                }
                if (sourcePayments) groups.push(sourcePayments);
                groups.push(plan.payments);
                paymentRows.push({
                    label: plan.label,
                    payment: formatCombatPaymentVariants(combineCombatPayments(groups)),
                });
            });
        }
        renderReloadPreview(preview, selected.item, paymentRows);
    };
    loaderSelect.addEventListener('change', () => {
        if (loaderSelect.value !== '') ammoSelect.selectedIndex = -1;
        updateReloadPreview();
    });
    ammoSelect.addEventListener('change', () => {
        if (ammoSelect.value !== '') loaderSelect.selectedIndex = -1;
        updateReloadPreview();
    });
    if (ammoItems.length > 0) {
        ammoSelect.selectedIndex = 0;
        loaderSelect.selectedIndex = -1;
    } else if (loaderItems.length > 0) {
        loaderSelect.selectedIndex = 0;
        ammoSelect.selectedIndex = -1;
    }
    await updateReloadPreview();

    const fullReloadButton = modal.querySelector('#reload-fixed-full-btn');
    if (fullReloadButton) {
        fullReloadButton.onclick = async () => {
            const selectedLoaderIdx = loaderSelect.value;
            const selectedAmmoIdx = ammoSelect.value;
            const selected = selectedLoaderIdx !== '' && loaderItems.length > 0
                ? loaderItems[selectedLoaderIdx]
                : (selectedAmmoIdx !== '' && ammoItems.length > 0 ? ammoItems[selectedAmmoIdx] : null);
            if (!selected) {
                showNotification('Выберите подавач, ленту или патроны');
                return;
            }
            const amount = Math.min(needed, ammoSourceCount(selected.item));
            if (amount <= 0) {
                showNotification(`Нет патронов калибра ${caliber}`);
                return;
            }
            const result = await submitAmmunitionLoading({
                weapon_index: weaponIndex, source_path: selected.path, full: true,
                selection: { weapon, source_path: selected.item },
            });
            if (result) modal.remove();
        };
    }

    // Кнопка подтверждения с замыканием нужных данных
    modal.querySelector('#confirm-fixed-reload-btn').onclick = async () => {
        const selectedLoaderIdx = loaderSelect.value;
        const selectedAmmoIdx = ammoSelect.value;

        const maxAmmo = weaponTemplate.attributes?.magazine_size || 0;
        const currentAmmo = weapon.ammo || 0;
        const needed = maxAmmo - currentAmmo;
        if (needed <= 0) {
            showNotification('Магазин уже полон');
            modal.remove();
            return;
        }

        const selected = selectedLoaderIdx !== '' && loaderItems.length > 0
            ? loaderItems[selectedLoaderIdx]
            : (selectedAmmoIdx !== '' && ammoItems.length > 0 ? ammoItems[selectedAmmoIdx] : null);
        if (!selected) {
            showNotification('Выберите подавач, ленту или патроны');
            return;
        }

        const state = currentCharacterData.combatMagazineLoading || {};
        const prepared = state.targetType === 'fixed' && Number(state.weaponIndex) === Number(weaponIndex);
        const sourceKey = selected.item.id || selected.path.join('.');
        const sameSource = prepared && state.sourceId === sourceKey;
        const plans = fixedMagazineLoadingPlans(weaponTemplate, selected.item, needed, prepared);
        if (!plans.length) {
            showNotification('Недостаточно патронов для выбранного способа зарядки');
            return;
        }
        const plan = await chooseConsumableApplication('Способ перезарядки', plans);
        if (!plan) return;

        const paymentGroups = [];
        if (!prepared && !plan.includesStart) {
            paymentGroups.push([{ actionPoints: 1, freeActions: 0 }]);
        }
        if (!sameSource) {
            paymentGroups.push(await inventoryItemPreparationPayments(selected.item, selected.path, 'ammo'));
        }
        paymentGroups.push(plan.payments);
        const result = await submitAmmunitionLoading({
            weapon_index: weaponIndex, source_path: selected.path, quantity: plan.quantity,
            selection: { weapon, source_path: selected.item },
        }, paymentGroups);
        if (result) modal.remove();
    };

    modal.style.display = 'flex';
};

window.confirmFixedReload = async function() {
    return document.querySelector('#confirm-fixed-reload-btn')?.onclick?.();
};

function updateArmorContainerSlots(newSlotCount) {
    const armor = currentCharacterData.equipment?.armor;
    if (!armor) return;
    const current = armor.containers || [];
    if (newSlotCount > current.length) {
        for (let i = current.length; i < newSlotCount; i++) {
            current.push({ item: null });
        }
    } else if (newSlotCount < current.length) {
        armor.containers = current.slice(0, newSlotCount);
    }
    renderEquipmentTab(currentCharacterData);
    scheduleAutoSave();
}

// Добавить предмет (контейнер или артефакт) в слот брони
window.addItemToArmorContainer = async function(containerIndex) {
    const armor = currentCharacterData.equipment?.armor;
    if (!armor || !armor.containers) return;
    const slot = armor.containers[containerIndex];
    if (slot.item) {
        showNotification('Слот уже занят');
        return;
    }

    // Собираем контейнеры и артефакты
    const candidates = [];
    const collect = (items, path) => {
        if (!Array.isArray(items)) return;
        items.forEach((it, idx) => {
            if (it.category === 'container' || it.category === 'artifact') {
                candidates.push({ item: it, path: path.concat(idx) });
            }
            if (it.contents) collect(it.contents, path.concat(idx, 'contents'));
        });
    };
    collect(currentCharacterData.inventory?.backpack, ['inventory', 'backpack']);
    collect(currentCharacterData.inventory?.pockets, ['inventory', 'pockets']);
    const beltPouches = currentCharacterData.equipment?.belt?.pouches || [];
    beltPouches.forEach((pouch, i) => collect(pouch.contents, ['equipment', 'belt', 'pouches', i, 'contents']));
    const vestPouches = currentCharacterData.equipment?.vest?.pouches || [];
    vestPouches.forEach((pouch, i) => collect(pouch.contents, ['equipment', 'vest', 'pouches', i, 'contents']));

    if (candidates.length === 0) {
        showNotification('Нет подходящих предметов (контейнеров или артефактов)');
        return;
    }

    // Модальное окно
    const oldModal = document.getElementById('armor-container-select-modal');
    if (oldModal) oldModal.remove();
    const modal = document.createElement('div');
    modal.id = 'armor-container-select-modal';
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content">
            <span class="close" onclick="this.closest('.modal').remove()">&times;</span>
            <h3>Выберите предмет для слота ${containerIndex+1}</h3>
            <select id="armor-container-select" class="form-control" size="5"></select>
            <div class="form-actions" style="margin-top:15px;">
                <button class="btn btn-primary" id="confirm-armor-container">Вставить</button>
                <button class="btn btn-secondary" onclick="this.closest('.modal').remove()">Отмена</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    const select = modal.querySelector('#armor-container-select');
    candidates.forEach((entry, idx) => {
        const opt = document.createElement('option');
        opt.value = idx;
        opt.textContent = `${entry.item.name} (${entry.item.category === 'container' ? 'Контейнер' : 'Артефакт'})`;
        select.appendChild(opt);
    });
    modal.querySelector('#confirm-armor-container').onclick = async () => {
        const idx = select.value;
        if (idx === '') return;
        const selected = candidates[idx];
        modal.remove();

        if (!removeItemByPath(selected.path)) {
            showNotification('Не удалось найти предмет в инвентаре');
            return;
        }

        slot.item = selected.item;
        await renderEquipmentTab(currentCharacterData);
        await renderInventoryTab(currentCharacterData);
        scheduleAutoSave();
        forceSyncCharacter();
        showNotification('Предмет помещён в контейнер брони', 'success');
    };
    modal.style.display = 'flex';
};

// Извлечь предмет из слота брони
window.removeArmorContainerItem = async function(containerIndex) {
    const armor = currentCharacterData.equipment?.armor;
    if (!armor || !armor.containers) return;
    const slot = armor.containers[containerIndex];
    if (!slot.item) return;
    const item = slot.item;
    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
    currentCharacterData.inventory.backpack.push(item);
    slot.item = null;
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Предмет извлечён из контейнера брони', 'success');
};

function isArtifactContainer(item) {
    if (item.category !== 'container') return false;
    const allTemplates = allTemplatesCache || [];
    const template = allTemplates.find(t => t.id === item.templateId);
    if (!template) return false;
    const slots = template.attributes?.slots || [];
    return slots.some(s => s.type === 'artifact');
}

// Просмотр содержимого контейнера (если это контейнер)
window.openContainerContents = function(containerIndex) {
    const armor = currentCharacterData.equipment?.armor;
    if (!armor || !armor.containers) return;
    const slot = armor.containers[containerIndex];
    if (!slot.item || slot.item.category !== 'container') return;
    const installed = slot.item.installedModules || [];
    const artifact = installed.find(m => m.slotType === 'artifact');
    if (artifact) {
        showNotification(`В контейнере: ${artifact.name}`, 'system');
    } else {
        showNotification('Контейнер пуст');
    }
};

function updateAmmoWeight(ammoItem) {
    const qty = ammoItem.quantity || 0;
    if (qty === 0) {
        ammoItem.weight = 0;
    } else {
        const singleVolume = ammoItem.volume || 0.02;
        const occupiedVolume = singleVolume * qty;
        ammoItem.weight = (occupiedVolume < 0.5) ? 0.1 : 0.25;
    }
}

window.changeMagazineAmmo = async function(pathStr, delta) {
    const path = pathStr.split(',').map(p => isNaN(p) ? p : parseInt(p));
    const mag = getItemByPath(path);
    if (!mag || mag.category !== 'magazine') return;
    if (isAmmoFeederTool(mag)) {
        showNotification('Подавач не хранит патроны');
        return;
    }

    const cap = mag.attributes?.capacity || 30;
    const totalAmmo = mag.ammo ? mag.ammo.reduce((sum, a) => sum + a.quantity, 0) : 0;

    // Определяем родительский контейнер
    const parentPath = path.slice(0, -1);
    const parent = parentPath.length === 0 ? currentCharacterData : getItemByPath(parentPath);
    let targetArray;
    if (Array.isArray(parent)) targetArray = parent;
    else if (parent?.contents) targetArray = parent.contents;
    else if (parent?.backpack) targetArray = parent.backpack;
    else targetArray = currentCharacterData.inventory.backpack;

    if (delta > 0) {
        return window.reloadMagazineFromInventory(pathStr);
    } else if (delta < 0) {
        // -1: извлечь один патрон из магазина
        if (totalAmmo <= 0) { showNotification('Магазин пуст'); return; }
        if (!mag.ammo || mag.ammo.length === 0) { showNotification('Нет данных о патронах'); return; }

        // Извлекаем последний добавленный тип (LIFO)
        const last = mag.ammo[mag.ammo.length - 1];
        const templateId = last.templateId;
        const allTemplates = await getAllItemTemplates();
        const ammoTemplate = allTemplates.find(t => t.id === templateId);
        if (!ammoTemplate) { showNotification('Шаблон патронов не найден'); return; }

        // Уменьшаем количество в магазине
        last.quantity -= 1;
        if (last.quantity <= 0) mag.ammo.pop();

        const returnedAmmo = createItemFromTemplate(ammoTemplate);
        applyAmmoVariantToItem(returnedAmmo, ammoTemplate, last.ammo_variant || null);
        addAmmoToInventorySlots(targetArray, returnedAmmo, 1);

        updateMagazineWeight(mag);
        showNotification(`-1 патрон (${last.name})`, 'system', 'bottom-left');
    }

    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
};

window.reloadMagazineFromInventory = async function(pathStr) {
    if (secondaryWeaponIndex()!=null) {showNotification('Для зарядки магазина сначала освободите руку','system');return;}
    const path = pathStr.split(',').map(p => isNaN(p) ? p : parseInt(p));
    const mag = getItemByPath(path);
    if (!mag || mag.category !== 'magazine') return;
    if (isAmmoFeederTool(mag)) {
        showNotification('Подавач используется при зарядке другого магазина');
        return;
    }

    const cap = mag.attributes?.capacity || 30;
    const cur = getMagazineAmmoCount(mag);
    const needed = cap - cur;
    if (needed <= 0) { showNotification('Магазин полон'); return; }

    const caliber = getItemCaliber(mag);
    const loadingTarget = isAmmoLoadingDevice(mag);
    if (!caliber && !loadingTarget) {
        showNotification('У магазина не указан калибр');
        return;
    }

    // Собираем ВСЕ подходящие патроны (включая разные типы)
    const ammoItems = [];
    const collectAmmo = (items, path) => {
        if (!Array.isArray(items)) return;
        items.forEach((item, idx) => {
            if (
                item !== mag
                &&
                (
                    ['ammo', 'grenade'].includes(item.category)
                    || isAmmoLoadingDevice(item)
                )
                && (
                    !caliber
                        ? ['ammo', 'grenade'].includes(item.category)
                        : (isAmmoLoadingDevice(item)
                            ? canLoadMagazineFromDevice(mag, item, caliber)
                            : getItemCaliber(item) === caliber)
                )
                && ammoSourceCount(item) > 0
            ) {
                ammoItems.push({ item, path: path.concat(idx) });
            }
            if (item.contents) collectAmmo(item.contents, path.concat(idx, 'contents'));
        });
    };
    collectAmmo(currentCharacterData.inventory?.backpack, ['inventory', 'backpack']);
    collectAmmo(currentCharacterData.inventory?.pockets, ['inventory', 'pockets']);
    const beltPouches = currentCharacterData.equipment?.belt?.pouches || [];
    beltPouches.forEach((pouch, i) => collectAmmo(pouch.contents, ['equipment', 'belt', 'pouches', i, 'contents']));
    const vestPouches = currentCharacterData.equipment?.vest?.pouches || [];
    vestPouches.forEach((pouch, i) => collectAmmo(pouch.contents, ['equipment', 'vest', 'pouches', i, 'contents']));

    if (ammoItems.length === 0) {
        showNotification(`Нет патронов калибра ${caliber}`);
        return;
    }

    // Создаём модальное окно выбора
    let modal = document.getElementById('ammo-select-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'ammo-select-modal';
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content">
                <span class="close" onclick="document.getElementById('ammo-select-modal').style.display='none'">&times;</span>
                <h3>Выберите патроны</h3>
                <select id="ammo-select" class="form-control" size="5"></select>
                <div id="inventory-magazine-reload-preview" style="margin-top:12px; padding:10px 12px; border:1px solid var(--border-color, #45483e); border-radius:6px; background:rgba(0,0,0,.18);"></div>
                <div class="form-actions" style="margin-top:15px;">
                    <button class="btn btn-primary" onclick="confirmReloadMagazine('${pathStr}')">Зарядить</button>
                    <button class="btn btn-success" id="reload-inventory-full-btn">Зарядить до полного</button>
                    <button class="btn btn-secondary" onclick="document.getElementById('ammo-select-modal').style.display='none'">Отмена</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }
    const select = document.getElementById('ammo-select');
    select.innerHTML = '';
    ammoItems.forEach((entry, idx) => {
        const opt = document.createElement('option');
        opt.value = idx;
        opt.textContent = `${entry.item.name} (${ammoSourceCount(entry.item)} патр.)`;
        select.appendChild(opt);
    });
    modal._ammoList = ammoItems;
    modal._magPath = pathStr;
    select.selectedIndex = ammoItems.length > 0 ? 0 : -1;
    const reloadPreview = modal.querySelector('#inventory-magazine-reload-preview');
    let previewVersion = 0;
    const updateReloadPreview = async () => {
        const version = ++previewVersion;
        const selected = ammoItems[select.value];
        if (!selected) {
            renderReloadPreview(reloadPreview, null, []);
            return;
        }
        const paymentRows = [];
        if (window.locationCombatState?.status === 'active') {
            const state = currentCharacterData.combatMagazineLoading || {};
            const targetKey = mag.id || path.join('.');
            const sourceKey = selected.item.id || selected.path.join('.');
            const sameMagazine = state.targetType === 'inventory' && state.targetId === targetKey;
            const sameSource = sameMagazine && state.sourceId === sourceKey;
            const feederTools = collectInventoryEntries(currentCharacterData, item => isAmmoFeederTool(item));
            const plans = magazineLoadingPlans(selected.item, needed, mag, feederTools.length > 0);
            const targetPayments = sameMagazine
                ? null
                : await inventoryItemPreparationPayments(mag, path, 'magazine');
            const sourcePayments = sameSource
                ? null
                : await inventoryItemPreparationPayments(selected.item, selected.path, 'ammo');
            if (version !== previewVersion || !modal.isConnected) return;

            for (const plan of plans) {
                const baseGroups = [];
                if (targetPayments) baseGroups.push(targetPayments);
                if (sourcePayments) baseGroups.push(sourcePayments);
                const variants = [];
                if (plan.usesFeeder && feederTools.length) {
                    for (const feeder of feederTools) {
                        const feederKey = feeder.item.id || feeder.path.join('.');
                        const sameFeeder = sameMagazine && state.feederId === feederKey;
                        const feederPayments = sameFeeder
                            ? null
                            : await inventoryItemPreparationPayments(feeder.item, feeder.path, 'ammo');
                        if (version !== previewVersion || !modal.isConnected) return;
                        const groups = [...baseGroups];
                        if (feederPayments) groups.push(feederPayments);
                        groups.push(plan.payments);
                        variants.push(...combineCombatPayments(groups));
                    }
                } else {
                    variants.push(...combineCombatPayments([...baseGroups, plan.payments]));
                }
                paymentRows.push({
                    label: plan.label,
                    payment: formatCombatPaymentVariants(variants),
                });
            }
        }
        renderReloadPreview(reloadPreview, selected.item, paymentRows);
    };
    select.onchange = updateReloadPreview;
    await updateReloadPreview();
    const fullReloadButton = modal.querySelector('#reload-inventory-full-btn');
    if (fullReloadButton) {
        fullReloadButton.style.display = window.locationCombatState?.status === 'active' ? 'none' : '';
        fullReloadButton.onclick = async () => {
            const selected = modal._ammoList[select.value];
            const targetPath = modal._magPath.split(',').map(p => isNaN(p) ? p : parseInt(p));
            const target = getItemByPath(targetPath);
            if (!selected || !target) return;
            const amount = Math.min(
                Math.max(0, (target.attributes?.capacity || 30) - getMagazineAmmoCount(target)),
                ammoSourceCount(selected.item),
            );
            if (amount <= 0) {
                showNotification('Магазин уже полон или источник пуст');
                return;
            }
            const result = await submitAmmunitionLoading({
                target_path: targetPath, source_path: selected.path, full: true,
                selection: { target_path: target, source_path: selected.item },
            });
            if (result) modal.style.display = 'none';
        };
    }
    modal.style.display = 'flex';
};

window.confirmReloadMagazine = async function(pathStr) {
    const modal = document.getElementById('ammo-select-modal');
    const select = document.getElementById('ammo-select');
    const selected = modal._ammoList[select.value];
    if (!selected) return;

    const targetPath = modal._magPath.split(',').map(p => isNaN(p) ? p : parseInt(p));
    const mag = getItemByPath(targetPath);
    if (!mag) return;

    const cap = mag.attributes?.capacity || 30;
    const totalAmmo = mag.ammo ? mag.ammo.reduce((sum, a) => sum + a.quantity, 0) : 0;
    const needed = cap - totalAmmo;
    const ammoItem = selected.item;
    const feederTools = collectInventoryEntries(
        currentCharacterData,
        item => isAmmoFeederTool(item)
    );
    const plans = magazineLoadingPlans(ammoItem, needed, mag, feederTools.length > 0);
    if (!plans.length) {
        showNotification('Недостаточно патронов для выбранного способа зарядки');
        return;
    }
    const plan = await chooseConsumableApplication('Способ зарядки', plans);
    if (!plan) return;

    const state = currentCharacterData.combatMagazineLoading || {};
    const targetKey = mag.id || targetPath.join('.');
    const sourceKey = ammoItem.id || selected.path.join('.');
    const sameMagazine = state.targetType === 'inventory' && state.targetId === targetKey;
    const sameSource = sameMagazine && state.sourceId === sourceKey;
    let feederTool = null;
    let feederKey = null;
    let sameFeeder = false;
    if (plan.usesFeeder) {
        feederTool = feederTools.length === 1
            ? feederTools[0]
            : await chooseConsumableApplication(
                'Выберите подавач',
                feederTools.map(entry => ({
                    ...entry,
                    label: entry.item.name,
                }))
            );
        if (!feederTool) return;
        feederKey = feederTool.item.id || feederTool.path.join('.');
        sameFeeder = sameMagazine && state.feederId === feederKey;
    }
    const paymentGroups = [];
    if (!sameMagazine) {
        paymentGroups.push(await inventoryItemPreparationPayments(mag, targetPath, 'magazine'));
    }
    if (!sameSource) {
        paymentGroups.push(await inventoryItemPreparationPayments(ammoItem, selected.path, 'ammo'));
    }
    if (feederTool && !sameFeeder) {
        paymentGroups.push(await inventoryItemPreparationPayments(feederTool.item, feederTool.path, 'ammo'));
    }
    paymentGroups.push(plan.payments);
    const result = await submitAmmunitionLoading({
        target_path: targetPath, source_path: selected.path, quantity: plan.quantity,
        ...(feederTool ? { feeder_path: feederTool.path } : {}),
        selection: { target_path: mag, source_path: selected.item, ...(feederTool ? { feeder_path: feederTool.item } : {}) },
    }, paymentGroups);
    if (result) modal.style.display = 'none';
};

async function returnAmmoStacksToInventory(ammoStacks, targetArray) {
    const allTemplates = await getAllItemTemplates();
    for (const ammoEntry of ammoStacks) {
        const quantity = Math.max(0, Number(ammoEntry?.quantity || 0));
        if (quantity <= 0) continue;
        const template = allTemplates.find(t => t.id == ammoEntry.templateId);
        const category = template?.category || ammoEntry.category || 'ammo';
        const newAmmo = template
            ? createItemFromTemplate(template)
            : {
                ...ammoEntry,
                attributes: { ...(ammoEntry.attributes || {}) },
                category,
            };
        if (template) {
            applyAmmoVariantToItem(newAmmo, template, ammoEntry.ammo_variant || null);
        }
        addAmmoToInventorySlots(targetArray, newAmmo, quantity);
    }
}

window.unloadFixedMagazine = async function(weaponIndex) {
    const weapon = currentCharacterData.weapons?.[weaponIndex];
    if (!weapon) return;
    const weaponTemplates = await loadTemplatesForLobby('weapon');
    const weaponTemplate = weaponTemplates.find(template => template.id == weapon.templateId);
    if (!weaponTemplate?.attributes?.fixedMagazine) {
        showNotification('Это оружие использует сменный магазин');
        return;
    }

    const ammoStacks = Array.isArray(weapon.fixedAmmo)
        ? weapon.fixedAmmo.filter(stack => Number(stack?.quantity) > 0)
        : [];
    const legacyAmmoCount = Math.max(0, Number(weapon.ammo || 0));
    if (!ammoStacks.length) {
        showNotification(
            legacyAmmoCount > 0
                ? 'Нельзя разрядить старую запись: тип патронов не указан'
                : 'Магазин пуст'
        );
        return;
    }

    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!Array.isArray(currentCharacterData.inventory.backpack)) {
        currentCharacterData.inventory.backpack = [];
    }
    await returnAmmoStacksToInventory(ammoStacks, currentCharacterData.inventory.backpack);
    weapon.fixedAmmo = [];
    weapon.ammo = 0;
    const loadingState = currentCharacterData.combatMagazineLoading;
    if (
        loadingState?.targetType === 'fixed'
        && Number(loadingState.weaponIndex) === Number(weaponIndex)
    ) {
        delete currentCharacterData.combatMagazineLoading;
    }

    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Несъёмный магазин разряжен', 'success');
};

window.unloadMagazineToInventory = async function(pathStr) {
    const path = pathStr.split(',').map(p => isNaN(p) ? p : parseInt(p));
    const mag = getItemByPath(path);
    if (!mag || mag.category !== 'magazine') return;
    if (!mag.ammo || mag.ammo.length === 0) { showNotification('Магазин пуст'); return; }

    const parentPath = path.slice(0, -1);
    const parent = parentPath.length === 0 ? currentCharacterData : getItemByPath(parentPath);
    let targetArray;
    if (Array.isArray(parent)) targetArray = parent;
    else if (parent?.contents) targetArray = parent.contents;
    else if (parent?.backpack) targetArray = parent.backpack;
    else targetArray = currentCharacterData.inventory.backpack;

    await returnAmmoStacksToInventory(mag.ammo, targetArray);

    mag.ammo = [];
    updateMagazineWeight(mag);

    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Магазин разряжен', 'success');
};

function updateMagazineWeight(mag) {
    const totalAmmo = mag.ammo ? mag.ammo.reduce((sum, a) => sum + a.quantity, 0) : 0;
    mag.weight = (totalAmmo > 0) ? (mag.loadedWeight || 0.25) : (mag.emptyWeight || 0);
};

function getMagazineAmmoCount(mag) {
    if (!mag || !Array.isArray(mag.ammo)) return 0;
    return mag.ammo.reduce((sum, ammoEntry) => sum + (Number(ammoEntry?.quantity) || 0), 0);
}

function getAmmoStackKey(ammoItem) {
    return [
        ammoItem?.templateId ?? '',
        normalizeAmmoVariant(ammoItem?.attributes?.ammo_variant || ammoItem?.ammo_variant || ammoItem?.attributes?.ammo_kind || ammoItem?.attributes?.special_version || ammoItem?.attributes?.effect) || ''
    ].join('::');
}

function formatAmmoStackLabel(ammoItem) {
    if (!ammoItem) return '';
    const variant = normalizeAmmoVariant(ammoItem?.attributes?.ammo_variant || ammoItem?.ammo_variant || ammoItem?.attributes?.ammo_kind || ammoItem?.attributes?.special_version || ammoItem?.attributes?.effect);
    return variant ? `${ammoItem.name} (${getAmmoVariantLabel(variant)})` : ammoItem.name;
}


window.openVisorModificationsModal = async function(itemPathStr, slotType) {
    const targetPath = JSON.parse(itemPathStr);
    const helmet = getItemByPath(targetPath);
    if (!helmet || !helmet.installedModules) {
        showNotification('Шлем или забрало не найдены');
        return;
    }
    const visor = helmet.installedModules.find(m => m.slotType === slotType);
    if (!visor) {
        showNotification('Забрало не установлено');
        return;
    }

    const modTemplates = await loadTemplatesForLobby('modification');

    const oldModal = document.getElementById('visor-modifications-modal');
    if (oldModal) oldModal.remove();

    const modal = document.createElement('div');
    modal.id = 'visor-modifications-modal';
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content">
            <span class="close" onclick="this.closest('.modal').remove()">&times;</span>
            <h3>Модификации забрала</h3>
            <div id="installed-mods-list"></div>
            <hr>
            <h4>Добавить модификацию</h4>
            <select id="visor-mod-select" class="form-control">
                <option value="">-- Выберите --</option>
                ${modTemplates.map(t => `<option value="${t.id}">${t.name}</option>`).join('')}
            </select>
            <div class="form-actions" style="margin-top:15px;">
                <button class="btn btn-primary" id="add-mod-btn">Добавить</button>
                <button class="btn btn-secondary" id="close-modal-btn">Закрыть</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    const installedList = modal.querySelector('#installed-mods-list');
    const select = modal.querySelector('#visor-mod-select');
    const addBtn = modal.querySelector('#add-mod-btn');
    const closeBtn = modal.querySelector('#close-modal-btn');

    function renderInstalled() {
        const installed = visor.modifications || [];
        installedList.innerHTML = installed.length ? installed.map((mod, idx) => `
            <div style="display: flex; align-items: center; gap: 5px; margin-bottom: 5px;">
                <span style="flex:1;">${escapeHtml(mod.name)}</span>
                <button type="button" class="btn btn-sm btn-danger" data-mod-index="${idx}">✕</button>
            </div>
        `).join('') : '<p>Нет установленных модификаций</p>';

        installedList.querySelectorAll('[data-mod-index]').forEach(btn => {
            btn.onclick = () => {
                const idx = parseInt(btn.dataset.modIndex, 10);
                visor.modifications.splice(idx, 1);
                renderInstalled(); // обновляем список
                scheduleAutoSave();
                forceSyncCharacter();
                showNotification('Модификация удалена', 'success');
            };
        });
    }

    renderInstalled();

    addBtn.onclick = async () => {
        const templateId = select.value;
        if (!templateId) return;

        const template = modTemplates.find(t => t.id == templateId);
        if (!template) return;

        if (!visor.modifications) visor.modifications = [];
        visor.modifications.push({
            id: generateItemId(),
            templateId: template.id,
            name: template.name,
            attributes: { ...template.attributes }
        });

        renderInstalled(); // обновляем список
        select.value = '';  // сбрасываем выбор
        scheduleAutoSave();
        forceSyncCharacter();
        showNotification(`Модификация "${template.name}" добавлена`, 'success');
    };

    closeBtn.onclick = () => modal.remove();
    modal.querySelector('.close').onclick = () => modal.remove();

    modal.style.display = 'flex';
};

window.selectWeaponModel = async function(index) {
    const select = document.getElementById(`weapon-model-select-${index}`);
    const selectedId = parseInt(select.value, 10);
    if (isNaN(selectedId)) return;

    if (!currentCharacterData.weapons) currentCharacterData.weapons = [];
    const weapon = currentCharacterData.weapons[index];

    const templates = [
        ...(await loadTemplatesForLobby('weapon')),
        ...(await loadTemplatesForLobby('melee_weapon')),
    ];
    const template = templates.find(t => t.id === selectedId);
    if (!template) return;

    const mapping = {
        'accuracy': 'accuracy',
        'noise': 'noise',
        'caliber': 'caliber',
        'range': 'range',
        'ergonomics': 'ergonomics',
        'burst': 'burst',
        'damage': 'damage',
        'durability': 'durability',
        'fireRate': 'fire_rate',
        'weight': 'weight'
    };
    applyTemplateToObject(weapon, template, mapping);
    const maximumDurability = Number(
        template.attributes?.max_durability
        ?? template.attributes?.durability
        ?? 100
    ) || 100;
    weapon.maxDurability = maximumDurability;
    weapon.durability = Number(
        template.attributes?.durability ?? maximumDurability
    );
    weapon.model = template.name;
    weapon.createdByPlayer = !window.isGM;

    await renderEquipmentTab(currentCharacterData);
    scheduleAutoSave();
};

function resetEquipmentPreset(type) {
    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    const previous = currentCharacterData.equipment[type];
    const installedModules = Array.isArray(previous?.installedModules)
        ? previous.installedModules
        : [];
    if (installedModules.length) {
        if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
        if (!Array.isArray(currentCharacterData.inventory.backpack)) {
            currentCharacterData.inventory.backpack = [];
        }
        currentCharacterData.inventory.backpack.push(...installedModules);
    }

    const blank = {
        templateId: null,
        name: '',
        weight: 0,
        volume: 0,
        durability: 0,
        maxDurability: 0,
        stage: 1,
        stageDurability: 0,
        currentStageDurability: 0,
        material: '',
        accuracyPenalty: 0,
        ergonomicsPenalty: 0,
        charismaBonus: 0,
        movementPenalty: 0,
        containerSlots: 0,
        protection: {
            physical: 0,
            chemical: 0,
            thermal: 0,
            electric: 0,
            radiation: 0,
        },
        protectionZones: [],
        modifications: [],
        installedModules: [],
        containers: [],
        powered: false,
    };
    currentCharacterData.equipment[type] = blank;
    if (
        type === 'armor'
        && currentCharacterData.equipment?.helmet?.integratedWithArmor
    ) {
        delete currentCharacterData.equipment.helmet;
    }
    return blank;
}

window.fillHelmetFromPreset = async function(select) {
    const selectedId = parseInt(select.value, 10);
    if (isNaN(selectedId)) {
        resetEquipmentPreset('helmet');
        await renderEquipmentTab(currentCharacterData);
        await renderInventoryTab(currentCharacterData);
        scheduleAutoSave();
        return;
    }

    const templates = await loadTemplatesForLobby('helmet');
    const template = templates.find(t => t.id === selectedId);
    if (!template) return;

    const currentHelmet = currentCharacterData.equipment?.helmet || {};
    if (!await canEquipHelmetWithCurrentGasMask(template, currentHelmet)) {
        select.value = currentHelmet.templateId || '';
        return;
    }
    const helmet = resetEquipmentPreset('helmet');
    const mapping = {
        'accuracyPenalty': 'accuracy_penalty',
        'ergonomicsPenalty': 'ergonomics_penalty',
        'charismaBonus': 'charisma_bonus',
        'movementPenalty': 'movement_penalty',
        'protection': 'protection'
    };
    applyTemplateToObject(helmet, template, mapping);
    helmet.templateId = template.id;
    helmet.name = template.name;
    helmet.weight = template.weight;
    helmet.volume = template.volume;
    helmet.material = template.attributes?.material || template.attributes?.armor_type || helmet.material || 'Текстиль';
    helmet.createdByPlayer = !window.isGM;

    initArmorStagedDurability(helmet, template);

    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    currentCharacterData.equipment.helmet = helmet;
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
};

window.fillGasMaskFromPreset = async function(select) {
    const selectedId = parseInt(select.value, 10);
    if (isNaN(selectedId)) {
        resetEquipmentPreset('gasMask');
        await renderEquipmentTab(currentCharacterData);
        await renderInventoryTab(currentCharacterData);
        scheduleAutoSave();
        return;
    }

    const templates = await loadTemplatesForLobby('gas_mask');
    const template = templates.find(t => t.id === selectedId);
    if (!template) return;

    if (!await canEquipGasMaskWithCurrentHelmet()) {
        select.value = currentCharacterData.equipment?.gasMask?.templateId || '';
        return;
    }

    const gasMask = resetEquipmentPreset('gasMask');
    const mapping = {
        'accuracyPenalty': 'accuracy_penalty',
        'ergonomicsPenalty': 'ergonomics_penalty',
        'charismaBonus': 'charisma_bonus',
        'protection': 'protection'
    };
    applyTemplateToObject(gasMask, template, mapping);
    gasMask.templateId = template.id;
    gasMask.name = template.name;
    gasMask.weight = template.weight;
    gasMask.volume = template.volume;
    gasMask.material = template.attributes?.material || template.attributes?.armor_type || gasMask.material || 'Текстиль';
    gasMask.createdByPlayer = !window.isGM;
    gasMask.isWorn = gasMask.isWorn || false;

    gasMask.maxDurability = template.attributes?.max_durability || gasMask.maxDurability || 1;
    gasMask.durability = gasMask.maxDurability;
    delete gasMask.stage;
    delete gasMask.stageDurability;
    delete gasMask.currentStageDurability;
    delete gasMask.condition;

    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    currentCharacterData.equipment.gasMask = gasMask;
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
};

window.fillArmorFromPreset = async function(select) {
    const selectedId = parseInt(select.value, 10);
    if (isNaN(selectedId)) {
        resetEquipmentPreset('armor');
        await renderEquipmentTab(currentCharacterData);
        await renderInventoryTab(currentCharacterData);
        await renderSkillsTab(currentCharacterData);
        scheduleAutoSave();
        return;
    }

    const templates = await loadTemplatesForLobby('armor');
    const template = templates.find(t => t.id === selectedId);
    if (!template) return;
    const equippedHelmet = currentCharacterData.equipment?.helmet;
    if (
        armorHasIntegratedHelmet(null, template)
        && (
            currentCharacterData.equipment?.gasMask?.templateId
            || (equippedHelmet?.templateId && !equippedHelmet.integratedWithArmor)
        )
    ) {
        showNotification('Сначала снимите обычный шлем и противогаз');
        select.value = currentCharacterData.equipment?.armor?.templateId || '';
        return;
    }

    const armor = resetEquipmentPreset('armor');
    const mapping = {
        'movementPenalty': 'movement_penalty',
        'containerSlots': 'container_slots',
        'protection': 'protection'
    };
    applyTemplateToObject(armor, template, mapping);
    armor.templateId = template.id;
    armor.name = template.name;
    armor.weight = template.weight;
    armor.volume = template.volume;
    armor.material = template.attributes?.material || armor.material || 'Текстиль';
    armor.createdByPlayer = !window.isGM;
    armor.protectionZones = [...(template.attributes?.protection_zones || [])];
    armor.integratedHelmet = armorHasIntegratedHelmet(armor, template);
    armor.isExoskeleton = Boolean(template.attributes?.is_exoskeleton) || template.name === 'Экзоскелет';
    armor.requiresExoskeletonBattery = Boolean(template.attributes?.requires_exoskeleton_battery || armor.isExoskeleton);
    armor.powered = armor.isExoskeleton ? false : (template.attributes?.powered ?? armor.powered ?? false);

    const containerSlots = template.attributes?.container_slots || 0;
    armor.containers = Array(containerSlots).fill().map(() => ({ item: null }));

    initArmorStagedDurability(armor, template);

    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    currentCharacterData.equipment.armor = armor;
    syncIntegratedArmorHelmet(armor, template);
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    await renderSkillsTab(currentCharacterData);
    scheduleAutoSave();
};

window.updateArmorStageFromSelect = function(select, type) {
    const newStage = parseInt(select.value, 10);
    const item = currentCharacterData.equipment[type];
    if (!item) return;
    item.stage = newStage;
    const stageNames = ['1. Целая', '2. Немного повреждена', '3. Повреждена', '4. Сильно повреждена', '5. Поломана'];
    item.condition = stageNames[newStage - 1];
    item.stageDurability = calculateStageDurability(item.durability || 0, item.material || 'Текстиль');
    renderEquipmentTab(currentCharacterData);
    scheduleAutoSave();
};

async function refreshCharacterAfterEquipmentAction(data = null) {
    if (data) {
        currentCharacterData = data;
    } else {
        const character = await Server.getCharacter(currentCharacterId);
        currentCharacterData = character.data || {};
    }
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    await renderSkillsTab(currentCharacterData);
}

async function runEquipmentAction(operation, slot, itemPath = null) {
    const combatState = window.locationCombatState;
    let result;
    if (combatState?.status === 'active') {
        const actor = combatState.current_character;
        if (!actor || Number(actor.character_id) !== Number(currentCharacterId)) {
            throw new Error('Сейчас не ход этого персонажа');
        }
        const item = itemPath ? getItemByPath(itemPath) : null;
        const access = item
            ? await calculateInventoryAccess(item, itemPath)
            : { retrievalActionPoints: 0 };
        const pendingActionId = `equipment-${actor.location_character_id}-${Date.now()}`;
        const payload = {
            location_character_id: actor.location_character_id,
            action_key: 'change_equipment',
            equipment_operation: operation,
            equipment_slot: slot,
            item_path: itemPath,
            inventory_retrieval_action_points: access.retrievalActionPoints,
            pending_action_id: pendingActionId,
        };
        result = await Server.performLocationCombatAction(
            window.currentLobbyId,
            window.currentLocationId,
            payload,
        );
        if (result?.pending_action) {
            const sceneModule = await import('./locationScene.js');
            sceneModule.registerDeferredCombatAction(result.pending_action_id, payload);
            showNotification('Смена экипировки начата и продолжится в следующем ходу', 'system');
            return result;
        }
        await refreshCharacterAfterEquipmentAction();
    } else {
        result = await Server.changeCharacterEquipment(currentCharacterId, {
            operation,
            slot,
            item_path: itemPath,
        });
        await refreshCharacterAfterEquipmentAction(result.data);
    }
    const details = result?.equipment_change || {};
    const duration = Number(details.duration_minutes || 0);
    const verb = operation === 'equip' ? 'надета' : 'снята';
    showNotification(
        duration
            ? `Экипировка ${verb}. Длительность операции: ${duration} мин.`
            : `Экипировка ${verb}`,
        'success',
    );
    return result;
}

async function runEquipmentModuleAction(operation, targetPath, slotType, itemPath = null) {
    await Server.waitForCharacterSave(currentCharacterId);
    const target = getItemByPath(targetPath);
    const source = itemPath ? getItemByPath(itemPath) : null;
    const identity = item => item ? Object.fromEntries(
        ['id', 'templateId', 'name', 'category']
            .filter(key => item[key] !== undefined)
            .map(key => [key, item[key]])
    ) : null;
    const selection = { target: identity(target), source: identity(source) };
    const combatState = window.locationCombatState;
    let result;
    if (combatState?.status === 'active') {
        const actor = combatState.current_character;
        if (!actor || Number(actor.character_id) !== Number(currentCharacterId)) {
            throw new Error('Сейчас не ход этого персонажа');
        }
        const pendingActionId = `equipment-module-${actor.location_character_id}-${Date.now()}`;
        const payload = {
            location_character_id: actor.location_character_id,
            action_key: 'change_equipment_module',
            module_operation: operation,
            module_slot_type: slotType,
            module_target_path: targetPath,
            module_selection: selection,
            item_path: itemPath,
            pending_action_id: pendingActionId,
        };
        result = await Server.performLocationCombatAction(
            window.currentLobbyId,
            window.currentLocationId,
            payload,
        );
        if (result?.pending_action) {
            const sceneModule = await import('./locationScene.js');
            sceneModule.registerDeferredCombatAction(result.pending_action_id, payload);
            showNotification('Установка фильтра начата и продолжится в следующем ходу', 'system');
            return result;
        }
        await refreshCharacterAfterEquipmentAction();
    } else {
        result = await Server.changeCharacterEquipmentModule(currentCharacterId, {
            operation,
            slot_type: slotType,
            target_path: targetPath,
            module_selection: selection,
            item_path: itemPath,
            character_revision: currentCharacterData._revision,
        });
        await refreshCharacterAfterEquipmentAction(result.data);
    }
    showNotification(operation === 'install' ? 'Фильтр установлен' : 'Фильтр снят', 'success');
    return result;
}

window.equipArmorFromInventory = async function(itemPath) {
    try {
        return await runEquipmentAction('equip', 'armor', itemPath);
    } catch (error) {
        showNotification(error.message || 'Не удалось надеть броню');
        return;
    }
};

async function legacyEquipArmorFromInventory(itemPath) {
    const item = getItemByPath(itemPath);
    if (!item || item.category !== 'armor') {
        showNotification('Этот предмет нельзя надеть как броню');
        return;
    }
    const templates = await loadTemplatesForLobby('armor');
    const template = templates.find(t => t.id === item.templateId);
    if (!template) {
        showNotification('Шаблон брони не найден');
        return;
    }
    const equippedHelmet = currentCharacterData.equipment?.helmet;
    if (
        armorHasIntegratedHelmet(item, template)
        && (
            currentCharacterData.equipment?.gasMask?.templateId
            || (equippedHelmet?.templateId && !equippedHelmet.integratedWithArmor)
        )
    ) {
        showNotification('Сначала снимите обычный шлем и противогаз');
        return;
    }
    const armorToEquip = {
        templateId: template.id,
        createdByPlayer: Boolean(item.createdByPlayer),
        name: template.name,
        weight: template.weight,
        volume: template.volume,
        material: item.material || template.attributes?.material || 'Текстиль',
        protection: item.protection || { ...template.attributes?.protection },
        movementPenalty: item.movementPenalty ?? template.attributes?.movement_penalty ?? 0,
        containerSlots: item.containerSlots || template.attributes?.container_slots || 0,
        modifications: item.modifications || [],
        installedModules: item.installedModules ? [...item.installedModules] : [],
        protectionZones: [...(item.protectionZones || template.attributes?.protection_zones || [])],
        integratedHelmet: armorHasIntegratedHelmet(item, template),
        isExoskeleton: Boolean(item.isExoskeleton || template.attributes?.is_exoskeleton || template.name === 'Экзоскелет'),
        requiresExoskeletonBattery: Boolean(
            item.requiresExoskeletonBattery
            || template.attributes?.requires_exoskeleton_battery
            || template.name === 'Экзоскелет'
        ),
        powered: false,
    };

    const containerSlots = template.attributes?.container_slots || 0;
    armorToEquip.containers = Array(containerSlots).fill().map(() => ({ item: null }));

    initArmorStagedDurability(armorToEquip, template);
    if (item.durability !== undefined) {
        armorToEquip.durability = item.durability;
        armorToEquip.maxDurability = item.maxDurability || template.attributes?.max_durability || 100;
        armorToEquip.stage = item.stage || 1;
        armorToEquip.condition = item.condition || '1. Целая';
        armorToEquip.currentStageDurability = item.currentStageDurability ?? armorToEquip.stageDurability;
    }
    if (!removeItemByPath(itemPath)) {
        showNotification('Не удалось найти предмет в инвентаре');
        return;
    }
    const oldArmor = currentCharacterData.equipment?.armor;
    if (oldArmor && oldArmor.templateId) {
        const oldTemplates = await loadTemplatesForLobby('armor');
        const oldTemplate = oldTemplates.find(t => t.id === oldArmor.templateId);
        if (oldTemplate) {
            const oldItem = createItemFromTemplate(oldTemplate);
            oldItem.durability = oldArmor.durability;
            oldItem.maxDurability = oldArmor.maxDurability;
            oldItem.material = oldArmor.material;
            oldItem.stage = oldArmor.stage;
            oldItem.condition = oldArmor.condition;
            oldItem.currentStageDurability = oldArmor.currentStageDurability;
            oldItem.protection = { ...oldArmor.protection };
            oldItem.modifications = oldArmor.modifications || [];
            oldItem.installedModules = oldArmor.installedModules
                ? [...oldArmor.installedModules]
                : [];
            restoreItemToPath(oldItem, itemPath);
        }
    }
    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    currentCharacterData.equipment.armor = armorToEquip;
    syncIntegratedArmorHelmet(armorToEquip, template);
    renderEquipmentTab(currentCharacterData);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Броня надета', 'success');
};

window.equipHelmetFromInventory = async function(itemPath) {
    try {
        return await runEquipmentAction('equip', 'helmet', itemPath);
    } catch (error) {
        showNotification(error.message || 'Не удалось надеть шлем');
        return;
    }
};

async function legacyEquipHelmetFromInventory(itemPath) {
    const item = getItemByPath(itemPath);
    if (!item || item.category !== 'helmet') {
        showNotification('Этот предмет нельзя надеть как шлем');
        return;
    }
    const templates = await loadTemplatesForLobby('helmet');
    const template = templates.find(t => t.id === item.templateId);
    if (!template) {
        showNotification('Шаблон шлема не найден');
        return;
    }
    if (!await canEquipHelmetWithCurrentGasMask(template, item)) return;
    const helmetToEquip = {
        templateId: template.id,
        createdByPlayer: Boolean(item.createdByPlayer),
        name: template.name,
        weight: template.weight,
        volume: template.volume,
        material: item.material || template.attributes?.material || 'Текстиль',
        protection: item.protection || { ...template.attributes?.protection },
        accuracyPenalty: item.accuracyPenalty || template.attributes?.accuracy_penalty || 0,
        ergonomicsPenalty: item.ergonomicsPenalty || template.attributes?.ergonomics_penalty || 0,
        charismaBonus: item.charismaBonus || template.attributes?.charisma_bonus || 0,
        movementPenalty: item.movementPenalty ?? template.attributes?.movement_penalty ?? 0,
        modifications: item.modifications || [],
        installedModules: item.installedModules ? [...item.installedModules] : []
    };
    initArmorStagedDurability(helmetToEquip, template);
    if (item.durability !== undefined) {
        helmetToEquip.durability = item.durability;
        helmetToEquip.maxDurability = item.maxDurability || template.attributes?.max_durability || 100;
        helmetToEquip.stage = item.stage || 1;
        helmetToEquip.condition = item.condition || '1. Целая';
        helmetToEquip.currentStageDurability = item.currentStageDurability ?? helmetToEquip.stageDurability;
    }
    if (!removeItemByPath(itemPath)) {
        showNotification('Не удалось найти предмет в инвентаре');
        return;
    }
    const oldHelmet = currentCharacterData.equipment?.helmet;
    if (oldHelmet && oldHelmet.templateId) {
        const oldTemplates = await loadTemplatesForLobby('helmet');
        const oldTemplate = oldTemplates.find(t => t.id === oldHelmet.templateId);
        if (oldTemplate) {
            const oldItem = createItemFromTemplate(oldTemplate);
            oldItem.durability = oldHelmet.durability;
            oldItem.maxDurability = oldHelmet.maxDurability;
            oldItem.material = oldHelmet.material;
            oldItem.stage = oldHelmet.stage;
            oldItem.condition = oldHelmet.condition;
            oldItem.currentStageDurability = oldHelmet.currentStageDurability;
            oldItem.protection = { ...oldHelmet.protection };
            oldItem.movementPenalty = oldHelmet.movementPenalty || 0;
            oldItem.modifications = oldHelmet.modifications || [];
            oldItem.installedModules = oldHelmet.installedModules || [];
            restoreItemToPath(oldItem, itemPath);
        }
    }
    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    currentCharacterData.equipment.helmet = helmetToEquip;
    renderEquipmentTab(currentCharacterData);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Шлем надет', 'success');
};

window.equipGasMaskFromInventory = async function(itemPath) {
    try {
        return await runEquipmentAction('equip', 'gasMask', itemPath);
    } catch (error) {
        showNotification(error.message || 'Не удалось надеть противогаз');
        return;
    }
};

async function legacyEquipGasMaskFromInventory(itemPath) {
    const item = getItemByPath(itemPath);
    if (!item || item.category !== 'gas_mask') {
        showNotification('Этот предмет нельзя надеть как противогаз');
        return;
    }
    const templates = await loadTemplatesForLobby('gas_mask');
    const template = templates.find(t => t.id === item.templateId);
    if (!template) {
        showNotification('Шаблон противогаза не найден');
        return;
    }
    if (!await canEquipGasMaskWithCurrentHelmet()) return;
    const gasMaskToEquip = {
        templateId: template.id,
        createdByPlayer: Boolean(item.createdByPlayer),
        name: template.name,
        weight: template.weight,
        volume: template.volume,
        material: item.material || template.attributes?.material || 'Текстиль',
        protection: item.protection || { ...template.attributes?.protection },
        accuracyPenalty: item.accuracyPenalty || template.attributes?.accuracy_penalty || 0,
        ergonomicsPenalty: item.ergonomicsPenalty || template.attributes?.ergonomics_penalty || 0,
        charismaBonus: item.charismaBonus || template.attributes?.charisma_bonus || 0,
        modifications: item.modifications || [],
        installedModules: item.installedModules ? [...item.installedModules] : [],
        isWorn: item.isWorn || false
    };
    gasMaskToEquip.maxDurability = template.attributes?.max_durability || 1;
    gasMaskToEquip.durability = gasMaskToEquip.maxDurability;
    if (item.durability !== undefined) {
        gasMaskToEquip.durability = item.durability;
        gasMaskToEquip.maxDurability = item.maxDurability || template.attributes?.max_durability || 100;
    }
    if (!removeItemByPath(itemPath)) {
        showNotification('Не удалось найти предмет в инвентаре');
        return;
    }
    const oldGasMask = currentCharacterData.equipment?.gasMask;
    if (oldGasMask && oldGasMask.templateId) {
        const oldTemplates = await loadTemplatesForLobby('gas_mask');
        const oldTemplate = oldTemplates.find(t => t.id === oldGasMask.templateId);
        if (oldTemplate) {
            const oldItem = createItemFromTemplate(oldTemplate);
            oldItem.durability = oldGasMask.durability;
            oldItem.maxDurability = oldGasMask.maxDurability;
            oldItem.material = oldGasMask.material;
            oldItem.protection = { ...oldGasMask.protection };
            oldItem.modifications = oldGasMask.modifications || [];
            oldItem.installedModules = oldGasMask.installedModules || [];
            restoreItemToPath(oldItem, itemPath);
        }
    }
    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    currentCharacterData.equipment.gasMask = gasMaskToEquip;
    renderEquipmentTab(currentCharacterData);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Противогаз надет', 'success');
};

window.equipWeaponFromInventory = async function(itemPath) {
    const item = getItemByPath(itemPath);
    if (!item || item.category !== 'weapon') {
        showNotification('Этот предмет нельзя экипировать как оружие');
        return;
    }

    const templates = await loadTemplatesForLobby('weapon');
    const template = templates.find(t => t.id === item.templateId);
    if (!template) {
        showNotification('Шаблон оружия не найден');
        return;
    }

    // Копируем модули с нормализацией модификаторов (минимальное изменение)
    const installedModulesCopy = (item.installedModules || []).map(mod => {
        const modCopy = { ...mod };
        // Если модификаторы лежат в attributes.modifiers, переносим на верхний уровень
        if (modCopy.attributes?.modifiers && !modCopy.modifiers) {
            modCopy.modifiers = { ...modCopy.attributes.modifiers };
        }
        // Глубокое копирование вложенных объектов, чтобы избежать ссылок
        if (modCopy.modifiers) modCopy.modifiers = { ...modCopy.modifiers };
        if (modCopy.attributes) modCopy.attributes = { ...modCopy.attributes };
        return modCopy;
    });

    // Создаём объект оружия для экипировки (всё как было)
    const weaponToEquip = {
        ...structuredClone(item),
        id: item.id || generateItemId(),
        templateId: template.id,
        createdByPlayer: Boolean(item.createdByPlayer),
        name: template.name,
        model: template.name,
        weight: item.weight ?? template.weight,
        volume: item.volume ?? template.volume,
        accuracy: item.accuracy ?? template.attributes?.accuracy ?? 0,
        noise: item.noise ?? template.attributes?.noise ?? 0,
        range: item.range ?? template.attributes?.range ?? 0,
        ergonomics: item.ergonomics ?? template.attributes?.ergonomics ?? 0,
        burst: item.burst || template.attributes?.burst || '',
        fireModes: item.fireModes || item.attributes?.fire_modes || template.attributes?.fire_modes || null,
        damage: item.damage ?? template.attributes?.damage ?? 0,
        durability: item.durability ?? template.attributes?.durability ?? template.attributes?.max_durability ?? 100,
        maxDurability: item.maxDurability ?? template.attributes?.max_durability ?? 100,
        fireRate: item.fireRate ?? template.attributes?.fire_rate ?? 0,
        caliber: item.caliber || template.attributes?.caliber,
        modifications: item.modifications || [],
        installedModules: installedModulesCopy,
        installedMagazine: item.installedMagazine || null,
        fixedAmmo: Array.isArray(item.fixedAmmo) ? item.fixedAmmo.map(stack => ({ ...stack })) : [],
        ammo: item.ammo || 0,
        jam: item.jam ? { ...item.jam } : null,
        jams: Array.isArray(item.jams) ? item.jams.map(jam => ({ ...jam })) : [],
        requiresManualCycle: Boolean(item.requiresManualCycle)
    };

    if (!removeItemByPath(itemPath)) {
        showNotification('Не удалось найти оружие в инвентаре');
        return;
    }

    // Добавляем в массив оружия
    if (!currentCharacterData.weapons) currentCharacterData.weapons = [];
    currentCharacterData.weapons.push(weaponToEquip);

    renderEquipmentTab(currentCharacterData);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Оружие экипировано', 'success');
};

window.equipMeleeWeaponFromInventory = async function(itemPath) {
    const item = getItemByPath(itemPath);
    if (!item || item.category !== 'melee_weapon') {
        showNotification('Этот предмет нельзя экипировать как оружие ближнего боя');
        return;
    }
    const templates = await loadTemplatesForLobby('melee_weapon');
    const template = templates.find(t => t.id === item.templateId);
    if (!template) {
        showNotification('Шаблон оружия не найден');
        return;
    }

    const weaponToEquip = {
        ...structuredClone(item),
        id: item.id || generateItemId(),
        templateId: item.templateId || template.id,
        createdByPlayer: Boolean(item.createdByPlayer),
        name: template.name,
        category: 'melee_weapon',
        weight: item.weight ?? template.weight,
        volume: item.volume ?? template.volume,
        // Состояние
        durability: item.durability ?? template.attributes?.durability ?? 100,
        maxDurability: item.maxDurability ?? template.attributes?.max_durability ?? 100,
        stage: item.stage ?? 1,
        condition: item.condition ?? '1. Целая',
        material: item.material ?? template.attributes?.material ?? 'Текстиль',
        stageDurability: item.stageDurability ?? calculateStageDurability(
            item.durability ?? template.attributes?.durability ?? 100,
            item.material ?? template.attributes?.material ?? 'Текстиль'
        ),
        currentStageDurability: item.currentStageDurability ?? item.stageDurability ?? 0,
        // Характеристики
        damage: item.damage ?? template.attributes?.damage ?? 0,
        accuracy: item.accuracy ?? template.attributes?.accuracy ?? 0,
        armorPiercing: item.armorPiercing ?? template.attributes?.armor_piercing ?? 0,
        bleeding: item.bleeding ?? template.attributes?.bleeding ?? 'Нет',
        weightClass: item.weightClass ?? template.attributes?.weight_class ?? 'Легкое',
        size: item.size ?? template.attributes?.size ?? 1,
        modifications: item.modifications || [],
        installedModules: item.installedModules ? [...item.installedModules] : []
    };

    if (!removeItemByPath(itemPath)) {
        showNotification('Не удалось найти оружие в инвентаре');
        return;
    }

    if (!currentCharacterData.weapons) currentCharacterData.weapons = [];
    currentCharacterData.weapons.push(weaponToEquip);

    renderEquipmentTab(currentCharacterData);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Оружие ближнего боя экипировано', 'success');
};

window.equipBeltFromInventory = async function(itemPath) {
    const item = getItemByPath(itemPath);
    if (!item || item.category !== 'belt') {
        showNotification('Этот предмет нельзя надеть как пояс');
        return;
    }

    const templates = await loadTemplatesForLobby('belt');
    const template = templates.find(t => t.id === item.templateId);
    if (!template) {
        showNotification('Шаблон пояса не найден');
        return;
    }

    const beltToEquip = {
        createdByPlayer: Boolean(item.createdByPlayer),
        templateId: template.id,
        name: template.name,
        weight: template.weight,
        volume: template.volume,
        pouches: item.pouches || template.attributes?.pouches || [],
        modifications: item.modifications || [],
        storedItem: item.storedItem || null
    };

    if (!removeItemByPath(itemPath)) {
        showNotification('Не удалось найти предмет в инвентаре');
        return;
    }

    const oldBelt = currentCharacterData.equipment?.belt;
    if (oldBelt && oldBelt.templateId) {
        const oldTemplates = await loadTemplatesForLobby('belt');
        const oldTemplate = oldTemplates.find(t => t.id === oldBelt.templateId);
        if (oldTemplate) {
            const oldItem = createItemFromTemplate(oldTemplate);
            oldItem.pouches = oldBelt.pouches || [];
            oldItem.modifications = oldBelt.modifications || [];
            oldItem.storedItem = oldBelt.storedItem || null;
            restoreItemToPath(oldItem, itemPath);
        }
    }

    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    currentCharacterData.equipment.belt = beltToEquip;

    renderEquipmentTab(currentCharacterData);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Пояс надет', 'success');
};

window.equipVestFromInventory = async function(itemPath) {
    const item = getItemByPath(itemPath);
    if (!item || item.category !== 'vest') {
        showNotification('Этот предмет нельзя надеть как разгрузку');
        return;
    }

    const templates = await loadTemplatesForLobby('vest');
    const template = templates.find(t => t.id === item.templateId);
    if (!template) {
        showNotification('Шаблон разгрузки не найден');
        return;
    }

    const vestToEquip = {
        createdByPlayer: Boolean(item.createdByPlayer),
        templateId: template.id,
        name: template.name,
        weight: template.weight,
        volume: template.volume,
        model: template.id ? String(template.id) : (item.model || 'custom'),
        totalCapacity: item.totalCapacity || template.attributes?.total_capacity || 0,
        pouches: item.pouches || template.attributes?.pouches || [],
        modifications: item.modifications || []
    };

    if (!removeItemByPath(itemPath)) {
        showNotification('Не удалось найти предмет в инвентаре');
        return;
    }

    const oldVest = currentCharacterData.equipment?.vest;
    if (oldVest && oldVest.templateId) {
        const oldTemplates = await loadTemplatesForLobby('vest');
        const oldTemplate = oldTemplates.find(t => t.id === oldVest.templateId);
        if (oldTemplate) {
            const oldItem = createItemFromTemplate(oldTemplate);
            oldItem.model = oldVest.model;
            oldItem.totalCapacity = oldVest.totalCapacity;
            oldItem.pouches = oldVest.pouches || [];
            oldItem.modifications = oldVest.modifications || [];
            restoreItemToPath(oldItem, itemPath);
        }
    }

    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    currentCharacterData.equipment.vest = vestToEquip;

    renderEquipmentTab(currentCharacterData);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Разгрузка надета', 'success');
};

window.equipBackpackFromInventory = async function(itemPath) {
    const item = getItemByPath(itemPath);
    if (!item || item.category !== 'backpack') {
        showNotification('Этот предмет нельзя надеть как рюкзак');
        return;
    }
    currentCharacterData.equipment = currentCharacterData.equipment || {};
    if (currentCharacterData.equipment.backpack) {
        showNotification('Сначала снимите экипированный рюкзак');
        return;
    }

    const contents = Array.isArray(item.contents) ? item.contents : [];
    const backpackToEquip = {
        ...item,
        quantity: 1,
        isContainer: true,
        isEquippable: true,
    };
    delete backpackToEquip.contents;

    if (Number(item.quantity || 1)>1 && contents.length) {
        showNotification('Сначала разделите стопку рюкзаков с содержимым');
        return;
    }
    if (Number(item.quantity || 1)>1) {
        item.quantity-=1;
        backpackToEquip.id=generateItemId();
    } else if (!removeItemByPath(itemPath)) {
        showNotification('Не удалось найти рюкзак в инвентаре');
        return;
    }

    currentCharacterData.inventory = currentCharacterData.inventory || {};
    const unplacedItems = Array.isArray(currentCharacterData.inventory.backpack) ? currentCharacterData.inventory.backpack : [];
    currentCharacterData.inventory.backpack = [...unplacedItems, ...contents];
    delete currentCharacterData.inventory.backpackModel;
    currentCharacterData.equipment.backpack = backpackToEquip;

    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Рюкзак экипирован', 'success');
};

window.unequipBackpack = async function() {
    const backpack = currentCharacterData.equipment?.backpack;
    if (!backpack) {
        showNotification('Рюкзак не экипирован');
        return;
    }

    currentCharacterData.inventory = currentCharacterData.inventory || {};
    currentCharacterData.inventory.pockets = Array.isArray(currentCharacterData.inventory.pockets)
        ? currentCharacterData.inventory.pockets
        : [];
    const contents = Array.isArray(currentCharacterData.inventory.backpack)
        ? currentCharacterData.inventory.backpack
        : [];
    currentCharacterData.inventory.pockets.push({
        ...backpack,
        quantity: 1,
        contents,
        isContainer: true,
        isEquippable: true,
    });
    currentCharacterData.inventory.backpack = [];
    delete currentCharacterData.inventory.backpackModel;
    delete currentCharacterData.equipment.backpack;

    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Рюкзак снят вместе с содержимым', 'success');
};

window.equipDetectorFromInventory = async function(itemPath) {
    const item = getItemByPath(itemPath);
    if (!item || (item.category !== 'device' && item.category !== 'detector')) {
        showNotification('Этот предмет нельзя надеть как детектор');
        return;
    }
    if (item.subcategory !== 'anomaly_detector' && item.attributes?.type !== 'anomaly') {
        showNotification('Можно надеть только детектор аномалий');
        return;
    }

    if (!removeItemByPath(itemPath)) {
        showNotification('Не удалось найти предмет в инвентаре');
        return;
    }

    // Снимаем старый детектор
    const oldDetector = currentCharacterData.equipment?.detector;
    if (oldDetector && oldDetector.templateId) {
        const isExistingDuplicate = oldDetector.id && item.id && oldDetector.id === item.id;
        if (!isExistingDuplicate) restoreItemToPath(oldDetector, itemPath);
    }

    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    item.bonus = item.bonus ?? item.attributes?.bonus ?? 0;
    item.installedModules = Array.isArray(item.installedModules) ? item.installedModules : [];
    currentCharacterData.equipment.detector = item;

    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Детектор аномалий надет', 'success');
};

window.equipToBeltFromInventory = async function(itemPath) {
    const item = getItemByPath(itemPath);
    if (!item || (item.category !== 'helmet' && item.category !== 'gas_mask')) {
        showNotification('На пояс можно повесить только шлем или противогаз');
        return;
    }

    // Проверяем, надет ли пояс
    const belt = currentCharacterData.equipment?.belt;
    if (!belt || !belt.templateId) {
        showNotification('Сначала наденьте пояс');
        return;
    }

    // Удаляем предмет из инвентаря
    if (!removeItemByPath(itemPath)) {
        showNotification('Не удалось найти предмет в инвентаре');
        return;
    }

    // Если на поясе уже что-то висело, возвращаем в инвентарь
    const oldStored = belt.storedItem;
    if (oldStored) {
        const oldTemplates = await loadTemplatesForLobby(oldStored.type);
        const oldTemplate = oldTemplates.find(t => t.id === oldStored.templateId);
        if (oldTemplate) {
            const oldItem = createItemFromTemplate(oldTemplate);
            // Копируем сохранённые характеристики (если были)
            Object.assign(oldItem, oldStored.savedAttributes || {});
            restoreItemToPath(oldItem, itemPath);
        }
    }

    // Сохраняем предмет на пояс
    belt.storedItem = {
        type: item.category,
        templateId: item.templateId,
        name: item.name,
        savedAttributes: {
            durability: item.durability,
            maxDurability: item.maxDurability,
            modifications: item.modifications,
            installedModules: item.installedModules
            // добавьте другие важные поля при необходимости
        },
        sourcePath: itemPath
    };

    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification(`${item.name} помещён на пояс`, 'success');
};

window.equipHeadphonesFromInventory = async function(itemPath) {
    const item = getItemByPath(itemPath);
    if (!item || item.category !== 'headphones') {
        showNotification('Этот предмет нельзя надеть как наушники');
        return;
    }
    const templates = await loadTemplatesForLobby('headphones');
    const template = templates.find(t => t.id === item.templateId);
    if (!template) {
        showNotification('Шаблон наушников не найден');
        return;
    }
    const headphonesToEquip = {
        createdByPlayer: Boolean(item.createdByPlayer),
        templateId: template.id,
        name: template.name,
        deafeningCoef: item.deafeningCoef ?? template.attributes?.deafening_coef ?? 0,
        noiseAbsorption: item.noiseAbsorption ?? template.attributes?.noise_absorption ?? 0,
        awarenessBonus: item.awarenessBonus ?? template.attributes?.awareness_bonus ?? 0,
    };
    if (!removeItemByPath(itemPath)) {
        showNotification('Не удалось найти предмет в инвентаре');
        return;
    }
    // Снимаем старые наушники, если были
    const oldHeadphones = currentCharacterData.equipment?.headphones;
    if (oldHeadphones && oldHeadphones.templateId) {
        const oldTemplates = await loadTemplatesForLobby('headphones');
        const oldTemplate = oldTemplates.find(t => t.id === oldHeadphones.templateId);
        if (oldTemplate) {
            const oldItem = createItemFromTemplate(oldTemplate);
            oldItem.deafeningCoef = oldHeadphones.deafeningCoef;
            oldItem.noiseAbsorption = oldHeadphones.noiseAbsorption;
            oldItem.awarenessBonus = oldHeadphones.awarenessBonus;
            restoreItemToPath(oldItem, itemPath);
        }
    }
    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    currentCharacterData.equipment.headphones = headphonesToEquip;
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Наушники надеты', 'success');
};

window.unequipHeadphones = async function() {
    const headphones = currentCharacterData.equipment?.headphones;
    if (!headphones || !headphones.templateId) {
        showNotification('Наушники не надеты');
        return;
    }
    const templates = await loadTemplatesForLobby('headphones');
    const template = templates.find(t => t.id === headphones.templateId);
    if (!template) {
        showNotification('Шаблон наушников не найден');
        return;
    }
    const restoredItem = createItemFromTemplate(template);
    restoredItem.deafeningCoef = headphones.deafeningCoef;
    restoredItem.noiseAbsorption = headphones.noiseAbsorption;
    restoredItem.awarenessBonus = headphones.awarenessBonus;
    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
    currentCharacterData.inventory.backpack.push(restoredItem);
    delete currentCharacterData.equipment.headphones;
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Наушники сняты', 'success');
};

window.equipGlassesFromInventory = async function(itemPath) {
    const item = getItemByPath(itemPath);
    if (!item || item.category !== 'glasses') {
        showNotification('Этот предмет нельзя надеть как очки');
        return;
    }
    // Проверка совместимости с противогазом
    if (item.attributes?.incompatible_with_gasmask && currentCharacterData.equipment?.gasMask) {
        showNotification('Нельзя надеть эти очки вместе с противогазом');
        return;
    }
    // Проверка совместимости с забралом шлема
    if (item.attributes?.incompatible_with_visor) {
        const helmet = currentCharacterData.equipment?.helmet;
        if (helmet && helmet.installedModules?.some(m => m.slotType === 'visor')) {
            showNotification('Нельзя надеть эти очки вместе со шлемом, имеющим забрало');
            return;
        }
    }
    const templates = await loadTemplatesForLobby('glasses');
    const template = templates.find(t => t.id === item.templateId);
    if (!template) {
        showNotification('Шаблон очков не найден');
        return;
    }
    const glassesToEquip = {
        createdByPlayer: Boolean(item.createdByPlayer),
        templateId: template.id,
        name: template.name,
        charismaBonus: item.charismaBonus ?? template.attributes?.charisma_bonus ?? 0,
        flashProtection: item.flashProtection ?? template.attributes?.flash_protection ?? 1,
        eyePhysicalProtection: item.eyePhysicalProtection ?? template.attributes?.eye_physical_protection ?? 0,
    };
    if (!removeItemByPath(itemPath)) {
        showNotification('Не удалось найти предмет в инвентаре');
        return;
    }
    const oldGlasses = currentCharacterData.equipment?.glasses;
    if (oldGlasses && oldGlasses.templateId) {
        const oldTemplates = await loadTemplatesForLobby('glasses');
        const oldTemplate = oldTemplates.find(t => t.id === oldGlasses.templateId);
        if (oldTemplate) {
            const oldItem = createItemFromTemplate(oldTemplate);
            oldItem.charismaBonus = oldGlasses.charismaBonus;
            oldItem.flashProtection = oldGlasses.flashProtection;
            oldItem.eyePhysicalProtection = oldGlasses.eyePhysicalProtection;
            restoreItemToPath(oldItem, itemPath);
        }
    }
    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    currentCharacterData.equipment.glasses = glassesToEquip;
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Очки надеты', 'success');
};

window.unequipGlasses = async function() {
    const glasses = currentCharacterData.equipment?.glasses;
    if (!glasses || !glasses.templateId) {
        showNotification('Очки не надеты');
        return;
    }
    const templates = await loadTemplatesForLobby('glasses');
    const template = templates.find(t => t.id === glasses.templateId);
    if (!template) {
        showNotification('Шаблон очков не найден');
        return;
    }
    const restoredItem = createItemFromTemplate(template);
    restoredItem.charismaBonus = glasses.charismaBonus;
    restoredItem.flashProtection = glasses.flashProtection;
    restoredItem.eyePhysicalProtection = glasses.eyePhysicalProtection;
    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
    currentCharacterData.inventory.backpack.push(restoredItem);
    delete currentCharacterData.equipment.glasses;
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Очки сняты', 'success');
};

window.equipGlovesFromInventory = async function(itemPath) {
    const item = getItemByPath(itemPath);
    if (!item || item.category !== 'gloves') {
        showNotification('Этот предмет нельзя надеть как перчатки');
        return;
    }
    const templates = await loadTemplatesForLobby('gloves');
    const template = templates.find(t => t.id === item.templateId);
    if (!template) {
        showNotification('Шаблон перчаток не найден');
        return;
    }
    const glovesToEquip = {
        createdByPlayer: Boolean(item.createdByPlayer),
        templateId: template.id,
        name: template.name,
        charismaBonus: item.charismaBonus ?? template.attributes?.charisma_bonus ?? 0,
        effect: item.effect ?? template.attributes?.effect ?? '',
    };
    if (!removeItemByPath(itemPath)) {
        showNotification('Не удалось найти предмет в инвентаре');
        return;
    }
    const oldGloves = currentCharacterData.equipment?.gloves;
    if (oldGloves && oldGloves.templateId) {
        const oldTemplates = await loadTemplatesForLobby('gloves');
        const oldTemplate = oldTemplates.find(t => t.id === oldGloves.templateId);
        if (oldTemplate) {
            const oldItem = createItemFromTemplate(oldTemplate);
            oldItem.charismaBonus = oldGloves.charismaBonus;
            oldItem.effect = oldGloves.effect;
            restoreItemToPath(oldItem, itemPath);
        }
    }
    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    currentCharacterData.equipment.gloves = glovesToEquip;
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Перчатки надеты', 'success');
};

window.unequipGloves = async function() {
    const gloves = currentCharacterData.equipment?.gloves;
    if (!gloves || !gloves.templateId) {
        showNotification('Перчатки не надеты');
        return;
    }
    const templates = await loadTemplatesForLobby('gloves');
    const template = templates.find(t => t.id === gloves.templateId);
    if (!template) {
        showNotification('Шаблон перчаток не найден');
        return;
    }
    const restoredItem = createItemFromTemplate(template);
    restoredItem.charismaBonus = gloves.charismaBonus;
    restoredItem.effect = gloves.effect;
    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
    currentCharacterData.inventory.backpack.push(restoredItem);
    delete currentCharacterData.equipment.gloves;
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Перчатки сняты', 'success');
};

// Кольцо
window.equipRingFromInventory = async function(itemPath) {
    const item = getItemByPath(itemPath);
    if (!item || item.category !== 'jewelry' || item.subcategory !== 'ring') {
        showNotification('Этот предмет нельзя надеть как кольцо');
        return;
    }
    const templates = await loadTemplatesForLobby('jewelry');
    const template = templates.find(t => t.id === item.templateId);
    if (!template) { showNotification('Шаблон не найден'); return; }
    const toEquip = {
        templateId: template.id,
        name: template.name,
        charismaBonus: item.charismaBonus ?? template.attributes?.charisma_bonus ?? 0,
    };
    if (!removeItemByPath(itemPath)) return;
    const old = currentCharacterData.equipment?.ring;
    if (old && old.templateId) restoreItemToPath(old, itemPath);
    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    currentCharacterData.equipment.ring = toEquip;
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave(); forceSyncCharacter();
    showNotification('Кольцо надето', 'success');
};

window.unequipRing = async function() {
    const item = currentCharacterData.equipment?.ring;
    if (!item || !item.templateId) { showNotification('Кольцо не надето'); return; }
    const templates = await loadTemplatesForLobby('jewelry');
    const template = templates.find(t => t.id === item.templateId);
    if (!template) { showNotification('Шаблон не найден'); return; }
    const restored = createItemFromTemplate(template);
    restored.charismaBonus = item.charismaBonus;
    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
    currentCharacterData.inventory.backpack.push(restored);
    delete currentCharacterData.equipment.ring;
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave(); forceSyncCharacter();
    showNotification('Кольцо снято', 'success');
};

// Амулет / цепочка
window.equipNecklaceFromInventory = async function(itemPath) {
    const item = getItemByPath(itemPath);
    if (!item || item.category !== 'jewelry' || (item.subcategory !== 'necklace' && item.subcategory !== 'amulet')) {
        showNotification('Этот предмет нельзя надеть как амулет или цепочку');
        return;
    }
    const templates = await loadTemplatesForLobby('jewelry');
    const template = templates.find(t => t.id === item.templateId);
    if (!template) {
        showNotification('Шаблон не найден');
        return;
    }
    const toEquip = {
        templateId: template.id,
        name: template.name,
        subcategory: item.subcategory || template.subcategory,
        charismaBonus: item.charismaBonus ?? template.attributes?.charisma_bonus ?? 0,
    };
    toEquip.charismaBonus = parseFloat(toEquip.charismaBonus) || 0;

    if (!removeItemByPath(itemPath)) {
        showNotification('Не удалось найти предмет в инвентаре');
        return;
    }
    const old = currentCharacterData.equipment?.necklace;
    if (old && old.templateId) {
        const oldTemplates = await loadTemplatesForLobby('jewelry');
        const oldTemplate = oldTemplates.find(t => t.id === old.templateId);
        if (oldTemplate) {
            const oldItem = createItemFromTemplate(oldTemplate);
            oldItem.charismaBonus = old.charismaBonus || 0;
            restoreItemToPath(oldItem, itemPath);
        }
    }
    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    currentCharacterData.equipment.necklace = toEquip;
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Амулет/цепочка надеты', 'success');
};

window.unequipNecklace = async function() {
    const item = currentCharacterData.equipment?.necklace;
    if (!item || !item.templateId) {
        showNotification('Амулет/цепочка не надеты');
        return;
    }
    const templates = await loadTemplatesForLobby('jewelry');
    const template = templates.find(t => t.id === item.templateId);
    if (!template) {
        showNotification('Шаблон не найден');
        return;
    }
    const restored = createItemFromTemplate(template);
    restored.charismaBonus = item.charismaBonus || 0;
    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
    currentCharacterData.inventory.backpack.push(restored);
    delete currentCharacterData.equipment.necklace;
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Амулет/цепочка сняты', 'success');
};

// Серьги
window.equipEarringsFromInventory = async function(itemPath) {
    const item = getItemByPath(itemPath);
    if (!item || item.category !== 'jewelry' || item.subcategory !== 'earrings') {
        showNotification('Этот предмет нельзя надеть как серьги');
        return;
    }
    const templates = await loadTemplatesForLobby('jewelry');
    const template = templates.find(t => t.id === item.templateId);
    if (!template) {
        showNotification('Шаблон не найден');
        return;
    }
    const toEquip = {
        templateId: template.id,
        name: template.name,
        subcategory: item.subcategory,
        charismaBonus: item.charismaBonus ?? template.attributes?.charisma_bonus ?? 0,
    };
    toEquip.charismaBonus = parseFloat(toEquip.charismaBonus) || 0;

    if (!removeItemByPath(itemPath)) {
        showNotification('Не удалось найти предмет в инвентаре');
        return;
    }
    const old = currentCharacterData.equipment?.earrings;
    if (old && old.templateId) {
        const oldTemplates = await loadTemplatesForLobby('jewelry');
        const oldTemplate = oldTemplates.find(t => t.id === old.templateId);
        if (oldTemplate) {
            const oldItem = createItemFromTemplate(oldTemplate);
            oldItem.charismaBonus = old.charismaBonus || 0;
            restoreItemToPath(oldItem, itemPath);
        }
    }
    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    currentCharacterData.equipment.earrings = toEquip;
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Серьги надеты', 'success');
};

window.unequipEarrings = async function() {
    const item = currentCharacterData.equipment?.earrings;
    if (!item || !item.templateId) {
        showNotification('Серьги не надеты');
        return;
    }
    const templates = await loadTemplatesForLobby('jewelry');
    const template = templates.find(t => t.id === item.templateId);
    if (!template) {
        showNotification('Шаблон не найден');
        return;
    }
    const restored = createItemFromTemplate(template);
    restored.charismaBonus = item.charismaBonus || 0;
    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
    currentCharacterData.inventory.backpack.push(restored);
    delete currentCharacterData.equipment.earrings;
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Серьги сняты', 'success');
};

// Браслет (с номером слота)
window.equipBraceletFromInventory = async function(itemPath, slotNumber) {
    const item = getItemByPath(itemPath);
    if (!item || item.category !== 'jewelry' || item.subcategory !== 'bracelet') {
        showNotification('Этот предмет нельзя надеть как браслет');
        return;
    }
    const templates = await loadTemplatesForLobby('jewelry');
    const template = templates.find(t => t.id === item.templateId);
    if (!template) { showNotification('Шаблон не найден'); return; }
    const toEquip = {
        templateId: template.id,
        name: template.name,
        charismaBonus: item.charismaBonus ?? template.attributes?.charisma_bonus ?? 0,
    };
    if (!removeItemByPath(itemPath)) return;
    const old = currentCharacterData.equipment?.[`bracelet${slotNumber}`];
    if (old && old.templateId) restoreItemToPath(old, itemPath);
    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    currentCharacterData.equipment[`bracelet${slotNumber}`] = toEquip;
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave(); forceSyncCharacter();
    showNotification(`Браслет ${slotNumber} надет`, 'success');
};

window.unequipBracelet = async function(slotNumber) {
    const item = currentCharacterData.equipment?.[`bracelet${slotNumber}`];
    if (!item || !item.templateId) { showNotification(`Браслет ${slotNumber} не надет`); return; }
    const templates = await loadTemplatesForLobby('jewelry');
    const template = templates.find(t => t.id === item.templateId);
    if (!template) { showNotification('Шаблон не найден'); return; }
    const restored = createItemFromTemplate(template);
    restored.charismaBonus = item.charismaBonus;
    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
    currentCharacterData.inventory.backpack.push(restored);
    delete currentCharacterData.equipment[`bracelet${slotNumber}`];
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave(); forceSyncCharacter();
    showNotification(`Браслет ${slotNumber} снят`, 'success');
};

window.unequipFromBelt = async function() {
    const belt = currentCharacterData.equipment?.belt;
    if (!belt || !belt.storedItem) {
        showNotification('На поясе ничего нет');
        return;
    }

    const stored = belt.storedItem;
    const templates = await loadTemplatesForLobby(stored.type);
    const template = templates.find(t => t.id === stored.templateId);
    if (!template) {
        showNotification('Шаблон предмета не найден');
        return;
    }

    const restoredItem = createItemFromTemplate(template);
    Object.assign(restoredItem, stored.savedAttributes || {});

    // Пытаемся вернуть в исходный контейнер (если путь сохранён), иначе в рюкзак
    const path = stored.sourcePath;
    let restored = false;
    if (path) {
        restored = restoreItemToPath(restoredItem, path);
    }
    if (!restored) {
        if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
        if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
        currentCharacterData.inventory.backpack.push(restoredItem);
    }

    delete belt.storedItem;

    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Предмет снят с пояса', 'success');
};

window.unequipArmor = async function() {
    try {
        return await runEquipmentAction('unequip', 'armor');
    } catch (error) {
        showNotification(error.message || 'Не удалось снять броню');
        return;
    }
};

async function legacyUnequipArmor() {
    const armor = currentCharacterData.equipment?.armor;
    if (!armor || !armor.templateId) {
        showNotification('Броня не надета');
        return;
    }
    const templates = await loadTemplatesForLobby('armor');
    const template = templates.find(t => t.id === armor.templateId);
    if (!template) {
        showNotification('Шаблон брони не найден');
        return;
    }
    const restoredItem = createItemFromTemplate(template);
    restoredItem.durability = armor.durability;
    restoredItem.maxDurability = armor.maxDurability;
    restoredItem.material = armor.material;
    restoredItem.stage = armor.stage;
    restoredItem.condition = armor.condition;
    restoredItem.currentStageDurability = armor.currentStageDurability;
    restoredItem.protection = { ...armor.protection };
    restoredItem.modifications = armor.modifications || [];
    restoredItem.installedModules = armor.installedModules
        ? [...armor.installedModules]
        : [];
    restoredItem.containers = armor.containers || [];
    restoredItem.protectionZones = armor.protectionZones || [];
    restoredItem.integratedHelmet = Boolean(armor.integratedHelmet);
    restoredItem.isExoskeleton = Boolean(armor.isExoskeleton);
    restoredItem.requiresExoskeletonBattery = Boolean(armor.requiresExoskeletonBattery);
    restoredItem.powered = armor.powered;
    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
    currentCharacterData.inventory.backpack.push(restoredItem);
    delete currentCharacterData.equipment.armor;
    if (currentCharacterData.equipment?.helmet?.integratedWithArmor) {
        delete currentCharacterData.equipment.helmet;
    }
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    await renderSkillsTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Броня снята', 'success');
};

window.unequipHelmet = async function() {
    try {
        return await runEquipmentAction('unequip', 'helmet');
    } catch (error) {
        showNotification(error.message || 'Не удалось снять шлем');
        return;
    }
};

async function legacyUnequipHelmet() {
    const helmet = currentCharacterData.equipment?.helmet;
    if (!helmet || !helmet.templateId) {
        showNotification('Шлем не надет');
        return;
    }
    if (helmet.integratedWithArmor) {
        showNotification('Встроенный шлем снимается только вместе с бронёй');
        return;
    }
    const templates = await loadTemplatesForLobby('helmet');
    const template = templates.find(t => t.id === helmet.templateId);
    if (!template) {
        showNotification('Шаблон шлема не найден');
        return;
    }
    const restoredItem = createItemFromTemplate(template);
    restoredItem.durability = helmet.durability;
    restoredItem.maxDurability = helmet.maxDurability;
    restoredItem.material = helmet.material;
    restoredItem.stage = helmet.stage;
    restoredItem.condition = helmet.condition;
    restoredItem.currentStageDurability = helmet.currentStageDurability;
    restoredItem.protection = { ...helmet.protection };
    restoredItem.movementPenalty = helmet.movementPenalty || 0;
    restoredItem.modifications = helmet.modifications || [];
    restoredItem.installedModules = helmet.installedModules || [];
    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
    currentCharacterData.inventory.backpack.push(restoredItem);
    delete currentCharacterData.equipment.helmet;
    renderEquipmentTab(currentCharacterData);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Шлем снят', 'success');
};

window.unequipGasMask = async function() {
    try {
        return await runEquipmentAction('unequip', 'gasMask');
    } catch (error) {
        showNotification(error.message || 'Не удалось снять противогаз');
        return;
    }
};

async function legacyUnequipGasMask() {
    const gasMask = currentCharacterData.equipment?.gasMask;
    if (!gasMask || !gasMask.templateId) {
        showNotification('Противогаз не надет');
        return;
    }
    const templates = await loadTemplatesForLobby('gas_mask');
    const template = templates.find(t => t.id === gasMask.templateId);
    if (!template) {
        showNotification('Шаблон противогаза не найден');
        return;
    }
    const restoredItem = createItemFromTemplate(template);
    restoredItem.durability = gasMask.durability;
    restoredItem.maxDurability = gasMask.maxDurability;
    restoredItem.material = gasMask.material;
    restoredItem.protection = { ...gasMask.protection };
    restoredItem.modifications = gasMask.modifications || [];
    restoredItem.installedModules = gasMask.installedModules || [];
    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
    currentCharacterData.inventory.backpack.push(restoredItem);
    delete currentCharacterData.equipment.gasMask;
    renderEquipmentTab(currentCharacterData);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Противогаз снят', 'success');
};

window.unequipWeapon = async function(weaponIndex) {
    const weapon = currentCharacterData.weapons[weaponIndex];
    if (!weapon) {
        showNotification('Оружие не найдено');
        return;
    }
    if (!weapon.templateId) {
        showNotification('Оружие должно быть основано на шаблоне');
        return;
    }

    const category = weapon.category === 'melee_weapon' ? 'melee_weapon' : 'weapon';
    const templates = await loadTemplatesForLobby(category);
    const template = templates.find(t => t.id == weapon.templateId);
    if (!template) {
        showNotification('Шаблон оружия не найден');
        return;
    }

    const restoredItem = { ...createItemFromTemplate(template), ...structuredClone(weapon), category, quantity: 1 };
    // Копируем текущие характеристики
    restoredItem.durability = weapon.durability ?? template.attributes?.durability ?? template.attributes?.max_durability ?? 100;
    restoredItem.maxDurability = weapon.maxDurability ?? template.attributes?.max_durability ?? 100;

    if (category === 'weapon') {
        restoredItem.ammo = weapon.ammo;
        restoredItem.installedMagazine = weapon.installedMagazine ? { ...weapon.installedMagazine } : null;
        restoredItem.fixedAmmo = Array.isArray(weapon.fixedAmmo)
            ? weapon.fixedAmmo.map(stack => ({ ...stack }))
            : [];
        restoredItem.jam = weapon.jam ? { ...weapon.jam } : null;
        restoredItem.jams = Array.isArray(weapon.jams)
            ? weapon.jams.map(jam => ({ ...jam }))
            : [];
        restoredItem.requiresManualCycle = Boolean(weapon.requiresManualCycle);
    } else {
        // Для ближнего боя дополнительно копируем специфичные поля (если они менялись)
        restoredItem.damage = weapon.damage;
        restoredItem.accuracy = weapon.accuracy;
        restoredItem.armorPiercing = weapon.armorPiercing;
        restoredItem.bleeding = weapon.bleeding;
        restoredItem.weightClass = weapon.weightClass;
        restoredItem.size = weapon.size;
    }

    restoredItem.modifications = weapon.modifications || [];
    restoredItem.installedModules = weapon.installedModules || [];

    // Добавляем в рюкзак
    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
    currentCharacterData.inventory.backpack.push(restoredItem);

    // Удаляем оружие из экипировки
    currentCharacterData.weapons.splice(weaponIndex, 1);
    if (Number(currentCharacterData.activeWeaponIndex) === Number(weaponIndex)) {
        delete currentCharacterData.activeWeaponIndex;
    } else if (Number(currentCharacterData.activeWeaponIndex) > Number(weaponIndex)) {
        currentCharacterData.activeWeaponIndex -= 1;
    }

    renderEquipmentTab(currentCharacterData);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Оружие снято', 'success');
};

window.unequipBelt = async function() {
    const belt = currentCharacterData.equipment?.belt;
    if (!belt || !belt.templateId) {
        showNotification('Пояс не надет');
        return;
    }
    const templates = await loadTemplatesForLobby('belt');
    const template = templates.find(t => t.id === belt.templateId);
    if (!template) {
        showNotification('Шаблон пояса не найден');
        return;
    }
    const restoredItem = createItemFromTemplate(template);
    restoredItem.pouches = belt.pouches || [];
    restoredItem.modifications = belt.modifications || [];
    restoredItem.storedItem = belt.storedItem || null;
    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
    currentCharacterData.inventory.backpack.push(restoredItem);
    delete currentCharacterData.equipment.belt;
    renderEquipmentTab(currentCharacterData);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Пояс снят', 'success');
};

window.unequipVest = async function() {
    const vest = currentCharacterData.equipment?.vest;
    if (!vest || !vest.templateId) {
        showNotification('Разгрузка не надета');
        return;
    }
    const templates = await loadTemplatesForLobby('vest');
    const template = templates.find(t => t.id === vest.templateId);
    if (!template) {
        showNotification('Шаблон разгрузки не найден');
        return;
    }
    const restoredItem = createItemFromTemplate(template);
    restoredItem.model = vest.model;
    restoredItem.totalCapacity = vest.totalCapacity;
    restoredItem.pouches = vest.pouches || [];
    restoredItem.modifications = vest.modifications || [];
    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
    currentCharacterData.inventory.backpack.push(restoredItem);
    delete currentCharacterData.equipment.vest;
    renderEquipmentTab(currentCharacterData);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Разгрузка снята', 'success');
};

window.unequipDetector = async function() {
    const detector = currentCharacterData.equipment?.detector;
    if (!detector || !detector.templateId) {
        showNotification('Детектор не надет');
        return;
    }
    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
    currentCharacterData.inventory.backpack.push(detector);
    delete currentCharacterData.equipment.detector;
    await renderEquipmentTab(currentCharacterData);
    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
    forceSyncCharacter();
    showNotification('Детектор аномалий снят', 'success');
};

function getMeleeAttackModifiers(attackType, baseDamage, baseAP) {
    switch(attackType) {
        case 'Колющий':
            return { damage: Math.floor(baseDamage * 1.25), ap: baseAP + 10 };
        case 'Режущий':
            return { damage: Math.floor(baseDamage * 0.75), ap: Math.max(0, baseAP - 10) };
        case 'Вспарывающий':
            return { damage: Math.floor(baseDamage * 1.35), ap: baseAP + 10 };
        case 'Круговой':
            return { damage: baseDamage, ap: Math.max(0, baseAP - 10) };
        default:
            return { damage: baseDamage, ap: baseAP };
    }
}

function getMeleeActionPointCost(weightClass, attackType) {
    const normalizedClass = String(weightClass || '').toLowerCase().replaceAll('ё', 'е');
    const normalizedAttack = String(attackType || '').toLowerCase().replaceAll('ё', 'е');
    const baseCost = normalizedClass.includes('очень')
        ? 4
        : (normalizedClass.includes('лег') ? 2 : 3);
    let modifier = 0;
    if (normalizedAttack.includes('кол')) modifier = 1;
    else if (normalizedAttack.includes('реж')) modifier = -1;
    else if (normalizedAttack.includes('всп')) modifier = 2;
    else if (normalizedAttack.includes('круг')) modifier = 1;
    return Math.max(1, baseCost + modifier);
}

window.useWeaponFromEquipment = async function(
    weaponIndex,
    fireMode = 'unaimed',
    shotCount = 1,
    actionPoints = 2,
    volleyCount = 1,
    pairedFire = false
) {
    const sheetData = currentCharacterData;
    const actorCharacterId = currentCharacterId;
    const weapon = sheetData?.weapons?.[weaponIndex];
    if (!weapon) return;
    const weaponTemplate = (allTemplatesCache || []).find(
        template => template.id == weapon.templateId
    );

    const combatState = window.locationCombatState;
    const isCombatActive = Boolean(combatState && combatState.status === 'active');
    const isCurrentTurn = Boolean(
        !isCombatActive ||
        combatState?.current_character?.character_id === actorCharacterId
    );

    if (isCombatActive && !isCurrentTurn) {
        showNotification('Сейчас не ход этого персонажа', 'system');
        return;
    }

    const weaponCategory = String(
        weapon.subcategory || weaponTemplate?.subcategory || ''
    ).toLowerCase();
    if (weaponCategory.includes('гранатом')) {
        if (!isCombatActive) {
            if (window.isLocationActive) {
                import('./locationScene.js').then(scene => scene.requestCombatStartForAttack(actorCharacterId));
            } else {
                showNotification('Стрельба из гранатомёта выполняется на боевой подлокации', 'system');
            }
            return;
        }
        if (!['unaimed', 'aimed'].includes(fireMode)) {
            showNotification('Гранатомёту доступны выстрел навскидку и прицельный выстрел', 'system');
            return;
        }
        closeCharacterSheet();
        import('./locationScene.js').then((scene) => {
            scene.queueCombatActionFromSheet({
                actorCharacterId,
                actionKey: 'explosive_attack',
                weaponIndex,
                explosiveSource: 'weapon',
                explosiveFireMode: fireMode,
                targetType: 'point',
                source: 'sheet',
            });
        });
        return;
    }

    const profile = getWeaponFireProfile(
        weapon,
        weaponTemplate
    );
    const shots = Math.max(1, Number.parseInt(shotCount, 10) || 1);
    const singleOptions = profile.single_shot_options || [1];
    const singleMode = ['unaimed', 'rapid', 'aimed'].includes(fireMode);
    const automaticMode = ['burst', 'suppression', 'area'].includes(fireMode);
    if (
        (singleMode && !singleOptions.includes(shots)) ||
        (automaticMode && !profile.supports_burst) ||
        (!profile.machine_gun_burst && automaticMode && shots !== Number(profile.burst_size) * volleyCount)
    ) {
        showNotification('Этот режим стрельбы недоступен для выбранного оружия');
        return;
    }
    if (getWeaponAmmoCount(weapon) < shots) {
        showNotification(`Недостаточно патронов: нужно ${shots}`);
        return;
    }

    const spendAmmo = async () => {
        const mag = weapon.installedMagazine;
        if (mag && Array.isArray(mag.ammo)) {
            let remaining = shots;
            while (remaining > 0 && mag.ammo.length > 0) {
                const last = mag.ammo[mag.ammo.length - 1];
                const consumed = Math.min(remaining, Number(last.quantity) || 0);
                last.quantity -= consumed;
                remaining -= consumed;
                if (last.quantity <= 0) mag.ammo.pop();
            }
            weapon.ammo = mag.ammo.reduce((sum, a) => sum + a.quantity, 0);
            updateMagazineWeight(mag);
        } else if (Array.isArray(weapon.fixedAmmo)) {
            let remaining = shots;
            while (remaining > 0 && weapon.fixedAmmo.length > 0) {
                const last = weapon.fixedAmmo[weapon.fixedAmmo.length - 1];
                const consumed = Math.min(remaining, Number(last.quantity) || 0);
                last.quantity -= consumed;
                remaining -= consumed;
                if (last.quantity <= 0) weapon.fixedAmmo.pop();
            }
            weapon.ammo = weapon.fixedAmmo.reduce(
                (sum, stack) => sum + (Number(stack.quantity) || 0),
                0
            );
        } else if (typeof weapon.ammo === 'number') {
            weapon.ammo -= shots;
        } else {
            showNotification('Нет патронов');
            return false;
        }
        const template = (allTemplatesCache || []).find(entry => entry.id == weapon.templateId);
        const cycleType = getManualCycleType(weapon, template);
        if (cycleType) weapon.requiresManualCycle = true;

        if (actorCharacterId && sheetData) {
            await Server.updateCharacter(actorCharacterId, { data: sheetData });
        }
        renderEquipmentTab(sheetData);
        scheduleAutoSave();
        forceSyncCharacter();
        showNotification(`Выстрелов: ${shots}. ${weapon.name}: осталось ${weapon.ammo} патронов`, 'system');
        return true;
    };

    if (!isCombatActive) {
        if (window.isLocationActive) {
            import('./locationScene.js').then(scene => scene.requestCombatStartForAttack(actorCharacterId));
            return;
        }
        spendAmmo();
        return;
    }

    let sniperMode;
    if (fireMode === 'aimed' && getWeaponClass(weapon, weaponTemplate) === 'sniper_rifle') {
        const options = [{ label: 'Обычный выстрел' }];
        if (traitCount(sheetData,'Снайперское милосердие')) options.push({label:'Снайперское милосердие · без дополнительных травм',mode:'mercy'});
        if (traitCount(sheetData,'Микроскопическая точность')) options.push({label:'Микроскопическая точность · +1 ОД, выбор травмы',mode:'precision'});
        if (options.length > 1) {
            const selected=await chooseConsumableApplication('Снайперский выстрел',options);
            if (!selected) return;
            sniperMode=selected.mode;
            if (sniperMode === 'precision') actionPoints=Number(actionPoints)+1;
        }
    }
    closeCharacterSheet();
    import('./locationScene.js').then((module) => {
        module.queueCombatActionFromSheet({
            actorCharacterId,
            actionKey: 'attack',
            weaponIndex,
            fireMode,
            attributeChoice: pairedFire ? 'paired_fire' : sniperMode ? {sniperMode} : undefined,
            shotCount: shots,
            actionPoints,
            volleyCount,
            targetType: fireMode === 'suppression'
                ? 'structure'
                : (fireMode === 'area' ? 'multi_character' : 'character'),
            source: 'sheet',
        });
    });
};

window.useMeleeAttack = function(weaponIndex, attackType, aimed = false) {
    const sheetData = currentCharacterData;
    const actorCharacterId = currentCharacterId;
    const isUnarmed = attackType === 'unarmed';
    const isFirearmButt = attackType === 'firearm_butt';
    const isBayonet = attackType === 'bayonet';
    const weapon = sheetData?.weapons?.[weaponIndex];
    if (!weapon && !isUnarmed) return;
    const template = (allTemplatesCache || []).find(t => t.id == weapon?.templateId);
    const bayonetTemplate = isBayonet ? (allTemplatesCache || []).find(t=>t.category==='melee_weapon'&&t.name?.toLowerCase()==='штык-нож') : null;
    const attrs = (isBayonet ? bayonetTemplate : template)?.attributes || {};
    const strengthBonus = Math.floor(
        (getSkillEffectiveValue(sheetData, 'physical.strength') - 10) / 2
    );
    const baseDamage = isUnarmed
        ? Math.max(10, 10 * strengthBonus)
        : (isFirearmButt ? ((Number(weapon.weight ?? template?.weight) || 0) <= 1 ? 25 : 40) : (attrs.damage || 0));
    const baseAP = (isUnarmed || isFirearmButt) ? 0 : (attrs.armor_piercing || 0);
    const modifiers = (isUnarmed || isFirearmButt)
        ? { damage: baseDamage, ap: baseAP }
        : getMeleeAttackModifiers(isBayonet?'Колющий':attackType, baseDamage, baseAP);
    const attackLabel = isUnarmed
        ? 'Удар кулаком'
        : (isFirearmButt
            ? ((Number(weapon.weight ?? template?.weight) || 0) <= 1 ? 'Удар рукояткой' : 'Удар прикладом')
            : (isBayonet?'Колющий штыком':attackType));
    const weaponLabel = isUnarmed ? 'Кулаки' : weapon.name;
    const combatState = window.locationCombatState;
    const isCombatActive = Boolean(combatState && combatState.status === 'active');
    const isCurrentTurn = Boolean(
        !isCombatActive ||
        combatState?.current_character?.character_id === actorCharacterId
    );
    if (isCombatActive && !isCurrentTurn) {
        showNotification('Сейчас не ход этого персонажа', 'system');
        return;
    }
    if (!isCombatActive) {
        if (window.isLocationActive) {
            import('./locationScene.js').then(scene => scene.requestCombatStartForAttack(actorCharacterId));
            return;
        }
        showNotification(`Атака «${attackLabel}»: ${weaponLabel}. Урон: ${modifiers.damage}, Бронебойность: ${modifiers.ap}%`, 'system');
        return;
    }

    const fencer = !isUnarmed && Number(weaponIndex)>=0 && combatState?.current_character?.fencer_available
        && window.confirm('Использовать приём Фехтовальщика: на 2 ОД дешевле, минимум 1 ОД? Один раз за ход.');
    closeCharacterSheet();
    import('./locationScene.js').then((module) => {
        module.queueCombatActionFromSheet({
            actorCharacterId,
            actionKey: 'attack',
            weaponIndex,
            attackType,
            payment: [aimed ? 'aimed' : null, fencer ? 'fencer' : null].filter(Boolean).join('+') || null,
            meleeAimed: aimed,
            targetType: String(attackType).toLowerCase().includes('круг')
                ? 'multi_melee'
                : 'character',
            source: 'sheet',
        });
    });
};

let combatSheetRefreshBound = false;

function refreshCombatSensitiveSheetTabs() {
    if (!currentCharacterData) return;
    const modal = document.getElementById('character-sheet-modal');
    if (!modal || modal.style.display === 'none') return;
    const activeTab = document.querySelector('#sheet-tabs .tab-btn.active')?.dataset.tab;
    if (activeTab === 'equipment') {
        renderEquipmentTab(currentCharacterData);
    }
}

if (!combatSheetRefreshBound) {
    combatSheetRefreshBound = true;
    window.addEventListener('combat-state-updated', refreshCombatSensitiveSheetTabs);
}

window.fireGrenadeLauncher = async function(weaponIndex) {
    const weapon = currentCharacterData.weapons[weaponIndex];
    const launcher = weapon.installedModules?.find(m => m.attributes?.type === 'grenade_launcher');
    if (!launcher) {
        showNotification('Подствольный гранатомёт не установлен');
        return;
    }

    if (!launcher.loaded || !launcher.loadedGrenade) {
        showNotification('Подствольник не заряжен');
        return;
    }
    if (!window.locationCombatState || window.locationCombatState.status !== 'active') {
        if (window.isLocationActive) {
            const scene = await import('./locationScene.js');
            await scene.requestCombatStartForAttack(currentCharacterId);
        } else {
            showNotification('Стрельба из подствольника выполняется на боевой подлокации', 'system');
        }
        return;
    }

    document.getElementById('grenade-fire-mode-modal')?.remove();
    const modal = document.createElement('div');
    modal.id = 'grenade-fire-mode-modal';
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content" style="max-width:420px">
            <span class="close">&times;</span>
            <h3>Выстрел из подствольника</h3>
            <p>${launcher.loadedGrenade.name || 'Граната'}</p>
            <div class="form-actions" style="display:grid;gap:8px">
                <button class="btn btn-primary" data-mode="unaimed">Навскидку · 2 ОД · точность −4</button>
                <button class="btn btn-warning" data-mode="aimed">Прицельный · 4 ОД</button>
                <button class="btn btn-secondary" data-cancel>Отмена</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    modal.style.display = 'flex';
    const close = () => modal.remove();
    modal.querySelector('.close').onclick = close;
    modal.querySelector('[data-cancel]').onclick = close;
    modal.querySelectorAll('[data-mode]').forEach(button => {
        button.onclick = async () => {
            const explosiveFireMode = button.dataset.mode;
            close();
            const actorCharacterId = currentCharacterId;
            closeCharacterSheet();
            const scene = await import('./locationScene.js');
            scene.queueCombatActionFromSheet({
                actorCharacterId,
                actionKey: 'explosive_attack',
                weaponIndex,
                explosiveSource: 'underbarrel',
                explosiveFireMode,
                targetType: 'point',
                source: 'sheet',
            });
        };
    });
};

window.reloadGrenadeLauncher = async function(weaponIndex) {
    if (secondaryWeaponIndex()!=null) {showNotification('Для перезарядки сначала освободите руку','system');return;}
    const weapon = currentCharacterData.weapons[weaponIndex];
    const launcher = weapon.installedModules?.find(m => m.attributes?.type === 'grenade_launcher');
    if (!launcher) {
        showNotification('Подствольный гранатомёт не установлен');
        return;
    }

    const caliber = launcher.attributes?.caliber;
    if (!caliber) {
        showNotification('Неизвестный калибр гранатомёта');
        return;
    }

    // Ищем гранату подходящего калибра
    const grenadeItems = [];
    const collectGrenades = (items, path) => {
        if (!Array.isArray(items)) return;
        items.forEach((item, idx) => {
            if (
                item.category === 'grenade'
                && getItemCaliber(item) === normalizeCaliberText(caliber)
                && item.quantity > 0
            ) {
                grenadeItems.push({ item, path: path.concat(idx) });
            }
            if (item.contents) collectGrenades(item.contents, path.concat(idx, 'contents'));
        });
    };
    collectGrenades(currentCharacterData.inventory?.backpack, ['inventory', 'backpack']);
    collectGrenades(currentCharacterData.inventory?.pockets, ['inventory', 'pockets']);
    const beltPouches = currentCharacterData.equipment?.belt?.pouches || [];
    beltPouches.forEach((pouch, i) => collectGrenades(pouch.contents, ['equipment', 'belt', 'pouches', i, 'contents']));
    const vestPouches = currentCharacterData.equipment?.vest?.pouches || [];
    vestPouches.forEach((pouch, i) => collectGrenades(pouch.contents, ['equipment', 'vest', 'pouches', i, 'contents']));

    if (grenadeItems.length === 0) {
        showNotification(`Нет гранат калибра ${caliber}`);
        return;
    }

    // Модальное окно выбора гранаты
    const oldModal = document.getElementById('grenade-select-modal');
    if (oldModal) oldModal.remove();

    const modal = document.createElement('div');
    modal.id = 'grenade-select-modal';
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content">
            <span class="close" onclick="this.closest('.modal').remove()">&times;</span>
            <h3>Выберите гранату</h3>
            <select id="grenade-select" class="form-control"></select>
            <div class="form-actions">
                <button class="btn btn-primary" id="confirm-grenade-btn">Зарядить</button>
                <button class="btn btn-secondary" onclick="this.closest('.modal').remove()">Отмена</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    const select = modal.querySelector('#grenade-select');
    grenadeItems.forEach((entry, idx) => {
        const opt = document.createElement('option');
        opt.value = idx;
        opt.textContent = `${entry.item.name} (${entry.item.quantity} шт.)`;
        select.appendChild(opt);
    });

    modal.querySelector('#confirm-grenade-btn').onclick = async () => {
        const idx = select.value;
        if (idx === '') return;
        const selected = grenadeItems[idx];
        const grenade = selected.item;
        try {
            const combatState = window.locationCombatState;
            if (!combatState || combatState.status !== 'active') {
                const grenadeToUse = { ...grenade, quantity: 1 };
                if (grenade.quantity > 1) grenade.quantity -= 1;
                else if (!removeItemByPath(selected.path)) {
                    showNotification('Не удалось найти гранату в инвентаре');
                    return;
                }
                launcher.loaded = true;
                launcher.loadedGrenade = grenadeToUse;
                modal.remove();
                renderInventoryTab(currentCharacterData);
                renderEquipmentTab(currentCharacterData);
                scheduleAutoSave();
                forceSyncCharacter();
                showNotification('Подствольник заряжен', 'success');
                return;
            }
            const actor = combatState.current_character;
            if (!actor || actor.character_id !== currentCharacterId) {
                showNotification('Сейчас не ход этого персонажа', 'system');
                return;
            }
            const access = await calculateInventoryAccess(grenade, selected.path);
            const pendingActionId = `underbarrel-${actor.location_character_id}-${Date.now()}`;
            const payload = {
                location_character_id: actor.location_character_id,
                action_key: 'reload_underbarrel',
                weapon_index: weaponIndex,
                item_path: selected.path,
                inventory_retrieval_action_points: access.retrievalActionPoints,
                inventory_use_action_discount: access.useActionDiscount,
                pending_action_id: pendingActionId,
            };
            const result = await Server.performLocationCombatAction(
                window.currentLobbyId,
                window.currentLocationId,
                payload,
            );
            modal.remove();
            if (result?.pending_action) {
                const sceneModule = await import('./locationScene.js');
                sceneModule.registerDeferredCombatAction(result.pending_action_id, payload);
                showNotification('Зарядка подствольника начата и продолжится в следующий ход', 'system');
                return;
            }
            showNotification('Подствольник заряжен', 'success');
        } catch (error) {
            showNotification(error.message || 'Не удалось зарядить подствольник', 'system');
        }
    };

    modal.style.display = 'flex';
};

// Использование предмета из инвентаря (для расходников, гранат и т.д.)
async function useItem(item, itemPath, options = {}) {
    if (item.category === 'consumable') {
        if (!options.consumableSideEffects) {
            return useCharacterInventoryItem(currentCharacterId, itemPath, options);
        }
        return await useConsumable(item, itemPath, options);
    } else if (item.category === 'grenade') {
        return await useGrenade(item, itemPath, options);
    } else if (item.category === 'tool' && item.attributes?.repair_profile) {
        return await useRepairTool(item, itemPath);
    } else if (item.category === 'device') {
        // Если устройство имеет батарею и разряжено, предложить зарядить
        if (item.attributes?.power !== undefined && item.attributes.power < 100) {
            await rechargeDevice(item, itemPath);
        } else {
            toggleDevice(item, itemPath);
        }
    } else {
        showNotification('Невозможно использовать этот предмет');
    }
}

function getRepairTargetCategory(item) {
    return String(item?.category || getInventoryItemTemplate(item)?.category || '').toLowerCase();
}

function getRepairTargetLabel(item, profile) {
    const name = item?.name || getInventoryItemTemplate(item)?.name || 'Снаряжение';
    if (profile.kind === 'restore_tool') {
        const maximum = Number(item?.maxUses ?? item?.attributes?.uses ?? 0);
        return `${name} · прочность ${Number(item?.uses ?? maximum)}/${maximum}`;
    }
    if (profile.kind === 'weapon') {
        const maximum = Number(item?.maxDurability ?? item?.attributes?.max_durability ?? 100);
        return `${name} · прочность ${Number(item?.durability ?? maximum)}/${maximum}`;
    }
    const stageNames = ['1. Целая', '2. Немного повреждена', '3. Повреждена', '4. Сильно повреждена', '5. Поломана'];
    const stage = Math.max(1, Math.min(5, Number(item?.stage || 1)));
    return `${name} · ${stageNames[stage - 1]}`;
}

async function useRepairTool(tool, toolPath) {
    const profile = tool?.attributes?.repair_profile;
    if (!profile || !['weapon', 'armor', 'armor_current_stage', 'restore_tool'].includes(profile.kind)) {
        showNotification('Этот инструмент пока нельзя использовать для ремонта');
        return false;
    }
    if (window.locationCombatState?.status === 'active') {
        showNotification('Ремонт снаряжения доступен только вне боя');
        return false;
    }
    const wantedCategories = profile.kind === 'weapon'
        ? new Set(['weapon', 'melee_weapon'])
        : (profile.kind === 'restore_tool'
            ? new Set(['tool'])
            : new Set(['armor', 'helmet', 'gas_mask']));
    const choices = [];
    const append = (item, path) => {
        if (!item || !wantedCategories.has(getRepairTargetCategory(item))) return;
        if (profile.kind === 'restore_tool') {
            if (item === tool) return;
            const maximum = Number(item.maxUses ?? item.attributes?.uses);
            const current = Number(item.uses ?? maximum);
            if (!Number.isFinite(maximum) || current >= maximum) return;
        }
        choices.push({
            label: getRepairTargetLabel(item, profile),
            item,
            path,
        });
    };
    (currentCharacterData.weapons || []).forEach((item, index) => append(item, ['weapons', index]));
    const equipment = currentCharacterData.equipment || {};
    append(equipment.armor, ['equipment', 'armor']);
    append(equipment.helmet, ['equipment', 'helmet']);
    append(equipment.gasMask, ['equipment', 'gasMask']);
    collectInventoryEntries(currentCharacterData, item => wantedCategories.has(getRepairTargetCategory(item)))
        .forEach(entry => append(entry.item, entry.path));
    if (!choices.length) {
        showNotification(profile.kind === 'weapon'
            ? 'Нет оружия для ремонта'
            : (profile.kind === 'restore_tool' ? 'Нет повреждённых наборов инструментов' : 'Нет брони для ремонта'));
        return false;
    }
    const selected = await chooseConsumableApplication(
        `Ремонт: ${tool.name}`,
        choices,
        { alwaysShow: true }
    );
    if (!selected) return false;
    try {
        const response = await Server.repairCharacterEquipment(
            currentCharacterId,
            toolPath,
            selected.path
        );
        currentCharacterData = response.character_data || currentCharacterData;
        await renderEquipmentTab(currentCharacterData);
        renderInventoryTab(currentCharacterData);
        const result = response.result || {};
        const summary = ['weapon', 'restore_tool'].includes(result.kind)
            ? `Прочность: ${result.before} → ${result.after}/${result.maximum_after}.${result.kind === 'weapon' ? ` Максимум −${result.maximum_penalty}.` : ''}`
            : `Стадия: ${result.stage_before} → ${result.stage_after}. Прочность брони: ${result.durability_before} → ${result.durability_after}.`;
        const duration = Number(result.duration_minutes || 0);
        const timeNote = duration > 0
            ? ` Длительность: ${duration} мин.; глобальное время изменяет ГМ.`
            : '';
        showNotification(`${response.message}. ${summary}${timeNote}`, 'success');
        return true;
    } catch (error) {
        showNotification(error.message || 'Не удалось отремонтировать снаряжение');
        return false;
    }
}

const BLEEDING_STAGE_RANK = { light: 1, medium: 2, severe: 3, extreme: 4 };
const BLEEDING_STAGE_LABEL = {
    light: 'лёгкое',
    medium: 'среднее',
    severe: 'сильное',
    extreme: 'экстремальное',
};
const BLEEDING_KIND_LABEL = { external: 'внешнее', internal: 'внутреннее' };

function getEffectAreaLabel(area) {
    return ({
        whole_body:'всё тело',mind:'сознание',head: 'голова', chest: 'грудь', abdomen: 'живот', leftArm: 'левая рука',
        rightArm: 'правая рука', leftLeg: 'левая нога', rightLeg: 'правая нога', internal: 'внутреннее',
        nose: 'нос', jaw: 'челюсть', leftEar: 'левое ухо', rightEar: 'правое ухо',
        leftEye: 'левый глаз', rightEye: 'правый глаз', spine: 'позвоночник',
        internalOrgan: 'внутренний орган', heart: 'сердце',
        rightLung: 'правое лёгкое', leftLung: 'левое лёгкое',
        rightKidney: 'правая почка', leftKidney: 'левая почка',
        stomach: 'желудок', liver: 'печень', brain: 'мозг'
    })[area] || 'область не указана';
}

function getEffectDisplayName(effect) {
    const baseName = effect?.name && effect.name !== effect.type && (effect.type === 'custom' || !/^[a-z][a-z0-9_]*$/.test(effect.name)) ? effect.name : getEffectTypeOptions().find(option => option.value === effect?.type)?.label || 'Эффект';
    if (effect?.type === 'organ_loss' && effect.area) {
        return `Повреждённый орган: ${getEffectAreaLabel(effect.area)}`;
    }
    if (effect?.type === 'organ_failure' && effect.area) {
        return `Смертельное повреждение: ${getEffectAreaLabel(effect.area)}`;
    }
    return baseName;
}

function formatTreatmentWindow(seconds) {
    const remaining = Math.max(0, Math.ceil(Number(seconds) || 0));
    if (remaining <= 0) return 'окно лечения истекло';
    const hours = Math.floor(remaining / 3600);
    const minutes = Math.ceil((remaining % 3600) / 60);
    if (hours && minutes) return `${hours} ч ${minutes} мин`;
    if (hours) return `${hours} ч`;
    if (minutes) return `${minutes} мин`;
    return `${remaining} сек`;
}

function getFracturePenaltyText(area) {
    const normalized = String(area || '').toLowerCase();
    if (normalized.includes('leg') || normalized.includes('foot')) return 'штраф перемещения +1';
    if (normalized.includes('arm') || normalized.includes('hand')) return '-1 к броскам, связанным с рукой';
    return 'постоянный штраф травмы';
}

function getFractureStatusText(effect, effects = []) {
    if (effect?.type === 'fracture') {
        const seconds = Math.max(0, Number(effect.regular_fixation_seconds ?? 1800));
        return `До перехода в незафиксированный перелом: ${Math.ceil(seconds / 60)} мин.`;
    }
    if (effect?.type === 'fracture_sequela') {
        return `Действует постоянно: ${getFracturePenaltyText(effect.area)}.`;
    }
    if (effect?.type !== 'fracture_unfixed') return '';
    const hasPenalty = Boolean(
        effect.permanent_penalty
        || effects.some(item => item.type === 'fracture_sequela' && item.area === effect.area)
    );
    const roll = Number(effect.fixation_consequence_roll) || null;
    const rollText = roll ? `Результат проверки: d100 ${roll}. ` : '';
    return hasPenalty
        ? `${rollText}Получен постоянный штраф: ${getFracturePenaltyText(effect.area)}.`
        : `${rollText}Постоянный штраф не получен.`;
}

function getBleedingInfo(effect) {
    const match = String(effect?.type || '').match(/^bleeding_(external|internal)_(light|medium|severe|extreme)$/);
    if (!match) return null;
    return { kind: match[1], stage: match[2], rank: BLEEDING_STAGE_RANK[match[2]] || 0 };
}

function getBleedingTreatmentOutcome(effect, application) {
    const bleeding = getBleedingInfo(effect);
    const maxStage = String(application?.max_stage || '').toLowerCase();
    const medicineRank = BLEEDING_STAGE_RANK[maxStage] || 0;
    if (!bleeding || !medicineRank) return null;
    if (!application.internal && bleeding.kind === 'internal') return null;
    if (application.internal_only && bleeding.kind !== 'internal') return null;
    if (bleeding.rank <= medicineRank) {
        return { mode: 'close', bleeding, resultStage: null };
    }
    if (application.allow_weakening !== false && bleeding.rank === medicineRank + 1) {
        return { mode: 'weaken', bleeding, resultStage: maxStage };
    }
    return null;
}

function getMedicalApplicationCostLabel(actionPoints, costContext = null) {
    const baseCost = Math.max(0, Number(actionPoints) || 0);
    if (!costContext) return `${baseCost} ОД`;
    const useCost = Math.max(0, baseCost - Number(costContext.useActionDiscount || 0));
    const retrievalCost = Number(costContext.retrievalActionPoints || 0);
    const totalCost = retrievalCost + useCost;
    if (!retrievalCost) return `${totalCost} ОД всего`;
    return `${totalCost} ОД всего (лечение ${useCost} + достать ${retrievalCost})`;
}

function getMedicineSkillLevel() {
    const medicine = currentCharacterData?.skills?.other?.medicine || {};
    const base = Number(medicine.base ?? medicine.value ?? 5);
    const bonus = Number(medicine.bonus || 0);
    return Math.max(0, (Number.isFinite(base) ? base : 5) + (Number.isFinite(bonus) ? bonus : 0));
}

function getMedicalTreatmentActionPoints(direct, fallbackActionPoints) {
    const configuredCost = Number(direct?.treatment_action_points);
    const hasConfiguredCost = Number.isFinite(configuredCost);
    let actionPoints = hasConfiguredCost
        ? configuredCost
        : Math.max(0, Number(fallbackActionPoints) || 0);
    if (hasConfiguredCost && direct?.treatment_time_uses_medicine !== false) {
        const medicineLevel = getMedicineSkillLevel();
        if (medicineLevel >= 15 && !backgroundIs(currentCharacterData,'Фармацевт')) actionPoints -= 1;
        else if (medicineLevel <= 5) actionPoints += 1;
    }
    return Math.max(0, actionPoints);
}

function getBleedingEffectLabel(effect, bleeding = getBleedingInfo(effect)) {
    if (!bleeding) return effect?.name || effect?.type || 'Кровотечение';
    return `${BLEEDING_KIND_LABEL[bleeding.kind]} ${BLEEDING_STAGE_LABEL[bleeding.stage]}`;
}

function getInjuryEffectLabel(effect) {
    const labels = {
        fracture: 'Перелом',
        fracture_fixed: 'Зафиксированный перелом',
        fracture_unfixed: 'Незафиксированный перелом',
        fracture_sequela: 'Постоянный штраф после перелома',
        amputation: 'Отсутствующая часть тела',
        mangled_limb: 'Искореженная конечность',
        organ_loss: 'Отсутствующий орган',
        damaged_zone: 'Повреждённая часть тела',
    };
    return effect?.name || labels[effect?.type] || effect?.type || 'Травма';
}

function collectInventoryEntries(data, predicate) {
    const found = [];
    const visit = (items, path) => {
        if (!Array.isArray(items)) return;
        items.forEach((entry, index) => {
            const entryPath = path.concat(index);
            if (predicate(entry)) found.push({ item: entry, path: entryPath });
            if (Array.isArray(entry?.contents)) visit(entry.contents, entryPath.concat('contents'));
        });
    };
    visit(data.inventory?.pockets, ['inventory', 'pockets']);
    visit(data.inventory?.backpack, ['inventory', 'backpack']);
    (data.equipment?.belt?.pouches || []).forEach((pouch, index) => visit(pouch.contents, ['equipment', 'belt', 'pouches', index, 'contents']));
    (data.equipment?.vest?.pouches || []).forEach((pouch, index) => visit(pouch.contents, ['equipment', 'vest', 'pouches', index, 'contents']));
    return found;
}

function getInventoryValueByPath(data, path) {
    return (Array.isArray(path) ? path : []).reduce(
        (value, key) => value === null || value === undefined ? undefined : value[key],
        data
    );
}

function getInventoryItemTemplate(item) {
    return (allTemplatesCache || []).find(template => Number(template.id) === Number(item?.templateId)) || null;
}

function getInventoryItemCategory(item) {
    return String(item?.category || getInventoryItemTemplate(item)?.category || '').trim().toLowerCase();
}

function getMagazineCompatibleWeaponIds(item) {
    const template = getInventoryItemTemplate(item);
    const compatible = template?.attributes?.compatible_weapons
        ?? item?.attributes?.compatible_weapons
        ?? [];
    return Array.isArray(compatible)
        ? compatible.map(value => Number(value)).filter(Number.isFinite)
        : [];
}

function getInventoryItemAccessActionPoints(item) {
    const template = getInventoryItemTemplate(item);
    const attributes = { ...(template?.attributes || {}), ...(item?.attributes || {}) };
    const candidates = [
        item?.accessActionPoints,
        item?.access_action_points,
        attributes.accessActionPoints,
        attributes.access_action_points,
        attributes.retrievalActionPoints,
        attributes.retrieval_action_points,
        attributes.openActionPoints,
        attributes.open_action_points,
        attributes.caseAccessActionPoints,
        attributes.case_access_action_points,
        attributes.accessCostOd,
        attributes.access_cost_od,
    ];
    const value = candidates.find(candidate => Number.isFinite(Number(candidate)));
    return Math.max(0, Number(value) || 0);
}

function getInventoryQuickAccessCategory(item) {
    const template = getInventoryItemTemplate(item);
    const category = getInventoryItemCategory(item);
    if (category !== 'consumable') return category;
    const section = String(
        item?.subcategory
        || item?.attributes?.section
        || template?.subcategory
        || template?.attributes?.section
        || ''
    ).trim().toLowerCase();
    return section === 'продукты' ? 'consumable' : 'med';
}

function isItemCompatibleWithPouch(item, pouch) {
    const template = getInventoryItemTemplate(pouch)
        || (allTemplatesCache || []).find(entry => Number(entry.id) === Number(pouch?.type));
    const allowed = pouch?.allowed_categories
        || pouch?.allowedCategories
        || pouch?.attributes?.allowed_categories
        || template?.attributes?.allowed_categories;
    if (!Array.isArray(allowed) || allowed.length === 0) return true;
    const category = getInventoryQuickAccessCategory(item);
    return allowed.map(value => String(value).toLowerCase()).includes(category);
}

function getDisabledArmRetrievalPenalty() {
    const zones = currentCharacterData?.health?.zones || {};
    return ['leftArm','rightArm'].some(key=>!prostheticLimb(currentCharacterData,key)&&zones[key]&&Number(zones[key].current)<=0)?1:0;
}

async function calculateInventoryAccess(item, itemPath) {
    if (!Array.isArray(allTemplatesCache) || allTemplatesCache.length === 0) {
        await getAllItemTemplates();
    }
    const path = Array.isArray(itemPath) ? itemPath : [];
    let baseActionPoints = 0;
    let quickAccessDiscount = 0;
    let source = 'unknown';

    if (path[0] === 'inventory' && path[1] === 'pockets') {
        baseActionPoints = traitCount(currentCharacterData,'Глубокие карманы')?2:1;
        source = 'pockets';
    } else if (path[0] === 'inventory' && path[1] === 'backpack') {
        baseActionPoints = 2;
        source = 'backpack';
    } else if (path[0] === 'equipment' && ['belt', 'vest'].includes(path[1]) && path[2] === 'pouches') {
        const pouch = getInventoryValueByPath(currentCharacterData, path.slice(0, 4));
        if (isItemCompatibleWithPouch(item, pouch)) {
            baseActionPoints = 1;
            const tactics = (Number(currentCharacterData?.skills?.other?.tactics?.base) || 0)
                + (Number(currentCharacterData?.skills?.other?.tactics?.bonus) || 0);
            quickAccessDiscount = tactics >= 15 ? 2 : 1;
            source = 'compatible_pouch';
        } else {
            baseActionPoints = 2;
            source = 'incompatible_pouch';
        }
    }

    path.forEach((part, index) => {
        if (part !== 'contents') return;
        if (path[0] === 'equipment' && path[2] === 'pouches' && index === 4) return;
        const container = getInventoryValueByPath(currentCharacterData, path.slice(0, index));
        let accessCost=getInventoryItemAccessActionPoints(container);
        if(String(container?.name||'').toLowerCase().includes('кейс')||getInventoryItemCategory(container)==='case'){
            const discount=2*traitCount(currentCharacterData,'Мастер хранения');if(discount)accessCost=Math.max(1,accessCost-discount);
        }
        baseActionPoints += accessCost;
    });

    const limbPenalty = getDisabledArmRetrievalPenalty();
    const retrievalActionPoints = Math.max(
        0,
        baseActionPoints - quickAccessDiscount + limbPenalty
    );
    return {
        source,
        baseActionPoints,
        quickAccessDiscount,
        limbPenalty,
        retrievalActionPoints,
        useActionDiscount: Math.max(0, quickAccessDiscount - baseActionPoints),
    };
}

async function spendInventoryAccessForCombat(item, itemPath, baseUseActionPoints = 0, pendingActionId = null, consumableSelection = null) {
    const combatState = window.locationCombatState;
    if (!combatState || combatState.status !== 'active') {
        return { totalActionPoints: 0, access: null };
    }
    const actor = combatState.current_character;
    if (!actor || actor.character_id !== currentCharacterId) {
        throw new Error('Сейчас не ход этого персонажа');
    }
    const access = await calculateInventoryAccess(item, itemPath);
    const useActionPoints = Math.max(
        0,
        Number(baseUseActionPoints || 0) - access.useActionDiscount
    );
    const totalActionPoints = access.retrievalActionPoints + useActionPoints;
    const resolvedPendingActionId = pendingActionId
        || `inventory-${actor.location_character_id}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const payment = await Server.spendLocationCombatResources(window.currentLobbyId, window.currentLocationId, {
        location_character_id: actor.location_character_id,
        action_points: totalActionPoints,
        allow_deferred: true,
        pending_action_id: resolvedPendingActionId,
        pending_action_label: `Использование: ${item?.name || 'предмет'}`,
        consumable_selection: consumableSelection,
    });
    return { totalActionPoints, useActionPoints, access, payment, pendingActionId: resolvedPendingActionId };
}

function combineCombatPayments(groups) {
    return groups.reduce((combined, group) => {
        const variants = Array.isArray(group) && group.length ? group : [{ actionPoints: 0, freeActions: 0 }];
        return combined.flatMap(current => variants.map(option => ({
            actionPoints: current.actionPoints + Number(option.actionPoints || 0),
            freeActions: current.freeActions + Number(option.freeActions || 0),
        })));
    }, [{ actionPoints: 0, freeActions: 0 }]).filter((option, index, all) =>
        all.findIndex(candidate =>
            candidate.actionPoints === option.actionPoints
            && candidate.freeActions === option.freeActions
        ) === index
    );
}

async function chooseAndSpendCombatPayment(title, groups) {
    const combatState = window.locationCombatState;
    if (!combatState || combatState.status !== 'active') return true;
    const actor = combatState.current_character;
    if (!actor || actor.character_id !== currentCharacterId) {
        throw new Error('Сейчас не ход этого персонажа');
    }
    const choices = combineCombatPayments(groups)
        .filter(option =>
            option.actionPoints <= Number(actor.action_points_current || 0)
            && option.freeActions <= Number(actor.free_actions_current || 0)
        )
        .map(option => ({
            ...option,
            label: [
                option.actionPoints ? `${option.actionPoints} ОД` : '',
                option.freeActions ? `${option.freeActions} СД` : '',
            ].filter(Boolean).join(' + ') || '0 ОД',
        }));
    if (!choices.length) throw new Error('Не хватает ОД или СД');
    const selected = await chooseConsumableApplication(title, choices);
    if (!selected) return false;
    await Server.spendLocationCombatResources(window.currentLobbyId, window.currentLocationId, {
        location_character_id: actor.location_character_id,
        action_points: selected.actionPoints,
        free_actions: selected.freeActions,
    });
    return true;
}

async function chooseAmmunitionPayment(title, groups) {
    const combatState = window.locationCombatState;
    if (!combatState || combatState.status !== 'active') {
        return { actionPoints: 0, freeActions: 0 };
    }
    const actor = combatState.current_character;
    if (!actor || actor.character_id !== currentCharacterId) {
        throw new Error('Сейчас не ход этого персонажа');
    }

    const currentActionPoints = Number(actor.action_points_current || 0);
    const currentFreeActions = Number(actor.free_actions_current || 0);
    const choices = combineCombatPayments(groups)
        // Deferred actions may owe AP, but free actions cannot be borrowed from a later turn.
        .filter(option => option.freeActions <= currentFreeActions)
        .map(option => {
            const remaining = Math.max(0, option.actionPoints - currentActionPoints);
            return {
                ...option,
                label: [
                    option.actionPoints ? `${option.actionPoints} ОД` : '',
                    option.freeActions ? `${option.freeActions} СД` : '',
                    remaining ? `останется ${remaining} ОД` : '',
                ].filter(Boolean).join(' + ') || '0 ОД',
            };
        });
    if (!choices.length) throw new Error('Не хватает СД');

    return chooseConsumableApplication(title, choices);
}

async function submitAmmunitionLoading(request, paymentGroups = []) {
    if (medicalOperationInProgress) return null;
    const characterId = currentCharacterId;
    const loading = JSON.parse(JSON.stringify(request));
    const sheet = document.getElementById('character-sheet-modal');
    const wasInert = sheet?.inert || false;
    let locked = false;
    try {
        if (autoSaveTimer) await forceSyncCharacter();
        await Server.waitForCharacterSave(characterId);
        if (currentCharacterId !== characterId || medicalOperationInProgress) return null;
        if (sheetSaveBlocked) throw new Error('Переоткройте лист перед зарядкой');
        medicalOperationInProgress = locked = true;
        medicalRemoteUpdate = null;
        if (sheet) sheet.inert = true;
        const payment = await chooseAmmunitionPayment('Оплата зарядки', paymentGroups);
        if (!payment) return null;
        const state = window.locationCombatState;
        let result;
        if (state?.status === 'active') {
            const actor = state.current_character;
            if (actor?.character_id !== characterId) throw new Error('Сейчас не ход этого персонажа');
            result = await Server.performLocationCombatAction(window.currentLobbyId, window.currentLocationId, {
                action_key: 'load_ammunition', location_character_id: actor.location_character_id,
                loading: { ...loading, action_points: payment.actionPoints, free_actions: payment.freeActions },
                pending_action_id: `loading-${actor.location_character_id}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
            });
            currentCharacterData = (await Server.getCharacter(characterId)).data;
        } else {
            result = await Server.loadAmmunition(characterId, { loading, character_revision: currentCharacterData._revision });
            currentCharacterData = result.data;
        }
        showNotification(result?.pending_action
            ? 'Зарядка начата. Патроны будут заряжены после полной оплаты ОД.'
            : 'Патроны заряжены', 'system');
        return result;
    } catch (error) {
        showNotification(error.message || 'Не удалось зарядить патроны', 'system');
        return null;
    } finally {
        if (locked) {
            if (medicalRemoteUpdate?.character_id === Number(characterId)
                && Number(medicalRemoteUpdate.data?._revision) > Number(currentCharacterData?._revision)) {
                currentCharacterData = medicalRemoteUpdate.data;
            }
            medicalRemoteUpdate = null;
            medicalOperationInProgress = false;
            if (sheet) sheet.inert = wasInert;
            await refreshSavedSheet(characterId);
        }
    }
}


async function inventoryItemPreparationPayments(item, itemPath, role) {
    const access = await calculateInventoryAccess(item, itemPath);
    const quick = access.source === 'pockets' || access.source === 'compatible_pouch';
    if (role === 'magazine') {
        return quick
            ? [{ actionPoints: 0, freeActions: 1 }, { actionPoints: 1, freeActions: 0 }]
            : [{ actionPoints: Math.max(1, access.retrievalActionPoints), freeActions: 0 }];
    }
    const retrieval = Math.max(1, Number(access.retrievalActionPoints || 0));
    return quick
        ? [{ actionPoints: 0, freeActions: 1 }, { actionPoints: 1, freeActions: 0 }]
        : [
            { actionPoints: Math.max(0, retrieval - 1), freeActions: 1 },
            { actionPoints: retrieval, freeActions: 0 },
        ];
}

function ammoLoadingKind(item) {
    const name = String(item?.name || '');
    if (isAmmoLoadingDevice(item) || Array.isArray(item?.ammo) || /лент/i.test(name)) return 'belt';
    return 'loose';
}

function isAmmoLoadingDevice(item) {
    return item?.category === 'magazine'
        && (
            item.attributes?.isLoader === true
            || item.attributes?.loadingDevice === true
            || /лент|спидлоадер/i.test(String(item.name || ''))
        );
}

function isLoaderCompatible(item, caliber) {
    if (!item || item.category !== 'magazine') return false;
    const loaderCaliber = getItemCaliber(item);
    // A loader without a known caliber cannot be used as a loaded source.
    // This also prevents old generic belt templates from bypassing matching.
    if (!loaderCaliber || !caliber) return false;
    return loaderCaliber === caliber;
}

function isBeltLoader(item) {
    return /лента/i.test(String(item?.name || ''));
}

function isSpeedloader(item) {
    return /спидлоадер/i.test(String(item?.name || ''));
}

function isRevolverWeapon(template) {
    return /револьвер/i.test(String(template?.name || ''));
}

function isSniperWeapon(template) {
    return /снайпер/i.test(String(template?.subcategory || ''))
        || /снайпер/i.test(String(template?.name || ''));
}

function canLoadFixedWeaponFromDevice(template, device) {
    if (isSpeedloader(device)) return isRevolverWeapon(template);
    if (isBeltLoader(device)) return isSniperWeapon(template);
    return false;
}

function canLoadMagazineFromDevice(magazine, device, caliber) {
    if (!isLoaderCompatible(device, caliber)) return false;
    if (isSpeedloader(device)) return false;
    return isBeltLoader(device) && magazine?.category === 'magazine';
}

function isAmmoFeederTool(item) {
    return item?.category === 'magazine'
        && (
            item.attributes?.loadingTool === 'feeder'
            || /подавач/i.test(String(item.name || ''))
        );
}

function isAmmoClip(item) {
    return /клипс/i.test(String(item?.name || ''));
}

function ammoSourceCount(item) {
    if (ammoLoadingKind(item) === 'loose') return Math.max(0, Number(item.quantity || 0));
    return (item.ammo || []).reduce((sum, stack) => sum + Math.max(0, Number(stack.quantity || 0)), 0);
}

function formatReloadAmmoComposition(item) {
    if (!item) return 'Пусто';
    if (ammoLoadingKind(item) === 'loose') {
        const quantity = ammoSourceCount(item);
        return quantity > 0 ? `${escapeHtml(formatAmmoStackLabel(item))} - ${quantity} шт.` : 'Пусто';
    }
    const stacks = Array.isArray(item.ammo)
        ? item.ammo.filter(stack => Number(stack?.quantity || 0) > 0)
        : [];
    if (!stacks.length) {
        const legacyCount = Math.max(0, Number(item.currentAmmo || 0));
        return legacyCount > 0 ? `Тип не указан - ${legacyCount} шт.` : 'Пусто';
    }
    return stacks
        .map(stack => `${escapeHtml(formatAmmoStackLabel(stack))} - ${Number(stack.quantity || 0)} шт.`)
        .join('<br>');
}

function formatCombatPaymentVariants(payments) {
    const unique = (payments || []).filter((payment, index, all) =>
        all.findIndex(candidate =>
            Number(candidate.actionPoints || 0) === Number(payment.actionPoints || 0)
            && Number(candidate.freeActions || 0) === Number(payment.freeActions || 0)
        ) === index
    );
    return unique.map(payment => [
        Number(payment.actionPoints || 0) ? `${Number(payment.actionPoints)} ОД` : '',
        Number(payment.freeActions || 0) ? `${Number(payment.freeActions)} СД` : '',
    ].filter(Boolean).join(' + ') || '0 ОД').join(' или ');
}

function reloadErgonomicsModifier(value) {
    const ergonomics = Math.max(0, Number(value || 0));
    if (ergonomics <= 20) return 2;
    if (ergonomics <= 50) return 1;
    if (ergonomics <= 90) return 0;
    if (ergonomics <= 99) return -1;
    return -2;
}

function renderReloadPreview(element, item, paymentRows) {
    if (!element) return;
    const rows = Array.isArray(paymentRows) ? paymentRows : [];
    element.innerHTML = `
        <div style="font-weight:700; margin-bottom:6px;">${escapeHtml(item?.name || 'Источник не выбран')}</div>
        <div style="display:grid; grid-template-columns:max-content 1fr; gap:5px 10px; align-items:start;">
            <strong>Боеприпасы:</strong>
            <div>${formatReloadAmmoComposition(item)}</div>
            <strong>Трата:</strong>
            <div>${rows.length
                ? rows.map(row => `${escapeHtml(row.label)}: ${escapeHtml(row.payment)}`).join('<br>')
                : 'ОД не тратятся'}</div>
        </div>`;
}

function magazineLoadingPlans(source, needed, targetMagazine = null, hasFeeder = false) {
    const kind = ammoLoadingKind(source);
    const available = Math.min(needed, ammoSourceCount(source));
    if (isAmmoClip(targetMagazine)) {
        return available >= needed && needed > 0 ? [{
            quantity: needed,
            label: `Зарядить клипсу (${needed} патр.)`,
            payments: [{ actionPoints: 2, freeActions: 0 }],
        }] : [];
    }
    if (kind === 'belt') {
        return available > 0 ? [{
            quantity: available,
            label: `Вставить ленту (${available} патр.)`,
            payments: [{ actionPoints: 2, freeActions: 0 }, { actionPoints: 1, freeActions: 1 }],
        }] : [];
    }
    const sizes = [1, 3];
    const plans = sizes.filter(size => available >= size).map(size => ({
        quantity: size,
        label: `Россыпь: ${size} патрон${size === 1 ? '' : 'а'}`,
        payments: size === sizes[0]
            ? [{ actionPoints: 0, freeActions: 1 }, { actionPoints: 1, freeActions: 0 }]
            : [{ actionPoints: 2, freeActions: 0 }, { actionPoints: 1, freeActions: 1 }],
    }));
    if (kind === 'loose' && hasFeeder) {
        const feederTiers = [
            { limit: 10, paymentIndex: 0 },
            { limit: 20, paymentIndex: 1 },
        ];
        feederTiers.forEach(({ limit, paymentIndex }) => {
            if (available <= 0 || (paymentIndex === 1 && available <= 10)) return;
            const quantity = Math.min(limit, available);
            plans.push({
                quantity,
                label: `Подавач: ${quantity} патронов${quantity < limit ? ` (тариф до ${limit})` : ''}`,
                usesFeeder: true,
                payments: paymentIndex === 0
                    ? [{ actionPoints: 0, freeActions: 1 }, { actionPoints: 1, freeActions: 0 }]
                    : [{ actionPoints: 2, freeActions: 0 }, { actionPoints: 1, freeActions: 1 }],
            });
        });
    }
    return plans;
}


function weaponSpecializationKey(template) {
    const category = String(template?.subcategory || '').toLowerCase();
    if (category.includes('дробов')) return 'shotguns';
    if (category.includes('снайпер')) return 'sniperRifles';
    if (category.includes('пистолет') && category.includes('пулем')) return 'smgs';
    if (category.includes('пистолет')) return 'pistols';
    if (category.includes('штурм') || category.includes('карабин')) return 'assaultRifles';
    if (category.includes('гранатом')) return 'grenadeLaunchers';
    if (category.includes('пулем')) return 'machineGuns';
    return null;
}

function fixedMagazineLoadingPlans(template, source, needed, prepared) {
    const sourceKind = ammoLoadingKind(source);
    const discount=traitCount(currentCharacterData,'Быстрые руки');
    const discounted=payments=>payments.map(p=>({...p,actionPoints:p.actionPoints?Math.max(1,p.actionPoints-discount):0}));
    if (sourceKind !== 'loose') return magazineLoadingPlans(source, needed).map(plan=>({...plan,includesStart:true,payments:discounted(plan.payments)}));
    const specialization = weaponSpecializationKey(template);
    const level = currentCharacterData?.skills?.specialized?.[specialization]?.level || 'unfamiliar';
    const quantity = level === 'professional' ? 2 : 1;
    const plans = [];
    if (
        !prepared
        && Number(template?.attributes?.fire_modes?.duplex_size || 0) >= 2
        && needed >= 2
        && ammoSourceCount(source) >= 2
    ) {
        plans.push({
            quantity: 2,
            label: 'Начать перезарядку и зарядить дуплетом 2 патрона',
            payments: [{ actionPoints: 2, freeActions: 0 }],
            includesStart: true,
        });
    }
    if (needed >= quantity && ammoSourceCount(source) >= quantity) {
        plans.push({
            quantity,
            label: `Зарядить ${quantity} патрон${quantity === 1 ? '' : 'а'} (${level === 'professional' ? 'Профессионал' : level === 'familiar' ? 'Знаком' : 'Не знаком'})`,
            payments: [{
                actionPoints: level === 'unfamiliar' ? 2 : 1,
                freeActions: 0,
            }],
        });
    }
    return plans.map(plan=>({...plan,includesStart:true,payments:discounted(plan.payments.map(p=>({...p,actionPoints:p.actionPoints+(!prepared&&!plan.includesStart?1:0)})))}));
}

window.calculateInventoryAccess = calculateInventoryAccess;

function findInventoryItemById(data, itemId) {
    if (!itemId) return null;
    return collectInventoryEntries(data, item => item?.id === itemId)[0] || null;
}

function resolveInventoryEntry(itemId, itemPath) {
    const path = Array.isArray(itemPath) ? itemPath : [];
    const atPath = getInventoryValueByPath(currentCharacterData, path);
    if (atPath && (itemId == null || String(atPath.id) === String(itemId))) {
        return { item: atPath, path };
    }
    if (itemId == null) return null;
    return collectInventoryEntries(
        currentCharacterData,
        item => item?.id != null && String(item.id) === String(itemId),
    )[0] || null;
}

function spendInventoryItemUses(entry, amount = 1) {
    if (!entry?.item) return false;
    const item = entry.item;
    let remaining = Math.max(0, Number(amount) || 0);
    const maxUses = Math.max(1, Number(item.maxUses ?? item.attributes?.uses ?? 1) || 1);
    let uses = Math.max(0, Number(item.uses ?? item.attributes?.uses_remaining ?? maxUses) || 0);
    while (remaining > 0 && Number(item.quantity || 0) > 0) {
        const spent = Math.min(uses || maxUses, remaining);
        uses = (uses || maxUses) - spent;
        remaining -= spent;
        if (uses <= 0) {
            item.quantity = Math.max(0, Number(item.quantity || 0) - 1);
            uses = item.quantity > 0 ? maxUses : 0;
        }
    }
    item.uses = uses;
    item.maxUses = maxUses;
    item.attributes = item.attributes || {};
    item.attributes.uses_remaining = uses;
    if (item.quantity <= 0) {
        const currentEntry = findInventoryItemById(currentCharacterData, item.id);
        removeItemByPath(currentEntry?.path || entry.path);
    }
    return remaining <= 0;
}

function getInventoryItemAvailableUses(item) {
    if (!item) return 0;
    const quantity = Math.max(0, Number(item.quantity || 0));
    const maxUses = Math.max(1, Number(item.maxUses ?? item.attributes?.uses ?? 1) || 1);
    const currentUses = Math.max(0, Number(item.uses ?? item.attributes?.uses_remaining ?? maxUses) || 0);
    return currentUses + Math.max(0, quantity - 1) * maxUses;
}

function isWaterSupplyConsumable(item) {
    const template = (allTemplatesCache || []).find(entry => entry.id == item?.templateId);
    const direct = template?.attributes?.consumable?.direct || item?.attributes?.consumable?.direct;
    if (direct && Object.keys(direct).length) {
        return Boolean(direct.water_source || (direct.satisfy_water && direct.nutrition === undefined));
    }
    return String(item?.name || '').trim().toLowerCase() === 'вода';
}

function spendWaterRequirement(data, fraction, allowAlcohol = false, consume = true) {
    const charges = Math.max(1, Math.ceil((Number(fraction) || 0) * 3 - 1e-6));
    const water = collectInventoryEntries(data, isWaterSupplyConsumable)
        .find(entry => {
            const maxUses = Math.max(1, Number(entry.item.maxUses ?? entry.item.attributes?.uses ?? 3) || 3);
            const currentUses = Math.max(0, Number(entry.item.uses ?? entry.item.attributes?.uses_remaining ?? maxUses) || 0);
            return currentUses + Math.max(0, Number(entry.item.quantity || 0) - 1) * maxUses >= charges;
        });
    if (water && (!consume || spendInventoryItemUses(water, charges))) {
        return { ok: true, source: 'water', itemName: water.item.name || 'Вода', charges };
    }
    if (allowAlcohol) {
        const alcohol = collectInventoryEntries(data, isAlcoholConsumable)
            .find(entry => Number(entry.item.quantity || 0) > 0);
        if (alcohol && (!consume || spendInventoryItemUses(alcohol, 1))) {
            return { ok: true, source: 'alcohol', itemName: alcohol.item.name || 'Алкоголь', charges: 1 };
        }
    }
    return { ok: false, source: null };
}

function findSmokingFireSource(data) {
    const sources = collectInventoryEntries(data, item =>
        Number(item?.quantity || 0) > 0
        && /зажигал|спич/i.test(String(item?.name || ''))
    );
    const lighter = sources.find(entry => /зажигал/i.test(String(entry.item?.name || '')));
    if (lighter) return { entry: lighter, consume: false };
    const matches = sources.find(entry => /спич/i.test(String(entry.item?.name || '')));
    return matches ? { entry: matches, consume: true } : null;
}

function chooseConsumableApplication(title, choices, { alwaysShow = false } = {}) {
    if (!Array.isArray(choices) || choices.length === 0) return Promise.resolve(null);
    if (choices.length === 1 && !alwaysShow) return Promise.resolve(choices[0]);
    return new Promise(resolve => {
        document.getElementById('consumable-application-modal')?.remove();
        const modal = document.createElement('div');
        modal.id = 'consumable-application-modal';
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content" style="max-width:520px;">
                <span class="close">&times;</span>
                <h3>${escapeHtml(title)}</h3>
                <div class="consumable-application-list" style="display:flex; flex-direction:column; gap:8px;"></div>
                <div class="form-actions" style="margin-top:12px;"><button type="button" class="btn btn-secondary cancel-btn">Отмена</button></div>
            </div>`;
        const onKeyDown = event => {
            if (event.key === 'Escape') finish(null);
        };
        const finish = value => {
            document.removeEventListener('keydown', onKeyDown);
            modal.remove();
            resolve(value);
        };
        modal.querySelector('.close').onclick = () => finish(null);
        modal.querySelector('.cancel-btn').onclick = () => finish(null);
        modal.addEventListener('click', event => { if (event.target === modal) finish(null); });
        choices.forEach(choice => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-secondary';
            button.style.textAlign = 'left';
            button.textContent = choice.label;
            button.disabled = choice.disabled === true;
            button.onclick = () => finish(choice);
            modal.querySelector('.consumable-application-list').appendChild(button);
        });
        document.body.appendChild(modal);
        document.addEventListener('keydown', onKeyDown);
        modal.style.display = 'flex';
    });
}

function isBloodPacketItem(item, template = null) {
    const resolved = template || (allTemplatesCache || []).find(entry => entry.id == item?.templateId);
    const profile = resolved?.attributes?.consumable || item?.attributes?.consumable;
    const direct = profile?.direct;
    if (direct && Object.keys(direct).length) {
        return Boolean(direct.blood_packet || (direct.blood_compatibility_required && direct.requires_infusion_tool));
    }
    return String(item?.name || '').trim().toLowerCase() === 'пакет крови';
}

async function resolveMedicalApplication(direct, health, itemName, costContext = null) {
    const effects = Array.isArray(health.effects) ? health.effects : [];
    const applications = Array.isArray(direct.applications) ? direct.applications : [];
    if (direct.blood_type_test) {
        const choices = [{ label: 'Определить группу крови персонажа', target: 'character', actionPoints: 0 }];
        collectInventoryEntries(currentCharacterData, item => isBloodPacketItem(item))
            .forEach(entry => choices.push({
                label: `Определить группу пакета крови${entry.item.attributes?.bloodTypeKnown ? ' (уже известна)' : ''}`,
                target: 'packet', entry, actionPoints: 0
            }));
        const selected = await chooseConsumableApplication(`Применить: ${itemName}`, choices);
        return selected ? { kind: 'blood_type_test', ...selected } : null;
    }
    if (applications.length) {
        const choices = [];
        effects.forEach(effect => {
            const bleed = getBleedingInfo(effect);
            if (!bleed || effect.closed || effect.suppressed) return;
            applications.forEach(application => {
                const outcome = getBleedingTreatmentOutcome(effect, application);
                if (!outcome) return;
                if (direct.limb_only && !['leftArm', 'rightArm', 'leftLeg', 'rightLeg'].includes(effect.area)) return;
                const actionPoints = getMedicalTreatmentActionPoints(
                    direct,
                    Number(application.action_points || 1)
                );
                const treatmentLabel = outcome.mode === 'weaken'
                    ? `ослабить до ${BLEEDING_STAGE_LABEL[outcome.resultStage]}`
                    : 'остановить';
                choices.push({
                    label: `${getEffectAreaLabel(effect.area)}: ${getBleedingEffectLabel(effect, bleed)} → ${treatmentLabel} · ${getMedicalApplicationCostLabel(actionPoints, costContext)}`,
                    effect,
                    application,
                    treatmentMode: outcome.mode,
                    resultStage: outcome.resultStage,
                    actionPoints,
                });
            });
        });
        if (!choices.length) throw new Error('Нет подходящего активного кровотечения');
        const selected = await chooseConsumableApplication(`Выберите кровотечение: ${itemName}`, choices, { alwaysShow: true });
        if (!selected) return null;
        return { kind: 'bleeding', ...selected };
    }
    if (direct.wound_treatment) {
        const actionPoints = Number(direct.action_points_cost || 1);
        const choices = effects.filter(effect => effect.type === 'untreated_wound').map(effect => ({
            label: `Обработать рану: ${getEffectAreaLabel(effect.area)} · ${getMedicalApplicationCostLabel(actionPoints, costContext)}`,
            effect,
            actionPoints,
        }));
        if (!choices.length) throw new Error('Нет необработанной раны');
        const selected = await chooseConsumableApplication(`Выберите рану: ${itemName}`, choices, { alwaysShow: true });
        return selected ? { kind: 'wound', ...selected } : null;
    }
    if (direct.catastrophic_limb_surgery) {
        const actionPoints = Number(direct.action_points_cost || 1);
        const fullRestoration = direct.catastrophic_limb_surgery === 'full_restoration';
        const minorParts = new Set(['nose', 'jaw', 'leftEar', 'rightEar', 'ear']);
        const choices = [];
        effects.forEach((effect) => {
            if (effect.type === 'fracture_unfixed') {
                choices.push({
                    label: `Лечить незафиксированный перелом: ${getEffectAreaLabel(effect.area)} · 3 зар. · 3 суток отдыха · ${getMedicalApplicationCostLabel(actionPoints, costContext)}`,
                    effect,
                    treatmentMode: 'treat_unfixed_fracture',
                    application: { item_uses: 3 },
                    actionPoints,
                });
                return;
            }
            if (effect.type === 'mangled_limb') {
                const itemUses = fullRestoration ? 1 : 5;
                choices.push({
                    label: `Восстановить искореженную конечность: ${getEffectAreaLabel(effect.area)} · ${itemUses} зар. · ${getMedicalApplicationCostLabel(actionPoints, costContext)}`,
                    effect,
                    treatmentMode: 'restore_mangled_limb',
                    application: { item_uses: itemUses },
                    actionPoints,
                });
                return;
            }
            if (!fullRestoration || effect.type !== 'organ_loss' || effect.treatment_window_expired) return;
            const itemUses = minorParts.has(String(effect.area || '')) ? 3 : 5;
            choices.push({
                label: `Восстановить утраченную часть: ${getEffectAreaLabel(effect.area)} · ${itemUses} зар. · ${getMedicalApplicationCostLabel(actionPoints, costContext)}`,
                effect,
                treatmentMode: 'restore_lost_part',
                application: { item_uses: itemUses },
                actionPoints,
            });
        });
        Object.entries(health.zones || {}).forEach(([area, zone]) => {
            if (!['leftArm', 'rightArm', 'leftLeg', 'rightLeg'].includes(area)
                || Number(zone?.current || 0) > 0) return;
            if (effects.some(effect =>
                ['mangled_limb', 'amputation'].includes(effect?.type)
                && effect.area === area
                && effect.active !== false
            )) return;
            choices.push({
                label: `Восстановить выбитую конечность: ${getEffectAreaLabel(area)} · 1 зар. · ${getMedicalApplicationCostLabel(actionPoints, costContext)}`,
                effect: { type: 'damaged_zone', area },
                treatmentMode: 'restore_disabled_limb',
                application: { item_uses: 1 },
                actionPoints,
            });
        });
        if (!choices.length) throw new Error('Нет травмы, которую можно восстановить этим набором');
        const selected = await chooseConsumableApplication(
            `Выберите операцию: ${itemName}`,
            choices,
            { alwaysShow: true }
        );
        return selected ? { kind: 'injury', ...selected } : null;
    }
    if (direct.special_limb_treatment) {
        const actionPoints = Number(direct.action_points_cost || 1);
        const areas = ['leftArm', 'rightArm', 'leftLeg', 'rightLeg'];
        if (direct.special_limb_treatment === 'chimera') areas.push('head');
        const choices = areas
            .filter(area => health.zones?.[area])
            .map(area => ({
                label: `${getEffectAreaLabel(area)} · ${getMedicalApplicationCostLabel(actionPoints, costContext)}`,
                effect: { type: 'body_zone', area },
                treatmentMode: direct.special_limb_treatment,
                actionPoints,
            }));
        if (!choices.length) throw new Error('Нет подходящих частей тела');
        const selected = await chooseConsumableApplication(
            `Выберите часть тела: ${itemName}`,
            choices,
            { alwaysShow: true }
        );
        return selected ? { kind: 'injury', ...selected } : null;
    }
    if (direct.fracture_splint || direct.cure_fracture || direct.target_body_part
        || direct.restore_limb_health || direct.temporary_limb_health_minutes
        || direct.temporary_limb_health_turns) {
        if (direct.affects_all_limbs
            && !direct.fracture_splint
            && !direct.cure_fracture
            && !direct.target_body_part
            && !direct.restore_limb_health) {
            return { kind: 'self', actionPoints: Number(direct.action_points_cost ?? 1) };
        }
        const actionPoints = Number(direct.action_points_cost || 1);
        const allowedTypes = new Set();
        if (direct.fracture_splint || direct.cure_fracture) allowedTypes.add('fracture');
        if (direct.suppress_limb_trauma) allowedTypes.add('fracture');
        if ((direct.restore_missing_part || direct.target_body_part) && !direct.surgical_kit) {
            allowedTypes.add('amputation');
            allowedTypes.add('organ_loss');
        }
        const hingedSplint = Boolean(direct.fracture_splint && direct.temporary_limb_health_minutes);
        const choices = effects.filter(effect => {
            if (!allowedTypes.has(effect.type)) return false;
            if (!direct.fracture_splint || effect.type !== 'fracture') return true;
            const remaining = hingedSplint
                ? Number(effect.hinged_fixation_seconds ?? 1800)
                : Number(effect.regular_fixation_seconds ?? 1800);
            return remaining > 0;
        }).map(effect => ({
            label: direct.fracture_splint && effect.type === 'fracture'
                ? `Зафиксировать перелом: ${getEffectAreaLabel(effect.area)} · ${getMedicalApplicationCostLabel(actionPoints, costContext)}`
                : `${getInjuryEffectLabel(effect)}: ${getEffectAreaLabel(effect.area)} · ${getMedicalApplicationCostLabel(actionPoints, costContext)}`,
            effect,
            treatmentMode: direct.fracture_splint && effect.type === 'fracture'
                ? 'fix_fracture'
                : 'treat_injury',
            actionPoints,
        }));
        const selectableLimbAreas = new Set(['leftArm', 'rightArm', 'leftLeg', 'rightLeg']);
        if (direct.restore_limb_health || direct.restore_full_body_part || direct.fracture_restore_health
            || direct.temporary_limb_health_minutes || direct.temporary_limb_health_turns) {
            const existingAreas = new Set(choices.map(choice => String(choice.effect?.area || '')));
            const separateSplintRestoration = Boolean(
                direct.fracture_splint
                && (direct.temporary_limb_health_minutes || direct.temporary_limb_health_turns)
            );
            Object.entries(health.zones || {}).forEach(([area, zone]) => {
                if (!selectableLimbAreas.has(area)
                    || (!separateSplintRestoration && existingAreas.has(area))) return;
                const current = Number(zone?.current || 0);
                const maximum = Number(zone?.max || 0);
                const requiresKnockedOutLimb = Boolean(
                    direct.temporary_limb_health_minutes || direct.temporary_limb_health_turns
                ) && !direct.restore_missing_part;
                if (requiresKnockedOutLimb ? current > 0 : current >= maximum) return;
                if (direct.surgical_kit && current > 0) return;
                if (direct.surgical_kit && effects.some(effect =>
                    ['mangled_limb', 'amputation'].includes(effect?.type)
                    && effect.area === area
                    && effect.active !== false
                )) return;
                const effect = { type: 'damaged_zone', area };
                choices.push({
                    label: separateSplintRestoration
                        ? `Временно восстановить до 1 ОЗ: ${getEffectAreaLabel(area)} · ${getMedicalApplicationCostLabel(actionPoints, costContext)}`
                        : `${getInjuryEffectLabel(effect)}: ${getEffectAreaLabel(area)} · ${getMedicalApplicationCostLabel(actionPoints, costContext)}`,
                    effect,
                    treatmentMode: separateSplintRestoration
                        ? 'restore_limb'
                        : (direct.surgical_kit ? 'restore_disabled_limb' : 'treat_injury'),
                    actionPoints,
                });
            });
        }
        if (!choices.length) throw new Error('Нет подходящей травмы');
        const selected = await chooseConsumableApplication(`Выберите травму: ${itemName}`, choices, { alwaysShow: true });
        return selected ? { kind: 'injury', ...selected } : null;
    }
    const defaultMedicalCost = direct.medical_difficulty !== undefined || direct.application_form === 'injectable' ? 1 : 0;
    return { kind: 'self', actionPoints: Number(direct.action_points_cost ?? defaultMedicalCost) };
}

async function useConsumable(item, itemPath, options = {}) {
    const template = (await getAllItemTemplates()).find(entry => entry.id == item.templateId);
    const templateAttributes = template?.attributes || {};
    const itemConsumable = item.attributes?.consumable || {};
    const templateConsumable = templateAttributes.consumable || {};
    const consumable = { ...itemConsumable, ...templateConsumable };
    const direct = { ...(itemConsumable.direct || {}), ...(templateConsumable.direct || {}) };
    if (direct.requires_water_fraction === undefined) {
        const requirementText = String(
            template?.description
            || templateAttributes.description
            || item.description
            || item.attributes?.description
            || ''
        ).toLowerCase();
        const waterMatch = requirementText.match(/(\d+)\s*\/\s*(\d+)[^.]{0,48}бутылк[^\s]*\s+воды/i);
        if (waterMatch) {
            const numerator = Number(waterMatch[1]);
            const denominator = Number(waterMatch[2]);
            if (numerator > 0 && denominator > 0) {
                direct.requires_water_fraction = numerator / denominator;
                const sentence = requirementText
                    .slice(requirementText.lastIndexOf('.', waterMatch.index) + 1)
                    .split('.')[0];
                if (sentence.includes('алкогол')) direct.water_or_alcohol = true;
            }
        }
    }
    if (direct.target_required && !options.targetCharacterId && !direct.blood_collection) {
        showNotification('Для этого предмета нужно выбрать цель через медицинское действие');
        return false;
    }
    const modifiers = Array.isArray(consumable.modifiers) ? consumable.modifiers : [];
    const statusRemovals = Array.isArray(consumable.status_removals) ? consumable.status_removals : [];
    const statusAdditions = Array.isArray(consumable.status_additions) ? consumable.status_additions : [];
    const targetData = options.targetData || currentCharacterData;
    const health = targetData.health || {};
    if (direct.requires_shock && !(health.effects || []).some(effect => ['shock', 'unconsciousness'].includes(effect?.type))) {
        showNotification('Нашатырь можно применить только к персонажу в болевом шоке');
        return false;
    }
    const combatState = window.locationCombatState;
    const isCombatActive = Boolean(combatState && combatState.status === 'active');
    let hasChanges = false;
    const itemName = String(item.name || '').toLowerCase();
    const directEffectTypes = new Set();

    if (direct.hp !== undefined) {
        directEffectTypes.add('heal');
        directEffectTypes.add('damage');
    }
    [
        ['radiation_delta', 'radiation'],
        ['intoxication_delta', 'intoxication'],
        ['exhaustion_delta', 'exhaustion'],
        ['stress_delta', 'stress'],
        ['stress_in_combat_delta', 'stress'],
        ['stress_safe_delta', 'stress'],
        ['pain_delta', 'pain'],
        ['infection_delta', 'infection'],
        ['temperature_delta', 'temperature'],
        ['psy_delta', 'psy'],
        ['strength_delta', 'strength'],
        ['agility_delta', 'agility'],
        ['accuracy_delta', 'accuracy'],
        ['weight_delta', 'weight'],
        ['will_delta', 'will'],
        ['psy_defense_delta', 'psy_defense'],
    ].forEach(([key, type]) => {
        if (direct[key] !== undefined) directEffectTypes.add(type);
    });
    if (direct.temporary_limb_health_minutes || direct.temporary_limb_health_turns) {
        directEffectTypes.add('limb_trauma_suppression');
        directEffectTypes.add('temporary_limb_restoration');
    }

    const effects = (
        Array.isArray(consumable.effects)
            ? consumable.effects
            : (templateAttributes.effects || item.attributes?.effects || [])
    ).filter(effect => {
        if (!effect || effect?.source === 'direct') return false;
        const type = String(effect.type || '').trim().toLowerCase();
        return !directEffectTypes.has(type);
    });

    effects.forEach(effect => {
        if (String(effect?.type || '').toLowerCase() !== 'radiation_filter') return;
        effect.tick = 'time_elapsed';
        effect.remaining = Number(effect.remaining ?? effect.max_hours ?? 24);
        effect.duration = effect.remaining;
        effect.time_unit = 'hour';
        effect.remaining_seconds = effect.remaining * 60 * 60;
        effect.capacity = Number(effect.capacity ?? direct.radiation_filter_capacity ?? 0);
        effect.remaining_capacity = effect.capacity;
    });
    if (Number(direct.radiation_filter_percent) > 0
        && !effects.some(effect => effect?.type === 'radiation_filter')) {
        effects.push({
            type: 'radiation_filter',
            name: `Выведение входящей радиации ${Number(direct.radiation_filter_percent)}%`,
            value: Number(direct.radiation_filter_percent),
            remaining: 24,
            duration: 24,
            tick: 'time_elapsed',
            time_unit: 'hour',
            remaining_seconds: 24 * 60 * 60,
            capacity: Number(direct.radiation_filter_capacity || 0),
            remaining_capacity: Number(direct.radiation_filter_capacity || 0),
            max_hours: 24,
        });
    }
    if (!templateConsumable?.direct) {
    const isFullRadist = itemName.includes('радист') && !itemName.includes('радист"-л')
        && !itemName.includes('радист-л');
    if (isFullRadist && !effects.some(effect => effect?.type === 'temporary_exhaustion')) {
        effects.push({
            type: 'temporary_exhaustion',
            name: 'Радист: истощение',
            remaining: 3,
            tick: 'turn_end',
            onExpire: [{ field: 'exhaustion', delta: -1, min: 0, max: 10 }],
        });
    }

    if (itemName.includes('самогон')) {
        direct.radiation_delta = -2.5;
        direct.intoxication_delta = 25;
        if (direct.exhaustion_delta === undefined) direct.exhaustion_delta = -0.5;
        if (direct.uses === undefined) direct.uses = 8;
    } else if (itemName.includes('вино') || itemName.includes('алког') || itemName.includes('спирт')) {
        if (direct.intoxication_delta === undefined) direct.intoxication_delta = 1;
    }
    if (itemName.includes('бинт')) {
        direct.treatment_action_points = 4;
        direct.treatment_time_uses_medicine = true;
        if (direct.bleeding_stop_light_cost === undefined) {
            direct.bleeding_stop_light_cost = 1;
            direct.bleeding_stop_type = 'external';
        }
    }
    if (itemName.includes('антисептический тампон')) {
        direct.treatment_action_points = 2;
        direct.treatment_time_uses_medicine = true;
    }
    if ((itemName.includes('жгут') || itemName.includes('турникет'))
        && !itemName.includes('шина шарнирова')) {
        direct.treatment_action_points = 4;
        direct.treatment_time_uses_medicine = true;
    }
    if (itemName.includes('губка коллагеновая')) {
        direct.bleeding_stop_light_cost = 1;
        direct.bleeding_stop_medium_cost = 2;
        direct.bleeding_stop_type = 'external';
    }
    if (itemName.includes('групп') && itemName.includes('кров')) {
        direct.blood_type_test = true;
    }
    if (itemName.includes('вода')) {
        direct.radiation_delta = -1;
        direct.intoxication_delta = -10;
        direct.exhaustion_delta = -0.5;
        if (direct.uses === undefined) direct.uses = 3;
        if (!template?.id && (!Number.isFinite(Number(item.uses)) || Number(item.uses) <= 0)) {
            item.uses = 3;
            item.maxUses = 3;
            item.attributes = item.attributes || {};
            item.attributes.uses_remaining = 3;
        }
    }
    if (Number(direct.radiation_delta) < 0) {
        const currentRadiation = Number(health.radiation || 0);
        const isWeakRadiationCleaner = [
            'самокрут',
            'сигарет',
            'пиво',
            'водк',
        ].some(name => itemName.includes(name));
        const isFoodProduct = direct.nutrition !== undefined || direct.satisfy_food;
        if (currentRadiation > 50 && isWeakRadiationCleaner) {
            delete direct.radiation_delta;
            showNotification('Эта вещь не выводит радиацию при накопленной дозе выше 50', 'system');
        } else if (currentRadiation > 75 && isFoodProduct) {
            delete direct.radiation_delta;
            showNotification('Продукты не выводят радиацию при накопленной дозе выше 75', 'system');
        }
    }
    if (itemName.includes('физраств') || itemName.includes('соляной раствор')) {
        direct.not_consumed = true;
    }
    }

    const adjust = (field, delta, min = 0, max = null) => {
        if (delta === undefined || delta === null || Number.isNaN(Number(delta)) || Number(delta) === 0) return;
        const defaultValue = field === 'temperature' ? 36 : 0;
        const current = Number(health[field] ?? defaultValue);
        let next = current + Number(delta);
        if (min !== null) next = Math.max(min, next);
        if (max !== null) next = Math.min(max, next);
        health[field] = next;
        hasChanges = true;
    };

    let medicalCostContext = null;
    if (isCombatActive) {
        try {
            medicalCostContext = await calculateInventoryAccess(item, itemPath);
        } catch (error) {
            showNotification(error.message || 'Не удалось рассчитать стоимость применения');
            return false;
        }
    }

    let application;
    try {
        application = options.preselectedApplication || await resolveMedicalApplication(
            direct,
            health,
            item.name || 'предмет',
            medicalCostContext
        );
    } catch (error) {
        showNotification(error.message || 'Предмет сейчас нельзя применить');
        return false;
    }
    if (!application) return false;
    const requiredItemUses = Number(application.application?.item_uses || 1);
    if (!direct.not_consumed && !options.skipItemConsumption
        && getInventoryItemAvailableUses(item) < requiredItemUses) {
        showNotification(`Недостаточно зарядов: требуется ${requiredItemUses}`);
        return false;
    }

    if (direct.use_limit) {
        health.combatMeta = health.combatMeta || {};
        const usage = health.combatMeta.consumableUsage || {};
        const usageKey = direct.exclusive_group || item.templateId || item.name;
        if (Number(usage[usageKey] || 0) >= Number(direct.use_limit || 1)) {
            showNotification('Лимит использования этого препарата исчерпан');
            return false;
        }
    }

    let infusionBonus = 0;
    if (direct.requires_infusion_tool) {
        const infusionTools = collectInventoryEntries(currentCharacterData, entry =>
            /капельница|хирургический набор|кустарный набор\s*[«"]?айболит|набор полного восстановления конечности/i.test(String(entry?.name || ''))
        );
        if (!infusionTools.length) {
            showNotification('Для применения нужна капельница или хирургический набор');
            return false;
        }
        if (infusionTools.some(entry => /капельница/i.test(String(entry.item.name || '')))) infusionBonus = 1;
    }
    if (direct.blood_compatibility_required) {
        const recipientType = Number(health.combatMeta?.bloodType || 0);
        const packetType = Number(item.attributes?.bloodType || item.attributes?.blood_type || 0);
        if (!recipientType || !packetType) {
            showNotification('Сначала нужно определить группу крови получателя и пакета');
            return false;
        }
        const compatibleGroups = { 1: [1], 2: [1, 2], 3: [1, 3], 4: [1, 2, 3, 4] };
        if (!(compatibleGroups[recipientType] || []).includes(packetType)) {
            showNotification('Группа крови не подходит');
            return false;
        }
    }

    let waterRequirement = null;
    let usingWithoutWater = false;
    const fireRequirement = direct.requires_fire
        ? findSmokingFireSource(currentCharacterData)
        : null;
    if (direct.requires_fire && !fireRequirement) {
        showNotification('Для курения нужна зажигалка или спичка');
        return false;
    }
    if (direct.requires_water_fraction) {
        const waterCheck = spendWaterRequirement(currentCharacterData, direct.requires_water_fraction, Boolean(direct.water_or_alcohol), false);
        if (!waterCheck.ok) {
            if (direct.exhaustion_if_no_water) {
                direct.exhaustion_delta = Number(direct.exhaustion_delta || 0) + Number(direct.exhaustion_if_no_water || 0);
                usingWithoutWater = true;
            } else {
                showNotification('Для использования не хватает воды');
                return false;
            }
        } else {
            waterRequirement = {
                fraction: direct.requires_water_fraction,
                allowAlcohol: Boolean(direct.water_or_alcohol),
            };
        }
    }

    if (options.requestTreatmentConsent && !options.treatmentConsentGranted) {
        let consent;
        try {
            consent = await options.requestTreatmentConsent({
                item_name: item.name || 'Медицинская процедура',
                application: application.label || application.kind || 'Применение препарата',
                action_points: Number(application.actionPoints || 0),
            });
        } catch (error) {
            showNotification(error.message || 'Не удалось запросить согласие на лечение');
            return false;
        }
        if (!consent?.allowed) {
            if (consent?.status === 'cancelled') {
                showNotification('Ожидание согласия на лечение отменено', 'system');
                return false;
            }
            const contest = consent?.result;
            showNotification(
                contest?.actor_strength
                    ? `Пациент отказался. Сила врача: ${contest.actor_strength.total}, пациента: ${contest.target_strength.total}`
                    : 'Пациент отказался от процедуры',
                'system',
            );
            return false;
        }
        options.treatmentConsentGranted = true;
        options.treatmentRequestId = consent.requestId;
    }

    const unsupportedSelfMedicalFields = [
        'applications', 'requires_injury', 'target_body_part', 'target_required',
        'requires_infusion_tool', 'blood_compatibility_required', 'blood_collection',
        'blood_type_test', 'requires_shock', 'fracture_splint', 'surgical_kit',
        'catastrophic_limb_surgery', 'special_limb_treatment', 'restore_missing_part',
        'restore_full_body_part', 'restore_limb_health', 'close_area_bleeding',
        'filter_charges', 'requires_gas_mask', 'wound_treatment', 'tourniquet',
        'limb_only', 'bleeding_stop_light_cost', 'bleeding_stop_medium_cost',
    ];
    const serverSelfMedicalProcedure = Boolean(
        application.kind === 'self'
        && (direct.medical_difficulty !== undefined || direct.application_form === 'injectable')
        && !unsupportedSelfMedicalFields.some(key => Boolean(direct[key]))
    );
    const serverInfusionProcedure = Boolean(
        application.kind === 'self'
        && direct.requires_infusion_tool
        && !direct.blood_collection
        && !direct.blood_type_test
    );
    const serverSpecialProcedure = Boolean(
        (application.kind === 'blood_type_test' && direct.blood_type_test)
        || (application.kind === 'self' && direct.blood_collection)
        || (application.kind === 'self' && direct.requires_shock)
    );
    const serverMedicalProcedure = Boolean(
        !options.skipMedicineCheck
        && template?.id
        && (
            serverSelfMedicalProcedure
            || serverInfusionProcedure
            || serverSpecialProcedure
            ||
            application.kind === 'bleeding'
            || (application.kind === 'wound' && direct.wound_treatment)
            || (application.kind === 'injury' && direct.special_limb_treatment)
            || (application.kind === 'injury' && direct.catastrophic_limb_surgery)
            || (application.kind === 'injury' && direct.surgical_kit)
            || (
                application.kind === 'injury'
                && direct.fracture_splint
                && ['fix_fracture', 'restore_limb'].includes(application.treatmentMode)
            )
        )
    );
    const unsupportedGeneralFields = [
        'applications', 'requires_injury', 'target_body_part', 'target_required',
        'requires_infusion_tool', 'blood_compatibility_required', 'blood_collection',
        'blood_type_test', 'requires_shock',
        'fracture_splint', 'surgical_kit', 'catastrophic_limb_surgery',
        'special_limb_treatment', 'restore_missing_part', 'restore_full_body_part',
        'restore_limb_health', 'temporary_limb_health_minutes', 'temporary_limb_health_turns',
        'affects_all_limbs', 'close_area_bleeding', 'filter_charges', 'requires_gas_mask',
        'wound_treatment', 'tourniquet', 'limb_only', 'stop_all_bleeding',
        'bleeding_stop_light_cost', 'bleeding_stop_medium_cost',
    ];
    const serverGeneralConsumable = Boolean(
        template?.id
        && application.kind === 'self'
        && !serverMedicalProcedure
        && direct.medical_difficulty === undefined
        && direct.application_form !== 'injectable'
        && !unsupportedGeneralFields.some(key => Boolean(direct[key]))
    );
    let medicalOperationId = options.deferredActionId || null;
    if (serverMedicalProcedure && !options.skipCombatPayment && !application.fineWork
        && traitCount(currentCharacterData,'Тонкая работа') && Number(application.actionPoints)>=3) {
        const cost=Number(application.actionPoints),reduction=traitCount(currentCharacterData,'Тонкая работа')>=2?6:4;
        const choice=await chooseConsumableApplication('Способ лечения',[
            {label:`Обычное лечение · ${cost} ОД`,fineWork:false},
            {label:`Тонкая работа · ${cost*2} ОД, СЛ −${reduction}`,fineWork:true},
        ]);
        if (!choice) return false;
        if (choice.fineWork) { application.fineWork=true;application.actionPoints=cost*2; }
    }
    if (isCombatActive && !options.skipCombatPayment) {
        let pendingActionId = null;
        try {
            // Persist the selection with payment before the server advances the turn.
            pendingActionId = `inventory-${combatState.current_character.location_character_id}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
            medicalOperationId = pendingActionId;
            const selection = {
                source: { path: [...itemPath], snapshot: JSON.parse(JSON.stringify(item)) },
                application: JSON.parse(JSON.stringify(application)),
                target_character_id: Number(options.targetCharacterId || currentCharacterId),
                treatment_request_id: options.treatmentRequestId || null,
                server_authoritative: serverMedicalProcedure || serverGeneralConsumable,
                server_operation: serverMedicalProcedure
                    ? 'medical'
                    : (serverGeneralConsumable ? 'general' : null),
            };
            const payment = await spendInventoryAccessForCombat(
                item, itemPath, application.actionPoints, pendingActionId, selection
            );
            if (payment.payment?.payment_complete === false) {
                showNotification(
                    `Начато длительное действие «${item.name}». Остаток ОД будет списан в следующих ходах.`,
                    'system'
                );
                return false;
            }
        } catch (error) {
            showNotification(error.message || 'Не хватает ОД');
            return false;
        }
    }
    if (serverMedicalProcedure) {
        try {
            const actor = combatState?.current_character;
            const response = await Server.applyMedicalProcedure(currentCharacterId, {
                operation_id: medicalOperationId
                    || `medical-${currentCharacterId}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
                deferred_action_id: isCombatActive ? medicalOperationId : null,
                target_character_id: Number(options.targetCharacterId || currentCharacterId),
                source: { path: [...itemPath], snapshot: JSON.parse(JSON.stringify(item)) },
                application: JSON.parse(JSON.stringify(application)),
                combat_location_id: isCombatActive ? Number(window.currentLocationId) : null,
                actor_location_character_id: isCombatActive
                    ? Number(options.interactionContext?.actorLocationCharacterId || actor?.location_character_id)
                    : null,
                interaction_request_id: options.treatmentRequestId || null,
            });
            options.serverMedicalResult = response;
            const result = response.medical_result || {};
            const details = result.roll == null
                ? ''
                : ` d20: ${result.roll}, СЛ: ${result.difficulty}`;
            if (result.success && result.roll == null) {
                showNotification(`${item.name} использован`, 'success');
            } else if (result.success) {
                showNotification(`Проверка Медицины успешна. ${details}`, 'success');
            } else {
                showNotification(`Проверка Медицины провалена. ${details}. Расходник потрачен.`);
            }
            return true;
        } catch (error) {
            showNotification(error.message || 'Не удалось применить медикамент');
            return false;
        }
    }
    if (serverGeneralConsumable) {
        try {
            const actor = combatState?.current_character;
            const response = await Server.applyGeneralConsumable(currentCharacterId, {
                operation_id: medicalOperationId
                    || `consumable-${currentCharacterId}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
                deferred_action_id: isCombatActive ? medicalOperationId : null,
                source: { path: [...itemPath], snapshot: JSON.parse(JSON.stringify(item)) },
                application: JSON.parse(JSON.stringify(application)),
                combat_location_id: isCombatActive ? Number(window.currentLocationId) : null,
                actor_location_character_id: isCombatActive
                    ? Number(options.interactionContext?.actorLocationCharacterId || actor?.location_character_id)
                    : null,
            });
            options.serverConsumableResult = response;
            const addiction = response.consumable_result?.consumable?.addiction;
            if (addiction?.acquired) {
                showNotification(`Получена зависимость: ${addiction.profile.label}`, 'system');
            } else if (addiction?.applicable && addiction.blocked) {
                showNotification('«Котик» заблокировал появление зависимости', 'success');
            }
            showNotification(`${item.name} использован`, 'success');
            return true;
        } catch (error) {
            showNotification(error.message || 'Не удалось применить расходник');
            return false;
        }
    }
    const needsMedicineCheck = application.kind !== 'wound' && (
        direct.medical_difficulty !== undefined
        || ['bleeding', 'injury'].includes(application.kind)
        || direct.requires_infusion_tool
    );
    if (needsMedicineCheck && !options.skipMedicineCheck) {
        const medicine = currentCharacterData.skills?.other?.medicine || {};
        const medicineBase = Number.isFinite(Number(medicine.base)) ? Number(medicine.base) : 10;
        const medicineSkillBonus = Math.floor((medicineBase - 10) / 2) + Number(medicine.bonus || 0);
        const medicationBonus = Number(direct.med_bonus || 0) + Number(application.application?.medicine_bonus || 0);
        const bleedingInfo = getBleedingInfo(application.effect);
        const bleedingDifficulties = { light: 8, medium: 10, severe: 12, extreme: 14 };
        let baseDifficulty = Number(direct.medical_difficulty);
        if (!Number.isFinite(baseDifficulty)) {
            if (bleedingInfo) {
                baseDifficulty = bleedingDifficulties[bleedingInfo.stage] || 8;
            } else if (application.effect?.type === 'fracture') {
                baseDifficulty = 10;
            } else if (direct.restore_missing_part || direct.restore_limb_health) {
                baseDifficulty = 14;
            } else {
                baseDifficulty = 4;
            }
        }
        const difficulty = Math.max(1, baseDifficulty - medicineSkillBonus - medicationBonus);
        const roll = Number.isInteger(options.medicineRoll) && options.medicineRoll >= 1 && options.medicineRoll <= 20
            ? options.medicineRoll
            : Math.floor(Math.random() * 20) + 1;
        const checkDetails = `d20: ${roll}, СЛ: ${difficulty} (база ${baseDifficulty}, Медицина ${medicineSkillBonus >= 0 ? '+' : ''}${medicineSkillBonus}, медикамент ${medicationBonus >= 0 ? '+' : ''}${medicationBonus})`;
        if (roll < difficulty) {
            const medicalRetry = {
                actor_character_id: Number(currentCharacterId),
                target_character_id: Number(options.targetCharacterId || currentCharacterId),
                item_id: item.id ?? null,
                item_path: Array.isArray(itemPath) ? [...itemPath] : [],
                item_snapshot: JSON.parse(JSON.stringify(item)),
                application: JSON.parse(JSON.stringify(application)),
                interaction_context: options.interactionContext ? {
                    actorLocationCharacterId: options.interactionContext.actorLocationCharacterId,
                    lobbyId: options.interactionContext.lobbyId,
                    locationId: options.interactionContext.locationId,
                } : null,
            };
            if (!direct.not_consumed) {
                spendInventoryItemUses({ item, path: itemPath }, Number(application.application?.item_uses || 1));
            }
            const actorHealth = currentCharacterData.health || (currentCharacterData.health = {});
            const actorCombatMeta = actorHealth.combatMeta || (actorHealth.combatMeta = {});
            actorCombatMeta.mustDoRetry = {
                kind: 'medical',
                name: `Применить ${item.name || 'медикамент'}`,
                skill_path: 'skills.other.medicine',
                skill_label: 'Медицина',
                difficulty: Math.max(1, baseDifficulty - medicationBonus),
                medical_retry: medicalRetry,
            };
            window.dispatchEvent(new CustomEvent('must-do-retry-registered', {
                detail: {
                    characterId: Number(currentCharacterId),
                    retry: {
                        kind: 'medical',
                        name: actorCombatMeta.mustDoRetry.name,
                        difficulty: actorCombatMeta.mustDoRetry.difficulty,
                    },
                },
            }));
            showNotification(`Проверка Медицины провалена. ${checkDetails}. Расходник потрачен.`);
            if (options.render !== false) renderInventoryTab(currentCharacterData);
            if (options.save !== false) {
                if (isCombatActive) {
                    try {
                        await Server.updateCharacter(currentCharacterId, { data: currentCharacterData });
                    } catch (error) {
                        showNotification(error.message || 'Не удалось сохранить повтор медицинской проверки');
                    }
                } else {
                    scheduleAutoSave();
                    forceSyncCharacter();
                }
            }
            return true;
        }
        showNotification(`Проверка Медицины успешна. ${checkDetails}`, 'system');
    }

    if (waterRequirement) {
        const spent = spendWaterRequirement(
            currentCharacterData,
            waterRequirement.fraction,
            waterRequirement.allowAlcohol,
            true
        );
        if (!spent.ok) {
            showNotification('Вода исчезла из инвентаря до применения. Предмет не израсходован.');
            return false;
        }
        if (spent.source === 'alcohol') {
            showNotification(`Для применения потрачено 1 использование: ${spent.itemName}`, 'system');
        } else {
            showNotification(`Для применения потрачено ${spent.charges}/3 бутылки воды`, 'system');
        }
    } else if (usingWithoutWater) {
        showNotification('Предмет использован без воды: получен штраф к истощению', 'system');
    }
    if (fireRequirement?.consume && !spendInventoryItemUses(fireRequirement.entry, 1)) {
        showNotification('Не удалось потратить спичку. Предмет не использован.');
        return false;
    }

    const applyTemporaryLimbEffect = (area, zone) => {
        const temporaryTurns = Number(direct.temporary_limb_health_turns || 0);
        const temporaryMinutes = Number(direct.temporary_limb_health_minutes || 0);
        if (!zone || (!temporaryTurns && !temporaryMinutes)) return false;
        const previousHealth = Number(zone.current || 0);
        if (previousHealth > 0 && !direct.suppress_limb_trauma && !direct.minimum_limb_health) {
            return false;
        }
        if (previousHealth <= 0) zone.current = 1;
        applyEffectToHealth(health, {
            type: 'temporary_limb_restoration',
            name: 'Временное восстановление конечности',
            area,
            source: item.id || item.name,
            previous_health: previousHealth,
            restore_on_expire: previousHealth <= 0,
            health_cap: previousHealth <= 0 ? 1 : undefined,
            minimum_limb_health: Number(direct.minimum_limb_health || 0),
            suppress_fracture: Boolean(direct.suppress_limb_trauma),
            remaining: temporaryMinutes || temporaryTurns,
            tick: temporaryMinutes ? 'time_elapsed' : 'turn_end',
            time_unit: temporaryMinutes ? 'minute' : undefined,
            remaining_seconds: temporaryMinutes ? temporaryMinutes * 60 : undefined,
        });
        return true;
    };

    effects.forEach(eff => {
        if (eff?.source === 'direct') return;
        const appliedEffect = { ...eff, source: eff.source || item.id || item.name };
        if (infusionBonus && appliedEffect.type === 'blood_recovery' && appliedEffect.remaining != null) {
            appliedEffect.remaining = Number(appliedEffect.remaining) + infusionBonus;
        }
        applyEffectToHealth(health, appliedEffect);
        hasChanges = true;
    });

    if (direct.affects_all_limbs) {
        ['leftArm', 'rightArm', 'leftLeg', 'rightLeg'].forEach((area) => {
            if (applyTemporaryLimbEffect(area, health.zones?.[area])) hasChanges = true;
        });
    }

    if (application.kind === 'bleeding') {
        const targetId = application.effect.id;
        if (application.treatmentMode === 'weaken') {
            const bleeding = getBleedingInfo(application.effect);
            application.effect.type = `bleeding_${bleeding.kind}_${application.resultStage}`;
            application.effect.name = `Кровотечение ${BLEEDING_KIND_LABEL[bleeding.kind]} ${BLEEDING_STAGE_LABEL[application.resultStage]}`;
            application.effect.closed = false;
            application.effect.suppressed = false;
            showNotification(
                `${getEffectAreaLabel(application.effect.area)}: кровотечение ослаблено до уровня «${BLEEDING_STAGE_LABEL[application.resultStage]}»`,
                'success'
            );
        } else {
            health.effects = (health.effects || []).filter(effect =>
                effect !== application.effect && (!targetId || effect.id !== targetId)
            );
            if (!application.application.treated) {
                applyEffectToHealth(health, {
                    type: 'untreated_wound', name: 'Необработанная рана', area: application.effect.area,
                    source: targetId || application.effect.source || item.name, tick: 'manual'
                });
            }
        }
        if (direct.tourniquet) {
            health.effects.forEach(effect => {
                if (getBleedingInfo(effect) && effect.area === application.effect.area) effect.suppressed = true;
            });
            applyEffectToHealth(health, {
                type: 'tourniquet', name: `Жгут: ${getEffectAreaLabel(application.effect.area)}`,
                area: application.effect.area, source: item.id || item.name, tick: 'manual'
            });
        }
        hasChanges = true;
    } else if (application.kind === 'wound') {
        health.effects = (health.effects || []).filter(effect => effect.id !== application.effect.id);
        hasChanges = true;
    } else if (application.kind === 'injury') {
        const targetArea = application.effect.area;
        const areaFractures = (health.effects || []).filter(effect =>
            ['fracture', 'fracture_fixed'].includes(effect?.type)
            && effect.area === targetArea
        );
        const isDelayedSpecialTreatment = Boolean(direct.special_limb_treatment);
        const isCatastrophicSurgery = ['restore_mangled_limb', 'restore_lost_part'].includes(
            application.treatmentMode
        );
        if (isCatastrophicSurgery) {
            health.effects = (health.effects || []).filter(effect =>
                effect !== application.effect
                && (!application.effect.id || effect.id !== application.effect.id)
            );
        }
        if (direct.restore_missing_part) {
            health.effects = (health.effects || []).filter(effect =>
                effect !== application.effect
                && (!application.effect.id || effect.id !== application.effect.id)
            );
        }
        if (direct.cure_fracture && !isDelayedSpecialTreatment
            && application.treatmentMode !== 'treat_unfixed_fracture') {
            health.effects = (health.effects || []).filter(effect => effect.id !== application.effect.id);
        }
        if (application.treatmentMode === 'treat_unfixed_fracture') {
            applyEffectToHealth(health, {
                type: 'delayed_limb_treatment',
                name: `Лечение незафиксированного перелома: ${getEffectAreaLabel(targetArea)}`,
                area: targetArea,
                source: item.id || item.name,
                cure_fracture: true,
                remaining: 72,
                remaining_seconds: 259200,
                tick: 'time_elapsed',
                time_unit: 'hour',
            });
        }
        if (direct.fracture_splint && application.treatmentMode === 'fix_fracture') {
            const targetArea = application.effect.area;
            health.effects = (health.effects || []).filter(effect => {
                if (!effect) return false;
                if (application.effect.id && effect.id === application.effect.id) return false;
                return !(effect.type === 'fracture'
                    && effect.area === targetArea
                    && (!application.effect.source || effect.source === application.effect.source));
            });
            adjust('painLevel', -1, 0, 10);
            applyEffectToHealth(health, {
                type: 'fracture_fixed',
                name: 'Зафиксированный перелом',
                area: targetArea,
                source: item.id || item.name,
                value: 1,
                remaining: 24,
                tick: 'time_elapsed',
                time_unit: 'hour',
                remaining_seconds: 86400,
            });
        }
        const zone = health.zones?.[targetArea];
        if (zone && application.treatmentMode === 'restore_mangled_limb') {
            const restoredHealth = direct.catastrophic_limb_surgery === 'full_restoration'
                ? Number(zone.max || 0)
                : Math.min(Number(zone.max || 0), Number(direct.restore_limb_health || 30));
            zone.current = Math.max(0, restoredHealth);
            zone.destructionDamage = Math.max(0, Number(zone.max || 0) - zone.current);
        }
        if (zone && direct.restore_full_body_part
            && application.treatmentMode !== 'treat_unfixed_fracture') {
            zone.current = Math.max(0, Number(zone.max || 0));
            zone.destructionDamage = 0;
        }
        if (isDelayedSpecialTreatment) {
            if (direct.special_limb_treatment === 'chimera' && targetArea === 'head') {
                applyEffectToHealth(health, {
                    type: 'death',
                    name: 'Смерть после применения «Химеры»',
                    area: targetArea,
                    source: item.id || item.name,
                    tick: 'manual',
                });
            } else if (direct.special_limb_treatment === 'chimera' && !areaFractures.length) {
                const damage = Math.abs(Number(direct.invalid_limb_damage || -200));
                const zoneHealth = Math.max(0, Number(zone?.current || 0));
                if (zone) zone.current = Math.max(0, zoneHealth - damage);
                const overflow = Math.max(0, damage - zoneHealth);
                if (overflow > 0) {
                    health.current = Math.max(0, Number(health.current || 0) - overflow);
                }
            } else {
                const delayMinutes = Number(direct.delayed_limb_treatment_minutes || direct.delay || 1);
                applyEffectToHealth(health, {
                    type: 'delayed_limb_treatment',
                    name: `${item.name}: лечение ${getEffectAreaLabel(targetArea)}`,
                    area: targetArea,
                    source: item.id || item.name,
                    cure_fracture: Boolean(direct.cure_fracture),
                    restore_limb_health: direct.special_limb_treatment === 'second_life'
                        ? Number(direct.restore_limb_health || 50)
                        : undefined,
                    remaining: delayMinutes,
                    remaining_seconds: delayMinutes * 60,
                    tick: 'time_elapsed',
                    time_unit: 'minute',
                });
            }
        }
        if (!direct.fracture_splint || application.treatmentMode === 'restore_limb') {
            if (!isDelayedSpecialTreatment) applyTemporaryLimbEffect(targetArea, zone);
        }
        if (zone && direct.restore_limb_health
            && !direct.temporary_limb_health_minutes && !direct.temporary_limb_health_turns
            && !isDelayedSpecialTreatment
            && application.treatmentMode !== 'treat_unfixed_fracture') {
            zone.current = Math.min(
                Number(zone.max || direct.restore_limb_health),
                Math.max(Number(zone.current || 0), Number(direct.restore_limb_health))
            );
            zone.destructionDamage = Math.max(0, Number(zone.max || 0) - zone.current);
        }
        if (direct.close_area_bleeding || ['second_life', 'chimera'].includes(direct.special_limb_treatment)) {
            health.effects = (health.effects || []).filter(effect => !(getBleedingInfo(effect) && effect.area === targetArea));
        }
        hasChanges = true;
    }

    if (direct.stop_all_bleeding) {
        health.effects = (health.effects || []).filter(effect => !getBleedingInfo(effect));
        hasChanges = true;
    }
    if (direct.clear_breathless) {
        health.effects = (health.effects || []).filter(effect => !['breathless', 'shortness_of_breath'].includes(effect?.type));
        hasChanges = true;
    }

    if (direct.use_limit) {
        health.combatMeta = health.combatMeta || {};
        health.combatMeta.consumableUsage = health.combatMeta.consumableUsage || {};
        const usageKey = direct.exclusive_group || item.templateId || item.name;
        health.combatMeta.consumableUsage[usageKey] = Number(health.combatMeta.consumableUsage[usageKey] || 0) + 1;
        hasChanges = true;
    }

    if (direct.infection_block_days) {
        applyEffectToHealth(health, {
            type: 'infection_growth_block', name: 'Блок нарастания заражения',
            remaining: Number(direct.infection_block_days), tick: 'day_start', time_unit: 'day', source: item.id || item.name
        });
        hasChanges = true;
    }
    if (direct.infection_block_chance) {
        const roll = Math.random() * 100;
        if (roll < Number(direct.infection_block_chance)) {
            applyEffectToHealth(health, {
                type: 'infection_growth_block', name: 'Блок нарастания заражения',
                remaining: 1, tick: 'day_start', time_unit: 'day', source: item.id || item.name
            });
            showNotification(`Нарастание заражения остановлено на сутки (${direct.infection_block_chance}% сработали)`, 'success');
        } else {
            showNotification(`Блок заражения не сработал (шанс ${direct.infection_block_chance}%)`, 'system');
        }
        hasChanges = true;
    }

    if (direct.satisfy_sleep || direct.nutrition !== undefined || direct.satisfy_water) {
        health.needs = normalizeDailyNeeds(health.needs);
        if (direct.satisfy_sleep) health.needs.sleptToday = true;
        if (direct.nutrition !== undefined) health.needs.mealsToday = Math.min(3, health.needs.mealsToday + 1);
        if (direct.satisfy_water) {
            health.needs.drinksToday = Math.min(3, health.needs.drinksToday + 1);
        }
        hasChanges = true;
    }

    if (direct.blood_collection) {
        const packetTemplate = (allTemplatesCache || []).find(entry =>
            entry.category === 'consumable' && isBloodPacketItem(entry, entry)
        );
        if (!packetTemplate) {
            showNotification('Шаблон пакета крови не найден');
            return false;
        }
        const packet = createItemFromTemplate(packetTemplate);
        health.combatMeta = health.combatMeta || {};
        let donorBloodType = Number(health.combatMeta.bloodType || 0);
        if (![1, 2, 3, 4].includes(donorBloodType)) {
            const result = rollBloodType();
            donorBloodType = result.bloodType;
            health.combatMeta.bloodType = donorBloodType;
            health.combatMeta.bloodTypeRoll = result.roll;
        }
        packet.attributes = packet.attributes || {};
        packet.attributes.bloodType = donorBloodType;
        packet.attributes.bloodTypeKnown = Boolean(health.combatMeta.bloodTypeKnown);
        packet.isStackable = false;
        currentCharacterData.inventory = currentCharacterData.inventory || {};
        currentCharacterData.inventory.pockets = currentCharacterData.inventory.pockets || [];
        currentCharacterData.inventory.pockets.push(packet);
        const order = ['normal', 'light', 'medium', 'severe', 'critical'];
        const currentStage = String(health.blood || health.bloodStage || 'normal').toLowerCase();
        health.blood = order[Math.min(order.length - 1, Math.max(0, order.indexOf(currentStage)) + 2)];
        health.bloodStage = health.blood;
        hasChanges = true;
    }

    if (direct.hp_max_delta !== undefined) {
        health.max = Math.max(1, Number(health.max || 0) + Number(direct.hp_max_delta || 0));
        hasChanges = true;
    }
    if (direct.post_duration_hp_delta !== undefined || direct.hp_max_delta !== undefined) {
        const onExpire = [];
        if (direct.post_duration_hp_delta !== undefined) {
            onExpire.push({ field: 'current', delta: Number(direct.post_duration_hp_delta || 0), min: 0 });
        }
        if (direct.hp_max_delta !== undefined) {
            onExpire.push({ field: 'max', delta: -Number(direct.hp_max_delta || 0), min: 1 });
        }
        applyEffectToHealth(health, {
            type: 'stimulant_crash', name: `${item.name}: окончание действия`,
            remaining: Number(direct.duration || 1), tick: direct.duration_phase || 'turn_end',
            source: `${item.id || item.name}:crash`, onExpire
        });
        hasChanges = true;
    }
    if (direct.organ_toughness_multiplier !== undefined) {
        health.combatMeta = health.combatMeta || {};
        health.combatMeta.consumableModifiers = health.combatMeta.consumableModifiers || [];
        health.combatMeta.consumableModifiers.push({
            stat: 'organ_toughness_multiplier', value: Number(direct.organ_toughness_multiplier || 1),
            remaining: Number(direct.duration || 1), tick: direct.duration_phase || 'turn_end', note: item.name
        });
        hasChanges = true;
    }
    if (direct.action_points_duration) {
        health.combatMeta = health.combatMeta || {};
        health.combatMeta.consumableModifiers = health.combatMeta.consumableModifiers || [];
        health.combatMeta.consumableModifiers.push({
            stat: 'action_points', value: Number(direct.action_points_delta || 0),
            remaining: Number(direct.action_points_duration), tick: 'turn_end', note: item.name
        });
        hasChanges = true;
    }
    if (direct.pain_block_turns) {
        applyEffectToHealth(health, {
            type: 'pain_block', name: `${item.name}: блок боли`, value: Number(direct.pain_block_turns),
            remaining: Number(direct.pain_block_turns), tick: 'turn_end', source: item.id || item.name,
            blocks_new_pain: true, return_fraction: Number(direct.blocked_pain_return_fraction ?? 1),
            exhaustion_on_expire: Number(direct.exhaustion_on_expire || 0)
        });
        hasChanges = true;
    }

    if (direct.hp !== undefined) {
        const hpDelta = Number(direct.hp);
        if (hpDelta > 0) {
            applyEffectToHealth(health, { type: 'heal', value: hpDelta, source: item.id || item.name });
        } else {
            health.current = Math.max(0, Number(health.current ?? 0) + hpDelta);
        }
        hasChanges = true;
    }
    adjust('radiation', direct.radiation_delta, 0, null);
    adjust('temperature', direct.temperature_delta, 0, null);
    adjust('psyState', direct.psy_delta, 0, 50);
    adjust('infection', direct.infection_delta, 0, 100);
    adjust('painLevel', direct.pain_delta, 0, 10);
    adjust('exhaustion', direct.exhaustion_delta, 0, 10);
    adjust('stress', direct.stress_delta, 0, 10);
    adjust('stress', isCombatActive ? direct.stress_in_combat_delta : direct.stress_safe_delta, 0, 10);
    adjust('intoxication', direct.intoxication_delta, 0, null);

    if (direct.pain_block_turns || direct.stress_block_turns || direct.addiction_block_hours || direct.medical_difficulty || direct.application_form || direct.sleep_block_hours || direct.will_shock_bonus || direct.will_shock_advantage) {
        if (!health.combatMeta) health.combatMeta = {};
        if (direct.pain_block_turns) health.combatMeta.painBlockTurns = Number(direct.pain_block_turns) || 0;
        if (direct.stress_block_turns) health.combatMeta.stressBlockTurns = Number(direct.stress_block_turns) || 0;
        if (direct.addiction_block_hours) health.combatMeta.addictionBlockHours = Number(direct.addiction_block_hours) || 0;
        if (direct.medical_difficulty !== undefined) health.combatMeta.medicalDifficulty = Number(direct.medical_difficulty) || 0;
        if (direct.application_form) health.combatMeta.applicationForm = String(direct.application_form);
        if (direct.sleep_block_hours) health.combatMeta.sleepBlockHours = Number(direct.sleep_block_hours) || 0;
        if (direct.will_shock_bonus !== undefined) health.combatMeta.willShockBonus = Number(direct.will_shock_bonus) || 0;
        if (direct.will_shock_advantage !== undefined) health.combatMeta.willShockAdvantage = Boolean(direct.will_shock_advantage);
        hasChanges = true;
    }

    if (direct.withdrawal_check_difficulty_reduction) {
        const reduction = Number(direct.withdrawal_check_difficulty_reduction) || 0;
        const delayMinutes = Number(direct.withdrawal_support_delay_minutes ?? 1);
        const durationMinutes = Number(direct.withdrawal_support_duration_minutes ?? 60);
        const supportEffect = {
            type: 'withdrawal_support',
            name: `${item.name}: поддержка при ломке`,
            source: item.id || item.name,
            active: true,
            tick: 'time_elapsed',
            time_unit: 'minute',
            remaining: durationMinutes,
            remaining_seconds: durationMinutes * 60,
            withdrawal_check_difficulty_reduction: reduction,
            forced_intoxication_stage: 'extreme',
            note: `Сложность проверок ломки снижена на ${reduction}.`,
        };
        if (delayMinutes > 0) {
            applyEffectToHealth(health, {
                type: 'withdrawal_support_pending',
                name: `${item.name}: действие через ${delayMinutes} мин.`,
                source: item.id || item.name,
                active: true,
                tick: 'time_elapsed',
                time_unit: 'minute',
                remaining: delayMinutes,
                remaining_seconds: delayMinutes * 60,
                activate_effects: [supportEffect],
            });
        } else {
            applyEffectToHealth(health, supportEffect);
        }
        hasChanges = true;
    }

    if (modifiers.length > 0) {
        if (!health.combatMeta) health.combatMeta = {};
        if (!Array.isArray(health.combatMeta.consumableModifiers)) {
            health.combatMeta.consumableModifiers = [];
        }
        modifiers.forEach((modifier) => {
            if (!modifier || typeof modifier !== 'object') return;
            const stat = String(modifier.stat || 'generic');
            if (direct[`${stat}_delta`] !== undefined) return;
            const remaining = modifier.remaining ?? direct.duration ?? null;
            if (remaining === null || Number(remaining) <= 0) return;
            health.combatMeta.consumableModifiers.push({
                stat,
                value: Number(modifier.value ?? 0) || 0,
                remaining: Number(remaining),
                tick: modifier.tick || direct.duration_phase || 'turn_end',
                note: modifier.note || '',
            });
        });
        hasChanges = true;
    }

    if (direct.bleeding_modifier_delta !== undefined) {
        if (!health.combatMeta) health.combatMeta = {};
        if (!Array.isArray(health.combatMeta.bleedingModifiers)) {
            health.combatMeta.bleedingModifiers = [];
        }
        health.combatMeta.bleedingModifiers.push({
            affects_severity: true,
            stat: 'bleeding',
            value: Number(direct.bleeding_modifier_delta) || 0,
            remaining: direct.duration ?? null,
            note: direct.bleeding_modifier_scope === 'combat' ? 'hematogen' : 'consumable',
            scope: direct.bleeding_modifier_scope || 'character',
        });
        hasChanges = true;
    }

    if (!Array.isArray(direct.applications) && (direct.bleeding_stop_light_cost !== undefined || direct.bleeding_stop_medium_cost !== undefined)) {
        const order = ['bleeding_external_light', 'bleeding_external_medium'];
        const target = Array.isArray(health.effects) ? health.effects.find(effect => order.includes(String(effect?.type || '').trim())) : null;
        if (target) {
            const targetType = String(target.type || '').trim();
            health.effects = health.effects.filter(effect => String(effect?.type || '').trim() !== targetType);
            hasChanges = true;
        }
        if (!health.combatMeta) health.combatMeta = {};
        health.combatMeta.collagenSponge = {
            lightCost: direct.bleeding_stop_light_cost ?? null,
            mediumCost: direct.bleeding_stop_medium_cost ?? null,
            stopType: direct.bleeding_stop_type || 'external',
        };
        hasChanges = true;
    }

    if (direct.nutrition !== undefined) {
        if (!health.combatMeta) health.combatMeta = {};
        health.combatMeta.nutrition = Number(health.combatMeta.nutrition || 0) + (Number(direct.nutrition) || 0);
        hasChanges = true;
    }

    if (direct.blood_type_test) {
        if (!health.combatMeta) health.combatMeta = {};
        if (application.kind === 'blood_type_test' && application.target === 'packet') {
            const packet = application.entry?.item;
            let bloodType = Number(packet?.attributes?.bloodType || packet?.attributes?.blood_type || 0);
            if (![1, 2, 3, 4].includes(bloodType)) {
                bloodType = rollBloodType().bloodType;
                packet.attributes.bloodType = bloodType;
            }
            packet.attributes.bloodTypeKnown = true;
            showNotification(`Группа крови в пакете: ${formatBloodType(bloodType)}`, 'success');
        } else {
            const storedType = Number(health.combatMeta.bloodType || 0);
            const result = [1, 2, 3, 4].includes(storedType)
                ? { bloodType: storedType, roll: health.combatMeta.bloodTypeRoll ?? null }
                : rollBloodType();
            health.combatMeta.bloodTypeTested = true;
            health.combatMeta.bloodTypeKnown = true;
            health.combatMeta.bloodType = result.bloodType;
            health.combatMeta.bloodTypeRoll = result.roll;
            showNotification(`Группа крови определена: ${formatBloodType(result.bloodType)}${result.roll == null ? '' : ` (d20: ${result.roll})`}`, 'success');
        }
        hasChanges = true;
    }

    if (direct.fracture_splint) {
        if (!health.combatMeta) health.combatMeta = {};
        health.combatMeta.fractureSplint = true;
        health.combatMeta.fractureSplintTurns = Number(direct.fracture_duration_turns) || 4;
        health.combatMeta.fractureRestoreHealth = Number(direct.fracture_restore_health) || 1;
        health.combatMeta.fractureFixedDurationMinutes = Number(direct.fracture_duration_minutes) || 0;
        hasChanges = true;
    }

    if (direct.filter_charges !== undefined) {
        if (!health.combatMeta) health.combatMeta = {};
        health.combatMeta.filterCharges = Number(direct.filter_charges) || 0;
        health.combatMeta.requiresGasMask = Boolean(direct.requires_gas_mask);
        hasChanges = true;
    }

    if (direct.strength_delta !== undefined || direct.agility_delta !== undefined || direct.accuracy_delta !== undefined || direct.weight_delta !== undefined || direct.will_delta !== undefined || direct.psy_defense_delta !== undefined) {
        if (!health.combatMeta) health.combatMeta = {};
        if (!Array.isArray(health.combatMeta.consumableModifiers)) {
            health.combatMeta.consumableModifiers = [];
        }
        [
            ['strength', direct.strength_delta],
            ['agility', direct.agility_delta],
            ['accuracy', direct.accuracy_delta],
            ['weight', direct.weight_delta],
            ['will', direct.will_delta],
            ['psy_defense', direct.psy_defense_delta],
        ].forEach(([stat, value]) => {
            if (value === undefined) return;
            const remaining = direct.duration ?? null;
            if (remaining === null || Number(remaining) <= 0) return;
            health.combatMeta.consumableModifiers.push({
                stat,
                value: Number(value) || 0,
                remaining: Number(remaining),
                tick: direct.duration_phase || 'turn_end',
                note: 'stat_bonus',
            });
        });
        hasChanges = true;
    }

    if (statusRemovals.length > 0 && Array.isArray(health.effects)) {
        const targetSet = new Set(statusRemovals.map(item => String(item).trim()).filter(Boolean));
        const before = health.effects.length;
        health.effects = health.effects.filter((effect) => {
            const type = String(effect?.type || '').trim();
            return !targetSet.has(type);
        });
        if (health.effects.length !== before) {
            hasChanges = true;
        }
    }

    if (statusAdditions.length > 0) {
        if (!health.combatMeta) health.combatMeta = {};
        if (!Array.isArray(health.combatMeta.consumableStatusAdditions)) {
            health.combatMeta.consumableStatusAdditions = [];
        }
        statusAdditions.forEach((status) => {
            const normalized = String(status || '').trim();
            if (!normalized) return;
            health.combatMeta.consumableStatusAdditions.push(normalized);
            applyEffectToHealth(health, { type: normalized, name: normalized, value: 0, remaining: direct.duration ?? null, tick: 'manual' });
        });
        hasChanges = true;
    }

    if (hasChanges) {
        targetData.health = health;
        normalizeCharacterEffects(targetData);
        syncHealthDerivedStatuses(targetData.health);
    } else if (effects.length === 0) {
        showNotification('Предмет не имеет эффектов');
        return;
    }

    options.consumableSideEffects.exposure = {
        item_name: item.name,
        price: Number(template?.price ?? item.price ?? item.attributes?.price ?? 0),
        intoxication: Math.max(0, Number(direct.intoxication_delta || 0)),
        exhaustion_relief: Math.max(0, -Number(direct.exhaustion_delta || 0)),
        addiction_block_hours: Math.max(0, Number(direct.addiction_block_hours || 0)),
    };

    if (!direct.not_consumed && !options.skipItemConsumption) {
        spendInventoryItemUses({ item, path: itemPath }, Number(application.application?.item_uses || 1));
    }
    if (isCombatActive && Number(direct.action_points_delta || 0) !== 0) {
        options.consumableSideEffects.action_points_delta = Number(direct.action_points_delta);
    }
    if (options.save !== false) showNotification(`${item.name} использован`, 'success');
    if (options.render !== false) {
        renderInventoryTab(currentCharacterData);
        refreshHealthPanel();
    }
    if (options.save !== false) {
        scheduleAutoSave();
        forceSyncCharacter();
    }
    return true;
}

async function useGrenade(item, itemPath, options = {}) {
    if (item.attributes?.caliber) {
        showNotification('Эту гранату нельзя метнуть вручную — она для гранатомёта');
        return;
    }

    const combatState = window.locationCombatState;
    if (combatState?.status === 'active') {
        const actorCharacterId = currentCharacterId;
        const access = await calculateInventoryAccess(item, itemPath);
        document.getElementById('grenade-fuse-modal')?.remove();
        const modal = document.createElement('div');
        modal.id = 'grenade-fuse-modal';
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content" style="max-width:440px">
                <span class="close">&times;</span>
                <h3>${escapeHtml(item.name || 'Граната')}</h3>
                <p>Выберите подготовку запала перед броском.</p>
                <div class="form-actions" style="display:grid;gap:8px">
                    <button class="btn btn-primary" data-fuse="normal">Обычный запал</button>
                    <button class="btn btn-warning" data-fuse="delay">Задержать гранату · +2 ОД, Тактика СЛ13</button>
                    <button class="btn btn-secondary" data-cancel>Отмена</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
        modal.style.display = 'flex';
        const close = () => modal.remove();
        modal.querySelector('.close').onclick = close;
        modal.querySelector('[data-cancel]').onclick = close;
        modal.querySelectorAll('[data-fuse]').forEach((button) => {
            button.onclick = async () => {
                const explosiveFuseMode = button.dataset.fuse;
                close();
                closeCharacterSheet();
                const scene = await import('./locationScene.js');
                scene.queueCombatActionFromSheet({
                    actorCharacterId,
                    actionKey: 'explosive_attack',
                    explosiveSource: 'hand',
                    explosiveFuseMode,
                    itemPath,
                    inventoryRetrievalActionPoints: access.retrievalActionPoints,
                    inventoryUseActionDiscount: access.useActionDiscount,
                    targetType: 'point',
                    source: 'sheet',
                });
            };
        });
        return true;
    }
    if (window.isLocationActive) {
        const scene = await import('./locationScene.js');
        await scene.requestCombatStartForAttack(currentCharacterId);
        return true;
    }
    showNotification('Метание гранаты доступно на боевой подлокации', 'system');
    return false;
}

export async function resumeDeferredConsumable(lobbyId, locationId, actionId) {
    if (medicalOperationInProgress) return false;
    const saved = await Server.prepareDeferredConsumable(lobbyId, locationId, actionId);
    if (medicalOperationInProgress) return false;
    const selection = saved.selection;
    const applied = await useCharacterInventoryItem(saved.actor_character_id, selection.source.path, {
        forceReload: true,
        targetCharacterId: selection.target_character_id,
        targetData: saved.target_data,
        preselectedApplication: selection.application,
        deferredActionId: saved.id,
        medicineRoll: saved.medicine_roll,
        treatmentConsentGranted: true,
        treatmentRequestId: selection.treatment_request_id,
        skipCombatPayment: true,
        interactionContext: {
            lobbyId, locationId, actorLocationCharacterId: saved.actor_location_character_id,
        },
    });
    if (!applied) throw new Error('Процедура не завершена. Проверьте условия применения или отмените действие.');
    return true;
}

export async function useCharacterInventoryItem(characterId, itemPath, options = {}) {
    if (medicalOperationInProgress) throw new Error('Дождитесь завершения применения предмета');
    if (autoSaveTimer) await forceSyncCharacter();
    if (sheetSaveBlocked && Number(characterId) === Number(currentCharacterId)) throw new Error('Переоткройте лист перед применением предмета');
    await Server.waitForCharacterSave(characterId);
    if (medicalOperationInProgress) throw new Error('Дождитесь завершения применения предмета');
    const normalizedPath = Array.isArray(itemPath)
        ? itemPath
        : String(itemPath || '')
            .split(',')
            .map(part => (part === '' || Number.isNaN(Number(part)) ? part : Number(part)));
    if (!characterId || !normalizedPath.length) {
        throw new Error('Не удалось выбрать предмет');
    }

    const previousCharacterId = currentCharacterId;
    const previousCharacterData = currentCharacterData;
    const shouldRestorePreviousState = previousCharacterId !== characterId;
    const activeData = !options.forceReload && !shouldRestorePreviousState && currentCharacterData
        ? currentCharacterData
        : null;
    medicalOperationInProgress = true;
    medicalRemoteUpdate = null;
    const sheetModal = document.getElementById('character-sheet-modal');
    const sheetWasInert = sheetModal?.inert || false;
    if (sheetModal) sheetModal.inert = true;
    if (autoSaveTimer) clearTimeout(autoSaveTimer);
    autoSaveTimer = null;
    let appliedData = null;

    try {
        if (options.forceReload || shouldRestorePreviousState || !currentCharacterData) {
            const loadedCharacter = await Server.getCharacter(characterId);
            currentCharacterId = characterId;
            currentCharacterData = loadedCharacter?.data || {};
        }

        if (!allTemplatesCache) {
            await getAllItemTemplates();
        }
        // Work on a draft. A rejected compound save must not leave a spent item locally.
        currentCharacterData = JSON.parse(JSON.stringify(currentCharacterData));
        normalizeCharacterEffects(currentCharacterData);

        let targetData = currentCharacterData;
        const targetCharacterId = Number(options.targetCharacterId || characterId);
        if (targetCharacterId !== Number(characterId)) {
            if (options.targetData && typeof options.targetData === 'object') {
                targetData = JSON.parse(JSON.stringify(options.targetData));
            } else {
                const loadedTarget = await Server.getCharacter(targetCharacterId);
                targetData = loadedTarget?.data || {};
            }
            normalizeCharacterEffects(targetData);
        }

        const resolvedEntry = options.itemId ? findInventoryItemById(currentCharacterData, options.itemId) : null;
        const resolvedPath = resolvedEntry?.path || normalizedPath;
        const locatedItem = resolvedEntry?.item || getItemByPath(resolvedPath);
        const retryItemMatches = !options.retryItem || (
            locatedItem
            && (
                (options.retryItem.id != null && locatedItem.id == options.retryItem.id)
                || (options.retryItem.id == null
                    && locatedItem.templateId == options.retryItem.templateId
                    && locatedItem.name === options.retryItem.name)
            )
        );
        const item = retryItemMatches ? locatedItem : options.retryItem;
        if (!item) {
            throw new Error('Предмет не найден');
        }

    let preselectedApplication = options.preselectedApplication;
    if (options.deferredActionId && preselectedApplication?.kind === 'blood_type_test'
        && preselectedApplication.target === 'packet') {
        const entry = preselectedApplication.entry;
        const packet = getItemByPath(entry?.path || []);
        if (!packet || ['id', 'templateId', 'name'].some(key => packet[key] !== entry.item?.[key])) {
            throw new Error('Пакет крови изменился. Отмените старую процедуру.');
        }
        preselectedApplication = { ...preselectedApplication, entry: { ...entry, item: packet } };
    }
    if (preselectedApplication?.effect) {
        const storedEffect = preselectedApplication.effect;
        const currentEffect = (targetData.health?.effects || []).find(effect =>
            (storedEffect.id && effect?.id === storedEffect.id)
            || (!storedEffect.id
                && effect?.type === storedEffect.type
                && effect?.area === storedEffect.area
                && effect?.source === storedEffect.source)
        );
        if (currentEffect) {
            preselectedApplication = { ...preselectedApplication, effect: currentEffect };
        } else if (options.deferredActionId && !['damaged_zone', 'body_zone'].includes(storedEffect.type)) {
            throw new Error('Выбранная травма больше не найдена. Отмените старую процедуру.');
        }
    }
    const useOptions = {
        ...options,
        preselectedApplication,
        targetData,
        render: false,
        save: false,
        consumableSideEffects: {},
    };
    const consumableSource = item.category === 'consumable' && !options.skipItemConsumption
        ? { path: [...resolvedPath], snapshot: JSON.parse(JSON.stringify(item)) }
        : undefined;
    const applied = await useItem(item, resolvedPath, useOptions);
    if (applied !== true) return false;
    if (useOptions.serverMedicalResult || useOptions.serverConsumableResult) {
        currentCharacterData = useOptions.serverMedicalResult?.actor_data
            || useOptions.serverConsumableResult?.data;
        appliedData = currentCharacterData;
        showNotification('Применение предмета сохранено', 'system');
        if (currentCharacterId === characterId) {
            renderInventoryTab(currentCharacterData);
            refreshHealthPanel();
        }
        return true;
    }
    const consumableUse = item.category === 'consumable' ? {
        id: options.deferredActionId || `consumable-${characterId}-${Date.now()}-${Math.random().toString(36).slice(2)}`,
        deferred_action_id: options.deferredActionId,
        side_effects: useOptions.consumableSideEffects,
        source: consumableSource,
        combat_location_id: window.locationCombatState?.status === 'active' ? Number(window.currentLocationId) : null,
    } : undefined;
    let consumableResult = null;
    if (targetCharacterId !== Number(characterId)) {
        const actorUpdates = Server.characterUpdatePayload(characterId, currentCharacterData);
        let response;
        if (options.interactionContext?.actorLocationCharacterId) {
            response = await Server.treatLocationCharacter(
                options.interactionContext.lobbyId,
                options.interactionContext.locationId,
                targetCharacterId,
                {
                    actor_location_character_id: options.interactionContext.actorLocationCharacterId,
                    health: targetData.health || {},
                    interaction_request_id: useOptions.treatmentRequestId,
                    actor_updates: actorUpdates,
                    target_revision: targetData._revision,
                    target_base_health: Server.characterUpdatePayload(targetCharacterId, targetData)._base_data?.health,
                    consumable_use: consumableUse,
                }
            );
        } else {
            response = await Server.finishSheetTreatment(targetCharacterId, {
                actor_character_id: Number(characterId), actor_updates: actorUpdates,
                target_updates: Server.characterUpdatePayload(targetCharacterId, targetData),
                consumable_use: consumableUse,
            });
        }
        currentCharacterData = response.actor_data;
        consumableResult = response.consumable_result;
    } else if (consumableUse) {
        const response = await Server.finishConsumableUse(characterId, {
            actor_updates: Server.characterUpdatePayload(characterId, currentCharacterData),
            consumable_use: consumableUse,
        });
        currentCharacterData = response.data;
        consumableResult = response.consumable_result;
    } else {
        const response = await Server.updateCharacter(characterId, { data: currentCharacterData });
        currentCharacterData = response.data;
    }
    appliedData = currentCharacterData;
    if (consumableResult?.addiction?.acquired) {
        showNotification(`Получена зависимость: ${consumableResult.addiction.profile.label}`, 'system');
    } else if (consumableResult?.addiction?.applicable && consumableResult.addiction.blocked) {
        showNotification('«Котик» заблокировал появление зависимости', 'success');
    }
    showNotification('Применение предмета сохранено', 'system');
    if (currentCharacterId === characterId) {
        renderInventoryTab(currentCharacterData);
        refreshHealthPanel();
    }
    } finally {
        medicalOperationInProgress = false;
        if (shouldRestorePreviousState) {
            currentCharacterId = previousCharacterId;
            currentCharacterData = previousCharacterData;
        } else if (activeData) {
            if (appliedData) {
                Object.keys(activeData).forEach(key => delete activeData[key]);
                Object.assign(activeData, appliedData);
            }
            currentCharacterData = activeData;
        } else if (!appliedData) {
            currentCharacterData = previousCharacterData;
        }
        if (medicalRemoteUpdate?.character_id === Number(currentCharacterId)
            && Number(medicalRemoteUpdate.data?._revision) > Number(currentCharacterData?._revision)) {
            currentCharacterData = medicalRemoteUpdate.data;
        }
        medicalRemoteUpdate = null;
        if (sheetModal) sheetModal.inert = sheetWasInert;
        if (currentCharacterId && currentCharacterData) {
            await refreshSavedSheet(currentCharacterId).catch(error => {
                console.error('Could not refresh the sheet after item use', error);
            });
        }
        window.dispatchEvent?.(new CustomEvent('character-item-operation-finished'));
    }
    return true;
}

export async function resolveMustDoMedicalRetry(retry, success) {
    if (!success) return false;
    if (!retry?.actor_character_id || !retry?.item_snapshot || !retry?.application) {
        throw new Error('Не удалось восстановить применение медикамента');
    }
    return useCharacterInventoryItem(retry.actor_character_id, retry.item_path || [], {
        targetCharacterId: retry.target_character_id || retry.actor_character_id,
        itemId: retry.item_id,
        retryItem: retry.item_snapshot,
        preselectedApplication: retry.application,
        interactionContext: retry.interaction_context || undefined,
        forceReload: true,
        skipCombatPayment: true,
        skipMedicineCheck: true,
        skipItemConsumption: true,
    });
}

function rollBloodType() {
    const roll = Math.floor(Math.random() * 20) + 1;
    if (roll <= 10) return { roll, bloodType: 1 };
    if (roll <= 15) return { roll, bloodType: 2 };
    if (roll <= 19) return { roll, bloodType: 3 };
    return { roll, bloodType: 4 };
}

function formatBloodType(value) {
    const normalized = Number(value);
    if (!Number.isFinite(normalized) || normalized <= 0) return 'неизвестна';
    return `${Math.trunc(normalized)} группа`;
}

function toggleDevice(item, itemPath) {
    if (item.attributes?.isActive === undefined) item.attributes.isActive = false;

    if (!item.attributes.isActive) {
        const battery = (item.installedModules || []).find(m => m.slotType === 'battery');
        if (!battery) {
            showNotification('Нет батарейки');
            return;
        }
        const currentCharge = battery.attributes?.power ?? 0;
        if (currentCharge <= 0) {
            showNotification('Батарейка разряжена');
            return;
        }
        battery.attributes.power = Math.max(0, currentCharge - 1);
    }

    item.attributes.isActive = !item.attributes.isActive;

    const itemDiv = document.querySelector(`[data-path="${itemPath.join(',')}"]`);
    if (itemDiv) {
        const useBtn = itemDiv.querySelector('button.btn-success');
        if (useBtn) {
            useBtn.textContent = item.attributes.isActive ? '⏹' : '▶';
            useBtn.title = item.attributes.isActive ? 'Выключить' : 'Включить';
        }
        const slotDiv = itemDiv.querySelector('div[style*="background: rgba(0,0,0,0.1)"]');
        if (slotDiv) {
            const infoSpan = slotDiv.querySelector('span[style*="flex: 1"]');
            if (infoSpan) {
                const battery = (item.installedModules || []).find(m => m.slotType === 'battery');
                if (battery) {
                    infoSpan.textContent = `${battery.name} (заряд ${battery.attributes.power}%)`;
                }
            }
        }
    }

    showNotification(`${item.name} ${item.attributes.isActive ? 'включен' : 'выключен'}`, 'success');
    scheduleAutoSave();
    forceSyncCharacter();
}

async function rechargeDevice(item, itemPath) {
    if (item.attributes?.power === undefined) {
        showNotification('Это устройство не имеет батареи');
        return;
    }
    if (item.attributes.power >= 100) {
        showNotification('Батарея уже полностью заряжена');
        return;
    }

    // Ищем батарейки в инвентаре
    const batteryItems = [];
    const collectBatteries = (items, path) => {
        if (!Array.isArray(items)) return;
        items.forEach((it, idx) => {
            if (it.category === 'device' && it.subcategory === 'battery' && it.quantity > 0) {
                batteryItems.push({ item: it, path: path.concat(idx) });
            }
            if (it.contents) collectBatteries(it.contents, path.concat(idx, 'contents'));
        });
    };
    collectBatteries(currentCharacterData.inventory?.backpack, ['inventory', 'backpack']);
    collectBatteries(currentCharacterData.inventory?.pockets, ['inventory', 'pockets']);
    const beltPouches = currentCharacterData.equipment?.belt?.pouches || [];
    beltPouches.forEach((pouch, i) => collectBatteries(pouch.contents, ['equipment', 'belt', 'pouches', i, 'contents']));
    const vestPouches = currentCharacterData.equipment?.vest?.pouches || [];
    vestPouches.forEach((pouch, i) => collectBatteries(pouch.contents, ['equipment', 'vest', 'pouches', i, 'contents']));

    if (batteryItems.length === 0) {
        showNotification('Нет батареек в инвентаре');
        return;
    }

    // Создаём модальное окно выбора
    const oldModal = document.getElementById('recharge-battery-modal');
    if (oldModal) oldModal.remove();

    const modal = document.createElement('div');
    modal.id = 'recharge-battery-modal';
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content">
            <span class="close" onclick="this.closest('.modal').remove()">&times;</span>
            <h3>Выберите батарейку для зарядки</h3>
            <select id="recharge-battery-select" class="form-control" size="5"></select>
            <div class="form-actions" style="margin-top:15px;">
                <button class="btn btn-primary" id="confirm-recharge-battery">Зарядить</button>
                <button class="btn btn-secondary" onclick="this.closest('.modal').remove()">Отмена</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    const select = modal.querySelector('#recharge-battery-select');
    batteryItems.forEach((entry, idx) => {
        const opt = document.createElement('option');
        opt.value = idx;
        opt.textContent = `${entry.item.name} (заряд ${entry.item.attributes?.power ?? '?'}%, ${entry.item.quantity} шт.)`;
        select.appendChild(opt);
    });

    modal.querySelector('#confirm-recharge-battery').onclick = () => {
        const idx = select.value;
        if (idx === '') return;
        const selected = batteryItems[idx];
        const battery = selected.item;
        modal.remove();

        // Заряжаем устройство
        item.attributes.power = 100;

        // Расходуем одну батарейку
        battery.quantity -= 1;
        const batteryPath = selected.path;
        if (battery.quantity <= 0) {
            removeItemByPath(batteryPath);
        }

        // Обновляем UI точечно
        // 1. Слоты устройства
        const deviceDiv = document.querySelector(`[data-path="${itemPath.join(',')}"]`);
        if (deviceDiv) {
            const slotsContainer = deviceDiv.querySelector('.item-slots-container');
            const newSlotsHtml = renderSlotsUniversal(item, itemPath, 1);
            if (slotsContainer) {
                slotsContainer.outerHTML = newSlotsHtml;
            } else if (newSlotsHtml) {
                deviceDiv.insertAdjacentHTML('beforeend', newSlotsHtml);
            }
        }

        // 2. Стопка батареек
        if (battery.quantity > 0) {
            const batteryDiv = document.querySelector(`[data-path="${batteryPath.join(',')}"]`);
            if (batteryDiv) {
                const qtyInput = batteryDiv.querySelector('input[placeholder="Кол-во"]');
                if (qtyInput) qtyInput.value = battery.quantity;
            }
        } else {
            const batteryDiv = document.querySelector(`[data-path="${batteryPath.join(',')}"]`);
            if (batteryDiv) {
                const parent = batteryDiv.parentNode;
                batteryDiv.remove();
                const remaining = Array.from(parent.children).filter(el => el.hasAttribute('data-path'));
                remaining.forEach((el, idx) => {
                    const newPath = batteryPath.slice(0, -1).concat(idx).join(',');
                    el.dataset.path = newPath;
                    updateHandlersInElement(el, batteryPath.slice(0, -1), idx);
                });
                if (parent.classList.contains('container-contents')) {
                    updatePouchVolumeFromContentsDiv(parent);
                }
            }
        }

        recalculateInventoryTotals();
        scheduleAutoSave();
        forceSyncCharacter();
        showNotification(`${item.name} заряжен`, 'success');
    };

    modal.style.display = 'flex';
}

function applyEffect(effect) {
    const health = currentCharacterData.health || {};
    applyEffectToHealth(health, effect);
    currentCharacterData.health = health;
    normalizeCharacterEffects(currentCharacterData);
    const summary = effectSummary(effect);
    if (summary) showNotification(`Эффект: ${summary}`, 'system');
}

// Функции добавления/удаления модификаций
window.addHelmetModification = function() {
    updateDataFromFields();
    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    if (!currentCharacterData.equipment.helmet) currentCharacterData.equipment.helmet = {};
    if (!Array.isArray(currentCharacterData.equipment.helmet.modifications)) {
        currentCharacterData.equipment.helmet.modifications = [];
    }
    currentCharacterData.equipment.helmet.modifications.push({ name: '', description: '' });
    renderEquipmentTab(currentCharacterData);
    scheduleAutoSave();
};

window.removeHelmetModification = function(index) {
    updateDataFromFields();
    if (!currentCharacterData.equipment?.helmet?.modifications) return;
    currentCharacterData.equipment.helmet.modifications.splice(index, 1);
    renderEquipmentTab(currentCharacterData);
    scheduleAutoSave();
};

window.addGasMaskModification = function() {
    updateDataFromFields();
    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    if (!currentCharacterData.equipment.gasMask) currentCharacterData.equipment.gasMask = {};
    if (!Array.isArray(currentCharacterData.equipment.gasMask.modifications)) {
        currentCharacterData.equipment.gasMask.modifications = [];
    }
    currentCharacterData.equipment.gasMask.modifications.push({ name: '', description: '' });
    renderEquipmentTab(currentCharacterData);
    scheduleAutoSave();
};

window.removeGasMaskModification = function(index) {
    updateDataFromFields();
    if (!currentCharacterData.equipment?.gasMask?.modifications) return;
    currentCharacterData.equipment.gasMask.modifications.splice(index, 1);
    renderEquipmentTab(currentCharacterData);
    scheduleAutoSave();
};

window.addArmorModification = function() {
    updateDataFromFields();
    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    if (!currentCharacterData.equipment.armor) currentCharacterData.equipment.armor = {};
    if (!Array.isArray(currentCharacterData.equipment.armor.modifications)) {
        currentCharacterData.equipment.armor.modifications = [];
    }
    currentCharacterData.equipment.armor.modifications.push({ name: '', description: '' });
    renderEquipmentTab(currentCharacterData);
    scheduleAutoSave();
};

window.removeArmorModification = function(index) {
    updateDataFromFields();
    if (!currentCharacterData.equipment?.armor?.modifications) return;
    currentCharacterData.equipment.armor.modifications.splice(index, 1);
    renderEquipmentTab(currentCharacterData);
    scheduleAutoSave();
};

// Функции для подсумков
window.addBeltPouch = function() {
    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    if (!currentCharacterData.equipment.belt) currentCharacterData.equipment.belt = {};
    if (!Array.isArray(currentCharacterData.equipment.belt.pouches)) {
        currentCharacterData.equipment.belt.pouches = [];
    }
    currentCharacterData.equipment.belt.pouches.push({
        type: null,
        capacity: 0,
        contents: [],
        isContainer: true
    });
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
};

window.removeBeltPouch = function(index) {
    updateDataFromFields();
    if (!currentCharacterData.equipment?.belt?.pouches) return;
    currentCharacterData.equipment.belt.pouches.splice(index, 1);
    renderInventoryTab(currentCharacterData)
    scheduleAutoSave();
};

window.addBeltModification = function() {
    updateDataFromFields();
    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    if (!currentCharacterData.equipment.belt) currentCharacterData.equipment.belt = {};
    if (!Array.isArray(currentCharacterData.equipment.belt.modifications)) {
        currentCharacterData.equipment.belt.modifications = [];
    }
    currentCharacterData.equipment.belt.modifications.push({ name: '', description: '' });
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
};

window.removeBeltModification = function(index) {
    updateDataFromFields();
    if (!currentCharacterData.equipment?.belt?.modifications) return;
    currentCharacterData.equipment.belt.modifications.splice(index, 1);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
};

window.addVestPouch = function() {
    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};
    if (!currentCharacterData.equipment.vest) {
        currentCharacterData.equipment.vest = { model: 'custom', pouches: [], totalCapacity: 0 };
    }
    if (!Array.isArray(currentCharacterData.equipment.vest.pouches)) {
        currentCharacterData.equipment.vest.pouches = [];
    }
    currentCharacterData.equipment.vest.pouches.push({
        type: null,
        capacity: 0,
        contents: [],
        isContainer: true
    });
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
};

window.removeVestPouch = function(index) {
    updateDataFromFields();
    if (!currentCharacterData.equipment?.vest?.pouches) return;
    currentCharacterData.equipment.vest.pouches.splice(index, 1);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
};

window.onVestModelChange = async function(select) {
    const selectedValue = select.value;
    if (!currentCharacterData.equipment) currentCharacterData.equipment = {};

    if (selectedValue === 'custom') {
        currentCharacterData.equipment.vest = {
            model: 'custom',
            pouches: currentCharacterData.equipment.vest?.pouches || [],
            totalCapacity: currentCharacterData.equipment.vest?.totalCapacity || 0
        };
    } else if (selectedValue) {
        const templates = await loadTemplatesForLobby('vest'); // <-- исправлено: 'vest', не 'vests'
        const template = templates.find(t => t.id == selectedValue);
        if (template) {
            // Убедимся, что у каждого подсумка есть internalVolume
            const pouches = (template.attributes?.pouches || []).map(p => ({
                ...p,
                internalVolume: p.internalVolume || template.volume || 0,
                contents: p.contents || []
            }));
            currentCharacterData.equipment.vest = {
                model: selectedValue,
                pouches: pouches,
                totalCapacity: template.attributes?.total_capacity || 0
            };
        } else {
            showNotification('Шаблон разгрузки не найден');
            return;
        }
    } else {
        delete currentCharacterData.equipment.vest;
    }

    await renderInventoryTab(currentCharacterData);
    const selectElement = document.querySelector('select[name="equipment.vest.model"]');
    if (selectElement) {
        selectElement.value = currentCharacterData.equipment.vest?.model || '';
    }
    scheduleAutoSave();
};

// Функции создания кастомных шаблонов
window.openCreateHelmetTemplateModal = function(template = null) {
    let modal = document.getElementById('create-helmet-template-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'create-helmet-template-modal';
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content" style="max-height: 80vh; overflow-y: auto;">
                <span class="close" onclick="document.getElementById('create-helmet-template-modal').style.display='none'">&times;</span>
                <h3>${template ? 'Редактировать' : 'Создать'} шаблон шлема</h3>
                <input type="hidden" id="helmet-template-id">
                <div class="form-group"><label>Название</label><input type="text" id="helmet-name" class="form-control"></div>
                <div class="form-group"><label>Материал</label><select id="helmet-material" class="form-control">${MATERIAL_OPTIONS.map(opt => `<option value="${opt}">${opt}</option>`).join('')}</select></div>
                <div class="form-group"><label>Прочность</label><input type="number" id="helmet-maxDurability" class="form-control number-input" value="1"></div>
                <div class="form-group"><label>Штраф Точности</label><input type="number" id="helmet-accuracyPenalty" class="form-control number-input" value="0"></div>
                <div class="form-group"><label>Штраф Эргономики</label><input type="number" id="helmet-ergonomicsPenalty" class="form-control number-input" value="0"></div>
                <div class="form-group"><label>Бонус Харизмы</label><input type="number" id="helmet-charismaBonus" class="form-control number-input" value="0"></div>
                <div class="form-group"><label>Штраф перемещения</label><input type="number" id="helmet-movementPenalty" class="form-control number-input" value="0"></div>
                <div class="form-group"><label>Вес</label><input type="number" id="helmet-weight" class="form-control number-input" value="0" step="0.1"></div>
                <div class="form-group"><label>Объём</label><input type="number" id="helmet-volume" class="form-control number-input" value="0" step="0.1"></div>
                <div class="form-group"><label>Защита</label>
                    <div style="display: grid; grid-template-columns: repeat(5,1fr); gap:5px;">
                        <div><label>Физ</label><input type="number" id="helmet-physical" class="form-control number-input" value="0"></div>
                        <div><label>Хим</label><input type="number" id="helmet-chemical" class="form-control number-input" value="0"></div>
                        <div><label>Терм</label><input type="number" id="helmet-thermal" class="form-control number-input" value="0"></div>
                        <div><label>Элек</label><input type="number" id="helmet-electric" class="form-control number-input" value="0"></div>
                        <div><label>Рад</label><input type="number" id="helmet-radiation" class="form-control number-input" value="0"></div>
                    </div>
                </div>
                <hr>
                <h4>Зоны защиты</h4>
                <div class="form-group">
                    <label><input type="checkbox" id="helmet-zone-crown"> Теменная часть</label><br>
                    <label><input type="checkbox" id="helmet-zone-back"> Затылок</label><br>
                    <label><input type="checkbox" id="helmet-zone-ears"> Уши</label><br>
                    <label><input type="checkbox" id="helmet-zone-face"> Забрало / Лицо</label>
                </div>
                <hr>
                <h4>Слоты</h4>
                <div class="form-group">
                    <label><input type="checkbox" id="helmet-has-nvg-slot"> Крепление для ПНВ</label>
                </div>
                <div class="form-group">
                    <label><input type="checkbox" id="helmet-has-filter-slot"> Слот для фильтра (противогазо-шлем)</label>
                </div>
                <div class="form-actions"><button class="btn btn-primary" onclick="saveHelmetTemplate()">Сохранить</button><button class="btn btn-secondary" onclick="document.getElementById('create-helmet-template-modal').style.display='none'">Отмена</button></div>
            </div>`;
        document.body.appendChild(modal);
    }
    if (template) {
        document.getElementById('helmet-template-id').value = template.id;
        document.getElementById('helmet-name').value = template.name || '';
        document.getElementById('helmet-material').value = template.attributes?.material || 'Текстиль';
        document.getElementById('helmet-maxDurability').value = template.attributes?.max_durability || 1;
        document.getElementById('helmet-accuracyPenalty').value = template.attributes?.accuracy_penalty || 0;
        document.getElementById('helmet-ergonomicsPenalty').value = template.attributes?.ergonomics_penalty || 0;
        document.getElementById('helmet-charismaBonus').value = template.attributes?.charisma_bonus || 0;
        document.getElementById('helmet-movementPenalty').value = template.attributes?.movement_penalty || 0;
        document.getElementById('helmet-weight').value = template.weight || 0;
        document.getElementById('helmet-volume').value = template.volume || 0;
        const prot = template.attributes?.protection || {};
        document.getElementById('helmet-physical').value = protectionPercentValue(prot.physical);
        document.getElementById('helmet-chemical').value = protectionPercentValue(prot.chemical);
        document.getElementById('helmet-thermal').value = protectionPercentValue(prot.thermal);
        document.getElementById('helmet-electric').value = protectionPercentValue(prot.electric);
        document.getElementById('helmet-radiation').value = protectionPercentValue(prot.radiation);
        const zones = template.attributes?.protection_zones || [];
        document.getElementById('helmet-zone-crown').checked = zones.includes('crown');
        document.getElementById('helmet-zone-back').checked = zones.includes('back');
        document.getElementById('helmet-zone-ears').checked = zones.includes('ears');
        document.getElementById('helmet-zone-face').checked = zones.includes('face');
        const slots = template.attributes?.slots || [];
        document.getElementById('helmet-has-nvg-slot').checked = slots.some(s => s.type === 'nvg');
        document.getElementById('helmet-has-filter-slot').checked = slots.some(s => s.type === 'filter');
    } else {
        document.getElementById('helmet-template-id').value = '';
    }
    modal.style.display = 'flex';
};

window.saveHelmetTemplate = async function() {
    const id = document.getElementById('helmet-template-id').value;
    const name = document.getElementById('helmet-name').value.trim();
    if (!name) { showNotification('Введите название'); return; }

    const slots = [];
    if (document.getElementById('helmet-has-nvg-slot').checked) slots.push({ type: 'nvg', label: 'ПНВ', maxItems: 1 });
    if (document.getElementById('helmet-has-filter-slot').checked) slots.push({ type: 'filter', label: 'Фильтр', maxItems: 1 });

    const protectionZones = [];
    if (document.getElementById('helmet-zone-crown').checked) protectionZones.push('crown');
    if (document.getElementById('helmet-zone-back').checked) protectionZones.push('back');
    if (document.getElementById('helmet-zone-ears').checked) protectionZones.push('ears');
    if (document.getElementById('helmet-zone-face').checked) protectionZones.push('face');

    const attributes = {
        material: document.getElementById('helmet-material').value,
        max_durability: parseInt(document.getElementById('helmet-maxDurability').value) || 1,
        accuracy_penalty: parseInt(document.getElementById('helmet-accuracyPenalty').value) || 0,
        ergonomics_penalty: parseInt(document.getElementById('helmet-ergonomicsPenalty').value) || 0,
        charisma_bonus: parseInt(document.getElementById('helmet-charismaBonus').value) || 0,
        movement_penalty: parseInt(document.getElementById('helmet-movementPenalty').value) || 0,
        protection: {
            physical: (parseFloat(document.getElementById('helmet-physical').value) || 0) / 100,
            chemical: (parseFloat(document.getElementById('helmet-chemical').value) || 0) / 100,
            thermal: (parseFloat(document.getElementById('helmet-thermal').value) || 0) / 100,
            electric: (parseFloat(document.getElementById('helmet-electric').value) || 0) / 100,
            radiation: (parseFloat(document.getElementById('helmet-radiation').value) || 0) / 100
        },
        protection_zones: protectionZones,
        slots: slots
    };
    const data = {
        name, category: 'helmet', subcategory: null, price: 0,
        weight: parseFloat(document.getElementById('helmet-weight').value) || 0,
        volume: parseFloat(document.getElementById('helmet-volume').value) || 0,
        attributes
    };
    try {
        if (id) await Server.updateLobbyTemplate(currentLobbyId, id, data);
        else await Server.createLobbyTemplate(currentLobbyId, data);
        clearTemplatesCache('helmet'); clearAllTemplatesCache();
        document.getElementById('create-helmet-template-modal').style.display = 'none';
        showNotification(id ? 'Шаблон обновлён' : 'Шаблон создан', 'success');
        if (currentCharacterData) await renderEquipmentTab(currentCharacterData);
        if (typeof loadTemplatesForManager === 'function') {
            const active = document.querySelector('#templates-modal .tab-btn.active')?.dataset.cat;
            if (active === 'helmet') loadTemplatesForManager('helmet');
        }
    } catch (e) { showNotification(e.message); }
};

window.openCreateGasMaskTemplateModal = function(template = null) {
    let modal = document.getElementById('create-gasMask-template-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'create-gasMask-template-modal';
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content" style="max-height: 80vh; overflow-y: auto;">
                <span class="close" onclick="document.getElementById('create-gasMask-template-modal').style.display='none'">&times;</span>
                <h3>${template ? 'Редактировать' : 'Создать'} шаблон противогаза</h3>
                <input type="hidden" id="gasMask-template-id">
                <div class="form-group">
                    <label>Название</label>
                    <input type="text" id="gasMask-name" class="form-control">
                </div>
                <div class="form-group">
                    <label>Прочность</label>
                    <input type="number" id="gasMask-maxDurability" class="form-control number-input" value="1">
                </div>
                <div class="form-group">
                    <label>Штраф Точности</label>
                    <input type="number" id="gasMask-accuracyPenalty" class="form-control number-input" value="0">
                </div>
                <div class="form-group">
                    <label>Штраф Эргономики</label>
                    <input type="number" id="gasMask-ergonomicsPenalty" class="form-control number-input" value="0">
                </div>
                <div class="form-group">
                    <label>Бонус Харизмы</label>
                    <input type="number" id="gasMask-charismaBonus" class="form-control number-input" value="0">
                </div>
                <div class="form-group">
                    <label>Вес</label>
                    <input type="number" id="gasMask-weight" class="form-control number-input" value="0" step="0.1">
                </div>
                <div class="form-group">
                    <label>Объём</label>
                    <input type="number" id="gasMask-volume" class="form-control number-input" value="0" step="0.1">
                </div>
                <div class="form-group">
                    <label>Защита</label>
                    <div style="display: grid; grid-template-columns: repeat(5, 1fr); gap: 5px;">
                        <div><label>Физ</label><input type="number" id="gasMask-physical" class="form-control number-input" value="0"></div>
                        <div><label>Хим</label><input type="number" id="gasMask-chemical" class="form-control number-input" value="0"></div>
                        <div><label>Терм</label><input type="number" id="gasMask-thermal" class="form-control number-input" value="0"></div>
                        <div><label>Элек</label><input type="number" id="gasMask-electric" class="form-control number-input" value="0"></div>
                        <div><label>Рад</label><input type="number" id="gasMask-radiation" class="form-control number-input" value="0"></div>
                    </div>
                </div>
                <div class="form-actions">
                    <button class="btn btn-primary" onclick="saveGasMaskTemplate()">Сохранить</button>
                    <button class="btn btn-secondary" onclick="document.getElementById('create-gasMask-template-modal').style.display='none'">Отмена</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }

    if (template) {
        document.getElementById('gasMask-template-id').value = template.id;
        document.getElementById('gasMask-name').value = template.name || '';
        document.getElementById('gasMask-maxDurability').value = template.attributes?.max_durability || 1;
        document.getElementById('gasMask-accuracyPenalty').value = template.attributes?.accuracy_penalty || 0;
        document.getElementById('gasMask-ergonomicsPenalty').value = template.attributes?.ergonomics_penalty || 0;
        document.getElementById('gasMask-charismaBonus').value = template.attributes?.charisma_bonus || 0;
        document.getElementById('gasMask-weight').value = template.weight || 0;
        document.getElementById('gasMask-volume').value = template.volume || 0;
        const prot = template.attributes?.protection || {};
        document.getElementById('gasMask-physical').value = protectionPercentValue(prot.physical);
        document.getElementById('gasMask-chemical').value = protectionPercentValue(prot.chemical);
        document.getElementById('gasMask-thermal').value = protectionPercentValue(prot.thermal);
        document.getElementById('gasMask-electric').value = protectionPercentValue(prot.electric);
        document.getElementById('gasMask-radiation').value = protectionPercentValue(prot.radiation);
    } else {
        document.getElementById('gasMask-template-id').value = '';
    }

    modal.style.display = 'flex';
};

window.saveGasMaskTemplate = async function() {
    const id = document.getElementById('gasMask-template-id').value;
    const name = document.getElementById('gasMask-name').value.trim();
    if (!name) { showNotification('Введите название'); return; }

    const attributes = {
        max_durability: parseInt(document.getElementById('gasMask-maxDurability').value) || 1,
        accuracy_penalty: parseInt(document.getElementById('gasMask-accuracyPenalty').value) || 0,
        ergonomics_penalty: parseInt(document.getElementById('gasMask-ergonomicsPenalty').value) || 0,
        charisma_bonus: parseInt(document.getElementById('gasMask-charismaBonus').value) || 0,
        protection: {
            physical: (parseFloat(document.getElementById('gasMask-physical').value) || 0) / 100,
            chemical: (parseFloat(document.getElementById('gasMask-chemical').value) || 0) / 100,
            thermal: (parseFloat(document.getElementById('gasMask-thermal').value) || 0) / 100,
            electric: (parseFloat(document.getElementById('gasMask-electric').value) || 0) / 100,
            radiation: (parseFloat(document.getElementById('gasMask-radiation').value) || 0) / 100
        },
        slots: [{ type: 'filter', label: 'Фильтр', maxItems: 1 }]   // ← слот всегда есть
    };

    const data = {
        name: name,
        category: 'gas_mask',
        subcategory: null,
        price: 0,
        weight: parseFloat(document.getElementById('gasMask-weight').value) || 0,
        volume: parseFloat(document.getElementById('gasMask-volume').value) || 0,
        attributes: attributes
    };

    try {
        if (id) {
            await Server.updateLobbyTemplate(currentLobbyId, id, data);
        } else {
            await Server.createLobbyTemplate(currentLobbyId, data);
        }
        clearTemplatesCache('gas_mask');
        clearAllTemplatesCache();
        document.getElementById('create-gasMask-template-modal').style.display = 'none';
        showNotification(id ? 'Шаблон обновлён' : 'Шаблон создан', 'success');

        if (currentCharacterData) {
            await renderEquipmentTab(currentCharacterData);
        }

        if (typeof loadTemplatesForManager === 'function') {
            const active = document.querySelector('#templates-modal .tab-btn.active')?.dataset.cat;
            if (active === 'gas_mask') loadTemplatesForManager('gas_mask');
        }
    } catch (e) {
        showNotification(e.message);
    }
};

window.openCreateArmorTemplateModal = function(template = null) {
    let modal = document.getElementById('create-armor-template-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'create-armor-template-modal';
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content" style="max-height: 80vh; overflow-y: auto;">
                <span class="close" onclick="document.getElementById('create-armor-template-modal').style.display='none'">&times;</span>
                <h3>${template ? 'Редактировать' : 'Создать'} шаблон брони</h3>
                <input type="hidden" id="armor-template-id">
                <div class="form-group"><label>Название</label><input type="text" id="armor-name" class="form-control"></div>
                <div class="form-group"><label>Материал</label><select id="armor-material" class="form-control">${MATERIAL_OPTIONS.map(opt => `<option value="${opt}">${opt}</option>`).join('')}</select></div>
                <div class="form-group"><label>Прочность</label><input type="number" id="armor-maxDurability" class="form-control number-input" value="1"></div>
                <div class="form-group"><label>Штраф перемещения</label><input type="number" id="armor-movementPenalty" class="form-control number-input" value="0"></div>
                <div class="form-group"><label>Слоты под контейнеры</label><input type="number" id="armor-containerSlots" class="form-control number-input" value="0"></div>
                <div class="form-group"><label>Вес</label><input type="number" id="armor-weight" class="form-control number-input" value="0" step="0.1"></div>
                <div class="form-group"><label>Объём</label><input type="number" id="armor-volume" class="form-control number-input" value="0" step="0.1"></div>
                <div class="form-group"><label>Защита</label>
                    <div style="display: grid; grid-template-columns: repeat(5,1fr); gap:5px;">
                        <div><label>Физ</label><input type="number" id="armor-physical" class="form-control number-input" value="0"></div>
                        <div><label>Хим</label><input type="number" id="armor-chemical" class="form-control number-input" value="0"></div>
                        <div><label>Терм</label><input type="number" id="armor-thermal" class="form-control number-input" value="0"></div>
                        <div><label>Элек</label><input type="number" id="armor-electric" class="form-control number-input" value="0"></div>
                        <div><label>Рад</label><input type="number" id="armor-radiation" class="form-control number-input" value="0"></div>
                    </div>
                </div>
                <hr>
                <h4>Зоны защиты</h4>
                <div class="form-group">
                    <label><input type="checkbox" id="armor-zone-torso"> Торс</label><br>
                    <label><input type="checkbox" id="armor-zone-arms"> Руки</label><br>
                    <label><input type="checkbox" id="armor-zone-legs"> Ноги</label><br>
                    <label><input type="checkbox" id="armor-zone-head"> Голова</label>
                </div>
                <div class="form-actions"><button class="btn btn-primary" onclick="saveArmorTemplate()">Сохранить</button><button class="btn btn-secondary" onclick="document.getElementById('create-armor-template-modal').style.display='none'">Отмена</button></div>
            </div>`;
        document.body.appendChild(modal);
    }
    if (template) {
        document.getElementById('armor-template-id').value = template.id;
        document.getElementById('armor-name').value = template.name || '';
        document.getElementById('armor-material').value = template.attributes?.material || 'Текстиль';
        document.getElementById('armor-maxDurability').value = template.attributes?.max_durability || 1;
        document.getElementById('armor-movementPenalty').value = template.attributes?.movement_penalty || 0;
        document.getElementById('armor-containerSlots').value = template.attributes?.container_slots || 0;
        document.getElementById('armor-weight').value = template.weight || 0;
        document.getElementById('armor-volume').value = template.volume || 0;
        const prot = template.attributes?.protection || {};
        document.getElementById('armor-physical').value = protectionPercentValue(prot.physical);
        document.getElementById('armor-chemical').value = protectionPercentValue(prot.chemical);
        document.getElementById('armor-thermal').value = protectionPercentValue(prot.thermal);
        document.getElementById('armor-electric').value = protectionPercentValue(prot.electric);
        document.getElementById('armor-radiation').value = protectionPercentValue(prot.radiation);
        const zones = template.attributes?.protection_zones || [];
        document.getElementById('armor-zone-torso').checked = zones.includes('torso');
        document.getElementById('armor-zone-arms').checked = zones.includes('arms');
        document.getElementById('armor-zone-legs').checked = zones.includes('legs');
        document.getElementById('armor-zone-head').checked = zones.includes('head');
    } else {
        document.getElementById('armor-template-id').value = '';
    }
    modal.style.display = 'flex';
};

window.saveArmorTemplate = async function() {
    const id = document.getElementById('armor-template-id').value;
    const name = document.getElementById('armor-name').value.trim();
    if (!name) { showNotification('Введите название'); return; }
    const protectionZones = [];
    if (document.getElementById('armor-zone-torso').checked) protectionZones.push('torso');
    if (document.getElementById('armor-zone-arms').checked) protectionZones.push('arms');
    if (document.getElementById('armor-zone-legs').checked) protectionZones.push('legs');
    if (document.getElementById('armor-zone-head').checked) protectionZones.push('head');
    const attributes = {
        material: document.getElementById('armor-material').value,
        max_durability: parseInt(document.getElementById('armor-maxDurability').value) || 1,
        movement_penalty: parseInt(document.getElementById('armor-movementPenalty').value) || 0,
        container_slots: parseInt(document.getElementById('armor-containerSlots').value) || 0,
        protection: {
            physical: (parseFloat(document.getElementById('armor-physical').value) || 0) / 100,
            chemical: (parseFloat(document.getElementById('armor-chemical').value) || 0) / 100,
            thermal: (parseFloat(document.getElementById('armor-thermal').value) || 0) / 100,
            electric: (parseFloat(document.getElementById('armor-electric').value) || 0) / 100,
            radiation: (parseFloat(document.getElementById('armor-radiation').value) || 0) / 100
        },
        protection_zones: protectionZones
    };
    const data = {
        name, category: 'armor', subcategory: null, price: 0,
        weight: parseFloat(document.getElementById('armor-weight').value) || 0,
        volume: parseFloat(document.getElementById('armor-volume').value) || 0,
        attributes
    };
    try {
        if (id) await Server.updateLobbyTemplate(currentLobbyId, id, data);
        else await Server.createLobbyTemplate(currentLobbyId, data);
        clearTemplatesCache('armor'); clearAllTemplatesCache();
        document.getElementById('create-armor-template-modal').style.display = 'none';
        showNotification(id ? 'Шаблон обновлён' : 'Шаблон создан', 'success');
        if (typeof loadTemplatesForManager === 'function') {
            const active = document.querySelector('#templates-modal .tab-btn.active')?.dataset.cat;
            if (active === 'armor') loadTemplatesForManager('armor');
        }
        if (currentCharacterData && typeof renderEquipmentTab === 'function') {
            await renderEquipmentTab(currentCharacterData);
        }
    } catch (e) { showNotification(e.message); }
};

window.openCreateWeaponTemplateModal = function(weaponIndex, template = null) {
    let modal = document.getElementById('create-weapon-template-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'create-weapon-template-modal';
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content" style="max-height: 80vh; overflow-y: auto;">
                <span class="close" onclick="document.getElementById('create-weapon-template-modal').style.display='none'">&times;</span>
                <h3>Создать кастомный шаблон оружия</h3>
                <div class="form-group">
                    <label>Название</label>
                    <input type="text" id="template-name" class="form-control">
                </div>
                <div class="form-group">
                    <label>Категория (подтип)</label>
                    <input type="text" id="template-category" class="form-control" placeholder="например, пистолеты">
                </div>
                <div class="form-group">
                    <label>Калибр</label>
                    <input type="text" id="template-caliber" class="form-control" placeholder="например, 5.45x39">
                </div>
                <div class="form-group">
                    <label>Точность</label>
                    <input type="number" id="template-accuracy" class="form-control number-input" value="0">
                </div>
                <div class="form-group">
                    <label>Шум</label>
                    <input type="number" id="template-noise" class="form-control number-input" value="0">
                </div>
                <div class="form-group">
                    <label>Дальность</label>
                    <input type="number" id="template-range" class="form-control number-input" value="0">
                </div>
                <div class="form-group">
                    <label>Эргономика</label>
                    <input type="number" id="template-ergonomics" class="form-control number-input" value="0">
                </div>
                <div class="form-group">
                    <label>Очередь</label>
                    <input type="text" id="template-burst" class="form-control" placeholder="например, 3, -/2/3">
                </div>
                <div class="form-group">
                    <label>Урон</label>
                    <input type="number" id="template-damage" class="form-control number-input" value="0">
                </div>
                <div class="form-group">
                    <label>Прочность</label>
                    <input type="number" id="template-durability" class="form-control number-input" value="100">
                </div>
                <div class="form-group">
                    <label>Скорострельность</label>
                    <input type="number" id="template-fireRate" class="form-control number-input" value="0">
                </div>
                <div class="form-group">
                    <label>Вес</label>
                    <input type="number" id="template-weight" class="form-control number-input" value="0" step="0.1">
                </div>
                <div class="form-group">
                    <label>Объём</label>
                    <input type="number" id="template-volume" class="form-control number-input" value="0" step="0.1">
                </div>
                <hr>
                <h4>Слоты для модулей</h4>
                <div class="form-group">
                    <label><input type="checkbox" id="template-slot-scope" checked> Прицел</label>
                </div>
                <div class="form-group">
                    <label><input type="checkbox" id="template-slot-barrel" checked> Ствол</label>
                </div>
                <div class="form-group">
                    <label><input type="checkbox" id="template-slot-handguard" checked> Цевье</label>
                </div>
                <div class="form-group">
                    <label><input type="checkbox" id="template-fixed-magazine"> Несъёмный магазин</label>
                </div>
                <div class="form-group" id="template-fixed-magazine-size-group" style="display:none;">
                    <label>Ёмкость встроенного магазина</label>
                    <input type="number" id="template-magazineSize" class="form-control number-input" value="0" min="1">
                </div>
                <input type="hidden" id="weapon-template-id">
                <div class="form-actions">
                    <button class="btn btn-primary" onclick="window.saveWeaponTemplate()">Сохранить</button>
                    <button class="btn btn-secondary" onclick="document.getElementById('create-weapon-template-modal').style.display='none'">Отмена</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }
    if (template) {
        document.getElementById('weapon-template-id').value = template.id;
        document.getElementById('template-name').value = template.name || '';
        document.getElementById('template-category').value = template.subcategory || '';
        document.getElementById('template-caliber').value = template.attributes?.caliber || '';
        document.getElementById('template-accuracy').value = template.attributes?.accuracy || 0;
        document.getElementById('template-noise').value = template.attributes?.noise || 0;
        document.getElementById('template-range').value = template.attributes?.range || 0;
        document.getElementById('template-ergonomics').value = template.attributes?.ergonomics || 0;
        document.getElementById('template-burst').value = template.attributes?.burst || '';
        document.getElementById('template-damage').value = template.attributes?.damage || 0;
        document.getElementById('template-durability').value = template.attributes?.durability || 100;
        document.getElementById('template-fireRate').value = template.attributes?.fire_rate || 0;
        document.getElementById('template-weight').value = template.weight || 0;
        document.getElementById('template-volume').value = template.volume || 0;
        document.getElementById('template-magazineSize').value = template.attributes?.magazine_size || 0;
        document.getElementById('template-fixed-magazine').checked = Boolean(template.attributes?.fixedMagazine);
        // Слоты
        const slots = template.attributes?.slots || [];
        document.getElementById('template-slot-scope').checked = slots.some(s => s.type === 'scope');
        document.getElementById('template-slot-barrel').checked = slots.some(s => s.type === 'barrel');
        document.getElementById('template-slot-handguard').checked = slots.some(s => s.type === 'handguard');
    } else {
        document.getElementById('weapon-template-id').value = '';
        document.getElementById('template-fixed-magazine').checked = false;
        document.getElementById('template-magazineSize').value = 0;
    }

    const fixedMagazineCheckbox = document.getElementById('template-fixed-magazine');
    const fixedMagazineSizeGroup = document.getElementById('template-fixed-magazine-size-group');
    const updateFixedMagazineFields = () => {
        fixedMagazineSizeGroup.style.display = fixedMagazineCheckbox.checked ? '' : 'none';
    };
    fixedMagazineCheckbox.onchange = updateFixedMagazineFields;
    updateFixedMagazineFields();
    modal.style.display = 'flex';
};

window.saveWeaponTemplate = async function() {
    const id = document.getElementById('weapon-template-id').value;
    const name = document.getElementById('template-name').value.trim();
    if (!name) { showNotification('Введите название'); return; }

    const caliber = document.getElementById('template-caliber').value.trim();
    const slots = [];
    if (document.getElementById('template-slot-scope').checked) slots.push({ type: 'scope', label: 'Прицел', maxItems: 1 });
    if (document.getElementById('template-slot-barrel').checked) slots.push({ type: 'barrel', label: 'Ствол', maxItems: 1 });
    if (document.getElementById('template-slot-handguard').checked) slots.push({ type: 'handguard', label: 'Цевье', maxItems: 1 });

    const hasFixedMagazine = document.getElementById('template-fixed-magazine').checked;
    const attributes = {
        accuracy: parseInt(document.getElementById('template-accuracy').value) || 0,
        noise: parseInt(document.getElementById('template-noise').value) || 0,
        range: parseInt(document.getElementById('template-range').value) || 0,
        ergonomics: parseInt(document.getElementById('template-ergonomics').value) || 0,
        burst: document.getElementById('template-burst').value,
        damage: parseInt(document.getElementById('template-damage').value) || 0,
        durability: parseInt(document.getElementById('template-durability').value) || 100,
        fire_rate: parseInt(document.getElementById('template-fireRate').value) || 0,
        caliber: caliber,
        slots: slots,
        fixedMagazine: hasFixedMagazine
    };
    if (hasFixedMagazine) {
        attributes.magazine_size = parseInt(document.getElementById('template-magazineSize').value) || 0;
    }

    const weight = parseFloat(document.getElementById('template-weight').value) || 0;
    const volume = parseFloat(document.getElementById('template-volume').value) || 0;

    const data = {
        name: name,
        category: 'weapon',
        subcategory: document.getElementById('template-category').value || null,
        price: 0,
        weight: weight,
        volume: volume,
        attributes: attributes
    };

    try {
        if (id) {
            await Server.updateLobbyTemplate(currentLobbyId, id, data);
        } else {
            await Server.createLobbyTemplate(currentLobbyId, data);
        }
        clearTemplatesCache('weapon');
        clearAllTemplatesCache();
        document.getElementById('create-weapon-template-modal').style.display = 'none';
        showNotification(id ? 'Шаблон обновлён' : 'Шаблон создан', 'success');

        // Обновляем список в менеджере, если он открыт
        if (typeof loadTemplatesForManager === 'function' && document.getElementById('templates-modal').style.display === 'flex') {
            const activeCat = document.querySelector('#templates-modal .tab-btn.active')?.dataset.cat;
            if (activeCat === 'weapon') loadTemplatesForManager('weapon');
        }

        if (currentCharacterData && typeof renderEquipmentTab === 'function') {
            await renderEquipmentTab(currentCharacterData);
        }
    } catch (err) {
        showNotification(err.message);
    }
};

window.openCreateVestTemplateModal = function(template = null) {
    let modal = document.getElementById('create-vest-template-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'create-vest-template-modal';
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content" style="max-height: 85vh; width: 700px; overflow-y: auto;">
                <span class="close" onclick="document.getElementById('create-vest-template-modal').style.display='none'">&times;</span>
                <h3>${template ? 'Редактировать' : 'Создать'} шаблон разгрузки</h3>
                <input type="hidden" id="vest-template-id">
                <div class="form-group">
                    <label>Название</label>
                    <input type="text" id="vest-template-name" class="form-control" placeholder="Например: Разгрузка сталкера">
                </div>
                <div class="form-group">
                    <label>Общий объём (литры)</label>
                    <input type="number" id="vest-template-total-capacity" class="form-control number-input" value="0" min="0" step="1">
                </div>
                <div class="form-group">
                    <label>Вес</label>
                    <input type="number" id="vest-template-weight" class="form-control number-input" value="0" step="0.1">
                </div>
                <hr>
                <h4>Подсумки</h4>
                <div id="vest-pouches-editor" style="margin-bottom: 10px;">
                    <table style="width:100%; border-collapse: collapse;">
                        <thead>
                            <tr style="border-bottom:1px solid #555;">
                                <th style="padding:5px; text-align:left;">Тип подсумка</th>
                                <th style="padding:5px; text-align:left;">Объём</th>
                                <th style="width:40px;"></th>
                            </tr>
                        </thead>
                        <tbody id="vest-pouches-tbody">
                            <!-- строки будут добавляться динамически -->
                        </tbody>
                    </table>
                </div>
                <button type="button" class="btn btn-sm btn-secondary" onclick="window.addVestPouchRow()">➕ Добавить подсумок</button>
                <hr>
                <div class="form-actions" style="margin-top:15px;">
                    <button class="btn btn-primary" onclick="window.saveVestTemplateFromEditor()">Сохранить</button>
                    <button class="btn btn-secondary" onclick="document.getElementById('create-vest-template-modal').style.display='none'">Отмена</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }

    const tbody = document.getElementById('vest-pouches-tbody');
    tbody.innerHTML = '';

    if (template) {
        document.getElementById('vest-template-id').value = template.id;
        document.getElementById('vest-template-name').value = template.name || '';
        document.getElementById('vest-template-total-capacity').value = template.attributes?.total_capacity || 0;
        document.getElementById('vest-template-weight').value = template.weight || 0;

        const pouches = template.attributes?.pouches || [];
        pouches.forEach(p => window.addVestPouchRow(p));
    } else {
        document.getElementById('vest-template-id').value = '';
        window.addVestPouchRow(); // одна пустая строка
    }

    modal.style.display = 'flex';
};

async function loadPouchTemplatesForSelect() {
    try {
        return await loadTemplatesForLobby('pouch');
    } catch (e) {
        console.warn('Не удалось загрузить шаблоны подсумков', e);
        return [];
    }
}

window.addVestPouchRow = async function(pouchData = null) {
    const tbody = document.getElementById('vest-pouches-tbody');
    if (!tbody) return;

    const pouchTemplates = await loadTemplatesForLobby('pouch');

    const tr = document.createElement('tr');
    tr.style.borderBottom = '1px solid #444';

    // Ячейка выбора шаблона
    const tdType = document.createElement('td');
    tdType.style.padding = '5px';
    const select = document.createElement('select');
    select.className = 'form-control';
    select.style.width = '100%';
    select.innerHTML = '<option value="">-- Выберите --</option>';
    pouchTemplates.forEach(t => {
        const option = document.createElement('option');
        option.value = t.id;
        option.textContent = t.name;
        select.appendChild(option);
    });
    tdType.appendChild(select);

    // Ячейка объёма
    const tdVolume = document.createElement('td');
    tdVolume.style.padding = '5px';
    const volumeInput = document.createElement('input');
    volumeInput.type = 'number';
    volumeInput.className = 'form-control number-input pouch-volume';
    volumeInput.value = 0;
    volumeInput.min = 0;
    volumeInput.step = 1;
    tdVolume.appendChild(volumeInput);

    // Ячейка удаления
    const tdDel = document.createElement('td');
    tdDel.style.padding = '5px';
    tdDel.style.textAlign = 'center';
    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'btn btn-sm btn-danger';
    delBtn.textContent = '✕';
    delBtn.onclick = () => tr.remove();
    tdDel.appendChild(delBtn);

    tr.appendChild(tdType);
    tr.appendChild(tdVolume);
    tr.appendChild(tdDel);
    tbody.appendChild(tr);

    // Если переданы начальные данные, заполняем
    if (pouchData) {
        select.value = pouchData.type;
        volumeInput.value = pouchData.capacity;
    }

    // При выборе шаблона подставляем его объём
    select.onchange = (e) => {
        const templateId = e.target.value;
        const template = pouchTemplates.find(t => t.id == templateId);
        if (template) {
            volumeInput.value = template.volume || 0;
        }
    };
};

window.saveVestTemplateFromEditor = async function() {
    const id = document.getElementById('vest-template-id').value;
    const name = document.getElementById('vest-template-name').value.trim();
    if (!name) {
        showNotification('Введите название');
        return;
    }

    const totalCapacity = parseInt(document.getElementById('vest-template-total-capacity').value) || 0;
    const weight = parseFloat(document.getElementById('vest-template-weight').value) || 0;

    const pouchTemplates = await loadTemplatesForLobby('pouch');

    const pouches = [];
    const rows = document.querySelectorAll('#vest-pouches-tbody tr');
    for (let row of rows) {
        const select = row.querySelector('select');
        const volumeInput = row.querySelector('.pouch-volume');
        if (!select || !volumeInput) continue;

        const typeId = select.value;
        const capacity = parseInt(volumeInput.value) || 0;
        if (!typeId) continue;

        const template = pouchTemplates.find(t => t.id == typeId);
        pouches.push({
            type: parseInt(typeId, 10),
            capacity: capacity,
            internalVolume: template ? template.volume : 0
        });
    }

    const attributes = {
        total_capacity: totalCapacity,
        pouches: pouches
    };

    const data = {
        name: name,
        category: 'vest',
        subcategory: null,
        price: 0,
        weight: weight,
        volume: 0,
        attributes: attributes
    };

    try {
        if (id) {
            await Server.updateLobbyTemplate(currentLobbyId, id, data);
        } else {
            await Server.createLobbyTemplate(currentLobbyId, data);
        }
        clearTemplatesCache('vest');
        allTemplatesCache = null;

        document.getElementById('create-vest-template-modal').style.display = 'none';
        showNotification(id ? 'Шаблон обновлён' : 'Шаблон создан', 'success');

        if (currentCharacterData) {
            await renderEquipmentTab(currentCharacterData);
            await refreshVestModelSelect();
        }

        if (typeof loadTemplatesForManager === 'function') {
            const active = document.querySelector('#templates-modal .tab-btn.active')?.dataset.cat;
            if (active === 'vest') loadTemplatesForManager('vest');
        }
    } catch (err) {
        showNotification(err.message);
    }
};

async function refreshVestModelSelect() {
    const select = document.querySelector('select[name="equipment.vest.model"]');
    if (!select) return;
    const currentValue = select.value;
    const vestTemplates = await loadTemplatesForLobby('vest');
    select.innerHTML = '<option value="">-- Выберите модель --</option><option value="custom">Своя (база)</option>';
    vestTemplates.forEach(t => {
        const option = document.createElement('option');
        option.value = t.id;
        option.textContent = t.name;
        select.appendChild(option);
    });
    select.value = currentValue;
}

window.addPouchToVestTemplateEditor = function() {
    vestTemplateEditorPouches.push({
        type: null,
        capacity: 0
    });
    renderVestTemplatePouches();
};

async function renderVestTemplatePouches() {
    const container = document.getElementById('vest-pouches-editor');
    if (!container) return;
    container.innerHTML = '';

    // Загружаем шаблоны подсумков
    let pouchTemplates = [];
    try {
        pouchTemplates = await loadTemplatesForLobby('pouch');
    } catch (e) {
        console.error('Failed to load pouch templates', e);
    }

    vestTemplateEditorPouches.forEach((pouch, index) => {
        const row = document.createElement('div');
        row.style.display = 'flex';
        row.style.gap = '10px';
        row.style.alignItems = 'center';
        row.style.marginBottom = '5px';

        const select = document.createElement('select');
        select.className = 'form-control';
        select.style.flex = '2';
        select.innerHTML = '<option value="">-- Выберите подсумок --</option>';
        pouchTemplates.forEach(t => {
            const option = document.createElement('option');
            option.value = t.id;
            option.textContent = t.name;
            if (pouch.type == t.id) option.selected = true;
            select.appendChild(option);
        });
        select.onchange = (e) => {
            const templateId = e.target.value;
            const template = pouchTemplates.find(t => t.id == templateId);
            if (template) {
                pouch.type = template.id;
                pouch.capacity = template.volume || 0;
                renderVestTemplatePouches(); // обновить отображение объёма
            }
        };

        const volumeSpan = document.createElement('span');
        volumeSpan.style.width = '80px';
        volumeSpan.textContent = `${pouch.capacity} л`;

        const delBtn = document.createElement('button');
        delBtn.type = 'button';
        delBtn.className = 'btn btn-sm btn-danger';
        delBtn.textContent = '✕';
        delBtn.onclick = () => {
            vestTemplateEditorPouches.splice(index, 1);
            renderVestTemplatePouches();
        };

        row.appendChild(select);
        row.appendChild(volumeSpan);
        row.appendChild(delBtn);
        container.appendChild(row);
    });
}

// ========== 6. ВКЛАДКА "ИНВЕНТАРЬ" ==========
async function renderInventoryTab(data) {
    const container = document.getElementById('sheet-tab-inventory');
    normalizeCharacterAmmoSlots(data);
    const inv = data.inventory || {};
    const eq = data.equipment || {};
    const pockets = Array.isArray(inv.pockets) ? inv.pockets : [];
    const backpack = Array.isArray(inv.backpack) ? inv.backpack : [];
    const pocketMaxVolume = pocketCapacity(data);
    const pocketFill = pockets.reduce((sum, item) => sum + (item.volume || 0) * (item.quantity || 1), 0);

    // Загружаем все возможные шаблоны предметов
    const allTemplates = await getAllItemTemplates();

    // Фильтруем нужные категории из allTemplates
    const visibleTemplates = window.isGM ? allTemplates : allTemplates.filter(t => t.source !== 'local');
    const helmetTemplates = visibleTemplates.filter(t => t.category === 'helmet');
    const gasMaskTemplates = visibleTemplates.filter(t => t.category === 'gas_mask');
    const pouchTemplates = allTemplates.filter(t => t.category === 'pouch');
    const modificationTemplates = allTemplates.filter(t => t.category === 'modification');
    const vestTemplates = allTemplates.filter(t => t.category === 'vest');

    // Группируем для селекторов в карманах/рюкзаке
    const groupedByCategory = {};
    visibleTemplates.forEach(t => {
        const group = t.categoryDisplay || 'Прочее';
        if (!groupedByCategory[group]) groupedByCategory[group] = [];
        groupedByCategory[group].push(t);
    });

    const equippedBackpack = eq.backpack || null;
    const backpackLimit = Number(equippedBackpack?.attributes?.limit || equippedBackpack?.attributes?.capacity || 0);
    const backpackWeightReduction = Number(equippedBackpack?.attributes?.weight_reduction || 0);
    const backpackFill = backpack.reduce((sum, item) => sum + backpackItemVolume(data,item), 0);

    const rawTotalWeight = calculateCarriedWeight(data);
    const movementPenalty = getMovementPenaltyBreakdown(data, rawTotalWeight);

    let html = `
        <div data-widget-block="inventory-weight" style="display:flex; gap:18px; align-items:flex-start; flex-wrap:wrap; margin-bottom:15px;">
            <div><strong>Общий вес:</strong> <span id="total-weight-display">${rawTotalWeight.toFixed(1)}</span> кг</div>
            <div>
                <strong>Штраф веса:</strong> <span id="weight-penalty-display">${movementPenalty.weightPenalty}</span>
                <small style="opacity:0.65;">
                    (1 за <span id="move-penalty-weight-step">${movementPenalty.weightPerPenalty.toFixed(1)}</span> кг)
                </small>
            </div>
            <details id="movement-penalty-details" style="min-width:min(100%, 310px);">
                <summary style="cursor:pointer;">
                    <strong>Итоговый штраф перемещения:</strong>
                    <span id="move-penalty-display">${movementPenalty.total}</span>
                </summary>
                <div style="display:grid; grid-template-columns:minmax(0,1fr) auto; gap:5px 14px; margin-top:8px; padding:9px 11px; background:rgba(0,0,0,.12); border-radius:7px;">
                    <span>Вес до рюкзака</span><span id="weight-penalty-raw">${movementPenalty.rawWeightPenalty}</span>
                    <span>Снижение от рюкзака</span><span id="weight-penalty-backpack">−${movementPenalty.backpackReduction}</span>
                    <span>Итог от веса</span><span id="weight-penalty-source">${movementPenalty.weightPenalty}</span>
                    <span>Броня</span><span id="armor-penalty-source">${movementPenalty.armorPenalty}</span>
                    <span>Шлем</span><span id="helmet-penalty-source">${movementPenalty.helmetPenalty}</span>
                    <span>Травмы ног</span><span id="injury-penalty-source">${movementPenalty.injuries}</span>
                    <span>Опьянение</span><span id="intoxication-penalty-source">${movementPenalty.intoxicationPenalty}</span>
                    <span>Временные модификаторы</span><span id="temporary-penalty-source">${movementPenalty.temporary}</span>
                    <span id="exoskeleton-weight-rule" style="grid-column:span 2; opacity:.7; font-size:12px; display:${movementPenalty.poweredExoskeleton ? 'block' : 'none'};">
                        Запитанный экзоскелет: броня устанавливает штраф 5, перегруз не учитывается. Бег и спринт недоступны.
                    </span>
                </div>
            </details>
        </div>
        ${window.isGM ? `<div style="margin-bottom: 15px; display: flex; gap: 10px; flex-wrap: wrap;">
            <button type="button" class="btn btn-sm btn-primary" onclick="openCreateInventoryItemModal()">➕ Создать предмет</button>
            <button type="button" class="btn btn-sm btn-secondary" onclick="openCreateVestTemplateModal()">➕ Создать разгрузку</button>
            <button type="button" class="btn btn-sm btn-secondary" onclick="openCreateModuleTemplateModal()">➕ Создать модуль</button>
            <button type="button" class="btn btn-sm btn-secondary" onclick="openCreateMagazineTemplateModal()">➕ Создать магазин</button>
        </div>` : ''}
        <hr>
        <section data-widget-block="pockets"><h4>Карманы</h4>
        <div style="margin-bottom:10px;">Заполнено: <span id="pocket-fill-display">${pocketFill}</span> / <span id="pocket-capacity-display">${pocketMaxVolume}</span></div>
        <div style="display: grid; grid-template-columns: 2fr 1fr 1fr 1fr auto; gap: 5px; font-weight: bold; margin-bottom: 5px; align-items: center;">
            <div>Название</div><div>Вес</div><div>Объём</div><div>Кол-во</div><div></div>
        </div>
        <div id="pockets-container"></div>
        ${window.isGM ? `<div style="margin-top:10px; display:flex; gap:8px; flex-wrap:wrap;">
            <button type="button" class="btn btn-sm btn-secondary" onclick="openInventoryTemplatePicker()">➕ Добавить предмет</button>
        </div>
        <button type="button" class="btn btn-sm btn-secondary" onclick="addPocketItemManual()">📝 Свой предмет</button>` : ''}
        ${!window.isGM ? `<div style="margin-top:10px; display:flex; gap:8px; flex-wrap:wrap;">
            <button type="button" class="btn btn-sm btn-secondary" onclick="openInventoryTemplatePicker()">➕ Добавить предмет</button>
        </div>` : ''}

        </section><!-- Пояс -->
        ${eq.belt?.templateId ? `
        <div class="equipment-group" data-widget-block="belt" style="margin-top: 20px;">
            <div class="equipment-header" style="display: flex; align-items: center; justify-content: space-between;">
                <h4>Пояс ${renderCreatedByPlayerBadge(eq.belt)}</h4>
                <button type="button" class="btn btn-sm btn-danger" onclick="unequipBelt()">Снять</button>
            </div>
            <div style="margin-bottom: 10px;">
                <label>Предмет на поясе</label>
                <div style="display: flex; align-items: center; gap: 10px; margin-top: 5px;">
                    <span style="flex: 1;">
                        ${eq.belt?.storedItem ?
                            `${escapeHtml(eq.belt.storedItem.name)} (${eq.belt.storedItem.type === 'helmet' ? 'Шлем' : 'Противогаз'})` :
                            '<span style="color: #aaa;">Пусто</span>'}
                    </span>
                    ${eq.belt?.storedItem ?
                        `<button type="button" class="btn btn-sm btn-danger" onclick="unequipFromBelt()">Снять</button>` : ''}
                </div>
            </div>
            <div style="display: flex; align-items: center;">
                <h5 style="margin: 0;">Подсумки</h5>
                <button type="button" class="btn btn-sm btn-secondary" onclick="addBeltPouch()" style="padding: 2px 8px;">➕</button>
            </div>
            <div id="belt-pouches-container"></div>
            <div style="display: flex; align-items: center; margin-top: 15px;">
                <h5 style="margin: 0;">Модификации пояса</h5>
                <button type="button" class="btn btn-sm btn-secondary" onclick="addBeltModification()" title="Добавить модификацию" style="padding: 2px 8px;">➕</button>
            </div>
            <div id="belt-modifications-container">
                ${renderBeltModifications(eq.belt?.modifications || [], modificationTemplates.filter(t => t.attributes?.type === 'belt'))}
            </div>
        </div>
        ` : ''}

        <!-- Разгрузка -->
        ${eq.vest?.templateId ? `
        <div class="equipment-group" data-widget-block="vest" style="margin-top: 20px;">
            <div class="equipment-header" style="display: flex; align-items: center; justify-content: space-between;">
                <h4>Разгрузка ${renderCreatedByPlayerBadge(eq.vest)}</h4>
                <button type="button" class="btn btn-sm btn-danger" onclick="unequipVest()">Снять</button>
            </div>
            <div style="display: flex; gap: 10px; margin-bottom: 10px; align-items: flex-end;">
                <div style="flex: 1;">
                    <label>Модель</label>
                    <select name="equipment.vest.model" class="form-control" onchange="onVestModelChange(this)" style="margin-bottom: 0;">
                        <option value="">-- Выберите модель --</option>
                        <option value="custom" ${eq.vest?.model === 'custom' ? 'selected' : ''}>Своя (база)</option>
                        ${vestTemplates.map(t => `<option value="${t.id}" ${eq.vest?.model == t.id ? 'selected' : ''}>${t.name}</option>`).join('')}
                    </select>
                </div>
            </div>
            ${eq.vest?.model === 'custom' ? `
                <div style="margin-bottom: 10px;">
                    <label>Общий объём</label>
                    <input type="number" class="form-control number-input" name="equipment.vest.totalCapacity" value="${eq.vest?.totalCapacity || 0}" placeholder="Объём">
                </div>
            ` : ''}
            <div style="display: flex; align-items: center;">
                <h5 style="margin: 0;">Подсумки</h5>
                ${eq.vest?.model === 'custom' ? `<button type="button" class="btn btn-sm btn-secondary" onclick="addVestPouch()" style="padding: 2px 8px;">➕</button>` : ''}
            </div>
            <div id="vest-pouches-container"></div>
        </div>
        ${(() => {
            const plateInfo = getEffectiveTorsoProtection();
            if (!plateInfo) return '';
            const frontText = plateInfo.front !== null ? `${plateInfo.front}%` : 'нет';
            const backText = plateInfo.back !== null ? `${plateInfo.back}%` : 'нет';
            return `<div data-vest-protection style="margin: 10px 0; padding: 8px; background: rgba(0,100,0,0.1); border-radius: 4px;">
                <strong>Бронеплиты:</strong> перед ${frontText}, спина ${backText}
            </div>`;
        })()}
        ` : ''}

        <section data-widget-block="backpack"><h4 style="margin-top:20px;">${!equippedBackpack && backpack.length ? 'Неразмещённое снаряжение' : 'Рюкзак'}</h4>
        <div style="display: flex; gap: 10px; align-items: center; margin-bottom: 10px; flex-wrap: wrap;">
            ${equippedBackpack ? `
                <strong>${escapeHtml(equippedBackpack.name || 'Рюкзак')}</strong>
                <span id="backpack-fill-display">Заполнено: ${backpackFill} / ${backpackLimit}</span>
                <button type="button" class="btn btn-sm btn-danger" onclick="unequipBackpack()">Снять</button>
            ` : `<span style="opacity:0.7;">${backpack.length ? 'Разложите эти вещи по карманам, подсумкам или наденьте рюкзак.' : 'Рюкзак не экипирован.'}</span>`}
        </div>
        <div style="display: grid; grid-template-columns: 2fr 1fr 1fr 1fr auto; gap: 5px; font-weight: bold; margin-bottom: 5px; align-items: center;">
            <div>Название</div><div>Вес</div><div>Объём</div><div>Кол-во</div><div></div>
        </div>
        <div id="backpack-container"></div>
        ${equippedBackpack ? `<div style="margin-top:10px;">
            <button type="button" class="btn btn-sm btn-secondary" onclick="openInventoryTemplatePicker('backpack')">➕ Добавить предмет</button>
        </div>
        ${window.isGM ? `<button type="button" class="btn btn-sm btn-secondary" onclick="addBackpackItemManual()">📝 Свой предмет</button>` : ''}` : ''}
    </section>`;

    const viewState = captureSheetViewState();
    container.innerHTML = html;
    if (eq.belt?.templateId) {
        renderBeltPouchesNew(eq.belt.pouches || [], pouchTemplates, allTemplates);
    }
    if (eq.vest?.templateId) {
        renderVestPouchesNew(eq.vest.pouches || [], pouchTemplates, eq.vest.model === 'custom', eq.vest.totalCapacity, allTemplates);
    }
    const pocketsContainer = document.getElementById('pockets-container');
    if (pocketsContainer) {
        pocketsContainer.innerHTML = '';
        pockets.forEach((item, index) => {
            renderBackpackItem(migrateOldItemToNew(item), index, ['inventory', 'pockets'], pocketsContainer, allTemplates);
        });
    }
    const backpackItems = Array.isArray(inv.backpack)
        ? inv.backpack.map(item => migrateOldItemToNew(item))
        : [];
    renderBackpackNew(backpackItems, groupedByCategory, allTemplates);

    const pocketsContainerEl = document.getElementById('pockets-container');
    const backpackContainerEl = document.getElementById('backpack-container');
    if (pocketsContainerEl) setupDropTarget(pocketsContainerEl, ['inventory', 'pockets'], { capacity: pocketMaxVolume, contents: pockets });
    if (backpackContainerEl && equippedBackpack) {
        setupDropTarget(backpackContainerEl, ['inventory', 'backpack'], { capacity: backpackLimit, contents: backpack });
    }

    recalculateInventoryTotals();

    container.oninput = (e) => {
        const target = e.target;
        if (target.matches('input[name*="weight"], input[name*="volume"], input[name*="quantity"]')) {
            updateDataFromFields();
            recalculateInventoryTotals();
            scheduleAutoSave();
        }
    };
    restoreSheetViewState(viewState);
}

function getItemByPath(pathArray) {
    let current = currentCharacterData;
    for (let i = 0; i < pathArray.length; i++) {
        const key = pathArray[i];
        if (current === null || current === undefined) return null;

        if (Array.isArray(current) && typeof key === 'number') {
            current = current[key];
        } else if (typeof current === 'object' && key in current) {
            current = current[key];
        } else {
            return null;
        }
    }
    return current;
}

function removeItemByPath(path) {
    let parent = currentCharacterData;
    for (let i = 0; i < path.length - 1; i++) {
        const key = path[i];
        if (Array.isArray(parent) && typeof key === 'number') parent = parent[key];
        else if (typeof parent === 'object' && key in parent) parent = parent[key];
        else return false;
    }
    const lastKey = path[path.length - 1];
    if (Array.isArray(parent) && typeof lastKey === 'number') {
        parent.splice(lastKey, 1);
        return true;
    }
    return false;
}

// Обновить поле предмета по пути
window.updateBackpackItemAtPath = function(pathStr, field, value) {
    const path = pathStr.split(',').map(p => isNaN(p) ? p : parseInt(p));
    const item = getItemByPath(path);
    if (!item) return;

    if (field === 'quantity') {
        setBackpackItemQuantityAtPath(path, parseInt(value) || 1);
    } else if (field === 'name') {
        item.name = value;
    } else {
        item[field] = parseFloat(value) || 0;
    }

    recalculateInventoryTotals();
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
};

window.adjustBackpackItemQuantityAtPath = function(pathStr, delta) {
    const path = pathStr.split(',').map(p => isNaN(p) ? p : parseInt(p));
    const item = getItemByPath(path);
    if (!item) return;

    const currentQuantity = Math.max(1, Number(item.quantity) || 1);
    const nextQuantity = currentQuantity + Number(delta || 0);
    if (setBackpackItemQuantityAtPath(path, nextQuantity)) {
        recalculateInventoryTotals();
        renderInventoryTab(currentCharacterData);
        scheduleAutoSave();
    }
};

window.removeBackpackItemAtPath = function(pathStr) {
    const path = pathStr.split(',').map(p => isNaN(p) ? p : parseInt(p));
    if (path.length === 0) return;

    // 1. Удаление из данных
    let parentData = currentCharacterData;
    for (let i = 0; i < path.length - 1; i++) {
        const key = path[i];
        if (Array.isArray(parentData) && typeof key === 'number') {
            parentData = parentData[key];
        } else if (typeof parentData === 'object' && key in parentData) {
            parentData = parentData[key];
        } else {
            return;
        }
    }
    const index = path[path.length - 1];
    if (!Array.isArray(parentData)) return;
    parentData.splice(index, 1);

    // 2. Удаление из DOM и обновление путей
    const itemDiv = document.querySelector(`[data-path="${pathStr}"]`);
    if (!itemDiv) {
        renderInventoryTab(currentCharacterData);
        recalculateInventoryTotals();
        scheduleAutoSave();
        forceSyncCharacter();
        return;
    }

    const containerDiv = itemDiv.parentNode;
    const containerPath = path.slice(0, -1);
    itemDiv.remove();

    // Обновляем data-path у оставшихся элементов в контейнере
    const remainingItems = Array.from(containerDiv.children).filter(el => el.hasAttribute('data-path'));
    remainingItems.forEach((el, idx) => {
        const newPath = containerPath.concat(idx).join(',');
        el.dataset.path = newPath;
        updateHandlersInElement(el, containerPath, idx);
    });

    if (containerDiv.classList.contains('container-contents')) {
        updatePouchVolumeFromContentsDiv(containerDiv);
    }

    recalculateInventoryTotals();
    scheduleAutoSave();
    forceSyncCharacter();
};

function getBackpackItemAtPath(path) {
    if (!Array.isArray(path) || path.length === 0) return null;
    let parentData = currentCharacterData;
    for (let i = 0; i < path.length; i += 1) {
        const key = path[i];
        if (Array.isArray(parentData) && typeof key === 'number') {
            parentData = parentData[key];
        } else if (parentData && typeof parentData === 'object' && key in parentData) {
            parentData = parentData[key];
        } else {
            return null;
        }
    }
    return parentData ?? null;
}

function setBackpackItemQuantityAtPath(path, quantity) {
    if (!Array.isArray(path) || path.length === 0) return false;
    let parentData = currentCharacterData;
    for (let i = 0; i < path.length - 1; i += 1) {
        const key = path[i];
        if (Array.isArray(parentData) && typeof key === 'number') {
            parentData = parentData[key];
        } else if (parentData && typeof parentData === 'object' && key in parentData) {
            parentData = parentData[key];
        } else {
            return false;
        }
    }

    const index = path[path.length - 1];
    if (!Array.isArray(parentData) || typeof index !== 'number' || !parentData[index]) return false;
    const item = parentData[index];
    if (!quantity || quantity <= 0) {
        parentData.splice(index, 1);
        return true;
    }
    if (item.category === 'ammo') {
        const stacks = splitAmmoItemIntoSlotStacks(item, quantity);
        parentData.splice(index, 1, ...stacks);
    } else {
        item.quantity = Math.floor(quantity);
    }
    return true;
}

window.dropBackpackItemAtPath = async function(pathStr) {
    if (medicalOperationInProgress) return;
    if (autoSaveTimer) await forceSyncCharacter();
    if (sheetSaveBlocked) {
        showNotification('Переоткройте лист перед переносом предметов', 'system');
        return;
    }
    const actorId = Number(currentCharacterId);
    const path = pathStr.split(',').map(p => (p === '' || Number.isNaN(Number(p)) ? p : parseInt(p, 10)));
    if (path.length === 0) return;

    const item = getBackpackItemAtPath(path);
    if (!item) return;

    const currentQuantity = Math.max(1, Number(item.quantity) || 1);
    let amount = currentQuantity;
    if (currentQuantity > 1) {
        const response = window.prompt(`Сколько выбросить? 1-${currentQuantity}`, '1');
        if (response === null) return;
        amount = Math.max(1, Math.min(parseInt(response, 10) || 1, currentQuantity));
    }

    const locationCharacterId = currentCharacterId || window.currentLocationCharacterId || null;
    const locationPosition = window.isLocationActive && typeof window.getLocationCharacterPosition === 'function'
        ? window.getLocationCharacterPosition(locationCharacterId)
        : null;

    if (window.isLocationActive && window.currentLocationId && locationPosition) {
        try {
            await Server.waitForCharacterSave(actorId);
            if (Number(currentCharacterId) !== actorId) return;
            const result = await Server.dropLocationItem(window.currentLobbyId, window.currentLocationId, {
                character_id: actorId, character_revision: currentCharacterData._revision,
                item_path: path, expected_item: item, amount,
            });
            if (result.created) window.addLocationObject?.(result.object);
            else window.updateLocationObject?.(result.object);
            if (Number(currentCharacterId) === actorId) {
                currentCharacterData = result.data;
                await renderInventoryTab(currentCharacterData);
                recalculateInventoryTotals();
            }
            showNotification('Предмет выброшен', 'success');
        } catch (error) {
            showNotification(error.message || 'Не удалось выбросить предмет', 'system');
        }
        return;
    }

    if (amount >= currentQuantity) {
        window.removeBackpackItemAtPath(pathStr);
    } else {
        setBackpackItemQuantityAtPath(path, currentQuantity - amount);
        renderInventoryTab(currentCharacterData);
        recalculateInventoryTotals();
        scheduleAutoSave();
        forceSyncCharacter();
    }
};

function updateHandlersInElement(element, basePath, index) {
    const newPath = basePath.concat(index).join(',');
    // Кнопка удаления (крестик)
    const delBtn = element.querySelector('.btn-danger');
    if (delBtn) {
        delBtn.onclick = () => removeBackpackItemAtPath(newPath);
    }
    // Поля ввода (вес, объём, количество, название)
    element.querySelectorAll('input').forEach(input => {
        const placeholder = input.placeholder;
        let field = null;
        if (placeholder === 'Вес') field = 'weight';
        else if (placeholder === 'Объём') field = 'volume';
        else if (placeholder === 'Кол-во') field = 'quantity';
        else if (input.type === 'text') field = 'name';

        if (field) {
            input.onchange = (e) => updateBackpackItemAtPath(newPath, field, e.target.value);
        }
    });
    // Кнопки сворачивания и добавления внутрь используют замыкания с путём, их трогать не нужно — они продолжат работать,
    // так как ссылаются на объект item, а не на путь в виде строки.
}

function renderBeltPouchesNew(pouches, pouchTemplates, allTemplates) {
    const container = document.getElementById('belt-pouches-container');
    if (!container) return;
    container.innerHTML = '';
    if (!pouches || pouches.length === 0) {
        container.innerHTML = '<p>Нет подсумков</p>';
        return;
    }
    pouches.forEach((pouch, index) => {
        renderPouchItem(pouch, index, ['equipment', 'belt', 'pouches', index], container, pouchTemplates, allTemplates);
    });
}

function renderVestPouchesNew(pouches, pouchTemplates, isCustom, totalCapacity, allTemplates) {
    const container = document.getElementById('vest-pouches-container');
    if (!container) return;
    container.innerHTML = '';
    if (!pouches || pouches.length === 0) {
        container.innerHTML = '<p>Нет подсумков</p>';
        return;
    }
    pouches.forEach((pouch, index) => {
        renderVestPouchItem(pouch, index, ['equipment', 'vest', 'pouches', index], container, pouchTemplates, isCustom, allTemplates);
    });
}

function renderPouchItem(pouch, index, path, parentContainer, pouchTemplates, allTemplates) {
    const itemDiv = document.createElement('div');
    itemDiv.className = 'container-item';
    itemDiv.style.marginBottom = '8px';
    itemDiv.style.padding = '8px';
    itemDiv.style.border = '1px solid #666';
    itemDiv.style.borderRadius = '4px';
    itemDiv.style.backgroundColor = 'rgba(0,0,0,0.2)';
    itemDiv.dataset.path = path.join(',');
    itemDiv.dataset.widgetIdentity = JSON.stringify(pouch.id != null ? { id: pouch.id } : { type: pouch.type, name: pouch.name });

    const row = document.createElement('div');
    row.style.display = 'grid';
    row.style.gridTemplateColumns = '2fr 1fr auto';
    row.style.gap = '10px';
    row.style.alignItems = 'center';

    // Иконка сворачивания + селект
    const leftWrapper = document.createElement('div');
    leftWrapper.style.display = 'flex';
    leftWrapper.style.alignItems = 'center';
    leftWrapper.style.gap = '5px';

    const toggleIcon = document.createElement('span');
    toggleIcon.textContent = '▶';
    toggleIcon.style.cursor = 'pointer';
    toggleIcon.style.userSelect = 'none';
    leftWrapper.appendChild(toggleIcon);

    const select = document.createElement('select');
    select.dataset.widgetKey = 'pouch-type';
    select.className = 'form-control';
    select.innerHTML = '<option value="">-- Выберите подсумок --</option>';
    pouchTemplates.forEach(t => {
        const option = document.createElement('option');
        option.value = t.id;
        option.textContent = t.name;
        if (pouch.type == t.id) option.selected = true;
        select.appendChild(option);
    });
    select.onchange = () => {
        const template = pouchTemplates.find(t => t.id == select.value);
        if (template) {
            pouch.type = template.id;
            pouch.capacity = template.volume || 0;
            pouch.name = template.name;
            renderInventoryTab(currentCharacterData);
            scheduleAutoSave();
        }
    };
    leftWrapper.appendChild(select);
    row.appendChild(leftWrapper);

    const infoSpan = document.createElement('span');
    infoSpan.dataset.volumeInfo = '';
    const used = calculatePouchUsedVolume(pouch);
    const internalLimit = pouch.internalVolume || pouch.capacity;
    infoSpan.textContent = `📦 ${used} / ${internalLimit} л`;
    row.appendChild(infoSpan);

    const delBtn = document.createElement('button');
    delBtn.dataset.widgetKey = 'pouch-delete';
    delBtn.className = 'btn btn-sm btn-danger';
    delBtn.textContent = '✕';
    delBtn.onclick = () => {
        const parentArray = currentCharacterData.equipment.belt.pouches;
        parentArray.splice(index, 1);
        renderInventoryTab(currentCharacterData);
        scheduleAutoSave();
    };
    row.appendChild(delBtn);
    itemDiv.appendChild(row);

    // Содержимое
    const contentsDiv = document.createElement('div');
    contentsDiv.className = 'container-contents';
    contentsDiv.style.marginLeft = '20px';
    contentsDiv.style.marginTop = '10px';
    contentsDiv.style.paddingLeft = '10px';
    contentsDiv.style.borderLeft = '2px dashed #666';
    contentsDiv.style.display = 'none';
    contentsDiv.setAttribute('data-container-path', path.concat('contents').join(','));
    const containerKey = inventoryContainerKey(pouch, path);
    setInventoryContainerOpen(contentsDiv, toggleIcon, containerKey, expandedInventoryContainers.has(containerKey));

    if (pouch.contents && pouch.contents.length > 0) {
        pouch.contents.forEach((subItem, subIndex) => {
            renderBackpackItem(subItem, subIndex, path.concat('contents'), contentsDiv, allTemplates);
        });
    }

    const pouchTemplate = pouchTemplates.find(t => t.id == pouch.type);
    const hasArmorPlateSlot = pouchTemplate?.attributes?.slots?.some(s => s.type === 'armor_plate');

    if (window.isGM && !hasArmorPlateSlot) {
        const addBtn = document.createElement('button');
        addBtn.dataset.widgetKey = 'pouch-add';
        addBtn.type = 'button';
        addBtn.className = 'btn btn-sm btn-secondary';
        addBtn.textContent = '➕ Добавить внутрь';
        addBtn.onclick = () => openInventoryTemplatePicker(path.concat('contents'));
        contentsDiv.appendChild(addBtn);
    }

    setupDropTarget(contentsDiv, path.concat('contents'), pouch);

    itemDiv.appendChild(contentsDiv);

    toggleIcon.onclick = () => setInventoryContainerOpen(
        contentsDiv, toggleIcon, containerKey, contentsDiv.style.display === 'none',
    );

    // Универсальное отображение слотов предмета
    if (getItemSlots(pouch).length > 0) {
        const slotsHtml = renderSlotsUniversal(pouch, path, 1);
        if (slotsHtml) {
            const slotsDiv = document.createElement('div');
            slotsDiv.className = 'item-slots-container';
            slotsDiv.style.marginTop = '8px';
            slotsDiv.style.marginLeft = '20px';
            slotsDiv.innerHTML = slotsHtml;
            itemDiv.appendChild(slotsDiv);
        }
    }

    setupDropTarget(itemDiv, path.concat('contents'), pouch);

    parentContainer.appendChild(itemDiv);
}

function renderVestPouchItem(pouch, index, path, parentContainer, pouchTemplates, isCustom, allTemplates) {
    const itemDiv = document.createElement('div');
    itemDiv.className = 'container-item';
    itemDiv.style.marginBottom = '8px';
    itemDiv.style.padding = '8px';
    itemDiv.style.border = '1px solid #666';
    itemDiv.style.borderRadius = '4px';
    itemDiv.style.backgroundColor = 'rgba(0,0,0,0.2)';
    itemDiv.dataset.path = path.join(',');
    itemDiv.dataset.widgetIdentity = JSON.stringify(pouch.id != null ? { id: pouch.id } : { type: pouch.type, name: pouch.name });

    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.alignItems = 'center';
    row.style.gap = '10px';

    // Левая часть с иконкой и селектом
    const leftWrapper = document.createElement('div');
    leftWrapper.style.display = 'flex';
    leftWrapper.style.alignItems = 'center';
    leftWrapper.style.gap = '5px';
    leftWrapper.style.flex = '1';

    const toggleIcon = document.createElement('span');
    toggleIcon.textContent = '▶';
    toggleIcon.style.cursor = 'pointer';
    toggleIcon.style.userSelect = 'none';
    leftWrapper.appendChild(toggleIcon);

    const select = document.createElement('select');
    select.dataset.widgetKey = 'pouch-type';
    select.className = 'form-control';
    select.disabled = !isCustom;
    select.style.flex = '1';
    select.innerHTML = '<option value="">-- Выберите подсумок --</option>';
    pouchTemplates.forEach(t => {
        const option = document.createElement('option');
        option.value = t.id;
        option.textContent = t.name;
        if (pouch.type == t.id) option.selected = true;
        select.appendChild(option);
    });
    select.onchange = () => {
        if (!isCustom) return;
        const template = pouchTemplates.find(t => t.id == select.value);
        if (template) {
            pouch.type = template.id;
            pouch.capacity = template.volume || 0;
            pouch.name = template.name;
            renderInventoryTab(currentCharacterData);
            scheduleAutoSave();
        }
    };
    leftWrapper.appendChild(select);
    row.appendChild(leftWrapper);

    const infoSpan = document.createElement('span');
    infoSpan.dataset.volumeInfo = '';
    const used = calculatePouchUsedVolume(pouch);
    const internalLimit = pouch.internalVolume || pouch.capacity;
    infoSpan.textContent = `📦 ${used} / ${internalLimit} л`;
    infoSpan.style.whiteSpace = 'nowrap';
    row.appendChild(infoSpan);

    // Кнопка удаления ТОЛЬКО для кастомной разгрузки
    if (isCustom) {
        const delBtn = document.createElement('button');
        delBtn.dataset.widgetKey = 'pouch-delete';
        delBtn.type = 'button';
        delBtn.className = 'btn btn-sm btn-danger';
        delBtn.textContent = '✕';
        delBtn.style.flexShrink = '0';
        delBtn.onclick = () => {
            const parentArray = currentCharacterData.equipment.vest.pouches;
            parentArray.splice(index, 1);
            renderInventoryTab(currentCharacterData);
            scheduleAutoSave();
        };
        row.appendChild(delBtn);
    }
    itemDiv.appendChild(row);

    // Содержимое
    const contentsDiv = document.createElement('div');
    contentsDiv.className = 'container-contents';
    contentsDiv.style.marginLeft = '20px';
    contentsDiv.style.marginTop = '10px';
    contentsDiv.style.paddingLeft = '10px';
    contentsDiv.style.borderLeft = '2px dashed #666';
    contentsDiv.style.display = 'none';
    contentsDiv.setAttribute('data-container-path', path.concat('contents').join(','));

    const containerKey = inventoryContainerKey(pouch, path);
    setInventoryContainerOpen(contentsDiv, toggleIcon, containerKey, expandedInventoryContainers.has(containerKey));

    if (pouch.contents && pouch.contents.length > 0) {
        pouch.contents.forEach((subItem, subIndex) => {
            renderBackpackItem(subItem, subIndex, path.concat('contents'), contentsDiv, allTemplates);
        });
    }

    const pouchTemplate = pouchTemplates.find(t => t.id == pouch.type);
    const hasArmorPlateSlot = pouchTemplate?.attributes?.slots?.some(s => s.type === 'armor_plate');

    if (window.isGM && !hasArmorPlateSlot) {
        const addBtn = document.createElement('button');
        addBtn.dataset.widgetKey = 'pouch-add';
        addBtn.type = 'button';
        addBtn.className = 'btn btn-sm btn-secondary';
        addBtn.textContent = '➕ Добавить внутрь';
        addBtn.onclick = () => openInventoryTemplatePicker(path.concat('contents'));
        contentsDiv.appendChild(addBtn);
    }

    setupDropTarget(contentsDiv, path.concat('contents'), pouch);

    itemDiv.appendChild(contentsDiv);

    toggleIcon.onclick = () => setInventoryContainerOpen(
        contentsDiv, toggleIcon, containerKey, contentsDiv.style.display === 'none',
    );

    if (getItemSlots(pouch).length > 0) {
        const slotsHtml = renderSlotsUniversal(pouch, path, 1);
        if (slotsHtml) {
            const slotsDiv = document.createElement('div');
            slotsDiv.className = 'item-slots-container';
            slotsDiv.style.marginTop = '8px';
            slotsDiv.style.marginLeft = '20px';
            slotsDiv.innerHTML = slotsHtml;
            itemDiv.appendChild(slotsDiv);
        }
    }

    setupDropTarget(itemDiv, path.concat('contents'), pouch);

    parentContainer.appendChild(itemDiv);
}

function updatePouchVolumeFromContentsDiv(contentsDiv) {
    const pouchDiv = contentsDiv.closest('.container-item');
    if (!pouchDiv) return;
    const infoSpan = pouchDiv.querySelector('span[data-volume-info]');
    if (!infoSpan) return;

    // Получаем путь из data-path
    const pathStr = pouchDiv.dataset.path;
    if (!pathStr) return;
    const containerPath = pathStr.split(',').map(p => isNaN(p) ? p : parseInt(p));
    const pouch = getItemByPath(containerPath);
    if (!pouch) return;

    const used = calculatePouchUsedVolume(pouch);
    const internalLimit = pouch.internalVolume || pouch.capacity;
    infoSpan.textContent = `📦 ${used} / ${internalLimit} л`;
}

// ========== МОДАЛЬНОЕ ОКНО ДЛЯ СОЗДАНИЯ ПРЕДМЕТА В ИНВЕНТАРЕ ==========
let currentItemCategory = 'consumable'; // по умолчанию

function showItemCategoryFields() {
    document.getElementById('consumable-fields').style.display = 'none';
    document.getElementById('material-fields').style.display = 'none';
    document.getElementById('artifact-fields').style.display = 'none';

    if (currentItemCategory === 'consumable') {
        document.getElementById('consumable-fields').style.display = 'block';
    } else if (currentItemCategory === 'material') {
        document.getElementById('material-fields').style.display = 'block';
    } else if (currentItemCategory === 'artifact') {
        document.getElementById('artifact-fields').style.display = 'block';
    }
}

const CONSUMABLE_EDITOR_IMMEDIATE = [
    ['hp', 'ОЗ'], ['radiation_delta', 'Радиация'], ['stress_delta', 'Стресс'],
    ['pain_delta', 'Боль'], ['exhaustion_delta', 'Истощение'], ['psy_delta', 'Пси-состояние'],
    ['infection_delta', 'Заражение'], ['intoxication_delta', 'Опьянение'], ['temperature_delta', 'Температура']
];
const CONSUMABLE_EDITOR_DELAYED = [
    ['radiation', 'Радиация'], ['stress', 'Стресс'], ['painLevel', 'Боль'],
    ['exhaustion', 'Истощение'], ['psyState', 'Пси-состояние'], ['infection', 'Заражение'],
    ['intoxication', 'Опьянение'], ['temperature', 'Температура']
];
const CONSUMABLE_EDITOR_MODIFIERS = [
    ['strength', 'Сила'], ['agility', 'Ловкость'], ['accuracy', 'Точность'],
    ['will', 'Воля'], ['psy_defense', 'Пси-защита'], ['weight', 'Переносимый вес']
];

window.addConsumableProfileRow = function(config = {}) {
    const container = document.getElementById('consumable-effects-container');
    const row = document.createElement('div');
    row.className = 'consumable-profile-row';
    row.style.cssText = 'display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:6px 0';
    row.innerHTML = `
        <select class="form-control profile-kind" aria-label="Тип эффекта">
            <option value="immediate">Сразу</option><option value="temporary">Временный бонус</option>
            <option value="delayed">После задержки</option><option value="trigger">По событию</option>
        </select>
        <select class="form-control profile-field" aria-label="Состояние"></select>
        <input class="form-control number-input profile-value" type="number" step="any" value="0" aria-label="Изменение" style="width:85px">
        <input class="form-control number-input profile-duration" type="number" min="1" max="10000" step="1" value="1" aria-label="Длительность" style="width:70px">
        <select class="form-control profile-unit" aria-label="Когда срабатывает">
            <option value="turn_end">Раундов</option><option value="time_elapsed">Минут</option>
            <option value="movement_end">Перемещений</option><option value="day_start">Дней</option>
        </select>
        <button type="button" class="btn btn-sm btn-danger" aria-label="Удалить эффект">×</button>`;
    container.appendChild(row);
    const kind = row.querySelector('.profile-kind');
    const field = row.querySelector('.profile-field');
    const unit = row.querySelector('.profile-unit');
    const refresh = () => {
        const choices = kind.value === 'temporary' ? CONSUMABLE_EDITOR_MODIFIERS
            : kind.value === 'immediate' ? CONSUMABLE_EDITOR_IMMEDIATE : CONSUMABLE_EDITOR_DELAYED;
        const previous = config.field || field.value;
        field.innerHTML = choices.map(([value, label]) => `<option value="${value}">${label}</option>`).join('');
        if (choices.some(([value]) => value === previous)) field.value = previous;
        const isImmediate = kind.value === 'immediate';
        row.querySelector('.profile-duration').style.display = isImmediate ? 'none' : '';
        unit.style.display = isImmediate ? 'none' : '';
        for (const option of unit.options) {
            option.hidden = kind.value === 'trigger'
                ? !['movement_end', 'day_start'].includes(option.value)
                : kind.value === 'delayed' && !['turn_end', 'time_elapsed'].includes(option.value);
        }
        if (kind.value === 'trigger' && !['movement_end', 'day_start'].includes(unit.value)) unit.value = 'movement_end';
        if (kind.value === 'delayed' && !['turn_end', 'time_elapsed'].includes(unit.value)) unit.value = 'turn_end';
    };
    kind.value = config.kind || 'immediate';
    kind.addEventListener('change', () => { config = {}; refresh(); });
    refresh();
    if (config.unit) unit.value = config.unit;
    if (config.value !== undefined) row.querySelector('.profile-value').value = config.value;
    if (config.duration !== undefined) row.querySelector('.profile-duration').value = config.duration;
    row.querySelector('button').onclick = () => row.remove();
};

window.openCreateInventoryItemModal = function(template = null) {
    let modal = document.getElementById('create-inventory-item-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'create-inventory-item-modal';
        modal.className = 'modal';
        modal.style.zIndex = '1320';
        modal.style.display = 'flex';
        modal.style.alignItems = 'center';
        modal.style.justifyContent = 'center';
        modal.innerHTML = `
            <div class="modal-content" style="max-width:760px;max-height:80vh;overflow-y:auto;">
                <span class="close" onclick="document.getElementById('create-inventory-item-modal').style.display='none'">&times;</span>
                <h3 id="inventory-item-editor-title">Создать кастомный предмет</h3>
                <div class="form-group">
                    <label>Категория</label>
                    <select id="item-category-select" class="form-control" onchange="window.itemCategoryChanged(this)">
                        <option value="consumable">Расходник</option>
                        <option value="material">Материал</option>
                        <option value="artifact">Артефакт</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Название</label>
                    <input type="text" id="item-name" class="form-control">
                </div>

                <!-- Поля для расходника -->
                <div id="consumable-fields" style="display: block;">
                    <div class="form-group">
                        <label>Вес</label>
                        <input type="number" id="consumable-weight" class="form-control number-input" value="0" step="0.1">
                    </div>
                    <div class="form-group">
                        <label>Объём</label>
                        <input type="number" id="consumable-volume" class="form-control number-input" value="0" step="0.1">
                    </div>
                    <div class="form-group">
                        <label>Количество использований</label>
                        <input type="number" id="consumable-uses" class="form-control number-input" value="1">
                    </div>
                    <div class="form-group" id="consumable-basic-action-cost">
                        <label>Стоимость применения, ОД</label>
                        <input type="number" id="consumable-action-points" class="form-control number-input" min="0" max="1000" value="0">
                    </div>
                    <div class="form-group" id="consumable-basic-effects">
                        <label>Эффекты препарата</label>
                        <p style="opacity:.7;font-size:12px;margin:3px 0 8px">Отрицательное число снижает состояние. Событие: конец перемещения или начало дня.</p>
                        <div id="consumable-effects-container"></div>
                        <button type="button" class="btn btn-sm btn-primary" onclick="addConsumableProfileRow()">+ Добавить эффект</button>
                    </div>
                    <button type="button" class="btn btn-sm btn-secondary" id="consumable-advanced-enable" onclick="enableAdvancedConsumableEditor()">Точная настройка: все правила каталога</button>
                    <div id="consumable-advanced-panel" style="display:none; margin-top:10px;">
                        <p style="opacity:.7;font-size:12px;">Выберите готовый препарат и добавьте его отдельное правило или весь профиль. Все вложенные параметры ниже можно изменить. Связанные правила лучше переносить вместе.</p>
                        <div class="form-group">
                            <label>Препарат-образец</label>
                            <select id="consumable-profile-source" class="form-control" onchange="refreshConsumableProfileComponents()"></select>
                        </div>
                        <div class="form-group">
                            <label>Правило для добавления</label>
                            <select id="consumable-profile-component" class="form-control"></select>
                        </div>
                        <button type="button" class="btn btn-sm btn-primary" onclick="addCatalogConsumableComponent()">Добавить правило</button>
                        <div id="consumable-advanced-fields" style="margin-top:10px"></div>
                    </div>
                </div>

                <!-- Поля для материала -->
                <div id="material-fields" style="display: none;">
                    <div class="form-group">
                        <label>Вес</label>
                        <input type="number" id="material-weight" class="form-control number-input" value="0" step="0.1">
                    </div>
                    <div class="form-group">
                        <label>Объём</label>
                        <input type="number" id="material-volume" class="form-control number-input" value="0" step="0.1">
                    </div>
                </div>

                <div id="artifact-fields" style="display: none;">
                    <div class="form-group">
                        <label>Вес</label>
                        <input type="number" id="artifact-weight" class="form-control number-input" value="0" step="0.1">
                    </div>
                    <div class="form-group">
                        <label>Объём</label>
                        <input type="number" id="artifact-volume" class="form-control number-input" value="0" step="0.1">
                    </div>
                    <div class="form-group">
                        <label>Эффекты</label>
                        <div id="artifact-effects-container"></div>
                        <button type="button" class="btn btn-sm btn-primary" onclick="addEffectToModal('artifact')">+ Добавить эффект</button>
                    </div>
                </div>

                <div class="form-actions">
                    <button class="btn btn-primary" onclick="saveInventoryItemTemplate()">Сохранить</button>
                    <button class="btn btn-secondary" onclick="document.getElementById('create-inventory-item-modal').style.display='none'">Отмена</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }
    modal._editingTemplate = template;
    modal._advancedProfile = null;
    document.getElementById('consumable-advanced-panel').style.display = 'none';
    document.getElementById('consumable-advanced-enable').style.display = '';
    document.getElementById('consumable-basic-action-cost').style.display = '';
    document.getElementById('consumable-basic-effects').style.display = '';
    document.getElementById('inventory-item-editor-title').textContent = template ? 'Настроить предмет' : 'Создать кастомный предмет';
    document.getElementById('item-name').value = template?.name || '';
    document.getElementById('consumable-weight').value = template?.weight ?? 0;
    document.getElementById('consumable-volume').value = template?.volume ?? 0;
    document.getElementById('consumable-uses').value = template?.attributes?.uses ?? 1;
    document.getElementById('consumable-action-points').value = template?.attributes?.consumable?.direct?.action_points_cost ?? 0;
    document.getElementById('item-category-select').value = template?.category === 'crafting_material'
        ? 'material' : template?.category || 'consumable';
    document.getElementById('item-category-select').disabled = Boolean(template);
    window.itemCategoryChanged(document.getElementById('item-category-select'));
    const consumableContainer = document.getElementById('consumable-effects-container');
    if (consumableContainer) consumableContainer.innerHTML = '';
    const profile = template?.attributes?.consumable;
    if (profile) {
        Object.entries(profile.direct || {}).forEach(([field, value]) => {
            if (CONSUMABLE_EDITOR_IMMEDIATE.some(([key]) => key === field)) addConsumableProfileRow({ kind: 'immediate', field, value });
        });
        (profile.modifiers || []).forEach(modifier => addConsumableProfileRow({
            kind: 'temporary', field: modifier.stat, value: modifier.value,
            duration: modifier.remaining, unit: modifier.tick
        }));
        (profile.effects || []).filter(effect => effect.type === 'delayed_adjustment').forEach(effect => addConsumableProfileRow({
            kind: ['movement_end', 'day_start'].includes(effect.tick) ? 'trigger' : 'delayed',
            field: effect.adjustments?.[0]?.field, value: effect.adjustments?.[0]?.delta,
            duration: effect.remaining, unit: effect.tick
        }));
    }
    const artifactContainer = document.getElementById('artifact-effects-container');
    if (artifactContainer) artifactContainer.innerHTML = '';
    modal.style.display = 'flex';
    if (profile && (profile.editor_version === 2 || !profile.editor_version)) {
        window.enableAdvancedConsumableEditor();
    }
};

window.enableAdvancedConsumableEditor = async function() {
    const modal = document.getElementById('create-inventory-item-modal');
    if (!modal._advancedProfile) {
        const original = modal._editingTemplate?.attributes?.consumable;
        if (original) modal._advancedProfile = JSON.parse(JSON.stringify(original));
        else {
            try {
                modal._advancedProfile = document.querySelectorAll('#consumable-effects-container .consumable-profile-row').length
                    ? readCustomConsumableProfile()
                    : { editor_version: 2, direct: {}, effects: [], modifiers: [] };
            }
            catch (error) { showNotification(error.message); return; }
        }
        modal._advancedProfile.editor_version = 2;
    }
    document.getElementById('consumable-basic-action-cost').style.display = 'none';
    document.getElementById('consumable-basic-effects').style.display = 'none';
    document.getElementById('consumable-advanced-enable').style.display = 'none';
    document.getElementById('consumable-advanced-panel').style.display = '';
    renderConsumableProfileTree(document.getElementById('consumable-advanced-fields'), modal._advancedProfile);
    const templates = (await getAllItemTemplates()).filter(entry =>
        entry.category === 'consumable' && entry.attributes?.consumable?.direct
    );
    modal._profileSources = templates;
    const sourceSelect = document.getElementById('consumable-profile-source');
    sourceSelect.innerHTML = templates.map(entry =>
        `<option value="${entry.id}">${escapeHtml(entry.name)}</option>`
    ).join('');
    window.refreshConsumableProfileComponents();
};

window.refreshConsumableProfileComponents = function() {
    const modal = document.getElementById('create-inventory-item-modal');
    const templateId = Number(document.getElementById('consumable-profile-source').value);
    const source = (modal._profileSources || []).find(entry => entry.id === templateId);
    modal._profileComponentOptions = profileComponentOptions(source);
    document.getElementById('consumable-profile-component').innerHTML = modal._profileComponentOptions.map((entry, index) =>
        `<option value="${index}">${escapeHtml(entry.label)}</option>`
    ).join('');
};

window.addCatalogConsumableComponent = function() {
    const modal = document.getElementById('create-inventory-item-modal');
    const templateId = Number(document.getElementById('consumable-profile-source').value);
    const source = (modal._profileSources || []).find(entry => entry.id === templateId);
    const component = modal._profileComponentOptions?.[Number(document.getElementById('consumable-profile-component').value)];
    if (!source || !component) return;
    modal._advancedProfile = copyProfileComponent(modal._advancedProfile, source, component);
    if (component.section === 'whole' || (component.section === 'direct' && component.index === 'uses')) {
        const uses = Number(modal._advancedProfile.direct?.uses ?? source.attributes?.uses);
        if (Number.isInteger(uses) && uses > 0) document.getElementById('consumable-uses').value = uses;
    }
    renderConsumableProfileTree(document.getElementById('consumable-advanced-fields'), modal._advancedProfile);
};

export function renderInventoryTemplatePicker(templates, query = '', options = {}) {
    const normalizedQuery = query.trim().toLowerCase();
    const availableTemplates = templates.filter(t => {
        const isGenericMagazine = t.category === 'magazine'
            && /^магазин$/i.test(String(t.name || '').trim())
            && !t.attributes?.caliber
            && !t.attributes?.capacity;
        return !isGenericMagazine;
    });
    const filtered = normalizedQuery
        ? availableTemplates.filter(t => {
            const haystack = [
                t.name,
                t.categoryDisplay || getCategoryDisplay(t.category),
                t.subcategory,
                t.item_class,
                t.description,
                t.attributes?.caliber,
                t.attributes?.ammo_group,
                t.attributes?.ammo_kind,
                t.attributes?.purchase_category
            ].filter(Boolean).join(' ').toLowerCase();
            return haystack.includes(normalizedQuery);
        })
        : availableTemplates;

    if (!filtered.length) {
        return '<div style="padding:12px; opacity:0.75;">Ничего не найдено</div>';
    }

    const grouped = {};
    filtered.forEach(t => {
        const group = t.categoryDisplay || getCategoryDisplay(t.category) || 'Прочее';
        if (!grouped[group]) grouped[group] = [];
        grouped[group].push(t);
    });

    const renderCards = (items) => `
        <div style="display:grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap:8px; margin-top:10px;">
            ${items.map(t => {
                const attrs = t.attributes || {};
                const chips = [];
                if (attrs.caliber) chips.push(`Калибр: ${escapeHtml(String(attrs.caliber))}`);
                if (attrs.damage !== undefined && attrs.damage !== null && t.category === 'ammo') chips.push(`Урон: ${escapeHtml(String(attrs.damage))}`);
                if (attrs.penetration !== undefined && attrs.penetration !== null && t.category === 'ammo') chips.push(`Пробитие: ${escapeHtml(formatAmmoPenetration(attrs.penetration))}`);
                if (attrs.range !== undefined && attrs.range !== null && t.category === 'ammo') chips.push(`Дальность: ${escapeHtml(String(attrs.range))}`);
                if (attrs.capacity !== undefined && attrs.capacity !== null && t.category === 'magazine') chips.push(`Ёмкость: ${escapeHtml(String(attrs.capacity))}`);
                if (t.category === 'ammo' && !options.purchase) {
                    const ammoVariants = attrs.ammo_variants?.length ? attrs.ammo_variants : (attrs.ammo_variant ? [attrs.ammo_variant] : []);
                    if (ammoVariants.length) chips.push(`Вариации: ${escapeHtml(getAmmoVariantLabels(ammoVariants))}`);
                }
                if (attrs.isLoader && t.category === 'magazine') chips.push('Спидлоадер');
                return `
                    <button type="button" data-item-template-id="${t.id}" ${options.purchase ? `data-purchase-select="${t.id}" aria-pressed="${Number(options.selectedId)===t.id}"` : `onclick="selectInventoryTemplate(${t.id})"`} style="text-align:left; width:100%; border:1px solid rgba(255,255,255,0.12); border-radius:8px; padding:10px; background: rgba(255,255,255,0.03); color:inherit; cursor:pointer;">
                        <div style="display:flex; justify-content:space-between; gap:8px;">
                            <div style="font-weight:600;">${escapeHtml(t.name)}</div>
                            <div style="opacity:0.75; font-size:12px; white-space:nowrap;">${options.purchase ? `${Number(t.price||0).toLocaleString('ru-RU')} ₽` : `ID ${t.id}`}</div>
                        </div>
                        <div style="display:flex; flex-wrap:wrap; gap:5px; margin-top:6px;">
                            ${chips.map(chip => `<span style="font-size:11px; padding:2px 6px; border-radius:999px; background: rgba(255,255,255,0.08);">${chip}</span>`).join('')}
                        </div>
                    </button>
                    ${!options.purchase && t.source === 'local' && t.category === 'consumable' && window.isGM
                        ? `<button type="button" class="btn btn-sm btn-secondary" style="margin-top:4px" onclick="editInventoryConsumableTemplate(${t.id})">Настроить эффекты</button>` : ''}`;
            }).join('')}
        </div>`;

    const order = Object.keys(grouped).sort((a, b) => compareByFixedOrder(a, b, ITEM_CATEGORY_ORDER));
    return order.map(group => {
        const items = grouped[group].sort(compareTemplatesBySourceOrder);
        let contents = renderCards(items);
        if (group === 'Расходники') {
            const sections = {};
            items.forEach((template) => {
                const rawSection = String(template.attributes?.section || template.subcategory || 'Прочее').trim();
                const section = rawSection === 'fire_source' ? 'Прочее' : rawSection;
                if (!sections[section]) sections[section] = [];
                sections[section].push(template);
            });
            contents = Object.keys(sections)
                .sort((a, b) => compareByFixedOrder(a, b, CONSUMABLE_SECTION_ORDER))
                .map(section => `
                    <details ${normalizedQuery ? 'open' : ''} style="border-left:2px solid rgba(174,165,120,.45); padding:6px 8px; margin-top:8px; background:rgba(255,255,255,.025);">
                        <summary style="cursor:pointer; font-weight:600; display:flex; justify-content:space-between; gap:10px;">
                            <span>${escapeHtml(section)}</span><span style="opacity:.6; font-weight:400;">${sections[section].length}</span>
                        </summary>
                        ${renderCards(sections[section].sort(compareTemplatesBySourceOrder))}
                    </details>`).join('');
        }
        return `
            <details ${normalizedQuery || options.open ? 'open' : ''} style="border:1px solid rgba(255,255,255,0.12); border-radius:10px; padding:8px 10px; background: rgba(0,0,0,0.12); margin-bottom:8px;">
                <summary style="cursor:pointer; font-weight:700; list-style:none; display:flex; align-items:center; justify-content:space-between; gap:12px;">
                    <span>${escapeHtml(group)}</span>
                    <span style="opacity:0.65; font-weight:400;">${items.length}</span>
                </summary>
                ${contents}
            </details>`;
    }).join('');
}

function getInventoryTargetPath(target = 'pockets') {
    if (Array.isArray(target)) return [...target];
    return target === 'backpack'
        ? ['inventory', 'backpack']
        : ['inventory', 'pockets'];
}

function getInventoryTargetItems(targetPath) {
    if (targetPath.length === 2 && targetPath[0] === 'inventory') {
        if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
        if (!Array.isArray(currentCharacterData.inventory[targetPath[1]])) {
            currentCharacterData.inventory[targetPath[1]] = [];
        }
    }
    const target = getItemByPath(targetPath);
    if (Array.isArray(target)) return target;
    if (target && typeof target === 'object') {
        if (!Array.isArray(target.contents)) target.contents = [];
        return target.contents;
    }
    return null;
}

async function addTemplateItemToInventory(templateId, target, quantity = 1, ammoVariant = null, itemAttributes = null) {
    if (!templateId) return false;

    const allTemplates = await getAllItemTemplates();
    const template = allTemplates.find(t => t.id == templateId);
    if (!template) return false;
    if (!window.isGM && template.source === 'local') {
        showNotification('Игроки могут добавлять только глобальные предметы');
        return false;
    }

    const targetPath = getInventoryTargetPath(target);
    const targetItems = getInventoryTargetItems(targetPath);
    if (!targetItems) {
        showNotification('Контейнер для предмета не найден');
        return false;
    }

    const newItem = createItemFromTemplateSelection(template, quantity, ammoVariant, {
        createdByPlayer: !window.isGM,
    });
    if (itemAttributes) newItem.attributes = { ...(newItem.attributes || {}), ...itemAttributes };
    targetItems.push(...splitAmmoItemIntoSlotStacks(newItem));

    await rerenderContainer(targetPath, null, { keepExpanded: true });
    recalculateInventoryTotals();
    scheduleAutoSave();
    return true;
}

window.openInventoryTemplatePicker = async function(target = 'pockets', options = {}) {
    let modal = document.getElementById('inventory-template-picker-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'inventory-template-picker-modal';
        modal.className = 'modal';
        modal.style.zIndex = '1300';
        modal.innerHTML = `
            <div class="modal-content" style="max-width: 980px; width: 92vw; max-height: 85vh; overflow-y:auto;">
                <span class="close" onclick="closeInventoryTemplatePicker()">&times;</span>
                <h3 class="inventory-template-picker-title">Добавить предмет в инвентарь</h3>
                <div class="form-group" style="position: sticky; top: 0; z-index: 3; padding-top: 8px; padding-bottom: 8px; background: linear-gradient(180deg, rgba(0,0,0,0.96), rgba(0,0,0,0.86)); backdrop-filter: blur(6px);">
                    <label>Поиск</label>
                    <input type="text" id="inventory-template-picker-search" class="form-control" placeholder="Название, калибр, категория...">
                </div>
                <div id="inventory-template-picker-content"></div>
            </div>`;
        document.body.appendChild(modal);
    }

    modal._inventoryTarget = getInventoryTargetPath(target);
    modal._templateSelectionHandler = typeof options.onSelect === 'function'
        ? options.onSelect
        : null;
    const title = modal.querySelector('.inventory-template-picker-title');
    if (title) title.textContent = options.title || 'Добавить предмет в инвентарь';
    modal.style.display = 'flex';
    bindBackdropClose(modal, closeInventoryTemplatePicker);

    if (!document._inventoryTemplatePickerEscBound) {
        document.addEventListener('keydown', (e) => {
            const picker = document.getElementById('inventory-template-picker-modal');
            if (e.key === 'Escape' && picker && picker.style.display !== 'none') {
                closeInventoryTemplatePicker();
            }
        });
        document._inventoryTemplatePickerEscBound = true;
    }

    const searchInput = modal.querySelector('#inventory-template-picker-search');
    const content = modal.querySelector('#inventory-template-picker-content');
    searchInput.value = '';
    content.innerHTML = 'Загрузка...';

    const render = async () => {
        const templates = (await getAllItemTemplates()).filter(t => window.isGM || t.source !== 'local');
        content.innerHTML = renderInventoryTemplatePicker(templates, searchInput.value);
    };

    if (!modal._pickerBound) {
        searchInput.addEventListener('input', () => render());
        modal._pickerBound = true;
    }

    await render();
    searchInput.focus();
};

window.selectInventoryTemplate = async function(templateId) {
    const allTemplates = (await getAllItemTemplates()).filter(t => window.isGM || t.source !== 'local');
    const template = allTemplates.find(t => t.id === templateId);
    if (!template) return;
    const picker = document.getElementById('inventory-template-picker-modal');
    const target = picker?._inventoryTarget || getInventoryTargetPath('pockets');
    const selectionHandler = picker?._templateSelectionHandler || null;
    if (template.category === 'ammo') {
        closeInventoryTemplatePicker();
        openAmmoSelectionModal(templateId, target, selectionHandler);
        return;
    }

    if (template.category === 'consumable' && isBloodPacketItem(template, template)) {
        closeInventoryTemplatePicker();
        const savePacket = async (bloodType, bloodTypeKnown) => {
            const attributes = { bloodType, bloodTypeKnown };
            if (selectionHandler) {
                const item = createItemFromTemplateSelection(template, 1, null, {
                    createdByPlayer: !window.isGM,
                });
                item.attributes = { ...(item.attributes || {}), ...attributes };
                await selectionHandler(item, template);
            } else {
                await addTemplateItemToInventory(templateId, target, 1, null, attributes);
            }
        };
        if (!window.isGM) {
            await savePacket(1 + Math.floor(Math.random() * 4), false);
            return;
        }
        const modal = document.createElement('div');
        modal.className = 'modal';
        modal.style.display = 'flex';
        modal.innerHTML = `<div class="modal-content" style="max-width:390px;">
            <h3>Пакет крови</h3>
            <label>Фактическая группа крови
                <select class="form-control blood-packet-type">
                    <option value="1">I</option><option value="2">II</option>
                    <option value="3">III</option><option value="4">IV</option>
                </select>
            </label>
            <label style="display:flex;gap:8px;align-items:center;margin:12px 0;">
                <input type="checkbox" class="blood-packet-known"> Группа известна персонажам
            </label>
            <div class="form-actions">
                <button type="button" class="btn btn-primary blood-packet-save">Добавить</button>
                <button type="button" class="btn btn-secondary blood-packet-cancel">Отмена</button>
            </div>
        </div>`;
        document.body.appendChild(modal);
        modal.querySelector('.blood-packet-cancel').onclick = () => modal.remove();
        modal.querySelector('.blood-packet-save').onclick = async () => {
            const button = modal.querySelector('.blood-packet-save');
            button.disabled = true;
            try {
                await savePacket(
                    Number(modal.querySelector('.blood-packet-type').value),
                    modal.querySelector('.blood-packet-known').checked,
                );
                modal.remove();
            } catch (error) {
                button.disabled = false;
                showNotification(error.message || 'Не удалось добавить пакет крови');
            }
        };
        return;
    }

    if (selectionHandler) {
        await selectionHandler(createItemFromTemplateSelection(template, 1, null, {
            createdByPlayer: !window.isGM,
        }), template);
        return;
    }

    await addTemplateItemToInventory(templateId, target);
};

function renderAmmoSelectionModalContent(templates, initialTemplateId = null) {
    const grouped = {};
    templates.forEach(t => {
        const caliber = t.attributes?.caliber || t.subcategory || t.name || 'Без калибра';
        if (!grouped[caliber]) grouped[caliber] = [];
        grouped[caliber].push(t);
    });
    const calibers = Object.keys(grouped).sort((a, b) => a.localeCompare(b, 'ru'));
    const initialTemplate = templates.find(t => t.id === initialTemplateId) || templates[0] || null;
    const initialCaliber = initialTemplate ? (initialTemplate.attributes?.caliber || initialTemplate.subcategory || initialTemplate.name || calibers[0] || '') : (calibers[0] || '');

    return {
        calibers,
        grouped,
        initialTemplate,
        initialCaliber
    };
}

window.closeAmmoSelectionModal = function() {
    const modal = document.getElementById('ammo-selection-modal');
    if (modal) modal.style.display = 'none';
};

window.openAmmoSelectionModal = async function(initialTemplateId = null, target = 'pockets', selectionHandler = null) {
    let modal = document.getElementById('ammo-selection-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'ammo-selection-modal';
        modal.className = 'modal';
        modal.style.zIndex = '1310';
        modal.innerHTML = `
            <div class="modal-content" style="max-width: 900px; width: 92vw; max-height: 85vh; overflow-y:auto;">
                <span class="close" onclick="closeAmmoSelectionModal()">&times;</span>
                <h3>Добавить патроны</h3>
                <div class="form-group">
                    <label>Калибр</label>
                    <select id="ammo-selection-caliber" class="form-control"></select>
                </div>
                <div class="form-group">
                    <label>Вариант</label>
                    <select id="ammo-selection-variant" class="form-control"></select>
                </div>
                <div class="form-group">
                    <label>Количество</label>
                    <input type="number" id="ammo-selection-quantity" class="form-control number-input" min="1" value="1">
                </div>
                <div id="ammo-selection-preview" style="margin: 8px 0 12px; padding: 10px; border:1px solid rgba(255,255,255,0.12); border-radius:8px; background: rgba(0,0,0,0.12);"></div>
                <div class="form-actions">
                    <button class="btn btn-primary" onclick="confirmAmmoSelection()">Добавить</button>
                    <button class="btn btn-secondary" onclick="closeAmmoSelectionModal()">Отмена</button>
                </div>
            </div>`;
        document.body.appendChild(modal);
    }

    modal._inventoryTarget = getInventoryTargetPath(target);
    modal._templateSelectionHandler = typeof selectionHandler === 'function'
        ? selectionHandler
        : null;
    modal.style.display = 'flex';
    bindBackdropClose(modal, closeAmmoSelectionModal);

    if (!document._ammoSelectionEscBound) {
        document.addEventListener('keydown', (e) => {
            const picker = document.getElementById('ammo-selection-modal');
            if (e.key === 'Escape' && picker && picker.style.display !== 'none') {
                closeAmmoSelectionModal();
            }
        });
        document._ammoSelectionEscBound = true;
    }

    const allTemplates = await getAllItemTemplates(true);
    const ammoTemplates = allTemplates.filter(t => t.category === 'ammo');
    const { calibers, grouped, initialCaliber, initialTemplate } = renderAmmoSelectionModalContent(ammoTemplates, initialTemplateId);
    modal._ammoGrouped = grouped;
    modal._ammoTemplates = ammoTemplates;
    modal._selectedAmmoTemplateId = initialTemplate?.id || null;

    const caliberSelect = modal.querySelector('#ammo-selection-caliber');
    const variantSelect = modal.querySelector('#ammo-selection-variant');
    const preview = modal.querySelector('#ammo-selection-preview');
    const qtyInput = modal.querySelector('#ammo-selection-quantity');

    caliberSelect.innerHTML = calibers.map(caliber => {
        const count = grouped[caliber].length;
        return `<option value="${escapeHtml(caliber)}">${escapeHtml(caliber)} (${count})</option>`;
    }).join('');

    const getVariantsForTemplate = (template) => {
        const variants = normalizeAmmoVariants(template?.attributes?.ammo_variants);
        if (variants.length) return variants;
        const single = normalizeAmmoVariant(template?.attributes?.ammo_variant || template?.attributes?.ammo_kind || template?.attributes?.special_version || template?.attributes?.effect);
        return single ? [single] : [];
    };

    const renderVariantOptions = (template, preferredVariant = null) => {
        const variants = getVariantsForTemplate(template);
        const options = ['__base__', ...variants.filter(variant => variant !== '__base__')];
        variantSelect.innerHTML = options.map(variant => {
            const value = variant === '__base__' ? '' : variant;
            const label = variant === '__base__' ? 'Обычный' : getAmmoVariantLabel(variant);
            const isSelected = preferredVariant ? value === preferredVariant : false;
            return `<option value="${escapeHtml(value)}" ${isSelected ? 'selected' : ''}>${escapeHtml(label)}</option>`;
        }).join('');
        if (!variantSelect.value && options.length) {
            variantSelect.value = options[0] === '__base__' ? '' : options[0];
        }
    };

    const updatePreview = () => {
        const selectedId = modal._selectedAmmoTemplateId;
        const selected = ammoTemplates.find(t => t.id === selectedId);
        const variant = variantSelect.value ? getAmmoVariantLabel(variantSelect.value) : 'Обычный';
        if (!selected) {
            preview.innerHTML = '<span style="opacity:0.7;">Нет данных</span>';
            return;
        }
        const variantStats = getAmmoVariantStats(selected.attributes?.damage || 0, selected.attributes?.penetration || 0, variantSelect.value);
        preview.innerHTML = `
            <div><strong>${escapeHtml(selected.name)}</strong></div>
            <div style="margin-top:4px; opacity:0.85;">Вариант: ${escapeHtml(variant)}</div>
            <div style="opacity:0.85;">Пробитие: ${escapeHtml(formatAmmoPenetration(variantStats.penetration / 100))}</div>
            <div style="opacity:0.85;">Урон: ${escapeHtml(String(variantStats.damage))}</div>
            <div style="opacity:0.85;">Дальность: ${escapeHtml(String(selected.attributes?.range ?? 0))}</div>
        `;
    };

    caliberSelect.onchange = () => {
        const options = grouped[caliberSelect.value] || [];
        const currentSelected = options.find(t => t.id === modal._selectedAmmoTemplateId) || options[0] || null;
        modal._selectedAmmoTemplateId = currentSelected ? currentSelected.id : null;
        renderVariantOptions(currentSelected);
        updatePreview();
    };
    variantSelect.onchange = updatePreview;

    const chosenCaliber = grouped[initialCaliber] ? initialCaliber : calibers[0];
    if (chosenCaliber) {
        caliberSelect.value = chosenCaliber;
        const selectedTemplate = (initialTemplate && getItemCaliber(initialTemplate) === normalizeCaliberText(chosenCaliber))
            ? initialTemplate
            : (grouped[chosenCaliber]?.[0] || null);
        modal._selectedAmmoTemplateId = selectedTemplate ? selectedTemplate.id : null;
        renderVariantOptions(selectedTemplate, normalizeAmmoVariant(initialTemplate?.attributes?.ammo_variant) || '');
    }
    updatePreview();

    modal._ammoTemplates = ammoTemplates;
    modal._ammoGrouped = grouped;
};

window.confirmAmmoSelection = async function() {
    const modal = document.getElementById('ammo-selection-modal');
    if (!modal) return;
    const variantSelect = modal.querySelector('#ammo-selection-variant');
    const qtyInput = modal.querySelector('#ammo-selection-quantity');
    const templateId = modal._selectedAmmoTemplateId;
    const quantity = Math.max(1, parseInt(qtyInput.value, 10) || 1);
    const chosenVariant = variantSelect?.value || null;
    const ammoTemplates = modal._ammoTemplates || [];
    const template = ammoTemplates.find(t => t.id === templateId);
    if (!template) {
        showNotification('Патрон не найден');
        return;
    }

    const ammoVariant = variantSelect
        ? (variantSelect.value || null)
        : normalizeAmmoVariant(template.attributes?.ammo_variant || template.attributes?.ammo_kind || template.attributes?.special_version || template.attributes?.effect);
    let added;
    if (modal._templateSelectionHandler) {
        const newItem = createItemFromTemplateSelection(template, quantity, ammoVariant, {
            createdByPlayer: !window.isGM,
        });
        added = true;
        for (const stack of splitAmmoItemIntoSlotStacks(newItem)) {
            if (!await modal._templateSelectionHandler(stack, template)) {
                added = false;
                break;
            }
        }
    } else {
        added = await addTemplateItemToInventory(
            templateId, modal._inventoryTarget, quantity, ammoVariant
        );
    }
    if (added) closeAmmoSelectionModal();
};

window.addEffectToModal = function(type) {
    const container = document.getElementById(`${type}-effects-container`);
    const div = document.createElement('div');
    div.className = 'effect-item';
    div.style.display = 'flex';
    div.style.gap = '8px';
    div.style.marginBottom = '8px';
    div.style.alignItems = 'center';

    div.innerHTML = `
        <select class="form-control effect-category" style="width: 160px;">
            <optgroup label="Основные эффекты">
                <option value="Исцеление">Исцеление</option>
                <option value="Урон">Урон</option>
                <option value="Защита">Защита</option>
                <option value="Характеристика">Характеристика</option>
                <option value="Радиация">Радиация</option>
                <option value="Статус">Статус</option>
            </optgroup>
            <optgroup label="Другое">
                <option value="__custom__">✨ Свой</option>
            </optgroup>
        </select>
        <input type="text" class="form-control effect-value" placeholder="Значение (например, +5 HP)" style="flex: 2;">
        <button type="button" class="btn btn-sm btn-danger" onclick="this.parentElement.remove()">✕</button>
    `;
    container.appendChild(div);

    const categorySelect = div.querySelector('.effect-category');
    const valueInput = div.querySelector('.effect-value');

    categorySelect.addEventListener('change', function() {
        if (this.value === '__custom__') {
            const customInput = document.createElement('input');
            customInput.type = 'text';
            customInput.className = 'form-control effect-custom-category';
            customInput.placeholder = 'Введите тип эффекта';
            customInput.style.width = '160px';
            customInput.style.marginRight = '8px';
            this.parentNode.insertBefore(customInput, this);
            this.style.display = 'none';
        }
    });
};

window.itemCategoryChanged = function(select) {
    currentItemCategory = select.value;
    showItemCategoryFields();
};

window.editInventoryConsumableTemplate = async function(templateId) {
    const templates = await loadTemplatesForLobby('consumable');
    const template = templates.find(entry => entry.id === templateId && entry.source === 'local');
    if (!template) return showNotification('Шаблон расходника не найден');
    closeInventoryTemplatePicker();
    window.openCreateInventoryItemModal(template);
};

function readCustomConsumableProfile() {
    const profile = { editor_version: 1, direct: {}, modifiers: [], effects: [] };
    const seenTimedStats = new Set();
    const actionPoints = Number(document.getElementById('consumable-action-points').value);
    if (!Number.isFinite(actionPoints) || actionPoints < 0 || actionPoints > 1000) throw new Error('Проверьте стоимость в ОД');
    profile.direct.action_points_cost = actionPoints;
    for (const [index, row] of [...document.querySelectorAll('#consumable-effects-container .consumable-profile-row')].entries()) {
        const kind = row.querySelector('.profile-kind').value;
        const field = row.querySelector('.profile-field').value;
        const value = Number(row.querySelector('.profile-value').value);
        const duration = Number(row.querySelector('.profile-duration').value);
        const tick = row.querySelector('.profile-unit').value;
        if (!Number.isFinite(value) || value === 0 || !field) throw new Error('Значение эффекта должно отличаться от нуля');
        if (kind === 'immediate') {
            profile.direct[field] = (profile.direct[field] || 0) + value;
            continue;
        }
        if (!Number.isInteger(duration) || duration < 1 || duration > 10000) throw new Error('Проверьте длительность эффекта');
        if (kind === 'temporary') {
            if (seenTimedStats.has(field)) throw new Error('Для одной характеристики можно задать только один временный бонус');
            seenTimedStats.add(field);
            profile.modifiers.push({ stat: field, value, remaining: duration, tick });
            continue;
        }
        const bounds = { stress: 10, painLevel: 10, exhaustion: 10, psyState: 50, infection: 100, intoxication: 100 };
        profile.effects.push({
            type: 'delayed_adjustment', name: `Отложенное изменение: ${row.querySelector('.profile-field').selectedOptions[0].textContent}`,
            area: `editor-${index}`, remaining: duration, tick,
            ...(tick === 'time_elapsed' ? { time_unit: 'minute' } : {}),
            adjustments: [{ field, delta: value, min: 0, ...(bounds[field] ? { max: bounds[field] } : {}) }]
        });
    }
    if (Object.keys(profile.direct).length === 1 && !profile.modifiers.length && !profile.effects.length) {
        throw new Error('Добавьте хотя бы один эффект препарата');
    }
    return profile;
}

window.saveInventoryItemTemplate = async function() {
    const name = document.getElementById('item-name').value;
    if (!name) {
        showNotification('Введите название');
        return;
    }

    let category, attributes;
    if (currentItemCategory === 'consumable') {
        category = 'consumable';
        let profile;
        try {
            profile = document.getElementById('create-inventory-item-modal')._advancedProfile
                || readCustomConsumableProfile();
        }
        catch (error) { showNotification(error.message); return; }
        attributes = {
            ...(document.getElementById('create-inventory-item-modal')._editingTemplate?.attributes || {}),
            weight: parseFloat(document.getElementById('consumable-weight').value) || 0,
            volume: parseFloat(document.getElementById('consumable-volume').value) || 0,
            uses: parseInt(document.getElementById('consumable-uses').value) || 1,
            consumable: profile
        };
        if (profile.editor_version === 2 && profile.direct?.uses !== undefined) {
            attributes.uses = Number(profile.direct.uses);
        }
    } else if (currentItemCategory === 'material') {
        category = 'crafting_material';
        attributes = {
            weight: parseFloat(document.getElementById('material-weight').value) || 0,
            volume: parseFloat(document.getElementById('material-volume').value) || 0
        };
    } else if (currentItemCategory === 'artifact') {
        category = 'artifact';
        const effects = [];
        const items = document.querySelectorAll('#artifact-effects-container .effect-item');
        items.forEach(item => {
            let type = '';
            const categorySelect = item.querySelector('.effect-category');
            const customInput = item.querySelector('.effect-custom-category');
            if (customInput && customInput.value) {
                type = customInput.value;
            } else if (categorySelect && categorySelect.value !== '__custom__') {
                type = categorySelect.value;
            }
            const value = item.querySelector('.effect-value').value;
            if (type && value) {
                effects.push({ type, value });
            }
        });
        attributes = {
            weight: parseFloat(document.getElementById('artifact-weight').value) || 0,
            volume: parseFloat(document.getElementById('artifact-volume').value) || 0,
            effects: effects
        };
    }

    const editing = document.getElementById('create-inventory-item-modal')._editingTemplate;
    const data = {
        name: name,
        category: category,
        subcategory: editing?.subcategory ?? null,
        description: editing?.description ?? null,
        price: editing?.price ?? 0,
        weight: attributes.weight || 0,
        volume: attributes.volume || 0,
        attributes: attributes
    };

    try {
        if (editing) await Server.updateLobbyTemplate(currentLobbyId, editing.id - 1_000_000, data);
        else await Server.createLobbyTemplate(currentLobbyId, data);
        clearAllTemplatesCache();
        await renderInventoryTab(currentCharacterData);
        document.getElementById('create-inventory-item-modal').style.display = 'none';
        showNotification('Предмет создан', 'success');
    } catch (err) {
        showNotification(err.message);
    }
};

function recalculateInventoryTotals() {
    const inv = currentCharacterData.inventory || {};
    const eq = currentCharacterData.equipment || {};

    // Рюкзак – только его собственное содержимое
    const backpackItems = Array.isArray(inv.backpack) ? inv.backpack.map(item => migrateOldItemToNew(item)) : [];
    const equippedBackpack = eq.backpack || null;
    const backpackLimit = Number(equippedBackpack?.attributes?.limit || equippedBackpack?.attributes?.capacity || 0);
    const backpackWeightReduction = Number(equippedBackpack?.attributes?.weight_reduction || 0);

    let totalWeight = 0;
    let totalVolume = 0;

    // Вес и объём из рюкзака (с учётом вложенности)
    backpackItems.forEach(item => {
        totalWeight += getTotalWeight(item);
        totalVolume += getTotalVolume(item);
    });

    // Карманы – отдельно, не прибавляем к рюкзаку
    const pockets = Array.isArray(inv.pockets) ? inv.pockets.map(item => migrateOldItemToNew(item)) : [];
    pockets.forEach(item => {
        totalWeight += getTotalWeight(item);
        totalVolume += getTotalVolume(item);
    });

    // Подсумки пояса и разгрузки – их содержимое добавляем к общему весу/объёму,
    const beltPouches = eq.belt?.pouches || [];
    const vestPouches = eq.vest?.pouches || [];
    [...beltPouches, ...vestPouches].forEach(pouch => {
        if (pouch.contents) {
            pouch.contents.forEach(item => {
                totalWeight += getTotalWeight(item);
                totalVolume += getTotalVolume(item);
            });
        }
    });

    // Экипированное оружие (огнестрельное и ближнего боя)
    const weapons = currentCharacterData.weapons || [];
    weapons.forEach(weapon => {
        totalWeight += getTotalWeight(weapon);
        totalVolume += getTotalVolume(weapon);
    });
    for(const parts of Object.values(currentCharacterData.health?.prostheses || {})) {
        for(const item of Object.values(parts))totalWeight+=getTotalWeight(item);
    }

    // Обновляем отображение общего веса и объёма
    const totalWeightSpan = document.getElementById('total-weight-display');
    if (totalWeightSpan) totalWeightSpan.textContent = totalWeight.toFixed(1);

    // Обновляем заполненность рюкзака (только из его содержимого)
    const backpackFillSpan = document.getElementById('backpack-fill-display');
    if (backpackFillSpan) {
        const backpackVolume = backpackItems.reduce((sum, item) => {
            const vol = getTotalVolume(item);
            return sum + (isNaN(vol) ? 0 : vol);
        }, 0);
        backpackFillSpan.textContent = `Заполнено: ${backpackVolume.toFixed(1)} / ${backpackLimit}`;
    }

    const movementPenalty = getMovementPenaltyBreakdown(currentCharacterData, totalWeight);
    const movePenaltySpan = document.getElementById('move-penalty-display');
    if (movePenaltySpan) movePenaltySpan.textContent = movementPenalty.total;
    const weightPenaltySpan = document.getElementById('weight-penalty-display');
    if (weightPenaltySpan) weightPenaltySpan.textContent = movementPenalty.weightPenalty;
    const weightStepSpan = document.getElementById('move-penalty-weight-step');
    if (weightStepSpan) weightStepSpan.textContent = movementPenalty.weightPerPenalty.toFixed(1);
    const sourceValues = {
        'weight-penalty-raw': movementPenalty.rawWeightPenalty,
        'weight-penalty-backpack': `−${movementPenalty.backpackReduction}`,
        'weight-penalty-source': movementPenalty.weightPenalty,
        'armor-penalty-source': movementPenalty.armorPenalty,
        'helmet-penalty-source': movementPenalty.helmetPenalty,
        'injury-penalty-source': movementPenalty.injuries,
        'intoxication-penalty-source': movementPenalty.intoxicationPenalty,
        'temporary-penalty-source': movementPenalty.temporary,
    };
    Object.entries(sourceValues).forEach(([id, value]) => {
        const element = document.getElementById(id);
        if (element) element.textContent = value;
    });
    const exoskeletonRule = document.getElementById('exoskeleton-weight-rule');
    if (exoskeletonRule) exoskeletonRule.style.display = movementPenalty.poweredExoskeleton ? 'block' : 'none';

    // Заполненность карманов
    const pocketMaxVolume = pocketCapacity(currentCharacterData);
    const pocketFill = pockets.reduce((sum, item) => {
        const vol = getTotalVolume(item);
        return sum + (isNaN(vol) ? 0 : vol);
    }, 0);
    const pocketFillSpan = document.getElementById('pocket-fill-display');
    if (pocketFillSpan) pocketFillSpan.textContent = pocketFill.toFixed(1);
    const pocketCapacitySpan = document.getElementById('pocket-capacity-display');
    if (pocketCapacitySpan) pocketCapacitySpan.textContent = pocketMaxVolume;
}

window.selectPocketItem = async function(index, selectedId) {
    const id = parseInt(selectedId, 10);
    if (isNaN(id)) return;

    const allTemplates = await getAllItemTemplates();
    const template = allTemplates.find(t => t.id === id);
    if (!template) return;

    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!Array.isArray(currentCharacterData.inventory.pockets)) {
        currentCharacterData.inventory.pockets = [];
    }

    const newItem = createItemFromTemplate(template);
    currentCharacterData.inventory.pockets[index] = newItem;

    await renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
};

window.openCreateBackpackTemplateModal = function(template = null) {
    let modal = document.getElementById('create-backpack-template-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'create-backpack-template-modal';
        modal.className = 'modal';
        modal.innerHTML = `
            <div class="modal-content" style="max-height: 80vh; overflow-y: auto;">
                <span class="close" onclick="document.getElementById('create-backpack-template-modal').style.display='none'">&times;</span>
                <h3>${template ? 'Редактировать' : 'Создать'} шаблон рюкзака</h3>
                <input type="hidden" id="backpack-template-id">
                <div class="form-group"><label>Название</label><input type="text" id="backpack-name" class="form-control"></div>
                <div class="form-group"><label>Объём (лимит)</label><input type="number" id="backpack-limit" class="form-control number-input" value="0"></div>
                <div class="form-group"><label>Снижение штрафа веса</label><input type="number" id="backpack-weightReduction" class="form-control number-input" value="0"></div>
                <div class="form-group"><label>Собственный вес</label><input type="number" id="backpack-self-weight" class="form-control number-input" value="0" step="0.1"></div>
                <div class="form-group"><label>Собственный объём</label><input type="number" id="backpack-self-volume" class="form-control number-input" value="0" step="0.1"></div>
                <div class="form-actions"><button class="btn btn-primary" onclick="saveBackpackTemplate()">Сохранить</button><button class="btn btn-secondary" onclick="document.getElementById('create-backpack-template-modal').style.display='none'">Отмена</button></div>
            </div>`;
        document.body.appendChild(modal);
    }
    if (template) {
        document.getElementById('backpack-template-id').value = template.id;
        document.getElementById('backpack-name').value = template.name || '';
        document.getElementById('backpack-limit').value = template.attributes?.limit || 0;
        document.getElementById('backpack-weightReduction').value = template.attributes?.weight_reduction || 0;
        document.getElementById('backpack-self-weight').value = template.weight || 0;
        document.getElementById('backpack-self-volume').value = template.volume || 0;
    } else {
        document.getElementById('backpack-template-id').value = '';
    }
    modal.style.display = 'flex';
};

window.saveBackpackTemplate = async function() {
    const id = document.getElementById('backpack-template-id').value;
    const name = document.getElementById('backpack-name').value.trim();
    if (!name) { showNotification('Введите название'); return; }
    const attributes = {
        limit: parseInt(document.getElementById('backpack-limit').value) || 0,
        weight_reduction: parseInt(document.getElementById('backpack-weightReduction').value) || 0
    };
    const data = {
        name, category: 'backpack', subcategory: null, price: 0,
        weight: parseFloat(document.getElementById('backpack-self-weight').value) || 0,
        volume: parseFloat(document.getElementById('backpack-self-volume').value) || 0,
        attributes
    };
    try {
        if (id) await Server.updateLobbyTemplate(currentLobbyId, id, data);
        else await Server.createLobbyTemplate(currentLobbyId, data);
        clearAllTemplatesCache();
        document.getElementById('create-backpack-template-modal').style.display = 'none';
        showNotification(id ? 'Шаблон обновлён' : 'Шаблон создан', 'success');
        if (currentCharacterData) await renderInventoryTab(currentCharacterData);
        if (typeof loadTemplatesForManager === 'function') {
            const active = document.querySelector('#templates-modal .tab-btn.active')?.dataset.cat;
            if (active === 'backpack') loadTemplatesForManager('backpack');
        }
    } catch (e) { showNotification(e.message); }
};

function renderBackpackNew(items, groupedByCategory, allTemplates) {
    const container = document.getElementById('backpack-container');
    if (!container) return;
    container.innerHTML = '';

    items.forEach((item, index) => {
        renderBackpackItem(item, index, ['inventory', 'backpack'], container, allTemplates);
    });
}

function renderBackpackItem(item, index, parentPath, parentContainer, allTemplates) {
    const itemDiv = document.createElement('div');
    itemDiv.dataset.itemTooltip = 'true';
    if (item.templateId) itemDiv.dataset.itemTemplateId = String(item.templateId);
    itemDiv.draggable = true;
    itemDiv.style.marginBottom = '5px';
    itemDiv.style.padding = '5px';
    itemDiv.style.border = '1px solid #444';
    itemDiv.style.borderRadius = '4px';
    itemDiv.style.backgroundColor = 'rgba(0,0,0,0.2)';

    const itemPath = parentPath.concat(index);
    itemDiv.dataset.path = itemPath.join(',');
    itemDiv.dataset.widgetIdentity = JSON.stringify(item.id != null ? { id: item.id } : { templateId: item.templateId, name: item.name, category: item.category });

    // Если предмет — контейнер, делаем его drop-целью (закрытый контейнер)
    const isArtifactCont = isArtifactContainer(item);
    if (item.isContainer && !isArtifactCont) {
        setupDropTarget(itemDiv, itemPath.concat('contents'), item);
    }

    const row = document.createElement('div');
    row.style.display = 'grid';
    row.style.gridTemplateColumns = '2fr 1fr 1fr 1fr auto';
    row.style.gap = '5px';
    row.style.alignItems = 'center';

    // --- Ячейка названия (drag zone) ---
    let nameCell;
    if (item.templateId) {
        nameCell = document.createElement('strong');
        nameCell.textContent = item.name;
    } else {
        nameCell = document.createElement('input');
        nameCell.dataset.widgetKey = 'name';
        nameCell.type = 'text';
        nameCell.className = 'form-control';
        nameCell.value = item.name || '';
        nameCell.placeholder = 'Название';
        nameCell.onchange = (e) => updateBackpackItemAtPath(itemPath.join(','), 'name', e.target.value);
    }

    const nameWrapper = document.createElement('div');
    nameWrapper.style.display = 'flex';
    nameWrapper.style.alignItems = 'center';

    if (item.isContainer && !isArtifactCont) {
        const toggleIcon = document.createElement('span');
        toggleIcon.textContent = '▶';
        toggleIcon.style.cursor = 'pointer';
        toggleIcon.style.marginRight = '5px';
        toggleIcon.style.userSelect = 'none';
        nameWrapper.appendChild(toggleIcon);
        itemDiv._toggleIcon = toggleIcon;
    }
    nameWrapper.appendChild(nameCell);
    const isBloodPacket = isBloodPacketItem(item);
    if (isBloodPacket) {
        const bloodBadge = document.createElement('span');
        bloodBadge.className = 'inventory-blood-type';
        const knownType = item.attributes?.bloodTypeKnown
            ? Number(item.attributes?.bloodType || item.attributes?.blood_type || 0) : 0;
        bloodBadge.textContent = knownType ? formatBloodType(knownType) : 'Группа неизвестна';
        nameWrapper.appendChild(bloodBadge);
    }
    if (item.createdByPlayer) {
        const createdBadge = document.createElement('span');
        createdBadge.className = 'created-by-player-badge';
        createdBadge.title = 'Предмет добавлен игроком';
        createdBadge.textContent = 'Создано игроком';
        createdBadge.style.marginLeft = '8px';
        createdBadge.style.padding = '2px 6px';
        createdBadge.style.borderRadius = '4px';
        createdBadge.style.fontSize = '11px';
        createdBadge.style.color = '#d8c58a';
        createdBadge.style.border = '1px solid rgba(216,197,138,.45)';
        nameWrapper.appendChild(createdBadge);
    }

    // ОБЁРТКА ДЛЯ ПЕРЕТАСКИВАНИЯ
    const nameDragZone = document.createElement('span');
    nameDragZone.className = 'item-name';
    nameDragZone.style.display = 'flex';
    nameDragZone.style.alignItems = 'center';
    nameDragZone.style.cursor = 'grab';
    nameDragZone.draggable = true;

    itemDiv.addEventListener('dragstart', (e) => {
        if (e.target.closest('button, input, select, textarea')) {
            e.preventDefault();
            e.stopPropagation();
            return false;
        }
        draggedItem = item;
        draggedItemPath = itemPath;
        e.dataTransfer.setData('text/plain', itemPath.join(','));
        e.dataTransfer.setData('application/x-inventory-path', JSON.stringify(itemPath));
        e.dataTransfer.effectAllowed = 'move';
        itemDiv.classList.add('dragging');
        e.stopPropagation();
    });
    itemDiv.addEventListener('dragend', () => {
        itemDiv.classList.remove('dragging');
        document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
        draggedItem = null;
        draggedItemPath = null;
    });

    nameDragZone.appendChild(nameWrapper);

    // Кнопка свойств (ⓘ) – добавляем в ту же зону, но она не вызовет drag
    const hasProt = item.durability !== null && item.durability !== undefined;
    const hasMods = item.modifications && item.modifications.length > 0;
    const hasEffects = item.attributes?.effects && item.attributes.effects.length > 0;
    const hasMagazineDetails = item.category === 'magazine' && item.ammo?.length;
    if (hasProt || hasMods || hasEffects || hasMagazineDetails || isBloodPacket) {
        const infoBtn = document.createElement('button');
        infoBtn.type = 'button';
        infoBtn.className = 'btn btn-sm btn-secondary';
        infoBtn.textContent = 'ⓘ';
        infoBtn.title = 'Свойства';
        infoBtn.style.marginLeft = '5px';
        infoBtn.style.padding = '2px 6px';
        infoBtn.style.fontSize = '0.8rem';
        infoBtn.onclick = (e) => {
            e.stopPropagation();
            showItemDetailsModal(item);
        };
        nameDragZone.appendChild(infoBtn);
    }

    const usesLeft = Number.isFinite(Number(item.uses)) ? Number(item.uses) : Number.isFinite(Number(item.attributes?.uses_remaining)) ? Number(item.attributes.uses_remaining) : null;
    const maxUses = Number.isFinite(Number(item.maxUses)) ? Number(item.maxUses) : Number.isFinite(Number(item.attributes?.uses)) ? Number(item.attributes.uses) : null;
    if (usesLeft !== null && maxUses !== null && maxUses > 0) {
        const usesBadge = document.createElement('span');
        usesBadge.style.cssText = 'margin-left:8px; padding:2px 8px; border-radius:999px; font-size:11px; background:rgba(255,255,255,0.08); color:#d7e7ff; white-space:nowrap;';
        usesBadge.textContent = `Исп.: ${Math.max(0, usesLeft)}/${maxUses}`;
        nameDragZone.appendChild(usesBadge);
    }

    row.appendChild(nameDragZone);

    // --- Вес ---
    const weightInput = document.createElement('input');
    weightInput.dataset.widgetKey = 'weight';
    weightInput.type = 'number';
    weightInput.step = 'any';
    weightInput.className = 'form-control number-input';
    weightInput.value = item.weight || 0;
    weightInput.onchange = (e) => updateBackpackItemAtPath(itemPath.join(','), 'weight', e.target.value);

    // --- Объём ---
    const volumeInput = document.createElement('input');
    volumeInput.dataset.widgetKey = 'volume';
    volumeInput.type = 'number';
    volumeInput.step = 'any';
    volumeInput.className = 'form-control number-input';
    volumeInput.value = item.volume || 0;
    volumeInput.onchange = (e) => updateBackpackItemAtPath(itemPath.join(','), 'volume', e.target.value);

    // --- Количество ---
    const qtyCell = document.createElement('div');
    qtyCell.style.display = 'flex';
    qtyCell.style.alignItems = 'center';
    qtyCell.style.gap = '4px';

    const qtyDownBtn = document.createElement('button');
    qtyDownBtn.dataset.widgetKey = 'quantity-down';
    qtyDownBtn.type = 'button';
    qtyDownBtn.className = 'btn btn-sm btn-secondary';
    qtyDownBtn.textContent = '−';
    qtyDownBtn.title = 'Уменьшить количество';
    qtyDownBtn.style.width = '28px';
    qtyDownBtn.style.height = '28px';
    qtyDownBtn.style.padding = '0';
    qtyDownBtn.style.lineHeight = '1';
    qtyDownBtn.onclick = (e) => {
        e.stopPropagation();
        return window.adjustBackpackItemQuantityAtPath(itemPath.join(','), -1);
    };

    const qtyInput = document.createElement('input');
    qtyInput.dataset.widgetKey = 'quantity';
    qtyInput.type = 'number';
    qtyInput.className = 'form-control number-input';
    qtyInput.style.minWidth = '54px';
    qtyInput.value = item.quantity || 1;
    qtyInput.setAttribute('data-field', 'quantity');
    qtyInput.onchange = (e) => updateBackpackItemAtPath(itemPath.join(','), 'quantity', e.target.value);

    const qtyUpBtn = document.createElement('button');
    qtyUpBtn.dataset.widgetKey = 'quantity-up';
    qtyUpBtn.type = 'button';
    qtyUpBtn.className = 'btn btn-sm btn-secondary';
    qtyUpBtn.textContent = '+';
    qtyUpBtn.title = 'Увеличить количество';
    qtyUpBtn.style.width = '28px';
    qtyUpBtn.style.height = '28px';
    qtyUpBtn.style.padding = '0';
    qtyUpBtn.style.lineHeight = '1';
    qtyUpBtn.onclick = (e) => {
        e.stopPropagation();
        return window.adjustBackpackItemQuantityAtPath(itemPath.join(','), 1);
    };

    // Отключаем временно draggable у родителя при взаимодействии с инпутами (чтобы не мешать редактированию)
    const disableDragForInput = (input) => {
        if (!input) return;
        input.addEventListener('mousedown', (e) => {
            e.stopPropagation();
            itemDiv.draggable = false;
        });
        input.addEventListener('mouseup', () => { itemDiv.draggable = true; });
        input.addEventListener('mouseleave', () => { itemDiv.draggable = true; });
    };
    disableDragForInput(weightInput);
    disableDragForInput(volumeInput);
    disableDragForInput(qtyInput);
    if (nameCell.tagName === 'INPUT') disableDragForInput(nameCell);

    // --- Кнопки действий ---
    const actionsDiv = document.createElement('div');
    actionsDiv.className = 'inventory-item-actions';
    actionsDiv.style.display = 'flex';
    actionsDiv.style.gap = '5px';
    actionsDiv.style.alignItems = 'center';
    actionsDiv.style.justifyContent = 'flex-end';

    // Удаление
    const delBtn = document.createElement('button');
    delBtn.dataset.widgetKey = 'delete';
    delBtn.type = 'button';
    delBtn.className = 'btn btn-sm btn-danger';
    delBtn.textContent = '✕';
    delBtn.style.width = '28px';
    delBtn.style.height = '28px';
    delBtn.style.padding = '0';
    delBtn.style.fontSize = '14px';
    delBtn.style.lineHeight = '1';
    delBtn.onclick = () => removeBackpackItemAtPath(itemPath.join(','));
    actionsDiv.appendChild(delBtn);
    const dropBtn = document.createElement('button');
    dropBtn.dataset.widgetKey = 'drop';
    dropBtn.type = 'button';
    dropBtn.className = 'btn btn-sm btn-warning';
    dropBtn.textContent = '⇩';
    dropBtn.title = 'Бросить на пол';
    dropBtn.style.width = '28px';
    dropBtn.style.height = '28px';
    dropBtn.style.padding = '0';
    dropBtn.style.fontSize = '14px';
    dropBtn.style.lineHeight = '1';
    dropBtn.onclick = async (e) => {
        e.stopPropagation();
        await window.dropBackpackItemAtPath(itemPath.join(','));
    };
    actionsDiv.appendChild(dropBtn);

    // Надеть
    const equippableCategories = ['armor', 'helmet', 'gas_mask', 'weapon', 'belt', 'vest', 'backpack', 'detector', 'melee_weapon',
    'device', 'headphones', 'glasses', 'gloves', 'jewelry'];
    if (item.isEquippable || equippableCategories.includes(item.category)) {
        const equipBtn = document.createElement('button');
        equipBtn.dataset.widgetKey = 'equip';
        equipBtn.type = 'button';
        equipBtn.className = 'btn btn-sm btn-primary';
        equipBtn.textContent = '↑';
        equipBtn.title = 'Надеть';
        equipBtn.style.width = '28px';
        equipBtn.style.height = '28px';
        equipBtn.style.padding = '0';
        equipBtn.style.fontSize = '14px';
        equipBtn.style.lineHeight = '1';
        equipBtn.onclick = (e) => {
            e.stopPropagation();
            const category = item.category;
            if (category === 'armor') return equipArmorFromInventory(itemPath);
            else if (category === 'helmet') return equipHelmetFromInventory(itemPath);
            else if (category === 'gas_mask') return equipGasMaskFromInventory(itemPath);
            else if (category === 'weapon') return equipWeaponFromInventory(itemPath);
            else if (category === 'belt') return equipBeltFromInventory(itemPath);
            else if (category === 'vest') return equipVestFromInventory(itemPath);
            else if (category === 'backpack') return equipBackpackFromInventory(itemPath);
            else if (category === 'device' || category === 'detector') return equipDetectorFromInventory(itemPath);
            else if (category === 'melee_weapon') return equipMeleeWeaponFromInventory(itemPath);
            else if (category === 'headphones') return equipHeadphonesFromInventory(itemPath);
            else if (category === 'glasses') return equipGlassesFromInventory(itemPath);
            else if (category === 'gloves') return equipGlovesFromInventory(itemPath);
            else if (category === 'jewelry') {
                const sub = item.subcategory;
                if (sub === 'ring') return equipRingFromInventory(itemPath);
                else if (sub === 'necklace' || sub === 'amulet') return equipNecklaceFromInventory(itemPath);
                else if (sub === 'earrings') return equipEarringsFromInventory(itemPath);
                else if (sub === 'bracelet') {
                    const eq = currentCharacterData.equipment || {};
                    if (!eq.bracelet1?.templateId) return equipBraceletFromInventory(itemPath, 1);
                    else if (!eq.bracelet2?.templateId) return equipBraceletFromInventory(itemPath, 2);
                    else showNotification('Оба слота браслетов заняты');
                }
                else showNotification('Неизвестный тип бижутерии');
            }
        };
        actionsDiv.appendChild(equipBtn);
    }

    // На пояс
    if (item.category === 'helmet' || item.category === 'gas_mask') {
        const beltBtn = document.createElement('button');
        beltBtn.dataset.widgetKey = 'belt';
        beltBtn.type = 'button';
        beltBtn.className = 'btn btn-sm btn-secondary';
        beltBtn.textContent = '↓';
        beltBtn.title = 'Поместить на пояс';
        beltBtn.style.width = '28px';
        beltBtn.style.height = '28px';
        beltBtn.style.padding = '0';
        beltBtn.style.fontSize = '14px';
        beltBtn.style.lineHeight = '1';
        beltBtn.onclick = (e) => {
            e.stopPropagation();
            equipToBeltFromInventory(itemPath);
        };
        actionsDiv.appendChild(beltBtn);
    }

    // Использовать
    const usableCategories = ['consumable', 'grenade', 'device'];
    const isBattery = (item.category === 'device' && item.subcategory === 'battery');
    if ((usableCategories.includes(item.category) || item.attributes?.usable) && !isBattery) {
        const useBtn = document.createElement('button');
        useBtn.dataset.widgetKey = 'use';
        useBtn.type = 'button';
        useBtn.className = 'btn btn-sm btn-success';
        useBtn.textContent = '▶';
        useBtn.title = 'Использовать';
        useBtn.style.width = '28px';
        useBtn.style.height = '28px';
        useBtn.style.padding = '0';
        useBtn.style.fontSize = '14px';
        useBtn.style.lineHeight = '1';
        useBtn.onclick = async (e) => {
            e.stopPropagation();
            const currentEntry = item.id ? findInventoryItemById(currentCharacterData, item.id) : { item: getItemByPath(itemPath), path: itemPath };
            if (!currentEntry) {
                showNotification('Предмет больше не найден в инвентаре');
                return;
            }
            try {
                await useItem(currentEntry.item, currentEntry.path);
            } catch (error) {
                showNotification(error.message || 'Не удалось применить предмет', 'system');
            }
        };
        actionsDiv.appendChild(useBtn);
    }

    row.appendChild(weightInput);
    row.appendChild(volumeInput);
    qtyCell.append(qtyDownBtn, qtyInput, qtyUpBtn);
    row.appendChild(qtyCell);
    row.appendChild(actionsDiv);
    itemDiv.appendChild(row);

    // Слоты предмета
    if (getItemSlots(item).length > 0) {
        const slotsHtml = renderSlotsUniversal(item, itemPath, 1);
        if (slotsHtml) {
            const slotsDiv = document.createElement('div');
            slotsDiv.className = 'item-slots-container';
            slotsDiv.style.marginTop = '8px';
            slotsDiv.style.marginLeft = '20px';
            slotsDiv.innerHTML = slotsHtml;
            itemDiv.appendChild(slotsDiv);
        }
    }

    // Магазин
    if (item.category === 'magazine') {
        if (isAmmoFeederTool(item)) {
            const toolInfo = document.createElement('div');
            toolInfo.style.marginTop = '5px';
            toolInfo.style.opacity = '0.8';
            toolInfo.textContent = 'Инструмент зарядки магазинов';
            itemDiv.appendChild(toolInfo);
        } else {
        const cap = item.attributes?.capacity || 30;
        const total = item.ammo ? item.ammo.reduce((sum, a) => sum + a.quantity, 0) : 0;
        let ammoText = `Патроны: ${total}/${cap}`;
        if (item.ammo && item.ammo.length > 0) {
            ammoText += ` (${item.ammo.map(a => a.name).join(', ')})`;
        }
        const ammoControls = document.createElement('div');
        ammoControls.style.display = 'flex';
        ammoControls.style.alignItems = 'center';
        ammoControls.style.gap = '5px';
        ammoControls.style.marginTop = '5px';
        ammoControls.innerHTML = `
            <span style="min-width: 120px;">${ammoText}</span>
            <button type="button" class="btn btn-sm btn-secondary" onclick="changeMagazineAmmo('${itemPath.join(',')}', 1)">+1</button>
            <button type="button" class="btn btn-sm btn-secondary" onclick="changeMagazineAmmo('${itemPath.join(',')}', -1)">-1</button>
            <button type="button" class="btn btn-sm btn-primary" onclick="reloadMagazineFromInventory('${itemPath.join(',')}')">Зарядить</button>
            <button type="button" class="btn btn-sm btn-danger" onclick="unloadMagazineToInventory('${itemPath.join(',')}')">Разрядить</button>
        `;
        itemDiv.appendChild(ammoControls);
        }
    }

    // Содержимое контейнера
    if (item.isContainer && !isArtifactCont) {
        const template = allTemplates?.find(t => t.id === item.templateId);
        const hasArmorPlateSlot = template?.attributes?.slots?.some(s => s.type === 'armor_plate');
        if (!hasArmorPlateSlot) {
            const contentsDiv = document.createElement('div');
            contentsDiv.className = 'container-contents';
            contentsDiv.style.marginLeft = '25px';
            contentsDiv.style.marginTop = '8px';
            contentsDiv.style.paddingLeft = '10px';
            contentsDiv.style.borderLeft = '2px dashed #666';
            contentsDiv.style.display = 'none';
            contentsDiv.setAttribute('data-container-path', itemPath.concat('contents').join(','));

            if (item.contents && item.contents.length > 0) {
                item.contents.forEach((subItem, subIndex) => {
                    renderBackpackItem(subItem, subIndex, itemPath.concat('contents'), contentsDiv, allTemplates);
                });
            }

            if (window.isGM) {
                const addBtn = document.createElement('button');
                addBtn.type = 'button';
                addBtn.className = 'btn btn-sm btn-secondary';
                addBtn.textContent = '➕ Добавить внутрь';
                addBtn.onclick = () => openInventoryTemplatePicker(itemPath.concat('contents'));
                contentsDiv.appendChild(addBtn);
            }

            setupDropTarget(contentsDiv, itemPath.concat('contents'), item);
            itemDiv.appendChild(contentsDiv);
            itemDiv._contentsDiv = contentsDiv;

            const toggleIcon = itemDiv._toggleIcon;
            const containerKey = inventoryContainerKey(item, itemPath);
            setInventoryContainerOpen(contentsDiv, toggleIcon, containerKey, expandedInventoryContainers.has(containerKey));
            if (toggleIcon) {
                toggleIcon.onclick = () => setInventoryContainerOpen(
                    contentsDiv, toggleIcon, containerKey, contentsDiv.style.display === 'none',
                );
            }
        }
    }

    parentContainer.appendChild(itemDiv);
}

// Универсальное окно свойств предмета
function showItemDetailsModal(item) {
    if (!item) {
        console.warn('showItemDetailsModal: item is undefined');
        return;
    }

    let html = `<h3>${escapeHtml(item.name)}</h3>`;
    html += '<hr>';
    if (isBloodPacketItem(item)) {
        const knownType = item.attributes?.bloodTypeKnown
            ? Number(item.attributes?.bloodType || item.attributes?.blood_type || 0) : 0;
        html += `<p><strong>Группа крови:</strong> ${knownType ? escapeHtml(formatBloodType(knownType)) : 'Неизвестна'}</p>`;
    }

    // Прочность
    if (item.durability != null && item.maxDurability != null) {
        html += `<p><strong>Прочность:</strong> ${item.durability} / ${item.maxDurability}</p>`;
    }

    // Защита
    if (item.protection) {
        const prot = item.protection;
        html += '<p><strong>Защита:</strong> ';
        html += `Физ: ${formatProtectionPercent(prot.physical)}, Хим: ${formatProtectionPercent(prot.chemical)}, Терм: ${formatProtectionPercent(prot.thermal)}, Элек: ${formatProtectionPercent(prot.electric)}, Рад: ${formatProtectionPercent(prot.radiation)}`;
        html += '</p>';
    }

    // Эффекты (если есть)
    if (item.attributes?.effects && Array.isArray(item.attributes.effects)) {
        html += '<p><strong>Эффекты:</strong><ul>';
        item.attributes.effects.forEach(eff => {
            html += `<li>${escapeHtml(eff.type)}: ${escapeHtml(eff.value)}</li>`;
        });
        html += '</ul></p>';
    }

    if (item.category === 'ammo') {
        const ammoVariants = item.attributes?.ammo_variants?.length
            ? item.attributes.ammo_variants
            : normalizeAmmoVariants(item.attributes?.ammo_variant || item.attributes?.ammo_kind || item.attributes?.special_version || item.attributes?.effect);
        html += `<p><strong>Варианты:</strong> ${escapeHtml(getAmmoVariantLabels(ammoVariants))}</p>`;
        html += `<p><strong>Пробитие:</strong> ${escapeHtml(formatAmmoPenetration(item.attributes?.penetration || 0))}</p>`;
        if (item.attributes?.damage !== undefined && item.attributes?.damage !== null) {
            html += `<p><strong>Урон:</strong> ${escapeHtml(String(item.attributes.damage))}</p>`;
        }
        if (item.attributes?.range !== undefined && item.attributes?.range !== null) {
            html += `<p><strong>Дальность:</strong> ${escapeHtml(String(item.attributes.range))}</p>`;
        }
    }

    // Модификации
    if (item.modifications && item.modifications.length > 0) {
        html += '<p><strong>Модификации:</strong><ul>';
        item.modifications.forEach(mod => {
            html += `<li>${escapeHtml(mod.name)}</li>`;
        });
        html += '</ul></p>';
    }

    // Для магазина покажем патроны
    if (item.category === 'magazine' && item.ammo) {
        const total = item.ammo.reduce((sum, a) => sum + a.quantity, 0);
        const cap = item.attributes?.capacity || 0;
        html += `<p><strong>Патроны:</strong> ${total} / ${cap}`;
        if (item.ammo.length > 0) {
            html += '<ul>';
            item.ammo.forEach(a => {
                html += `<li>${escapeHtml(a.name)}: ${a.quantity}</li>`;
            });
            html += '</ul>';
        }
        html += '</p>';
    }

    // Закрываем старое окно, если есть
    const oldModal = document.getElementById('item-details-modal');
    if (oldModal) oldModal.remove();

    const modal = document.createElement('div');
    modal.id = 'item-details-modal';
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content" style="max-width: 500px;">
            <span class="close" onclick="this.closest('.modal').remove()">&times;</span>
            ${html}
            <div class="form-actions" style="margin-top:15px;">
                <button class="btn btn-secondary" onclick="this.closest('.modal').remove()">Закрыть</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    modal.style.display = 'flex';
}

window.updateBackpackItemField = function(index, field, value) {
    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    let items = currentCharacterData.inventory.backpack;
    if (!Array.isArray(items)) items = [];
    if (index >= items.length) return;

    const item = migrateOldItemToNew(items[index]);
    if (field === 'quantity') {
        item.quantity = parseInt(value) || 1;
    } else if (field === 'name') {
        item.name = value;
    } else {
        item[field] = parseFloat(value) || 0;
    }
    items[index] = item;
    recalculateInventoryTotals();
    scheduleAutoSave();
};

window.removeBackpackItemNew = function(index) {
    if (!currentCharacterData.inventory?.backpack) return;
    currentCharacterData.inventory.backpack.splice(index, 1);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
};

function removeItemFromInventory(itemId) {
    // Поиск по ID в рюкзаке (с вложенностью)
    const backpack = currentCharacterData.inventory?.backpack;
    if (Array.isArray(backpack)) {
        for (let i = 0; i < backpack.length; i++) {
            if (backpack[i].id === itemId) {
                backpack.splice(i, 1);
                return true;
            }
            if (backpack[i].contents && removeFromArrayById(backpack[i].contents, itemId)) {
                return true;
            }
        }
    }

    // Поиск в карманах (старый формат без id, ищем по templateId?)
    const pockets = currentCharacterData.inventory?.pockets;
    if (Array.isArray(pockets)) {
        for (let i = 0; i < pockets.length; i++) {
            const pocketItem = pockets[i];
            // Если у предмета есть id и он совпадает
            if (pocketItem.id === itemId) {
                pockets.splice(i, 1);
                return true;
            }
            // Для старых предметов без id — проверяем templateId?
            // Но нам нужно удалить конкретный экземпляр, поэтому будем полагаться на id.
            // Если id нет, значит предмет не мигрирован. Пропускаем.
        }
    }

    // Поиск в подсумках пояса
    const beltPouches = currentCharacterData.equipment?.belt?.pouches;
    if (Array.isArray(beltPouches)) {
        for (const pouch of beltPouches) {
            if (pouch.contents && removeFromArrayById(pouch.contents, itemId)) return true;
        }
    }

    // Поиск в подсумках разгрузки
    const vestPouches = currentCharacterData.equipment?.vest?.pouches;
    if (Array.isArray(vestPouches)) {
        for (const pouch of vestPouches) {
            if (pouch.contents && removeFromArrayById(pouch.contents, itemId)) return true;
        }
    }

    return false;
}

function removeFromArrayById(arr, id) {
    for (let i = 0; i < arr.length; i++) {
        if (arr[i].id === id) {
            arr.splice(i, 1);
            return true;
        }
        if (arr[i].contents && removeFromArrayById(arr[i].contents, id)) return true;
    }
    return false;
}

window.addPocketItemFromTemplate = templateId =>
    addTemplateItemToInventory(templateId, 'pockets');

window.addPocketItemManual = function() {
    if (!window.isGM) {
        showNotification('Только ГМ может добавлять предметы');
        return;
    }
    const newItem = {
        id: generateItemId(),
        templateId: null,
        name: 'Новый предмет',
        category: 'misc',
        quantity: 1,
        weight: 0,
        volume: 0,
        price: 0,
        attributes: {},
        installedModules: [],
        contents: [],
        isContainer: false,
        isEquippable: false,
        isStackable: false
    };

    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!Array.isArray(currentCharacterData.inventory.pockets)) {
        currentCharacterData.inventory.pockets = [];
    }
    currentCharacterData.inventory.pockets.push(newItem);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
};

function calculateStageDurability(baseDurability, material) {
    const coefficient = MATERIAL_COEFFICIENTS[material] || 1;
    return Math.floor(10 * coefficient * baseDurability);
}

function initArmorStagedDurability(armor, template) {
    const baseDur = template.attributes?.max_durability || 100;
    armor.durability = baseDur;
    armor.maxDurability = baseDur;
    armor.material = template.attributes?.material || 'Текстиль';
    armor.stage = 1;
    armor.stageDurability = calculateStageDurability(armor.durability, armor.material);
    armor.currentStageDurability = armor.stageDurability;   // <-- инициализируем

    const stageNames = ['1. Целая', '2. Немного повреждена', '3. Повреждена', '4. Сильно повреждена', '5. Поломана'];
    armor.condition = stageNames[armor.stage - 1];
}

// ========== 7. ВКЛАДКА "ЗАМЕТКИ" ==========
function renderNotesTab(data) {
    const container = document.getElementById('sheet-tab-notes');
    container.innerHTML = `
        <div class="form-group">
            <label>Журнал заметок</label>
            <textarea class="form-control" name="notes" rows="20" style="width:100%;">${escapeHtml(data.notes || '')}</textarea>
        </div>
    `;
}

// ========== 8. ВКЛАДКА "НАСТРОЙКИ" ==========
function renderSettingsTab(data) {
    const container = document.getElementById('sheet-tab-settings');
    if (!container) return;

    const ownerUsername = currentCharacterData.ownerUsername || 'Неизвестно';
    const visibleTo = currentCharacterData.visible_to || [];
    const editableTo = currentCharacterData.editable_to || [];
    const currentUserId = parseInt(localStorage.getItem('user_id'));
    const isOwner = currentCharacterData.ownerId === currentUserId;
    const isGM = window.isGM === true;

    let html = `
        <h4>Владелец: ${escapeHtml(ownerUsername)}</h4>
    `;

    if (isGM) {
        html += `
            <hr>
            <h4>Видимость</h4>
            <div id="visibility-settings-container"></div>
            <button type="button" class="btn btn-sm" onclick="applyVisibilityFromSheet()">Применить видимость</button>
        `;
    }
    if (isOwner || isGM) {
        html += `
            ${isGM ? '<hr>' : ''}
            <button type="button" class="btn btn-sm btn-danger" onclick="deleteCharacterFromSheet()">Удалить персонажа</button>
        `;
    }

    container.innerHTML = html;

    const visContainer = document.getElementById('visibility-settings-container');
    if (visContainer && lobbyParticipants && lobbyParticipants.length) {
        visContainer.innerHTML = '';
        lobbyParticipants.forEach(p => {
            const div = document.createElement('div');
            div.className = 'visibility-participant';
            div.innerHTML = `
                <label><input class="character-visible-checkbox" type="checkbox" value="${p.user_id}" ${visibleTo.includes(p.user_id) ? 'checked' : ''}> Видимость</label>
                <label><input class="character-editable-checkbox" type="checkbox" value="${p.user_id}" ${editableTo.includes(p.user_id) ? 'checked' : ''}> Редактирование</label>
                <label>${p.username}</label>
            `;
            const visible = div.querySelector('.character-visible-checkbox');
            const editable = div.querySelector('.character-editable-checkbox');
            editable.addEventListener('change', () => {
                if (editable.checked) visible.checked = true;
            });
            visible.addEventListener('change', () => {
                if (!visible.checked) editable.checked = false;
            });
            visContainer.appendChild(div);
        });
    }
}

window.applyVisibilityFromSheet = function() {
    const visibleTo = Array.from(document.querySelectorAll(
        '#visibility-settings-container .character-visible-checkbox:checked'
    )).map(cb => parseInt(cb.value, 10));
    const editableTo = Array.from(document.querySelectorAll(
        '#visibility-settings-container .character-editable-checkbox:checked'
    )).map(cb => parseInt(cb.value, 10));
    Server.setCharacterVisibility(currentCharacterId, visibleTo, editableTo)
        .then(() => showNotification('Видимость обновлена', 'success'))
        .catch(err => showNotification(err.message));
};

window.deleteCharacterFromSheet = function() {
    if (!confirm('Удалить персонажа?')) return;
    Server.deleteCharacter(currentCharacterId)
        .then(() => {
            showNotification('Персонаж удалён', 'success');
            closeCharacterSheet();
            import('./characters.js').then(module => module.loadLobbyCharacters());
        })
        .catch(err => showNotification(err.message));
};

// ========== 9. ПУБЛИЧНЫЕ ФУНКЦИИ ==========
export async function openCharacterSheet(characterId, tabId = 'basic', options = {}) {
    const oldTooltip = document.getElementById('delayed-item-tooltip');
    if (oldTooltip) oldTooltip.style.display = 'none';
    if (!options.background && (widgetOperation || widgetContext && widgetDialogOpen())) {
        showNotification('Сначала завершите действие виджета', 'system');
        return;
    }
    if (medicalOperationInProgress) {
        showNotification('Дождитесь завершения применения предмета', 'system');
        return;
    }
    if (Number(currentCharacterId) !== Number(characterId)) expandedInventoryContainers.clear();
    currentCharacterId = characterId;
    if (!options.snapshot) {
        window.currentCharacterId = characterId;
        localStorage.setItem('currentCharacterId', String(characterId));
    }
    try {
        const character = options.character || await Server.getCharacter(characterId);
        if (character.data?.is_mutant || character.data?.basic?.is_mutant) {
            if (options.background) throw new Error('Этот виджет относится к листу персонажа другого типа');
            currentCharacterId = null;
            window.currentCharacterId = null;
            localStorage.removeItem('currentCharacterId');
            const characters = await import('./characters.js');
            await characters.openMutantCard(characterId, character);
            return;
        }
        currentCharacterCanEdit = character.can_edit === true;
        sheetSaveBlocked = false;
        currentCharacterData = character.data || {};
        normalizeCharacterEffects(currentCharacterData);

        migratePouchesToNewFormat();

        function ensureSkillXp(data) {
            const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);
            if (!isRecord(data.skills)) data.skills = {};
            const skills = data.skills;
            const categories = ['physical', 'social', 'other'];
            for (const cat of categories) {
                if (!isRecord(skills[cat])) skills[cat] = {};
                for (const [key, rawSkill] of Object.entries(skills[cat])) {
                    const skill = isRecord(rawSkill)
                        ? rawSkill
                        : { base: Number.isFinite(Number(rawSkill)) && rawSkill !== '' ? Number(rawSkill) : 5, bonus: 0 };
                    skills[cat][key] = skill;
                    if (skill.xp === undefined) skill.xp = 0;
                }
            }
            if (skills.skillPoints === undefined) skills.skillPoints = 30;

            const weaponKeys = ['pistols', 'shotguns', 'smgs', 'assaultRifles', 'sniperRifles', 'grenadeLaunchers', 'machineGuns'];
            if (!isRecord(skills.specialized)) skills.specialized = {};
            for (const key of weaponKeys) {
                const specialization = skills.specialized[key];
                if (!isRecord(specialization)) {
                    const legacyLevel = typeof specialization === 'string' && specialization
                        ? specialization
                        : 'unfamiliar';
                    skills.specialized[key] = { level: legacyLevel, xp: 0 };
                }
                if (skills.specialized[key].xp === undefined) skills.specialized[key].xp = 0;
            }
        }
        ensureSkillXp(currentCharacterData);

        currentCharacterData.ownerId = character.owner_id;
        currentCharacterData.ownerUsername = character.owner_username;
        currentCharacterData.visible_to = character.visible_to || [];
        currentCharacterData.editable_to = character.editable_to || [];
        await getAllItemTemplates();
        await renderCharacterSheet(character.name, currentCharacterData);
        applySheetEditPermissions();
        switchSheetTab(tabId);
        if (!options.background) {
            widgetContext = false;
            document.body.classList.add('character-sheet-open');
            document.getElementById('character-sheet-modal').style.display = 'flex';
            document.dispatchEvent(new CustomEvent('character-sheet-opened'));
        }

        const socket = getSocket();
        if (socket && !options.snapshot) {
            socket.emit('join_character', { token: localStorage.getItem('access_token'), character_id: characterId });
            if (sheetSocketHandler) socket.off('character_data_updated', sheetSocketHandler);
            sheetSocketHandler = async (data) => {
                rememberCharacterSnapshots(data);
                if (Number(data.character_id) === Number(currentCharacterId)) {
                    if (Server.isOwnCharacterSave(data.save_id)) return;
                    if (widgetOperation) { rememberPendingSheetRemoteUpdate(data.updates.data); return; }
                    if (medicalOperationInProgress) {
                        if (!medicalRemoteUpdate || Number(data.updates.data?._revision) > Number(medicalRemoteUpdate.data?._revision)) {
                            medicalRemoteUpdate = { character_id: Number(data.character_id), data: data.updates.data };
                        }
                        return;
                    }
                    if (autoSaveTimer || Server.isCharacterSaving(currentCharacterId) || sheetSaveBlocked) {
                        rememberPendingSheetRemoteUpdate(data.updates.data);
                        return;
                    }
                    if (Number(data.updates.data?._revision) < Number(currentCharacterData?._revision)) return;
                    await queueRemoteSheetData(data.updates.data, currentCharacterId);
                }
            };
            socket.on('character_data_updated', sheetSocketHandler);
        }
    } catch (error) {
        if (options.background) {
            currentCharacterId = null;
            currentCharacterData = null;
            currentCharacterCanEdit = false;
            throw error;
        }
        showNotification(error.message);
    }
}

export async function openCharacterAttackSheet(characterId) {
    await openCharacterSheet(characterId, 'equipment');
    if (Number(currentCharacterId) !== Number(characterId)) return;
    const tab = document.getElementById('sheet-tab-equipment');
    const weaponsPanel = tab?.querySelector('[data-equipment-panel="weapons"]');
    if (!weaponsPanel) return;
    weaponsPanel.open = true;

    const combatCharacter = window.locationCombatState?.status === 'active'
        ? window.locationCombatState.characters?.find(item => Number(item.character_id) === Number(characterId))
        : null;
    const equippedIndex = combatCharacter?.drawn_weapon_index ?? currentCharacterData?.activeWeaponIndex;
    const weaponPanel = equippedIndex !== null && equippedIndex !== undefined
        && Number.isInteger(Number(equippedIndex)) && Number(equippedIndex) >= 0
        ? tab.querySelector(`[data-equipment-panel="weapon-${Number(equippedIndex)}"]`)
        : null;
    if (weaponPanel) weaponPanel.open = true;
    requestAnimationFrame(() => {
        if (tab.classList.contains('active')) {
            (weaponPanel || weaponsPanel).scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    });
}

export function closeCharacterSheet() {
    if (medicalOperationInProgress) {
        showNotification('Дождитесь завершения применения предмета', 'system');
        return;
    }
    if (autoSaveTimer) void forceSyncCharacter();
    const socket = getSocket();
    if (socket && currentCharacterId) {
        socket.emit('leave_character', { token: localStorage.getItem('access_token'), character_id: currentCharacterId });
    }
    document.getElementById('character-sheet-modal').style.display = 'none';
    document.body.classList.remove('character-sheet-open');
    const itemTooltip = document.getElementById('delayed-item-tooltip');
    if (itemTooltip) itemTooltip.style.display = 'none';
    currentCharacterId = null;
    currentCharacterData = null;
    pendingSheetRemoteUpdate = null;
    if (autoSaveTimer) clearTimeout(autoSaveTimer);
    autoSaveTimer = null;
    draggedItem = null;
    draggedItemPath = null;
    document.dispatchEvent(new CustomEvent('character-sheet-closed'));
}

export function exportCharacter() {
    const dataStr = JSON.stringify(currentCharacterData, null, 2);
    const blob = new Blob([dataStr], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `character_${currentCharacterId}.json`;
    a.click();
    URL.revokeObjectURL(url);
}

export function importCharacter(file) {
    const reader = new FileReader();
    reader.onload = async (e) => {
        try {
            const importedData = JSON.parse(e.target.result);
            importedData._revision = currentCharacterData?._revision;
            importedData._character_id = currentCharacterData?._character_id;
            currentCharacterData = importedData;
            const nameEl = document.getElementById('character-sheet-name');
            if (nameEl) {
                renderCharacterSheet(nameEl.textContent, currentCharacterData);
            }
            showNotification('Данные импортированы', 'system');
        } catch (err) {
            showNotification('Ошибка парсинга JSON');
        }
    };
    reader.readAsText(file);
}

window.rollSkill = async function(skillPath, skillLabel) {
    if (!currentCharacterData) return;
    if(skillPath.startsWith('social.')||currentCharacterData.health?.combatMeta?.brigadierOrder) {
        const characterId=currentCharacterId,lobbyId=currentLobbyId;
        await forceSyncCharacter();
        if(Number(currentCharacterId)!==Number(characterId))return;
        const {showSocialCheck}=await import('./socialChecks.js');
        await showSocialCheck({characterId,lobbyId,skillPath:`skills.${skillPath}`,skillLabel,
            favoredMutant:backgroundIs(currentCharacterData,'Охотник')?currentCharacterData.basic?.background?.choices?.favoredMutant:'',
            onSaved:data=>queueRemoteSheetData(data,characterId)});
        return;
    }
    let darkness = isWorldDarkTime(window.lobbyGameTimeMinutes ?? 480),portableIllumination=null;
    if (window.isLocationActive && (skillPath === 'physical.awareness' || skillPath === 'physical.shooting')) {
        const scene = await import('./locationScene.js');
        darkness = scene.isLocationCharacterInDarkness(currentCharacterId) ?? darkness;
        portableIllumination=scene.getLocationPortableIllumination(currentCharacterId);
    }
    let awarenessMode = null;
    if (skillPath === 'physical.awareness') {
        const choice = await chooseConsumableApplication(
            'Проверка Внимательности',
            getAwarenessModeChoices(currentCharacterData, darkness),
            { alwaysShow: true },
        );
        if (!choice) return;
        awarenessMode = choice.mode;
        skillLabel = `${skillLabel} (${awarenessMode === 'visual' ? 'зрение' : 'слух'})`;
    }
    if(backgroundIs(currentCharacterData,'Охотник')||skillPath==='other.stealth'&&traitCount(currentCharacterData,'Человек-куст')) {
        const characterId=currentCharacterId,lobbyId=currentLobbyId;
        await forceSyncCharacter();
        if(Number(currentCharacterId)!==Number(characterId))return;
        const {showSocialCheck}=await import('./socialChecks.js');
        await showSocialCheck({characterId,lobbyId,skillPath:awarenessMode?`skills.physical.awareness.${awarenessMode}`:`skills.${skillPath}`,skillLabel,
            favoredMutant:backgroundIs(currentCharacterData,'Охотник')?currentCharacterData.basic?.background?.choices?.favoredMutant:'',
            vegetation:skillPath==='other.stealth'&&Boolean(traitCount(currentCharacterData,'Человек-куст')),onSaved:data=>queueRemoteSheetData(data,characterId)});
        return;
    }
    const parts = skillPath.split('.');
    let skillObj = currentCharacterData.skills;
    for (const part of parts) {
        if (!skillObj) break;
        skillObj = skillObj[part];
    }
    if (!skillObj) return;

    const effectiveValue = getSkillEffectiveValue(currentCharacterData, skillPath);
    const selfMod = Math.floor((effectiveValue - 10) / 2)+(skillPath==='social.charisma'?facialProfile(currentCharacterData).charismaBonus:0);

    // Харизма для социальных навыков (кроме самой харизмы)
    let charismaMod = 0;
    if (skillPath.startsWith('social.') && skillPath !== 'social.charisma') {
        const charismaValue = getSkillEffectiveValue(currentCharacterData, 'social.charisma');
        charismaMod = Math.floor((charismaValue - 10) / 2)+facialProfile(currentCharacterData).charismaBonus;
    }

    // Бонус от экипировки
    let equipmentBonus = 0;
    const eq = currentCharacterData.equipment || {};

    // --- Бонус к Харизме (от очков, перчаток, бижутерии) ---
    if (skillPath === 'social.charisma') {
        for (const key of ['glasses','gloves','ring','necklace','earrings','bracelet1','bracelet2','helmet','gasMask']) {
            const item=eq[key];if(!item)continue;
            let bonus=Number(item.charismaBonus ?? item.charisma_bonus ?? item.attributes?.charisma_bonus)||0;
            if(key==='glasses'&&traitCount(currentCharacterData,'Сварщик')&&item.name?.trim().toLowerCase()==='очки сварщика')bonus=1;
            equipmentBonus+=bonus;
        }
    }

    // --- Канальные бонусы к Внимательности ---
    if (skillPath === 'physical.awareness') {
        if (awarenessMode === 'hearing' && eq.headphones?.awarenessBonus) {
            equipmentBonus += eq.headphones.awarenessBonus;
        }
        if (awarenessMode === 'visual') {
            equipmentBonus += getConsumableStatValue(currentCharacterData, 'vision_awareness');
            equipmentBonus += getConsumableStatValue(
                currentCharacterData, darkness ? 'darkness_awareness' : 'light_awareness',
            );
        }
    }

    // Можно добавить другие бонусы по необходимости (например, от артефактов, контейнеров и т.д.)

    const statusModifier = getHealthRollModifier(currentCharacterData, skillPath)
        +(awarenessMode==='hearing'?hearingModifier(getDeafnessLevel(currentCharacterData)):0)
        +(awarenessMode==='hearing'?facialProfile(currentCharacterData).hearing:0)
        +(skillPath==='other.stealth'&&backgroundIs(currentCharacterData,'Ночной дозор')?(darkness?3:-5):0);
    const nvgActive=usesNightVision(currentCharacterData);
    const portablePenalty=(awarenessMode==='visual' || skillPath==='physical.shooting') && portableIllumination?.lit && portableIllumination.base_dark && !nvgActive
        ? Number(portableIllumination.penalty)||0 : 0;
    const totalBonus = selfMod + charismaMod + equipmentBonus + statusModifier-portablePenalty;

    const deafness = awarenessMode === 'hearing' ? getDeafnessLevel(currentCharacterData) : 0;
    const hearingBlocked = awarenessMode === 'hearing' && (deafness >= 90 || facialProfile(currentCharacterData).ears===2);
    const firstDice = hearingBlocked ? 0 : Math.floor(Math.random() * 20) + 1;
    const darknessImmunity = getConsumableStatValue(
        currentCharacterData,
        awarenessMode === 'visual' ? 'darkness_visual_immunity' : 'darkness_shooting_immunity',
    ) > 0;
    const darknessDisadvantage = darkness && (awarenessMode === 'visual' || skillPath === 'physical.shooting')
        && !darknessImmunity && !nvgActive && !passiveTraits(currentCharacterData).noDarknessPenalty;
    const disadvantaged = !hearingBlocked && (
        hasHealthRollDisadvantage(currentCharacterData, skillPath) || darknessDisadvantage
    );
    const secondDice = disadvantaged ? Math.floor(Math.random() * 20) + 1 : firstDice;
    const dice = disadvantaged ? Math.min(firstDice, secondDice) : firstDice;
    const total = dice + totalBonus;

    let modStr = `навык ${effectiveValue}, модификатор = ${selfMod}`;
    if (charismaMod !== 0) modStr += ` + харизма = ${charismaMod}`;
    if (equipmentBonus !== 0) modStr += ` + экипировка = ${equipmentBonus}`;
    if (statusModifier !== 0) modStr += ` + состояния = ${statusModifier}`;
    if (portablePenalty) modStr += ` − освещение = ${portablePenalty}`;

    const disadvantageText = disadvantaged ? ', Помеха' : '';
    const blockedText = hearingBlocked ? ', персонаж не слышит' : '';
    showNotification(`🎲 ${skillLabel}: бросок к20 = ${dice}${disadvantaged ? ` (${firstDice}/${secondDice})` : ''}${disadvantageText}${blockedText}, ${modStr}, итог = ${total}`, 'system');

    const socket = getSocket();
    if (socket && currentLobbyId) {
        const message = `🎲 ${skillLabel}: бросок к20 = ${dice}${disadvantaged ? ` (${firstDice}/${secondDice}), Помеха` : ''}${blockedText}, ${modStr}, итог = **${total}**`;
        socket.emit('send_message', {
            token: localStorage.getItem('access_token'),
            lobby_id: currentLobbyId,
            message: message
        });
    }
};

// ========== УНИВЕРСАЛЬНЫЕ ОБЁРТКИ ДЛЯ СЛОТОВ ==========

window.universalInstallModulePrompt = async function(targetPath, slotType) {
    const targetItem = getItemByPath(targetPath);
    if (!targetItem) {
        showNotification('Целевой предмет не найден');
        return;
    }

    const candidateModules = [];
    const collect = (items, path) => {
        if (!Array.isArray(items)) return;
        items.forEach((it, idx) => {
            let matches = false;
            if (slotType === 'battery') {
                matches = (it.category === 'device' && it.subcategory === 'battery');
            } else if (slotType === 'exoskeleton_battery') {
                matches = (
                    it.category === 'exoskeleton_module'
                    && (
                        it.subcategory === 'battery'
                        || it.attributes?.slot_type === 'exoskeleton_battery'
                    )
                    && Number(it.attributes?.remaining_days) > 0
                );
            } else if (slotType === 'filter') {
                matches = (
                    (it.category === 'gas_mask_module' && (it.subcategory === 'filter' || it.attributes?.slot_type === 'filter'))
                    || Number.isFinite(Number(it.attributes?.consumable?.direct?.filter_charges))
                    || Number.isFinite(Number(it.attributes?.filter_charges))
                );
            } else if (slotType === 'artifact') {
                matches = (it.category === 'artifact');
            } else {
                matches = (it.slot_type === slotType) || (it.attributes?.slot_type === slotType);
            }
            if (matches) candidateModules.push({ item: it, path: path.concat(idx) });
            if (it.contents) collect(it.contents, path.concat(idx, 'contents'));
        });
    };

    collect(currentCharacterData.inventory?.backpack, ['inventory', 'backpack']);
    collect(currentCharacterData.inventory?.pockets, ['inventory', 'pockets']);
    const beltPouches = currentCharacterData.equipment?.belt?.pouches || [];
    beltPouches.forEach((pouch, i) => collect(pouch.contents, ['equipment', 'belt', 'pouches', i, 'contents']));
    const vestPouches = currentCharacterData.equipment?.vest?.pouches || [];
    vestPouches.forEach((pouch, i) => collect(pouch.contents, ['equipment', 'vest', 'pouches', i, 'contents']));

    if (candidateModules.length === 0) {
        showNotification('Нет подходящих модулей в инвентаре');
        return;
    }

    const oldModal = document.getElementById('universal-module-select-modal');
    if (oldModal) oldModal.remove();

    const modal = document.createElement('div');
    modal.id = 'universal-module-select-modal';
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content">
            <span class="close" onclick="this.closest('.modal').remove()">&times;</span>
            <h3>Выберите модуль</h3>
            <select id="universal-module-select" class="form-control" size="5"></select>
            <div class="form-actions" style="margin-top:15px;">
                <button class="btn btn-primary" id="confirm-universal-module">Установить</button>
                <button class="btn btn-secondary" onclick="this.closest('.modal').remove()">Отмена</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    const select = modal.querySelector('#universal-module-select');
    candidateModules.forEach((entry, idx) => {
        const opt = document.createElement('option');
        opt.value = idx;
        let desc = entry.item.name;
        if (slotType === 'filter') {
            const dur = entry.item.attributes?.durability || 0;
            const maxDur = entry.item.attributes?.max_durability || 0;
            desc += ` (прочность ${dur}/${maxDur})`;
        } else if (slotType === 'battery') {
            const power = entry.item.attributes?.power;
            desc += ` (заряд ${power !== undefined ? power : '?'}%)`;
        } else if (slotType === 'exoskeleton_battery') {
            desc = `Аккумулятор · ${Number(entry.item.attributes?.remaining_days) || 0} сут.`;
        }
        opt.textContent = desc;
        select.appendChild(opt);
    });

    modal.querySelector('#confirm-universal-module').onclick = async function() {
        const idx = select.value;
        if (idx === '') return;
        const selected = candidateModules[idx];
        modal.remove();

        if (slotType === 'filter') {
            try {
                await runEquipmentModuleAction('install', targetPath, slotType, selected.path);
            } catch (error) {
                showNotification(error.message || 'Не удалось установить фильтр');
            }
            return;
        }

        updateDataFromFields();
        const success = universalInstallModule(targetItem, targetPath, selected.item, selected.path, slotType);
        if (success) {
            // Обновить слоты – удалить старый блок, вставить новый
            const targetDiv = document.querySelector(`[data-path="${targetPath.join(',')}"]`);
            if (targetDiv) {
                const oldSlots = targetDiv.querySelector('.item-slots-container');
                if (oldSlots) oldSlots.remove();
                const newSlotsHtml = renderSlotsUniversal(targetItem, targetPath, 1);
                if (newSlotsHtml) targetDiv.insertAdjacentHTML('beforeend', newSlotsHtml);
            }
            // Перерисовать контейнер-источник
            const sourceContainerPath = selected.path.slice(0, -1);
            await rerenderContainer(sourceContainerPath);
            await renderEquipmentTab(currentCharacterData);
            if (slotType === 'exoskeleton_battery') {
                await renderSkillsTab(currentCharacterData);
            }
            recalculateInventoryTotals();
            updatePlateProtectionDisplay();
            scheduleAutoSave();
            forceSyncCharacter();
            showNotification('Модуль установлен', 'success');
        } else {
            showNotification('Не удалось установить модуль');
        }
    };

    modal.style.display = 'flex';
};

function universalUninstallModule(targetItem, targetPath, slotType) {
    if (!targetItem.installedModules) return null;
    const index = targetItem.installedModules.findIndex(m => m.slotType === slotType);
    if (index === -1) return null;

    const moduleItem = targetItem.installedModules[index];
    targetItem.installedModules.splice(index, 1);
    if (slotType === 'exoskeleton_battery') targetItem.powered = false;

    let restoredItem;
    const templateId = moduleItem.templateId;
    if (templateId) {
        const allTemplates = allTemplatesCache || [];
        const template = allTemplates.find(t => t.id === templateId);
        if (template) {
            restoredItem = createItemFromTemplate(template);
            restoredItem.durability = moduleItem.durability;
            restoredItem.maxDurability = moduleItem.maxDurability;
            restoredItem.installedModules = moduleItem.installedModules ? [...moduleItem.installedModules] : [];
            restoredItem.modifications = moduleItem.modifications ? [...moduleItem.modifications] : [];
            if (moduleItem.attributes) restoredItem.attributes = { ...moduleItem.attributes };
        } else {
            restoredItem = { ...moduleItem };
        }
    } else {
        restoredItem = { ...moduleItem };
    }

    // Всегда кладём в рюкзак
    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!currentCharacterData.inventory.backpack) currentCharacterData.inventory.backpack = [];
    currentCharacterData.inventory.backpack.push(restoredItem);

    restoredItem.sourcePath = null;
    return restoredItem;
}

window.universalUninstallModuleByPath = async function(targetPath, slotType) {
    if (slotType === 'filter') {
        try {
            await runEquipmentModuleAction('uninstall', targetPath, slotType);
        } catch (error) {
            showNotification(error.message || 'Не удалось снять фильтр');
        }
        return;
    }
    updateDataFromFields();
    const targetItem = getItemByPath(targetPath);
    if (!targetItem) {
        showNotification('Предмет не найден');
        return;
    }

    const restoredItem = universalUninstallModule(targetItem, targetPath, slotType);
    if (restoredItem) {
        // Обновить слоты – удалить старый блок, вставить новый
        const targetDiv = document.querySelector(`[data-path="${targetPath.join(',')}"]`);
        if (targetDiv) {
            const oldSlots = targetDiv.querySelector('.item-slots-container');
            if (oldSlots) oldSlots.remove();
            const newSlotsHtml = renderSlotsUniversal(targetItem, targetPath, 1);
            if (newSlotsHtml) targetDiv.insertAdjacentHTML('beforeend', newSlotsHtml);
        }
        // Перерисовать рюкзак (куда вернули модуль)
        await rerenderContainer(['inventory', 'backpack']);
        await renderEquipmentTab(currentCharacterData);
        if (slotType === 'exoskeleton_battery') {
            await renderSkillsTab(currentCharacterData);
        }
        recalculateInventoryTotals();
        updatePlateProtectionDisplay();
        scheduleAutoSave();
        forceSyncCharacter();
        showNotification('Модуль снят', 'success');
    } else {
        showNotification('Не удалось снять модуль');
    }
};

window.installModuleFromSlot = function(jsonPath, slotType) {
    const targetPath = JSON.parse(jsonPath);
    universalInstallModulePrompt(targetPath, slotType);
};

window.uninstallModuleFromSlot = function(jsonPath, slotType) {
    const targetPath = JSON.parse(jsonPath);
    universalUninstallModuleByPath(targetPath, slotType);
};

function updatePlateProtectionDisplay() {
    const plateInfo = getEffectiveTorsoProtection();
    const vestDiv = document.querySelector('[data-vest-protection]');
    if (vestDiv) {
        if (plateInfo) {
            const frontText = plateInfo.front !== null ? `${plateInfo.front}%` : 'нет';
            const backText = plateInfo.back !== null ? `${plateInfo.back}%` : 'нет';
            vestDiv.innerHTML = `<strong>Бронеплиты:</strong> перед ${frontText}, спина ${backText}`;
        } else {
            vestDiv.innerHTML = ''; // или скрыть
        }
    }
}

// Добавить где-нибудь после функции getItemByPath (например, перед universalInstallModulePrompt)

/**
 * Перерисовать содержимое контейнера (массива items) по его пути.
 * @param {Array} containerPath - путь к контейнеру в currentCharacterData
 * @param {HTMLElement} [parentElement] - если известен, иначе найдёт по data-path
 */
async function rerenderContainer(containerPath, parentElement = null, options = {}) {
    const pouchesIndex = containerPath.indexOf('pouches');
    if (pouchesIndex !== -1 &&
        pouchesIndex + 1 < containerPath.length &&
        typeof containerPath[pouchesIndex + 1] === 'number' &&
        containerPath[containerPath.length - 1] !== 'contents') {
        const contentsPath = containerPath.concat('contents');
        await rerenderContainer(contentsPath, parentElement, options);
        return;
    }

    const container = getItemByPath(containerPath);
    if (!container) return;

    let itemsArray = null;
    const lastKey = containerPath[containerPath.length - 1];
    if (typeof lastKey === 'string' && (lastKey === 'contents' || lastKey === 'backpack' || lastKey === 'pockets')) {
        itemsArray = container;
    } else if (Array.isArray(container)) {
        itemsArray = container;
    } else if (container && typeof container === 'object' && Array.isArray(container.contents)) {
        itemsArray = container.contents;
    } else {
        return;
    }

    const isPockets = (containerPath.length === 2 && containerPath[0] === 'inventory' && containerPath[1] === 'pockets');
    const isBackpack = (containerPath.length === 2 && containerPath[0] === 'inventory' && containerPath[1] === 'backpack');

    let containerDiv = parentElement;
    if (!containerDiv) {
        if (isPockets) {
            containerDiv = document.getElementById('pockets-container');
        } else if (isBackpack) {
            containerDiv = document.getElementById('backpack-container');
        } else {
            // Ищем элемент с data-container-path и классом container-contents
            const candidates = document.querySelectorAll(`[data-container-path="${containerPath.join(',')}"]`);
            for (const el of candidates) {
                if (el.classList.contains('container-contents')) {
                    containerDiv = el;
                    break;
                }
            }
            if (!containerDiv) {
                const anyElement = document.querySelector(`[data-container-path="${containerPath.join(',')}"]`);
                if (anyElement) {
                    containerDiv = anyElement.querySelector('.container-contents');
                }
            }
            if (!containerDiv && containerPath.length > 0) {
                const parentPath = containerPath.slice(0, -1);
                const parentDiv = document.querySelector(`[data-path="${parentPath.join(',')}"]`);
                if (parentDiv) {
                    containerDiv = parentDiv.querySelector('.container-contents');
                }
            }
        }
    }

    if (!containerDiv) return;
    if (!isPockets && !isBackpack && !containerDiv.classList.contains('container-contents')) return;

    const keepExpanded = Boolean(options.keepExpanded);
    const wasCollapsed = (!isPockets && !isBackpack && !keepExpanded)
        ? (containerDiv.style.display === 'none')
        : false;
    const allTemplates = await getAllItemTemplates();
    const parentItem = (!isPockets && !isBackpack) ? containerDiv.closest('.container-item') : null;

    containerDiv.innerHTML = '';
    itemsArray.forEach((item, idx) => {
        renderBackpackItem(item, idx, containerPath, containerDiv, allTemplates);
    });

    // Кнопка добавления (только для подсумков)
    if (!isPockets && !isBackpack) {
        let isArmorPlateSlot = false;
        if (containerPath.includes('pouches')) {
            const pouchPath = containerPath.slice(0, containerPath.indexOf('pouches') + 2);
            const pouchItem = getItemByPath(pouchPath);
            if (pouchItem && pouchItem.attributes?.slots?.some(s => s.type === 'armor_plate')) {
                isArmorPlateSlot = true;
            }
        }
        if (window.isGM && !isArmorPlateSlot) {
            const addBtn = document.createElement('button');
            addBtn.type = 'button';
            addBtn.className = 'btn btn-sm btn-secondary';
            addBtn.textContent = '➕ Добавить внутрь';
            addBtn.onclick = () => openInventoryTemplatePicker(containerPath);
            containerDiv.appendChild(addBtn);
        }
    }

    if (!isPockets && !isBackpack && keepExpanded) {
        containerDiv.style.display = '';
    } else if (wasCollapsed) {
        containerDiv.style.display = 'none';
    }
    if (parentItem && parentItem._toggleIcon) {
        parentItem._toggleIcon.textContent = wasCollapsed ? '▶' : '▼';
    }

    if (!isPockets && !isBackpack) {
        updatePouchVolumeFromContentsDiv(containerDiv);
    }
    setupDropTarget(containerDiv, containerPath, container);
}

// ========== 10. UI-ФУНКЦИИ ДОБАВЛЕНИЯ/УДАЛЕНИЯ ==========
window.addWeapon = function() {
    updateDataFromFields();
    if (!currentCharacterData.weapons) currentCharacterData.weapons = [];
    currentCharacterData.weapons.push({ id: generateItemId() });
    renderEquipmentTab(currentCharacterData);
    scheduleAutoSave();
};

window.removeWeapon = function(index) {
    updateDataFromFields();
    if (!currentCharacterData.weapons) return;
    currentCharacterData.weapons.splice(index, 1);
    if (Number(currentCharacterData.activeWeaponIndex) === Number(index)) {
        delete currentCharacterData.activeWeaponIndex;
    } else if (Number(currentCharacterData.activeWeaponIndex) > Number(index)) {
        currentCharacterData.activeWeaponIndex -= 1;
    }
    renderEquipmentTab(currentCharacterData);
    scheduleAutoSave();
};

window.addWeaponModule = function(weaponIndex) {
    updateDataFromFields();
    if (!currentCharacterData.weapons) currentCharacterData.weapons = [];
    if (!currentCharacterData.weapons[weaponIndex]) currentCharacterData.weapons[weaponIndex] = {};
    if (!Array.isArray(currentCharacterData.weapons[weaponIndex].modules)) {
        currentCharacterData.weapons[weaponIndex].modules = [];
    }
    currentCharacterData.weapons[weaponIndex].modules.push({ name: '', description: '' });
    renderEquipmentTab(currentCharacterData);
    scheduleAutoSave();
};

window.removeWeaponModule = function(weaponIndex, moduleIndex) {
    updateDataFromFields();
    if (!currentCharacterData.weapons?.[weaponIndex]?.modules) return;
    currentCharacterData.weapons[weaponIndex].modules.splice(moduleIndex, 1);
    renderEquipmentTab(currentCharacterData);
    scheduleAutoSave();
};

window.addWeaponModification = function(weaponIndex) {
    updateDataFromFields();
    if (!currentCharacterData.weapons) currentCharacterData.weapons = [];
    if (!currentCharacterData.weapons[weaponIndex]) currentCharacterData.weapons[weaponIndex] = {};
    if (!Array.isArray(currentCharacterData.weapons[weaponIndex].modifications)) {
        currentCharacterData.weapons[weaponIndex].modifications = [];
    }
    currentCharacterData.weapons[weaponIndex].modifications.push({ name: '', description: '' });
    renderEquipmentTab(currentCharacterData);
    scheduleAutoSave();
};

window.removeWeaponModification = function(weaponIndex, modIndex) {
    updateDataFromFields();
    if (!currentCharacterData.weapons?.[weaponIndex]?.modifications) return;
    currentCharacterData.weapons[weaponIndex].modifications.splice(modIndex, 1);
    renderEquipmentTab(currentCharacterData);
    scheduleAutoSave();
};

window.addBackpackItemFromTemplate = templateId =>
    addTemplateItemToInventory(templateId, 'backpack');

window.addBackpackItemManual = function() {
    if (!window.isGM) {
        showNotification('Только ГМ может добавлять предметы');
        return;
    }
    const newItem = {
        id: generateItemId(),
        templateId: null,
        name: 'Новый предмет',
        category: 'misc',
        quantity: 1,
        weight: 0,
        volume: 0,
        price: 0,
        attributes: {},
        installedModules: [],
        contents: [],
        isContainer: false,
        isEquippable: false,
        isStackable: false
    };

    if (!currentCharacterData.inventory) currentCharacterData.inventory = {};
    if (!Array.isArray(currentCharacterData.inventory.backpack)) {
        currentCharacterData.inventory.backpack = [];
    }
    currentCharacterData.inventory.backpack.push(newItem);
    renderInventoryTab(currentCharacterData);
    scheduleAutoSave();
};

window.addPdaItem = function() {
    updateDataFromFields();
    if (!currentCharacterData.modifications) currentCharacterData.modifications = {};
    if (!currentCharacterData.modifications.pda) currentCharacterData.modifications.pda = {};
    if (!Array.isArray(currentCharacterData.modifications.pda.items)) {
        currentCharacterData.modifications.pda.items = [];
    }
    currentCharacterData.modifications.pda.items.push({ name: '', effect: '' });
    renderEquipmentTab(currentCharacterData);
    scheduleAutoSave();
};

window.removePdaItem = function(index) {
    updateDataFromFields();
    if (!currentCharacterData.modifications?.pda?.items) return;
    currentCharacterData.modifications.pda.items.splice(index, 1);
    renderEquipmentTab(currentCharacterData);
    scheduleAutoSave();
};

function setupDropTarget(containerDiv, containerPath, containerItem) {
    if (!containerDiv) return;
    containerDiv.classList.add('drop-target');
    containerDiv.setAttribute('data-container-path', containerPath.join(','));

    containerDiv.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.stopPropagation();
        e.dataTransfer.dropEffect = 'move';
        containerDiv.classList.add('drag-over');
    });

    containerDiv.addEventListener('dragleave', () => {
        containerDiv.classList.remove('drag-over');
    });

    containerDiv.addEventListener('drop', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        containerDiv.classList.remove('drag-over');

        let sourcePath = draggedItemPath;
        try {
            const transferredPath = e.dataTransfer.getData('application/x-inventory-path');
            if (transferredPath) sourcePath = JSON.parse(transferredPath);
        } catch (error) {
            console.warn('Invalid inventory drag path', error);
        }
        if (!Array.isArray(sourcePath)) {
            const plainPath = e.dataTransfer.getData('text/plain');
            if (plainPath) sourcePath = plainPath.split(',').map(part => /^\d+$/.test(part) ? Number(part) : part);
        }
        const sourceItem = Array.isArray(sourcePath) ? getItemByPath(sourcePath) : null;
        if (!sourceItem || !sourcePath) return;
        draggedItem = sourceItem;
        draggedItemPath = sourcePath;

        const targetContainer = containerItem || getItemByPath(containerPath);
        if (!targetContainer) return;

        if (containerPath.length > draggedItemPath.length &&
            containerPath.slice(0, draggedItemPath.length).every((v, i) => v === draggedItemPath[i])) {
            showNotification('Нельзя поместить контейнер в самого себя');
            draggedItem = null;
            draggedItemPath = null;
            return;
        }

        const targetItems = Array.isArray(targetContainer)
            ? targetContainer
            : targetContainer.contents;
        if (!Array.isArray(targetItems)) return;

        const volumeOf=containerPath[0]==='inventory'&&containerPath[1]==='backpack'?(item=>backpackItemVolume(currentCharacterData,item)):getTotalVolume;
        const newVolume = volumeOf(draggedItem);
        const sourceContainerPath = draggedItemPath.slice(0, -1);
        const isSameContainer = sourceContainerPath.length === containerPath.length
            && sourceContainerPath.every((value, index) => value === containerPath[index]);
        const currentUsed = targetItems.reduce((sum, item) => sum + volumeOf(item), 0)
            - (isSameContainer ? newVolume : 0);
        const limit = containerPath.length===2&&containerPath[0]==='inventory'&&containerPath[1]==='pockets'
            ? pocketCapacity(currentCharacterData)
            : Number(targetContainer.internalVolume || targetContainer.capacity || targetContainer.volume || 0);
        if (currentUsed + newVolume > limit) {
            showNotification('Недостаточно места в контейнере');
            draggedItem = null; draggedItemPath = null;
            return;
        }

        // Удаляем предмет из источника
        if (!removeItemByPath(draggedItemPath)) {
            showNotification('Ошибка при удалении предмета из источника');
            draggedItem = null; draggedItemPath = null;
            return;
        }

        // Добавляем в целевой контейнер
        targetItems.push(draggedItem);

        // Полная перерисовка сохраняет корректные пути после сдвига индексов.
        await renderInventoryTab(currentCharacterData);

        recalculateInventoryTotals();
        scheduleAutoSave();
        forceSyncCharacter();
        showNotification('Предмет перемещён', 'success');

        draggedItem = null;
        draggedItemPath = null;
    });
}

window.getAllItemTemplates = getAllItemTemplates;

async function resumeClientPendingItem(event) {
    // A payment may finish synchronously while the initiating request still holds the sheet lock.
    if (medicalOperationInProgress) return;
    const state = event.detail || window.locationCombatState;
    if (state?.status && state.status !== 'active') return;
    const current = state?.current_character;
    const actionId = current?.completed_pending_action_id;
    const pendingJam = actionId ? pendingWeaponJamActions.get(actionId) : null;
    if (current?.deferred_action?.server_executable || current?.deferred_action?.client_executable) {
        pendingWeaponJamActions.delete(actionId);
        return;
    }
    if (pendingJam && pendingJam.characterId === currentCharacterId) {
        pendingWeaponJamActions.delete(actionId);
        await window.clearWeaponJam(pendingJam.weaponIndex, {
            actionId,
            resume: true,
        });
        return;
    }
}

window.addEventListener('combat-state-updated', resumeClientPendingItem);
window.addEventListener('character-item-operation-finished', resumeClientPendingItem);
