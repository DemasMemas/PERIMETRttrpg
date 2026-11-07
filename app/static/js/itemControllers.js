import {Server} from './api.js';
import {showNotification} from './utils.js';

const node=(tag,text)=>{const el=document.createElement(tag);if(text!=null)el.textContent=text;return el;};
function dialog(id,title){
    document.getElementById(id)?.remove();
    const modal=node('div');modal.id=id;modal.className='modal';
    modal.style.cssText='display:flex;position:fixed;inset:0;align-items:center;justify-content:center;z-index:1700;background:rgba(0,0,0,.7)';
    const panel=node('div');panel.className='modal-content';panel.setAttribute('role','dialog');panel.setAttribute('aria-label',title);
    panel.style.cssText='width:min(680px,90vw);max-height:85vh;overflow:auto;padding:20px;display:grid;gap:12px';
    panel.append(node('h3',title));modal.append(panel);document.body.append(modal);
    const close=node('button','Закрыть');close.type='button';close.className='btn btn-secondary';close.onclick=()=>modal.remove();
    return {modal,panel,close};
}

export async function runController(characterId,payload,onSaved){
    await Server.waitForCharacterSave(characterId);
    const character=await Server.getCharacter(characterId),combat=window.locationCombatState;
    const request={operation_id:crypto.randomUUID(),character_revision:character.data._revision,
        ...(window.isLocationActive?{location_id:window.currentLocationId}:{}),...payload};
    let result;
    if(combat?.status==='active'){
        const actor=combat.current_character;
        if(Number(actor?.character_id)!==Number(characterId))throw new Error('Сейчас не ход этого персонажа');
        const action={location_character_id:actor.location_character_id,action_key:'controller',attribute_choice:request,pending_action_id:request.operation_id};
        result=await Server.performLocationCombatAction(window.currentLobbyId,window.currentLocationId,action);
        if(result.pending_action){
            const scene=await import('./locationScene.js');scene.registerDeferredCombatAction(result.pending_action_id,action);
            showNotification('Действие начато и продолжится в следующем ходу','system');return result;
        }
    }else result=await Server.operateController(characterId,request);
    const data=result.data||(await Server.getCharacter(characterId)).data;
    await onSaved?.(data);
    const detail=result.result||result.narrative_action;
    showNotification(detail?.error||detail?.name||'Готово','system');
    return result;
}

