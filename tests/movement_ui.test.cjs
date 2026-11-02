const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/locationScene.js'), 'utf8');

test('отмена размещения очищает очередь и призрак, не отправляя перенос',()=>{
    let cleared=0;
    const context=vm.createContext({pendingCharacterPlacement:{gmRelocation:true},pendingCharacterPlacementQueue:[{},{}],
        renderer:{domElement:{style:{cursor:'crosshair'}}},clearCharacterPlacementPreview:()=>cleared++});
    vm.runInContext(functionSource('cancelCharacterPlacement'),context);
    context.cancelCharacterPlacement();
    assert.equal(context.pendingCharacterPlacement,null);assert.equal(context.pendingCharacterPlacementQueue.length,0);
    assert.equal(context.renderer.domElement.style.cursor,'default');assert.equal(cleared,1);
});

test('перенос ГМа использует существующий запрос без смены управляющего',async()=>{
    let payload;
    const context=vm.createContext({window:{currentLobbyId:7,isGM:true},getCurrentLocationId:()=>8,locationViewLevel:0,
        localStorage:{getItem:()=>''},fetch:async(url,options)=>{payload=JSON.parse(options.body);return{ok:true,json:async()=>({})};}});
    vm.runInContext('async '+functionSource('spawnCharacterAtTile'),context);
    await context.spawnCharacterAtTile(5,3.2,3.7,undefined,{forceRelocate:true,floorLevel:2});
    assert.equal(payload.force_relocate,true);assert.equal(payload.floor_level,2);
    assert.equal(payload.tile_x,3.2);assert.equal(Object.hasOwn(payload,'assign_to_user_id'),false);
});

test('при завершении размещения удаляются оба вида предпросмотра',()=>{
    const disposed=[];
    const sprite={material:{map:{dispose:()=>disposed.push('texture')},dispose:()=>disposed.push('material')}};
    const context=vm.createContext({previewSprite:sprite,scene:{remove:item=>disposed.push(item===sprite?'sprite':'other')},clearMovementPreview:()=>disposed.push('ghost')});
    vm.runInContext(functionSource('clearCharacterPlacementPreview'),context);
    context.clearCharacterPlacementPreview();
    assert.equal(context.previewSprite,null);
    assert.deepEqual(disposed,['ghost','sprite','texture','material']);
});

test('повторное размещение ГМом не открывает выбор владельца',async()=>{
    let request,finished=false;
    const context=vm.createContext({window:{isGM:true},clearCharacterPlacementPreview(){},getCharacterModelEntry:()=>({controlledBy:9}),
        spawnCharacterAtTile:async(...args)=>{request=args;},document:{createElement:()=>{throw Error('Окно владельца не требуется');}}});
    vm.runInContext('async '+functionSource('openOwnerSelectionModal'),context);
    await context.openOwnerSelectionModal(5,3.2,3.7,9,{onSuccess:()=>{finished=true;}});
    assert.equal(request[3],undefined);assert.equal(request[4].forceRelocate,true);assert.equal(finished,true);
});

test('цель на улице сбрасывает этаж независимо от этажа персонажа',()=>{
    const context=vm.createContext({navigationPointer:null,movementTargetLevel:1,
        getPointerWorldPoint:()=>({x:25,y:3.4,z:3}),locationObjectMeshes:[],tileCubes:[],
        raycaster:{intersectObjects:objects=>objects===context.tileCubes?[{point:{x:25,y:1,z:3}}]:[]},
        locationHitOccludedByTerrain:()=>false});
    vm.runInContext(functionSource('getMovementPointerPoint'),context);
    const point=context.getMovementPointerPoint(10,10,{floorLevel:1,model:{position:{y:3.4}}});
    assert.equal(point.y,1);assert.equal(context.movementTargetLevel,0);
});

