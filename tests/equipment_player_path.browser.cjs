// Каталожные предметы проходят выдачу, надевание, отказ и повтор через лист персонажа.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { waitForDocument } = require('./durable_reload.browser.cjs');

module.exports = async ({ page, request, token, lobby, offered, loc, base, connect, debugPort, safeSession, npcActorId }) => {
    const password = 'synthetic-equipment-player-password';
    await request('/auth/register', { username: 'equipment-player', email: 'equipment-player@example.test', password });
    const login = await request('/auth/login', { username: 'equipment-player', password });
    await request('/lobbies/' + lobby.id + '/join', {}, login.access_token);
    const debugUrl = 'http://127.0.0.1:' + debugPort;
    const version = await (await fetch(debugUrl + '/json/version')).json();
    const protocol = await connect(version.webSocketDebuggerUrl);
    const context = await protocol.call('Target.createBrowserContext');
    let peer;
    try {
        const tab = await protocol.call('Target.createTarget', { url: base, browserContextId: context.browserContextId });
        const target = (await (await fetch(debugUrl + '/json')).json()).find(row => row.id === tab.targetId);
        assert.ok(target);
        peer = await connect(target.webSocketDebuggerUrl);
        await peer.call('Page.enable');
        await peer.call('Runtime.enable');
        await waitForDocument(peer, { href: base, label: 'Отдельный профиль игрока' });
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(login.access_token)});
            localStorage.setItem('user_id',${JSON.stringify(String(login.user_id))});true`);
        const href = await page.evaluate('location.href');
        const epoch = await peer.evaluate('performance.timeOrigin');
        await peer.call('Page.navigate', { url: href });
        await waitForDocument(peer, { href, afterEpoch: epoch, label: 'Припять глазами игрока' });
        await peer.until('window.locationControls && window.socket?.connected && window.isGM===false', 'Подключён обычный игрок');
        await playPlayerPath({ page: peer, request, token: login.access_token, gmToken: token,
            userId: login.user_id, lobby, offered, loc, safeSession, npcActorId });
    } finally {
        await protocol.call('Target.disposeBrowserContext', { browserContextId: context.browserContextId }).catch(() => {});
        await peer?.close().catch(() => {});
        await protocol.close().catch(() => {});
    }
};

async function playPlayerPath({ page, request, token, gmToken, userId, lobby, offered, loc, safeSession, npcActorId }) {
    const api = '/lobbies/' + lobby.id;
    const findTemplate = (name, category) => {
        const template = offered.global.find(item => item.name === name && item.category === category);
        assert.ok(template, name);
        return template;
    };
    const armor = findTemplate('Комбинезон Купол-М', 'armor');
    const helmet = findTemplate('Шлем Волк-А', 'helmet');
    const artifact = findTemplate('Хозяин свечения', 'artifact');
    const thought = findTemplate('Ядро мысли', 'artifact');
    const storage = findTemplate('Контейнер для артефактов R-1', 'container');
    const unlimited = findTemplate('Контейнер для артефактов СОР-5', 'container');
    const barrier = findTemplate('Барьер', 'container');
    const bag = offered.global.filter(item => item.category === 'backpack')
        .sort((a, b) => (b.attributes?.limit || b.volume) - (a.attributes?.limit || a.volume))[0];
    assert.ok(bag);
    const character = await request(api + '/characters', { name: 'Проверка экипировки и радиации',
        data: { equipment: { backpack: {
            id: 'player-path-backpack', templateId: bag.id, name: bag.name, category: bag.category,
            attributes: bag.attributes, limit: bag.attributes?.limit || bag.volume,
            weight: bag.weight, volume: bag.volume, quantity: 1,
        } }, health: { current: 700, radiation: 0 }, inventory: { pockets: [], backpack: [] } },
    }, token);
    const sheetUrl = '/lobbies/characters/' + character.id;
    const sheet = async () => (await request(sheetUrl, null, token, 'GET')).data;
    const open = tab => page.evaluate(`window.openCharacterSheet(${character.id},${JSON.stringify(tab)}).then(()=>true)`);
    const selector = value => `document.querySelector(${JSON.stringify(value)})`;
    const click = async value => {
        const node = selector(value);
        await page.until(`${node}?.checkVisibility()`, 'Видна кнопка ' + value);
        await page.evaluate(`${node}.click();true`);
    };
    const closePicker = async () => {
        const close = selector('#inventory-template-picker-modal .close');
        await page.evaluate(`if (${close}?.checkVisibility()) ${close}.click();true`);
    };
    const add = async template => {
        await open('inventory');
        await click('#sheet-tab-inventory [data-widget-block="backpack"] button[onclick^="openInventoryTemplatePicker"]');
        await page.until("document.getElementById('inventory-template-picker-search')?.checkVisibility()", 'Виден поиск каталога');
        await page.evaluate(`(()=>{const search=document.getElementById('inventory-template-picker-search');
            search.value=${JSON.stringify(template.name)};search.dispatchEvent(new Event('input'));return true})()`);
        await click(`#inventory-template-picker-content button[data-item-template-id="${template.id}"]`);
        await page.until(`(async()=>{const {Server}=await import('/static/js/api.js');
            const s=await Server.getCharacter(${character.id});return s.data.inventory.backpack.some(i=>i.templateId===${template.id});})()`,
        'Предмет добавлен из каталога и сохранён');
        await closePicker();
    };
    const row = template => `#backpack-container > [data-item-template-id="${template.id}"]`;
    const equip = async template => {
        await open('inventory');
        await click(row(template) + ' button[data-widget-key="equip"]');
    };
    const armorPanel = '[data-equipment-open="armor"]';
    const helmetPanel = '[data-equipment-open="helmet"]';
    const waitSlot = (panel, template) => page.until(`${selector(panel)}?.textContent.includes(${JSON.stringify(template.name)})`,
        'В листе появился предмет ' + template.name);
    const screenshot = async name => fs.writeFileSync(path.join(safeSession.artifacts, name + '.png'),
        Buffer.from((await page.call('Page.captureScreenshot', { format: 'png' })).data, 'base64'));
    const dose = (value, delay) => page.until(`[...document.querySelectorAll('#effects-container .health-effect-card')].some(card=>
            card.textContent.includes(${JSON.stringify(artifact.name)})&&card.textContent.includes('Радиация +${value}')&&
            card.textContent.includes('Через ${delay} мин игрового времени')&&!card.querySelector('details'))`,
        'В листе видны доза и срок начисления');
    const minutes = async count => {
        const now = await request(api, null, token, 'GET');
        const target = now.game_day * 1440 + now.game_time_minutes + count;
        await request(api + '/time', { game_day: Math.floor(target / 1440), game_time_minutes: target % 1440 }, gmToken, 'PATCH');
    };
    const placement = await request(loc + '/spawn_character', {
        character_id: character.id, tile_x: 43, tile_y: 48, assign_to_user_id: userId,
    }, gmToken);
    await add(helmet);
    await add(armor);
    await equip(helmet);
    await waitSlot(helmetPanel, helmet);
    await equip(armor);
    const warning = '[data-armor-equip-error][role="alert"]';
    await page.until(`${selector(warning)}?.checkVisibility() && ${selector(warning)}.textContent.includes('Сначала снимите надетый шлем')`,
        'Причина отказа видна в листе');
    const rejected = await sheet();
    assert.equal(rejected.equipment.helmet.templateId, helmet.id);
    assert.ok(!rejected.equipment.armor?.templateId);
    assert.ok(rejected.inventory.backpack.some(item => item.templateId === armor.id));
    await screenshot('integrated-helmet-warning');

    await open('equipment');
    await click(helmetPanel);
    await click('[data-equipment-group="helmet"] button[onclick="unequipHelmet()"]');
    await page.until(`!${selector(helmetPanel)}?.textContent.includes(${JSON.stringify(helmet.name)})`, 'Отдельный шлем снят');
    await equip(armor);
    await waitSlot(armorPanel, armor);
    await open('equipment');
    await click(armorPanel);
    const worn = await sheet();
    assert.deepEqual([worn.equipment.armor.durability, worn.equipment.armor.currentStageDurability,
        worn.equipment.armor.material, worn.equipment.armor.containers.length], [75, 375, 'Текстиль', 3]);
    assert.deepEqual([worn.equipment.helmet.accuracyPenalty, worn.equipment.helmet.charismaBonus], [3, -2]);
    assert.equal(await page.evaluate("document.querySelectorAll('.armor-containers-row > div').length"), 3);

    await add(thought);
    const instance = (await sheet()).inventory.backpack.find(item => item.templateId === thought.id);
    assert.deepEqual([thought.price, instance.weight, instance.volume], [21500, 2, 1]);
    const choose = async (select, name, confirm) => {
        await page.until(`[...(${selector(select)}?.options || [])].some(option=>option.textContent.includes(${JSON.stringify(name)}))`,
            'Предмет доступен в списке');
        await page.evaluate(`(()=>{const select=${selector(select)};select.value=[...select.options]
            .find(option=>option.textContent.includes(${JSON.stringify(name)})).value;return true})()`);
        await click(confirm);
    };
    await open('equipment');
    await click(armorPanel);
    await click('.armor-containers-row button[onclick="addItemToArmorContainer(0)"]');
    await choose('#armor-container-select', thought.name, '#confirm-armor-container');
    await page.until(`document.querySelector('.armor-containers-row')?.textContent.includes(${JSON.stringify(thought.name)})`,
        'Артефакт появился в слоте');
    await click('.armor-containers-row button[onclick="removeArmorContainerItem(0)"]');
    await page.until(`!document.querySelector('.armor-containers-row')?.textContent.includes(${JSON.stringify(thought.name)})`,
        'Артефакт извлечён');
    await page.until(`(async()=>{const {Server}=await import('/static/js/api.js');
        const s=await Server.getCharacter(${character.id});return s.data.inventory.backpack.some(i=>i.id===${JSON.stringify(instance.id)});})()`,
    'Извлечение сохранено с тем же экземпляром');
    const href = await page.evaluate('location.href'), epoch = await page.evaluate('performance.timeOrigin');
    await page.call('Page.reload', { ignoreCache: true });
    await waitForDocument(page, { href, afterEpoch: epoch, label: 'Инвентарь после перезагрузки' });
    await page.until('window.locationControls && window.socket?.connected', 'Припять загружена');
    await open('inventory');
    assert.ok((await sheet()).inventory.backpack.some(item => item.id === instance.id));

    await add(artifact);
    await add(storage);
    await add(unlimited);
    await open('basic');
    await dose(5, 10);
    await page.evaluate(`document.querySelector('#effects-container .health-effect-card')?.scrollIntoView({block:'center'});true`);
    await screenshot('inventory-radiation');
    await minutes(9);
    await dose(5, 1);
    assert.equal((await sheet()).health.radiation, 0);
    await minutes(1);
    assert.equal((await sheet()).health.radiation, 5);

    await open('equipment');
    await click(armorPanel);
    await click('.armor-containers-row button[onclick="addItemToArmorContainer(0)"]');
    assert.ok(!(await page.evaluate("document.querySelector('#armor-container-select').textContent")).includes(storage.name));
    await click('#armor-container-select-modal .close');
    const install = async template => {
        await open('inventory');
        await click(row(template) + ' button[onclick^="window.installModuleFromSlot"]');
        await choose('#universal-module-select', artifact.name, '#confirm-universal-module');
        await page.until(`${selector(row(template))}?.querySelector('button[onclick^="window.uninstallModuleFromSlot"]')`,
            'Вложенный артефакт отображается в слоте');
    };
    await install(storage);
    await open('basic');
    await dose(2, 10);
    await minutes(10);
    assert.equal((await sheet()).health.radiation, 7);
    await open('inventory');
    await click(row(storage) + ' button[onclick^="window.uninstallModuleFromSlot"]');
    await page.until(`${selector(row(storage))}?.querySelector('button[onclick^="window.installModuleFromSlot"]')`, 'Артефакт извлечён из R-1');
    await install(unlimited);
    await open('basic');
    assert.ok(!(await page.evaluate("document.querySelector('#effects-container').textContent")).includes(artifact.name));
    await minutes(10);
    const final = await sheet();
    assert.equal(final.health.radiation, 7);
    const stored = final.inventory.backpack.find(item => item.templateId === unlimited.id);
    assert.equal(stored.installedModules[0].templateId, artifact.id);

    await open('inventory');
    await click(row(unlimited) + ' button[onclick^="window.uninstallModuleFromSlot"]');
    await page.until(`${selector(row(unlimited))}?.querySelector('button[onclick^="window.installModuleFromSlot"]')`, 'Артефакт извлечён из СОР-5');
    await add(barrier);
    await open('equipment');
    await click(armorPanel);
    await click('.armor-containers-row button[onclick="addItemToArmorContainer(0)"]');
    await choose('#armor-container-select', barrier.name, '#confirm-armor-container');
    await page.until(`(async()=>{const {Server}=await import('/static/js/api.js');const s=await Server.getCharacter(${character.id});
        return s.data.equipment.armor.containers[0].item?.templateId===${barrier.id};})()`, 'Барьер закреплён на броне');
    const combat = loc + '/combat';
    const actorId = placement.location_character_id;
    const beforeCombat = await request(combat + '/start', {
        location_character_ids: [actorId, npcActorId], initiator_location_character_id: actorId, initiator_first: true,
    }, gmToken);
    const beforePoints = beforeCombat.current_character.action_points_current;
    assert.ok(beforePoints >= 4 && beforePoints < 12);
    const mounted = '.armor-containers-row [data-widget-slots="equipment.armor.containers.0.item"]';
    await click(mounted + ' button[onclick^="window.installModuleFromSlot"]');
    await choose('#universal-module-select', artifact.name, '#confirm-universal-module');
    const extractionButton = mounted + ' button[onclick^="window.uninstallModuleFromSlot"]';
    await page.until(`${selector(extractionButton)}?.checkVisibility()`, 'Артефакт вложен в Барьер в бою');
    const installedCombat = await request(combat, null, token, 'GET');
    assert.equal(beforePoints - installedCombat.current_character.action_points_current, 4);
    await click(extractionButton);
    const remaining = 8 - (beforePoints - 4);
    await page.until(`window.locationCombatState?.characters?.some(actor=>actor.location_character_id===${actorId}
        &&actor.deferred_action?.remaining_action_points===${remaining})`, 'Бой получил состояние оплаты извлечения');
    const paying = await request(combat, null, token, 'GET');
    assert.equal(paying.current_character.location_character_id, npcActorId);
    const payingActor = paying.characters.find(actor => actor.location_character_id === actorId);
    assert.equal(payingActor.deferred_action.remaining_action_points, remaining);
    const extractId = payingActor.deferred_action.id;
    const hud = '#combat-status-panel';
    if (await page.evaluate(`${selector(hud)}?.dataset.collapsed === 'true'`)) await click(hud + ' .combat-hud-collapse-btn');
    const debt = hud + ` .combat-deferred-cancel[data-action="${extractId}"]`;
    await page.until(`${selector(debt)}?.checkVisibility()&&${selector(debt)}.parentElement.textContent.includes('Осталось оплатить: ${remaining} ОД')`,
        'Игрок видит точный остаток оплаты извлечения');
    assert.ok((await sheet()).equipment.armor.containers[0].item.installedModules.some(item => item.templateId === artifact.id));
    const turns = Math.ceil(remaining / payingActor.action_points_max);
    for (let i = 0; i < turns; i++) await request(combat + '/end_turn', { location_character_id: npcActorId }, gmToken);
    await page.until(`(async()=>{const {Server}=await import('/static/js/api.js');const s=await Server.getCharacter(${character.id});
        return s.data.inventory.backpack.some(i=>i.id===${JSON.stringify(stored.installedModules[0].id)});})()`, 'После доплаты возвращён тот же артефакт');
    const settled = await request(combat, null, token, 'GET');
    assert.notEqual(settled.current_character.deferred_action?.id, extractId);
    await request(combat + '/end', {}, gmToken);
    fs.writeFileSync(path.join(safeSession.artifacts, 'equipment-player-path.json'), JSON.stringify({
        warning: true, armor: { durability: 75, material: 'Текстиль', slots: 3, accuracy: -3, charisma: -2 },
        thought: { price: thought.price, weight: instance.weight, volume: instance.volume, identityRetained: true },
        radiation: { loose: 5, storageR1: 2, storageSor5: 0 },
        barrier: { installActionPoints: 4, extractActionPoints: 8, paidOnFollowingTurns: turns },
    }, null, 2));
    await page.evaluate('window.closeCharacterSheet();true');
}
