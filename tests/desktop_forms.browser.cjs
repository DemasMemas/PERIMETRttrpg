// №228: формы ГМа, выбор текста и горячие клавиши после уровня воды.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,actor,lobby,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'catalog');assert.ok(safeSession.verified);
    await page.evaluate("window.closeCharacterSheet();true");
    const npc=await request(`/lobbies/${lobby.id}/characters`,{name:'Проверка интерфейса НПС с длинным именем',data:{is_npc:true}},token);
    for(let i=0;i<49;i++)await request(`/lobbies/${lobby.id}/characters`,{name:`Персонаж длинного списка ${i+1}`,data:{is_npc:true}},token);
    await request(`/lobbies/${lobby.id}/characters`,{name:'Мутант не входит в группировки',data:{is_mutant:true}},token);
    const factions=await request(`/lobbies/${lobby.id}/world-factions`,null,token,'GET');
    await request(`/lobbies/${lobby.id}/world-factions`,{...factions,source:'custom',groups:Array.from({length:25},(_,i)=>({id:`test-group-${i}`,name:`Группировка длинного списка ${i+1}`,factions:[]})),relations:[]},token,'PUT');
    const editable=await request('/lobbies/characters/'+actor.id,null,token,'GET');
    editable.data.inventory.pockets.push({id:'text-selection-item',name:'Текст для проверки выделения мышью',category:'misc',quantity:1,weight:0,volume:0});
    await request('/lobbies/characters/'+actor.id,{data:editable.data},token,'PUT');
    const records=[];
    const geometry=async(selector,required=[])=>{
        const result=await page.evaluate(`(()=>{const root=document.querySelector(${JSON.stringify(selector)}),r=root.getBoundingClientRect();return{viewport:[innerWidth,innerHeight],x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height,scrollWidth:root.scrollWidth,clientWidth:root.clientWidth,controls:${JSON.stringify(required)}.map(selector=>{const el=root.querySelector(selector);if(!el)return{selector,missing:true};const b=el.getBoundingClientRect();return{selector,x:b.x,y:b.y,right:b.right,bottom:b.bottom}})}})()`);
        records.push({selector,...result});fs.writeFileSync(path.join(safeSession.artifacts,'desktop-forms.json'),JSON.stringify(records,null,2));
        assert.ok(result.x>=0&&result.y>=0&&result.right<=result.viewport[0]+1&&result.bottom<=result.viewport[1]+1,selector+' целиком на экране');
        assert.ok(result.scrollWidth<=result.clientWidth+2,selector+' без горизонтальной прокрутки');
        assert.ok(result.controls.every(b=>!b.missing&&b.x>=result.x&&b.right<=result.right+1&&b.y>=result.y&&b.bottom<=result.bottom+1),selector+' основные кнопки видимы');
    };
    for(const [width,height]of [[1920,1080],[1600,900],[1366,768],[1280,720]]){
        await page.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
        await page.evaluate(`(async()=>{await(await import('/static/js/crafting.js')).showCrafting({characterId:${actor.id},lobbyId:${lobby.id}});return true})()`);
        await page.until("document.querySelector('.crafting-recipe-card')",'Рецепты загружены');
        await geometry('#crafting-modal .modal-content',['.block-header button']);
        await page.evaluate("document.querySelector('#crafting-modal .crafting-body').scrollTop=1e6;true");
        await geometry('#crafting-modal .modal-content',['.block-header button']);
        await page.evaluate("document.querySelector('#crafting-modal .block-header button').click();true");
        await page.evaluate(`(async()=>{await(await import('/static/js/merchantEditor.js')).openNpcMerchant(${lobby.id},${npc.id});return true})()`);
        await page.until("document.querySelector('.merchant-editor')",'Настройки торговли');
        await page.evaluate("document.querySelector('.merchant-workbenches').open=true;true");
        await geometry('.merchant-editor',['.trade-close','.merchant-save']);
        if(width===1280){const shot=await page.call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(safeSession.artifacts,'merchant-1280x720.png'),Buffer.from(shot.data,'base64'));}
        await page.evaluate("document.querySelector('.merchant-editor .trade-close').click();true");
        await page.evaluate(`(async()=>{await(await import('/static/js/npcLoot.js')).openNpcLoot(${lobby.id},${npc.id});return true})()`);
        await page.until("document.querySelector('.npc-loot-modal .modal-content')",'Настройки добычи');
        await geometry('.npc-loot-modal .modal-content',['.trade-close']);
        await page.evaluate("for(let i=0;i<9;i++)document.querySelector('.loot-add').click();true");
        await geometry('.npc-loot-modal .modal-content',['.trade-close','.loot-save','.loot-generate']);
        await page.evaluate("document.querySelector('.npc-loot-body').scrollTop=1e6;true");
        await geometry('.npc-loot-modal .modal-content',['.trade-close','.loot-save','.loot-generate']);
        await page.evaluate("document.querySelector('.npc-loot-modal .trade-close').click();true");
        await page.evaluate(`(async()=>{await(await import('/static/js/worldFactions.js')).openWorldFactionSettings(${lobby.id});return true})()`);
        await geometry('.faction-dialog',['.faction-close']);
        for(const tab of ['groups','relations','characters']){
            await page.evaluate(`document.querySelector('.faction-tab[data-tab="${tab}"]').click();document.querySelector('.faction-panel[data-panel="${tab}"]').scrollTop=1e6;true`);
            await geometry('.faction-dialog',['.faction-close']);
            assert.ok(await page.evaluate(`(()=>{const panel=document.querySelector('.faction-panel[data-panel="${tab}"]'),tabs=document.querySelector('.faction-tabs');return panel.getBoundingClientRect().top>=tabs.getBoundingClientRect().bottom-1})()`),'Список не заходит под вкладки');
        }
        assert.equal(await page.evaluate("document.querySelectorAll('.faction-assignment-row').length"),51,'Игрок и 50 НПС, без мутанта');
        if(width===1280){const shot=await page.call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(safeSession.artifacts,'factions-1280x720.png'),Buffer.from(shot.data,'base64'));}
        await page.evaluate("document.querySelector('.faction-close').click();true");
    }
    // Настоящее выделение мышью в редактируемом имени, без начала переноса предмета.
    await page.evaluate(`window.openCharacterSheet(${actor.id},'inventory').then(()=>true)`);
    await page.until("[...document.querySelectorAll('[data-widget-identity]')].some(e=>JSON.parse(e.dataset.widgetIdentity).id==='text-selection-item')",'Свой предмет с редактируемым названием');
    const point=await page.evaluate("(()=>{const row=[...document.querySelectorAll('[data-widget-identity]')].find(e=>JSON.parse(e.dataset.widgetIdentity).id==='text-selection-item'),field=row.querySelector('[data-widget-key=name]');field.scrollIntoView({block:'center'});field.focus();field.setSelectionRange(0,0);const r=field.getBoundingClientRect();return{x:r.x+5,y:r.y+r.height/2,end:r.right-5}})()");
    await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',x:point.x,y:point.y});
    await page.call('Input.dispatchMouseEvent',{type:'mousePressed',x:point.x,y:point.y,button:'left',buttons:1,clickCount:1});
    for(let step=1;step<=8;step++)await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',x:point.x+(point.end-point.x)*step/8,y:point.y,button:'left',buttons:1});
    const selected=await page.evaluate("(()=>{const field=document.activeElement;return{tag:field.tagName,start:field.selectionStart,end:field.selectionEnd,dragging:Boolean(document.querySelector('[data-widget-identity].dragging'))}})()");
    await page.call('Input.dispatchMouseEvent',{type:'mouseReleased',x:point.end,y:point.y,button:'left',buttons:0});
    const released=await page.evaluate("(()=>{const field=document.activeElement;return{tag:field.tagName,start:field.selectionStart,end:field.selectionEnd,dragging:Boolean(document.querySelector('[data-widget-identity].dragging'))}})()");
    fs.writeFileSync(path.join(safeSession.artifacts,'text-selection.json'),JSON.stringify({selected,released},null,2));
    assert.ok(released.tag==='INPUT'&&released.end>released.start&&!released.dragging,'Текст выделен, предмет не перетаскивается: '+JSON.stringify({selected,released}));
    await page.evaluate("window.closeCharacterSheet();if(!window.locationEditMode)window.toggleEditMode();window.selectLocationEditorTool('height');true");
    await page.until("document.getElementById('loc-water-level-mode')",'Уровень воды в редакторе');
    await page.evaluate("(async()=>{window.auditMap=(await import('/static/js/mapSceneContext.js')).getMapSceneContext();return true})()");
    for(const code of ['KeyW','KeyA','KeyS','KeyD']){
        await page.evaluate("document.getElementById('loc-water-level-mode').focus();window.auditCameraBefore=auditMap.camera.position.toArray();true");
        await page.call('Input.dispatchKeyEvent',{type:'keyDown',code,key:code.slice(-1).toLowerCase()});
        try{await page.evaluate("new Promise(resolve=>{let frames=0;const tick=()=>++frames===8?resolve(true):requestAnimationFrame(tick);requestAnimationFrame(tick)})");}
        finally{await page.call('Input.dispatchKeyEvent',{type:'keyUp',code,key:code.slice(-1).toLowerCase()});}
        assert.ok(await page.evaluate("auditMap.camera.position.toArray().some((value,i)=>Math.abs(value-auditCameraBefore[i])>.01)"),code+' работает при фокусе на уровне воды');
    }
    await page.evaluate("document.getElementById('loc-water-level-mode').focus();true");
    await page.call('Input.dispatchKeyEvent',{type:'keyDown',code:'KeyQ',key:'q'});
    try{await page.until("document.querySelector('.map-editor-wheel')",'Q открывает меню после списка уровня воды');}
    finally{await page.call('Input.dispatchKeyEvent',{type:'keyUp',code:'KeyQ',key:'q'});}
    await page.evaluate("document.getElementById('loc-water-level-mode').focus();true");
    await page.call('Input.dispatchKeyEvent',{type:'keyDown',code:'KeyE',key:'e'});
    await page.call('Input.dispatchKeyEvent',{type:'keyUp',code:'KeyE',key:'e'});
    assert.equal(await page.evaluate('window.locationEditMode'),false,'E переключает редактор после списка уровня воды');
    console.log('Настольные формы: крафт, торговля/станки НПС, пулы добычи, группировки на четырёх размерах; мышь выделяет текст в инвентаре; WASD/Q/E работают после уровня воды.');
};
