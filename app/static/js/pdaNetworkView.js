import {Server} from './api.js';
import {showNotification} from './utils.js';
import {testPdaSignal} from './pdaProximity.js';

export function createPdaNetworkView({characterId,findSource,getData,operateHardware,canEdit}){
    const element=document.createElement('section');element.className='pda-network';
    element.innerHTML=`<p data-radio-status></p><button type="button" data-radio-jammer hidden></button>
        <div data-radio-proximity hidden><label><input type="checkbox" data-radio-proximity-sound> Звуковой сигнал о незнакомом КПК</label><button type="button" data-radio-test-sound>Проверить звук</button></div><p role="alert" data-radio-error></p>
        <div data-radio-chat><div class="pda-radio-messages" data-radio-messages aria-live="polite"></div>
        <form data-radio-send><label>Получатель<select aria-label="Получатель сообщения КПК"></select></label>
        <label>Сообщение<textarea aria-label="Сообщение КПК" rows="3" maxlength="2000"></textarea></label><button type="submit">Передать</button></form></div>
        <div data-radio-contacts hidden><h4>Сигналы поблизости</h4><div data-radio-signals></div><h4>Сохранённые контакты</h4><div data-radio-saved></div>
        <form data-radio-add><label>Логин контакта<input aria-label="Логин контакта КПК" autocomplete="off" maxlength="32"></label><button type="submit">Добавить в друзья</button></form></div>`;
    const status=element.querySelector('[data-radio-status]'),error=element.querySelector('[data-radio-error]'),jammer=element.querySelector('[data-radio-jammer]');
    const send=element.querySelector('[data-radio-send]'),receiver=send.querySelector('select'),text=send.querySelector('textarea');
    const add=element.querySelector('[data-radio-add]'),login=add.querySelector('input');
    const proximity=element.querySelector('[data-radio-proximity]'),sound=element.querySelector('[data-radio-proximity-sound]');
    let active=false,closed=false,busy=false,loading=false,timer=null,snapshot=null,pendingMessage=null,actionError='';
    const node=(tag,value)=>{const el=document.createElement(tag);el.textContent=value;return el;};
    const selection=()=>{
        const source=findSource();if(!source)throw Error('КПК перемещён. Откройте его заново.');
        return {item_path:source.path,item_id:source.item.id||null,
            location_id:window.isLocationActive?Number(window.currentLocationId)||null:null};
    };
    const controls=()=>{
        const unavailable=busy||!canEdit||!snapshot?.account;
        for(const input of send.querySelectorAll('input,textarea,select,button'))input.disabled=unavailable||Boolean(snapshot?.source.blocked);
        for(const input of add.querySelectorAll('input,button'))input.disabled=unavailable;
        for(const input of element.querySelectorAll('[data-radio-signals] button,[data-radio-saved] button,[data-radio-saved] input'))input.disabled=unavailable;
        const state=window.locationCombatState,offTurn=state?.status==='active'&&Number(state.current_character?.character_id)!==Number(characterId);
        jammer.disabled=busy||!canEdit||offTurn;jammer.title=offTurn?'Только в свой ход':'';
        sound.disabled=busy||!canEdit;
    };
    function paint(){
        if(!snapshot)return;
        const source=snapshot.source;
        status.textContent=`Дальность передачи: ${source.transmit_range} м${source.blocked?' · '+source.blocked:''}${snapshot.account?'':' · войдите в аккаунт для переписки и контактов'}`;
        jammer.hidden=!source.has_jammer;jammer.textContent=source.jammer_on?'Выключить глушилку':'Включить глушилку · радиус 25 м';
        proximity.hidden=!source.has_proximity;sound.checked=source.proximity_sound!==false;
        const feed=element.querySelector('[data-radio-messages]'),bottom=feed.scrollHeight-feed.scrollTop-feed.clientHeight<40;
        feed.replaceChildren();
        for(const message of snapshot.messages){const author=message.outgoing?(message.recipient?'Вы → '+message.recipient:'Вы · общий канал'):
            message.sender+(message.recipient?' · личное':' · общий канал');
            const row=node('div',`${author}: ${message.text}`);row.className='pda-radio-message';feed.append(row);}
        if(!snapshot.messages.length)feed.append(node('p','Сообщений пока нет.'));
        if(bottom)feed.scrollTop=feed.scrollHeight;
        if(document.activeElement!==receiver){
            const previous=receiver.value,previousLabel=receiver.selectedOptions[0]?.textContent;receiver.replaceChildren();const all=node('option','Общий радиоканал');all.value='';receiver.append(all);
            const accounts=new Map([...snapshot.contacts,...snapshot.signals].filter(v=>v.account_id&&v.account_id!==snapshot.account?.id).map(v=>[v.account_id,v]));
            for(const [id,account]of accounts){const option=node('option',account.login);option.value=id;receiver.append(option);}
            if(previous&&![...receiver.options].some(option=>option.value===previous)){
                const retained=node('option',previousLabel||'Выбранный контакт');retained.value=previous;receiver.append(retained);
            }
            receiver.value=previous;
        }
        const signals=element.querySelector('[data-radio-signals]');signals.replaceChildren();
        for(const signal of snapshot.signals){
            const row=node('div',`${signal.login}${signal.friendly?' · друг':''}${signal.can_send?'':' · сигнал принят, ответ вне дальности'}`);row.className='pda-radio-contact';
            if(signal.position){const p=signal.position;row.append(node('small',p.location_id?
                `Координаты: ${p.x.toFixed(1)}, ${p.y.toFixed(1)} · этаж ${p.level}`:`Клетка мира: ${p.tile_x}, ${p.tile_y}`));}
            if(signal.account_id&&signal.account_id!==snapshot.account?.id&&!signal.friendly){const friend=node('button','В друзья');friend.type='button';friend.disabled=busy||!canEdit||!snapshot.account;
                friend.onclick=()=>act({operation:'contact',login:signal.login,friendly:true});row.append(friend);}
            signals.append(row);
        }
        if(!snapshot.signals.length)signals.append(node('p',source.blocked?'Сигналы подавлены.':'Доступных сигналов нет.'));
        const saved=element.querySelector('[data-radio-saved]');saved.replaceChildren();
        for(const contact of snapshot.contacts){
            const row=node('div',`${contact.login} · ${contact.can_send?'передача доступна':'нет доступного сигнала'}`);row.className='pda-radio-contact';
            const label=node('label','Друг '),check=document.createElement('input');check.type='checkbox';check.checked=contact.friendly;check.disabled=busy||!canEdit;
            check.onchange=()=>act({operation:'contact',login:contact.login,friendly:check.checked});label.append(check);row.append(label);
            const remove=node('button','Удалить контакт');remove.type='button';remove.disabled=busy||!canEdit;
            remove.onclick=()=>act({operation:'remove_contact',target_account_id:contact.account_id});row.append(remove);saved.append(row);
        }
        if(!snapshot.contacts.length)saved.append(node('p','Сохранённых контактов нет.'));
        controls();
    }
    async function refresh(){
        if(!active||closed||busy||loading)return;
        loading=true;
        try{const fresh=await Server.getPdaNetwork(characterId,selection());if(closed||!active)return;snapshot=fresh;error.textContent=actionError;paint();}
        catch(failure){if(!closed){error.textContent=failure.message;controls();}}
        finally{loading=false;}
    }
    async function act(payload){
        if(busy||!canEdit)return;busy=true;actionError='';error.textContent='';controls();
        try{
            if(payload.operation==='jammer'){
                const source=findSource();if(!source)throw Error('КПК перемещён');
                await operateHardware({operation:'pda_jammer',path:source.path,source_id:source.item.id,enabled:payload.enabled});
            }else await Server.changePdaNetwork(characterId,{...selection(),...payload,character_revision:getData()?._revision});
            if(payload.operation==='send'){pendingMessage=null;text.value='';showNotification('Сообщение передано в эфир','system');}
            if(payload.operation==='contact')login.value='';
        }catch(failure){actionError=failure.message;error.textContent=actionError;}
        finally{busy=false;controls();await refresh();}
    }
    send.onsubmit=event=>{event.preventDefault();if(!text.value.trim())return;
        pendingMessage ||= {operation:'send',operation_id:crypto.randomUUID(),text:text.value,
            target_account_id:receiver.value?Number(receiver.value):null};void act(pendingMessage);};
    text.oninput=()=>{pendingMessage=null;};receiver.onchange=()=>{pendingMessage=null;};
    add.onsubmit=event=>{event.preventDefault();if(login.value.trim())void act({operation:'contact',login:login.value,friendly:true});};
    jammer.onclick=()=>act({operation:'jammer',enabled:!snapshot?.source.jammer_on});
    sound.onchange=()=>act({operation:'proximity_sound',enabled:sound.checked});
    element.querySelector('[data-radio-test-sound]').onclick=()=>{void testPdaSignal();};
    const onUpdate=()=>{void refresh();};window.addEventListener('pda-network-updated',onUpdate);
    window.addEventListener('pda-game-time-updated',onUpdate);
    controls();
    return {element,refresh,setActive(value,mode){
        active=value;element.querySelector('[data-radio-chat]').hidden=mode!=='chat';element.querySelector('[data-radio-contacts]').hidden=mode!=='contacts';
        if(active&&!timer){timer=setInterval(onUpdate,5000);void refresh();}else if(!active&&timer){clearInterval(timer);timer=null;}
    },close(){closed=true;active=false;if(timer)clearInterval(timer);window.removeEventListener('pda-network-updated',onUpdate);window.removeEventListener('pda-game-time-updated',onUpdate);}};
}
