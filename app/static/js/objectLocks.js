import {Server} from './api.js';
import {showNotification} from './utils.js';

export async function showObjectLock({lobbyId,characterId,object,inCombat,onObject}) {
    if(document.getElementById('object-lock-modal'))return;
    const character=await Server.getCharacter(characterId),entries=[];
    const names=new Set(['Отмычка','Монтировка','Горелка','Термический Заряд']);
    const visit=(items,path)=>{for(const [index,item] of (items||[]).entries()){
        const sourcePath=[...path,index];if(names.has(item?.name))entries.push({item,path:sourcePath});
        if(Array.isArray(item?.contents))visit(item.contents,[...sourcePath,'contents']);
    }};
    visit(character.data.inventory?.pockets,['inventory','pockets']);visit(character.data.inventory?.backpack,['inventory','backpack']);
    for(const slot of ['belt','vest'])for(const [index,pouch]of(character.data.equipment?.[slot]?.pouches||[]).entries())
        visit(pouch.contents,['equipment',slot,'pouches',index,'contents']);
    const modal=document.createElement('div');modal.id='object-lock-modal';modal.className='modal';
    modal.innerHTML=`<div class="modal-content" style="max-width:520px;width:90%;">
        <button type="button" class="close" aria-label="Закрыть">×</button><h3>Вскрытие замка</h3>
        <p data-lock-title></p><label>Способ<select class="form-control" data-lock-mode><option value="tools">Инструментами</option><option value="force">Силой</option></select></label>
        <label data-lock-tool-label>Инструмент<select class="form-control" data-lock-tool><option value="">Без инструментов · штраф 10</option></select></label>
        <p data-lock-info>Инструментами: сложность объекта − бонус Ловкости − бонус Инженерии − бонус инструмента.</p>
        <p data-lock-result role="status"></p><div class="form-actions"><button type="button" class="btn btn-primary" data-lock-attempt></button>
        <button type="button" class="btn btn-secondary" data-lock-cancel>Отмена</button></div></div>`;
    modal.querySelector('[data-lock-title]').textContent=`${object.name||'Объект'} · сложность ${object.properties?.lock_difficulty??8}`;
    const select=modal.querySelector('[data-lock-tool]'),mode=modal.querySelector('[data-lock-mode]'),button=modal.querySelector('[data-lock-attempt]');
    entries.forEach((entry,index)=>{const option=document.createElement('option');option.value=index;option.textContent=entry.item.name;select.append(option);});
    const close=()=>modal.remove();modal.querySelector('.close').onclick=modal.querySelector('[data-lock-cancel]').onclick=close;
    mode.onchange=()=>{
        modal.querySelector('[data-lock-tool-label]').hidden=mode.value==='force';
        modal.querySelector('[data-lock-info]').textContent=mode.value==='force'
            ?'Силой: сложность объекта × 1,5 − бонус Силы. При неудаче замок может заклинить.'
            :'Инструментами: сложность объекта − бонус Ловкости − бонус Инженерии − бонус инструмента. Отмычка при неудаче может сломаться.';
    };
    button.textContent=inCombat?'Попытка · 3 ОД':'Попытка';button.disabled=Boolean(object.properties?.lock_jammed);
    if(object.properties?.lock_jammed)modal.querySelector('[data-lock-result]').textContent='Замок заклинило; открыть объект больше нельзя.';
    const operationId=crypto.randomUUID();
    button.onclick=async()=>{
        if(button.disabled)return;button.disabled=true;mode.disabled=true;select.disabled=true;
        try{
            const entry=mode.value==='tools'&&select.value!==''?entries[Number(select.value)]:null;
            const response=await Server.attemptObjectLock(lobbyId,object.id,{
                character_id:Number(characterId),character_revision:character.data._revision,object_revision:object.revision,
                operation_id:operationId,mode:mode.value,tool:entry?{path:entry.path,snapshot:entry.item}:null});
            onObject?.(response.object);const result=response.operation;
            modal.querySelector('[data-lock-result]').textContent=`d20: ${result.roll}, СЛ ${result.difficulty}. `+
                (result.success?'Замок открыт.':'Попытка не удалась.')+(result.tool_broken?' Отмычка сломалась.':'')+
                (result.jammed?' Замок заклинило; открыть объект больше нельзя.':'');
            button.textContent='Готово';button.disabled=false;button.onclick=close;
            modal.querySelector('[data-lock-cancel]').remove();
            showNotification(result.success?'Замок открыт':'Попытка вскрытия не удалась',result.success?'success':'system');
        }catch(error){modal.querySelector('[data-lock-result]').textContent=error.message;button.disabled=false;mode.disabled=false;select.disabled=false;}
    };
    document.body.append(modal);modal.style.display='flex';
}
