import {Server} from './api.js';
import {showNotification} from './utils.js';
import {invalidateRadiationReadings} from './radiationReadings.js';

export async function openDosimeter({characterId,itemId,itemPath,operate}){
    document.getElementById('dosimeter-modal')?.remove();
    const modal=document.createElement('div');modal.id='dosimeter-modal';modal.className='modal';
    modal.style.cssText='display:flex;position:fixed;inset:0;align-items:center;justify-content:center;z-index:1600;background:rgba(0,0,0,.65)';
    const panel=document.createElement('div');panel.className='modal-content';panel.style.cssText='width:min(520px,90vw);padding:20px';modal.append(panel);document.body.append(modal);
    let busy=false;
    const node=(tag,text)=>{const value=document.createElement(tag);value.textContent=text;return value;};
    const read=async()=>{
        const character=await Server.getCharacter(characterId),snapshot=await Server.getPortableLights(characterId);
        if(!modal.isConnected)return;
        let selected=null;
        const walk=(value,path)=>{if(Array.isArray(value))value.forEach((item,index)=>walk(item,[...path,index]));else if(value&&typeof value==='object'){
            if(itemId?value.id===itemId:JSON.stringify(path)===JSON.stringify(itemPath))selected={item:value,path};
            for(const key of ['contents','pouches','installedModules'])if(Array.isArray(value[key]))walk(value[key],[...path,key]);
        }};
        for(const [key,value]of Object.entries(character.data.inventory||{}))walk(value,['inventory',key]);
        for(const [key,value]of Object.entries(character.data.equipment||{}))walk(value,['equipment',key]);
        panel.replaceChildren(node('h3','Дозиметр'));
        const close=node('button','Закрыть');close.type='button';close.onclick=()=>modal.remove();
        if(!selected){panel.append(node('p','Дозиметр перемещён. Откройте его снова.'),close);return;}
        itemId ||= selected.item.id;
        const state=selected.item.deviceState||{},remaining=state.remaining_seconds??604800;
        if(selected.item.electronicsState?.burned){panel.append(node('p','Прибор сгорел: использование и ремонт невозможны'),close);return;}
        panel.append(node('p',`${state.on?'Включён':'Выключен'} · батарейка: ${(remaining/6048).toFixed(1)}%`));
        const act=async(operation,extra={})=>{
            if(busy)return;busy=true;for(const button of panel.querySelectorAll('button'))button.disabled=true;
            try{await operate({operation,path:selected.path,source_id:itemId,...extra});invalidateRadiationReadings();await read();}
            catch(error){showNotification(error.message,'system');for(const button of panel.querySelectorAll('button'))button.disabled=false;}
            finally{busy=false;}
        };
        const toggle=node('button',state.on?'Выключить':'Включить');toggle.type='button';toggle.disabled=remaining<=0;
        toggle.onclick=()=>act('dosimeter_toggle');panel.append(toggle);
        const batteries = snapshot.batteries.filter(battery => battery.standard_fit ?? battery.name === 'Батарейки');
        if(batteries.length){
            const select=document.createElement('select');select.className='form-control';select.setAttribute('aria-label','Батарейка для дозиметра');
            batteries.forEach((battery,index)=>{const option=node('option',battery.charge == null ? battery.name : `${battery.name} · ${battery.charge.toFixed(1)}%`);option.value=index;select.append(option);});
            const battery=node('button','Заменить батарейку');battery.type='button';battery.onclick=()=>act('dosimeter_battery',{battery_path:batteries[Number(select.value)].path});
            panel.append(select,battery);
        }
        panel.append(close);
    };
    try{await read();}catch(error){panel.append(node('p',error.message));const close=node('button','Закрыть');close.onclick=()=>modal.remove();panel.append(close);}
    return modal;
}
