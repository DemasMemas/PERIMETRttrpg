const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/characterSheet.js'), 'utf8');

test('attack shortcut expands equipped weapon and scrolls to its actions', async () => {
    const start = source.indexOf('export async function openCharacterAttackSheet(');
    const end = source.indexOf('\nexport function closeCharacterSheet(', start);
    assert.ok(start >= 0 && end > start);
    const weaponsPanel = { open: false };
    let weaponsTabSelected = false;
    const equippedPanel = {
        open: false,
        scrollIntoView(options) { this.scrolled = options; },
    };
    const tab = {
        classList: { contains: value => value === 'active' },
        querySelector(selector) {
            if (selector === '.equipment-panel-tab[data-panel="weapons"]') {
                return { click() { weaponsTabSelected = true; } };
            }
            if (selector.includes('weapon-1')) return equippedPanel;
            if (selector.includes('"weapons"')) return weaponsPanel;
            return null;
        },
    };
    const context = vm.createContext({
        currentCharacterId: null,
        currentCharacterData: { activeWeaponIndex: 1 },
        window: { locationCombatState: null },
        document: { getElementById: () => tab },
        async openCharacterSheet(characterId, tabId) {
            assert.equal(tabId, 'equipment');
            context.currentCharacterId = characterId;
        },
        requestAnimationFrame(callback) { callback(); },
    });
    vm.runInContext(source.slice(start, end).replace('export async function', 'async function'), context);
    await context.openCharacterAttackSheet(7);
    assert.equal(weaponsTabSelected, true);
    assert.equal(weaponsPanel.open, true);
    assert.equal(equippedPanel.open, true);
    assert.equal(equippedPanel.scrolled.block, 'start');
});

test('запросы начала боя не загружаются для ещё не открытой локации',async()=>{
    const code=fs.readFileSync(path.join(__dirname,'../app/static/js/locationScene.js'),'utf8');
    const start=code.indexOf('export async function loadCombatStartRequests('),end=code.indexOf('\nasync function showCombatParticipantSelection(',start);
    const calls=[],context=vm.createContext({window:{isGM:true,isLocationActive:true,currentLobbyId:1},
        getCurrentLocationId:()=>null,combatStartRequestVersion:0,combatState:{status:'active'},
        Server:{async getLocationCombatStartRequests(...args){calls.push(args);return[];}},
        showNotification(){throw Error('Не должно быть запроса к локации null');}});
    vm.runInContext(code.slice(start,end).replace('export ',''),context);
    await context.loadCombatStartRequests();assert.equal(calls.length,0);
    context.getCurrentLocationId=()=>4;await context.loadCombatStartRequests();assert.deepEqual(calls,[[1,4]]);
});

test('attack on a sublocation outside combat requests approval without spending ammunition', async () => {
    const start = source.indexOf('window.useWeaponFromEquipment = async function(');
    const end = source.indexOf('\nwindow.useMeleeAttack = function(', start);
    assert.ok(start >= 0 && end > start);
    let requestedCharacterId = null;
    const weapon = { name: 'Rifle', templateId: 1, ammo: 5 };
    const context = vm.createContext({
        window: { isLocationActive: true, locationCombatState: null },
        currentCharacterData: { weapons: [weapon] },
        currentCharacterId: 7,
        allTemplatesCache: [],
        getWeaponFireProfile: () => ({ single_shot_options: [1] }),
        getWeaponAmmoCount: item => item.ammo,
        sceneModule: { requestCombatStartForAttack(id) { requestedCharacterId = id; } },
        showNotification() { throw new Error('Attack should request combat instead of firing'); },
    });
    const code = source.slice(start, end).replaceAll("import('./locationScene.js')", 'Promise.resolve(sceneModule)');
    vm.runInContext(code, context);
    context.window.useWeaponFromEquipment(0);
    await new Promise(setImmediate);
    assert.equal(requestedCharacterId, 7);
    assert.equal(weapon.ammo, 5);
});

test('GM can choose whether the attacker acts first when starting combat', () => {
    const sceneSource = fs.readFileSync(path.join(__dirname, '../app/static/js/locationScene.js'), 'utf8');
    const apiSource = fs.readFileSync(path.join(__dirname, '../app/static/js/api.js'), 'utf8');
    assert.match(sceneSource, /class="combat-initiator-first" checked/);
    assert.match(sceneSource, /list\.querySelectorAll\('input\[type="checkbox"\]:checked'\)/);
    assert.match(sceneSource, /initiatorLocationCharacterId, initiatorFirst,/);
    assert.match(sceneSource, /condition\.state === 'active'\) \{\s*menuItems\.push\(\{\s*label: 'Начать бой'/);
    assert.match(sceneSource, /actor\.condition\?\.state !== 'active'/);
    assert.match(sceneSource, /'approve', selectedIds,\s+initiatorFirst/);
    assert.match(apiSource, /initiator_first: initiatorFirst/);
    assert.match(apiSource, /initiator_location_character_id: initiatorLocationCharacterId/);
});

test('dead characters are not offered for combat initiative', () => {
    const sceneSource = fs.readFileSync(path.join(__dirname, '../app/static/js/locationScene.js'), 'utf8');
    assert.match(sceneSource, /const selectableCharacters = characters\.filter\(character => character\.condition\?\.state !== 'dead'\)/);
    assert.match(sceneSource, /selectableCharacters\.forEach\(\(character\) => \{/);
    assert.match(sceneSource, /const absentCombatCharacters = [\s\S]*?character\.condition\?\.state !== 'dead'/);
});

test('неподвижный участник блокирует старт, а ошибка сервера разблокирует кнопку', async () => {
    const code = fs.readFileSync(path.join(__dirname, '../app/static/js/locationScene.js'), 'utf8');
    const begin = code.indexOf('async function showCombatParticipantSelection(');
    const end = code.indexOf('\nconst COMBAT_STATUS_LABELS', begin);
    const condition = { state: 'pain_shock', label: 'Болевой шок', can_act: false, can_recover: false };
    const button = { disabled: false, isConnected: true };
    const other = {};
    const list = { appendChild() {}, querySelectorAll: () => [{ value: '7' }] };
    const menu = { style: {}, addEventListener() {}, querySelector: selector => ({
        '.combat-participant-start': button, '.combat-participant-list': list,
    })[selector] || other };
    let attempts = 0;
    let rejectStart;
    const messages = [];
    const context = vm.createContext({
        window: { isGM: true }, combatState: null, combatParticipantMenu: null, getCurrentLocationId: () => 1,
        closeCombatParticipantMenu() {}, showNotification: message => messages.push(message),
        document: { body: { appendChild() {} }, createElement: tag => tag === 'div' ? menu : { style: {}, querySelector: () => ({}) } },
        Server: {
            getLocationCombatState: async () => ({ characters: [{ location_character_id: 7, condition }] }),
            startLocationCombat() { attempts++; return new Promise((_, reject) => { rejectStart = reject; }); },
        },
    });
    vm.runInContext(code.slice(begin, end), context);
    await context.showCombatParticipantSelection();
    await button.onclick({ currentTarget: button });
    assert.equal(attempts, 0);
    assert.match(messages.pop(), /не может сделать ход/);

    condition.can_recover = true;
    const event = { currentTarget: button };
    const request = button.onclick(event);
    assert.equal(button.disabled, true);
    event.currentTarget = null;
    rejectStart(new Error('Состояние изменилось'));
    await request;
    assert.equal(attempts, 1);
    assert.equal(button.disabled, false);
    assert.deepEqual(messages, ['Состояние изменилось']);
});
