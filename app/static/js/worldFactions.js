import { Server } from './api.js';

const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const newEntry=()=>({id:crypto.randomUUID(),name:''});

export async function openWorldFactionSettings(lobbyId) {
    if(document.querySelector('.world-faction-modal'))return;
    let [config,characters]=await Promise.all([Server.getWorldFactions(lobbyId),Server.getLobbyCharacters(lobbyId)]);
    if(document.querySelector('.world-faction-modal'))return;
    let savedGroups=structuredClone(config.groups);
    const root=document.createElement('div');root.className='modal npc-generator-modal world-faction-modal';
    root.style.display='flex';root.style.zIndex='1330';
    root.innerHTML=`<div class="modal-content npc-generator-dialog"><div class="character-trade-heading">
        <h3>Группировки мира</h3><button type="button" class="btn btn-secondary faction-close" aria-label="Закрыть">×</button></div>
        <p>Готовый набор содержит группировки и явно названных врагов из авторской таблицы. Отделения в ней пока не перечислены; ГМ может добавить их в собственном наборе или копии готового.</p>
        <div class="npc-generator-body"><button type="button" class="btn btn-secondary faction-copy-ready" hidden>Создать копию для донастройки</button>
        <h4>Группировки</h4><div class="faction-groups"></div>
        <button type="button" class="btn btn-secondary faction-add-group">Добавить группировку</button>
        <h4>Отношения между группировками</h4><div class="faction-relations"></div>
        <button type="button" class="btn btn-secondary faction-add-relation">Добавить отношение</button>
        <h4>Принадлежность персонажей</h4><small>Сначала сохраните изменения группировок, затем назначайте персонажей.</small>
        <div class="faction-assignments"></div>
        <div style="margin-top:1rem"><button type="button" class="btn btn-primary faction-save">Сохранить</button></div>
        <div class="trade-error faction-error" role="status"></div></div></div>`;
    document.body.append(root);
    root.querySelector('.faction-close').onclick=()=>root.remove();
    const render=()=>{
        const ready=config.source==='ready';
        root.querySelector('.faction-copy-ready').hidden=!ready;
        const groups=root.querySelector('.faction-groups');groups.replaceChildren();
        config.groups.forEach((group,index)=>{
            const card=document.createElement('section');card.className='npc-generator-level';
            card.style.display='block';card.style.marginBottom='1rem';
            card.innerHTML=`<div class="character-trade-heading"><input class="form-control faction-group-name" maxlength="100"
                aria-label="Название группировки" placeholder="Название группировки" value="${escapeHtml(group.name)}">
                <button type="button" class="btn btn-sm btn-danger faction-remove-group">Убрать</button></div>
                <div class="faction-members"></div><button type="button" class="btn btn-sm btn-secondary faction-add-member">Добавить фракцию</button>`;
            card.querySelector('.faction-group-name').oninput=event=>{group.name=event.target.value;};
            card.querySelector('.faction-remove-group').onclick=()=>{
                config.groups.splice(index,1);config.relations=config.relations.filter(r=>r.from_id!==group.id&&r.to_id!==group.id);render();};
            card.querySelector('.faction-add-member').onclick=()=>{group.factions.push(newEntry());render();};
            const members=card.querySelector('.faction-members');
            group.factions.forEach((faction,memberIndex)=>{
                const row=document.createElement('div');row.className='npc-generator-level';
                row.innerHTML=`<input class="form-control" maxlength="100" aria-label="Название фракции"
                    placeholder="Отделение группировки" value="${escapeHtml(faction.name)}">
                    <button type="button" class="btn btn-sm btn-danger">Убрать</button>`;
                row.querySelector('input').oninput=event=>{faction.name=event.target.value;};
                row.querySelector('button').onclick=()=>{group.factions.splice(memberIndex,1);render();};
                members.append(row);
            });
            groups.append(card);
        });
        const relations=root.querySelector('.faction-relations');relations.replaceChildren();
        config.relations.forEach((relation,index)=>{
            const row=document.createElement('div');row.className='npc-generator-level';
            const options=config.groups.map(group=>`<option value="${escapeHtml(group.id)}">${escapeHtml(group.name||'Без названия')}</option>`).join('');
            row.innerHTML=`<select class="form-control faction-from" aria-label="От группировки">${options}</select>
                <select class="form-control faction-to" aria-label="К группировке">${options}</select>
                <input class="form-control faction-value" maxlength="100" placeholder="Отношение" aria-label="Отношение"
                    value="${escapeHtml(relation.value)}"><button type="button" class="btn btn-sm btn-danger">Убрать</button>`;
            row.querySelector('.faction-from').value=relation.from_id;
            row.querySelector('.faction-to').value=relation.to_id;
            row.querySelector('.faction-from').onchange=event=>{relation.from_id=event.target.value;};
            row.querySelector('.faction-to').onchange=event=>{relation.to_id=event.target.value;};
            row.querySelector('.faction-value').oninput=event=>{relation.value=event.target.value;};
            row.querySelector('button').onclick=()=>{config.relations.splice(index,1);render();};
            relations.append(row);
        });
        for(const control of root.querySelectorAll('.faction-groups input,.faction-groups button,.faction-relations input,.faction-relations select,.faction-relations button'))
            control.disabled=ready;
        for(const selector of ['.faction-add-group','.faction-add-relation','.faction-save'])
            root.querySelector(selector).disabled=ready;
        const assignments=root.querySelector('.faction-assignments');assignments.replaceChildren();
        characters.forEach((character,index)=>{
            const row=document.createElement('div');row.className='npc-generator-level';
            const current=character.data?.basic||{};
            row.innerHTML=`<span>${escapeHtml(character.name)}</span>
                <select class="form-control faction-assignment-group" aria-label="Группировка персонажа">
                    <option value="">Без группировки</option>${savedGroups.map(group=>`<option value="${escapeHtml(group.id)}">${escapeHtml(group.name)}</option>`).join('')}</select>
                <select class="form-control faction-assignment-member" aria-label="Фракция персонажа"></select>
                <button type="button" class="btn btn-sm btn-secondary">Назначить</button>`;
            const groupSelect=row.querySelector('.faction-assignment-group');
            const memberSelect=row.querySelector('.faction-assignment-member');
            groupSelect.value=current.group_id||'';
            const renderMembers=selected=>{
                const group=savedGroups.find(entry=>entry.id===groupSelect.value);
                memberSelect.innerHTML='<option value="">Без фракции</option>'+(group?.factions||[]).map(faction=>
                    `<option value="${escapeHtml(faction.id)}">${escapeHtml(faction.name)}</option>`).join('');
                memberSelect.value=selected||'';
            };
            groupSelect.onchange=()=>renderMembers('');renderMembers(current.faction_id);
            row.querySelector('button').onclick=async()=>{
                const button=row.querySelector('button');button.disabled=true;
                try{
                    const result=await Server.assignWorldFaction(lobbyId,character.id,{
                        character_revision:character.revision,group_id:groupSelect.value||null,faction_id:memberSelect.value||null});
                    characters[index]=result.character;
                    root.querySelector('.faction-error').textContent=`Принадлежность «${character.name}» сохранена`;
                }catch(error){root.querySelector('.faction-error').textContent=error.message||'Не удалось назначить персонажа';}
                button.disabled=false;
            };
            assignments.append(row);
        });
    };
    root.querySelector('.faction-copy-ready').onclick=()=>{config.source='ready_customized';render();};
    root.querySelector('.faction-add-group').onclick=()=>{config.groups.push({...newEntry(),factions:[]});render();};
    root.querySelector('.faction-add-relation').onclick=()=>{
        if(config.groups.length<2){root.querySelector('.faction-error').textContent='Для отношения нужны две группировки';return;}
        config.relations.push({from_id:config.groups[0].id,to_id:config.groups[1].id,value:''});render();};
    root.querySelector('.faction-save').onclick=async()=>{
        const button=root.querySelector('.faction-save');button.disabled=true;
        try{const saved=await Server.saveWorldFactions(lobbyId,config);
            config={revision:saved.revision,source:saved.source,groups:saved.groups,relations:saved.relations};
            savedGroups=structuredClone(config.groups);
            root.querySelector('.faction-error').textContent='Группировки сохранены';render();button.disabled=false;}
        catch(error){root.querySelector('.faction-error').textContent=error.message||'Не удалось сохранить группировки';button.disabled=false;}
    };
    render();
}
