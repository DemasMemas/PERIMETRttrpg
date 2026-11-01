const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('app/static/js/locationScene.js','utf8');
const fn=source.slice(source.indexOf('function updateJumpPreview('),source.indexOf('function updateAttackPreview('));
function setup(fetch){
    let timer;
    const hint={style:{},textContent:''},line={geometry:{setFromPoints:p=>{line.points=p;}},material:{color:{setHex:c=>{line.color=c;}}}};
    const context=vm.createContext({pendingCombatAction:{actorCharacterId:7},currentLocationData:{grid_width:20,grid_height:20},
        movementMapVersion:1,jumpPreviewVisible:false,attackPreviewLine:null,combatState:{status:'active'},window:{currentLobbyId:2},localStorage:{getItem:()=>''},
        getCurrentLocationId:()=>3,getPointerWorldPoint:()=>({x:1.84,z:2.64}),ensureMovementPreviewHint:()=>hint,
        createPreviewLine:()=>line,scene:{add(){}},THREE:{Vector3:class{constructor(x,y,z){Object.assign(this,{x,y,z});}}},
        formatMovementNumber:n=>String(Math.round(n*10)/10),fetch,setTimeout:f=>{timer=f;return 1;},clearTimeout(){}});
    vm.runInContext(fn,context);
    context.updateJumpPreview(100,200,{physicalHeight:3.435,model:{position:{y:3.435,clone:()=>({x:.5,y:3.435,z:.5})}}});
    return{context,hint,line,finish:()=>timer()};
}
test('предпросмотр сохраняет десятые метра и рисует высоты настоящего серверного плана',async()=>{
    let sent;
    const ui=setup(async(url,options)=>{sent=JSON.parse(options.body);return{ok:true,json:async()=>({
        route:{distance:2.5,surfaces:[{x:0,y:0,height:3.435},{x:1.3,y:2.1,height:4.435}]},
        jump:{max_distance:4.8,difficulty:6,has_action_points:true}})};});
    await ui.finish();
    assert.deepEqual(sent,{character_id:7,special_action:'jump',x:1.3,y:2.1});
    assert.equal(ui.line.points[1].y,4.555);
    assert.equal(ui.line.color,0x72e8ae);
    assert.match(ui.hint.textContent,/2.5 м \/ 4.8 м.*СЛ6.*2 ОД/);
});
test('ответ старого предпросмотра не возвращает линию после отмены выбора',async()=>{
    let done;
    const gate=new Promise(resolve=>{done=resolve;});
    const ui=setup(async()=>{await gate;return{ok:true,json:async()=>({route:{surfaces:[]},jump:{}})};});
    const pending=ui.finish();ui.context.pendingCombatAction=null;
    done();await pending;
    assert.equal(ui.line.color,0xff8c42);
    assert.equal(ui.hint.textContent,'Проверка прыжка…');
});
test('серверный запрет показывает причину и не заменяется локальной допустимостью',async()=>{
    const ui=setup(async()=>({ok:false,json:async()=>({error:{message:'Перепад выше 1 м'}})}));
    await ui.finish();
    assert.equal(ui.line.color,0xff7777);
    assert.equal(ui.hint.textContent,'Перепад выше 1 м');
});

test('двойной клик по точке не отправляет второй прыжок до ответа первого',async()=>{
    let release,calls=0,cleared=0;
    const gate=new Promise(resolve=>{release=resolve;});
    const actor={character_id:7};
    const context=vm.createContext({pendingCombatAction:{actorCharacterId:7,actorLocationCharacterId:8,actionKey:'jump',targetType:'point'},
        findCombatCharacterByCharacterId:()=>actor,characterModels:new Map([[7,{model:{position:{y:1}}}]]),
        getPointerWorldPoint:()=>({x:3.8,z:2.5}),currentLocationData:{grid_width:20,grid_height:20},
        Server:{performLocationCombatAction:async()=>{calls++;await gate;return{state:{},jump:{outcome:'midpoint',check:{total:1,difficulty:10}}};}},
        window:{currentLobbyId:1},getCurrentLocationId:()=>3,setCombatState(){},showNotification(){},
        clearPendingCombatAction(){cleared++;context.pendingCombatAction=null;}});
    const start=source.indexOf('async function resolveCombatPointSelection(');
    vm.runInContext(source.slice(start,source.indexOf('\n}',start)+2),context);
    const first=context.resolveCombatPointSelection(1,2);
    assert.equal(await context.resolveCombatPointSelection(1,2),true);
    assert.equal(calls,1);
    release();assert.equal(await first,true);assert.equal(cleared,1);
});
