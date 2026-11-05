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
    assert.equal(view.incomeText,'Доход за месяц: 2 500 ₽ + 25 000 ₽ = 27 500 ₽');
});
