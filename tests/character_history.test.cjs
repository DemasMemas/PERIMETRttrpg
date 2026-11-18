const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');

test('история Зоны скрывает номера бросков и показывает прибавку рядом с исходным доходом',async()=>{
    const module=new vm.SourceTextModule(fs.readFileSync(path.join(__dirname,'../app/static/js/characterHistory.js'),'utf8'));
    await module.link(specifier=>{
        const values=specifier.includes('characterTraits')?{ruleText:value=>String(value??'').trim()}
            :specifier.includes('characterCreation')?{traitChoiceFields:()=>''}:{Server:{}};
        return new vm.SyntheticModule(Object.keys(values),function(){for(const [key,value] of Object.entries(values))this.setExport(key,value);});
    });
    await module.evaluate();
    const view=module.namespace.historyMonthPresentation({title:'8. Редкий артефакт!',
        details:['4. +25000'],cashDelta:25000,incomeMultiplier:1,incomeOptions:[27500]},
        {options:[2500]});
    assert.equal(view.title,'Редкий артефакт!');
    assert.deepEqual(view.details,[]);
    assert.equal(view.incomeText,'2 500 ₽ + 25 000 ₽ = 27 500 ₽');
    const legacyEvent={month:2,title:'2. Зараза...',details:[
        '2. Болезнь. Бросьте 1к4 для определения вида',
        '3. Травма. Бросьте 1к4 для определения вида',
        '2. Заражение крови. -75% от заработка в месяц',
    ],incomeOptions:[625]};
    const legacy=module.namespace.renderHistoryMonth(legacyEvent,{options:[2500]},9);
    assert.match(legacy,/<p>Месяц 2 из 9<\/p>/);
    assert.match(legacy,/<p>Заражение крови. -75% от заработка в месяц<\/p>/);
    assert.doesNotMatch(legacy,/Бросьте|1к4/);
    const debt=module.namespace.renderHistoryMonth({month:1,title:'Должник',incomeOptions:[2500],
        debt:{creditor:'Наёмники',amount:10000,paidAtCreation:7000,remaining:3000}});
    assert.match(debt,/<dt>Группировка-кредитор<\/dt><dd>Наёмники<\/dd>/);
    assert.match(debt,/<dt>Сумма долга<\/dt><dd>10 000 ₽<\/dd>/);
    assert.match(debt,/<dt>Списано при создании<\/dt><dd>7 000 ₽<\/dd>/);
    assert.match(debt,/<dt>Остаток долга<\/dt><dd>3 000 ₽<\/dd>/);
    assert.match(debt,/<span>Доход за месяц<\/span><strong>2 500 ₽<\/strong>/);

    const sheet=fs.readFileSync(path.join(__dirname,'../app/static/js/characterSheet.js'),'utf8');
    const start=sheet.indexOf('function renderCreationHistory(');
    const end=sheet.indexOf('\n}',start)+2;
    const context=vm.createContext({
        renderHistoryMonth:module.namespace.renderHistoryMonth,escapeHtml:value=>String(value),
        window:{isGM:false},
    });
    vm.runInContext(sheet.slice(start,end),context);
    const history=context.renderCreationHistory({settled:true,events:[{
        month:1,title:'8. Редкий артефакт!',details:['4. Найден артефакт'],incomeOptions:[27500],
    },legacyEvent]});
    assert.match(history,/<p>Месяц 1<\/p>\s*<h4>Редкий артефакт!<\/h4>/);
    assert.match(history,/Найден артефакт/);
    assert.doesNotMatch(history,/8\. |4\. |Бросьте|1к4/);
});
