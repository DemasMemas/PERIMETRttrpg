const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const widgets=require('./character_widgets.browser.cjs');
const movement=require('./movement_regression.browser.cjs');
test('Исправленное Runtime.evaluate возвращает DOM-значения без вызова объекта',()=>{
    for(const [base,bonus]of [['14','3'],['17','-2']]){
        const controls=new Map([['#sheet-content [name="skills.physical.will.base"]',{value:base}],['#sheet-content [name="skills.physical.will.bonus"]',{value:bonus}]]);
        const document={querySelector(selector){assert.ok(controls.has(selector));return controls.get(selector);}};
        const value=vm.runInNewContext(widgets.skillValuesExpression,{document});
        assert.deepEqual(JSON.parse(JSON.stringify(value)),{base,bonus});
    }
});
test('Ожидаемая цена использует серверный divisor/множитель и отдельную цену перелезания',()=>{
    const rules=movement.serverMovementRules();assert.ok(rules.walk.divisor>0&&rules.run.divisor>0);
    assert.equal(movement.expectedMovementCost({cost:5,climb_cost:0},{divisor:2},1),2.5);
    assert.equal(movement.expectedMovementCost({cost:10,climb_cost:5},{divisor:2},1),7.5);
    assert.equal(movement.expectedMovementCost({cost:.2,climb_cost:0},{divisor:2},1),.1);
    assert.equal(movement.expectedMovementCost({cost:1.3,climb_cost:0},{divisor:2},1),.65);
    assert.throws(()=>movement.expectedMovementCost({cost:5},{divisor:0},1));
});
test('Кандидаты #96 берутся из неизменённой Припяти и остаются NEEDS_REPRO',()=>{
    const fixture=JSON.parse(fs.readFileSync(path.join(__dirname,'../tmp/pripyat-regression.json'),'utf8').replace(/^\uFEFF/,''));
    const cases=movement.navigationCandidates(fixture);
    assert.equal(cases.status,'NEEDS_REPRO');assert.equal(cases.stairs.length,7);
    assert.equal(cases.selectedStair.dimensions.width,.9);assert.deepEqual(cases.selectedStair.from,[7,6,0]);assert.deepEqual(cases.selectedStair.to,[7,11,1]);
    assert.deepEqual(cases.corridor.from,[8,7,0]);assert.equal(cases.corridor.walls.length,2);
});

test('Реальное выражение movement-снимка читает надпись HUD и видимость без изменения DOM',()=>{
    const root={querySelectorAll:()=>[{textContent:'ОД: 3/5'},{textContent:'ОП: 5/6'}],getClientRects:()=>[{}]};
    const context={document:{querySelector(selector){assert.equal(selector,'#combat-status-panel .combat-hud-body');return root;}},
        getComputedStyle:()=>({display:'block'}),L:{movementHintSnapshot:()=>({text:'ОП 1/5',visible:true}),characterPose:id=>({id,x:42,y:48})},movementEvidenceEvents:[{round:1,op:5}]};
    const value=vm.runInNewContext(movement.movementUISnapshotExpression(7),context);
    assert.equal(value.line,'ОП: 5/6');assert.equal(value.visible,true);assert.equal(value.hint.text,'ОП 1/5');assert.equal(value.pose.id,7);assert.equal(value.events[0].op,5);
});

