import {Server} from './api.js';
import {showNotification} from './utils.js';

const names={mask:'Замаскировать',mask_stash:'Устроить тайник',bury_ground_stash:'Прикопать тайник',
    dig_up_stash:'Раскопать ящик',defuse:'Обезвредить',recover:'Подобрать',detect:'Осмотреть клетку',
    escape_net:'Освободиться из сети',cut_net:'Разрезать сеть'};

export function isGroundStashTool(item) {
    return ['tool', 'misc', 'weapon', 'melee_weapon'].includes(item?.category)
        && ['лопата', 'лопатка саперная'].includes(String(item.name || '').trim().toLowerCase().replaceAll('ё', 'е'))
        && (item.quantity ?? 1) > 0;
}

export async function submitTrapAction({ lobbyId, locationId, inCombat, locationCharacterId, payload }) {
    const result = inCombat ? await Server.performLocationCombatAction(lobbyId, locationId, {
        location_character_id: locationCharacterId, action_key: 'trap',
        attribute_choice: payload, pending_action_id: payload.operation_id,
    }) : await Server.operateTrap(lobbyId, locationId, payload);
    const detail = result.result || result.narrative_action;
    const found = ['detect', 'search_stash'].includes(payload.operation) && detail
        ? (detail.found ? ` · найдено ${detail.found}` : ' · ничего не найдено') : '';
    showNotification(result.pending_action ? 'Действие начато' : detail?.check
        ? `${detail.name}: ${detail.check.total}${detail.difficulty != null ? ' · СЛ ' + detail.difficulty : ''}${detail.check.success === false ? ' · неудача' : ''}${found}`
        : detail?.name || 'Готово', 'system');
    return result;
}

export async function showTrapAction({characterId,lobbyId,locationId,trap,operation,inCombat,locationCharacterId,cell,requireShovel=false}){
    document.getElementById('trap-action-modal')?.remove();
    const modal=document.createElement('div');modal.id='trap-action-modal';modal.className='modal';
    modal.style.cssText='display:flex;position:fixed;inset:0;align-items:center;justify-content:center;z-index:1600;background:rgba(0,0,0,.65)';
    const panel=document.createElement('div');panel.className='modal-content';panel.style.cssText='width:min(600px,90vw);max-height:80vh;overflow:auto;padding:20px';
    modal.append(panel);document.body.append(modal);
    const node=(tag,text)=>{const el=document.createElement(tag);el.textContent=text;return el;};
    const close=node('button','Закрыть');close.type='button';close.className='btn btn-secondary';close.onclick=()=>modal.remove();
    const label=operation==='mask_stash'&&requireShovel?'Закопать ящик':names[operation];
    panel.append(node('h3',`${label}${trap?' · '+trap.name:''}`));
    const masking=['mask','mask_stash','bury_ground_stash'].includes(operation);
    try{
        const snapshot=await Server.getCrafting(characterId),character=await Server.getCharacter(characterId);
        if(!modal.isConnected)return;
        panel.firstElementChild.after(node('p', character.name));
        const inventory=[...snapshot.inventory,...(character.data.weapons||[]).map((item,index)=>({path:['weapons',index],item}))];
        const kind=trap?.properties?.trap_kind;
        const tools=inventory.filter(e=>{
            if(!['tool','misc','weapon','melee_weapon'].includes(e.item.category))return false;
            const name=(e.item.name||'').toLocaleLowerCase('ru').replaceAll('ё','е');
            if(requireShovel)return /лопат/.test(name);
            if(masking)return /нож|лопат/.test(name);
            if(operation==='cut_net')return (e.item.attributes?.allowed_attacks||[]).some(a=>/реж|руб|cut|slash/i.test(a));
            return /мультитул/.test(name)||kind==='beartrap'&&/грузик/.test(name)
                ||['net','alarm','mine','tripwire'].includes(kind)&&/нож/.test(name)&&!/кустар/.test(name)
                ||kind==='mine'&&/отвертка|лопат/.test(name)||kind==='tripwire'&&/кусач/.test(name);
        });
        const choice=document.createElement('select');choice.className='form-control';choice.setAttribute('aria-label','Инструмент');
        const none=node('option',requireShovel?'Выберите лопату':operation==='cut_net'?'Выберите острое оружие':'Без инструмента');none.value='';choice.append(none);
        for(const [index,entry]of tools.entries()){const option=node('option',entry.item.name);option.value=index;choice.append(option);}
        if(masking||requireShovel||['defuse','cut_net'].includes(operation))panel.append(node('label','Инструмент'),choice);
        const toolMultiplier=snapshot.trap_rules?.tool_multiplier||1;
        if(masking)panel.append(node('p',`5 ОД. К20 + Скрытность + инструмент. Нож: +${Math.ceil(toolMultiplier)}; лопата или саперная лопатка: +${Math.ceil(3*toolMultiplier)}. Результат — СЛ обнаружения по Внимательности.`));
        if(masking&&snapshot.trap_rules?.masking_bonus)panel.append(node('p',`Мастер маскировки: +${snapshot.trap_rules.masking_bonus} к броску.`));
        if((masking||operation==='defuse')&&snapshot.trap_rules?.tool_multiplier>1)panel.append(node('p','Полевой инженер: бонус инструмента ×1,5 с округлением вверх.'));
        if(operation==='defuse')panel.append(node('p','2 ОД. При неудаче ловушка сработает. Инструмент не обязателен.'));
        if(operation==='escape_net')panel.append(node('p','2 ОД. Проверка Силы: СЛ 12.'));
        if(operation==='cut_net')panel.append(node('p','3 ОД. Нужна режущая или рубящая атака выбранного оружия.'));
        const submit=node('button',label);submit.type='button';submit.className='btn btn-primary';
        if(requireShovel){submit.disabled=true;choice.onchange=()=>{submit.disabled=choice.value==='';};}
        let pending=null;
        submit.onclick=async()=>{
            submit.disabled=true;choice.disabled=true;
            pending ||= {operation,operation_id:crypto.randomUUID(),character_id:characterId,character_revision:character.data._revision,
                trap_id:trap?.properties?.trap_id,trap_revision:trap?.revision,
                stash_id:trap?.id,stash_revision:trap?.revision,
                target_character_id:trap?.properties?.trap_victim_id,x:cell?.x,y:cell?.y,
                tool_path:choice.value!==''?tools[Number(choice.value)]?.path:undefined};
            try{
                await submitTrapAction({ lobbyId, locationId, inCombat, locationCharacterId, payload: pending });
                modal.remove();
            }catch(error){
                showNotification(error.message,'system');
                if(error.status)pending=null;
                submit.disabled=false;choice.disabled=false;
            }
        };
        const actions=node('div','');actions.style.cssText='display:flex;gap:8px;margin-top:12px';actions.append(submit,close);panel.append(actions);
    }catch(error){panel.append(node('p',error.message),close);}
}
