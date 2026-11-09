const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const WIDGET_SKILL_VALUES_EXPRESSION = `({base:document.querySelector('#sheet-content [name="skills.physical.will.base"]').value,bonus:document.querySelector('#sheet-content [name="skills.physical.will.bonus"]').value})`;

module.exports = async ({ page, request, token, lobby, login, profile, base, connect, debugPort, safeSession, focusRegression=false }) => {
    const artifactDir = safeSession ? safeSession.artifacts : profile;
    const template = await request(`/lobbies/${lobby.id}/templates`, { name: 'Widget pistol', category: 'weapon',
        subcategory: 'Пистолет', weight: 1, volume: 1, attributes: { fixedMagazine: true, magazine_size: 6, caliber: '9x19', damage: 25 } }, token);
    const medicine = await request(`/lobbies/${lobby.id}/templates`, { name: 'Проверочный препарат', category: 'consumable',
        weight: .1, volume: .1, attributes: { notes: 'Описание проверочного предмета', consumable: { direct: { pain: -1 } } } }, token);
    const character = await request(`/lobbies/${lobby.id}/characters`, { name: 'Виджеты — первый', data: {
        basic: { name: 'Виджеты — первый' }, health: { current: 100, max: 100, painLevel: 1,
            organs: { heart: { current: 1, max: 100 } }, effects: [{ id: 'widget-effect', type: 'custom', name: 'Проверочный эффект', value: 2, remaining: 3 }] },
        skills: { physical: { will: { base: 10, bonus: 0, xp: 0 }, strength: { base: 10, bonus: 0, xp: 0 } } },
        equipment: { headphones: { id: 'test-headphones', templateId: 999991, name: 'Наушники', deafeningCoef: .5 }, glasses: { id: 'test-glasses', templateId: 999992, name: 'Очки', charismaBonus: .2 }, gloves: { id: 'test-gloves', templateId: 999993, name: 'Перчатки', charismaBonus: .3 }, armor: { id: 'widget-armor', name: 'Widget armor', durability: 40, currentStageDurability: 20 }, backpack: { id: 'bag', name: 'Рюкзак', attributes: { capacity: 20 } } },
        weapons: [{ id: 'widget-pistol', name: 'Widget pistol', templateId: template.id + 1000000,
            type: 'firearm', ammo: 3, durability: 40, currentStageDurability: 20, fixedAmmo: [{ quantity: 3, name: '9x19', templateId: 999001 }] },
            { id: 'widget-knife', name: 'Нож', category: 'melee_weapon', attributes: { damage: 10, allowed_attacks: ['Колющий'], weight_class: 'Лёгкое' } }],
        inventory: { backpack: [], pockets: [{ id: 'widget-ammo', templateId: 999001, name: '9x19',
            category: 'ammo', quantity: 6, volume: .02, attributes: { caliber: '9x19', damage: 25 } },
            { id: 'widget-medicine', templateId: medicine.id + 1000000, name: 'Проверочный препарат', category: 'consumable', quantity: 3, volume: .1, contents: [],
                attributes: { consumable: { direct: { pain: -1 } } } },
            { id: 'widget-physical-pda-qa', name: 'КПК', category: 'misc', quantity: 1, weight: .3, volume: .5, contents: [] }] },
    } }, token);
    const other = await request(`/lobbies/${lobby.id}/characters`, { name: 'Виджеты — второй', data: {
        health: { current: 100, max: 100, painLevel: 2, effects: [] },
    } }, token);
    const key = `character-widgets:${lobby.id}:${login.user_id}`;
    const prefs = () => page.evaluate(`JSON.parse(localStorage.getItem(${JSON.stringify(key)})||'[]')`);
    const pinBlock = async (block, scope = '') => {
        const selector = `${scope || '#sheet-content'} .sheet-pin[data-pin-blocks~="${block}"]`;
        await page.until(`document.querySelector(${JSON.stringify(selector)})`, `pin affordance ${block}`);
        const wasPressed = await page.evaluate(`document.querySelector(${JSON.stringify(selector)}).getAttribute('aria-pressed')==='true'`);
        await page.evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
        await page.evaluate(`document.querySelector('.sheet-pin-menu [data-pin-block="${block}"]')?.click()`);
        assert.equal(await page.evaluate(`document.querySelector(${JSON.stringify(selector)}).getAttribute('aria-pressed')`), String(!wasPressed), `pin state for ${block}`);
        assert.equal(await page.evaluate(`document.querySelector(${JSON.stringify(selector)}).querySelector('path').getAttribute('d')`),
            wasPressed ? 'm8 3 8 0-1 7 4 4v2h-6v6h-2v-6H5v-2l4-4z' : 'm5 12 4 4L19 6', `pin/check icon for ${block}`);
    };
    const pinField = field => pinBlock(field === 'health.painLevel' ? 'health-status' : 'equipment:armor');
    const pinAction = text => pinBlock('skill:physical.will');
    const panelFor = async (id, kind, prop, value) => {
        const block = kind === 'field' ? value === 'health.painLevel' ? 'health-status' : 'equipment:armor'
            : value === 'rollSkill' ? 'skill:physical.will' : 'weapon';
        const pin = (await prefs()).find(pin => pin.characterId === id && pin.references[0]?.block === block);
        assert.ok(pin, `pin ${block}`); return '#character-widget-' + pin.id;
    };
    await page.evaluate(`window.openCharacterSheet(${character.id})`);
    await page.until(`document.body.classList.contains('character-sheet-open') && document.querySelectorAll('#sheet-content .sheet-pin').length>10`, 'sheet pin controls');
    assert.equal(await page.evaluate(`getComputedStyle(document.querySelector('.lobby-menu')).visibility==='hidden' && getComputedStyle(document.getElementById('panel-participants')).visibility==='hidden' && getComputedStyle(document.getElementById('panel-common-tools')).visibility==='hidden'`), true);
    const masteryKeys = await page.evaluate(`(async()=>Object.keys((await import('/static/js/characterWidgets.js')).weaponMasteryLabels))()`);
    const catalogueState = `(()=>{const root=document.getElementById('sheet-content');return{fields:[...root.querySelectorAll('[name]')].map(control=>control.name).filter(name=>name.startsWith('skills.specialized.')&&name.endsWith('.level')),blocks:[...root.querySelectorAll('.sheet-pin')].flatMap(button=>(button.dataset.pinBlocks||'').split(' ')).filter(Boolean)}})()`;
    try {
        await page.until(`(()=>{const state=${catalogueState};return ${JSON.stringify(masteryKeys)}.every(key=>state.fields.includes('skills.specialized.'+key+'.level')&&state.blocks.includes('weapon-mastery:'+key))})()`, 'семь специализаций и pin-кнопок готовы');
    } catch (error) {
        const state=await page.evaluate(catalogueState);
        fs.writeFileSync(path.join(artifactDir,'widget-catalogue-readiness.json'),JSON.stringify(state,null,2));
        throw error;
    }
    const physicalPdaId = 'widget-physical-pda-qa';
    const physicalPdaPath = '[data-path="inventory,pockets,2"]';
    assert.equal(await page.evaluate(`(()=>{
        const pda=document.querySelector('${physicalPdaPath}');
        return pda.querySelector('.item-name strong')?.textContent==='КПК'
            && !pda.querySelector('input[data-widget-key=name]')
            && Boolean(pda.querySelector('[data-widget-key=use]'));
    })()`),true,'У КПК без ссылки на шаблон есть подпись и кнопка использования');
    await pinBlock('inventory-item', physicalPdaPath);
    const physicalPdaPreference = (await prefs()).find(pin => pin.characterId === character.id
        && pin.references.length === 1 && pin.references[0].block === 'inventory-item'
        && pin.references[0].binding?.identity?.id === physicalPdaId);
    await page.evaluate('window.closeCharacterSheet()');
    const physicalPdaWidgetId = physicalPdaPreference?.id;
    if (physicalPdaWidgetId) await page.until(`document.querySelector('#character-widget-${physicalPdaWidgetId} .widget-inventory-name')`, 'виджет физического КПК появился');
    const physicalPdaWidget = physicalPdaWidgetId ? await page.evaluate(`(()=>{
        const panel=document.querySelector('#character-widget-${physicalPdaWidgetId}');
        const row=panel?.querySelector('.widget-inventory-name');
        const item=row?.closest('[data-widget-identity]');
        return {present:Boolean(panel),name:row?.value||row?.querySelector('input')?.value||row?.textContent?.trim()||null,
            identity:item?.dataset.widgetIdentity?JSON.parse(item.dataset.widgetIdentity):null,
            reference:JSON.parse(localStorage.getItem(${JSON.stringify(key)})||'[]').find(pin=>pin.id===${JSON.stringify(physicalPdaWidgetId)})?.references?.[0]||null};
    })()`) : null;
    if (physicalPdaWidgetId) {
        await page.evaluate(`document.querySelector('#character-widget-${physicalPdaWidgetId} .panel-header button[aria-label^="Открепить:"]')?.click()`);
        await page.until(`!document.querySelector('#character-widget-${physicalPdaWidgetId}')`, 'физический КПК откреплён штатной кнопкой');
    }
    const physicalPdaUnpinned = !(await prefs()).some(pin => pin.references[0]?.binding?.identity?.id === physicalPdaId);
    const physicalPdaProbe = { itemPath: 'inventory.pockets.2', itemId: physicalPdaId,
        preferenceReference: physicalPdaPreference?.references?.[0] || null, widget: physicalPdaWidget,
        unpinned: physicalPdaUnpinned };
    fs.writeFileSync(path.join(artifactDir, 'physical-pda-widget-probe.json'), JSON.stringify(physicalPdaProbe, null, 2));
    assert.ok(physicalPdaPreference, 'Закрепление КПК сохраняет ссылку на конкретный предмет инвентаря');
    assert.deepEqual(physicalPdaWidget?.identity, {id: physicalPdaId}, 'Виджет показывает КПК с тем же идентификатором');
    assert.equal(physicalPdaWidget?.name, 'КПК');
    assert.equal(physicalPdaWidget?.reference?.binding?.identity?.id, physicalPdaId);
    assert.equal(physicalPdaUnpinned, true, 'КПК откреплён штатной кнопкой виджета');
    const legacyPdaPin = {id:'legacy-pda-qa',characterId:character.id,title:'Модификации КПК',
        references:[{kind:'block',block:'equipment:pda',title:'Модификации КПК'}]};
    const widgetSheet = `/lobbies/characters/${character.id}`;
    const originalPda = (await request(widgetSheet,null,token,'GET')).data.inventory.pockets[2];
    const extraPda = {...originalPda,id:'another-physical-pda-qa'};
    const saveDevices = async (pockets, backpack) => {
        const fresh = await request(widgetSheet,null,token,'GET');
        if (pockets) fresh.data.inventory.pockets = pockets(fresh.data.inventory.pockets);
        fresh.data.inventory.backpack = backpack;
        await request(widgetSheet,{data:fresh.data},token,'PUT');
    };
    await saveDevices(null,[extraPda]);
    await page.evaluate(`(()=>{const key=${JSON.stringify(key)},pins=JSON.parse(localStorage.getItem(key)||'[]');
        const newValue=JSON.stringify([...pins,${JSON.stringify(legacyPdaPin)}]);
        localStorage.setItem(key,newValue);window.dispatchEvent(new StorageEvent('storage',{key,newValue}));return true;})()`);
    const legacyPanel = '#character-widget-legacy-pda-qa';
    await page.until(`document.querySelector('${legacyPanel}')?.textContent.includes('Закрепите нужный КПК')`,
        'При двух КПК старый виджет предлагает выбрать нужный');
    assert.equal((await prefs()).find(pin=>pin.id===legacyPdaPin.id).references[0].block,'equipment:pda');
    await saveDevices(null,[]);
    await page.until(`document.querySelector('${legacyPanel} .widget-inventory-name')`,'Единственный КПК привязан к старому виджету');
    assert.equal((await prefs()).find(pin=>pin.id===legacyPdaPin.id).references[0].binding.identity.id,physicalPdaId);
    await saveDevices(pockets=>pockets.filter(item=>item.id!==physicalPdaId),[extraPda]);
    await page.until(`document.querySelector('${legacyPanel}')?.textContent.includes('предмет недоступен')`,
        'Виджет не переключается на другое устройство после исчезновения первого');
    await saveDevices(pockets=>[...pockets,originalPda],[]);
    await page.until(`document.querySelector('${legacyPanel} .widget-inventory-name')`,'После возврата исходного КПК виджет снова доступен');
    await page.evaluate(`document.querySelector('${legacyPanel} .panel-header button').click()`);

    const lampId='inventory-reflow-light';
    const nestedId='inventory-reflow-pouch';
    const lamp={id:lampId,name:'Фонарик',category:'misc',quantity:1,weight:.2,volume:.25,
        attributes:{portable_light:'hand'},lightPower:{uses_left:6,elapsed_seconds:40}};
    const pouch={id:nestedId,name:'Подсумок для проверки',category:'pouch',quantity:1,weight:.2,volume:.2,
        attributes:{capacity:2},contents:[{id:'inventory-reflow-content',name:'Тестовые детали',
            category:'material',quantity:2,weight:.1,volume:.1}]};
    await saveDevices(pockets=>[...pockets,lamp,pouch],[]);
    await page.evaluate(`window.openCharacterSheet(${character.id},'inventory').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=inventory]')?.getAttribute('aria-selected')==='true'",
        'Старый переход открывает новый раздел инвентаря');
    assert.deepEqual(await page.evaluate(`(()=>{
        const nav=[...document.querySelectorAll('.equipment-panel-tab')].map(tab=>tab.textContent);
        return [Boolean(document.querySelector('#sheet-tabs [data-tab=inventory]')),nav[nav.indexOf('Защита')+1],nav.includes('Приборы')];
    })()`),[false,'Инвентарь',true]);
    for (const [width,height] of [[1366,768],[1920,1080]]) {
        await page.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
        assert.equal(await page.evaluate(`(()=>{
            const figure=document.querySelector('.loadout-figure').getBoundingClientRect();
            const pack=document.querySelector('[data-equipment-panel=inventory]').getBoundingClientRect();
            return figure.height>0&&figure.bottom<=innerHeight&&pack.left>figure.right&&pack.right<=innerWidth;
        })()`),true,`Инвентарь и силуэт помещаются при ${width}×${height}`);
        const columnWidths=await page.evaluate(`(()=>{
            const row=document.querySelector('#pockets-container .inventory-item-grid');
            return ['weight','volume','quantity'].map(key=>row.querySelector('[data-widget-key='+key+']').getBoundingClientRect().width);
        })()`);
        assert.ok(columnWidths[0]<=57&&columnWidths[1]<=57&&columnWidths[2]<=41,
            `Компактные числовые поля при ${width}×${height}: ${columnWidths.join(', ')}`);
        fs.writeFileSync(path.join(artifactDir,`equipment-inventory-${width}.png`),
            Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    }
    await page.call('Emulation.setDeviceMetricsOverride',{width:1366,height:768,deviceScaleFactor:1,mobile:false});
    assert.equal(await page.evaluate(`(()=>{
        const figure=document.querySelector('.loadout-figure'),sheet=document.querySelector('#sheet-tab-equipment');
        const inventory=document.querySelector('[data-equipment-panel=inventory]'),top=figure.getBoundingClientRect().top;
        inventory.scrollTop=inventory.scrollHeight;
        const pinned=inventory.scrollTop>0&&sheet.scrollTop===0&&figure.getBoundingClientRect().top===top;
        inventory.scrollTop=0;
        return pinned;
    })()`),true,'При прокрутке инвентаря левая схема и вкладки остаются на месте');
    await page.call('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false});
    await page.evaluate(`import('/static/js/characterSheet.js').then(module=>module.openCharacterAttackSheet(${character.id})).then(()=>true)`);
    assert.equal(await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=weapons]').getAttribute('aria-selected')"),'true');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=inventory]').click()");
    assert.equal(await page.evaluate(`(()=>{
        const row=[...document.querySelectorAll('#pockets-container [draggable=true][data-widget-identity]')]
            .find(el=>JSON.parse(el.dataset.widgetIdentity).id===${JSON.stringify(lampId)});
        const input=row.querySelector('input[data-widget-key=name]'),transfer=new DataTransfer();
        input.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0}));
        const drag=new DragEvent('dragstart',{bubbles:true,cancelable:true,dataTransfer:transfer});
        row.dispatchEvent(drag);
        document.dispatchEvent(new PointerEvent('pointerup',{bubbles:true,button:0}));
        return drag.defaultPrevented&&row.draggable&&transfer.types.length===0;
    })()`),true,'Нажатие на поле названия оставляет выделение текста');
    await page.evaluate(`(()=>{
        const source=[...document.querySelectorAll('#pockets-container [draggable=true][data-widget-identity]')]
            .find(el=>JSON.parse(el.dataset.widgetIdentity).id===${JSON.stringify(lampId)});
        const transfer=new DataTransfer();
        source.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:transfer}));
        document.querySelector('#backpack-container').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer}));
        return true;
    })()`);
    await page.until(`[...document.querySelectorAll('#backpack-container [data-widget-identity]')].some(el=>JSON.parse(el.dataset.widgetIdentity).id===${JSON.stringify(lampId)})`,
        'В пустой рюкзак положен фонарь перетаскиванием');
    await page.evaluate(`import('/static/js/api.js').then(({Server})=>Server.waitForCharacterSave(${character.id})).then(()=>true)`);
    const movedLamp=(await request(widgetSheet,null,token,'GET')).data.inventory;
    assert.equal(movedLamp.backpack[0].id,lampId);
    assert.equal(movedLamp.backpack[0].lightPower.uses_left,6);
    assert.equal(movedLamp.backpack[0].lightPower.elapsed_seconds,40);
    assert.equal(movedLamp.pockets.some(item=>item.id===lampId),false);
    assert.deepEqual(movedLamp.pockets.find(item=>item.id===nestedId).contents.map(item=>[item.id,item.quantity]),
        [['inventory-reflow-content',2]]);

    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=detector]').click()");
    const lightRow=`[data-equipment-panel="detector"] [data-light-id="${lampId}"]`;
    await page.until(`document.querySelector('${lightRow} [data-light-operation=prepare]:not(:disabled)')`,'Фонарём можно управлять из Приборов');
    await page.evaluate(`document.querySelector('${lightRow} [data-light-operation=prepare]').click()`);
    await page.until("document.querySelector('#equipment-hands-items')?.textContent.includes('Фонарик')",'Фонарь показан в руках');
    await page.until(`document.querySelector('${lightRow} [data-light-operation=stow]:not(:disabled)')`,'Фонарь можно убрать');
    await page.evaluate(`document.querySelector('${lightRow} [data-light-operation=stow]').click()`);
    await page.until("document.querySelector('#equipment-hands-items')?.textContent.includes('Руки свободны')",'Освобождение руки обновилось');
    await page.evaluate('window.closeCharacterSheet()');
    await saveDevices(pockets=>pockets.filter(item=>item.id!==nestedId),[]);
    await page.evaluate(`window.openCharacterSheet(${character.id})`);
    await page.until(`document.body.classList.contains('character-sheet-open') && document.querySelectorAll('#sheet-content .sheet-pin').length>10`, 'sheet restored after physical PDA probe');

    const agreed = await page.evaluate(`[...document.querySelectorAll('#sheet-content .sheet-pin')].flatMap(b=>b.dataset.pinBlocks.split(' '))`);
    const legacyPdaAvailable = agreed.includes('equipment:pda');
    fs.writeFileSync(path.join(artifactDir, 'legacy-pda-availability.json'), JSON.stringify({ legacyPdaAvailable, physicalPdaProbe }, null, 2));
    assert.equal(legacyPdaAvailable, false, 'КПК закрепляется по физическому экземпляру без общей панели модификаций');
    for (const block of ['health-pool', 'health-status', 'blood-check', 'infection', 'needs', 'organs', 'health-summary', 'effects', 'effect', 'addictions', 'zone:head',
        'weapon-mastery:pistols', 'equipment:helmet', 'equipment:gasMask', 'equipment:armor', 'protection:armor', 'coverage:armor', 'modifications:armor',
        'equipment:detector', 'cosmetic:headphones', 'cosmetic:glasses', 'cosmetic:gloves', 'cosmetic:jewelry', 'inventory-weight', 'pockets', 'backpack', 'inventory-item', 'money', 'notes']) {
        assert.ok(agreed.includes(block), `agreed block ${block}`);
    }
    assert.equal(agreed.includes('loadout'), false);
    assert.equal(agreed.includes('inventory-actions'), false);
    assert.equal(agreed.includes('inventory-container'), false);
    assert.equal(agreed.includes('inventory-magazine'), false);
    assert.equal(agreed.includes('weapon-mastery'), false);
    assert.equal(agreed.filter(block => block.startsWith('weapon-mastery:')).length, 7);
    assert.equal(await page.evaluate(`document.querySelector('[data-path="inventory,pockets,1"] .sheet-pin').dataset.pinBlocks`), 'inventory-item');
    assert.equal(await page.evaluate(`document.querySelectorAll('[name^="basic."] + .sheet-pin,[name^="health.organs."] + .sheet-pin,[name="skills.skillPoints"] + .sheet-pin').length`), 0);
    for (const block of ['blood-check', 'infection', 'zone:head', 'cosmetic:glasses', 'effects', 'money']) {
        await pinBlock(block);
        await pinBlock(block);
    }
    // Exercise the blocks which previously could not be pinned, then remove them
    // to keep the later layout/permissions regression compact.
    await pinBlock('organs'); await pinBlock('effect');
    await pinBlock('inventory-item', '[data-path="inventory,pockets,1"]');
    assert.equal(await page.evaluate(`document.querySelector('[data-path="inventory,pockets,1"] .sheet-pin').getAttribute('aria-pressed')`), 'true');
    assert.equal(await page.evaluate(`document.querySelector('[data-path="inventory,pockets,1"] .sheet-pin path').getAttribute('d')`), 'm5 12 4 4L19 6');
    await pinBlock('weapon', '[data-weapon-index="1"]');
    const extraPrefs = await prefs();
    const extra = block => '#character-widget-' + extraPrefs.find(pin => pin.references[0].block === block).id;
    await page.evaluate('window.closeCharacterSheet()');
    await page.until(`document.querySelector('${extra('effect')} [data-widget-control="field:$item.value"]') && document.querySelector('${extra('organs')} .zone-item-vertical')`, 'effect and damaged organs projected');
    assert.equal(await page.evaluate(`document.querySelectorAll('${extra('organs')} .zone-item-vertical').length`), 1);
    await page.until(`document.querySelector('${extra('weapon')}')?.textContent.includes('Колющий')`, 'melee widget projected');
    await page.evaluate(`(()=>{const i=document.querySelector('${extra('effect')} [data-widget-control="field:$item.value"]');i.value=4;i.dispatchEvent(new Event('change',{bubbles:true}))})()`);
    await page.until(`document.querySelector('${extra('effect')} [data-widget-control="field:$item.value"]')?.value==='4' && !document.querySelector('${extra('effect')} input').disabled`, 'individual effect saved');
    assert.equal((await request(`/lobbies/characters/${character.id}`, null, token, 'GET')).data.health.effects.find(e => e.id === 'widget-effect').value, 4);
    const itemPanel = extra('inventory-item');
    assert.deepEqual(await page.evaluate(`[...document.querySelectorAll('${itemPanel} .widget-inventory-field > span')].map(e=>e.textContent)`), ['Вес, кг','Объём','Количество']);
    assert.ok(await page.evaluate(`document.querySelector('${itemPanel}').getBoundingClientRect().width >= 600`));
    assert.equal(await page.evaluate(`(()=>{const p=document.querySelector('${itemPanel} .panel-content');return p.scrollWidth<=p.clientWidth+1})()`), true);
    assert.equal(await page.evaluate(`[...document.querySelectorAll('${itemPanel} input[type="number"]')].every(input=>input.validity.valid)`), true, 'fractional item measurements do not trigger native invalid-value tooltip');
    await page.evaluate(`(()=>{const i=document.querySelector('${itemPanel} [data-widget-key="weight"]');i.value='0.35';i.dispatchEvent(new Event('change',{bubbles:true}))})()`);
    await page.until(`document.querySelector('${itemPanel} [data-widget-key="weight"]')?.value==='0.35' && !document.querySelector('${itemPanel} input').disabled`, 'fractional weight saved');
    assert.equal((await request(`/lobbies/characters/${character.id}`, null, token, 'GET')).data.inventory.pockets[1].weight, .35);
    fs.writeFileSync(path.join(artifactDir, 'inventory-widget-fields.png'), Buffer.from((await page.call('Page.captureScreenshot', { format: 'png' })).data, 'base64'));
    await page.evaluate(`document.querySelector('${itemPanel} [data-widget-control$=":quantity-up"]').click()`);
    await page.until(`document.querySelector('${itemPanel} [data-widget-control$=":quantity"]')?.value==='4' && !document.querySelector('${itemPanel} input').disabled`, 'inventory quantity through original closure');
    const movedItem = await request(`/lobbies/characters/${character.id}`, null, token, 'GET');
    movedItem.data.inventory.backpack.push(movedItem.data.inventory.pockets.splice(1,1)[0]);
    await request(`/lobbies/characters/${character.id}`, { data: movedItem.data }, token, 'PUT');
    await page.evaluate(`document.querySelector('${itemPanel} [data-widget-control$=":quantity-up"]').click()`);
    await page.until(`document.querySelector('${itemPanel} [data-widget-control$=":quantity"]')?.value==='5' && !document.querySelector('${itemPanel} input').disabled`, 'pinned item follows transfer to backpack');
    assert.equal((await request(`/lobbies/characters/${character.id}`, null, token, 'GET')).data.inventory.backpack[0].quantity, 5);
    await page.evaluate(`window.openCharacterSheet(${character.id})`);
    await page.until(`document.body.classList.contains('character-sheet-open')`, 'sheet to pin backpack');
    await pinBlock('backpack');
    const backpackPin = (await prefs()).find(pin => pin.references[0].block === 'backpack');
    await page.evaluate('window.closeCharacterSheet()');
    await page.until(`document.querySelectorAll('#character-widget-${backpackPin.id} .widget-inventory-field').length===3`, 'backpack contains labeled item fields');
    assert.equal(await page.evaluate(`(()=>{const p=document.querySelector('#character-widget-${backpackPin.id} .panel-content');return p.scrollWidth<=p.clientWidth+1})()`), true);
    fs.writeFileSync(path.join(artifactDir, 'backpack-widget-fields.png'), Buffer.from((await page.call('Page.captureScreenshot', { format: 'png' })).data, 'base64'));
    await page.evaluate(`document.querySelector('#character-widget-${backpackPin.id} .panel-header button').click()`);
    for (const pin of extraPrefs) await page.evaluate(`document.querySelector('#character-widget-${pin.id} .panel-header button').click()`);
    await page.evaluate(`window.openCharacterSheet(${character.id})`);
    await page.until(`document.body.classList.contains('character-sheet-open')`, 'sheet for tooltip checks');
    const hover = async (selector, expected) => {
        await page.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`);
        await page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
        const point = await page.evaluate(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()`);
        await page.call('Input.dispatchMouseEvent', { type: 'mouseMoved', ...point });
        await page.until(`(()=>{const t=document.getElementById('delayed-item-tooltip');return t && getComputedStyle(t).display!=='none' && getComputedStyle(t).visibility==='visible' && t.textContent.includes(${JSON.stringify(expected)})})()`, 'visible hover tooltip');
    };
    await page.evaluate(`document.querySelector('[data-tab="equipment"]').click()`);
    await page.evaluate(`document.querySelector('.equipment-panel-tab[data-panel="weapons"]').click()`);
    await page.call('Emulation.setDeviceMetricsOverride', { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
    const loadoutLayout = await page.evaluate(`(()=>{
        const tab=document.querySelector('#sheet-tab-equipment');tab.scrollTop=0;
        const slots=[...tab.querySelectorAll('.equipment-loadout .loadout-slot')].map(node=>node.getBoundingClientRect());
        const figure=tab.querySelector('.loadout-figure').getBoundingClientRect();
        return {allSlotsVisible:slots.every(rect=>rect.top>=0&&rect.bottom<=innerHeight),
            figureVisible:figure.top>=0&&figure.bottom<=innerHeight,
            noVerticalScroll:tab.scrollHeight<=tab.clientHeight+2};
    })()`);
    assert.deepEqual(loadoutLayout,{allSlotsVisible:true,figureVisible:true,noVerticalScroll:true},'1920×1080: все слоты и силуэт видны без прокрутки');
    await page.until(`document.querySelector('[data-equipment-panel="detector"] .portable-light-row')`, 'КПК в разделе Приборы');
    const leftHeight = await page.evaluate(`document.querySelector('.equipment-loadout').getBoundingClientRect().height`);
    await page.evaluate(`document.querySelector('[data-equipment-panel="weapon-0"]').open=false`);
    const collapsedHeight = await page.evaluate(`document.querySelector('.equipment-loadout').getBoundingClientRect().height`);
    assert.ok(Math.abs(leftHeight-collapsedHeight)<2,'левая панель не меняет высоту при сворачивании оружия справа');
    await page.evaluate(`document.querySelector('[data-equipment-panel="weapon-0"]').open=true`);
    await page.evaluate(`document.querySelector('.loadout-slot[data-equipment-open="weapon-1"]').click()`);
    assert.deepEqual(await page.evaluate(`[...document.querySelectorAll('.loadout-weapon-details')].filter(node=>node.open).map(node=>node.dataset.equipmentPanel)`),
        ['weapon-1'],'слот ближнего боя сворачивает остальные карточки оружия');
    await page.evaluate(`document.querySelector('.loadout-slot[data-equipment-open="weapon-0"]').click()`);
    assert.deepEqual(await page.evaluate(`[...document.querySelectorAll('.loadout-weapon-details')].filter(node=>node.open).map(node=>node.dataset.equipmentPanel)`),
        ['weapon-0'],'слот основного оружия сворачивает остальные карточки');
    await page.evaluate(`document.querySelector('.loadout-slot[data-equipment-open="helmet"]').click()`);
    const protectionSections = `[...document.querySelectorAll('.loadout-protection-details[open]')].map(p=>p.dataset.equipmentPanel)`;
    assert.deepEqual(await page.evaluate(protectionSections),['helmet']);
    assert.equal(await page.evaluate(`document.querySelector('.equipment-panel-tab[data-panel="protection"]').getAttribute('aria-selected')`),'true');
    assert.equal(await page.evaluate(`Boolean(document.querySelector('select[name="equipment.helmet.templateId"]'))`),false);
    await page.evaluate(`document.querySelector('[data-equipment-panel="gasMask"] > summary').click()`);
    assert.deepEqual(await page.evaluate(protectionSections),['helmet','gasMask']);
    await page.evaluate(`document.querySelector('.loadout-slot[data-equipment-open="armor"]').click()`);
    assert.deepEqual(await page.evaluate(protectionSections),['armor']);
    await page.evaluate(`document.querySelector('.loadout-slot[data-equipment-open="helmet"]').click()`);
    const helmetLayout = await page.evaluate(`(()=>{
        const tab=document.querySelector('#sheet-tab-equipment');
        const fields=tab.querySelector('.helmet-main-block .fields-container');
        return {noHorizontalScroll:fields.scrollWidth<=fields.clientWidth+2,
            noVerticalScroll:tab.scrollHeight<=tab.clientHeight+2};
    })()`);
    assert.deepEqual(helmetLayout,{noHorizontalScroll:true,noVerticalScroll:true},'1920×1080: весь раздел шлема виден без прокрутки');
    fs.writeFileSync(path.join(artifactDir, 'equipment-protection-1920.png'), Buffer.from((await page.call('Page.captureScreenshot', {format:'png'})).data,'base64'));
    await page.evaluate(`document.querySelector('.equipment-panel-tab[data-panel="weapons"]').click()`);
    await page.call('Emulation.clearDeviceMetricsOverride');
    await page.evaluate(`document.querySelector('[data-equipment-panel="cosmetic"]').open=true;document.querySelector('[data-equipment-group="cosmetic"]').scrollIntoView({block:'center'})`);
    fs.writeFileSync(path.join(artifactDir, 'cosmetic-pin-buttons.png'), Buffer.from((await page.call('Page.captureScreenshot', { format: 'png' })).data, 'base64'));
    assert.equal(await page.evaluate(`(()=>{
        return ['headphones','glasses','gloves'].every(key=>{
            const pin=document.querySelector('.sheet-pin[data-pin-block="cosmetic:'+key+'"]');
            const remove=pin.previousElementSibling;
            const a=pin.getBoundingClientRect(),b=remove.getBoundingClientRect();
            return remove.textContent==='Снять' && pin.parentElement===remove.parentElement && a.left>=b.right && a.left-b.right<=8 && Math.abs(a.top+a.height/2-b.top-b.height/2)<2;
        });
    })()`), true, 'cosmetic unequip and pin buttons sit side by side');
    fs.writeFileSync(path.join(artifactDir, 'cosmetic-pin-buttons.png'), Buffer.from((await page.call('Page.captureScreenshot', { format: 'png' })).data, 'base64'));
    const collisions = await page.evaluate(`(()=>{
        const failures=[];
        for(const key of ['gasMask','armor']){
            document.querySelector('.loadout-slot[data-equipment-open="'+key+'"]').click();
            const names=['equipment:'+key,'protection:'+key,...(key==='armor'?['coverage:'+key]:[])];
            const rects=names.map(name=>document.querySelector('.sheet-pin[data-pin-block="'+name+'"]').getBoundingClientRect());
            for(let i=0;i<rects.length;i++)for(let j=i+1;j<rects.length;j++){
                const a=rects[i],b=rects[j];if(Math.min(a.right,b.right)>Math.max(a.left,b.left)&&Math.min(a.bottom,b.bottom)>Math.max(a.top,b.top)) failures.push(names[i]+' / '+names[j]);
            }
        }return failures;
    })()`);
    assert.deepEqual(collisions, [], 'equipment, protection and coverage pin buttons do not overlap');
    await hover('#sheet-content .loadout-slot[data-equipment-open="weapon-0"]', 'Widget pistol');
    await page.evaluate(`document.querySelector('.equipment-panel-tab[data-panel="inventory"]').click()`);
    await hover('#sheet-tab-inventory [data-item-template-id="' + (medicine.id + 1000000) + '"] strong', 'Описание проверочного предмета');
    fs.writeFileSync(path.join(artifactDir, 'inventory-hover-tooltip.png'), Buffer.from((await page.call('Page.captureScreenshot', { format: 'png' })).data, 'base64'));
    console.log('PASS: agreed block catalogue, damaged organs only, melee, effect edit, inventory quantity/transfer and visible equipment/item hover tooltips');

    if (safeSession) {
        // #51 использует уже существующий legacy-экземпляр и hover выше, без второго предмета.
        const readCharacter = () => request(`/lobbies/characters/${character.id}`, null, token, 'GET');
        const waitSaved = async (predicate, label) => {
            for (let attempt=0;attempt<150;attempt++) {
                const state=await readCharacter();
                if(predicate(state.data)) {
                    await page.evaluate(`(async()=>{const {Server}=await import('/static/js/api.js');await Server.waitForCharacterSave(${character.id})})()`);
                    const confirmed=await readCharacter();
                    if(predicate(confirmed.data))return confirmed.data;
                }
                await new Promise(resolve=>setTimeout(resolve,100));
            }
            assert.fail('Нет серверного подтверждения: '+label);
        };
        // Реальный ввод CDP: input/change имеют isTrusted для _manual_fields, в отличие от Event().
        const fillNative = async (selector, text) => {
            await page.until(`document.querySelector(${JSON.stringify(selector)})?.getClientRects().length`, 'поле ручного ввода');
            await page.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`);
            await page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
            const point=await page.evaluate(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
            for(const type of ['mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,...point,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1});
            await page.call('Input.dispatchKeyEvent',{type:'keyDown',key:'a',code:'KeyA',windowsVirtualKeyCode:65,modifiers:2});
            await page.call('Input.dispatchKeyEvent',{type:'keyUp',key:'a',code:'KeyA',windowsVirtualKeyCode:65,modifiers:0});
            await page.call('Input.insertText',{text:String(text)});
            for(const type of ['keyDown','keyUp'])await page.call('Input.dispatchKeyEvent',{type,key:'Tab',code:'Tab',windowsVirtualKeyCode:9,modifiers:0});
        };
        let regressionPeer;
        if(focusRegression){
            assert.equal(process.env.VTT_SAFE_SCENARIO,'sheet-repro');
            await request('/auth/register',{username:'sheet-repro-reader',email:'sheet-repro-reader@example.test',password:'synthetic-sheet-repro'});
            const reader=await request('/auth/login',{username:'sheet-repro-reader',password:'synthetic-sheet-repro'});
            await request(`/lobbies/${lobby.id}/join`,{},reader.access_token);
            await request(`/lobbies/characters/${character.id}/visibility`,{visible_to:[reader.user_id],editable_to:[]},token,'PUT');
            const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json(),protocol=await connect(version.webSocketDebuggerUrl);
            const context=await protocol.call('Target.createBrowserContext'),target=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
            const info=(await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json()).find(t=>t.id===target.targetId);assert.ok(info);
            regressionPeer=await connect(info.webSocketDebuggerUrl);await regressionPeer.call('Page.enable');await regressionPeer.call('Runtime.enable');
            await require('./durable_reload.browser.cjs').waitForDocument(regressionPeer,{href:base,label:'sheet reader origin'});
            await regressionPeer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(reader.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(reader.user_id))});true`);
            await regressionPeer.call('Page.navigate',{url:base+`/lobbies/${lobby.id}/page`});
            await regressionPeer.until(`typeof openCharacterSheet==='function'&&window.socket?.connected&&window.isGM===false`,'ordinary sheet reader');
            await regressionPeer.evaluate(`window.sheetReproEvents=[];socket.on('character_data_updated',p=>{if(p.character_id===${character.id})sheetReproEvents.push({source:p.source||null,data:p.updates?.data,transport:socket.io.engine.transport.name})});openCharacterSheet(${character.id});true`);
            await regressionPeer.until(`document.body.classList.contains('character-sheet-open')&&document.querySelector('#sheet-content [name="health.current"]')`,'reader sheet before edits');
            const ack=await regressionPeer.evaluate(`new Promise(resolve=>socket.emit('join_character',{character_id:${character.id},token:localStorage.getItem('access_token')},resolve))`);assert.equal(ack.ok,true);
        }
        const initial=(await readCharacter()).data;
        const legacy=[...initial.inventory.backpack,...initial.inventory.pockets].find(item=>item.id==='widget-medicine');
        assert.ok(legacy,'ранее добавленный legacy-предмет сохранён');
        const repro={cards:{item:'6ae4891464ddc8c03ef03fe1',skills:'6ae48917bc96d644e211e009',effects:'6ae4891e30cd7d53b3c9238f'},
            legacy:{id:legacy.id,templateId:legacy.templateId,hasInstanceNotes:Boolean(legacy.attributes?.notes)}, assertions:[]};
        await page.evaluate(`document.querySelector('[data-tab="skills"]').click()`);
        await fillNative('#sheet-content [name="skills.physical.will.base"]','14');
        await waitSaved(data=>data.skills.physical.will.base===14,'ручной base=14');
        await fillNative('#sheet-content [name="skills.physical.will.bonus"]','3');
        await waitSaved(data=>data.skills.physical.will.base===14&&data.skills.physical.will.bonus===3,'ручной bonus=3');
        repro.assertions.push('Ручные base/bonus сохранены на сервере');
        await page.evaluate(`document.querySelector('[data-tab="basic"]').click()`);
        const count=await page.evaluate(`document.querySelectorAll('#sheet-content [name^="health.effects."][name$=".name"]').length`);
        await page.evaluate(`document.querySelector('#sheet-content [onclick="addEffect()"]').click()`);
        await page.until(`document.querySelectorAll('#sheet-content [name^="health.effects."][name$=".name"]').length===${count+1}`, 'новый custom-эффект от кнопки');
        const name=await page.evaluate(`[...document.querySelectorAll('#sheet-content [name^="health.effects."][name$=".name"]')].at(-1).name`);
        const prefix=name.slice(0,-'.name'.length);
        const manualName='Ручной эффект REG-REPRO-A';
        await fillNative(`#sheet-content [name="${name}"]`,manualName);
        await fillNative(`#sheet-content [name="${prefix}.value"]`,'7');
        await fillNative(`#sheet-content [name="${prefix}.remaining"]`,'4');
        const saved=await waitSaved(data=>data.health.effects.some(effect=>effect.name===manualName&&effect.type==='custom'&&effect.value===7&&effect.remaining===4),'custom name/value/remaining');
        const manual=saved.health.effects.find(effect=>effect.name===manualName);
        assert.ok(typeof manual.id==='string'&&manual.id.length>0,'сервер сохранил стабильный ID custom-эффекта');
        const neighbor=saved.health.effects.findIndex(effect=>effect.id==='widget-effect');
        assert.ok(neighbor>=0,'соседний исходный эффект существует');
        await page.evaluate(`document.querySelector('#sheet-content [onclick="removeEffect(${neighbor})"]').click()`);
        const expected=data=>data.skills.physical.will.base===14&&data.skills.physical.will.bonus===3
            &&!data.health.effects.some(effect=>effect.id==='widget-effect')
            &&data.health.effects.filter(effect=>effect.id===manual.id&&effect.name===manualName&&effect.type==='custom'&&effect.value===7&&effect.remaining===4).length===1;
        await waitSaved(expected,'удалён сосед, custom ID/данные сохранены');
        const checkUI=async label=>{
            await page.evaluate(`document.querySelector('[data-tab="skills"]').click()`);
            assert.deepEqual(await page.evaluate(WIDGET_SKILL_VALUES_EXPRESSION),{base:'14',bonus:'3'},label);
            await page.evaluate(`document.querySelector('[data-tab="basic"]').click()`);
            assert.equal(await page.evaluate(`[...document.querySelectorAll('#sheet-content [name^="health.effects."][name$=".name"]')].filter(input=>input.value===${JSON.stringify(manualName)}).length`),1,label+' custom отображается один раз');
            assert.deepEqual(await page.evaluate(`(()=>{const input=[...document.querySelectorAll('#sheet-content [name^="health.effects."][name$=".name"]')].find(i=>i.value===${JSON.stringify(manualName)});const root=input.name.slice(0,-5);const value=suffix=>document.querySelector('#sheet-content [name="'+root+'.'+suffix+'"]').value;return{value:value('value'),remaining:value('remaining'),type:value('type')}})()`),{value:'7',remaining:'4',type:'custom'},label+' поля custom-эффекта');
            assert.ok(expected((await readCharacter()).data),label+' серверные значения/ID');
        };
        await page.evaluate('window.closeCharacterSheet()');
        await page.evaluate(`window.openCharacterSheet(${character.id})`);
        await page.until(`document.body.classList.contains('character-sheet-open')&&document.getElementById('character-sheet-name').textContent==='Виджеты — первый'`,'лист повторно открыт');
        await checkUI('повторное открытие');
        await page.evaluate('window.closeCharacterSheet();window.regReproBeforeReload=true');
        const oldEpoch=await page.evaluate('performance.timeOrigin');
        await page.call('Page.reload');
        await require('./durable_reload.browser.cjs').waitForDocument(page,{href:base+`/lobbies/${lobby.id}/page`,afterEpoch:oldEpoch,label:'sheet regression reload'});
        await page.until(`!window.regReproBeforeReload&&typeof window.openCharacterSheet==='function'&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'перезагрузка после сохранения');
        await page.evaluate(`window.openCharacterSheet(${character.id})`);
        await page.until(`document.body.classList.contains('character-sheet-open')&&document.getElementById('character-sheet-name').textContent==='Виджеты — первый'`,'лист после reload');
        await checkUI('перезагрузка');
        await page.evaluate(`document.querySelector('[data-tab="equipment"]').click();document.querySelector('.equipment-panel-tab[data-panel="inventory"]').click()`);
        await hover('#sheet-tab-inventory [data-item-template-id="'+(medicine.id+1000000)+'"] strong','Описание проверочного предмета');
        assert.ok([...((await readCharacter()).data.inventory.backpack),...((await readCharacter()).data.inventory.pockets)].some(item=>item.id===legacy.id),'тот же старый экземпляр после reload');
        if(regressionPeer){
            await regressionPeer.until(`sheetReproEvents.some(e=>e.data?.skills?.physical?.will?.base===14&&e.data?.skills?.physical?.will?.bonus===3&&e.data.health.effects.some(v=>v.id===${JSON.stringify(manual.id)}&&v.value===7&&v.remaining===4)&&!e.data.health.effects.some(v=>v.id==='widget-effect'))`,'actual reader synchronization event');
            await regressionPeer.until(`[...document.querySelectorAll('#sheet-content [name^="health.effects."][name$=".name"]')].some(n=>n.value===${JSON.stringify(manualName)})`,'reader effect without reopen/reload');
            repro.reader={events:await regressionPeer.evaluate('sheetReproEvents'),epoch:await regressionPeer.evaluate('performance.timeOrigin'),connected:await regressionPeer.evaluate('socket.connected')};
            fs.writeFileSync(path.join(artifactDir,'reg-repro-a-reader.png'),Buffer.from((await regressionPeer.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        }
        repro.manualEffectId=manual.id;
        repro.assertions.push('Custom-эффект создан кнопкой и изменён нативным вводом','Сосед удалён без изменения ID custom-эффекта','Base/bonus/custom сохранены после закрытия, открытия и reload','Описание того же legacy-предмета доступно после reload');
        fs.writeFileSync(path.join(artifactDir,'reg-repro-a.json'),JSON.stringify(repro,null,2));
        fs.writeFileSync(path.join(artifactDir,'reg-repro-a-hover.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    }

    if(focusRegression){
        return;
    }
    await pinField('health.painLevel');
    await pinField('equipment.armor.currentStageDurability');
    await pinAction("window.rollSkill('physical.will', 'Воля')");
    await pinBlock('weapon', '[data-weapon-index="0"]');
    const attackText = await page.evaluate(`[...document.querySelectorAll('[data-weapon-index="0"] [onclick]')].find(b=>b.getAttribute('onclick').startsWith("useWeaponFromEquipment(0, 'unaimed'"))?.getAttribute('onclick')`);
    assert.ok(attackText);
    await pinBlock('needs');
    await pinBlock('health-pool');
    await pinBlock('weapon-mastery:pistols');
    await pinBlock('notes');
    const savedCount = (await prefs()).length;
    assert.ok(savedCount >= 7);
    console.log('PASS: scalar, armor, skill, reload, attack and block pin references created');
    assert.equal(await page.evaluate(`getComputedStyle(document.querySelector('.character-widget')).visibility`), 'hidden');
    await page.evaluate(`window.closeCharacterSheet()`);
    await page.until(`document.querySelectorAll('.character-widget .widget-character-name').length===${savedCount}`, 'authorized widget values');
    assert.equal(await page.evaluate(`getComputedStyle(document.querySelector('.lobby-menu')).visibility`), 'visible');
    const masteryPin = (await prefs()).find(pin => pin.references[0].block === 'weapon-mastery:pistols');
    const masteryPanel = '#character-widget-' + masteryPin.id;
    await page.until(`document.querySelector('${masteryPanel} [data-widget-control*="addWeaponXp"]')`, 'weapon mastery projected');
    assert.equal(await page.evaluate(`document.querySelectorAll('${masteryPanel} [data-widget-control*="addWeaponXp"]').length`), 1, 'one mastery type per panel');
    await page.evaluate(`document.querySelector('${masteryPanel} [data-widget-control*="addWeaponXp"]').click()`);
    await page.until(`document.querySelector('${masteryPanel}').textContent.includes('Прогресс: 1/5') && !document.querySelector('${masteryPanel} button[data-widget-control]').disabled`, 'weapon mastery progress saved');
    assert.equal((await request(`/lobbies/characters/${character.id}`, null, token, 'GET')).data.skills.specialized.pistols.xp, 1);
    const notePin = (await prefs()).find(pin => pin.references[0].block === 'notes');
    await page.evaluate(`(()=>{const t=document.querySelector('#character-widget-${notePin.id} textarea');t.value='Проверочная заметка';t.dispatchEvent(new Event('change',{bubbles:true}))})()`);
    await page.until(`document.querySelector('#character-widget-${notePin.id} textarea')?.value==='Проверочная заметка' && !document.querySelector('#character-widget-${notePin.id} textarea').disabled`, 'note saved');
    assert.equal((await request(`/lobbies/characters/${character.id}`, null, token, 'GET')).data.notes, 'Проверочная заметка');
    const painPanel = await panelFor(character.id, 'field', 'path', 'health.painLevel');
    await page.evaluate(`(()=>{const i=document.querySelector('${painPanel} [data-widget-control="field:health.painLevel"]');i.value='3';i.dispatchEvent(new Event('change',{bubbles:true}))})()`);
    await page.until(`document.querySelector('${painPanel} [data-widget-control="field:health.painLevel"]')?.value==='3' && !document.querySelector('${painPanel} [data-widget-control="field:health.painLevel"]').disabled`, 'widget field saved through sheet');
    assert.equal((await request(`/lobbies/characters/${character.id}`, null, token, 'GET')).data.health.painLevel, 3);
    const skillPanel = await panelFor(character.id, 'action', 'name', 'rollSkill');
    await page.evaluate(`document.querySelector('${skillPanel} [data-widget-control^="click:window.rollSkill"]').click()`);
    await page.until(`[...document.querySelectorAll('#chat .message')].some(m=>m.textContent.includes('Воля: бросок'))`, 'skill uses existing roll and chat');
    const reloadPanel = await panelFor(character.id, 'action', 'name', 'reloadFixedMagazine');
    await page.evaluate(`document.querySelector('${reloadPanel} [data-widget-control*="reloadFixedMagazine"]').click()`);
    await page.until(`document.getElementById('fixed-reload-modal')?.getClientRects().length>0`, 'reload uses existing ammunition dialog');
    await page.evaluate(`document.getElementById('fixed-reload-modal').remove()`);
    await page.evaluate(`window.openCharacterSheet(${other.id})`);
    await page.until(`document.body.classList.contains('character-sheet-open') && document.getElementById('character-sheet-name').textContent==='Виджеты — второй'`, 'second character sheet');
    await pinField('health.painLevel');
    await page.evaluate(`window.closeCharacterSheet()`);
    const secondPanel = await panelFor(other.id, 'field', 'path', 'health.painLevel');
    await page.until(`document.querySelector('${secondPanel} [data-widget-control="field:health.painLevel"]') && !document.querySelector('${secondPanel} [data-widget-control="field:health.painLevel"]').disabled`, 'second widget loaded');
    await page.evaluate(`(()=>{const i=document.querySelector('${secondPanel} [data-widget-control="field:health.painLevel"]');i.value='5';i.dispatchEvent(new Event('change',{bubbles:true}))})()`);
    await page.until(`document.querySelector('${secondPanel} [data-widget-control="field:health.painLevel"]')?.value==='5' && !document.querySelector('${secondPanel} [data-widget-control="field:health.painLevel"]').disabled`, 'second character save');
    assert.equal((await request(`/lobbies/characters/${other.id}`, null, token, 'GET')).data.health.painLevel, 5);
    const updated = await request(`/lobbies/characters/${character.id}`, null, token, 'GET');
    updated.data.health.painLevel = 7;
    await request(`/lobbies/characters/${character.id}`, { data: updated.data }, token, 'PUT');
    await page.until(`document.querySelector('${painPanel} [data-widget-control="field:health.painLevel"]')?.value==='7'`, 'remote update of another pinned character');
    const reordered = await request(`/lobbies/characters/${character.id}`, null, token, 'GET');
    reordered.data.weapons.unshift({ id: 'other-pistol', name: 'Other pistol', templateId: template.id + 1000000, type: 'firearm', ammo: 1 });
    await request(`/lobbies/characters/${character.id}`, { data: reordered.data }, token, 'PUT');
    const armorPanel = await panelFor(character.id, 'field', 'path', 'equipment.armor.currentStageDurability');
    await page.evaluate(`(()=>{const i=document.querySelector('${armorPanel} [data-widget-control="field:$item.currentStageDurability"]');i.value='17';i.dispatchEvent(new Event('change',{bubbles:true}))})()`);
    await page.until(`document.querySelector('${armorPanel} [data-widget-control="field:$item.currentStageDurability"]')?.value==='17' && !document.querySelector('${armorPanel} [data-widget-control="field:$item.currentStageDurability"]').disabled`, 'armor uses existing field persistence');
    assert.equal((await request(`/lobbies/characters/${character.id}`, null, token, 'GET')).data.equipment.armor.currentStageDurability, 17);
    // A stored reload reference follows its weapon after a list reorder.
    await page.evaluate(`document.querySelector('${reloadPanel} [data-widget-control*="reloadFixedMagazine"]').click()`);
    await page.until(`document.getElementById('fixed-reload-modal')?.getClientRects().length>0`, 'reload follows weapon identity after reorder');
    await page.evaluate(`document.getElementById('fixed-reload-modal').remove()`);
    const attackPanel = await panelFor(character.id, 'action', 'name', 'useWeaponFromEquipment');
    await page.evaluate(`document.querySelector('${attackPanel} [data-widget-control*="useWeaponFromEquipment"]').click()`);
    await page.until(`document.querySelector('${attackPanel} [data-widget-control*="useWeaponFromEquipment"]')?.disabled===false`, 'existing weapon action completed');
    const afterShot = await request(`/lobbies/characters/${character.id}`, null, token, 'GET');
    assert.equal(afterShot.data.weapons.find(weapon => weapon.id === 'widget-pistol').ammo, 2);
    assert.equal(afterShot.data.weapons.find(weapon => weapon.id === 'other-pistol').ammo, 1);
    // Native drag and folding use the same panel position store as other room UI.
    const header = await page.evaluate(`(()=>{const r=document.querySelector('${painPanel} .panel-header').getBoundingClientRect();return {x:r.left+25,y:r.top+15}})()`);
    await page.call('Input.dispatchMouseEvent',{type:'mousePressed',...header,button:'left',buttons:1,clickCount:1});
    await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',x:header.x+40,y:header.y+20,button:'left',buttons:1});
    await page.call('Input.dispatchMouseEvent',{type:'mouseReleased',x:header.x+40,y:header.y+20,button:'left',buttons:0,clickCount:1});
    const position = await page.evaluate(`(()=>{const r=document.querySelector('${painPanel}').getBoundingClientRect();return {left:r.left,top:r.top}})()`);
    await page.evaluate(`document.querySelector('${painPanel} .toggle-btn').click();window.widgetsBeforeReload=true`);
    await page.call('Page.reload');
    await page.until(`!window.widgetsBeforeReload && document.querySelector('${painPanel} .widget-character-name') && document.getElementById('scene-loading-screen').classList.contains('is-hidden')`, 'pin preferences after reload');
    assert.equal(await page.evaluate(`document.querySelector('${painPanel}').classList.contains('collapsed')`), true);
    assert.deepEqual(await page.evaluate(`(()=>{const r=document.querySelector('${painPanel}').getBoundingClientRect();return {left:r.left,top:r.top}})()`), position);
    assert.equal((await prefs()).length, savedCount + 1);
    await page.evaluate(`document.querySelector('${painPanel} .toggle-btn').click();window.openCharacterSheet(${character.id})`);
    await page.until(`document.body.classList.contains('character-sheet-open')`, 'sheet reopened over widgets');
    assert.equal(await page.evaluate(`document.querySelector('[name="health.painLevel"]').value`), '7');
    const screenshot = path.join(artifactDir, 'character-sheet-pins.png');
    fs.writeFileSync(screenshot, Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    console.log('Sheet pins screenshot:', screenshot);
    await page.evaluate(`window.closeCharacterSheet();(async()=>{window.sceneModule=await import('/static/js/lobby3d.js');sceneModule.controls.target.set(31.5,1,10.5);sceneModule.camera.position.set(35,9,19);sceneModule.controls.update()})()`);
    await page.evaluate(`new Promise(resolve=>setTimeout(resolve,1000))`);
    const mapScreenshot = path.join(artifactDir, 'character-map-widgets.png');
    fs.writeFileSync(mapScreenshot, Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    console.log('Map widgets screenshot:', mapScreenshot);
    if (process.env.VTT_WIDGETS_FOCUSED === '1') {
        console.log('PASS: pinned checkmark, cosmetic button placement, separate mastery progress, decimal measurements, actions, realtime and reload persistence');
        return;
    }
    const version = await (await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();
    const protocol = await connect(version.webSocketDebuggerUrl);
    let player;
    try {
        await request('/auth/register', { username: 'widgets-viewer', email: 'widgets-viewer@example.test', password: 'widget-test-password' });
        const viewer = await request('/auth/login', { username: 'widgets-viewer', password: 'widget-test-password' });
        await request(`/lobbies/${lobby.id}/join`, {}, viewer.access_token);
        await request(`/lobbies/characters/${character.id}`, { visible_to: [viewer.user_id], editable_to: [] }, token, 'PUT');
        const context = await protocol.call('Target.createBrowserContext');
        const target = await protocol.call('Target.createTarget', { url: base, browserContextId: context.browserContextId });
        const pages = await (await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
        player = await connect(pages.find(item => item.id === target.targetId).webSocketDebuggerUrl);
        await player.call('Page.enable'); await player.call('Runtime.enable');
        await player.until(`location.origin===${JSON.stringify(base)}`, 'viewer origin');
        await player.evaluate(`localStorage.setItem('access_token',${JSON.stringify(viewer.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(viewer.user_id))})`);
        await player.call('Page.navigate', { url: `${base}/lobbies/${lobby.id}/page` });
        await player.until(`window.isGM===false && document.getElementById('character-widgets') && document.getElementById('scene-loading-screen').classList.contains('is-hidden')`, 'viewer room');
        assert.equal(await player.evaluate(`document.querySelectorAll('.character-widget').length`), 0, 'pins are personal');
        await player.evaluate(`window.openCharacterSheet(${character.id})`);
        await player.until(`document.querySelector('.sheet-pin[data-pin-block="health-status"]')`, 'read-only sheet pin');
        await player.evaluate(`document.querySelector('.sheet-pin[data-pin-block="health-status"]').click();window.closeCharacterSheet()`);
        await player.until(`document.querySelector('.character-widget [data-widget-control="field:health.painLevel"]')?.disabled===true`, 'read-only widget respects edit permissions');
        const gmPinCount = (await prefs()).length;
        await request(`/lobbies/characters/${character.id}`, { editable_to: [viewer.user_id] }, token, 'PUT');
        await player.until(`document.querySelector('.character-widget [data-widget-control="field:health.painLevel"]')?.disabled===false`, 'granted editor permission revalidated');
        await player.evaluate(`(()=>{const i=document.querySelector('.character-widget [data-widget-control="field:health.painLevel"]');i.value='9';i.dispatchEvent(new Event('change',{bubbles:true}))})()`);
        await player.until(`document.querySelector('.character-widget [data-widget-control="field:health.painLevel"]')?.value==='9' && !document.querySelector('.character-widget [data-widget-control="field:health.painLevel"]').disabled`, 'player widget edit saved');
        await page.until(`document.querySelector('${painPanel} [data-widget-control="field:health.painLevel"]')?.value==='9'`, 'player widget change reaches GM');
        assert.equal((await prefs()).length, gmPinCount, 'player pins do not modify GM preferences');
        await request(`/lobbies/characters/${character.id}`, { visible_to: [], editable_to: [] }, token, 'PUT');
        await player.evaluate(`window.dispatchEvent(new Event('focus'))`);
        await player.until(`!document.querySelector('.character-widget [data-widget-control="field:health.painLevel"]') && document.querySelector('.character-widget .panel-content').textContent.includes('недоступен')`, 'revoked access clears widget values');
        await protocol.call('Target.closeTarget', { targetId: target.targetId });
        await protocol.call('Target.disposeBrowserContext', { browserContextId: context.browserContextId });
        console.log('PASS: real player has personal pins, read-only access, granted edits and revoked access; updates reach GM');
    } finally { player?.close(); protocol.close(); }
    const location = await request(`/lobbies/${lobby.id}/locations`, { name: 'Widget scene', type: 'battle',
        grid_width: 5, grid_height: 5, world_tile_x: 10, world_tile_z: 10,
        tiles_data: Array.from({ length: 5 }, () => Array.from({ length: 5 }, () => ({ type: 'grass', height: 1 }))) }, token);
    await page.call('Page.bringToFront');
    await page.evaluate(`window.enterLocation(${location.id})`);
    await page.until(`window.isLocationActive && document.getElementById('scene-loading-screen').classList.contains('is-hidden')`, 'real local scene ready');
    await page.evaluate(`window.openCharacterSheet(${character.id})`);
    await page.until(`document.body.classList.contains('character-sheet-open')`, 'sheet over local scene');
    assert.equal(await page.evaluate(`getComputedStyle(document.getElementById('exit-location-btn')).visibility==='hidden' && [...document.querySelectorAll('.scene-fps-counter')].every(e=>getComputedStyle(e).visibility==='hidden')`), true);
    assert.equal(await page.evaluate(`[...document.querySelectorAll('.draggable-panel,#combat-status-panel,#character-widgets')].every(e=>getComputedStyle(e).visibility==='hidden')`), true);
    await page.evaluate(`window.closeCharacterSheet()`);
    assert.equal(await page.evaluate(`getComputedStyle(document.getElementById('exit-location-btn')).visibility`), 'visible');
    await page.evaluate(`(()=>{const i=document.querySelector('${painPanel} [data-widget-control="field:health.painLevel"]');i.value='10';i.dispatchEvent(new Event('change',{bubbles:true}))})()`);
    await page.until(`document.querySelector('${painPanel} [data-widget-control="field:health.painLevel"]')?.value==='10' && !document.querySelector('${painPanel} [data-widget-control="field:health.painLevel"]').disabled`, 'widget works on local scene');
    assert.equal((await request(`/lobbies/characters/${character.id}`, null, token, 'GET')).data.health.painLevel, 10);
    const localScreenshot = path.join(artifactDir, 'character-widgets-local-scene.png');
    fs.writeFileSync(localScreenshot, Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    console.log('Local widgets screenshot:', localScreenshot);
    await page.evaluate(`document.querySelector('${secondPanel} .panel-header button').click()`);
    assert.equal(await page.evaluate(`!!document.querySelector('${secondPanel}')`), false);
    assert.equal((await prefs()).length, savedCount);
    console.log('PASS: same widgets work in real local scene; sheet masks local controls/FPS and restores them');
    console.log('PASS: individual fields/actions, weapon/status blocks, hidden room UI, multi-character saves, existing skill roll and reload dialog, realtime updates, personal preferences, drag/collapse/reload');
};

module.exports.skillValuesExpression = WIDGET_SKILL_VALUES_EXPRESSION;