function deferred(){let resolve;const promise=new Promise(done=>{resolve=done;});return{promise,resolve};}
function fakeCombat(op=0,free=1,ap=5){return{round_number:1,current_character:{character_id:7,movement_points_current:op,movement_points_max:op,free_actions_current:free,action_points_current:ap,movement_mode_this_turn:null,movement_distance_this_turn:0},available_actions:[{key:'convert_free_action_to_movement',movement_points_gain:8,free_actions:1,action_points:2}]};}
function readyMovement(){return{menuClosed:true,armedCharacterId:7,mode:'walk',previewCharacterId:7,previewInitialized:true,startX:41,startY:48};}
test('Read-only readiness не принимает открытое меню или неверный/неинициализированный режим',()=>{
    const ready=readyMovement();assert.equal(movement.isMovementReady(ready,7,'walk'),true);
    for(const change of [{menuClosed:false},{armedCharacterId:8},{mode:'run'},{previewCharacterId:null},{previewInitialized:false},{startX:null}])assert.equal(movement.isMovementReady({...ready,...change},7,'walk'),false);
});
test('Конверсия проверяется отдельно: сначала СД, иначе ОД, gain из серверного каталога',()=>{
    assert.deepEqual(movement.conversionExpectation(fakeCombat(0,1,5)),{gain:8,freeCost:1,actionCost:0});
    assert.deepEqual(movement.conversionExpectation(fakeCombat(0,0,5)),{gain:8,freeCost:0,actionCost:2});
    assert.throws(()=>movement.conversionExpectation(fakeCombat(0,0,1)));
    const zero=fakeCombat();zero.available_actions[0].movement_points_gain=0;assert.throws(()=>movement.conversionExpectation(zero));
});
test('Реальная prepareModePreview ждёт async выбор и обоих клиентов до baseline и mouseMoved',async()=>{
    const selection=deferred(),clients=deferred(),waitingSelection=deferred(),waitingClients=deferred();
    const log=[],records=[];let state=fakeCombat(),readiness={...readyMovement(),menuClosed:false,previewInitialized:false};
    const pending=movement.prepareModePreview({mode:'walk',
        readServer:async()=>{log.push('server:'+state.current_character.movement_points_current);return JSON.parse(JSON.stringify(state));},
        readReadiness:async()=>({...readiness}),readUi:async()=>[{op:state.current_character.movement_points_current}],
        record:async(stage,data)=>{log.push('record:'+stage);records.push({stage,data});},
        choose:async()=>{log.push('native click');},
        waitReady:async()=>{log.push('wait armed');waitingSelection.resolve();await selection.promise;assert.ok(movement.isMovementReady(readiness,7,'walk'));},
        syncClients:async()=>{log.push('wait both clients');waitingClients.resolve();await clients.promise;log.push('both acknowledged');},
        baseline:async confirmed=>{log.push('baseline');assert.equal(confirmed.current_character.movement_points_current,8);return{state:confirmed,price:1};},
        pointAt:async()=>{log.push('point');return{x:10,y:20};},movePointer:async()=>{log.push('mouseMoved');}});
    await waitingSelection.promise;
    assert.ok(records.some(record=>record.stage==='before'&&record.data.server.movement_points_current===0));
    assert.ok(records.some(record=>record.stage==='sent'&&record.data.after.movement_points_current===0));
    assert.ok(!log.includes('baseline')&&!log.includes('mouseMoved'));
    state=fakeCombat(8,0,5);readiness=readyMovement();selection.resolve();
    await waitingClients.promise;
    assert.ok(!log.includes('baseline')&&!log.includes('mouseMoved'));
    clients.resolve();const result=await pending;
    assert.equal(result.state.current_character.movement_points_current,8);
    assert.ok(log.indexOf('both acknowledged')<log.indexOf('baseline'));
    assert.ok(log.indexOf('baseline')<log.indexOf('mouseMoved'));
    assert.ok(records.some(record=>record.stage==='ready'&&record.data.after.movement_points_current===8));
    assert.equal(records.at(-1).stage,'pointer-ready');
});
test('При отказе readiness сохранены before/sent, baseline и pointer не выполняются',async()=>{
    const records=[];let touched=false;
    await assert.rejects(movement.prepareModePreview({mode:'walk',readServer:async()=>fakeCombat(),readReadiness:async()=>({menuClosed:false}),readUi:async()=>[],record:async stage=>records.push(stage),choose:async()=>{},waitReady:async()=>{throw Error('not armed');},syncClients:async()=>{touched=true;},baseline:async()=>{touched=true;},pointAt:async()=>{touched=true;},movePointer:async()=>{touched=true;}}),/not armed/);
    assert.deepEqual(records,['before','sent']);assert.equal(touched,false);
});