test('обновляемая линия маршрута не исчезает из-за старой области видимости',()=>{
    const context=vm.createContext({THREE:{BufferGeometry:class{setFromPoints(){}},
        Vector3:class{},LineBasicMaterial:class{constructor(options){Object.assign(this,options);}},
        Line:class{constructor(geometry,material){Object.assign(this,{geometry,material});}}}});
    vm.runInContext(functionSource('createPreviewLine'),context);
    const line=context.createPreviewLine(0x00ff00);
    assert.equal(line.frustumCulled,false);assert.equal(line.material.depthWrite,false);
});

test('одноэтажная сцена со стеной использует серверный маршрут для preview', () => {
    const route = { cost: 1, path: [[5,5],[5,6]] };
    let requests = 0;
    const context = vm.createContext({
        currentLocationData: { objects: [{ type: 'wall' }] }, hasStoreyNavigation: () => false,
        serverMovementPreview: () => { requests++; return route; },
    });
    vm.runInContext(functionSource('hasPreciseWallNavigation'),context);
    vm.runInContext(functionSource('findMovementPath'),context);
    assert.equal(context.findMovementPath(5,5,5,6,123,0),route);
    assert.equal(requests,1);
});

test('встроенная в клетку стена также требует серверный preview', () => {
    const context = vm.createContext({currentLocationData:{objects:[],tiles_data:[[{objects:[{type:'wall'}]}]]}});
    vm.runInContext(functionSource('hasPreciseWallNavigation'),context);
    assert.equal(context.hasPreciseWallNavigation(),true);
});

test('указатель движения выбирает десятые метра вместо пиксельной точности', () => {
    const context = vm.createContext({currentLocationData:{grid_width:10,grid_height:10}});
    vm.runInContext(functionSource('movementPointerPosition'),context);
    const point = context.movementPointerPosition({x:6.75,z:5.125});
    assert.equal(point.x,6.3);
    assert.equal(point.y,4.6);
    const same=context.movementPointerPosition({x:6.751,z:5.124});
    assert.equal(same.x,point.x);assert.equal(same.y,point.y);
});

test('дистанция и ОП отображаются до десятых без изменения исходного расчёта',()=>{
    const context=vm.createContext({});vm.runInContext(functionSource('formatMovementNumber'),context);
    assert.equal(context.formatMovementNumber(3.4343467365764),'3.4');
    assert.equal(context.formatMovementNumber(7.999999999),'8');
    assert.equal(context.formatMovementNumber(Infinity),'—');
});

test('видимость между дробными позициями не зацикливает обход клеток', () => {
    let calls = 0;
    const context = vm.createContext({
        currentLocationData:{tiles_data:[],objects:[]},getTileHeight:()=>1,
        getVisionObjectIndex:()=>({terrainCache:new Map(),walls:[]}),terrainLineHit:()=>null,
        getVisionBlockerHeight:()=>{calls++;return -Infinity;},lightPathOpen:()=>true,
    });
    vm.runInContext(functionSource('hasLineOfSight'),context);
    assert.equal(vm.runInContext('hasLineOfSight(6.5,3.5,6.5,5.25)',context,{timeout:100}),true);
    assert.ok(calls<=3);
    assert.equal(context.hasLineOfSight(NaN,0,1,1),false);
});

test('horizontal facing preserves zero Y when calculating turn angle and payment', () => {
    const context=vm.createContext({});
    vm.runInContext(functionSource('getFacingChangeOptions'),context);
    const character={facing_x:1,facing_y:0,posture:'standing'};
    assert.equal(context.getFacingChangeOptions(character,1,0).degrees,0);
    assert.equal(context.getFacingChangeOptions(character,0,1).degrees,90);
    const reverse=context.getFacingChangeOptions(character,-1,0);
    assert.equal(reverse.degrees,180);
    assert.equal(reverse.options.find(o=>o.payment==='action').action,2);
});

