// Ограниченный сценарий #58; #96 — точные кандидаты/preview, без телепорта.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
function serverMovementRules(source=fs.readFileSync(path.resolve(__dirname,'../app/services/combat.py'),'utf8')) {
    const start=source.indexOf('MOVEMENT_MODES = {');assert.ok(start>=0);
    const block=source.slice(start,source.indexOf('\n}',start)+2),rules={};
    for(const mode of ['walk','run']){
        const entry=block.match(new RegExp("'"+mode+"':\\s*\\{([^}]+)\\}"));assert.ok(entry,'серверный режим '+mode);
        const number=key=>{const match=entry[1].match(new RegExp("'"+key+"':\\s*([0-9.]+)"));assert.ok(match,key);return Number(match[1]);};
        rules[mode]={label:entry[1].match(/'label':\s*'([^']+)'/)[1],divisor:number('movement_divisor'),actionPoints:number('action_points'),freeActions:number('free_actions')};
    }
    return rules;
}
function expectedMovementCost(route,rule,multiplier){
    assert.ok(Number.isFinite(multiplier)&&multiplier>0);assert.ok(rule.divisor>0);
    const climb=Number(route.climb_cost||0),travel=Math.max(0,route.cost-climb);
    return Math.round((travel*multiplier/rule.divisor+climb)*1e9)/1e9;
}
function expectedSurfaceRouteCost(start,end,fixture){
    const dx=end.x-start.x,dy=end.y-start.y,length=Math.hypot(dx,dy);
    if(!length)return 0;
    const cuts=new Set([0,1]);
    for(const [axis,delta] of [['x',dx],['y',dy]]){
        if(!delta)continue;
        const lo=Math.min(start[axis]+.5,end[axis]+.5),hi=Math.max(start[axis]+.5,end[axis]+.5);
        for(let boundary=Math.floor(lo)+1;boundary<Math.ceil(hi);boundary++){
            const t=(boundary-start[axis]-.5)/delta;if(t>0&&t<1)cuts.add(t);
        }
    }
    const ordered=[...cuts].sort((a,b)=>a-b);let cost=0;
    for(let index=0;index<ordered.length-1;index++){
        const t=(ordered[index]+ordered[index+1])/2;
        const x=Math.floor(start.x+dx*t+.5),y=Math.floor(start.y+dy*t+.5);
        const tile=fixture.tiles_data?.[y]?.[x]||{};
        const difficult=tile.difficult_surface??(['snow','sand','swamp','ice'].includes(tile.terrain)
            ||(fixture.objects||[]).some(object=>object.tile_x===x&&object.tile_y===y
                &&/^nature_(bush|tall_grass)_(green|dry|dead)$/.test(object.properties?.visual_asset||'')));
        cost+=length*(ordered[index+1]-ordered[index])*(difficult?2:1);
    }
    return Math.round(cost*1e9)/1e9;
}
function navigationCandidates(fixture){
    const stairs=fixture.objects.flatMap((object,index)=>object.type==='stairs'?[{sourceIndex:index,x:object.tile_x,y:object.tile_y,level:object.properties.level||0,building:object.properties.building_id,rotation:object.properties.rotation||0,dimensions:object.properties.dimensions}]:[]);
    const stair=stairs.find(s=>s.x===7&&s.y===9&&s.level===0&&s.building==='pripyat-0');assert.ok(stair,'существующий марш Припяти');
    const walls=fixture.objects.flatMap((object,index)=>object.type==='wall'&&object.properties?.building_id==='pripyat-0'&&(object.properties.level||0)===0&&object.tile_y===9&&[6,9].includes(object.tile_x)?[{sourceIndex:index,x:object.tile_x+.5+(object.properties.offset_x||0),z:object.tile_y+.5+(object.properties.offset_z||0),dimensions:object.properties.dimensions,rotation:object.properties.rotation}]:[]);
    return {status:'NEEDS_REPRO',radiusRule:'NEEDS_RULE: физический радиус персонажа не задан, не вводится тестом',stairs,
        selectedStair:{...stair,from:[7,6,0],to:[7,11,1]},corridor:{from:[8,7,0],to:[8,11,0],walls},
        limitation:'Исходные start/end игрового дефекта неизвестны; preview от текущей позиции не равен прохождению этого марша/коридора'};
}
function movementUISnapshotExpression(actorId){
    assert.ok(Number.isInteger(actorId));
    return `(()=>{const root=document.querySelector('#combat-status-panel .combat-hud-body');const line=[...root.querySelectorAll('div')].find(node=>node.textContent.trim().startsWith('ОП: '));return{line:line?.textContent.trim(),visible:Boolean(root.getClientRects().length&&getComputedStyle(root).display!=='none'),hint:L.movementHintSnapshot(),pose:L.characterPose(${actorId}),events:movementEvidenceEvents.slice(-12)}})()`;
}
function resourceSnapshot(current){
    return Object.fromEntries(['character_id','location_character_id','movement_points_current','movement_points_max','action_points_current','free_actions_current','movement_mode_this_turn','movement_distance_this_turn','posture'].map(key=>[key,current[key]]));
}
function isMovementReady(snapshot,actorId,mode){
    return snapshot.menuClosed===true&&snapshot.armedCharacterId===actorId&&snapshot.mode===mode
        &&snapshot.previewCharacterId===actorId&&snapshot.previewInitialized===true
        &&Number.isFinite(snapshot.startX)&&Number.isFinite(snapshot.startY);
}
function conversionExpectation(state){
    const action=state.available_actions.find(action=>action.key==='convert_free_action_to_movement');
    assert.ok(action&&action.movement_points_gain>0,'сервер разрешает положительный добор ОП');
    const current=state.current_character,free=Number(action.free_actions),ap=Number(action.action_points);
    assert.ok(free>0&&ap>0);
    const useFree=current.free_actions_current>=free;
    assert.ok(useFree||current.action_points_current>=ap,'серверных ресурсов хватает на конверсию');
    return{gain:Number(action.movement_points_gain),freeCost:useFree?free:0,actionCost:useFree?0:ap};
}
async function selectModeReady(options){
    const before=await options.readServer();
    await options.record('before',{server:resourceSnapshot(before.current_character),readiness:await options.readReadiness(),ui:await options.readUi()});
    await options.choose(options.mode,before);
    const sent=await options.readServer();
    await options.record('sent',{before:resourceSnapshot(before.current_character),after:resourceSnapshot(sent.current_character),readiness:await options.readReadiness()});
    await options.waitReady(options.mode);
    const after=await options.readServer();
    await options.record('ready',{before:resourceSnapshot(before.current_character),after:resourceSnapshot(after.current_character),readiness:await options.readReadiness()});
    await options.syncClients(after);
    await options.waitReady(options.mode);
    await options.record('synced',{server:resourceSnapshot(after.current_character),ui:await options.readUi(),readiness:await options.readReadiness()});
    return{before,after};
}
async function prepareModePreview(options){
    const selection=await selectModeReady(options);
    const baseline=await options.baseline(selection.after);
    const point=await options.pointAt(baseline);
    await options.waitReady(options.mode);
    await options.record('pointer-ready',{readiness:await options.readReadiness()});
    await options.movePointer(point);
    return{...baseline,point};
}
module.exports=async({page,request,token,base,connect,debugPort,safeSession,fixture,location,actor,player,lobby})=>{
    assert.ok(safeSession&&process.env.VTT_SAFE_DB_MODE==='owned-file');
    const url=`/lobbies/${lobby.id}/locations/${location.id}`,rules=serverMovementRules();
    const result={card58:'6ae489287a7e1a601e3b8316',card96:'6ae516d57babdb5e827ad219',status:'RUNNING',phases:[],navigation:navigationCandidates(fixture)};
    const save=()=>fs.writeFileSync(path.join(safeSession.artifacts,'movement-regression.json'),JSON.stringify(result,null,2));save();
    const server=()=>request(url+'/combat',null,token,'GET');
    const preview=(x,y,level=0)=>request(url+'/movement-preview',{character_id:actor.id,x,y,floor_level:level},token);
    const click=async(client,selector,label=null)=>{
        await client.call('Page.bringToFront');
        const expression=`(()=>{const all=[...document.querySelectorAll(${JSON.stringify(selector)})];const node=${label===null?'all[0]':`all.find(node=>node.querySelector('strong')?.textContent.trim()===${JSON.stringify(label)})`};if(!node||node.disabled)throw Error('Нет доступной кнопки');node.scrollIntoView({block:'center'});const r=node.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`;
        const point=await client.evaluate(expression);
        for(const type of ['mousePressed','mouseReleased'])await client.call('Input.dispatchMouseEvent',{type,...point,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1});
    };
    const {webSocketDebuggerUrl}=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();
    const protocol=await connect(webSocketDebuggerUrl);let peer,context;
    try{
        context=await protocol.call('Target.createBrowserContext');
        const target=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
        const targets=await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
        peer=await connect(targets.find(item=>item.id===target.targetId).webSocketDebuggerUrl);
        await peer.call('Page.enable');await peer.call('Runtime.enable');
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(player.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(player.user_id))})`);
        await peer.call('Page.navigate',{url:base+`/lobbies/${lobby.id}/page`});
        await peer.until(`typeof enterLocation==='function'&&!window.isGM`,'клиент игрока');
        await peer.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');window.T=await import('three');await enterLocation(${location.id})})()`);
        await peer.until(`window.locationControls&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'локация игрока');
        for(const client of [page,peer])await client.evaluate(`window.movementEvidenceEvents=[];socket.on('combat_state_updated',state=>{const c=state.current_character;if(c)movementEvidenceEvents.push({round:state.round_number,op:c.movement_points_current,ap:c.action_points_current})});true`);
        const initial=await server();assert.equal(initial.characters.length,1,'один обычный участник для штатной смены хода');
        await request(url+'/combat/start',{location_character_ids:initial.characters.map(c=>c.location_character_id),initiator_location_character_id:initial.characters[0].location_character_id,initiator_first:true},token);
        for(const client of [page,peer]){
            await client.until(`L.getCombatState()?.status==='active'&&L.getCombatState().current_character.character_id===${actor.id}`,'бой синхронизирован');
            if(await client.evaluate(`getComputedStyle(document.querySelector('#combat-status-panel .combat-hud-body')).display==='none'`))await click(client,'#combat-status-panel .combat-hud-collapse-btn');
        }
        const ui=client=>client.evaluate(movementUISnapshotExpression(actor.id));
        const numericHud=snapshot=>{assert.ok(snapshot.visible,'остаток ОП виден');const match=snapshot.line?.match(/^ОП: ([0-9.]+)\/([0-9.]+)$/);assert.ok(match,'видимая надпись ОП');return{current:Number(match[1]),max:Number(match[2])};};
        const readReadiness=()=>peer.evaluate('L.movementReadinessSnapshot()');
        const choose=async(mode,state)=>{
            await peer.evaluate(`L.showMovementTypeMenu(${actor.id})`);
            const text=await peer.evaluate(`document.getElementById('combat-movement-type-menu').innerText`);
            assert.ok(text.includes('ОП '+state.current_character.movement_points_current),'ресурсы меню соответствуют серверу');
            await click(peer,'#combat-movement-type-menu .movement-type-options button',rules[mode].label);
        };
        const syncClients=async(state)=>{
            const current=state.current_character;
            for(const client of [page,peer])await client.until(`(()=>{const c=L.getCombatState()?.current_character;return c&&c.character_id===${actor.id}&&L.getCombatState().round_number===${state.round_number}&&c.movement_points_current===${current.movement_points_current}&&c.movement_points_max===${current.movement_points_max}&&c.action_points_current===${current.action_points_current}&&c.free_actions_current===${current.free_actions_current}})()`,'ресурсы подтверждены обоими клиентами');
            for(const client of [page,peer]){
                await client.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
                const snapshot=await ui(client);assert.equal(numericHud(snapshot).current,current.movement_points_current);assert.equal(numericHud(snapshot).max,current.movement_points_max);
            }
        };
        const selectionOptions=(mode,label)=>({mode,readServer:server,readReadiness,choose,
            readUi:()=>Promise.all([page,peer].map(ui)),syncClients,
            waitReady:key=>peer.until(`(${isMovementReady.toString()})(L.movementReadinessSnapshot(),${actor.id},${JSON.stringify(mode)})`,'меню закрыто, нужный режим и preview вооружены'),
            record:async(stage,data)=>{result.phases.push({stage:label+':'+stage,...data});save();}});
        const ensureMovementBudget=async(label)=>{
            const state=await server();
            result.phases.push({stage:label+':budget-before',server:resourceSnapshot(state.current_character),ui:await Promise.all([page,peer].map(ui)),readiness:await readReadiness()});save();
            if(state.current_character.movement_points_current>0){await syncClients(state);return state;}
            conversionExpectation(state); // Проверка достаточности перед реальным UI-действием.
            const conversion=await selectModeReady(selectionOptions('walk',label+':auto-gain'));
            const expected=conversionExpectation(conversion.before);
            const before=conversion.before.current_character,after=conversion.after.current_character;
            const actual={gain:after.movement_points_current-before.movement_points_current,freeCost:before.free_actions_current-after.free_actions_current,actionCost:before.action_points_current-after.action_points_current};
            result.phases.push({stage:label+':budget-paid',expected,actual,before:resourceSnapshot(before),after:resourceSnapshot(after)});save();
            assert.deepEqual(actual,expected,'конверсия оплачена по серверному контракту отдельно от шага');
            assert.equal(after.movement_points_max,before.movement_points_max+expected.gain);
            assert.equal(after.movement_mode_this_turn,before.movement_mode_this_turn);assert.equal(after.movement_distance_this_turn,before.movement_distance_this_turn);
            return conversion.after;
        };
        const pointAt=async(x,y,height,client=peer)=>{
            await client.call('Page.bringToFront');
            // CDP передаёт указатель целыми пикселями. В общем плане полпикселя
            // попадало в соседнюю десятую метра; приближаем обычную камеру.
            await client.evaluate(`(()=>{const delta=new T.Vector3(${x+.5}-locationControls.target.x,${height}-locationControls.target.y,${y+.5}-locationControls.target.z);locationControls.object.position.add(delta);locationControls.target.add(delta);locationControls.object.zoom=4;locationControls.object.updateProjectionMatrix();locationControls.update();locationControls.object.updateMatrixWorld(true)})()`);
            return client.evaluate(`(()=>{const p=new T.Vector3(${x+.5},${height},${y+.5}).project(locationControls.object),r=document.querySelector('#location-canvas canvas').getBoundingClientRect();return{x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2}})()`);
        };
        let expectedPosition={x:41,y:48};
        const armPreview=async(mode,x,y)=>{
            const prepared=await prepareModePreview({...selectionOptions(mode,'select-'+mode),
                baseline:async(state)=>{
                    const route=(await preview(x,y)).route;assert.ok(route);
                    if(process.env.VTT_SAFE_SCENARIO==='surface')assert.equal(route.cost,expectedSurfaceRouteCost(expectedPosition,{x,y},fixture),'№102: цена пути по клеткам сложной поверхности');assert.equal(route.climb_cost,0,'проверяется обычный шаг без цены перелезания');
                    const price=expectedMovementCost(route,rules[mode],state.current_character.posture_modifiers.movement_multiplier);
                    result.phases.push({stage:'baseline-'+mode,target:[x,y],server:resourceSnapshot(state.current_character),price,readiness:await readReadiness()});save();
                    return{state,route,price};
                },pointAt:({route})=>pointAt(x,y,route.surfaces.at(-1).height),
                movePointer:point=>peer.call('Input.dispatchMouseEvent',{type:'mouseMoved',...point,buttons:0})});
            const {state,route,price,point}=prepared;
            const start=await peer.evaluate(`L.characterPose(${actor.id})`);
            try{
                await peer.until(`L.navigationPreviewSettled(${start.x},${start.y},${x},${y},${actor.id},0)`,'серверный preview шага');
                await peer.until(`L.movementHintSnapshot().visible&&/ОП [0-9.]+\\/[0-9.]+/.test(L.movementHintSnapshot().text)`,'надпись после отрисовки готового маршрута');
            }catch(error){
                result.previewFailure={mode,target:[x,y],start,point,ui:await ui(peer),diagnostics:await peer.evaluate('L.navigationPreviewDiagnostics()')};save();
                fs.writeFileSync(path.join(safeSession.artifacts,'movement-preview-failure.png'),Buffer.from((await peer.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
                throw error;
            }
            const snapshot=await ui(peer);result.phases.push({stage:'preview-'+mode,target:[x,y],server:resourceSnapshot(state.current_character),price,ui:snapshot});save();
            assert.equal(numericHud(snapshot).current,state.current_character.movement_points_current);
            assert.equal(numericHud(snapshot).max,state.current_character.movement_points_max);
            assert.ok(snapshot.hint.visible,'надпись стоимости видна');const ratio=snapshot.hint.text.match(/ОП ([0-9.]+)\/([0-9.]+)/);assert.ok(ratio);
            assert.equal(Number(ratio[1]),Number(price.toFixed(1)));assert.equal(Number(ratio[2]),Number(state.current_character.movement_points_current.toFixed(1)));
            return{point,state,route,price};
        };
        await ensureMovementBudget('initial-turn');
        if(process.env.VTT_SAFE_SCENARIO==='surface'){
            const tile=fixture.tiles_data[48][42];
            const before=(await preview(42,48)).route;
            assert.equal(before.cost,1.5,'Полметра обычной земли и полметра снега');
            await peer.evaluate("window.surfaceUpdates=[];socket.on('location_editor_objects_changed', event=>surfaceUpdates.push(...(event.cells||[])));true");
            const waitSurface=async(value,count,terrain='snow')=>{
                await peer.until(`surfaceUpdates.filter(cell=>cell.x===42&&cell.z===48&&cell.state.terrain===${JSON.stringify(terrain)}&&cell.state.difficult_surface===${String(value)}).length===${count}`,
                    'Переопределение наземного участка дошло до игрока');
                const saved=await request(url,null,token,'GET');
                assert.equal(saved.tiles_data[48][42].terrain,terrain);
                assert.equal(saved.tiles_data[48][42].difficult_surface,value);
                tile.terrain=terrain;
                if(value===undefined)delete tile.difficult_surface;else tile.difficult_surface=value;
            };
            const paint=async(mode,brushMode='surface',terrain='snow')=>{
                await page.evaluate(`(async()=>{document.querySelector('[data-location-tool=terrain]').click();
                    const brush=document.getElementById('loc-terrain-mode');brush.value=${JSON.stringify(brushMode)};brush.dispatchEvent(new Event('change'));
                    const covering=document.getElementById('loc-edit-terrain');covering.value=${JSON.stringify(terrain)};covering.dispatchEvent(new Event('change'));
                    const input=document.getElementById('loc-surface-cost');input.value=${JSON.stringify(mode)};
                    input.dispatchEvent(new Event('change'));(await import('/static/js/ui_interactions.js')).collapseMapToolsPanel();return true})()`);
                const point=await pointAt(42,48,before.surfaces.at(-1).height,page);
                await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...point,buttons:0});
                await page.until('L.getHoveredTileCoords()?.x===42&&L.getHoveredTileCoords()?.z===48','Кисть над снежной клеткой');
                for(const type of ['mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,...point,
                    button:'left',buttons:type==='mousePressed'?1:0,clickCount:1});
            };
            await paint('normal');
            await waitSurface(false,1);
            await page.evaluate('L.setLocationEditMode(false);true');
            const ordinary=await armPreview('walk',42,48);
            assert.equal(ordinary.route.cost,1);
            await page.evaluate("L.setLocationEditMode(true);document.querySelector('[data-location-undo]').click();true");
            await waitSurface(undefined,1);
            assert.equal((await preview(42,48)).route.cost,1.5);
            await page.evaluate("document.querySelector('[data-location-redo]').click();true");
            await waitSurface(false,2);
            await paint('auto');
            await waitSurface(undefined,2);
            await paint('difficult','both','gravel');
            await waitSurface(true,1,'gravel');
            await page.evaluate("document.querySelector('[data-location-undo]').click();true");
            await waitSurface(undefined,3);
            await page.evaluate("document.querySelector('[data-location-redo]').click();true");
            await waitSurface(true,2,'gravel');
            await paint('auto','terrain');
            await waitSurface(true,1);
            await paint('auto');
            await waitSurface(undefined,4);
            assert.equal((await preview(42,48)).route.cost,1.5);
            await page.evaluate("window.selectLocationEditorTool('select');L.setLocationEditMode(false);true");
            result.phases.push({stage:'surface-override',before:before.cost,ordinary:ordinary.route.cost,
                automatic:(await preview(42,48)).route.cost,combined:true,undoRedo:true,network:true});save();
        }
        const beforeSelection=await server();
        await armPreview('walk',42,48);await armPreview('run',42,48);
        const unchanged=await server();
        result.phases.push({stage:'mode-selection',before:resourceSnapshot(beforeSelection.current_character),after:resourceSnapshot(unchanged.current_character)});save();
        for(const field of ['movement_points_current','action_points_current','free_actions_current'])assert.equal(unchanged.current_character[field],beforeSelection.current_character[field],'выбор режима не расходует '+field);
        assert.equal(unchanged.current_character.movement_mode_this_turn,null,'выбор режима не фиксирует серверный режим до шага');
        for(const [mode,x,y]of [['walk',42,48],['run',44,48]]){
            if(mode==='run'){
                await peer.evaluate(`L.showMovementTypeMenu(${actor.id})`);
                assert.equal(await peer.evaluate(`[...document.querySelectorAll('#combat-movement-type-menu .movement-type-options button')].find(button=>button.querySelector('strong').textContent.trim()===${JSON.stringify(rules.run.label)}).disabled`),true,'режимы не смешиваются в одном ходу');
                for(const type of ['keyDown','keyUp'])await peer.call('Input.dispatchKeyEvent',{type,key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
                const before=await server();await click(peer,'#combat-status-panel .combat-end-turn-btn');
                await peer.until(`L.getCombatState().round_number>${before.round_number}&&L.getCombatState().current_character.character_id===${actor.id}`,'штатный следующий ход');
                await ensureMovementBudget('next-turn');
            }
            const before=await armPreview(mode,x,y),counts=await Promise.all([page,peer].map(async client=>(await ui(client)).events.length));
            for(const type of ['mousePressed','mouseReleased'])await peer.call('Input.dispatchMouseEvent',{type,...before.point,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1});
            for(const client of [page,peer])await client.until(`L.characterPose(${actor.id}).x===${x}&&L.characterPose(${actor.id}).y===${y}&&L.getCombatState().current_character.movement_points_current===${before.state.current_character.movement_points_current-before.price}`,'движение/ОП через серверное событие');
            expectedPosition={x,y};
            const after=await server();await peer.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
            const snapshots=await Promise.all([page,peer].map(ui));result.phases.push({stage:'committed-'+mode,before:resourceSnapshot(before.state.current_character),after:resourceSnapshot(after.current_character),expectedCost:before.price,ui:snapshots});save();
            assert.equal(after.current_character.movement_points_current,before.state.current_character.movement_points_current-before.price);
            assert.equal(after.current_character.action_points_current,before.state.current_character.action_points_current-rules[mode].actionPoints);
            for(const [index,snapshot]of snapshots.entries()){
                assert.equal(numericHud(snapshot).current,after.current_character.movement_points_current);assert.equal(numericHud(snapshot).max,after.current_character.movement_points_max);
                assert.ok(snapshot.events.length>counts[index]&&snapshot.events.some(event=>event.op===after.current_character.movement_points_current),'зафиксировано реальное combat_state_updated');
            }
            await armPreview(mode,x+1,y); // Новая видимая надпись уже с уменьшенным серверным остатком.
            fs.writeFileSync(path.join(safeSession.artifacts,'movement-'+mode+'.png'),Buffer.from((await peer.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        }
        await request(url+'/combat/end',{},token);
        result.navigation.previewOrigin=await page.evaluate(`L.characterPose(${actor.id})`);
        result.navigation.probes=[];
        for(const target of [result.navigation.corridor.to,result.navigation.selectedStair.to]){
            const route=(await preview(...target)).route;
            result.navigation.probes.push({target,route,scope:'Только preview от фактической позиции, не прохождение лестницы'});save();
        }
        result.status='READY_FOR_REVIEW';save();
    }catch(error){result.status='FAILED';result.firstError=error.message;save();throw error;}
    finally{peer?.close();if(context)await protocol.call('Target.disposeBrowserContext',{browserContextId:context.browserContextId}).catch(()=>{});protocol.close();}
};
module.exports.serverMovementRules=serverMovementRules;
module.exports.expectedMovementCost=expectedMovementCost;
module.exports.expectedSurfaceRouteCost=expectedSurfaceRouteCost;
module.exports.navigationCandidates=navigationCandidates;

module.exports.movementUISnapshotExpression=movementUISnapshotExpression;

module.exports.isMovementReady=isMovementReady;
module.exports.conversionExpectation=conversionExpectation;
module.exports.selectModeReady=selectModeReady;
module.exports.prepareModePreview=prepareModePreview;
