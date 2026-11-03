import {Server} from './api.js';
import {showNotification} from './utils.js';

export async function showSocialCheck({characterId,lobbyId,skillPath,skillLabel,onSaved,vegetation=false,favoredMutant=''}) {
    const social=skillPath.startsWith('skills.social.');
    const characters=(await Server.getLobbyCharacters(lobbyId)).filter(character=>character.id!==characterId);
    if(window.isLocationActive){
        const {getSocialTargetCharacters}=await import('./locationScene.js');
        const visible=await getSocialTargetCharacters();
        for(const character of visible)if(character.id!==characterId&&!characters.some(existing=>existing.id===character.id))characters.push(character);
    }
    document.getElementById('social-check-modal')?.remove();
    const modal=document.createElement('div');modal.id='social-check-modal';modal.className='modal';
    modal.innerHTML=`<form class="modal-content" style="max-width:520px;display:grid;gap:12px;">
        <h3></h3><label>${social?'Собеседник, если есть':'С кем связана проверка, если есть'} <select name="target" class="form-control"><option value="">Обычная проверка без цели</option></select></label>
        <label>Сложность, заданная ГМом <input name="difficulty" type="number" min="1" max="40" value="10" class="form-control" required></label>
        ${social?'<label><input name="face" type="checkbox" checked> Собеседник видит лицо персонажа</label>':''}
        ${vegetation?'<label><input name="vegetation" type="checkbox"> Скрываюсь в растительном укрытии</label>':''}
        ${favoredMutant?'<label class="favored-mutant-context"><input name="related_favored" type="checkbox"> <span></span></label>':''}
        <div class="social-check-result" role="status" style="white-space:pre-line;"></div>
        <button class="btn btn-primary" type="submit">Бросить</button>
        <button class="btn btn-secondary diplomat-retry" type="button" hidden>Дипломат: повторить Убеждение</button>
        <button class="btn btn-secondary social-check-close" type="button">Закрыть</button></form>`;
    modal.querySelector('h3').textContent=skillLabel;
    const form=modal.querySelector('form');
    if(favoredMutant)modal.querySelector('.favored-mutant-context span').textContent=`Проверка связана с избранным мутантом: ${favoredMutant}`;
    for(const character of characters)form.elements.target.add(new Option(character.name,character.id));
    modal.querySelector('.social-check-close').onclick=()=>modal.remove();
    modal.onclick=event=>{if(event.target===modal)modal.remove();};
    const resultBox=modal.querySelector('.social-check-result'),retryButton=modal.querySelector('.diplomat-retry');
    let pendingId;
    const perform=async retry=>{
        form.querySelectorAll('button').forEach(button=>{button.disabled=true;});
        pendingId ||= crypto.randomUUID();
        try {
            const payload=retry?{retry:true,operation_id:pendingId}:{operation_id:pendingId,target_character_id:form.elements.target.value?Number(form.elements.target.value):null,
                skill_path:skillPath,difficulty:Number(form.elements.difficulty.value),face_visible:form.elements.face?.checked||false,
                vegetation:form.elements.vegetation?.checked||false,related_favored:form.elements.related_favored?.checked||false};
            const result=await Server.performSkillCheck(characterId,payload);pendingId=null;
            const check=result.check;
            resultBox.textContent=check.blocked?check.failure_reason:
                `${result.target_name}: d20 ${(check.rolls||[]).join('/')} → ${check.roll}, итог ${check.total} против СЛ ${check.difficulty} — ${check.success?'успех':'провал'}`;
            if(check.rules?.length)resultBox.textContent+='\n'+check.rules.join('\n');
            if(check.brigadier_order)resultBox.textContent+='\nУказание Бригадира: +3'+(check.brigadier_order.advantage?' и Преимущество':'');
            if(check.will_check)resultBox.textContent+=`\nЗаставить себя солгать: Воля ${check.will_check.total} против СЛ 12 — ${check.will_check.success?'успех':'провал'}`;
            retryButton.hidden=!result.diplomat_retry_available;
            await onSaved?.(result.data);
        } catch(error){showNotification(error.message||'Не удалось выполнить проверку','system');}
        finally{form.querySelectorAll('button').forEach(button=>{button.disabled=false;});}
    };
    form.onsubmit=event=>{event.preventDefault();perform(false);};retryButton.onclick=()=>perform(true);
    document.body.append(modal);modal.style.display='flex';
}
