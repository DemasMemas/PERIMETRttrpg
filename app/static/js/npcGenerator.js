import { Server } from './api.js';
import { categoryNames } from './characterTrade.js';

const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const level=()=>({id:crypto.randomUUID(),name:''});
const blankPool=(maxPrice=0)=>({category:'consumable',subcategory:'',item_class:'',min_price:0,max_price:maxPrice,draws:1,quantity:1,chance:100});
const blankProfile=(maxPrice=0)=>({name:'Пул снаряжения',budget:0,money_min:0,money_max:0,pools:[blankPool(maxPrice)]});

function modal(title,description) {
    const root=document.createElement('div');root.className='modal npc-generator-modal';
    root.style.display='flex';root.style.zIndex='1330';
    root.innerHTML=`<div class="modal-content npc-generator-dialog"><div class="character-trade-heading">
        <h3>${escapeHtml(title)}</h3><button type="button" class="btn btn-secondary npc-generator-close" aria-label="Закрыть">×</button></div>
        <p>${escapeHtml(description)}</p><div class="npc-generator-body"></div><div class="trade-error npc-generator-error" role="status"></div></div>`;
    document.body.append(root);
    root.querySelector('.npc-generator-close').onclick=()=>root.remove();
    return root;
}

function renderLevels(root,config,kind,templates) {
    const list=root.querySelector(`[data-level-list="${kind}"]`);
    list.replaceChildren();
    config[kind].forEach((entry,index)=>{
        const row=document.createElement('div');row.className='npc-generator-level';
        row.innerHTML=`<input class="form-control" maxlength="80" aria-label="Название уровня" value="${escapeHtml(entry.name)}">
            <button type="button" class="btn btn-sm btn-secondary" aria-label="Удалить уровень">Убрать</button>`;
        row.querySelector('input').oninput=event=>{entry.name=event.target.value;};
        row.querySelector('input').onchange=()=>renderCombinations(root,config,templates);
        row.querySelector('button').onclick=()=>{
            config[kind].splice(index,1);
            config.combinations=config.combinations.filter(pair=>kind==='danger_levels'
                ?pair.danger_id!==entry.id:pair.wealth_id!==entry.id);
            root.dataset.selectedPair='';
            renderLevels(root,config,kind,templates);renderCombinations(root,config,templates);
        };
        list.append(row);
    });
}

