import {Server} from './api.js';
import {showNotification} from './utils.js';

export async function showTraitAction(characterId,data,kind) {
    if(kind==='scout_location')return showScouting(characterId,data);
    const scene=await import('./locationScene.js');
    const locationId=scene.getCurrentLocationId();
    if(!window.isLocationActive||!locationId)throw new Error('Сначала разместите персонажа в подлокации');
    const state=await Server.getLocationCombatState(window.currentLobbyId,locationId);
    scene.setCombatState(state);
    const actor=state.characters.find(actor=>Number(actor.character_id)===Number(characterId));
    if(!actor)throw new Error('Персонаж не размещён в подлокации');
    document.getElementById('trait-action-modal')?.remove();
    const modal=document.createElement('div');modal.id='trait-action-modal';modal.className='modal';
    modal.innerHTML='<form class="modal-content" style="max-width:540px;display:grid;gap:12px"><h3></h3><div class="trait-fields" style="display:grid;gap:10px"></div><div class="trait-result" style="white-space:pre-line" role="status"></div><button class="btn btn-primary" type="submit">Выполнить</button><button class="btn btn-secondary trait-close" type="button">Закрыть</button></form>';
    const form=modal.querySelector('form'),fields=modal.querySelector('.trait-fields');
    modal.querySelector('h3').textContent=kind==='brigadier_order'?'Указание союзнику':'Уклонение';
    if(kind==='brigadier_order'){
        fields.innerHTML='<label>Союзник <select name="target" class="form-control" required></select></label><label>Вид указания <select name="detailed" class="form-control"><option value="no">Быстрое · 2 ОД · +3 следующему броску</option><option value="yes">Подробное · 4 ОД · +3 и Преимущество</option></select></label><label>Указание <textarea name="text" class="form-control" required maxlength="500"></textarea></label>';
        for(const other of state.characters)if((actor.ally_character_ids||[]).includes(other.character_id))form.elements.target.add(new Option(other.name,other.character_id));
        if(!form.elements.target.options.length)throw new Error('Нет союзников из команды или группы на локации');
    } else fields.textContent='Штраф атакующему: 5 + Бонус Ловкости. Действует до начала следующего своего хода.';
    modal.querySelector('.trait-close').onclick=()=>modal.remove();
    modal.onclick=event=>{if(event.target===modal)modal.remove();};
    form.onsubmit=async event=>{
        event.preventDefault();const button=form.querySelector('[type="submit"]');button.disabled=true;
        try{
            const payload={location_character_id:actor.location_character_id,action_key:kind};
            if(kind==='brigadier_order')Object.assign(payload,{target_character_id:Number(form.elements.target.value),attribute_choice:{detailed:form.elements.detailed.value==='yes',text:form.elements.text.value},pending_action_id:crypto.randomUUID()});
            const result=await Server.performLocationCombatAction(window.currentLobbyId,locationId,payload);
            scene.setCombatState(result.state);
            if(result.pending_action)scene.registerTraitDeferredAction(result.pending_action_id,payload);
            modal.remove();showNotification(result.pending_action?'Действие начато, ожидает полной оплаты':kind==='brigadier_order'?'Союзнику отправлено указание':'Уклонение действует до следующего хода','system');
        }catch(error){showNotification(error.message||'Действие не выполнено','system');}
        finally{button.disabled=false;}
    };
    document.body.append(modal);modal.style.display='flex';
}