export async function openController({characterId,itemId,itemPath,onSaved}){
    const {modal,panel,close}=dialog('item-controller-modal','Взрыватели и таймеры');
    try{
        let snapshot=await Server.getControllers(characterId);
        if(!modal.isConnected)return;
        const current=[...snapshot.controllers,...snapshot.charges,...snapshot.devices].find(e=>itemId?e.id===itemId:JSON.stringify(e.path)===JSON.stringify(itemPath));
        if(!current)throw new Error('Предмет перемещён. Откройте его заново.');
        const content=node('div');content.style.cssText='display:grid;gap:12px';
        panel.append(content,close);
        const button=(text,makePayload)=>{
            const b=node('button',text);b.type='button';b.className='btn btn-primary';
            b.onclick=async()=>{
                const controls=[...panel.querySelectorAll('button,input,select')];controls.forEach(e=>e.disabled=true);
                try{await runController(characterId,makePayload(),onSaved);modal.remove();}
                catch(error){showNotification(error.message,'system');controls.forEach(e=>e.disabled=false);}
            };return b;
        };
        const choose=(label,entries)=>{
            const field=node('label',label),select=node('select');select.className='form-control';select.setAttribute('aria-label',label);
            entries.forEach((e,i)=>{const option=node('option',e.name);option.value=i;select.append(option);});field.append(select);content.append(field);
            return Object.assign(()=>entries[Number(select.value)],{select,field});
        };
        const seconds=()=>{
            const label=node('label','Отсрочка, секунд (до 86400)'),input=node('input');input.type='number';input.min=1;input.max=86400;input.value=60;
            input.className='form-control';input.setAttribute('aria-label','Отсрочка таймера');label.append(input);content.append(label);return ()=>Number(input.value);
        };
        content.append(node('strong',current.name));
        const selection={item_path:current.path,item_id:current.id};
        const powerMode=()=>choose('Действие таймера',[{name:'Включить прибор',mode:'on'},{name:'Выключить прибор',mode:'off'}]);
        if(current.kind){
            if(current.kind==='remote_detonator'){
                content.append(node('p',current.controller.paired_pda_id?'Привязка сохранена на физический КПК.':'Перед установкой привяжите взрыватель к физическому КПК. Привязка бесплатная.'));
                if(snapshot.pdas.length){const pda=choose('КПК для привязки',snapshot.pdas);
                    content.append(button('Привязать к КПК · бесплатно',()=>({...selection,operation:'bind',pda_path:pda().path,pda_id:pda().id})));}
                else content.append(node('p','В инвентаре нет КПК для привязки.'));
            }
            const charges=[...snapshot.charges,...(current.kind==='timer'?snapshot.devices:[])].filter(e=>!e.controller.kind);
            if(charges.length&&(current.kind==='timer'||current.controller.paired_pda_id)){
                const charge=choose('Предмет для установки',charges),time=current.kind==='timer'?seconds():()=>undefined;
                const mode=current.kind==='timer'?powerMode():null;
                if(mode){const update=()=>{mode.field.hidden=Boolean(charge()?.explosive);};charge.select.onchange=update;update();}
                content.append(button(current.kind==='timer'?'Установить таймер · заряд 5 ОД / прибор 3 ОД + доставание':'Установить на заряд · 5 ОД + доставание',()=>({operation:'install',controller_path:current.path,controller_id:current.id,
                    item_path:charge().path,item_id:charge().id,seconds:time(),mode:mode?.().mode})));
            }else content.append(node('p',charges.length?'Сначала выполните привязку.':'Нет подходящей взрывчатки без установленного контроллера.'));
        }else{
            const state=current.controller;
            content.append(node('p',state.error||(!state.armed?state.disconnected||state.kind!=='timer'?'Провод отсоединён.':'Таймер остановлен.':state.kind==='timer'?`Таймер активен: ${state.remaining_seconds} с.`:'Дистанционный взрыватель активен.')));
            if(state.armed)content.append(button('Оторвать провод · 3 ОД',()=>({...selection,operation:'disarm'})));
            if(state.kind==='timer'){
                const time=seconds(),mode=current.explosive?null:powerMode();
                if(mode)mode.select.value=state.mode==='off'?'1':'0';
                content.append(button('Настроить и запустить таймер · 3 ОД',()=>({...selection,operation:'timer',seconds:time(),mode:mode?.().mode})));
                if(!state.disconnected&&(state.armed||!current.explosive))content.append(button('Нажать кнопку таймера сейчас · 3 ОД',()=>({...selection,operation:'activate',mode:mode?.().mode})));
            }
        }
    }catch(error){panel.append(node('p',error.message),close);}
}

