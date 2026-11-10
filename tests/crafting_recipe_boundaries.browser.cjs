const assert = require('node:assert/strict');

module.exports = async ({page, request, token, api, loc}) => {
    const {global: templates} = await request(api + '/templates', null, token, 'GET');
    const template = name => {
        const result = templates.find(item => item.name === name);
        assert.ok(result, 'Каталожный шаблон: ' + name);
        return result;
    };
    const item = (name, id, overrides = {}) => {
        const t = template(name);
        return {id, name, templateId: t.id, category: t.category, attributes: t.attributes, quantity: 1,
            weight: t.weight, volume: t.volume, uses: t.attributes?.uses, maxUses: t.attributes?.uses, ...overrides};
    };
    const actor = await request(api + '/characters', {name: 'Проверка рецептов', data: {
        health: {current: 700, max: 700, effects: []},
        skills: {other: {engineering: {base: 20}, survival: {base: 20}}},
        features: {specialTraits: [{name: 'Мастер на все руки'}]},
        inventory: {pockets: [item('Металлические детали', 'recipe-metal')], backpack: []},
    }}, token);
    await request(loc + '/spawn_character', {character_id: actor.id, tile_x: 41, tile_y: 48}, token);
    await request(loc + '/objects', {name: 'Печь', type: 'household_workbench', tile_x: 42, tile_y: 48,
        properties: {craft_workbench: 'Печь', level: 0}}, token);
    const url = '/lobbies/characters/' + actor.id;
    const read = () => request(url, null, token, 'GET');
    const state = `(async()=>{const {Server}=await import('/static/js/api.js');return (await Server.getCharacter(${actor.id})).data`;
    const waitState = (expression, label) => page.until(`(async()=>{const data=await ${state}})();return ${expression};})()`, label);
    const button = label => `[...document.querySelectorAll('#crafting-modal button')].find(b=>b.textContent===${JSON.stringify(label)})`;
    const click = async label => {
        await page.until(`Boolean(${button(label)} && !${button(label)}.disabled)`, label);
        await page.evaluate(`${button(label)}.click();true`);
    };
    const selectRecipe = async id => {
        await page.until("document.querySelector('#crafting-modal select[aria-label=Рецепт]')", 'Загружено создание');
        await page.evaluate(`(()=>{const select=document.querySelector('#crafting-modal select[aria-label=Рецепт]');
            select.value=${JSON.stringify(id)};select.dispatchEvent(new Event('change'));return true})()`);
    };
    const openSheetCrafting = async () => {
        await page.evaluate(`window.openCharacterSheet(${actor.id},'equipment').then(()=>true)`);
        await page.until("document.querySelector('#character-crafting-open')", 'Лист с кнопкой создания');
        await page.evaluate("document.querySelector('#character-crafting-open').click();true");
    };
    const closeCrafting = () => page.evaluate(
        "document.querySelector('#crafting-modal .block-header button').click();window.closeCharacterSheet?.();true");
    const replacePockets = async pockets => {
        const {data} = await read();
        data.inventory.pockets = pockets;
        await request(url, {data}, token, 'PUT');
    };
    const advance = async minutes => {
        const clock = await request(api, null, token, 'GET');
        const end = clock.game_time_minutes + minutes;
        await request(api + '/time', {game_day: clock.game_day + Math.floor(end / 1440),
            game_time_minutes: end % 1440}, token, 'PATCH');
        await click('Обновить');
    };
    const finish = async minutes => {
        await page.until(`Boolean(${button('Завершить · к100')}?.disabled)`, 'До окончания срока кнопка заблокирована');
        await advance(minutes);
        await click('Завершить · к100');
        await waitState('!data.crafting?.current', 'Работа завершена');
    };
    const checkOutput = async (name, amount) => {
        const {data} = await read();
        const entries = data.inventory.pockets.filter(i => i.templateId === template(name).id);
        assert.equal(entries.reduce((sum, entry) => sum + entry.quantity, 0), amount);
        assert.ok(data.crafting.history.at(-1).success);
        return entries;
    };

    await openSheetCrafting();
    await selectRecipe('recipe-m27');
    await page.until("document.querySelector('#crafting-modal .crafting-recipe-detail')?.innerText.includes('Результат: Отмычка × 2')",
        'Котировка показывает две отмычки');
    await click('Начать создание');
    await finish(40);
    await checkOutput('Отмычка', 2);
    await closeCrafting();

    const fuelName = 'Топливо(1 Литр)';
    for (const useFuel of [false,true]) {
        const liquid = useFuel
            ? item('Канистра(Пустая)(Объем 15 литров)(45 использований воды)', 'molotov-canister', {
                fuelUsed: true, contents: [item(fuelName, 'molotov-fuel')],
            })
            : item('Спирт', 'molotov-spirit', {uses: 10,maxUses: 10});
        await replacePockets([liquid,
            item('Стеклянная бутылка (Объем 1 литр) (3 использования воды)', 'molotov-bottle'),
            item('Тканные материалы', 'molotov-cloth')]);
        await openSheetCrafting();
        await selectRecipe('recipe-v11');
        const quoteLine = useFuel ? 'Топливо 1/0.5' : 'Спирт 10/3';
        await page.until(`[...document.querySelectorAll('#crafting-modal .crafting-recipe-detail p')].some(p=>
            p.textContent.includes(${JSON.stringify(quoteLine)}))`, 'Котировка учитывает остаток выбранной жидкости');
        await click('Начать создание');
        await waitState("data.crafting?.current?.recipe_id==='recipe-v11'", 'Материалы коктейля зарезервированы');
        const {data} = await read();
        const job = data.crafting.current;
        assert.equal(job.station_usage ?? null,null);
        assert.equal(data.inventory.pockets.length,1);
        assert.equal(useFuel ? data.inventory.pockets[0].contents[0].quantity : data.inventory.pockets[0].uses,
            useFuel ? 0.5 : 7);
        const reserved = name => job.escrow.some(entry=>entry.item.templateId===template(name).id&&entry.amount===1);
        assert.ok(reserved('Стеклянная бутылка (Объем 1 литр) (3 использования воды)'));
        assert.ok(reserved('Тканные материалы'));
        await finish(30);
        await checkOutput('Коктейль Молотова', 1);
        const done = (await read()).data;
        const receipt = done.crafting.receipts.at(-1);
        const repeat = await request(url+'/crafting',{operation:'finish',job_id:job.id,
            operation_id:receipt.id,character_revision:data._revision},token);
        assert.equal(repeat.result.replayed,true);
        await checkOutput('Коктейль Молотова', 1);
        await closeCrafting();
    }
    console.log('Коктейль Молотова: через спирт и через 0,5 л топлива из канистры, без станка, 30 минут, один результат и защита от повторной выдачи.');

    for (const water of ['Дождевая вода', 'Болотная вода']) {
        await replacePockets([item(water, 'recipe-water', {uses: 2}),
            item('Хлорные таблетки (10 штук)', 'recipe-chlorine')]);
        await openSheetCrafting();
        await selectRecipe('recipe-aa10');
        await page.until(`[...document.querySelectorAll('#crafting-modal .crafting-recipe-detail p')].some(p=>
            p.textContent.includes('Дождевая вода') && p.textContent.includes('Болотная вода') &&
            p.textContent.includes(${JSON.stringify(water + ' 2/2')}))`, 'Окно показывает оба варианта очистки');
        await click('Начать создание');
        await waitState("data.crafting?.current?.recipe_id==='recipe-aa10'", 'Компоненты очистки зарезервированы');
        assert.equal((await read()).data.inventory.pockets.find(i => i.id === 'recipe-chlorine').uses, 9);
        await finish(1);
        await checkOutput('Радиоактивная вода', 1);
        await closeCrafting();
    }

    const aera = await request(loc + '/objects', {name: 'Аэрационная установка', type: 'station_aeration',
        tile_x: 41, tile_y: 49, properties: {portable_station: 'recipe-m34', aeration_unit: true,
            visual_asset: 'station_aeration', level: 0, dimensions: {width: .9, depth: .8, height: 2}}}, token);
    const filter = (id, durability) => {
        const entry = item('Фильтр для противогаза', id, {durability});
        entry.attributes = {...entry.attributes, durability};
        return entry;
    };
    await replacePockets([item('Радиоактивная вода', 'recipe-radioactive', {quantity: 4, uses: 1}),
        filter('recipe-filter-6', 6), filter('recipe-filter-7', 7)]);
    await page.until(`(async()=>{const L=await import('/static/js/locationScene.js');
        const roots=L.sceneGraphicsForTest().roots;
        return roots.some(r=>r.userData.locationObjectId===${aera.id})})()`, 'Аэрационная установка появилась в Припяти');
    await page.evaluate(`(async()=>{const L=await import('/static/js/locationScene.js');
        window.currentLocationCharacterId=${actor.id};L.showObjectContextMenu(650,350,${JSON.stringify(aera)});return true})()`);
    await page.until("[...document.querySelectorAll('button')].some(b=>b.textContent==='Очистка воды')",
        'Действие очистки доступно у установки');
    await page.evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Очистка воды').click();true");
    await page.until("document.querySelector('#crafting-modal .crafting-recipe-detail')?.innerText.includes('Результат: Вода × 8')",
        'Действие открывает рецепт аэрации');
    await click('Начать создание');
    await waitState("data.crafting?.current?.recipe_id==='recipe-aa12'", 'Вода и заряды зарезервированы');
    assert.deepEqual((await read()).data.inventory.pockets.map(i => [i.id, i.durability]), [['recipe-filter-7', 3]]);
    const relocate = (x,y) => request(loc+'/spawn_character',
        {character_id:actor.id,tile_x:x,tile_y:y,force_relocate:true},token);
    await relocate(35,48);
    await advance(30);
    await click('Завершить · к100');
    await page.until("document.body.innerText.includes('Вернитесь к аэрационной установке')",'Издалека воду забрать нельзя');
    await relocate(41,48);
    await click('Завершить · к100');
    await waitState('!data.crafting?.current','Вода собрана после возвращения');
    const cleanWater = await checkOutput('Вода', 3);
    assert.deepEqual(cleanWater.map(i => [i.quantity, i.uses, i.maxUses]), [[3, 2, 3]]);
    await closeCrafting();

    const still = await request(loc + '/objects', {name: 'Самогонный аппарат', type: 'station_distillery',
        tile_x: 42, tile_y: 49, properties: {portable_station: 'recipe-m40', standard_distillery: true,
            craft_workbench: 'Самогонный Аппарат', level: 0}}, token);
    const spiritIngredients = prefix => [item('Вода', prefix + '-water', {quantity: 2, uses: 3}),
        item('Банка пива', prefix + '-beer'), item('Хлеб', prefix + '-bread', {quantity: 2})];
    await replacePockets(spiritIngredients('recipe-spirit'));
    await page.evaluate(`(async()=>{const L=await import('/static/js/locationScene.js');
        window.currentLocationCharacterId=${actor.id};L.showObjectContextMenu(650,350,${JSON.stringify(still)});return true})()`);
    await page.until("[...document.querySelectorAll('button')].some(b=>b.textContent==='Создание предметов')",
        'В меню аппарата есть создание');
    await page.evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Создание предметов').click();true");
    await selectRecipe('recipe-aa7');
    await click('Начать подготовку');
    await page.until("document.querySelector('#crafting-modal')?.innerText.includes('Подготовка · осталось 10 мин')",
        'У аппарата отображается подготовка');
    const spiritJob = (await read()).data.crafting.current;
    assert.equal(spiritJob.station_usage.object_id, still.id);
    await advance(10);
    await page.until("document.querySelector('#crafting-modal')?.innerText.includes('Аппарат работает сам · осталось 1440 мин')",
        'После подготовки начинается суточная выдержка');
    await relocate(35,48);
    await advance(1440);
    const collect = 'Забрать результат · к100';
    await page.until(`Boolean(${button(collect)}?.disabled) &&
        document.querySelector('#crafting-modal').innerText.includes('Вернитесь к аппарату за результатом')`,
        'Готовую партию нельзя забрать издалека');
    await relocate(41,48);
    await click('Обновить');
    await click(collect);
    await waitState(`!data.crafting?.current && data.crafting?.history.at(-1)?.job_id===${JSON.stringify(spiritJob.id)}`,
        'После возвращения партия завершена');
    await closeCrafting();

    await replacePockets(spiritIngredients('recipe-spirit-race'));
    const competitor = await request(api + '/characters', {name: 'Вторая партия спирта', data: {
        health: {current: 700, max: 700, effects: []}, skills: {other: {survival: {base: 20}}},
        inventory: {pockets: spiritIngredients('competing-spirit'), backpack: []},
    }}, token);
    await request(loc + '/spawn_character', {character_id: competitor.id, tile_x: 43, tile_y: 49}, token);
    const results = await page.evaluate(`(async()=>{
        const {Server}=await import('/static/js/api.js');
        const ids=${JSON.stringify([actor.id,competitor.id])};
        const characters=await Promise.all(ids.map(id=>Server.getCharacter(id)));
        return Promise.all(ids.map((id,index)=>Server.craft(id,{operation:'start',recipe_id:'recipe-aa7',
            station_id:${still.id},character_revision:characters[index].data._revision,operation_id:'distillery-race'})
            .then(({result,data})=>({id,status:200,job:result.id,revision:data._revision}),
                error=>({id,status:error.status,message:error.message}))));
    })()`);
    const winner = results.find(result => result.status === 200);
    const loser = results.find(result => result.id !== winner?.id);
    assert.equal(results.filter(result => result.status === 200).length, 1, JSON.stringify(results));
    assert.ok([400,409].includes(loser.status) && loser.message.includes('занят'), JSON.stringify(results));
    const rejected = await request('/lobbies/characters/' + loser.id, null, token, 'GET');
    assert.ok(!rejected.data.crafting?.current);
    assert.equal(rejected.data.inventory.pockets.length, 3);
    await request('/lobbies/characters/' + winner.id + '/crafting', {operation:'cancel',
        operation_id:'distillery-race-cancel',character_revision:winner.revision,job_id:winner.job}, token);
    console.log(JSON.stringify({lockpick_recipe:true, chlorine_rain_and_swamp:true, aeration_from_scene:true,
        clean_water_portions:8, distillery_phases:true, distillery_concurrent_starts:true}));
};
