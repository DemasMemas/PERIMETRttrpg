const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const load=()=>import(`data:text/javascript;base64,${Buffer.from(fs.readFileSync(path.join(__dirname,'../app/static/js/inventoryContainers.js'),'utf8')).toString('base64')}`);

test('смена подсумка обновляет вместимость и ограничения, сохраняя вещи',async()=>{
    const {applyPouchTemplate}=await load();
    const contents=[{id:'medicine'}],pouch={internalVolume:20,capacity:20,contents,allowedCategories:['ammo']};
    const template={id:2,name:'Медицинский',weight:.2,volume:1,attributes:{capacity:5,allowed_categories:['med']}};
    applyPouchTemplate(pouch,template);
    assert.equal(pouch.internalVolume,5);assert.equal(pouch.capacity,5);assert.equal(pouch.volume,1);
    assert.equal(pouch.contents,contents);assert.equal(pouch.allowedCategories,undefined);
    assert.deepEqual(pouch.allowed_categories,['med']);
    applyPouchTemplate(pouch,{id:3,name:'Универсальный',volume:4});
    assert.equal(pouch.internalVolume,4);assert.deepEqual(pouch.allowed_categories,[]);
    assert.deepEqual(template.attributes.allowed_categories,['med']);
});

test('подсумки готовой разгрузки используют свою вместимость, а не объём разгрузки',async()=>{
    const {vestPouches}=await load();
    const template={volume:12,attributes:{pouches:[{capacity:3,contents:[{id:'x'}]},{capacity:0}]}};
    const pouches=vestPouches(template);
    assert.equal(pouches[0].internalVolume,3);assert.equal(pouches[1].internalVolume,0);
    pouches[0].contents[0].id='changed';
    assert.equal(template.attributes.pouches[0].contents[0].id,'x');
});

test('смена разгрузки и удаление подсумка возвращают его вещи без потери и дублей',async()=>{
    const {applyVestTemplate,returnPouchContents}=await load();
    const old={contents:[{id:'bandage',quantity:4}]};
    const data={equipment:{vest:{id:'my-vest',templateId:1,pouches:[old]}},inventory:{backpack:[{id:'food'}]}};
    const template={id:2,name:'Другая',volume:2,attributes:{total_capacity:8,pouches:[{capacity:3,contents:[]}]}};
    applyVestTemplate(data,template);
    assert.equal(data.equipment.vest.id,'my-vest');assert.equal(data.equipment.vest.templateId,2);
    assert.deepEqual(data.inventory.backpack.map(item=>item.id),['food','bandage']);
    assert.equal(data.inventory.backpack[1].quantity,4);assert.equal(data.equipment.vest.pouches[0].internalVolume,3);
    returnPouchContents(data,[old]);
    assert.equal(data.inventory.backpack.length,2);
    const beltPouch={contents:[{id:'cartridge',quantity:25}]};
    returnPouchContents(data,[beltPouch]);
    assert.equal(data.inventory.backpack.at(-1).quantity,25);assert.deepEqual(beltPouch.contents,[]);
    assert.deepEqual(template.attributes.pouches[0].contents,[]);
});

test('профильная скидка разгрузки меняет внешний объём только нужных подсумков',async()=>{
    const {vestMountVolume,applyPouchTemplate}=await load();
    const medical={id:10,name:'Медицинский',subcategory:'Медицинский',volume:2,attributes:{capacity:6,mount_volume:2}};
    const magazine={id:11,name:'Магазинный',subcategory:'Магазинный',volume:4,attributes:{capacity:20,mount_volume:4}};
    const a=applyPouchTemplate({},medical),b=applyPouchTemplate({},magazine);
    const vest={attributes:{discount_pouch_type:'Магазинный',pouch_volume_multiplier:.5},pouches:[a,b]};
    assert.equal(vestMountVolume(vest,[medical,magazine]),4);
    assert.equal(vestMountVolume({...vest,attributes:{}},[medical,magazine]),6);
    assert.equal(vestMountVolume({...vest,pouches:[{type:11}]},[medical,magazine]),2);
    assert.equal(a.capacity,6);assert.equal(b.capacity,20);
});

test('лучшие подсумки увеличивают вместимость только медицинских подсумков надетого пояса',async()=>{
    const {applyPouchTemplate,pouchCapacity}=await load();
    const medical=applyPouchTemplate({}, {id:12,name:'Медицинский подсумок',subcategory:'Медицинский',attributes:{capacity:5}});
    const magazine=applyPouchTemplate({}, {id:13,name:'Магазинный подсумок',subcategory:'Магазинный',attributes:{capacity:5}});
    const belt={modifications:[{ruleId:'armor-g66',rules:{medical_pouch_capacity_multiplier:2}}]};
    assert.equal(pouchCapacity(medical,belt),10);
    assert.equal(pouchCapacity(magazine,belt),5);
    assert.equal(pouchCapacity(medical,null),5);
    assert.equal(pouchCapacity(medical,{...belt,protectionDisabled:true}),5);
    assert.equal(pouchCapacity(medical,{modifications:[{ruleId:'armor-g66',rules:{medical_pouch_capacity_multiplier:3}}]}),15);
    assert.equal(medical.internalVolume,5);
});
