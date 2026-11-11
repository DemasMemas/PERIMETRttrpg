// №150: три каталожных бинокля в осмотре территории на копии Припяти.
const assert = require('node:assert/strict');

module.exports = async ({page, request, token, lobby, offered, loc, locationId}) => {
    const api = `/lobbies/${lobby.id}`;
    const cases = [
        {name:'Бинокль', bonus:15, darkness:true},
        {name:'Бинокль Ночной', bonus:15, darkness:false},
        {name:'Бинокль с дальномером и увеличенной кратностью', bonus:25, darkness:false},
    ];
    const pockets = cases.map(({name}, index) => {
        const template = offered.global.find(item => item.category === 'misc' && item.name === name);
        assert.ok(template, name);
        return {id:'scout-optic-'+index, name, category:template.category, templateId:template.id,
            attributes:structuredClone(template.attributes), quantity:1, weight:template.weight, volume:template.volume};
    });
    const observer = await request(api+'/characters', {name:'Проверка трёх биноклей', data:{
        skills:{physical:{awareness:{base:20}}, other:{tactics:{base:20}}}, inventory:{pockets},
    }}, token);
    const target = await request(api+'/characters', {name:'Цель впереди ночью', data:{}}, token);
    const rear = await request(api+'/characters', {name:'Цель позади ночью', data:{}}, token);
    await request(loc+'/spawn_character', {character_id:target.id, tile_x:41, tile_y:50}, token);
    await request(loc+'/spawn_character', {character_id:rear.id, tile_x:41, tile_y:46}, token);
    const current = await request(api, null, token, 'GET');
    const nightDay = current.game_day+1;
    await request(api+'/time', {game_day:nightDay, game_time_minutes:120}, token, 'PATCH');
    await request(api+'/weather', {rain:{enabled:false,intensity:0},fog:{enabled:false,intensity:0},
        emission:{enabled:false,intensity:0}}, token, 'PATCH');

    const ownerUrl = '/lobbies/characters/'+observer.id;
    const scoutUrl = ownerUrl+'/location-scout';
    await request(scoutUrl, {location_id:locationId, x:41, y:48, floor_level:0, facing_x:0, facing_y:1}, token, 'PUT');
    for (const [index, {name, bonus, darkness}] of cases.entries()) {
        const result = await request(scoutUrl, {item_path:['inventory','pockets',index]}, token);
        assert.equal(result.check.item_bonus, bonus, name);
        assert.equal(result.check.darkness, darkness, name);
        assert.equal(result.check.equipment_modifier, darkness ? -5 : 0, name);
        assert.equal(result.check.weather_modifier,0,name);
        assert.equal(result.check.success, true, name);
        const ids = result.targets.map(person => person.character_id);
        assert.equal(ids.includes(target.id), !darkness, name);
        assert.ok(!ids.includes(rear.id), name);
    }
    const beforeView = await request(loc+'/combat', null, token, 'GET');
    assert.ok(!beforeView.characters.some(person => person.character_id === observer.id));

    await page.evaluate(`window.openCharacterSheet(${observer.id},'basic').then(()=>true)`);
    await page.until("document.querySelector('.scout-territory-btn')?.getClientRects().length", 'Осмотр территории в листе');
    await page.evaluate("document.querySelector('.scout-territory-btn').click();true");
    await page.until("document.querySelector('#trait-action-modal select[name=instrument]')", 'Выбор бинокля');
    const choices = await page.evaluate("[...document.querySelectorAll('#trait-action-modal select[name=instrument] option')].slice(1).map(option=>option.textContent)");
    assert.deepEqual(choices, cases.map(item => item.name));
    await page.evaluate(`(()=>{const form=document.querySelector('#trait-action-modal form');
        form.elements.instrument.value='item:2';form.querySelector('[type=submit]').click();return true})()`);
    await page.until("window.locationScoutView?.check.item_bonus===25&&document.querySelector('.scout-view-exit')?.getClientRects().length", 'Осмотр с дальномером показан');
    const actual = await page.evaluate('window.locationScoutView');
    assert.ok(actual.targets.some(person => person.character_id === target.id));
    await page.evaluate("document.querySelector('.scout-view-exit').click();true");
    await page.until('!window.isLocationActive&&!window.locationScoutView', 'Возврат после осмотра');
    await request(api+'/time', {game_day:nightDay, game_time_minutes:720}, token, 'PATCH');
    await page.evaluate(`window.enterLocation(${locationId}).then(()=>true)`);
    await page.until("window.isLocationActive&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')", 'Возврат ГМа в Припять');
    console.log('Бинокли: три каталожные модели, бонусы +15/+15/+25, тёмная цель видна через две ночные модели, цель позади скрыта. Дальномер выбран в листе, осмотр из точки ГМа и выход через штатный интерфейс.');
};
