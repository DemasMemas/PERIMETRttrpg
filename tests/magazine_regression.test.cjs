const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm');
const scenario=require('./magazine_regression.browser.cjs');
const {MAGAZINE_ID,WEAPON_ID,MAGAZINE_NAME}=scenario;
const ammo=[{id:'a',quantity:3,attributes:{ammo_variant:'bp'}},{id:'b',quantity:4,attributes:{ammo_variant:'ep'}}];
const magazine=()=>({id:MAGAZINE_ID,templateId:1000002,name:MAGAZINE_NAME,ammo:structuredClone(ammo)});
const data=installed=>({_revision:installed?4:3,inventory:{backpack:installed?[]:[magazine()]},
    weapons:[{id:WEAPON_ID,ammo:installed?7:0,installedMagazine:installed?magazine():null}]});
const expected={id:MAGAZINE_ID,name:MAGAZINE_NAME,templateId:1000002,count:7,
    ammo:[{id:'a',quantity:3,variant:'bp'},{id:'b',quantity:4,variant:'ep'}]};
const project=d=>scenario.projectMagazine(d,MAGAZINE_ID,WEAPON_ID);

test('Реальная проекция отличает исходный backpack от установленного магазина',()=>{
    const before=data(false),after=data(true),copy=structuredClone(before);
    scenario.assertBefore(project(before),expected);scenario.assertInstalled(project(after),expected);
    assert.deepEqual(before,copy);assert.equal(project(after).revision,4);
    assert.throws(()=>scenario.assertInstalled(project(before),expected));
});

test('Дубликат, потеря, смена ID, патронов или порядка не принимаются',()=>{
    const changes=[d=>d.inventory.backpack.push(magazine()),d=>{d.weapons[0].installedMagazine=null;},
        d=>{d.weapons[0].installedMagazine.id='another';},d=>{d.weapons[0].installedMagazine.ammo[0].quantity=2;},
        d=>d.weapons[0].installedMagazine.ammo.reverse(),d=>{d.weapons[0].ammo=6;},
        d=>{d.weapons[0].installedMagazine.ammo[0].attributes.ammo_variant='other';},
        d=>{d.equipment={pouch:{contents:[magazine()]}};}];
    for(const change of changes){const d=data(true);change(d);assert.throws(()=>scenario.assertInstalled(project(d),expected));}
});

test('Collector настоящего события сохраняет только проекцию нужного персонажа',()=>{
    const listeners={},context={socket:{on:(name,handler)=>listeners[name]=handler}};context.window=context;
    vm.runInNewContext(scenario.eventCollector(9),context);
    assert.deepEqual(Object.keys(listeners),['character_data_updated']);
    listeners.character_data_updated({character_id:8,updates:{data:data(true)}});assert.equal(context.magazineEvents.length,0);
    const d=data(true);d.access_token='DO_NOT_SAVE';d.health={secret:'DO_NOT_SAVE'};
    listeners.character_data_updated({character_id:9,source:'reload',token:'DO_NOT_SAVE',updates:{data:d}});
    const event=JSON.parse(JSON.stringify(context.magazineEvents[0]));
    scenario.assertInstalled(event,expected);assert.equal(event.source,'reload');
    assert.ok(!JSON.stringify(event).includes('DO_NOT_SAVE'));assert.ok(!Object.hasOwn(event,'health'));
});