function renderProfile(root,pair,templates) {
    const profile=pair.profile;
    const form=root.querySelector('.npc-generator-profile');
    form.innerHTML=`<h4>Пул: ${escapeHtml(profile.name)}</h4><div class="loot-profile-fields">
        ${[['name','Название профиля','text'],['budget','Бюджет вещей, ₽','number'],['money_min','Деньги от, ₽','number'],['money_max','Деньги до, ₽','number']]
            .map(([key,label,type])=>`<label>${label}<input class="form-control" data-profile="${key}" type="${type}" ${type==='number'?'min="0" step="1"':''}
                value="${escapeHtml(profile[key])}"></label>`).join('')}</div><div class="npc-generator-pools"></div>
        <button type="button" class="btn btn-secondary npc-generator-add-pool">Добавить пул</button>`;
    form.querySelectorAll('[data-profile]').forEach(input=>input.oninput=()=>{
        profile[input.dataset.profile]=input.type==='number'?Number(input.value):input.value;
        if(input.dataset.profile==='name')form.querySelector('h4').textContent=`Пул: ${input.value}`;
    });
    const categories=[...new Set([...templates.map(item=>item.category),...profile.pools.map(pool=>pool.category)])]
        .sort((a,b)=>(categoryNames[a]||a).localeCompare(categoryNames[b]||b,'ru'));
    const options=(values,current,all)=>`${all?'<option value="">Все</option>':''}${[...new Set(values)].filter(Boolean).sort((a,b)=>String(a).localeCompare(String(b),'ru'))
        .map(value=>`<option value="${escapeHtml(value)}" ${value===current?'selected':''}>${escapeHtml(value)}</option>`).join('')}`;
    function pools(){
        const list=form.querySelector('.npc-generator-pools');list.replaceChildren();
        profile.pools.forEach((pool,index)=>{
            const row=document.createElement('section');row.className='loot-pool';
            const same=templates.filter(item=>item.category===pool.category);
            row.innerHTML=`<div class="loot-pool-heading"><strong>Пул ${index+1}</strong>
                <button type="button" class="btn btn-sm btn-secondary" data-remove>Убрать</button></div>
                <div class="loot-pool-filters"><label>Категория<select class="form-control" data-pool="category">
                    ${categories.map(value=>`<option value="${escapeHtml(value)}" ${value===pool.category?'selected':''}>${escapeHtml(categoryNames[value]||value)}</option>`).join('')}</select></label>
                <label>Тип<select class="form-control" data-pool="subcategory">${options(same.map(item=>item.subcategory),pool.subcategory,true)}</select></label>
                <label>Класс<select class="form-control" data-pool="item_class">${options(same.map(item=>item.item_class),pool.item_class,true)}</select></label></div>
                <div class="loot-pool-numbers">${[['min_price','Цена от, ₽',0,undefined],['max_price','До, ₽',0,undefined],
                    ['draws','Выборов',1,50],['quantity','В стопке',1,1000],['chance','Шанс, %',0,100]]
                    .map(([key,label,min,max])=>`<label>${label}<input class="form-control" data-pool="${key}" type="number"
                        min="${min}" ${max===undefined?'':`max="${max}"`} step="1" value="${pool[key]}"></label>`).join('')}</div>`;
            row.querySelectorAll('[data-pool]').forEach(input=>{
                input[input.tagName==='SELECT'?'onchange':'oninput']=()=>{
                    pool[input.dataset.pool]=input.tagName==='SELECT'?input.value:Number(input.value);
                    if(input.dataset.pool==='category'){pool.subcategory='';pool.item_class='';pools();}
                };
            });
            row.querySelector('[data-remove]').onclick=()=>{profile.pools.splice(index,1);pools();};
            list.append(row);
        });
        form.querySelector('.npc-generator-add-pool').disabled=profile.pools.length>=30;
    }
    form.querySelector('.npc-generator-add-pool').onclick=()=>{
        profile.pools.push(blankPool(Math.max(0,...templates.map(item=>Number(item.price)||0))));pools();
    };
    pools();
}

function renderCombinations(root,config,templates) {
    const area=root.querySelector('.npc-generator-combinations');
    const name=(levels,id)=>levels.find(entry=>entry.id===id)?.name||'Без названия';
    area.innerHTML=config.combinations.map((pair,index)=>`<button type="button" class="btn btn-sm btn-secondary" data-pair="${index}">
        ${escapeHtml(name(config.danger_levels,pair.danger_id))} · ${escapeHtml(name(config.wealth_levels,pair.wealth_id))}</button>`).join('');
    area.querySelectorAll('[data-pair]').forEach(button=>button.onclick=()=>{
        const index=Number(button.dataset.pair);root.dataset.selectedPair=String(index);
        renderProfile(root,config.combinations[index],templates);
        root.querySelector('.npc-generator-remove-pair').hidden=false;
    });
    const selected=root.dataset.selectedPair===''?-1:Number(root.dataset.selectedPair);
    if(!Number.isInteger(selected)||selected<0||selected>=config.combinations.length){
        root.dataset.selectedPair='';root.querySelector('.npc-generator-profile').replaceChildren();
        root.querySelector('.npc-generator-remove-pair').hidden=true;
    }
    for(const [selector,levels] of [['.npc-generator-danger',config.danger_levels],['.npc-generator-wealth',config.wealth_levels]]){
        const control=root.querySelector(selector);
        control.innerHTML=`<option value="">Выберите уровень</option>${levels.map(entry=>`<option value="${escapeHtml(entry.id)}">${escapeHtml(entry.name)}</option>`).join('')}`;
    }
}

