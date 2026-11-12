const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

module.exports = async ({page, request, token, loc, shooterId, targetId, shooterLocationId,
    targetLocationId, rules, safeSession}) => {
    assert.equal(process.env.VTT_SAFE_SCENARIO, 'artifacts');
    assert.ok(safeSession.verified);
    const characterUrl = id => '/lobbies/characters/'+id;
    const shooter = await request(characterUrl(shooterId), null, token, 'GET');
    const weapon = shooter.data.weapons[0];
    const ammunition = weapon.installedMagazine.ammo[0];
    ammunition.damage = 100;
    ammunition.attributes = {...ammunition.attributes, damage: 100, armor_piercing: 35};
    weapon.damage = 100;
    await request(characterUrl(shooterId), {data: shooter.data}, token, 'PUT');

    const target = await request(characterUrl(targetId), null, token, 'GET');
    const skin = rules.find(rule => rule.name === 'Вторая кожа');
    assert.ok(skin);
    target.data.equipment = {
        armor: {
            id: 'penalty-test-armor', name: 'Проверочная броня', category: 'armor',
            durability: 100, maxDurability: 100, protection: {physical: 50},
            containers: [{item: {
                id: 'penalty-test-artifact', name: skin.name, category: 'artifact', attributes: skin,
            }}],
        },
    };
    target.data.health.current = 700;
    target.data.health.painLevel = 0;
    target.data.health.effects = [];
    for (const zone of Object.values(target.data.health.zones || {})) zone.current = zone.max;
    await request(characterUrl(targetId), {data: target.data}, token, 'PUT');
    await page.evaluate(`window.openCharacterSheet(${targetId},'basic').then(()=>true)`);
    await request(loc+'/combat/start', {
        location_character_ids: [shooterLocationId, targetLocationId],
        initiator_location_character_id: shooterLocationId, initiator_first: true,
    }, token);
    const command = {
        location_character_id: shooterLocationId, weapon_index: 0, target_character_id: targetId,
    };
    await request(loc+'/combat/action', {...command, action_key: 'aim'}, token);
    const state = await request(loc+'/combat', null, token, 'GET');
    const cost = state.current_character.weapon_ergonomics[0].aimed_shot_action_points;
    const before = await request(characterUrl(targetId), null, token, 'GET');
    const shot = await request(loc+'/combat/action', {
        ...command, action_key: 'attack', fire_mode: 'aimed', target_zone: 'chest',
        shot_count: 1, volley_count: 1, action_points: cost,
    }, token);
    const hit = shot.attack.results[0];
    assert.ok(hit.hit);
    assert.equal(hit.armor, 35);
    assert.equal(hit.armor_piercing, 35);
    assert.equal(hit.damage, 100);
    const after = await request(characterUrl(targetId), null, token, 'GET');
    assert.equal(before.data.health.current-after.data.health.current, 100);
    assert.equal(before.data.health.zones.chest.current-after.data.health.zones.chest.current, 100);
    await page.until(`Number(document.querySelector('[name="health.current"]')?.value)===${after.data.health.current}`,
        'Полный урон после снижения защиты виден в листе');
    await request(loc+'/combat/end', {}, token);
    const skillOwner = await request(characterUrl(shooterId), null, token, 'GET');
    const resonance = rules.find(rule => rule.name === 'Резонанс разума');
    assert.ok(resonance);
    skillOwner.data.equipment = {armor: {containers: [{item: {
        id: 'skill-display-artifact', category: 'artifact', name: resonance.name, attributes: resonance,
    }}]}};
    skillOwner.data.skills.physical.awareness = {base: 10, bonus: 0, xp: 0};
    skillOwner.data.skills.other = {medicine: {base: 10, bonus: 0, xp: 0}};
    await request(characterUrl(shooterId), {data: skillOwner.data}, token, 'PUT');
    await page.evaluate(`window.openCharacterSheet(${shooterId},'skills').then(()=>true)`);
    const skillBonus = name => `#sheet-tab-skills [name="skills.${name}.bonus"]`;
    for (const name of ['physical.awareness', 'other.medicine']) {
        await page.until(`document.querySelector(${JSON.stringify(skillBonus(name))})?.value==='3'`,
            'Артефакт показан в бонусе навыка '+name);
        assert.equal(await page.evaluate(`document.querySelector(${JSON.stringify(skillBonus(name))}).dataset.transientBonus`), '3');
    }
    await page.evaluate(`(()=>{
        const input=document.querySelector('#sheet-tab-skills [name="skills.physical.awareness.base"]');
        input.value='11';input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));
        return true;
    })()`);
    await page.until(`fetch(${JSON.stringify(characterUrl(shooterId))},{headers:{Authorization:'Bearer '+localStorage.getItem('access_token')}})
        .then(response=>response.json()).then(sheet=>sheet.data.skills.physical.awareness.base===11&&sheet.data.skills.physical.awareness.bonus===0)`,
        'Автосохранение не записало временный бонус артефакта в постоянный');
    const unequipped = await request(characterUrl(shooterId), null, token, 'GET');
    unequipped.data.equipment.armor.containers = [];
    await request(characterUrl(shooterId), {data: unequipped.data}, token, 'PUT');
    for (const name of ['physical.awareness', 'other.medicine']) {
        await page.until(`document.querySelector(${JSON.stringify(skillBonus(name))})?.value==='0'`,
            'Снятие артефакта обновило открытые навыки '+name);
    }
    fs.writeFileSync(path.join(safeSession.artifacts, 'artifact-negative-protection.json'), JSON.stringify({
        scene: 'Копия Припяти', artifact: skin.name, armorBefore: 50, armorAfter: hit.armor,
        armorPiercing: hit.armor_piercing, realShot: true, damage: hit.damage,
        poolAndChest: true, networkVisible: true,
        skillBonusesVisible: true, noPermanentBonusDuplication: true, liveUnequipRefresh: true,
    }, null, 2));
    console.log('Вторая кожа — настоящий выстрел, физическая защита 50 → 35%, полное пробитие и 100 урона в листе.');
};
