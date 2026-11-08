// Типизированные дополнительные крепления, настоящие подсумки и сохранённые ID.
const assert=require('node:assert/strict');
module.exports=async({page,request,token,lobby,offered,loc,npc,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'catalog');assert.ok(safeSession.verified);
    const item=(t,id,quantity=1)=>({id,templateId:t.id,name:t.name,category:t.category,quantity,
        attributes:structuredClone(t.attributes||{}),weight:t.weight,volume:t.volume,contents:[]});
    const beltTemplate=offered.global.find(t=>t.category==='belt');
    const general=offered.global.find(t=>t.category==='pouch'&&t.name==='Вещевой подсумок — 1');
    const magazines=offered.global.find(t=>t.category==='pouch'&&t.attributes?.allowed_categories?.includes('magazine'));
    assert.ok(beltTemplate&&general&&magazines);
    const belt={...item(beltTemplate,'belt-extra-mounts'),maxPouches:2,pouches:[item(general,'belt-base-1'),item(general,'belt-base-2')]};
    const owner=await request(`/lobbies/${lobby.id}/characters`,{name:'Испытатель креплений',data:{equipment:{belt},
        inventory:{money:100000,pockets:[item(general,'belt-third'),item(general,'belt-unused'),item(magazines,'belt-mag-stack',2)],backpack:[]}}},token);
    const placement=await request(loc+'/spawn_character',{character_id:owner.id,tile_x:47,tile_y:48},token);
    const service=async(action,id)=>{
        const actor=await request('/lobbies/characters/'+owner.id,null,token,'GET'),master=await request('/lobbies/characters/'+npc.id,null,token,'GET');
        return request(loc+'/professional-modification',{actor_location_character_id:placement.location_character_id,
            specialist_character_id:npc.id,target_path:['equipment','belt'],item_id:belt.id,execute:true,
            operation_id:id,actor_revision:actor.data._revision,specialist_revision:master.data._revision,...action},token);
    };
    await service({rule_id:'armor-g56'},'belt-free-mount');await service({action:'unlock_slot'},'belt-unlock');
    await service({rule_id:'armor-g76'},'belt-mag-mounts');
    await page.evaluate(`window.openCharacterSheet(${owner.id},'inventory').then(()=>true)`);
    const mount=async(expected,onlyMagazines)=>{
        await page.evaluate('window.addBeltPouch();true');
        await page.until("document.querySelector('#pouch-select-modal select option')",'Выбор подсумка');
        const options=await page.evaluate("[...document.querySelectorAll('#pouch-select-modal select option')].map(o=>o.textContent)");
        if(onlyMagazines){assert.equal(options.length,1);assert.ok(options[0].includes(magazines.name));}
        await page.evaluate("document.querySelector('#pouch-select-modal .pouch-confirm').click();true");
        await page.until("!document.querySelector('#pouch-select-modal')",'Подсумок установлен');
        await page.evaluate(`(async()=>{const {Server}=await import('/static/js/api.js');await Server.waitForCharacterSave(${owner.id});return true})()`);
        const saved=await request('/lobbies/characters/'+owner.id,null,token,'GET');
        assert.equal(saved.data.equipment.belt.pouches.length,expected);
        return saved;
    };
    await mount(3,false);await mount(4,true);const saved=await mount(5,true);
    const ids=saved.data.equipment.belt.pouches.map(p=>p.id);assert.equal(new Set(ids).size,5);
    assert.ok(ids.includes('belt-third')&&ids.includes('belt-mag-stack'));
    assert.ok(saved.data.inventory.pockets.some(p=>p.id==='belt-unused'));
    assert.ok(!saved.data.inventory.pockets.some(p=>p.id==='belt-third'||p.id==='belt-mag-stack'));
    console.log('Крепления пояса: куплены g56/g76 и слот, физически установлены 3 обычных и 2 магазинных подсумка, неподходящий подсумок исключён из выбора, сохранены ID и остаток инвентаря.');
};