function fakeSheet({inventoryVisible=true,installed=false,weaponVisible=true}={}){
    const visible=()=>[{}],hidden=()=>[];
    const row={dataset:{widgetIdentity:JSON.stringify({id:MAGAZINE_ID})},innerText:MAGAZINE_NAME,getClientRects:inventoryVisible?visible:hidden};
    const span={textContent:MAGAZINE_NAME+' (7/15)',getClientRects:weaponVisible?visible:hidden};
    const inventory={getClientRects:inventoryVisible?visible:hidden,querySelectorAll:()=>installed?[]:[row]};
    const weapon={getClientRects:weaponVisible?visible:hidden,querySelectorAll:selector=>selector==='span'?(installed?[span]:[]):selector.includes('unequipMagazine')?(installed?[{}]:[]):(installed?[]:[{}])};
    const selectors={'#sheet-tab-inventory':inventory,'#weapons-container [data-weapon-index="0"]':weapon,'#character-sheet-modal':{getClientRects:visible}};
    return{document:{querySelector:selector=>{assert.ok(Object.hasOwn(selectors,selector));return selectors[selector];}},getComputedStyle:()=>({display:'block',visibility:'visible'})};
}
test('Реальное DOM-выражение отдельно читает видимость и магазин в двух вкладках',()=>{
    const before=vm.runInNewContext(scenario.sheetExpression(),fakeSheet({weaponVisible:false}));
    assert.equal(before.inventoryRows[0].id,MAGAZINE_ID);assert.equal(before.inventoryRows[0].visible,true);assert.equal(before.installed.length,0);
    const after=vm.runInNewContext(scenario.sheetExpression(),fakeSheet({inventoryVisible:false,installed:true}));
    assert.equal(after.inventoryRows.length,0);assert.equal(after.installed[0].visible,true);assert.ok(after.installed[0].text.includes('(7/15)'));assert.equal(after.removeButtons,1);assert.equal(after.installButtons,0);
    const hidden=vm.runInNewContext(scenario.sheetExpression(),fakeSheet({installed:true,weaponVisible:false}));
    assert.equal(hidden.installed[0].visible,false);assert.equal(hidden.weaponVisible,false);
});

test('Меню готово только с единственным исходным магазином и видимой кнопкой',()=>{
    const check=({name=MAGAZINE_NAME,count=7,selected='0',disabled=false,confirm=true,visible=true,extra=false}={})=>{
        const button=confirm?{disabled,getClientRects:()=>visible?[{}]:[]}:null;
        const select={value:selected,options:[{textContent:name+' ('+count+' патр.)'},...(extra?[{textContent:'Другой'}]:[])]};
        const modal={getClientRects:()=>[{}],querySelector:selector=>selector==='#magazine-select'?select:button};
        return vm.runInNewContext(`(${scenario.readyMenu.toString()})(${JSON.stringify(MAGAZINE_NAME)},7)`,{document:{querySelector:()=>modal}});
    };
    assert.equal(check(),true);
    for(const options of [{name:'Другой'},{count:6},{selected:''},{disabled:true},{confirm:false},{visible:false},{extra:true}])assert.equal(check(options),false);
});

test('Фактическая последовательность UI ждёт готовность меню до подтверждения',async()=>{
    const calls=[];let ready;const pending=new Promise(resolve=>{ready=resolve;});
    const run=scenario.installViaUI({click:async selector=>calls.push(selector),waitMenu:async()=>{calls.push('wait');await pending;},recordMenu:async()=>calls.push('record')});
    for(let i=0;i<6;i++)await Promise.resolve();assert.equal(calls.at(-1),'wait');assert.ok(!calls.includes('#confirm-magazine-btn'));
    ready();await run;assert.equal(calls.at(-2),'record');assert.equal(calls.at(-1),'#confirm-magazine-btn');
    assert.equal(calls.filter(value=>value==='#confirm-magazine-btn').length,1);
    const failed=[];
    await assert.rejects(scenario.installViaUI({click:async selector=>failed.push(selector),waitMenu:async()=>{throw Error('Не готово');},recordMenu:async()=>assert.fail()}));
    assert.ok(!failed.includes('#confirm-magazine-btn'));
});

test('Ожидание сохранения не принимает старое/частичное состояние',async()=>{
    let reads=0,waits=0;
    const result=await scenario.waitInstalled(async()=>{reads++;return project(data(reads>=3));},expected,async()=>{waits++;});
    assert.equal(result.installed.id,MAGAZINE_ID);assert.equal(reads,3);assert.equal(waits,2);
    reads=0;
    await assert.rejects(scenario.waitInstalled(async()=>{reads++;return project(data(false));},expected,async()=>{}));
    assert.equal(reads,120);
});