async function showScouting(characterId,data){
    const scene=await import('./locationScene.js');
    let configured=await Server.getLocationScoutPoint(characterId);
    const locationId=window.isLocationActive?scene.getCurrentLocationId():null;
    const state=locationId?await Server.getLocationCombatState(window.currentLobbyId,locationId):null;
    const actor=state?.characters.find(person=>Number(person.character_id)===Number(characterId));
    document.getElementById('trait-action-modal')?.remove();
    const modal=document.createElement('div');modal.id='trait-action-modal';modal.className='modal';
    modal.innerHTML='<form class="modal-content" style="max-width:540px;display:grid;gap:12px"><h3>Осмотр территории</h3><div class="scout-point" role="status"></div><button type="button" class="btn btn-secondary scout-configure">Задать точку и направление на карте</button><label>Чем осматриваем <select name="instrument" class="form-control"><option value="">Без оптики</option></select></label><div class="trait-result" style="white-space:pre-line" role="status"></div><button class="btn btn-primary" type="submit">Осмотреть</button><button class="btn btn-secondary trait-close" type="button">Закрыть</button></form>';
    const form=modal.querySelector('form'),select=form.elements.instrument,paths=[];
    const refresh=()=>{
        modal.querySelector('.scout-point').textContent=configured.point?`Точка ГМа · ${configured.location_name} · (${configured.point.x.toFixed(1)}, ${configured.point.y.toFixed(1)}), этаж ${configured.point.floor_level}`:actor?'Осмотр из текущего положения персонажа':'ГМ ещё не задал точку наблюдения. Попросите его открыть подлокацию и задать точку в этом листе.';
        form.querySelector('[type="submit"]').disabled=!configured.point&&!actor;
    };
    const configure=modal.querySelector('.scout-configure');configure.hidden=!window.isGM||!locationId;
    configure.onclick=async()=>{
        modal.style.display='none';
        const sheet=document.getElementById('character-sheet-modal'),display=sheet?.style.display;
        if(sheet)sheet.style.display='none';
        try{configured=await Server.setLocationScoutPoint(characterId,await scene.chooseScoutObservationPoint());refresh();}
        catch(error){showNotification(error.message,'system');}
        finally{if(sheet)sheet.style.display=display;modal.style.display='flex';}
    };
    for(const [index,weapon] of (data.weapons||[]).entries()){
        const range=Number(weapon.range??weapon.attributes?.range)||0;
        select.add(new Option(`${weapon.name||'Оружие'} · дальность ${range}`,`weapon:${index}`));
    }
    if(actor?.drawn_weapon_index!=null)select.value=`weapon:${actor.drawn_weapon_index}`;
    const visit=(value,path)=>{
        if(Array.isArray(value)){value.forEach((item,index)=>visit(item,[...path,index]));return;}
        if(!value||typeof value!=='object')return;
        if(/бинок/i.test(value.name||'')||[15,25].includes(Number(value.scouting_bonus||value.attributes?.scouting_bonus))){paths.push(path);select.add(new Option(value.name||'Бинокль',`item:${paths.length-1}`));}
        for(const key of ['contents','pouches','storedItem'])visit(value[key],[...path,key]);
    };
    for(const root of ['inventory','equipment'])for(const [key,value] of Object.entries(data[root]||{}))visit(value,[root,key]);
    if(!configured.point&&actor){
        // Из текущего положения используются только оружие в руках или бинокль.
        for(const option of [...select.options])if(option.value.startsWith('weapon:')&&option.value!==`weapon:${actor.drawn_weapon_index}`)option.remove();
        select.options[0].textContent='Оружие в руках: дальность / 5';
    }
    modal.querySelector('.trait-close').onclick=()=>modal.remove();
    modal.onclick=event=>{if(event.target===modal)modal.remove();};
    form.onsubmit=async event=>{
        event.preventDefault();const button=form.querySelector('[type="submit"]');button.disabled=true;
        try{
            const [type,index]=select.value.split(':'),instrument=type==='item'?{item_path:paths[Number(index)]}:type==='weapon'?{weapon_index:Number(index)}:{};
            let details;
            if(configured.point){
                details=await Server.scoutFromPoint(characterId,instrument);
                modal.remove();window.closeCharacterSheet?.();
                await window.enterLocation(details.location_id,{scoutView:details});
                scene.setLocationViewLevel(details.point.floor_level);
                showNotification(`Осмотр: ${details.check.total} против СЛ ${details.check.difficulty} — ${details.check.success?'успех':'провал'}`,'system');
            }else{
                const result=await Server.performLocationCombatAction(window.currentLobbyId,locationId,{location_character_id:actor.location_character_id,action_key:'scout_location',attribute_choice:type==='item'?instrument:{}});
                scene.setCombatState(result.state);details=result.narrative_action;scene.setScoutedCharacters(details.targets||[],characterId);
                const check=details.check;
                modal.querySelector('.trait-result').textContent=`Осмотр: ${check.total} против СЛ ${check.difficulty} — ${check.success?'успех':'провал'}\n${details.targets.map(target=>`${target.name} · (${target.x.toFixed(1)}, ${target.y.toFixed(1)}), этаж ${target.floor_level||0}`).join('\n')||'Новых открытых целей не обнаружено'}`;
            }
        }catch(error){showNotification(error.message||'Осмотр не выполнен','system');}
        finally{button.disabled=false;}
    };
    refresh();document.body.append(modal);modal.style.display='flex';
}
