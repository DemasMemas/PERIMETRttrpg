import { Server } from './api.js';

const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export async function showSessionReward(lobbyId,characterId,onUpdated){
    if(document.getElementById('session-reward-modal'))return;
    const rules=await Server.characterCreationRules(lobbyId),modal=document.createElement('div');
    modal.id='session-reward-modal';modal.className='modal';modal.style.display='flex';modal.style.zIndex='11000';
    modal.innerHTML=`<div class="modal-content" style="max-width:600px;max-height:90vh;overflow:auto"><button type="button" class="close" aria-label="Закрыть">×</button><h3>Награда за сессию или цель</h3><form><p>Автоматически: 1 свободное очко навыков.</p>${[['roleplay','Отыгрыш'],['heroism','Героизм'],['contribution','Вклад в решение сложной ситуации'],['gmBonus','Поощрение от ведущего']].map(([k,n])=>`<p><label><input type="checkbox" name="${k}"> ${n}: +1 свободное очко</label></p>`).join('')}<label>Обучение: 1 очко в подходящий навык<select name="trainingSkill" class="form-control"><option value="">Без обучения</option>${Object.entries(rules.skills).flatMap(([g,s])=>Object.entries(s).map(([k,n])=>`<option value="${g}.${k}">${escape(n)}</option>`)).join('')}</select></label><label>Использованное оружие (для СБУшника)<select name="weaponType" class="form-control"><option value="">Не выбрано</option>${Object.entries(rules.weaponTypes).map(([k,n])=>`<option value="${k}">${escape(n)}</option>`).join('')}</select></label><p>Бонусы и риски обучения от предыстории, коэффициент «Заинтересованного» применяются автоматически.</p><p data-error role="alert"></p><button type="submit" class="btn">Начислить награду</button></form></div>`;
    document.body.appendChild(modal);let busy=false;const operationId=crypto.randomUUID();
    modal.querySelector('.close').onclick=()=>{if(!busy)modal.remove()};
    modal.querySelector('form').onsubmit=async event=>{
        event.preventDefault();if(busy)return;busy=true;
        const form=event.target,payload={operationId};for(const k of ['roleplay','heroism','contribution','gmBonus'])payload[k]=form.elements[k].checked;
        if(form.elements.trainingSkill.value)payload.trainingSkill=form.elements.trainingSkill.value;
        if(form.elements.weaponType.value)payload.weaponType=form.elements.weaponType.value;
        form.querySelector('[type=submit]').disabled=true;
        try{const result=await Server.awardCharacterSession(lobbyId,characterId,payload);modal.remove();onUpdated(result);}
        catch(error){busy=false;form.querySelector('[type=submit]').disabled=false;modal.querySelector('[data-error]').textContent=error.message;}
    };
}
