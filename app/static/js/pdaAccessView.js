import {Server} from './api.js';
import {showNotification} from './utils.js';
import {runController} from './itemControllers.js';
import {createPdaNetworkView} from './pdaNetworkView.js';

const node=(tag,text)=>{const el=document.createElement(tag);if(text!=null)el.textContent=text;return el;};
const modeName=mode=>mode==='physical'?'кабель':'радиосвязь';

export function createPdaAccessView({characterId,selection,onSaved,canEdit,getData}){
    const element=node('section');element.className='pda-access';
    const status=node('p'),error=node('p'),body=node('div');error.setAttribute('role','alert');element.append(status,error,body);
    let active=false,closed=false,busy=false,loading=false,timer=null,snapshot=null,selected=null,network=null,tab='account';
    const request=extra=>({...selection(),...extra});
    const button=(text,handler)=>{const el=node('button',text);el.type='button';el.className='btn btn-secondary';el.disabled=busy||!canEdit;
        el.onclick=handler;return el;};
    function controls(){for(const input of body.querySelectorAll('button,input,textarea,select'))input.disabled=busy||!canEdit;}
    async function act(payload,software=false){
        if(busy||!canEdit)return;busy=true;error.textContent='';controls();
        try{
            const data=request({...payload,...(selected?{session_id:selected}:{}),operation_id:payload.operation_id||crypto.randomUUID()});
            if(software){const result=await Server.changePdaAccess(characterId,{...data,software:true});await onSaved?.(result.data);
                if(payload.operation==='save_notes')showNotification('Заметки сохранены','system');}
            else await runController(characterId,data,onSaved,{actionKey:'pda_access',send:Server.changePdaAccess});
            network?.close();network=null;body.replaceChildren();
        }catch(failure){error.textContent=failure.message;showNotification(failure.message,'system');}
        finally{busy=false;controls();await refresh();}
    }
    async function refresh(){
        if(!active||closed||loading||busy)return;loading=true;
        try{
            const fresh=await Server.getPdaAccess(characterId,request(selected?{session_id:selected}:{}));
            if(closed||!active)return;snapshot=fresh;
            // Не заменяем поля и не теряем несохранённые заметки при фоновом опросе.
            if(!selected||!body.childElementCount)paint();
            else if(fresh.session.status!=='active')lost('Соединение разорвано');
            status.textContent=selected?`Управление: ${fresh.session.name} · ${modeName(fresh.session.mode)}`:status.textContent;
        }catch(failure){if(!closed){error.textContent=failure.message;if(selected)lost(failure.message);}}
        finally{loading=false;}
    }
    function lost(message){network?.close();network=null;selected=null;snapshot=null;body.replaceChildren();status.textContent=message;}
    function open(id){network?.close();network=null;selected=id;body.replaceChildren();void refresh();}
    function paint(){
        network?.close();network=null;body.replaceChildren();
        if(!snapshot)return;
        if(selected){paintTarget();return;}
        const level=snapshot.source.decipher;
        status.textContent=level?`Дешифратор ${level}-го порядка · взлом ${snapshot.duration_seconds/60} мин игрового времени`:'Для взлома обязателен дешифратор.';
        if(snapshot.source.blocked)status.textContent+=' · '+snapshot.source.blocked;
        body.append(node('p','Кабель требует физического доступа; дистанционный взлом — двусторонней связи. После потери соединения доступ прекращается. Подключение и отключение: 3 ОД.'));
        body.append(node('h3','Доступные устройства'));
        for(const target of snapshot.targets){
            const row=node('article');row.className='pda-file-card';row.append(node('strong',target.name));
            for(const mode of target.methods){const start=button(`Начать взлом · ${modeName(mode)} · 3 ОД`,()=>act({operation:'start',mode,target_token:target.token}));start.disabled||=!level;row.append(start);}
            body.append(row);
        }
        if(!snapshot.targets.length)body.append(node('p','Доступных устройств нет.'));
        for(const [title,rows,incoming]of [['Мои подключения',snapshot.sessions,false],['Входящие подключения',snapshot.incoming,true]]){
            body.append(node('h3',title));
            for(const session of rows){
                const row=node('article');row.className='pda-file-card';
                const label=session.status==='working'?`дешифровка · осталось ${Math.ceil(session.remaining_seconds/60)} мин`:
                    session.status==='active'?'доступ получен':session.closed_reason||'соединение закрыто';
                row.append(node('strong',`${session.name} · ${modeName(session.mode)}`),node('p',label));
                if(session.status==='active'&&!incoming)row.append(button('Управлять КПК',()=>open(session.id)));
                if(session.status!=='closed'){
                    row.append(button('Разорвать соединение · 3 ОД',()=>act({operation:'disconnect',session_id:session.id})));
                    if(incoming&&session.status==='active'&&session.mode==='remote'&&snapshot.source.counter){
                        row.append(node('small','Контратака: шанс 25% вывести из строя КПК взломщика.'));
                        row.append(button('Контратака · 3 ОД',()=>act({operation:'counter',session_id:session.id})));
                    }
                }
                body.append(row);
            }
            if(!rows.length)body.append(node('p','Подключений пока нет.'));
        }
    }
    function paintTarget(){
        const target=snapshot;
        status.textContent=`Управление: ${target.session.name} · ${modeName(target.session.mode)}`;
        body.append(button('К подключениям',()=>{lost('');void refresh();}),button('Обновить устройство',()=>{body.replaceChildren();void refresh();}),
            node('p',`Заряд ${target.device.power.charge_percent.toFixed(1)}%`),
            button('Выключить подключённый КПК · 1 ОД',()=>act({operation:'power',enabled:false})));
        if(target.device.has_jammer)body.append(button(target.device.jammer_on?'Выключить глушилку · 1 ОД':'Включить глушилку · 1 ОД',()=>act({operation:'jammer',enabled:!target.device.jammer_on})));
        const tabs=node('div');tabs.className='pda-access-tabs';
        for(const [id,label]of [['account','Аккаунт и заметки'],['chat','Общий чат'],['contacts','Контакты'],['files','Файлы'],['charges','Заряды']]){
            const b=button(label,()=>{tab=id;paint();});if(tab===id)b.classList.add('active');tabs.append(b);
        }
        body.append(tabs);
        if(tab==='account'){
            if(target.account){
                body.append(node('h3',target.account.login));const notes=node('textarea');notes.className='form-control pda-account-notes';notes.maxLength=10000;
                notes.setAttribute('aria-label','Заметки подключённого КПК');notes.value=target.account.notes;notes.readOnly=!canEdit;
                body.append(notes,button('Сохранить заметки',()=>act({operation:'save_notes',notes:notes.value},true)),button('Выйти из аккаунта',()=>act({operation:'logout'},true)));
            }else{
                const form=node('form');form.className='pda-account-form';form.autocomplete='off';
                const login=node('input'),password=node('input');password.type='password';password.autocomplete='new-password';login.autocomplete='off';
                login.maxLength=32;password.maxLength=128;login.className=password.className='form-control';
                const l=node('label','Логин'),p=node('label','Пароль');l.className=p.className='pda-account-field';l.append(login);p.append(password);
                const mode=node('select');mode.setAttribute('aria-label','Действие с аккаунтом подключённого КПК');
                for(const [id,label]of [['login','Войти'],['register','Создать аккаунт']]){const option=node('option',label);option.value=id;mode.append(option);}
                const submit=node('button','Войти');submit.type='submit';submit.className='btn btn-secondary';mode.onchange=()=>submit.textContent=mode.value==='login'?'Войти':'Создать аккаунт';
                form.append(l,p,mode,submit);form.onsubmit=e=>{e.preventDefault();void act({operation:mode.value,login:login.value,password:password.value},true);};
                body.append(node('p','Логин: 3–32 буквы, цифры, точки, дефисы или _. Пароль: 6–128 символов.'),form);
            }
        }else if(tab==='chat'||tab==='contacts'){
            const service={getPdaNetwork:async()=>{
                const fresh=await Server.getPdaAccess(characterId,request({session_id:selected}));
                if(!fresh.network)throw Error('Нет положения подключённого КПК на карте');return fresh.network;
            },changePdaNetwork:async(_id,payload)=>act({...payload,operation:payload.operation},true)};
            network=createPdaNetworkView({characterId,findSource:()=>({path:selection().item_path,item:{id:selection().item_id}}),getData,canEdit,
                operateHardware:payload=>act({operation:'jammer',enabled:payload.enabled}),service});
            body.append(network.element);network.setActive(true,tab);
        }else if(tab==='files'){
            body.append(node('p','Файлы на подключённом физическом устройстве.'));
            for(const file of target.files){const card=node('article');card.className='pda-file-card';card.append(node('strong',file.name));body.append(card);}
            if(!target.files.length)body.append(node('p','В КПК пока нет книг.'));
        }else if(tab==='charges'){
            body.append(node('p','Команда: 1 ОД; свой включённый КПК должен быть в руке. Дальность от подключённого КПК до заряда — 250 м.'));
            for(const charge of target.charges){const row=node('article');row.className='pda-file-card';
                const fire=button('Подорвать · 1 ОД',()=>act({operation:'detonate',attachment_id:charge.id}));fire.disabled||=!charge.armed||!charge.reachable;
                row.append(node('strong',charge.label),node('p',!charge.armed?'Обезврежен':charge.reachable?'Связь есть':'Нет связи'),fire);body.append(row);}
            if(!target.charges.length)body.append(node('p','Привязанных зарядов нет.'));
        }
    }
    const update=()=>{void refresh();};
    window.addEventListener('pda-access-updated',update);window.addEventListener('pda-game-time-updated',update);
    return {element,setActive(value){active=value;network?.setActive(value&&(tab==='chat'||tab==='contacts'),tab);
        if(active&&!timer){timer=setInterval(update,5000);void refresh();}else if(!active&&timer){clearInterval(timer);timer=null;}
    },close(){closed=true;active=false;if(timer)clearInterval(timer);network?.close();window.removeEventListener('pda-access-updated',update);window.removeEventListener('pda-game-time-updated',update);}};
}