export function renderRemoteCharges(parent,{characterId,selection,onSaved}){
    const title=node('h3','Дистанционные заряды'),list=node('div'),refresh=node('button','Обновить');refresh.type='button';
    parent.append(title,node('p','Дальность — 250 м. Для команды подрыва возьмите этот включённый КПК в руку. Команда: 1 ОД.'),refresh,list);
    let busy=false;
    async function load(){
        if(busy)return;busy=true;refresh.disabled=true;
        try{
            const result=await Server.getControllers(characterId,selection());
            if(!parent.isConnected)return;
            list.replaceChildren();
            if(!result.charges.length)list.append(node('p','К этому КПК пока не привязаны установленные заряды.'));
            for(const charge of result.charges){
                const row=node('div');row.style.cssText='display:flex;align-items:center;justify-content:space-between;gap:12px;margin:10px 0';
                const button=node('button','Подорвать · 1 ОД');button.type='button';button.className='btn btn-danger';button.disabled=!charge.armed||!charge.reachable;
                row.append(node('span',`${charge.label} · ${!charge.armed?'обезврежен':charge.reachable?'связь есть':'нет связи'}`),button);list.append(row);
                button.onclick=async()=>{
                    if(busy)return;busy=true;[...list.querySelectorAll('button')].forEach(b=>b.disabled=true);
                    try{await runController(characterId,{operation:'detonate',attachment_id:charge.id},onSaved);}
                    catch(error){showNotification(error.message,'system');}
                    finally{busy=false;await load();}
                };
            }
        }catch(error){list.replaceChildren(node('p',error.message));}
        finally{busy=false;refresh.disabled=false;}
    }
    refresh.onclick=load;void load();
}

export async function openDetonations(lobbyId){
    const {modal,panel,close}=dialog('world-detonations-modal','Последствия взрывов');
    try{
        const [snapshot,people]=await Promise.all([Server.getDetonations(lobbyId),Server.getLobbyCharacters(lobbyId)]);
        if(!modal.isConnected)return;
        if(!snapshot.events.length)panel.append(node('p','Нет взрывов с неразобранными последствиями.'));
        for(let event of snapshot.events){
            const card=node('section');card.style.cssText='display:grid;gap:10px;border:1px solid #665c39;padding:12px';
            const status=node('p');
            const update=()=>{status.textContent='Уже затронуты: '+(event.results.map(r=>r.name||'#'+r.character_id).join(', ')||'никто');};update();
            card.append(node('h4',event.name),status);
            const targets=node('select');targets.className='form-control';targets.setAttribute('aria-label','Добавить пострадавшего');
            for(const person of people.characters||people){if(event.affected_character_ids.includes(person.id))continue;const o=node('option',person.name);o.value=person.id;targets.append(o);}
            const distance=node('input');distance.type='number';distance.min=0;distance.max=event.radius;distance.step=.1;distance.value=0;distance.setAttribute('aria-label','Расстояние от взрыва, м');
            const cover=node('input');cover.type='number';cover.min=0;cover.max=100;cover.value=0;cover.setAttribute('aria-label','Защита укрытия, %');
            const add=node('button','Применить к выбранному персонажу'),done=node('button','Завершить последствия');add.type=done.type='button';add.className=done.className='btn btn-secondary';
            card.append(targets,node('label','Расстояние от взрыва, м'),distance,node('label','Защита укрытия, %'),cover,add,done);panel.append(card);
            async function apply(operation){
                add.disabled=done.disabled=true;
                try{
                    const result=await Server.resolveDetonation(lobbyId,event.id,{operation,revision:event.revision,
                        ...(operation==='add_target'?{character_id:Number(targets.value),distance:Number(distance.value),cover_percent:Number(cover.value)}:{})});
                    event=result.event;update();
                    if(operation==='add_target')targets.selectedOptions[0]?.remove();else{card.remove();void refreshDetonations(lobbyId);}
                }catch(error){showNotification(error.message,'system');}
                finally{add.disabled=!targets.options.length;done.disabled=false;}
            }
            add.disabled=!targets.options.length;add.onclick=()=>apply('add_target');done.onclick=()=>apply('close');
        }
        panel.append(close);
    }catch(error){panel.append(node('p',error.message),close);}
}

export async function refreshDetonations(lobbyId){
    const button=document.getElementById('detonations-btn');if(!button)return;
    try{
        const snapshot=await Server.getDetonations(lobbyId);
        button.style.display=snapshot.events.length?'inline-block':'none';button.textContent=`Взрывы: ${snapshot.events.length}`;
        button.onclick=event=>{event.stopPropagation();void openDetonations(lobbyId);};
    }catch(error){showNotification(error.message,'system');}
}
