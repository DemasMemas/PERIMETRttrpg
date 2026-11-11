import {Server} from './api.js';
import {showNotification} from './utils.js';
import {modificationTitle,renderModificationMap} from './modificationOptions.js';

const el=(tag,text,css)=>{const e=document.createElement(tag);if(text!=null)e.textContent=text;if(css)e.className=css;return e;};
const button=(text,action)=>{const b=el('button',text,'btn btn-secondary');b.type='button';b.onclick=action;return b;};
const select=(label,rows)=>{
    const e=el('select',null,'form-control');e.setAttribute('aria-label',label);
    for(const [value,text] of rows){const option=el('option',text);option.value=value;e.append(option);}return e;
};
const ammoRecipeCategory=id=>({'recipe-v22':1,'recipe-v23':2,'recipe-v24':3,
    'recipe-v25':1,'recipe-v26':2,'recipe-v27':3,'recipe-v28':1,'recipe-v29':2,'recipe-v30':3})[id];
const fixedAmmoVariant=id=>['recipe-v22','recipe-v23','recipe-v24'].includes(id)?'handmade':
    ['recipe-v28','recipe-v29','recipe-v30'].includes(id)?'improved':null;
const recipeLabel=recipe=>{
    const fixed=fixedAmmoVariant(recipe.id),category=ammoRecipeCategory(recipe.id);
    return fixed?`${fixed==='handmade'?'Кустарные':'Улучшенные'} патроны категории ${category}`
        :recipe.output_count>1?recipe.output_name:recipe.name;
};

