const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { loadFixture, waitForDocument } = require('./durable_reload.browser.cjs');

module.exports = async ({ page, request, token, lobby, base, safeSession }) => {
    const { fixture } = loadFixture();
    const api = `/lobbies/${lobby.id}`;
    const location = await request(api + '/locations', {
        ...fixture, objects: undefined, world_tile_x: 7, world_tile_z: 8,
    }, token);
    await request(`${api}/locations/${location.id}/objects/batch`, { objects: fixture.objects }, token);
    const localHelmet = await request(api + '/templates', {
        name: 'Проверочная балаклава', category: 'helmet', price: 150, weight: 0.1, volume: 1,
        attributes: { soft_headwear: true, max_durability: 1 },
    }, token);
    const helmet = { id: localHelmet.id + 1_000_000 };
    const character = await request(api + '/characters', {
        name: 'Внешность без пересборки листа',
        data: { equipment: { backpack: { id: 'appearance-backpack', templateId: 900000, name: 'Тестовый рюкзак',
            category: 'backpack', attributes: { limit: 20 } } }, inventory: { pockets: [{ id: 'appearance-bag', name: 'Проверочный мешок',
            category: 'backpack', isContainer: true, quantity: 1, capacity: 10,
            contents: [{ id: 'appearance-nested', name: 'Вложенный предмет', quantity: 2 }] }], backpack: [] } },
    }, token);
    await request(`${api}/locations/${location.id}/spawn_character`, {
        character_id: character.id, tile_x: 41, tile_y: 48,
    }, token);
    await page.call('Emulation.setDeviceMetricsOverride', { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
    const href = `${base}${api}/page?location=${location.id}`;
    const epoch = await page.evaluate('performance.timeOrigin');
    await page.call('Page.navigate', { url: href });
    await waitForDocument(page, { href, afterEpoch: epoch, label: 'Припять для внешности' });
    await page.until(`window.currentLocationId===${location.id}&&window.locationControls&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`, 'Припять загружена');
    await page.evaluate(`window.openCharacterSheet(${character.id}, 'equipment')`);
    const panel = '#sheet-tab-equipment';
    const select = key => `${panel} [name="appearance.${key}"]`;
    const click = async selector => {
        await page.until(`document.querySelector(${JSON.stringify(selector)})?.getClientRects().length`, 'Элемент листа виден');
        const point = await page.evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.scrollIntoView({block:'nearest'});const r=e.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
        for (const type of ['mousePressed', 'mouseReleased']) {
            await page.call('Input.dispatchMouseEvent', { type, ...point, button: 'left', buttons: type === 'mousePressed' ? 1 : 0, clickCount: 1 });
        }
    };
    await click(`${panel} [data-panel="appearance"]`);
    await page.until(`document.querySelector('${panel} [data-character-appearance-preview] canvas')`, 'Модель загружена');
    await page.evaluate(`(()=>{
        window.appearanceCanvas=document.querySelector('${panel} [data-character-appearance-preview] canvas');
        window.appearanceSelect=document.querySelector('${select('top')}');
        window.sheetScroll=document.querySelector('${panel}');sheetScroll.scrollTop=24;
        window.sheetScrollBefore=sheetScroll.scrollTop;window.appearanceMutations=0;
        window.appearanceObserver=new MutationObserver(rows=>appearanceMutations+=rows.filter(r=>[...r.removedNodes].some(n=>n===appearanceCanvas||n.contains?.(appearanceCanvas))).length);
        appearanceObserver.observe(document.querySelector('${panel}'),{childList:true,subtree:true});
        const original=window.fetch;window.fetch=async(...args)=>{
            if(String(args[0]).endsWith('/characters/${character.id}')&&args[1]?.method==='PUT'&&!window.appearanceRequestHeld){
                window.appearanceRequestHeld=true;await new Promise(resolve=>window.releaseAppearanceRequest=resolve);
            }return original(...args);
        };window.restoreAppearanceFetch=()=>window.fetch=original;
    })()`);
    const choose = async (key, value) => page.evaluate(`(()=>{
        const e=document.querySelector('${select(key)}');const started=performance.now();
        e.value=${JSON.stringify(value)};e.dispatchEvent(new Event('change',{bubbles:true}));
        return performance.now()-started;
    })()`);
    const latency = [await choose('top', 'gorka')];
    await page.until('Boolean(window.releaseAppearanceRequest)', 'Первый ответ задержан');
    latency.push(await choose('bottom', 'gorka'));
    latency.push(await choose('hair', 'bald'));
    latency.push(await choose('expression', 'wounded'));
    await page.evaluate('releaseAppearanceRequest();restoreAppearanceFetch()');
    const saved = async expected => page.until(`fetch('/lobbies/characters/${character.id}',{headers:{Authorization:'Bearer '+localStorage.getItem('access_token')}})
        .then(r=>r.json()).then(sheet=>${JSON.stringify(expected)}.every(([key,value])=>sheet.data.appearance[key]===value))`, 'Все изменения внешности сохранены');
    await saved([['top', 'gorka'], ['bottom', 'gorka'], ['hair', 'bald'], ['expression', 'wounded']]);
    latency.push(await choose('top', 'sweater'));
    latency.push(await choose('sweaterColor', 'blue'));
    latency.push(await choose('bottom', 'jeans'));
    latency.push(await choose('jeansColor', 'green'));
    await saved([['top', 'sweater'], ['sweaterColor', 'blue'], ['bottom', 'jeans'], ['jeansColor', 'green']]);
    await page.evaluate('new Promise(resolve=>setTimeout(resolve,700))');
    const stable = await page.evaluate(`({canvas:appearanceCanvas===document.querySelector('${panel} canvas'),
        select:appearanceSelect===document.querySelector('${select('top')}'),mutations:appearanceMutations,
        scrollBefore:sheetScrollBefore,scrollAfter:sheetScroll.scrollTop})`);
    assert.equal(stable.canvas, true);
    assert.equal(stable.select, true);
    assert.equal(stable.mutations, 0);
    assert.equal(stable.scrollBefore, stable.scrollAfter);
    // Изменение другим клиентом приходит штатным сетевым событием и сохраняет предпросмотр.
    const fresh = await request(`/lobbies/characters/${character.id}`, null, token, 'GET');
    fresh.data.appearance.hair = 'short';
    fresh.data.appearance.hairColor = 'blond';
    await request(`/lobbies/characters/${character.id}`, { data: fresh.data }, token, 'PUT');
    await page.until(`document.querySelector('${select('hair')}').value==='short'&&document.querySelector('${select('hairColor')}').value==='blond'`, 'Удалённая внешность обновилась');
    assert.equal(await page.evaluate(`appearanceCanvas===document.querySelector('${panel} canvas')`), true);
    const final = await request(`/lobbies/characters/${character.id}`, null, token, 'GET');
    assert.equal(final.data.inventory.pockets[0].contents[0].id, 'appearance-nested');
    assert.equal(final.data.inventory.pockets[0].contents[0].quantity, 2);
    fs.writeFileSync(path.join(safeSession.artifacts, 'sheet-appearance.png'), Buffer.from((await page.call('Page.captureScreenshot', { format: 'png' })).data, 'base64'));
    await page.call('Emulation.setDeviceMetricsOverride', { width: 1366, height: 600, deviceScaleFactor: 1, mobile: false });
    const scrollBefore = await page.evaluate(`new Promise(resolve=>requestAnimationFrame(()=>{
        sheetScroll.scrollTop=24;resolve(sheetScroll.scrollTop);
    }))`);
    assert.ok(scrollBefore > 0, 'Меньшая высота окна действительно требует прокрутки');
    await choose('expression', 'calm');
    await saved([['expression', 'calm']]);
    await page.evaluate('new Promise(resolve=>setTimeout(resolve,400))');
    assert.equal(await page.evaluate('sheetScroll.scrollTop'), scrollBefore);
    final.data.appearance.expression = 'calm';
    final.data.inventory.pockets[0].contents[0].quantity = 3;
    const beforeStructural = await request(`/lobbies/characters/${character.id}`, null, token, 'GET');
    final.data._revision = beforeStructural.data._revision;
    await request(`/lobbies/characters/${character.id}`, { data: final.data }, token, 'PUT');
    await page.until(`document.querySelector('[data-path="inventory,pockets,0,contents,0"] [data-field="quantity"]')?.value==='3'`, 'Предмет изменён другим клиентом');
    assert.equal(await page.evaluate(`appearanceCanvas===document.querySelector('${panel} canvas')`), true);
    const scrollAfter = await page.evaluate('sheetScroll.scrollTop');
    assert.equal(scrollAfter, scrollBefore, 'Прокрутка сохраняется после изменения вложенного предмета');

    await page.evaluate(`(()=>{
        window.inventoryPane=document.querySelector('#sheet-tab-inventory');
        window.pocketsList=document.querySelector('#pockets-container');
        window.backpackList=document.querySelector('#backpack-container');
        window.equipmentNav=document.querySelector('.equipment-panel-nav');
    })()`);
    await page.evaluate(`window.selectInventoryTemplate(${helmet.id})`);
    const read = () => request(`/lobbies/characters/${character.id}`, null, token, 'GET');
    const waitForSaved = async (predicate, label) => {
        for (let attempt = 0; attempt < 30; attempt++) {
            const { data } = await read();
            if (predicate(data)) return data;
            await new Promise(resolve => setTimeout(resolve, 120));
        }
        assert.fail(label);
    };
    const added = await waitForSaved(data => data.inventory.pockets.some(item => item.templateId === helmet.id), 'Добавление из каталога сохранено');
    const itemIndex = added.inventory.pockets.findIndex(item => item.templateId === helmet.id);
    const itemId = added.inventory.pockets[itemIndex].id;
    await page.evaluate(`(()=>{
        const transfer=new DataTransfer();
        transfer.setData('application/x-inventory-path',JSON.stringify(['inventory','pockets',${itemIndex}]));
        backpackList.dispatchEvent(new DragEvent('drop',{dataTransfer:transfer,bubbles:true,cancelable:true}));
    })()`);
    const moved = await waitForSaved(data => data.inventory.backpack.some(item => item.id === itemId), 'Перетаскивание в рюкзак сохранено');
    assert.ok(moved.inventory.pockets.every(item => item.id !== itemId));
    const backpackIndex = moved.inventory.backpack.findIndex(item => item.id === itemId);
    assert.equal(await page.evaluate(`pocketsList===document.querySelector('#pockets-container')
        &&backpackList===document.querySelector('#backpack-container')`), true);
    await page.evaluate(`window.equipHelmetFromInventory(['inventory','backpack',${backpackIndex}])`);
    await waitForSaved(data => data.equipment.helmet?.id === itemId, 'Балаклава надета');
    await page.until("document.querySelector('.loadout-slot[data-equipment-open=helmet]').classList.contains('is-equipped')", 'Слот шлема обновился');
    await page.evaluate('window.unequipHelmet()');
    const unequipped = await waitForSaved(data => !data.equipment.helmet?.templateId, 'Балаклава снята');
    assert.ok([...unequipped.inventory.pockets, ...unequipped.inventory.backpack].some(item => item.id === itemId));
    await page.until("document.querySelector('.loadout-slot[data-equipment-open=helmet]').classList.contains('is-empty')", 'Слот шлема очищен');
    assert.equal(await page.evaluate(`inventoryPane===document.querySelector('#sheet-tab-inventory')
        && equipmentNav===document.querySelector('.equipment-panel-nav')
        && appearanceCanvas===document.querySelector('${panel} canvas') && appearanceMutations===0`), true);
    assert.equal(await page.evaluate('sheetScroll.scrollTop'), scrollBefore);
    assert.equal(unequipped.inventory.pockets[0].contents[0].quantity, 3);
    fs.writeFileSync(path.join(safeSession.artifacts, 'sheet-appearance.json'), JSON.stringify({
        scene: fixture.name, objects: fixture.objects.length, stable, latencyMs: latency,
        slowSavePreservesChanges: true, remoteUpdate: true, nestedInventoryPreserved: true,
        addMoveEquipUnequip: true,
        shortWindowScroll: { before: scrollBefore, after: scrollAfter },
    }, null, 2));
    await page.evaluate('appearanceObserver.disconnect();window.closeCharacterSheet()');
    console.log('PASS: внешность в Припяти, быстрый ввод, задержанный ответ, прежний canvas, прокрутка и вложенные предметы');
};