export async function openNpcGeneratorConfig(lobbyId,characters=[]) {
    const [config,catalog]=await Promise.all([Server.getNpcGenerationConfig(lobbyId),Server.getLobbyTemplates(lobbyId,'')]);
    const templates=(catalog.global||[]).filter(item=>item.id>0&&!['background','special_trait','modification'].includes(item.category)
        && !item.attributes?.not_for_sale);
    const root=modal('Настройка генератора НПС','Имена и уровни задаёт ГМ. Численные характеристики и способности пока вводятся вручную. Для каждого сочетания можно сохранить собственный пул снаряжения.');
    root.querySelector('.npc-generator-body').innerHTML=`<label>Имена НПС, по одному в строке<textarea class="form-control npc-generator-names" rows="4"></textarea></label>
        <div class="npc-generator-levels"><section><h4>Опасность</h4><div data-level-list="danger_levels"></div>
            <button type="button" class="btn btn-sm btn-secondary" data-add-level="danger_levels">Добавить уровень</button></section>
            <section><h4>Богатство</h4><div data-level-list="wealth_levels"></div>
            <button type="button" class="btn btn-sm btn-secondary" data-add-level="wealth_levels">Добавить уровень</button></section></div>
        <h4>Сочетания и пулы</h4><div class="npc-generator-pair-picker"><select class="form-control npc-generator-danger" aria-label="Опасность"></select>
            <select class="form-control npc-generator-wealth" aria-label="Богатство"></select>
            <select class="form-control npc-generator-copy" aria-label="Взять пул за основу"></select>
            <button type="button" class="btn btn-sm btn-secondary npc-generator-add-pair">Настроить сочетание</button></div>
        <div class="npc-generator-combinations"></div><div class="npc-generator-profile"></div>
        <button type="button" class="btn btn-sm btn-secondary npc-generator-remove-pair" hidden>Удалить выбранное сочетание</button>
        <div class="form-actions"><button type="button" class="btn btn-primary npc-generator-save">Сохранить настройки</button></div>`;
    const body=root.querySelector('.npc-generator-body');
    body.querySelector('.npc-generator-names').value=config.names.join('\n');
    body.querySelector('.npc-generator-names').oninput=event=>{
        config.names=event.target.value.split(/\r?\n/).map(name=>name.trim()).filter(Boolean);
    };
    for(const kind of ['danger_levels','wealth_levels']){
        renderLevels(root,config,kind,templates);
        body.querySelector(`[data-add-level="${kind}"]`).onclick=()=>{config[kind].push(level());renderLevels(root,config,kind,templates);renderCombinations(root,config,templates);};
    }
    const sourceProfiles=characters.filter(character=>character.data?.npcLoot?.profile).map(character=>({
        label:`НПС: ${character.name}`,profile:character.data.npcLoot.profile}));
    const renderSources=()=>{
        const sources=[...sourceProfiles,...config.combinations.map((pair,index)=>({
            label:`Сочетание ${index+1}: ${pair.profile.name}`,profile:pair.profile}))];
        body.querySelector('.npc-generator-copy').innerHTML='<option value="">Пустой пул</option>'+sources.map((source,index)=>
            `<option value="${index}">${escapeHtml(source.label)}</option>`).join('');
        return sources;
    };
    renderCombinations(root,config,templates);renderSources();
    body.querySelector('.npc-generator-add-pair').onclick=()=>{
        const danger_id=body.querySelector('.npc-generator-danger').value;
        const wealth_id=body.querySelector('.npc-generator-wealth').value;
        const error=root.querySelector('.npc-generator-error');
        if(!danger_id||!wealth_id){error.textContent='Выберите оба уровня';return;}
        if(config.combinations.some(pair=>pair.danger_id===danger_id&&pair.wealth_id===wealth_id)){
            error.textContent='Это сочетание уже настроено';return;
        }
        const sourceIndex=body.querySelector('.npc-generator-copy').value;
        const source=sourceIndex===''?null:renderSources()[Number(sourceIndex)];
        const maxPrice=Math.max(0,...templates.map(item=>Number(item.price)||0));
        config.combinations.push({danger_id,wealth_id,profile:structuredClone(source?.profile||blankProfile(maxPrice))});
        root.dataset.selectedPair=String(config.combinations.length-1);
        renderCombinations(root,config,templates);renderProfile(root,config.combinations.at(-1),templates);
        body.querySelector('.npc-generator-remove-pair').hidden=false;renderSources();error.textContent='';
    };
    body.querySelector('.npc-generator-remove-pair').onclick=()=>{
        const index=Number(root.dataset.selectedPair);
        if(Number.isInteger(index)&&index>=0&&index<config.combinations.length)config.combinations.splice(index,1);
        root.dataset.selectedPair='';renderCombinations(root,config,templates);renderSources();
    };
    body.querySelector('.npc-generator-save').onclick=async()=>{
        const button=body.querySelector('.npc-generator-save');button.disabled=true;
        try{
            const saved=await Server.saveNpcGenerationConfig(lobbyId,config);
            config.revision=saved.revision;root.querySelector('.npc-generator-error').textContent='Настройки сохранены';
        }catch(error){root.querySelector('.npc-generator-error').textContent=error.message||'Не удалось сохранить настройки';}
        finally{button.disabled=false;}
    };
}