export async function showCrafting({characterId,lobbyId,onSaved,recipeId='',stationId,modificationTarget}){
    document.getElementById('crafting-modal')?.closeCrafting?.();
    const modal=el('div',null,'modal');modal.id='crafting-modal';modal.style.display='flex';
    const panel=el('div',null,'modal-content');panel.style.cssText='max-width:900px;max-height:90vh;overflow:hidden;display:flex;flex-direction:column';
    modal.append(panel);document.body.append(modal);
    const header=el('div',null,'block-header');header.append(el('h3','Создание, модификации и разбор'));
    const socket=window.socket;
    const handlers={};
    const close=()=>{
        document.removeEventListener('keydown',onKey);
        for(const [name,handler] of Object.entries(handlers))socket?.off?.(name,handler);
        modal.remove();
    };
    modal.closeCrafting=close;
    const onKey=event=>{if(event.key==='Escape')close();};document.addEventListener('keydown',onKey);
    header.append(button('×',close));panel.append(header);
    const body=el('div',null,'crafting-body');panel.append(body);
    let snapshot,busy=false,selectedRecipe=recipeId,searchValue='',generation=0,snapshotGeneration=0;
    let activeTab=modificationTarget?'modifications':'creation',selectedGroup='Все';
    const targetKey=entry=>String(entry.item.id || JSON.stringify(entry.path));
    const modSelection={target:modificationTarget && String(modificationTarget.id || JSON.stringify(modificationTarget.path))};
    const act=async(operation,payload={},targetId=characterId,revision=snapshot.revision)=>{
        if(busy)return;busy=true;
        body.querySelectorAll('button,select,input').forEach(e=>{e.disabled=true;});
        try{
            const response=await Server.craft(targetId,{operation,operation_id:crypto.randomUUID(),character_revision:revision,...payload});
            if(operation==='modify')activeTab='creation';
            if(targetId===characterId)await onSaved?.(response.data);
            await refresh();return response;
        }catch(error){showNotification(error.message,'system');await refresh();}
        finally{busy=false;}
    };
    const refresh=async()=>{
        const requested=++snapshotGeneration;
        const fresh=await Server.getCrafting(characterId);
        if(requested!==snapshotGeneration||!modal.isConnected)return;
        snapshot=fresh;
        body.replaceChildren();
        const toolbar=el('div',null,'crafting-toolbar');
        toolbar.append(button('Обновить',refresh));
        if(snapshot.is_gm)toolbar.append(button('Подтверждения условий сцены',showRequests));
        const job=snapshot.state.current;
        panel.classList.toggle('crafting-workshop',activeTab==='modifications');
        if(job&&activeTab==='modifications'){
            toolbar.append(button('К текущей работе',()=>{activeTab='creation';void refresh();}));
            body.append(toolbar);
            renderModifications(body,true);
            return;
        }
        body.append(toolbar);
        if(job){
            const minutes=Math.ceil(job.remaining_seconds/60);
            const status=job.phase==='preparing'
                ? `Подготовка · осталось ${minutes} мин. ${job.at_station?'Побудьте рядом с аппаратом.':'Вернитесь к аппарату, чтобы продолжить.'}`
                : job.phase==='running'?`Аппарат работает сам · осталось ${minutes} мин. Можно уйти.`
                : job.phase==='ready'?(job.at_station?'Партия готова.':'Партия готова. Вернитесь к аппарату за результатом.')
                : job.remaining_seconds>0?`Работа идёт. Осталось ${minutes} мин игрового времени.`:'Время работы прошло. Можно завершить создание.';
            body.append(el('h4',job.name),el('p',status));
            if(job.station_usage?.tool_use_saved)body.append(el('p','«Конструктор» сохранил заряд инструмента станка.'));
            if(job.kind==='book'){
                body.append(el('p','Чтение книги продолжается в КПК → Файлы.'));
                return;
            }
            if(job.kind==='salvage')body.append(el('p',`Разбор: к100 + ${job.bonus} по таблице добычи.`));
            else if(job.kind!=='book')body.append(el('p',`Итоговая СЛ: ${job.quote.difficulty}. ${job.quote.automatic_success?'Автоуспех.':'Успех: к100 строго выше СЛ.'}`));
            const choices=[];
            if(!job.quote.automatic_success&&!job.quote.failure_loss.all){
                for(let i=0;i<job.quote.failure_loss.chosen;i++){
                    body.append(el('label','Потерять при неудаче — на ваш выбор'));
                    const choice=select('Компонент при неудаче',job.escrow.map((e,index)=>[index,`${e.item.name} · зарезервировано ${e.amount}`]));body.append(choice);choices.push(choice);
                }
            }
            const finish=button(job.phase?'Забрать результат · к100':'Завершить · к100',()=>act('finish',{job_id:job.id,chosen_loss:choices.map(e=>Number(e.value))}));
            finish.disabled=job.remaining_seconds>0||Boolean(job.phase&&!job.at_station);body.append(finish);
            if(job.salvage_roll?.awaiting_gm){
                finish.disabled=true;
                body.append(el('p',`Результат ${job.salvage_roll.total}: в книге эта строка не заполнена. Предмет сохранён, нужен выбор ГМа.`));
                if(snapshot.is_gm){
                    const bands=snapshot.catalog.salvage[job.salvage_kind];
                    const ruling=select('Решение ГМа по разбору',bands.map((band,index)=>[index,`${band.maximum==null?'150+':'До '+band.maximum}: ${band.source_text}`]).filter(([index])=>bands[index].materials.length));
                    body.append(ruling,button('ГМ: применить строку таблицы',()=>act('finish',{job_id:job.id,salvage_band:Number(ruling.value)})));
                }
            }
            body.append(button('Отменить работу',async()=>{if(window.confirm('Отменить работу и вернуть зарезервированные компоненты?'))await act('cancel',{job_id:job.id});}));
            if(job.escrow.length)body.append(el('h4','Зарезервированные компоненты'));
            for(const entry of job.escrow)body.append(el('p',`${entry.item.name} × ${entry.amount}`));
            return;
        }
        const tabs=el('div',null,'crafting-tabs');tabs.setAttribute('role','tablist');body.append(tabs);
        const panes={creation:el('section',null,'crafting-pane'),modifications:el('section',null,'crafting-pane'),salvage:el('section',null,'crafting-pane'),other:el('section',null,'crafting-pane')};
        for(const [key,label] of [['creation','Создание'],['modifications','Модификации'],['salvage','Разбор'],['other','Другое']]){
            const tab=button(label,()=>{activeTab=key;activateTab();});tab.setAttribute('role','tab');tab.dataset.craftingTab=key;tabs.append(tab);
            panes[key].dataset.craftingPane=key;body.append(panes[key]);
        }
        const activateTab=()=>{
            panel.classList.toggle('crafting-workshop',activeTab==='modifications');
            for(const [key,pane] of Object.entries(panes)){pane.hidden=key!==activeTab;const tab=tabs.querySelector(`[data-crafting-tab="${key}"]`);tab.setAttribute('aria-selected',String(key===activeTab));tab.classList.toggle('is-active',key===activeTab);}
        };
        activateTab();
        const filter=el('input',null,'form-control');filter.type='search';filter.placeholder='Поиск рецепта';filter.setAttribute('aria-label','Поиск рецепта');filter.value=searchValue;panes.creation.append(filter);
        const recipes=select('Рецепт',snapshot.catalog.recipes.map(r=>[r.id,`${r.group} · ${recipeLabel(r)}`]));recipes.hidden=true;panes.creation.append(recipes);
        if(selectedRecipe&&snapshot.catalog.recipes.some(r=>r.id===selectedRecipe))recipes.value=selectedRecipe;
        const categories=el('div',null,'crafting-categories'),layout=el('div',null,'crafting-recipe-layout'),cards=el('div',null,'crafting-recipe-cards');
        const detail=el('div',null,'crafting-recipe-detail');layout.append(cards,detail);panes.creation.append(categories,layout);
        const groups=['Все',...new Set(snapshot.catalog.recipes.map(r=>r.group))];
        if(!groups.includes(selectedGroup))selectedGroup='Все';
        const paintCards=()=>{
            categories.replaceChildren();cards.replaceChildren();
            for(const group of groups){const category=button(group,()=>{selectedGroup=group;paintCards();});category.classList.toggle('is-active',selectedGroup===group);category.setAttribute('aria-pressed',String(selectedGroup===group));categories.append(category);}
            const matched=snapshot.catalog.recipes.filter(recipe=>(selectedGroup==='Все'||recipe.group===selectedGroup)&&
                `${recipeLabel(recipe)} ${recipe.group} ${recipe.tools_text}`.toLocaleLowerCase().includes(searchValue.toLocaleLowerCase()));
            for(const recipe of matched){
                const card=button('',()=>{recipes.value=recipe.id;void show();paintCards();});card.className='crafting-recipe-card';card.classList.toggle('is-selected',recipes.value===recipe.id);
                card.append(el('span','◈','crafting-recipe-icon'),el('strong',recipeLabel(recipe)),el('small',`${recipe.group} · ${recipe.duration_text}`));cards.append(card);
            }
            if(!matched.length)cards.append(el('p','Рецепты не найдены.'));
        };
        const show=async()=>{
            ++generation;selectedRecipe=recipes.value;detail.replaceChildren();
            const recipe=snapshot.catalog.recipes.find(r=>r.id===recipes.value);if(!recipe)return;
            detail.append(el('small',recipe.group),el('h4',recipeLabel(recipe)),el('p',`Время: ${recipe.duration_text}.`));
            const ammoCategory=ammoRecipeCategory(recipe.id),fixedVariant=fixedAmmoVariant(recipe.id);
            const ammoChoices=ammoCategory?snapshot.ammo_options.filter(option=>option.category===ammoCategory):[];
            const ammo=ammoCategory?select('Калибр патронов',ammoChoices.map(option=>[option.id,`${option.name} · ${option.price} ₽/шт.`])):null;
            const ammoVariant=ammoCategory?select('Вид патронов',[]):null;
            const fillAmmoVariants=()=>{
                if(!ammoVariant)return;
                const selected=ammoChoices.find(option=>option.id===Number(ammo.value));
                const previous=ammoVariant.value;ammoVariant.replaceChildren();
                const variants=fixedVariant?[[fixedVariant,fixedVariant==='handmade'?'Кустарные':'Улучшенные']]:[['base','Обычные'],['bp','БП'],['ep','ЭП']];
                for(const [kind,label] of variants)if(fixedVariant||kind==='base'||selected?.variants.includes(kind)){
                    const option=el('option',label);option.value=kind;ammoVariant.append(option);
                }
                if([...ammoVariant.options].some(option=>option.value===previous))ammoVariant.value=previous;
            };
            if(ammo){detail.append(el('label','Калибр'),ammo,el('label','Вид патронов'),ammoVariant);fillAmmoVariants();}
            const grenades=snapshot.inventory.filter(e=>e.item.category==='grenade'&&e.tripwire_compatible);
            const grenadeChoices=[];
            if(['recipe-m8','recipe-m9'].includes(recipe.id)){
                detail.append(el('p','Подствольные гранаты, ОГ-12, коктейль Молотова и кустарная бомба не подходят для растяжки.'));
                for(let i=0;i<(recipe.id==='recipe-m9'?2:1);i++){
                    const choice=select(`Граната ${i+1}`,grenades.map((entry,index)=>[index,`${entry.item.name} × ${entry.item.quantity||1}`]));
                    if(grenades[i])choice.value=String(i);
                    grenadeChoices.push(choice);detail.append(el('label',`Граната ${i+1}`),choice);
                }
            }
            const parts=recipe.tools_text.split(',').at(-1).split('/');
            const tool=select('Вариант приготовления',(ammoCategory===2?[65]:recipe.difficulty_options.length?recipe.difficulty_options:[0]).map((difficulty,i)=>[i,`${parts[i]?.trim()||'По рецепту'} · исходная СЛ ${difficulty}`]));
            if(recipe.difficulty_options.length>1)detail.append(tool);
            const approval=snapshot.state.tool_requests?.filter(r=>r.recipe_id===recipe.id&&r.environment&&r.status==='approved'&&!r.used).at(-1);
            const preview=el('div');detail.append(preview);
            const updateQuote=async()=>{
                const quoteGeneration=++generation;preview.replaceChildren(el('p','Считаем компоненты…'));
                try{
                    const ammoPayload=ammo?{ammo_template_id:Number(ammo.value),ammo_variant:ammoVariant.value}:{};
                    if(stationId)ammoPayload.station_id=stationId;
                    if(grenadeChoices.length)ammoPayload.grenade_paths=grenadeChoices.map(choice=>grenades[Number(choice.value)]?.path||[]);
                    const response=await Server.craft(characterId,{operation:'quote',recipe_id:recipe.id,tool_option:Number(tool.value),approval_id:approval?.id,...ammoPayload});
                    if(quoteGeneration!==generation||!modal.isConnected)return;
                    const plan=response.result;preview.replaceChildren(el('p',`Результат: ${plan.recipe.output_name} × ${plan.recipe.output_count}`),
                        el('p',`Итоговая СЛ ${plan.difficulty}${plan.automatic_success?' · автоуспех':''}`));
                    if(plan.station_name)preview.append(el('p',`Устройство: ${plan.station_name}${plan.spoil_chance?` · риск порчи результата ${plan.spoil_chance}%`:''}`));
                    if(approval)preview.append(el('p','ГМ подтвердил условие сцены. СЛ не меняется.'));
                    if(plan.constructor_materials)preview.append(el('p','«Конструктору» нужны два комплекта материалов до первого успеха.'));
                    let statusIndex=0;
                    for(const component of plan.recipe.components){
                        if(/^доступ к |^в округе должна быть мебель из дерева или готовые доски$|^трава$/i.test(component.name)){
                            preview.append(el('p',component.name));continue;
                        }
                        const status=plan.component_status?.[statusIndex++];
                        const options=status?.alternatives || component.alternatives || [component];
                        preview.append(el('p',options.map(option=>
                            `${option.name} ${option.available ?? 0}/${option.required ?? option.quantity}`).join(' или ')));
                    }
                    if(plan.extra_component_status?.length){
                        preview.append(el('h4','Дополнительный комплект'));
                        for(const status of plan.extra_component_status){
                            preview.append(el('p',status.alternatives.map(option=>
                                `${option.name} ${option.available}/${option.required}`).join(' или ')));
                        }
                    }
                    for(const missing of plan.other_missing || plan.missing)preview.append(el('p',missing,'error-message'));
                    const start=button(plan.preparation_seconds?'Начать подготовку':'Начать создание',()=>act('start',{recipe_id:recipe.id,tool_option:Number(tool.value),approval_id:approval?.id,...ammoPayload}));
                    start.disabled=plan.missing.length>0;preview.append(start);
                }catch(error){if(quoteGeneration===generation)preview.replaceChildren(el('p',error.message));}
            };
            tool.onchange=updateQuote;
            for(const choice of grenadeChoices)choice.onchange=updateQuote;
            if(ammo){ammo.onchange=()=>{fillAmmoVariants();void updateQuote();};ammoVariant.onchange=updateQuote;}
            await updateQuote();
            if(!modal.isConnected||selectedRecipe!==recipe.id)return;
            if(recipe.components.some(component=>/^доступ к |^в округе должна быть мебель из дерева или готовые доски$/i.test(component.name))){
                detail.append(el('h4','Условие сцены'),el('p','ГМ подтверждает доступ к указанному в рецепте условию. Это не меняет СЛ и действует для одной работы.'));
                detail.append(button('Попросить подтверждение ГМа',()=>act('request_tool',{recipe_id:recipe.id})));
                for(const request of snapshot.state.tool_requests||[])if(request.recipe_id===recipe.id&&request.environment)
                    detail.append(el('p',`Условие сцены: ${{pending:'ожидает ГМа',approved:'подтверждено',rejected:'отклонено'}[request.status]}${request.used?' · использовано':''}`));
            }
        };
        recipes.onchange=()=>{void show();paintCards();};
        filter.oninput=()=>{
            searchValue=filter.value;
            paintCards();
        };
        paintCards();
        await show();
        if(snapshot.targets.length)renderModifications(panes.modifications);
        else panes.modifications.append(el('p','Нет снаряжения для модификации.'));
        renderSalvage(panes.salvage);
        renderFlamethrowers(panes.other);
        if(snapshot.state.history?.length){panes.other.append(el('h4','Последние работы'));for(const result of snapshot.state.history.slice(-5).reverse())panes.other.append(el('p',`${result.name}: ${result.cancelled?'отменено':result.spoiled_by_station?'результат испорчен кустарным станком':result.success?'готово':'неудача'}${result.roll!=null?(result.kind==='salvage'?` · к100 ${result.roll} + ${result.bonus} = ${result.total} · ${result.materials.map(m=>`${m.name} ×${m.quantity}`).join(', ')||'без добычи'}`:` · к100 ${result.roll}, СЛ ${result.difficulty}`):''}`));}
    };
    const renderFlamethrowers=container=>{
        if(!snapshot.flamethrowers?.length)return;
        const section=el('details',null,'crafting-flamethrowers');section.open=true;section.append(el('summary','Огнемёты'));container.append(section);
        for(const entry of snapshot.flamethrowers){
            const weapon=entry.weapon,kind=weapon.attributes.flamethrower;
            section.append(el('h4',weapon.name),el('p',entry.resources.map((item,i)=>`${i?'Воздух':'Топливо'}: ${item?`${item.name} · ${item.uses??item.attributes?.uses_remaining??0}`:'не установлен'}`).join(' · ')));
            if(!snapshot.placement){section.append(el('p','Разместите персонажа на подлокации.'));continue;}
            for(const [i,mount] of ['fuel','air'].entries())if(entry.resources[i])section.append(button(i?'Снять воздушный баллон · 4 ОД':'Снять топливный бак · 8 ОД',async()=>{
                if(busy)return;busy=true;
                try{
                    const p=snapshot.placement,payload={location_character_id:p.actor_id,action_key:'flamethrower_service',weapon_index:entry.index,equipment_slot:mount,
                        equipment_operation:'detach',pending_action_id:crypto.randomUUID()};
                    const result=await Server.performLocationCombatAction(p.lobby_id,p.location_id,payload);
                    if(result.pending_action){const scene=await import('./locationScene.js');scene.registerDeferredCombatAction(result.pending_action_id,payload);}
                    const saved=await Server.getCharacter(characterId);await onSaved?.(saved.data);await refresh();
                }catch(error){showNotification(error.message,'system');}finally{busy=false;}
            }));
            const sources=snapshot.inventory.filter(e=>e.item.attributes?.flamethrower_air||e.item.attributes?.flamethrower_tank===kind);
            if(sources.length){
                const supply=select('Ёмкость огнемёта',sources.map((e,i)=>[i,`${e.item.name} · ${e.item.uses??e.item.attributes?.uses_remaining??0}`]));
                const mount=select('Крепление топливного бака',[['backpack','Вместо рюкзака'],['belt','Вместо пояса']]);
                section.append(supply);if(kind==='jet')section.append(mount);
                section.append(button('Заменить ёмкость · бак 8 ОД / воздух 4 ОД + доступ',async()=>{
                    if(busy)return;busy=true;
                    try{
                        const p=snapshot.placement,payload={location_character_id:p.actor_id,action_key:'flamethrower_service',weapon_index:entry.index,
                            item_path:sources[Number(supply.value)].path,equipment_slot:mount.value,pending_action_id:crypto.randomUUID()};
                        const result=await Server.performLocationCombatAction(p.lobby_id,p.location_id,payload);
                        if(result.pending_action){const scene=await import('./locationScene.js');scene.registerDeferredCombatAction(result.pending_action_id,payload);}
                        const saved=await Server.getCharacter(characterId);await onSaved?.(saved.data);await refresh();
                    }catch(error){showNotification(error.message,'system');}finally{busy=false;}
                }));
            }
            for(const mode of ['short','long'])section.append(button(mode==='short'?'Короткий залп · 3 ОД':`Длинный залп · ${kind==='jet'?5:4} ОД`,async()=>{
                if(window.locationCombatState?.status!=='active'){showNotification('Для залпа начните бой. Перед стрельбой возьмите огнемёт в руки.','system');return;}
                close();window.closeCharacterSheet?.();const scene=await import('./locationScene.js');
                scene.queueCombatActionFromSheet({actorCharacterId:characterId,actionKey:'flamethrower_fire',weaponIndex:entry.index,fireMode:mode,
                    targetType:kind==='reactive'&&mode==='short'?'character':'point',source:'sheet'});
            }));
        }
    };
    const renderSalvage=container=>{
        container.append(el('h4','Разбор предметов'));
        const candidates=snapshot.inventory.filter(e=>e.salvage_kind && (e.item.category!=='ammo'||Number(e.item.quantity)>=20));
        const tools=snapshot.inventory.filter(e=>e.salvage_tool_bonus>0);
        if(!candidates.length)container.append(el('p','Нет предметов для разбора. Подходят оружие, броня, гранаты, электроника и стопки от 20 одинаковых патронов.'));
        if(!candidates.length)return;
        container.append(el('p','Инструмент не обязателен, но даёт бонус к броску разбора.'));
        const section=el('div',null,'crafting-salvage');container.append(section);
        const target=select('Предмет для разбора',candidates.map((e,i)=>[i,e.item.name]));
        const tool=select('Инструмент для разбора',[['','Без инструмента'],...tools.map((e,i)=>[i,`${e.item.name} · +${e.salvage_tool_bonus}`])]);
        const amount=el('input',null,'form-control');amount.type='number';amount.min=1;amount.step=1;amount.value=1;amount.setAttribute('aria-label','Количество для разбора');
        const update=()=>{const item=candidates[Number(target.value)].item;amount.disabled=item.category!=='ammo';amount.value=item.category==='ammo'?Math.min(Number(item.quantity)||1,20):1;amount.max=item.quantity||1;};
        target.onchange=update;update();
        section.append(el('label','Предмет'),target,el('label','Инструмент (необязательно)'),tool,el('label','Количество (для патронов)'),amount,button('Начать разбор',async()=>{
            const payload={path:candidates[Number(target.value)].path,quantity:Number(amount.value)};
            if(tool.value!=='')payload.tool_path=tools[Number(tool.value)].path;
            try{
                const result=(await Server.craft(characterId,{operation:'quote_salvage',...payload})).result;
                if(window.confirm(`${result.name}: разбор займёт ${result.duration_seconds/60} мин. Бросок к100 + ${result.bonus}. После завершения предмет исчезнет. Начать?`))await act('salvage',payload);
            }catch(error){showNotification(error.message,'system');}
        }));
    };
    const renderModifications=(container,readOnly=false)=>{
        if(!snapshot.targets.length){container.append(el('p','Нет снаряжения для модификации.'));return;}
        const targets=select('Предмет для модификации',snapshot.targets.map(entry=>[targetKey(entry),entry.item.name||entry.item.model]));
        if(modSelection.target!=null){
            if(!snapshot.targets.some(entry=>targetKey(entry)===modSelection.target)){
                const missing=el('option','Для выбранного предмета нет схемы');missing.value='';missing.disabled=true;targets.prepend(missing);
                modSelection.target='';
            }
            targets.value=modSelection.target;
        }
        const label=el('label','Предмет для модификации','modification-target-label');label.append(targets);container.append(label);
        const form=el('div',null,'modification-workspace');container.append(form);let quoteVersion=0;
        const redraw=()=>{
            modSelection.target=targets.value;
            const target=snapshot.targets.find(entry=>targetKey(entry)===targets.value);
            form.replaceChildren();
            if(!target)return;
            const choices=snapshot.catalog.modifications.filter(r=>r.family===target.slots.family);
            const map=el('div',null,'modification-map'),sidebar=el('aside',null,'modification-inspector');
            const ruleInfo=el('div',null,'modification-inspector-info'),fields=el('div',null,'modification-inspector-fields');
            form.append(map,sidebar);sidebar.append(ruleInfo,fields);
            const toolName=choices[0]?.kind==='weapon'?/оружейник/i:/бронник/i;
            const tools=[...snapshot.inventory,...(snapshot.stations||[]).map(station=>({item:{name:station.name},stationId:station.id}))]
                .filter(entry=>toolName.test(entry.item.name||''));
            const tool=select('Инструменты для модификации',tools.length
                ? tools.map((entry,i)=>[i,entry.stationId?`${entry.item.name} · рядом · 1 заряд за работу`:`${entry.item.name} · ${entry.uses} зарядов`])
                : [['','Нет подходящих инструментов']]);
            if(tools[modSelection.tool])tool.value=modSelection.tool;
            const toolLabel=el('label','Набор инструментов или станок');toolLabel.append(tool);fields.append(toolLabel);
            const double=el('input');double.type='checkbox';double.checked=!!modSelection.double;
            const doubleLabel=el('label','20 зарядов вместо 10 · СЛ −25','modification-checkbox');doubleLabel.prepend(double);fields.append(doubleLabel);
            const replacement=select('Заменяемая модификация',[]);
            const replacementLabel=el('label','Замена');replacementLabel.append(replacement);fields.append(replacementLabel);
            const materialSection=el('details');materialSection.append(el('summary','Дополнительные материалы · −1 СЛ за 150 ₽'));
            const materialInputs=[];
            for(const entry of snapshot.inventory.filter(e=>e.item.category==='crafting_material')){
                const label=el('label',`${entry.item.name} · ${entry.item.price||0} ₽/шт.`),input=el('input',null,'form-control');
                input.type='number';input.min=0;input.max=entry.uses;input.step=1;input.value=0;
                input.setAttribute('aria-label','Потратить: '+entry.item.name);label.append(input);materialSection.append(label);materialInputs.push({entry,input});
            }
            fields.append(materialSection);
            const preview=el('div',null,'modification-preview');preview.setAttribute('role','status');sidebar.append(preview);
            let rule;
            const update=async()=>{
                const version=++quoteVersion;modSelection.tool=Number(tool.value);modSelection.double=double.checked;
                const selectedTool=tools[Number(tool.value)];
                double.disabled=Boolean(selectedTool?.stationId);
                if(double.disabled)double.checked=false;
                preview.replaceChildren();
                if(!rule||fields.hidden)return;
                materialSection.hidden=rule.kind==='weapon';
                preview.append(el('p','Рассчитываем установку…'));
                const payload={rule_id:rule.id,target_path:target.path,tool_path:selectedTool?.path,station_id:selectedTool?.stationId,
                    double_charges:double.checked,replace_rule_id:replacement.value||null,
                    material_contributions:rule.kind==='weapon'?[]:materialInputs.filter(r=>Number(r.input.value)>0).map(r=>({path:r.entry.path,quantity:Number(r.input.value)}))};
                try{
                    const response=await Server.craft(characterId,{operation:'quote_modification',...payload});
                    if(version!==quoteVersion||!preview.isConnected)return;
                    const plan=response.result;
                    preview.replaceChildren();
                    if(plan.station_name)preview.append(el('p',`${plan.station_name} · риск порчи результата ${plan.spoil_chance}%`));
                    if(plan.material_value)preview.append(el('p',`Материалы на ${plan.material_value} ₽ будут потрачены при начале установки.`));
                    preview.append(el('p',`Итоговая СЛ ${plan.difficulty} · ${plan.tool_uses===1?'1 заряд':`${plan.tool_uses} зарядов`} · ${plan.duration_seconds/60} мин`),el('p',`Установка у специалиста: ${plan.professional_price} ₽`),
                        button('Установить самостоятельно',()=>act('modify',payload)));
                }catch(error){if(version===quoteVersion)preview.replaceChildren(el('p',error.message));}
            };
            const unavailableReason=rule=>rule.scientist_only?'Только у учёного'
                :!rule.automatic_installation?'Установка недоступна':'';
            const selectRule=selected=>{
                rule=selected;modSelection.rule=rule?.id;
                const installed=(target.item.modifications||[]).filter(record=>record.ruleId);
                const owned=installed.find(record=>record.ruleId===rule?.id);
                const unavailable=rule&&unavailableReason(rule);
                fields.hidden=!rule||Boolean(owned)||readOnly||Boolean(unavailable);
                ruleInfo.replaceChildren();
                if(rule){
                    const title=modificationTitle(rule),description=owned?.description||rule.description;
                    ruleInfo.append(el('small',`${rule.branch||rule.family} · Уровень ${rule.level||1}`),el('h4',title));
                    if(description&&description!==title)ruleInfo.append(el('p',description));
                    if(owned)ruleInfo.append(el('p',owned.gunsmith?'Установлено · Оружейник ×1,5':'Установлено'));
                    else if(readOnly)ruleInfo.append(el('p','Завершите текущую работу, чтобы начать установку.'));
                    else if(unavailable)ruleInfo.append(el('p',unavailable));
                }
                const candidates=installed.filter(record=>record.level===(rule?.level||1));
                const none=el('option','Без замены');none.value='';replacement.replaceChildren(none);
                for(const record of candidates){const option=el('option',record.name||record.ruleId);option.value=record.ruleId;replacement.append(option);}
                replacementLabel.hidden=!candidates.length;
                if(rule?.kind==='weapon'&&candidates.length)replacement.value=candidates[0].ruleId;
                void update();
            };
            selectRule(renderModificationMap(map,{target,rules:choices,selectedId:modSelection.rule,onSelect:selectRule,unavailableReason}));
            tool.onchange=update;double.onchange=update;replacement.onchange=update;
            for(const row of materialInputs)row.input.onchange=update;
        };
        targets.onchange=redraw;redraw();
    };
    const showRequests=async()=>{
        const response=await Server.getCraftingRequests(lobbyId);body.replaceChildren(button('Назад',refresh),el('h4','Подтверждения условий сцены'));
        body.append(el('p','Подтверждение открывает рецепт с условием сцены для одной работы и не меняет СЛ.'));
        if(!response.requests.length)body.append(el('p','Ожидающих заявок нет.'));
        for(const request of response.requests){
            const row=el('div',null,'loadout-group');row.append(el('strong',request.character_name),el('p',`${request.recipe_name}: проверить условие сцены`));
            for(const [label,approved] of [['Подтвердить',true],['Отклонить',false]])row.append(button(label,async()=>{await act('approve_tool',{request_id:request.id,modifier:0,approved},request.character_id,request.revision);await showRequests();}));
            body.append(row);
        }
    };
    const refreshIfIdle=()=>{if(!modal.isConnected){close();return;}if(!busy)void refresh().catch(()=>{});};
    handlers.character_data_updated=event=>{if(Number(event?.character_id)===Number(characterId))refreshIfIdle();};
    handlers.lobby_time_updated=()=>{if(snapshot?.state.current?.phase)refreshIfIdle();};
    handlers.character_moved=event=>{
        if(Number(event?.character_id)===Number(characterId)&&snapshot?.state.current?.phase)refreshIfIdle();
    };
    handlers.location_object_updated=event=>{
        if(stationId===event?.object?.id)refreshIfIdle();
    };
    for(const [name,handler] of Object.entries(handlers))socket?.on?.(name,handler);
    try{await refresh();}catch(error){body.replaceChildren(el('p',error.message));}
}
