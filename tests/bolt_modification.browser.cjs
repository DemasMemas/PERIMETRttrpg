// №145: стоимость на кнопке и реальное действие затвора после улучшения.
const assert = require('node:assert/strict');

module.exports = async ({page, request, token, lobby, offered, loc, npc}) => {
    const template = offered.global.find(item => item.category === 'weapon' && item.item_class === '3'
        && item.attributes?.manual_cycle === 'bolt');
    assert.ok(template);
    const weapon = {
        id:'bolt-cost-test', templateId:template.id, name:template.name, category:'weapon',
        subcategory:template.subcategory, itemClass:template.item_class, attributes:structuredClone(template.attributes),
        weight:template.weight, volume:template.volume, quantity:1, requiresManualCycle:true,
    };
    const owner = await request(`/lobbies/${lobby.id}/characters`, {name:'Проверка затвора', data:{
        weapons:[weapon], inventory:{money:1000000},
    }}, token);
    const ownerUrl = '/lobbies/characters/'+owner.id;
    const readOwner = () => request(ownerUrl, null, token, 'GET');
    const {location_character_id:actor} = await request(loc+'/spawn_character', {
        character_id:owner.id, tile_x:46, tile_y:48,
    }, token);
    for (const id of ['weapon-b40','weapon-b41','weapon-b42']) {
        const mechanic = await request('/lobbies/characters/'+npc.id, null, token, 'GET');
        await request(loc+'/professional-modification', {
            actor_location_character_id:actor, specialist_character_id:npc.id,
            target_path:['weapons',0], item_id:weapon.id, rule_id:id, execute:true,
            operation_id:'bolt-cost-'+id, actor_revision:(await readOwner()).data._revision,
            specialist_revision:mechanic.data._revision,
        }, token);
    }
    await request(loc+'/combat/start', {location_character_ids:[actor],
        initiator_location_character_id:actor, initiator_first:true}, token);
    await request(loc+'/combat/action', {location_character_id:actor, action_key:'draw_weapon', weapon_index:0}, token);
    await page.evaluate(`window.openCharacterSheet(${owner.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.loadout-slot[data-equipment-open=weapon-0]')", 'Снайперская винтовка в листе');
    await page.evaluate("document.querySelector('.loadout-slot[data-equipment-open=weapon-0]').click();true");
    const button = '[onclick="cycleWeaponFromEquipment(0)"]';
    await page.until(`document.querySelector(${JSON.stringify(button)})?.getClientRects().length`, 'Кнопка затвора в бою');
    assert.match(await page.evaluate(`document.querySelector(${JSON.stringify(button)}).textContent`), /Передёрнуть затвор · 0 ОД/);
    const combat = async () => (await request(loc+'/combat', null, token, 'GET')).current_character;
    const before = await combat();
    await page.evaluate(`document.querySelector(${JSON.stringify(button)}).click();true`);
    await page.until(`(async()=>{const result=await(await import('/static/js/api.js')).Server.getCharacter(${owner.id});
        return result.data.weapons[0].requiresManualCycle===false})()`, 'Затвор передёрнут и состояние сохранено');
    const after = await combat();
    assert.equal(after.action_points_current, before.action_points_current);
    assert.equal(after.free_actions_current, before.free_actions_current);
    await request(loc+'/combat/end', {}, token);
    await request(loc+'/characters/'+owner.id, null, token, 'DELETE');
    await page.evaluate('window.closeCharacterSheet();true');
    console.log('Затвор: три уровня установлены у оружейника, кнопка показывает 0 ОД, действие в бою сохраняется и не тратит ОД и СД.');
};