export async function openNpcCreation(lobbyId,onCreated) {
    const config=await Server.getNpcGenerationConfig(lobbyId);
    const root=modal('Создать НПС','Имя можно переписать или перебросить. Уровни выбирают сохранённый пул добычи; после создания ГМ настраивает числа, экипировку и размещение.');
    const body=root.querySelector('.npc-generator-body');
    body.innerHTML=`<label>Имя НПС<input class="form-control npc-generator-name" maxlength="100"></label>
        <button type="button" class="btn btn-sm btn-secondary npc-generator-reroll" ${config.names.length?'':'disabled'}>Перебросить имя</button>
        <label>Сочетание уровней<select class="form-control npc-generator-pair"><option value="">Без пула снаряжения</option>
            ${config.combinations.map((pair,index)=>`<option value="${index}">${escapeHtml(config.danger_levels.find(level=>level.id===pair.danger_id)?.name||'Опасность')}
                · ${escapeHtml(config.wealth_levels.find(level=>level.id===pair.wealth_id)?.name||'Богатство')}</option>`).join('')}</select></label>
        <div class="form-actions"><button type="button" class="btn btn-primary npc-generator-create">Создать НПС</button></div>`;
    const input=body.querySelector('.npc-generator-name');let operation=crypto.randomUUID();
    const reroll=async()=>{
        const button=body.querySelector('.npc-generator-reroll');button.disabled=true;
        try{input.value=(await Server.previewNpcName(lobbyId,input.value||null)).name;}
        catch(error){root.querySelector('.npc-generator-error').textContent=error.message||'Не удалось подобрать имя';}
        finally{button.disabled=false;}
    };
    body.querySelector('.npc-generator-reroll').onclick=reroll;
    if(config.names.length)await reroll();
    body.querySelector('.npc-generator-create').onclick=async()=>{
        const button=body.querySelector('.npc-generator-create');button.disabled=true;
        const pairIndex=body.querySelector('.npc-generator-pair').value;
        const pair=pairIndex===''?null:config.combinations[Number(pairIndex)];
        try{
            await Server.createGeneratedNpc(lobbyId,{operation_id:operation,name:input.value,
                danger_id:pair?.danger_id??null,wealth_id:pair?.wealth_id??null});
            root.remove();await onCreated?.();
        }catch(error){root.querySelector('.npc-generator-error').textContent=error.message||'Не удалось создать НПС';
            button.disabled=false;}
    };
}