test('facing socket API is exported, idempotent and supports one fog refresh per state batch', () => {
    assert.match(source, /export function applyCharacterFacingVisual\(/);
    let refreshes = 0;
    const entry = {model:{rotation:{}},facingX:0,facingY:1};
    const context = vm.createContext({getCharacterModelEntry:()=>entry,applyFogOfWar:()=>refreshes++});
    vm.runInContext(functionSource('applyCharacterFacingVisual'),context);
    context.applyCharacterFacingVisual(1,1,0,{deferRefresh:true});
    assert.equal(entry.model.rotation.y,Math.PI/2);
    assert.equal(refreshes,0);
    context.applyCharacterFacingVisual(1,1,0);
    assert.equal(refreshes,0);
    context.applyCharacterFacingVisual(1,-1,0);
    assert.equal(refreshes,1);
    context.applyCharacterFacingVisual(1,0,0);
    assert.equal(refreshes,1);
});

test('navigation preview deduplicates requests and rejects responses from an old map version', async () => {
    let resolve, requests=0;const sent=[];
    const context=vm.createContext({currentLocationId:1,movementMapVersion:1,combatState:null,armedMovementType:'walk',
        navigationPreviews:new Map(),navigationPreviewRequests:new Set(),navigationPreviewErrors:new Set(),
        navigationPreviewBusy:false,navigationPreviewQueued:null,
        movementPreviewCharacterId:null,window:{currentLobbyId:1},localStorage:{getItem:()=>''},
        fetch:(url,options)=>{sent.push(JSON.parse(options.body));requests++;return new Promise(r=>resolve=r);}});
    for(const name of ['navigationPreviewKey','serverMovementPreview','sendQueuedNavigationPreview'])vm.runInContext(functionSource(name),context);
    context.serverMovementPreview(0,0,3,0,1,0);
    context.serverMovementPreview(0,0,3,0,1,0);
    assert.equal(requests,1);assert.equal(context.navigationPreviewRequests.size,1);
    assert.equal(sent[0].movement_mode,'walk');
    context.movementMapVersion++;
    resolve({ok:true,json:async()=>({route:{path:[[0,0],[3,0]]}})});
    await new Promise(r=>setImmediate(r));
    assert.equal(context.navigationPreviewRequests.size,0);
    assert.equal([...context.navigationPreviews.values()][0],null);
    // While one request runs, intermediate pointer destinations are discarded.
    context.serverMovementPreview(0,0,4,0,1,0);
    context.serverMovementPreview(0,0,5,0,1,0);
    context.serverMovementPreview(0,0,6,0,1,0);
    assert.equal(requests,2);
    resolve({ok:true,json:async()=>({route:{path:[[0,0],[4,0]]}})});
    await new Promise(r=>setImmediate(r));
    assert.equal(requests,3);
    assert.equal([...context.navigationPreviews.keys()].some(key=>key.endsWith(':5:0:0')),false);
    resolve({ok:true,json:async()=>({route:{path:[[0,0],[6,0]]}})});
    await new Promise(r=>setImmediate(r));
    assert.equal(context.navigationPreviewRequests.size,0);
    context.combatState={status:'active',current_character:{character_id:1,facing_x:0,facing_y:1}};
    const walking=context.navigationPreviewKey(0,0,3,0,1,0);
    context.armedMovementType='run';
    assert.notEqual(context.navigationPreviewKey(0,0,3,0,1,0),walking,'смена режима не использует старый маршрут');
    context.serverMovementPreview(0,0,3,0,1,0);
    assert.equal(sent.at(-1).movement_mode,'run','выбранный режим передан серверу');
    resolve({ok:true,json:async()=>({route:null,reason:'Сначала измените направление взгляда.'})});await new Promise(r=>setImmediate(r));
    assert.equal(context.serverMovementPreview(0,0,3,0,1,0).blocked_reason,'Сначала измените направление взгляда.','ограничение режима сохраняется отдельно от коллизии');
    context.armedMovementType='walk';
    context.combatState.current_character.facing_x=1;
    assert.notEqual(context.navigationPreviewKey(0,0,3,0,1,0),walking,'поворот не использует старый маршрут');
});
function functionSource(name) {
    const start = source.indexOf(`function ${name}(`);
    assert.ok(start >= 0);
    return source.slice(start, source.indexOf('\n}', start) + 2);
}

function movementContext(current = {}) {
    const context = vm.createContext({
        armedMovementEvasion: false,
        combatState: { round_number: 1, current_character: {
            action_points_current: 20, free_actions_current: 1,
            movement_points_current: 50, posture: 'standing', ...current,
        } },
    });
    vm.runInContext(functionSource('formatMovementNumber'),context);
    for (const name of ['COMBAT_MOVEMENT_TYPES', 'COMBAT_POSTURES']) {
        const start = source.indexOf(`const ${name} =`);
        vm.runInContext(source.slice(start, source.indexOf('\n};', start) + 3), context);
    }
    vm.runInContext(functionSource('resolveMovementType'), context);
    vm.runInContext(functionSource('getClimbPaymentProfile'), context);
    vm.runInContext(functionSource('getSpecialMovementAvailability'), context);
    vm.runInContext(functionSource('getMovementModeRouteCost'), context);
    vm.runInContext(functionSource('getMovementModeAvailability'), context);
    return context;
}

test('после срыва бега ходьба использует оставшиеся ОП и исходный лимит без повторной платы ОД',()=>{
    const context=movementContext({movement_mode_this_turn:'walk',movement_distance_this_turn:16,
        slippery_movement:{forced_from:'run'},movement_points_current:.25,facing_x:1,facing_y:0});
    context.combatState.status='active';
    const result=context.getMovementModeAvailability('walk',{path:[[6,0],[6.1,0]],cost:.2,distance:.1});
    assert.equal(result.allowed,true);
    assert.equal(result.modeActionPoints,0);
    assert.equal(result.remainingDistance,4);
    assert.equal(result.movementPoints,.2);
    assert.equal(context.getMovementModeAvailability('run').allowed,false);
});

test('evasion shows reduced movement distances and disallows correction', () => {
    const context = movementContext();
    for (const [mode, distance] of [['walk', 7], ['run', 15], ['sprint', 23]]) {
        const result = context.getMovementModeAvailability(mode, null, true);
        assert.equal(result.allowed, true);
        assert.equal(result.remainingDistance, distance);
    }
    assert.equal(context.getMovementModeAvailability('correction', null, true).allowed, false);
});

test('шаг за спину автоматически получает цену и лимит спиной/боком', () => {
    const context = movementContext({facing_x:0,facing_y:1});
    context.combatState.status = 'active';
    const result = context.getMovementModeAvailability('walk',{path:[[1,1],[1,0]],cost:1,distance:1});
    assert.equal(result.allowed,true);
    assert.equal(result.movementType,'backward_sideways');
    assert.equal(result.movementPoints,2);
    assert.equal(result.remainingDistance,5);
    assert.equal(context.getMovementModeAvailability('run',{path:[[1,1],[1,0]],cost:1,distance:1}).allowed,false);
});

test('диагональ до 45° включительно оплачивается вперёд; за границей — спиной/боком',()=>{
    for(const [fx,fy] of [[0,1],[1,0],[1,1],[-1,1],[-1,-1],[1,-1],[0,0]]){
        const context=movementContext({facing_x:fx,facing_y:fy});
        context.combatState.status='active';
        for(const angle of [0,44.999,45,-45,45.001,-45.001,60,90,180]){
            const x=fx||0,y=(fx||fy)?fy:1,r=angle*Math.PI/180;
            const route={path:[[0,0],[0,0],[x*Math.cos(r)-y*Math.sin(r),x*Math.sin(r)+y*Math.cos(r)]],cost:1,distance:1};
            const forward=Math.abs(angle)<=45,result=context.getMovementModeAvailability('walk',route);
            assert.equal(result.allowed,true,`${fx},${fy}: ${angle}°`);
            assert.equal(result.movementType,forward?'walk':'backward_sideways');
            assert.equal(result.movementPoints,forward?1:2);
            assert.equal(result.remainingDistance,forward?10:5);
            assert.equal(context.getMovementModeAvailability('backward_sideways',route).allowed,!forward);
            for(const mode of ['run','sprint'])assert.equal(context.getMovementModeAvailability(mode,route).allowed,forward);
        }
    }
});

test('диагональная цель не отменяет боковой участок обхода препятствия',()=>{
    const context=movementContext({facing_x:0,facing_y:1});
    context.combatState.status='active';
    const result=context.getMovementModeAvailability('walk',{path:[[0,0],[0,1],[1,1]],cost:2,distance:2});
    assert.equal(result.allowed,false);
    assert.match(result.reason,/смешивает/);
});

test('лимиты учитывают штраф, бонус и округление ползком', () => {
    const slow=movementContext({movement_penalty:3});
    for(const [mode,limit] of [['walk',7],['run',14],['sprint',21],['backward_sideways',3],['correction',3]]) {
        assert.equal(slow.getMovementModeAvailability(mode).remainingDistance,limit);
    }
    assert.equal(movementContext({movement_penalty:-2}).getMovementModeAvailability('walk').remainingDistance,12);
    assert.equal(movementContext({posture:'prone'}).getMovementModeAvailability('walk').remainingDistance,4);
});

test('корректировка допускает основной режим, списывается один раз и входит в общий лимит', () => {
    const context=movementContext({movement_penalty:3,movement_distance_this_turn:1.5,
        correction_distance_this_turn:1.5,free_actions_current:0});
    assert.equal(context.getMovementModeAvailability('correction',{cost:1,distance:1}).modeFreeActions,0);
    assert.equal(context.getMovementModeAvailability('walk',{cost:5.5,distance:5.5}).allowed,true);
    assert.equal(context.getMovementModeAvailability('walk',{cost:5.75,distance:5.75}).allowed,false);
    context.combatState.current_character.movement_mode_this_turn='walk';
    context.combatState.current_character.movement_distance_this_turn=6.5;
    assert.equal(context.getMovementModeAvailability('correction',{cost:.5,distance:.5}).allowed,true);
    assert.equal(context.getMovementModeAvailability('correction',{cost:.75,distance:.75}).allowed,false);
});

test('continuing movement uses authoritative evasion and does not repay run AP', () => {
    const context = movementContext({
        movement_mode_this_turn: 'run', movement_evasion: true,
        movement_distance_this_turn: 14, strenuous_movement_blocked_until_round: 2,
    });
    const allowed = context.getMovementModeAvailability('run', { cost: 1, distance: 1 }, false);
    assert.equal(allowed.allowed, true);
    assert.equal(allowed.remainingDistance, 1);
    assert.equal(allowed.modeActionPoints, 0);
    assert.equal(context.getMovementModeAvailability('run', { cost: 2, distance: 2 }).allowed, false);
});

test('remembered models stay at the observed position and cannot be picked', () => {
    const disabledRaycast = () => {};
    const child = { isMesh: true };
    const root = {
        userData: {}, rotation: {}, position: { set(...values) { this.values = values; } },
        traverse(callback) { callback(child); },
    };
    const context = vm.createContext({
        createCharacterModel: () => root,
        createMutantModel: () => null,
        setFogGhostAppearance: (model) => { child.raycast = disabledRaycast; return model; },
        applyFogGhostPosture() {},
    });
    vm.runInContext(functionSource('createCharacterFogGhost'), context);
    const ghost = context.createCharacterFogGhost({ posX: 5, posY: 3, height: 1, posture: 'standing' });
    assert.equal(child.raycast, disabledRaycast);
    assert.deepEqual(root.position.values, [5.5, 1, 3.5]);
    assert.equal(ghost.userData.rememberedCharacter, undefined);
});

test('a crouching target hidden by a low cover keeps its last visible snapshot', () => {
    const entry = { posX: 2, posY: 0, posture: 'standing', facingY: 0 };
    const memory = new Map([['2:0', { height: 1 }], ['3:0', { height: 1 }]]);
    const context = vm.createContext({
        fogMemory: memory, characterModels: new Map([[42, entry]]),
        getTileHeight: () => 1,
        isPointVisibleToPlayer: (x, y, height) => x === 2 && height > 2.5,
        isTileVisibleToPlayer: () => false,
    });
    vm.runInContext(functionSource('isCharacterVisibleToPlayer'), context);
    vm.runInContext(functionSource('rememberVisibleCharacters'), context);
    context.rememberVisibleCharacters([]);
    const snapshot = JSON.stringify(memory.get('2:0').characters);
    entry.posture = 'sitting';
    assert.equal(context.isCharacterVisibleToPlayer(entry, []), false);
    assert.equal(context.rememberVisibleCharacters([]), false);
    entry.posX = 3;
    context.rememberVisibleCharacters([]);
    assert.equal(JSON.stringify(memory.get('2:0').characters), snapshot);
    assert.equal(memory.get('3:0').characters, undefined);
    context.isPointVisibleToPlayer = () => true;
    context.rememberVisibleCharacters([]);
    assert.equal(memory.get('2:0').characters.length, 0);
    assert.equal(memory.get('3:0').characters[0].posX, 3);
});

test('an empty visible tile forgets a removed or moved character', () => {
    const memory = new Map([['2:0', { characters: [{ characterId: 42, posX: 2, posY: 0 }] }]]);
    const models = new Map();
    const context = vm.createContext({
        fogMemory: memory, characterModels: models,
        isTileVisibleToPlayer: () => true,
        isCharacterVisibleToPlayer: () => false,
    });
    vm.runInContext(functionSource('rememberVisibleCharacters'), context);
    assert.equal(context.rememberVisibleCharacters([]), true);
    assert.equal(memory.get('2:0').characters.length, 0);
    memory.get('2:0').characters = [{ characterId: 42, posX: 2, posY: 0 }];
    models.set(42, { posX: 3, posY: 0 });
    assert.equal(context.rememberVisibleCharacters([]), true);
    assert.equal(memory.get('2:0').characters.length, 0);
});

test('actual sight ray sees a standing head but not a crouching head behind low cover', async () => {
    const geometryUrl = 'data:text/javascript;base64,' + Buffer.from(fs.readFileSync(path.join(__dirname, '../app/static/js/locationTerrain.js'), 'utf8')).toString('base64');
    const lineSource = fs.readFileSync(path.join(__dirname, '../app/static/js/terrainLineOfSight.js'), 'utf8').replace("'./locationTerrain.js'", JSON.stringify(geometryUrl));
    const terrain = await import('data:text/javascript;base64,' + Buffer.from(lineSource).toString('base64'));
    const context = vm.createContext({
        FOG_VIEW_RADIUS: 25, FOG_VIEW_HALF_ANGLE: Math.PI / 3,
        getTileHeight: () => 1,
        getVisionBlockerHeight: (x, y) => x === 1 && y === 0 ? 2.5 : -Infinity,
        darknessFactorAt: () => 1,
        lightPathOpen: () => true,
        terrainLineHit: terrain.terrainLineHit, terrainOpeningAt: terrain.terrainOpeningAt,
        getVisionObjectIndex: () => ({walls:[],terrainCache:new Map()}),
        currentLocationData: { objects: [], tiles_data: [[{height:1},{height:1},{height:1}]] },
    });
    for (const name of ['visionEyeHeight', 'hasLineOfSight', 'isPointVisibleToPlayer', 'isCharacterVisibleToPlayer']) {
        vm.runInContext(functionSource(name), context);
    }
    const sources = [{ posX: 0, posY: 0, posture: 'standing', facingX: 1, facingY: 0 }];
    assert.equal(context.isCharacterVisibleToPlayer({ posX: 2, posY: 0, posture: 'standing' }, sources), true);
    assert.equal(context.isCharacterVisibleToPlayer({ posX: 2, posY: 0, posture: 'sitting' }, sources), false);
    assert.equal(context.isCharacterVisibleToPlayer({ posX: 2, posY: 0, posture: 'prone' }, sources), false);
    const sideObserver = { posX: 2, posY: 2, posture: 'standing', facingX: 0, facingY: -1 };
    assert.equal(context.isCharacterVisibleToPlayer({ posX: 2, posY: 0, posture: 'sitting' }, [sideObserver]), true);
    assert.equal(context.isCharacterVisibleToPlayer({ posX: 2, posY: 0, posture: 'sitting' }, [...sources, sideObserver]), true);
    const rearObserver = { posX: 4, posY: 0, posture: 'standing', facingX: -1, facingY: 0 };
    assert.equal(context.isCharacterVisibleToPlayer({ posX: 2, posY: 0, posture: 'sitting' }, [rearObserver]), true);
    assert.equal(context.isCharacterVisibleToPlayer({ posX: 2, posY: 3, posture: 'standing' }, sources), true);
    context.darknessFactorAt = () => 0.07;
    assert.equal(context.isCharacterVisibleToPlayer({ posX: 2, posY: 3, posture: 'standing' }, sources), false);
});

test('evasion is an unchecked independent checkbox next to automatic MP conversion', () => {
    const context = movementContext();
    const buttons = [];
    const options = { appendChild(button) { buttons.push(button); } };
    const checkbox = {};
    const menu = {
        style: {}, innerHTML: '',
        querySelector(selector) { return selector === '.movement-type-options' ? options : checkbox; },
    };
    Object.assign(context, {
        ensureMovementTypeMenu: () => menu,
        movementModeSummary: () => '',
        closeMovementTypeMenu() {},
        document: { createElement: () => ({ style: {} }) },
    });
    vm.runInContext(functionSource('showMovementTypeMenu'), context);
    context.showMovementTypeMenu(1);
    const input = menu.innerHTML.match(/<input[^>]*id="movement-evasion"[^>]*>/)[0];
    assert.ok(!input.includes('checked'));
    assert.ok(menu.innerHTML.includes('id="movement-gain-op-if-needed"'));
    assert.equal(buttons.length, 5);
    buttons.length = 0;
    context.showMovementTypeMenu(1, true);
    assert.match(menu.innerHTML, /id="movement-evasion" checked/);
    assert.equal(buttons.length, 5);
});


function traversalContext() {
    const context = vm.createContext({});
    for (const name of ['LOW_CLIMB_OBJECT_TYPES', 'HIGH_CLIMB_OBJECT_TYPES',
        'TOO_HIGH_OBJECT_TYPES', 'PASSABLE_OBJECT_TYPES']) {
        const start = source.indexOf(`const ${name} =`);
        assert.ok(start >= 0);
        vm.runInContext(source.slice(start, source.indexOf(';', start) + 1), context);
    }
    for (const name of ['getObjectTraversalHeight', 'getMovementObjectProfile']) {
        vm.runInContext(functionSource(name), context);
    }
    return object => JSON.parse(JSON.stringify(context.getMovementObjectProfile(object)));
}

test('Оба отрицательных флага отменяют цену перелезания chair/fence', () => {
    const profile = traversalContext();
    for (const type of ['chair', 'fence']) {
        for (const flag of ['blocks_movement', 'block_movement']) {
            assert.deepEqual(profile({ type, properties: { [flag]: false, climb_cost: 9 } }),
                { blocked: false, climbCost: 0 }, `${type}: ${flag}`);
            const other = flag === 'blocks_movement' ? 'block_movement' : 'blocks_movement';
            assert.deepEqual(profile({ type, properties: { [flag]: false, [other]: true } }),
                { blocked: false, climbCost: 0 }, `${type}: ${flag} при втором true`);
        }
    }
});

test('Обычное перелезание сохраняет цены и пользовательскую стоимость', () => {
    const profile = traversalContext();
    for (const [type, cost] of [['chair', 5], ['fence', 12]]) {
        assert.deepEqual(profile({ type }), { blocked: false, climbCost: cost });
        assert.deepEqual(profile({ type, properties: { climb_cost: 8 } }),
            { blocked: false, climbCost: 8 });
    }
});

test('Категория по высоте совпадает с серверными границами и допускает переопределение ГМа',()=>{
    const profile=traversalContext();
    for(const [height,blocked,cost] of [[.49,false,0],[.5,false,5],[.99,false,5],[1,false,12],[1.99,false,12],[2,true,0]]) {
        assert.deepEqual(profile({type:'wall',properties:{dimensions:{height}}}),{blocked,climbCost:cost});
    }
    assert.deepEqual(profile({type:'wall',properties:{dimensions:{height:2.5},traversal_category:'low'}}),{blocked:false,climbCost:5});
});

test('Два препятствия получают два бонуса Ловкости; оплата ОД выбирается явно',()=>{
    const context=movementContext({climb_agility_bonus:2,movement_points_current:100});
    const route={cost:16,distance:6,climbCost:10,climbs:[{base_cost:5,category:'low'},{base_cost:5,category:'low'}]};
    const op=context.getMovementModeAvailability('walk',{...route,climbPayment:'movement'});
    const ap=context.getMovementModeAvailability('walk',{...route,climbPayment:'action'});
    assert.equal(op.movementPoints,12);assert.equal(op.modeActionPoints,0);
    assert.equal(ap.movementPoints,6);assert.equal(ap.modeActionPoints,2);
    context.combatState.current_character.movement_points_current=0;
    assert.equal(context.getMovementModeAvailability('walk',{...route,climbPayment:'movement'}).allowed,false);
});

test('Подъём и спуск показывают фиксированную цену действия без цены шага или лишних СД',()=>{
    const context=movementContext({climb_agility_bonus:-2,movement_points_current:100});
    const route={special_action:'climb_up',cost:12,climb_cost:12,distance:1,
        climbs:[{base_cost:12,category:'high'}]};
    const op=context.getMovementModeAvailability('walk',{...route,climbPayment:'movement'});
    const ap=context.getMovementModeAvailability('walk',{...route,climbPayment:'action'});
    assert.equal(op.movementPoints,14);assert.equal(op.modeActionPoints,0);assert.equal(op.modeFreeActions,0);
    assert.equal(ap.movementPoints,0);assert.equal(ap.modeActionPoints,3);
    const down=context.getMovementModeAvailability('walk',{special_action:'descend',height_change:-2,distance:1,climbPayment:'action'});
    assert.equal(down.allowed,true);assert.equal(down.movementPoints,0);assert.equal(down.modeActionPoints,1);
});

test('Door/window/passable сохраняют приоритет над отрицательными флагами', () => {
    const profile = traversalContext();
    for (const flag of ['blocks_movement', 'block_movement']) {
        for (const type of ['door', 'window']) {
            assert.deepEqual(profile({ type, properties: { [flag]: false } }),
                { blocked: true, climbCost: 0 });
            assert.deepEqual(profile({ type, properties: { [flag]: false, passable: true } }),
                { blocked: false, climbCost: 0 });
        }
        assert.deepEqual(profile({ type: 'door', properties: { [flag]: false, is_open: true } }),
            { blocked: false, climbCost: 0 });
        assert.deepEqual(profile({ type: 'door', properties: { [flag]: false, climbable: true } }),
            { blocked: false, climbCost: 12 });
        assert.deepEqual(profile({ type: 'door', properties: { [flag]: false, climbable: true, climb_cost: 7 } }),
            { blocked: false, climbCost: 7 });
    }
    assert.deepEqual(profile({ type: 'floor' }), { blocked: false, climbCost: 0 });
});
