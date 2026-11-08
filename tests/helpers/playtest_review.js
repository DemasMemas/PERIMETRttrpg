// Выводит фактические записи сценариев; игровых формул здесь нет.
(()=>{
    const el=id=>document.getElementById(id),labels={tool:'Инструмент',quantity:'Количество',uses:'Осталось использований',
        maxUses:'Всего использований',weight:'Масса, кг',actionPoints:'ОД',mounted:'На креплениях',remaining:'Осталось в стопке',
        id:'Экземпляр',name:'Предмет',toolIds:'Экземпляры инструментов',equippedBelt:'Надетый пояс',storedBelt:'Снятый пояс',
        pocketQuantity:'В карманах',pockets:'Предметы в карманах',mount:'Крепление',occupiedHand:'В руке',weapon:'Оружие',gripHands:'Рук в хвате',
        durationSeconds:'Исходная длительность, с',remainingSeconds:'Осталось, с',water:'Вода',waterUses:'Использований воды',sugarUses:'Использований сахара',
        radiation:'Радиация',exhaustion:'Истощение',stress:'Стресс',headHits:'Попаданий в голову',damage:'Контрольный урон',armor:'Броня',
        distanceMeters:'Дистанция, м',shock:'Болевой шок',shockRoll:'Бросок шока',skippedTurns:'Пропущенных ходов ожидается',
        rollPenalty:'Штраф к броскам',painPerTurn:'Боль за ход',effects:'Эффекты',timeUnit:'Единица времени',
        painLevel:'Боль',turnSkipped:'Ход пропущен',currentActor:'Следующий ход'};
    let state=null,selected=null,step=0,poll=null,busy=false,lastKey='';
    const node=(tag,text,className)=>{const element=document.createElement(tag);if(text!=null)element.textContent=text;if(className)element.className=className;return element;};
    async function api(url,body){const response=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json','X-Review-Token':state.token},body:JSON.stringify(body)}:{});
        const value=await response.json();if(!response.ok)throw Error(value.error||'Не удалось выполнить действие');return value;}
    function valueNode(value){
        if(Array.isArray(value)){const list=node('ul');for(const item of value){const row=node('li');row.append(valueNode(item));list.append(row);}return list;}
        if(value&&typeof value==='object'){const facts=node('dl',null,'facts');for(const[key,valuePart]of Object.entries(value)){
            const row=node('div');row.append(node('dt',labels[key]||key),node('dd'));row.lastChild.append(valueNode(valuePart));facts.append(row);
        }return facts;}
        const friendly={hand:'В руке',belt:'На поясе',hour:'Часы',true:'Да',false:'Нет'};
        return node('span',value===null||value===undefined?'Не требуется':friendly[value]??String(value));
    }
    const entry=()=>state?.cases.find(c=>c.id===selected);
    function updateButtons(){const run=entry()?.run,canDecide=run?.current&&run?.cleaned&&run?.automaticResult==='passed'&&!busy;
        el('accept').disabled=el('rework').disabled=!canDecide;el('repeat').disabled=busy||state?.job?.status==='running';
        el('refresh').disabled=busy;el('exit').disabled=busy||state?.job?.status==='running';
    }
    function render(){
        const list=el('cases');list.replaceChildren();
        for(const item of state.cases){const button=node('button',item.title);button.setAttribute('aria-current',String(item.id===selected));
            const status=!item.run?'Не запускалось':!item.run.current?'Требует повторной проверки':!item.run.cleaned?'Стенд ещё не закрыт':
                item.run.decision?.status==='accepted'?'Принят автором':item.run.decision?.status==='rework'?'На доработку':'Ожидает вашего решения';
            button.append(node('small',status));button.onclick=()=>{selected=item.id;step=0;lastKey='';el('comment').value='';render();};list.append(button);
        }
        const item=entry();if(!item)return;const run=item.run;
        el('title').textContent=item.title;el('source').textContent=item.source;
        el('repeat').textContent=run?'Повторить проверку':'Запустить проверку';
        el('status').textContent=!run?'Пока нет фактического результата. Запуск подготовит отдельную базу и собственный Chrome.':
            !run.current?'Игровой код или сценарий изменился. Прежнее решение сохранено в истории; требуется новый прогон.':
                !run.cleaned?'Сценарий записан, ожидается подтверждение закрытия тестового стенда.':'Автоматические проверки прошли. Ручное решение ещё за вами.';
        el('decision-status').textContent=run?.decision?(run.decision.status==='accepted'?'Принят':'Возвращён на доработку')+
            ' · '+new Date(run.decision.decidedAt).toLocaleString('ru-RU')+(run.current?'':' · устаревший результат'):'';
        const content=el('content');content.replaceChildren();
        const rule=node('section',null,'block');rule.append(node('h3','Правило'),node('p',item.rule,'rule'));
        const link=node('a','Карточка задачи');link.href=item.card;link.target='_blank';link.rel='noopener';rule.append(link);content.append(rule);
        const expected=node('section',null,'block');expected.append(node('h3','Ожидаемый результат'));const expectedList=node('ul',null,'expected');
        for(const text of item.expected)expectedList.append(node('li',text));expected.append(expectedList);
        if(run){
            const controls=node('section',null,'block');controls.append(node('h3','Условия проверки'),node('p',run.scene+' · '+run.roles.join(', ')));
            const settings=node('div',null,'controls');settings.append(node('p','Броски: '+(run.controls?.random||'Не указано')),
                node('p','Время: '+(run.controls?.time||'Не указано')));controls.append(settings);
            step=Math.min(step,run.steps.length-1);const current=run.steps[step],section=node('section',null,'block');
            const heading=node('div',null,'steps-heading');heading.append(node('h3',`Шаг ${step+1} из ${run.steps.length}: ${current.title}`));
            const buttons=node('div',null,'step-buttons');for(const[title,delta]of [['Назад',-1],['Далее',1]]){
                const button=node('button',title);button.disabled=step+delta<0||step+delta>=run.steps.length;button.onclick=()=>{step+=delta;render();};buttons.append(button);
            }heading.append(buttons);section.append(heading);const comparison=node('div',null,'comparison');
            for(const[title,key]of [['До','before'],['После','after']]){const panel=node('section');panel.append(node('h3',title),valueNode(current[key]));comparison.append(panel);}
            section.append(comparison);content.append(section,expected,controls);
            if(run.images.length){const gallery=node('section',null,'block');gallery.append(node('h3','Настоящий игровой интерфейс'));
                const grid=node('div',null,'gallery');for(const[name,index]of run.images.map((name,index)=>[name,index])){
                    const link=node('a'),image=node('img');link.href=`/image/${run.runId}/${name}`;link.target='_blank';link.rel='noopener';
                    image.src=link.href;image.alt='Игровой снимок '+(index+1);image.loading='lazy';link.append(image,node('span','Открыть снимок '+(index+1)));grid.append(link);
                }gallery.append(grid);content.append(gallery);}
            const limitations=node('section',null,'block');limitations.append(node('h3','Границы этой проверки'));
            const notes=node('ul',null,'limitations');for(const text of run.limitations)notes.append(node('li',text));limitations.append(notes);
            const details=node('details');details.append(node('summary','Версия и журнал'));
            details.append(node('p',`Сценарий ${run.caseVersion}; код ${run.codeVersion.slice(0,12)}; запуск ${run.runId}`),
                node('p','Записано '+new Date(run.recordedAt).toLocaleString('ru-RU')));
            if(run.decision?.comment)details.append(node('p','Замечание: '+run.decision.comment));limitations.append(details);content.append(limitations);
        }else content.append(expected);
        updateButtons();
    }
    function jobText(){const job=state?.job;el('job').textContent=job?.status==='running'?'Идёт отдельный прогон…':
        job?.status==='failed'?'Проверка не завершена: '+job.error:job?.status==='finished'?'Прогон завершён, стенд закрыт':'';}
    async function refresh(){
        state=await api('/api/state');selected=selected||state.cases.find(c=>c.run)?.id||state.cases[0]?.id;jobText();
        const run=entry()?.run,key=selected+':'+run?.runId+':'+run?.current+':'+run?.cleaned+':'+run?.decision?.id;
        if(key!==lastKey){lastKey=key;render();}else updateButtons();
        clearTimeout(poll);if(state.job?.status==='running')poll=setTimeout(()=>refresh().catch(showError),2000);
    }
    function showError(error){el('error').textContent=error.message;}
    async function act(action){
        if(busy)return;busy=true;el('error').textContent='';updateButtons();
        try{await action();lastKey='';await refresh();}catch(error){showError(error);}finally{busy=false;updateButtons();}
    }
    el('refresh').onclick=()=>act(()=>Promise.resolve());
    el('repeat').onclick=()=>act(()=>api('/api/repeat',{caseId:selected}));
    el('exit').onclick=async()=>{
        if(busy)return;busy=true;updateButtons();
        try{await api('/api/shutdown',{});clearTimeout(poll);
            document.body.replaceChildren(node('p','Плейтест закрыт. Эту вкладку можно закрыть.'));
        }catch(error){showError(error);busy=false;updateButtons();}
    };
    async function decide(status){const run=entry()?.run;if(!run)return;
        await api('/api/decision',{caseId:selected,runId:run.runId,caseVersion:run.caseVersion,codeVersion:run.codeVersion,status,comment:el('comment').value});}
    el('accept').onclick=()=>act(()=>decide('accepted'));el('rework').onclick=()=>act(()=>decide('rework'));
    refresh().catch(showError);
})();
