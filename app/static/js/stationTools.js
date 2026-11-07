import {Server} from './api.js';

export async function showStationTools({lobbyId,locationId,characterId,object,onObject}) {
    document.getElementById('station-tools-modal')?.remove();
    const modal=document.createElement('div');modal.id='station-tools-modal';modal.className='modal';
    modal.style.cssText='display:flex;align-items:center;justify-content:center;position:fixed;inset:0;z-index:1500;background:rgba(0,0,0,.65);';
    const panel=document.createElement('div');panel.className='modal-content';
    panel.style.cssText='width:min(600px,90vw);max-height:80vh;overflow:auto;padding:20px;';modal.append(panel);document.body.append(modal);
    let current=object,busy=false;
    const node=(tag,text)=>{const item=document.createElement(tag);item.textContent=text;return item;};
    const charges=value=>{const n=Number(value)||0,last=n%10,teen=n%100>=11&&n%100<=14;
        return `${n} ${!teen&&last===1?'заряд':!teen&&last>=2&&last<=4?'заряда':'зарядов'}`;};
    const button=(text,action)=>{const b=node('button',text);b.type='button';b.className='btn btn-sm btn-secondary';b.onclick=action;return b;};
    const words={'recipe-m40':'паяльные инструменты','recipe-m42':'инструментов оружейника',
        'recipe-m43':'инструментов бронника','recipe-m44':'смесительные инструменты','recipe-m45':'паяльные инструменты'};
    const run=async(operation,extra,revision)=>{
        if(busy)return;busy=true;panel.querySelectorAll('button').forEach(b=>b.disabled=true);
        try {
            const result=await Server.operateStation(lobbyId,locationId,{operation,operation_id:crypto.randomUUID(),
                character_id:characterId,character_revision:revision,object_id:current.id,object_revision:current.revision,...extra});
            current=result.object;onObject?.(current);busy=false;await render();
        } catch(error) {
            busy=false;
            const detail=await Server.getLocationDetail(lobbyId,locationId).catch(()=>null);
            const fresh=detail?.objects?.find(entry=>entry.id===current.id);
            if(fresh)current=fresh;
            await render(error.message);
        }
    };
    const render=async(errorMessage='')=>{
        const snapshot=await Server.getCrafting(characterId);if(!modal.isConnected)return;
        panel.replaceChildren(node('h3',current.name),node('p','Один заряд установленного инструмента расходуется за одну работу.'));
        if(errorMessage)panel.append(node('p',errorMessage));
        const installed=current.properties?.station_tools||[];
        panel.append(node('h4','Установленные инструменты'));
        if(!installed.length)panel.append(node('p','Инструменты не установлены.'));
        for(const tool of installed){
            const row=node('p',`${tool.name}: ${charges(tool.uses??tool.attributes?.uses_remaining??tool.maxUses??0)} `);
            row.append(button('Забрать',()=>run('unload',{item_id:tool.id},snapshot.revision)));panel.append(row);
        }
        panel.append(node('h4','Подходящие инструменты в инвентаре'));
        const word=words[current.properties?.portable_station];
        const available=snapshot.inventory.filter(entry=>entry.item.category==='tool'&&word&&entry.item.name.toLocaleLowerCase('ru').includes(word));
        if(!available.length)panel.append(node('p','Подходящих инструментов нет.'));
        for(const entry of available){
            const row=node('p',`${entry.item.name}: ${charges(entry.uses)} `);
            row.append(button('Установить',()=>run('load',{item_path:entry.path,item_id:entry.item.id??null},snapshot.revision)));panel.append(row);
        }
        panel.append(button('Закрыть',()=>modal.remove()));
    };
    try {await render();}catch(error){panel.replaceChildren(node('p',error.message),button('Закрыть',()=>modal.remove()));}
}
