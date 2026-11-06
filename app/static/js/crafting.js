import {Server} from './api.js';
import {showNotification} from './utils.js';

const el=(tag,text,css)=>{const e=document.createElement(tag);if(text!=null)e.textContent=text;if(css)e.className=css;return e;};
const button=(text,action)=>{const b=el('button',text,'btn btn-secondary');b.type='button';b.onclick=action;return b;};
const select=(label,rows)=>{
    const e=el('select',null,'form-control');e.setAttribute('aria-label',label);
    for(const [value,text] of rows){const option=el('option',text);option.value=value;e.append(option);}return e;
};

export async function showCrafting({characterId,lobbyId,onSaved}){
    document.getElementById('crafting-modal')?.remove();
    const modal=el('div',null,'modal');modal.id='crafting-modal';modal.style.display='flex';
    const panel=el('div',null,'modal-content');panel.style.cssText='max-width:900px;max-height:90vh;overflow:auto';
    modal.append(panel);document.body.append(modal);
    const header=el('div',null,'block-header');header.append(el('h3','Создание и модификации'));
    const close=()=>{document.removeEventListener('keydown',onKey);modal.remove();};
    const onKey=event=>{if(event.key==='Escape')close();};document.addEventListener('keydown',onKey);
    header.append(button('×',close));panel.append(header);
    const body=el('div');panel.append(body);
    let snapshot,busy=false,selectedRecipe='',searchValue='',generation=0;
    const modSelection={};
    const act=async(operation,payload={},targetId=characterId,revision=snapshot.revision)=>{
        if(busy)return;busy=true;
        body.querySelectorAll('button,select,input').forEach(e=>{e.disabled=true;});
        try{
            const response=await Server.craft(targetId,{operation,operation_id:crypto.randomUUID(),character_revision:revision,...payload});
            if(targetId===characterId)await onSaved?.(response.data);
            await refresh();return response;
        }catch(error){showNotification(error.message,'system');await refresh();}
        finally{busy=false;}
    };
    const refresh=async()=>{
        snapshot=await Server.getCrafting(characterId);if(!modal.isConnected)return;
        body.replaceChildren();
        body.append(button('Обновить',refresh));
        if(snapshot.is_gm)body.append(button('Заявки на инструменты',showRequests));
        const job=snapshot.state.current;
        if(job){
            body.append(el('h4',job.name),el('p',job.remaining_seconds>0?`Работа идёт. Осталось ${Math.ceil(job.remaining_seconds/60)} мин игрового времени.`:'Время работы прошло. Можно завершить создание.'));
            if(job.kind==='salvage')body.append(el('p',`Разбор: к100 + ${job.bonus} по таблице добычи.`));
            else if(job.kind!=='book')body.append(el('p',`Итоговая СЛ: ${job.quote.difficulty}. ${job.quote.automatic_success?'Автоуспех.':'Успех: к100 строго выше СЛ.'}`));
            const choices=[];
            if(!job.quote.automatic_success&&!job.quote.failure_loss.all){
                for(let i=0;i<job.quote.failure_loss.chosen;i++){
                    body.append(el('label','Потерять при неудаче — на ваш выбор'));
                    const choice=select('Компонент при неудаче',job.escrow.map((e,index)=>[index,`${e.item.name} · зарезервировано ${e.amount}`]));body.append(choice);choices.push(choice);
                }
            }
            const finish=button(job.kind==='book'?'Завершить чтение':'Завершить · к100',()=>act('finish',{job_id:job.id,chosen_loss:choices.map(e=>Number(e.value))}));
            finish.disabled=job.remaining_seconds>0;body.append(finish);
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
        const filter=el('input',null,'form-control');filter.type='search';filter.placeholder='Поиск рецепта';filter.value=searchValue;body.append(filter);
        const recipes=select('Рецепт',snapshot.catalog.recipes.map(r=>[r.id,`${r.group} · ${r.name}`]));body.append(recipes);
        if(selectedRecipe&&snapshot.catalog.recipes.some(r=>r.id===selectedRecipe))recipes.value=selectedRecipe;
        const detail=el('div');body.append(detail);
        const show=async()=>{
            ++generation;selectedRecipe=recipes.value;detail.replaceChildren();
            const recipe=snapshot.catalog.recipes.find(r=>r.id===recipes.value);if(!recipe)return;
            detail.append(el('h4',recipe.name),el('p',`Время: ${recipe.duration_text}. Нужно: ${recipe.tools_text}.`));
            const parts=recipe.tools_text.split(',').at(-1).split('/');
            const tool=select('Вариант приготовления',(recipe.difficulty_options.length?recipe.difficulty_options:[0]).map((difficulty,i)=>[i,`${parts[i]?.trim()||'По рецепту'} · исходная СЛ ${difficulty}`]));
            if(recipe.difficulty_options.length>1)detail.append(tool);
            const approval=snapshot.state.tool_requests?.filter(r=>r.recipe_id===recipe.id&&r.status==='approved'&&!r.used).at(-1);
            const preview=el('div');detail.append(preview);
            const updateQuote=async()=>{
                const quoteGeneration=++generation;preview.replaceChildren(el('p','Считаем компоненты…'));
                try{
                    const response=await Server.craft(characterId,{operation:'quote',recipe_id:recipe.id,tool_option:Number(tool.value),approval_id:approval?.id});
                    if(quoteGeneration!==generation||!modal.isConnected)return;
                    const plan=response.result;preview.replaceChildren(el('p',`Итоговая СЛ ${plan.difficulty}${plan.automatic_success?' · автоуспех':''}`));
                    if(approval)preview.append(el('p',`ГМ одобрил: ${approval.tool_name||'условия'} · СЛ ${approval.modifier}`));
                    let statusIndex=0;
                    for(const component of recipe.components){
                        if(component.name.toLocaleLowerCase().startsWith('доступ к ')){
                            preview.append(el('p',component.name));continue;
                        }
                        const status=plan.component_status?.[statusIndex++];
                        const options=status?.alternatives || component.alternatives || [component];
                        preview.append(el('p',options.map(option=>
                            `${option.name} ${option.available ?? 0}/${option.required ?? option.quantity}`).join(' или ')));
                    }
                    for(const missing of plan.other_missing || plan.missing)preview.append(el('p',missing,'error-message'));
                    const start=button('Начать создание',()=>act('start',{recipe_id:recipe.id,tool_option:Number(tool.value),approval_id:approval?.id}));
                    start.disabled=plan.missing.length>0;preview.append(start);
                }catch(error){if(quoteGeneration===generation)preview.replaceChildren(el('p',error.message));}
            };
            tool.onchange=updateQuote;await updateQuote();
            if(!modal.isConnected||selectedRecipe!==recipe.id)return;
            const extra=select('Дополнительный инструмент',[['','Без дополнительного инструмента'],...snapshot.inventory.map((e,i)=>[i,e.item.name])]);
            detail.append(el('h4','Дополнительный инструмент'),extra);
            detail.append(button('Запросить одобрение ГМа',()=>act('request_tool',{recipe_id:recipe.id,tool_path:extra.value===''?null:snapshot.inventory[Number(extra.value)].path})));
            for(const request of snapshot.state.tool_requests||[])if(request.recipe_id===recipe.id)detail.append(el('p',`${request.tool_name||'Условия создания'}: ${{pending:'ожидает ГМа',approved:'одобрено',rejected:'отклонено'}[request.status]}${request.used?' · использовано':''}`));
        };
        recipes.onchange=show;
        filter.oninput=()=>{
            searchValue=filter.value;
            for(const option of recipes.options)option.hidden=!option.textContent.toLocaleLowerCase().includes(searchValue.toLocaleLowerCase());
            if(recipes.selectedOptions[0]?.hidden){const first=[...recipes.options].find(o=>!o.hidden);if(first){recipes.value=first.value;void show();}}
        };
        await show();
        if(snapshot.targets.length)renderModifications();
        renderSalvage();
        renderFlamethrowers();
        const books=snapshot.inventory.filter(e=>e.item.attributes?.book_kind);
        if(books.length){
            body.append(el('h4','Электронные книги'));
            const book=select('Книга для чтения',books.map((entry,i)=>[i,entry.item.name]));body.append(book);
            const skill=select('Навык книги',[]);body.append(skill);
            const update=()=>{
                const entry=books[Number(book.value)],attrs=entry.item.attributes;
                skill.replaceChildren();skill.hidden=attrs.book_kind!=='skill';
                const subject=String(attrs.book_subject).toLowerCase();
                const group=subject.includes('физическ')?'physical':subject.includes('социальн')?'social':'other';
                for(const item of snapshot.skills.filter(s=>s.path.startsWith(group+'.'))){const option=el('option',item.label);option.value=item.path;skill.append(option);}
                const assigned=snapshot.state.book_assignments?.[entry.item.id];if(assigned){skill.value=assigned;skill.disabled=true;}else skill.disabled=false;
            };
            book.onchange=update;update();
            body.append(button('Читать · 4 часа',()=>act('read_book',{path:books[Number(book.value)].path,skill_path:skill.hidden?null:skill.value})));
        }
        if(snapshot.state.library?.length){body.append(el('h4','Прочитано'));for(const book of snapshot.state.library)body.append(el('p',`${book.name}${book.skill_path?' · '+(snapshot.skills.find(s=>s.path===book.skill_path)?.label||''):''}`));}
        if(snapshot.state.history?.length){body.append(el('h4','Последние работы'));for(const result of snapshot.state.history.slice(-5).reverse())body.append(el('p',`${result.name}: ${result.cancelled?'отменено':result.success?'готово':'неудача'}${result.roll!=null?(result.kind==='salvage'?` · к100 ${result.roll} + ${result.bonus} = ${result.total} · ${result.materials.map(m=>`${m.name} ×${m.quantity}`).join(', ')||'без добычи'}`:` · к100 ${result.roll}, СЛ ${result.difficulty}`):''}`));}
    };
    const renderFlamethrowers=()=>{
        if(!snapshot.flamethrowers?.length)return;
        const section=el('details',null,'crafting-flamethrowers');section.open=true;section.append(el('summary','Огнемёты'));body.append(section);
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
    const renderSalvage=()=>{
        const candidates=snapshot.inventory.filter(e=>e.salvage_kind);
        const tools=snapshot.inventory.filter(e=>e.salvage_tool_bonus>0);
        if(!candidates.length||!tools.length)return;
        const section=el('details',null,'crafting-salvage');section.append(el('summary','Разобрать предмет'));body.append(section);
        const target=select('Предмет для разбора',candidates.map((e,i)=>[i,e.item.name]));
        const tool=select('Один инструмент',tools.map((e,i)=>[i,e.item.name]));
        const amount=el('input',null,'form-control');amount.type='number';amount.min=1;amount.step=1;amount.value=1;amount.setAttribute('aria-label','Количество для разбора');
        const update=()=>{const item=candidates[Number(target.value)].item;amount.disabled=item.category!=='ammo';amount.value=item.category==='ammo'?Math.min(Number(item.quantity)||1,20):1;amount.max=item.quantity||1;};
        target.onchange=update;update();
        section.append(target,tool,amount,button('Начать разбор',async()=>{
            const payload={path:candidates[Number(target.value)].path,tool_path:tools[Number(tool.value)].path,quantity:Number(amount.value)};
            try{
                const result=(await Server.craft(characterId,{operation:'quote_salvage',...payload})).result;
                if(window.confirm(`${result.name}: разбор займёт ${result.duration_seconds/60} мин. Бросок к100 + ${result.bonus}. После завершения предмет исчезнет. Начать?`))await act('salvage',payload);
            }catch(error){showNotification(error.message,'system');}
        }));
    };
    const renderModifications=()=>{
        const section=el('details');section.append(el('summary','Модификации снаряжения'));body.append(section);
        const root=el('div');section.append(root);
        const targets=select('Предмет для модификации',snapshot.targets.map((entry,i)=>[i,`${entry.item.name||entry.item.model} · ${entry.slots.used}/${entry.slots.available} слотов`]));
        if(modSelection.target!=null&&snapshot.targets[modSelection.target])targets.value=modSelection.target;
        root.append(targets);const form=el('div');root.append(form);let quoteVersion=0;
        const redraw=()=>{
            modSelection.target=Number(targets.value);const target=snapshot.targets[modSelection.target];form.replaceChildren();
            const choices=snapshot.catalog.modifications.filter(r=>r.family===target.slots.family);
            const rules=select('Модификация',choices.map(r=>[r.id,`Ур. ${r.level||1} · ${r.name||r.description}`]));
            if(choices.some(r=>r.id===modSelection.rule))rules.value=modSelection.rule;
            form.append(rules,el('p',`Открыто слотов: ${target.slots.available} / ${target.slots.maximum}`));
            const tool=select('Инструменты для модификации',snapshot.inventory.map((entry,i)=>[i,`${entry.item.name} · ${entry.item.uses??entry.item.maxUses??entry.item.attributes?.uses??'—'} зарядов`]));
            if(snapshot.inventory[modSelection.tool])tool.value=modSelection.tool;
            form.append(el('label','Набор инструментов или станок'),tool);
            const double=el('input');double.type='checkbox';double.checked=!!modSelection.double;
            const doubleLabel=el('label','20 зарядов вместо 10: СЛ −25 ');doubleLabel.prepend(double);form.append(doubleLabel);
            const replacement=select('Заменяемая модификация',[]);form.append(replacement);
            const materialSection=el('details');materialSection.append(el('summary','Дополнительные материалы · −1 СЛ за 150 ₽'));
            const materialInputs=[];
            for(const entry of snapshot.inventory.filter(e=>e.item.category==='crafting_material')){
                const label=el('label',`${entry.item.name} · ${entry.item.price||0} ₽/шт.`),input=el('input',null,'form-control');
                input.type='number';input.min=0;input.max=entry.uses;input.step=1;input.value=0;
                input.setAttribute('aria-label','Потратить: '+entry.item.name);label.append(input);materialSection.append(label);materialInputs.push({entry,input});
            }
            form.append(materialSection);
            const preview=el('div');form.append(preview);
            const update=async()=>{
                const version=++quoteVersion;modSelection.rule=rules.value;modSelection.tool=Number(tool.value);modSelection.double=double.checked;
                const rule=choices.find(r=>r.id===rules.value);if(!rule)return;
                materialSection.hidden=rule.kind==='weapon';
                const selectedReplacement=replacement.value;
                replacement.replaceChildren();const none=el('option','Новая модификация');none.value='';replacement.append(none);
                for(const record of target.item.modifications||[])if(record.ruleId&&record.level===(rule.level||1)){const option=el('option','Заменить: '+record.name);option.value=record.ruleId;replacement.append(option);}
                if([...replacement.options].some(o=>o.value===selectedReplacement))replacement.value=selectedReplacement;
                preview.replaceChildren(el('p',rule.description));
                if(!rule.automatic_installation){preview.append(el('p','Автоматическая установка этого улучшения пока недоступна.'));return;}
                const approval=snapshot.state.tool_requests?.filter(r=>r.recipe_id===rule.id&&r.status==='approved'&&!r.used).at(-1);
                const payload={rule_id:rule.id,target_path:target.path,tool_path:snapshot.inventory[Number(tool.value)]?.path,
                    double_charges:double.checked,replace_rule_id:replacement.value||null,approval_id:approval?.id,
                    material_contributions:rule.kind==='weapon'?[]:materialInputs.filter(r=>Number(r.input.value)>0).map(r=>({path:r.entry.path,quantity:Number(r.input.value)}))};
                try{
                    const response=await Server.craft(characterId,{operation:'quote_modification',...payload});
                    if(version!==quoteVersion||!modal.isConnected)return;
                    const plan=response.result;
                    if(plan.material_value)preview.append(el('p',`Материалы на ${plan.material_value} ₽ будут потрачены при начале установки.`));
                    preview.append(el('p',`Итоговая СЛ ${plan.difficulty} · ${plan.tool_uses} зарядов · ${plan.duration_seconds/60} мин`),el('p',`Установка у специалиста: ${plan.professional_price} ₽`),
                        button('Установить самостоятельно',()=>act('modify',payload)));
                }catch(error){if(version===quoteVersion)preview.append(el('p',error.message));}
            };
            rules.onchange=update;tool.onchange=update;double.onchange=update;replacement.onchange=update;void update();
            for(const row of materialInputs)row.input.onchange=update;
            const extra=select('Дополнительный инструмент для улучшения',[['','Выберите инструмент'],...snapshot.inventory.map((e,i)=>[i,e.item.name])]);form.append(extra);
            form.append(button('Запросить инструмент у ГМа',()=>act('request_tool',{recipe_id:rules.value,tool_path:extra.value===''?null:snapshot.inventory[Number(extra.value)].path})));
        };
        targets.onchange=redraw;redraw();
    };
    const showRequests=async()=>{
        const response=await Server.getCraftingRequests(lobbyId);body.replaceChildren(button('Назад',refresh),el('h4','Заявки на инструменты'));
        if(!response.requests.length)body.append(el('p','Ожидающих заявок нет.'));
        for(const request of response.requests){
            const row=el('div',null,'loadout-group');row.append(el('strong',request.character_name),el('p',`${request.recipe_name}: ${request.tool_name||'Подтвердить условия рецепта'}`));
            const modifier=select('Снижение СЛ',request.tool_id?Array.from({length:11},(_,i)=>[-5-i,`СЛ −${5+i}`]):[[0,'Условия выполнены']]);row.append(modifier);
            for(const [label,approved] of [['Одобрить',true],['Отклонить',false]])row.append(button(label,async()=>{await act('approve_tool',{request_id:request.id,modifier:Number(modifier.value),approved},request.character_id,request.revision);await showRequests();}));
            body.append(row);
        }
    };
    try{await refresh();}catch(error){body.replaceChildren(el('p',error.message));}
}
