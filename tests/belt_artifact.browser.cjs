// Открытый артефакт на поясе: общие свойства, масса и отсутствие контейнера.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,offered,loc,npc,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'equipment');assert.ok(safeSession.verified);
    const evidence=require('./helpers/playtest_registry.cjs').evidenceWriter(safeSession,'belt-artifact');
    await page.call('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false});
    const make=(t,id)=>({id,templateId:t.id,name:t.name,category:t.category,weight:t.weight,volume:t.volume,quantity:1,attributes:structuredClone(t.attributes||{})});
    const belt=make(offered.global.find(t=>t.category==='belt'),'artifact-browser-belt');
    const artifact=make(offered.global.find(t=>t.id===9600),'artifact-browser-item');
    const owner=await request(`/lobbies/${lobby.id}/characters`,{name:'Испытатель артефакта на поясе',data:{
        equipment:{belt},inventory:{money:100000,pockets:[artifact],backpack:[]}}},token);
    const placed=await request(loc+'/spawn_character',{character_id:owner.id,tile_x:46,tile_y:48},token);
    const master=await request('/lobbies/characters/'+npc.id,null,token,'GET');
    await request(loc+'/professional-modification',{actor_location_character_id:placed.location_character_id,specialist_character_id:npc.id,
        target_path:['equipment','belt'],item_id:belt.id,rule_id:'armor-g70',execute:true,operation_id:'belt-artifact-install',
        actor_revision:owner.data._revision,specialist_revision:master.data._revision},token);
    await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id],initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
    const before=await request(loc+'/combat',null,token,'GET');
    await page.evaluate(`window.openCharacterSheet(${owner.id},'inventory').then(()=>true)`);
    await page.until("document.querySelector('[data-belt-artifact]')",'Крепление артефакта');
    await page.evaluate('window.mountBeltArtifact();true');
    await page.until("document.querySelector('#consumable-application-modal .consumable-application-list button')",'Физический артефакт в инвентаре');
    await page.evaluate("document.querySelector('#consumable-application-modal .consumable-application-list button').click();true");
    await page.until("document.querySelector('[data-belt-artifact]')?.textContent.includes('Контрольный артефакт крепления')",'Артефакт закреплён');
    const after=await request(loc+'/combat',null,token,'GET'),saved=await request('/lobbies/characters/'+owner.id,null,token,'GET');
    assert.equal(after.current_character.action_points_current,before.current_character.action_points_current-3);
    assert.equal(after.current_character.movement_penalty,before.current_character.movement_penalty+2);
    assert.equal(saved.data.equipment.belt.installedModules[0].id,artifact.id);assert.equal(saved.data.inventory.pockets.length,0);
    assert.equal(saved.data._artifact_profile.protection.physical,15);assert.equal(saved.data._artifact_profile.radiation_per_movement,3);
    assert.equal(await page.evaluate("Number(document.getElementById('total-weight-display').textContent)"),2);
    assert.equal(await page.evaluate("Number(document.getElementById('artifact-penalty-source').textContent)"),2);
    assert.equal(await page.evaluate("Number(document.getElementById('move-penalty-display').textContent)"),after.current_character.movement_penalty);
    evidence.step('Артефакт из карманов закреплён без контейнера',
        {artifact:artifact.name,quantity:1,actionPoints:before.current_character.action_points_current,movementPenalty:before.current_character.movement_penalty},
        {artifact:saved.data.equipment.belt.installedModules[0].name,actionPoints:after.current_character.action_points_current,
            movementPenalty:after.current_character.movement_penalty,weight:2,physicalProtection:saved.data._artifact_profile.protection.physical,
            radiationPerMovement:saved.data._artifact_profile.radiation_per_movement});
    for(const[width,height]of [[1920,1080],[1600,900],[1366,768],[1280,720]]){
        await page.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
        await page.evaluate("document.querySelector('[data-belt-artifact]').scrollIntoView({block:'center'});true");
        await page.until("(()=>{const r=document.querySelector('[data-belt-artifact]').getBoundingClientRect();return r.width>0&&r.x>=0&&r.right<=innerWidth&&r.y>=0&&r.bottom<=innerHeight})()",'Крепление доступно '+width);
        if(width===1280)fs.writeFileSync(path.join(safeSession.artifacts,'belt-artifact-1280x720.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    }
    await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    const beforeUnmount=await request(loc+'/combat',null,token,'GET');
    await page.evaluate(`window.openCharacterSheet(${owner.id},'inventory').then(()=>true)`);
    await page.evaluate('window.unmountBeltArtifact(0);true');
    await page.until("!document.querySelector('[data-belt-artifact]')?.textContent.includes('Контрольный артефакт крепления')",'Артефакт снят');
    const returned=await request('/lobbies/characters/'+owner.id,null,token,'GET'),ended=await request(loc+'/combat',null,token,'GET');
    assert.equal(returned.data.inventory.pockets[0].id,artifact.id);assert.equal(returned.data._artifact_profile.protection.physical,0);
    assert.equal(ended.current_character.movement_penalty,before.current_character.movement_penalty);
    assert.equal(ended.current_character.action_points_current,beforeUnmount.current_character.action_points_current-3);
    evidence.step('Артефакт снят с пояса',
        {movementPenalty:after.current_character.movement_penalty,physicalProtection:15,actionPoints:beforeUnmount.current_character.action_points_current},
        {movementPenalty:ended.current_character.movement_penalty,physicalProtection:returned.data._artifact_profile.protection.physical,
            sameInstances:returned.data.inventory.pockets[0].id===artifact.id,actionPoints:ended.current_character.action_points_current});
    await request(loc+'/combat/end',{},token);
    evidence.publish({images:['belt-artifact-1280x720.png'],controls:{random:'Бросков нет',time:'Крепление в одном ходу, снятие в следующем; время идёт штатно'},
        limitations:['Контрольный шаблон артефакта, не проверка всех артефактов каталога. Физическая защита проверяется серверным профилем и профильным тестом, попадание в этом сценарии не выполняется.',
            'Радиация за переход проверена серверным тестом; глобальный переход в браузере не выполняется. Один клиент ГМа.']});
    console.log('Артефакт на поясе: платная установка g70, физический экземпляр без контейнера, масса/два свойства/штраф боя, 3 ОД туда и обратно, четыре настольных размера.');
};
