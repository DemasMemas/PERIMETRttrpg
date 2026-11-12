const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
async function load(name){
    const context=vm.createContext({console}),cache=new Map();
    async function moduleAt(file){
        if(cache.has(file))return cache.get(file);
        const module=new vm.SourceTextModule(fs.readFileSync(file,'utf8'),{context,identifier:file});cache.set(file,module);
        await module.link((specifier,parent)=>moduleAt(path.resolve(path.dirname(parent.identifier),specifier)));
        return module;
    }
    const module=await moduleAt(path.resolve(__dirname,'../app/static/js/',name));await module.evaluate();return module.namespace;
}
test('состояние питания различает разряд, погасший источник и новый ХИС', async () => {
    const { itemPowerWarning } = await load('itemPresentation.js');
    assert.equal(itemPowerWarning({ lightPower: { uses_left: 0 } }), 'Разряжен');
    assert.equal(itemPowerWarning({ pdaState: { charge_percent: 0 } }), 'Разряжен');
    assert.equal(itemPowerWarning({ deviceState: { remaining_seconds: 0 } }), 'Разряжен');
    assert.equal(itemPowerWarning({ glowState: { activated: true, remaining_seconds: 0 } }), 'Свечение погасло');
    assert.equal(itemPowerWarning({ name: 'ХИС', glowState: { activated: false, remaining_seconds: 0 } }), '');
    assert.equal(itemPowerWarning({ lightPower: { uses_left: 4 } }), '');
});
test('Морфин не сохраняет долг боли, а Воля сохраняет',async()=>{
    const {applyEffectToHealth}=await load('effects.js');
    for(const [type,debt] of [['analgesia',undefined],['pain_block',3]]){
        const health={painLevel:0,effects:[{type,blocks_new_pain:true,remaining:3,active:true}],combatMeta:{}};
        applyEffectToHealth(health,{type:'pain',value:3});
        assert.equal(health.painLevel,0);assert.equal(health.combatMeta.blockedPain,debt);
    }
});
test('Гематоген в браузере снижает тяжесть без повторного уменьшения сложности',async()=>{
    const {syncHealthDerivedStatuses}=await load('effects.js');
    const health={effects:[{type:'bleeding_external_severe',area:'leftArm'}],combatMeta:{bleedingModifiers:[{value:-2,note:'hematogen',scope:'combat'}]}};
    syncHealthDerivedStatuses(health);
    assert.equal(health.bleeding.totalSeverity,3);assert.equal(health.bleeding.rawSeverity,5);
    assert.equal(health.bleeding.modifierTotal,0);assert.equal(health.bleeding.difficulty,8);
    health.effects=[{type:'bleeding_external_light'}];syncHealthDerivedStatuses(health);
    assert.equal(health.bleeding.totalSeverity,0);assert.equal(health.bleeding.difficulty,5);
});
test('Подсказка берёт текущие значения экземпляра, полную защиту и модули',async()=>{
    const {itemTooltipModel}=await load('itemPresentation.js');
    const result=itemTooltipModel({name:'Шлем',category:'helmet',attributes:{max_durability:100,uses:5,protection:{physical:.5,thermal:.4,chemical:.3,electric:.2,radiation:.1}}},
        {durability:17,maxDurability:80,uses:2,maxUses:5,protection:{physical:.25,thermal:.4,chemical:.3,electric:.2,radiation:.1},installedModules:[{name:'Забрало'}]});
    assert.ok(result.metrics.includes('Прочность: 17 / 80'));
    assert.ok(result.metrics.includes('Использований: 2 / 5'));
    assert.equal(result.metrics.filter(t=>t.includes('защита:')).length,5);
    assert.ok(result.metrics.includes('Физическая защита: 25%'));
    assert.ok(result.rules.includes('Установлено: Забрало'));
    assert.ok(!result.metrics.some(text=>text.startsWith('Штраф нагрузки:')));
});

test('Пустой фильтр выключает радиозащиту противогаза, но не меняет остальные защиты',async()=>{
    const {itemTooltipModel}=await load('itemPresentation.js');
    const template={category:'gas_mask',attributes:{protection:{radiation:.3,chemical:.5}}};
    for(const installedModules of [[],[{slotType:'filter',durability:0}]]){
        const model=itemTooltipModel(template,{installedModules});
        assert.ok(model.metrics.includes('Химическая защита: 50%'));
        assert.ok(model.metrics.some(text=>text.startsWith('Радиационная защита: 0%')));
    }
    const model=itemTooltipModel(template,{installedModules:[{slotType:'filter',durability:1}]});
    assert.ok(model.metrics.includes('Радиационная защита: 30%'));
    assert.ok(itemTooltipModel(template).metrics.includes('Радиационная защита: 30%'));
});
test('Подсказка встроенного шлема показывает оба штрафа экземпляра',async()=>{
    const {itemTooltipModel}=await load('itemPresentation.js');
    const model=itemTooltipModel({},{
        name:'Шлем Купол-М',category:'helmet',integratedWithArmor:true,durability:75,
        protection:{physical:.35},accuracyPenalty:3,charismaBonus:-2,
    });
    assert.ok(model.metrics.includes('Физическая защита: 35%'));
    assert.ok(model.metrics.includes('Штраф точности: 3'));
    assert.ok(model.metrics.includes('Штраф харизмы: 2'));
});
test('Импортное описание не повторяется разбором direct и техническими effects',async()=>{
    const {itemTooltipModel}=await load('itemPresentation.js');
    const result=itemTooltipModel({name:'Медикамент',description:'−1 Боль. +5 ХП.',attributes:{consumable:{direct:{hp:5,pain_delta:-1},effects:[{type:'pain',value:-1,source:'direct'}]}}});
    assert.deepEqual(Array.from(result.rules),['ОЗ: 5','Боль: -1']);
});

test('Старая версия официального экземпляра не возвращает ложный бонус срока',async()=>{
    const {itemTooltipModel}=await load('itemPresentation.js');
    const description='Длительность - 6 перемещений.';
    const base={catalog_name:'Подавитель эмоций',raw_description:description};
    const template={name:'Подавитель эмоций',description,attributes:{...base,
        consumable:{catalog_rules_version:'2026-11-04-duration',direct:{duration:6,duration_phase:'movement_end'},effects:[],modifiers:[]}}};
    const item={attributes:{...base,consumable:{catalog_rules_version:'2026-11-04',direct:{movement_points_delta:-6},
        modifiers:[{stat:'movement_points',value:-6}],effects:[]}}};
    const model=itemTooltipModel(template,item);
    assert.ok(!model.rules.some(rule=>rule.includes('-6')));
    assert.ok(model.rules.some(rule=>rule.includes('6')));
});

test('Вода: дробь из книги не дублируется direct/effects, инвентарный объём не равен литрам',async()=>{
    const {itemTooltipModel}=await load('itemPresentation.js');
    const effects=[{type:'radiation',name:'radiation',value:-1,tick:'manual'},
        {type:'intoxication',name:'intoxication',value:-10,tick:'manual'},
        {type:'exhaustion',name:'exhaustion',value:-.5,tick:'manual'}];
    const result=itemTooltipModel({name:'Вода',category:'consumable',volume:2,
        description:'-1 радиации -1/2 Уровня истощения -10 Опьянения 3 Использования.',
        attributes:{uses:3,effects,consumable:{direct:{radiation_delta:-1,intoxication_delta:-10,
            exhaustion_delta:-.5,uses:3,satisfy_water:true,water_source:true},effects}}}, {uses:3,maxUses:3});
    assert.ok(result.metrics.includes('Объём: 2'));
    assert.ok(result.metrics.includes('Запас воды: 1 л'));
    assert.deepEqual(Array.from(result.rules),['Радиация: -1','Опьянение: -10','Истощение: -0.5','Утоляет жажду']);
});

test('Радиоактивные виды воды показывают каждый эффект отдельно',async()=>{
    const {itemTooltipModel}=await load('itemPresentation.js');
    const model=itemTooltipModel({name:'Дождевая вода',description:'+5 радиации, +1 уровня истощения, -10 опьянения при питье. 3 использования.',
        category:'consumable',attributes:{uses:3,consumable:{direct:{radiation_delta:5,exhaustion_delta:1,
            intoxication_delta:-10,uses:3,satisfy_water:true,water_source:true}}}}, {uses:2,maxUses:3});
    assert.deepEqual(Array.from(model.rules),['Радиация: +5','Истощение: +1','Опьянение: -10','Утоляет жажду']);
});

test('Подсказка сохраняет противоположные модификаторы и переводит технический слот',async()=>{
    const {itemTooltipModel}=await load('itemPresentation.js');
    const model=itemTooltipModel({category:'weapon_module',subcategory:'handguard',
        attributes:{consumable:{modifiers:[{stat:'accuracy',value:1,remaining:2,tick:'turn_end'},
            {stat:'accuracy',value:-1,remaining:2,tick:'turn_end'}]}}},{lightPower:{uses_left:8}});
    assert.equal(model.metrics[0],'Цевьё');
    assert.ok(model.metrics.includes('Батарейка: 8 / 10'));
    assert.equal(model.rules.length,2);
    assert.ok(model.rules.some(text=>text.includes('+1')) && model.rules.some(text=>text.includes('-1')));
});

test('Дедупликация сохраняет условия из описания и эффекты с отдельным сроком',async()=>{
    const {itemTooltipModel}=await load('itemPresentation.js');
    const result=itemTooltipModel({description:'-1/2 Уровня истощения, только после отдыха.',
        attributes:{consumable:{direct:{exhaustion_delta:-.5},effects:[{type:'exhaustion',value:-.5,remaining:2,tick:'turn_end'}]}}});
    assert.ok(result.rules.some(text=>text.includes('только после отдыха')));
    assert.equal(result.rules.filter(text=>text==='Истощение: -0.5').length,0);
    assert.ok(result.rules.some(text=>text.includes('2 ходов')));
});
test('Отредактированный профиль и предмет без описания показывают действующие правила',async()=>{
    const {itemTooltipModel}=await load('itemPresentation.js');
    const result=itemTooltipModel({name:'Препарат',attributes:{notes:'Описание проверочного предмета',consumable:{editor_version:2,direct:{pain_delta:-1,stress_delta:-2},effects:[{type:'regeneration',value:5,remaining:3,tick:'turn_end'},{type:'pain',value:-1,source:'direct'}]}}});
    assert.ok(result.rules.includes('Боль: -1'));
    assert.ok(result.rules.includes('Стресс: -2'));
    assert.ok(result.rules.includes('Регенерация · +5 · осталось 3 ходов'));
    assert.ok(result.rules.includes('Описание проверочного предмета'));
    assert.equal(result.rules.filter(t=>t.includes('Боль')).length,1);
});
test('У экземпляра без шаблона сохраняются описание и скрытая группа крови не раскрывается',async()=>{
    const {itemTooltipModel}=await load('itemPresentation.js');
    const result=itemTooltipModel({}, {name:'Пакет',description:'Стерильный пакет',attributes:{bloodType:4,bloodTypeKnown:false}});
    assert.ok(result.rules.includes('Стерильный пакет'));
    assert.ok(!JSON.stringify(result).includes('bloodType'));
    assert.ok(result.metrics.includes('Группа крови: неизвестна'));
    assert.ok(itemTooltipModel({}, {attributes:{bloodType:4,bloodTypeKnown:true}}).metrics.includes('Группа крови: 4 группа'));
});
test('Пометка экземпляра не скрывает действие препарата из профиля',async()=>{
    const {itemTooltipModel}=await load('itemPresentation.js');
    const result=itemTooltipModel({name:'Препарат',description:'Боль -1',attributes:{consumable:{direct:{pain_delta:-1}}}}, {description:'Индивидуальная пометка'});
    assert.ok(result.rules.includes('Боль: -1'));
    assert.ok(result.rules.includes('Индивидуальная пометка'));
});
test('Подсказка выбирает сторону или пространство над строкой без выхода за экран',async()=>{
    const {tooltipPosition}=await load('itemPresentation.js');
    for(const rect of [{left:700,right:990,top:100,bottom:160},{left:10,right:990,top:400,bottom:460}]){
        const p=tooltipPosition(rect,350,300,{width:1000,height:700});
        assert.ok(p.left>=8&&p.left+350<=992);
        const bottom=p.top+Math.min(300,p.maxHeight);
        assert.ok(p.top>=8&&bottom<=692);
        assert.ok(p.left+350<=rect.left||p.left>=rect.right||bottom<=rect.top||p.top>=rect.bottom);
    }
});
test('Сроки и отрицательные значения имеют русские единицы, просмотр не бросает смерть',async()=>{
    const {effectSummary,syncHealthDerivedStatuses}=await load('effects.js');
    assert.equal(effectSummary({type:'pain',name:'pain',value:-.25,remaining:3,tick:'round_end'}),'Боль · -0.25 · осталось 3 раундов');
    assert.ok(effectSummary({type:'regeneration',value:5,remaining:24,time_unit:'hour',remaining_seconds:82800}).includes('23 ч'));
    const health={intoxication:150,effects:[]};syncHealthDerivedStatuses(health);syncHealthDerivedStatuses(health);
    assert.equal(health.combatMeta.intoxicationDeathRoll,undefined);
    assert.ok(!health.effects.some(e=>e.type==='death'));
});
test('Краткое описание каталога не скрывает дополнительные правила и временные состояния',async()=>{
    const {itemTooltipModel}=await load('itemPresentation.js');
    const result=itemTooltipModel({name:'Радист',description:'Препарат Радист. Ампула.',attributes:{consumable:{direct:{exhaustion_delta:1,radiation_filter_capacity:100},effects:[{type:'temporary_exhaustion',name:'Радист: истощение',remaining:3,tick:'turn_end',onExpire:[{field:'exhaustion',delta:-1}]}]}}});
    assert.ok(result.rules.includes('Истощение: +1'));
    assert.ok(result.rules.some(t=>t.includes('Радист: истощение')));
});
test('Типы реально применяемых состояний сохраняются на клиенте',async()=>{
    const {normalizeEffect}=await load('effects.js');
    for(const type of ['temporary_exhaustion','temporary_hp_max','amputation','anomaly_compulsion','anomaly_electric_penalty','anomaly_will_penalty','circular_attack_recovery','death_order','mutant_poison','psy_roar_disadvantage','zombification']){
        assert.equal(normalizeEffect({type,id:'stable'}).type,type);
    }
});
test('Подпись тяжести совпадает с расчётом кровопотери при старом value=1',async()=>{
    const {effectSummary,getEffectImpact,syncHealthDerivedStatuses}=await load('effects.js');
    const effect={type:'bleeding_external_medium',value:1,stacks:1};
    const health={effects:[effect]};syncHealthDerivedStatuses(health);
    assert.equal(getEffectImpact(effect).severity,health.bleeding.totalSeverity);
    assert.ok(effectSummary(effect).includes(`тяжесть ${health.bleeding.totalSeverity}`));
});

test('неизвестный размер из каталога не выдаётся за нулевой, косметика имеет русские поля',async()=>{
    const {itemTooltipModel}=await load('itemPresentation.js');
    const item={name:'Рюкзак',category:'backpack',weight:0,volume:0,
        attributes:{unspecified_fields:['weight','volume'],limit:40}};
    const result=itemTooltipModel(item);
    assert.ok(result.metrics.includes('Вес: не указан'));
    assert.ok(result.metrics.includes('Объём: не указан'));
    assert.ok(result.metrics.includes('Вместимость рюкзака: 40'));
    const changed=itemTooltipModel({...item,weight:2});
    assert.ok(changed.metrics.includes('Вес: 2 кг'));
    assert.ok(!changed.metrics.includes('Вес: не указан'));
    const glasses=itemTooltipModel({category:'glasses',attributes:{flash_protection:.8,eye_physical_protection:15}});
    assert.ok(glasses.metrics.includes('Коэффициент ослепления: 0.8'));
    assert.ok(glasses.metrics.includes('Физическая защита глаз (%): 15'));
});

test('старая официальная подпись обновляется, а ручной текст экземпляра остаётся',async()=>{
    const {itemTooltipModel}=await load('itemPresentation.js');
    const template={name:'12.7x55',category:'ammo',description:'Две проверки дополнительной травмы.',
        attributes:{source_description:'Одна дополнительная травма.'}};
    const result=itemTooltipModel(template,{name:template.name,category:'ammo',description:'Одна дополнительная травма.'});
    assert.ok(result.rules.includes('Две проверки дополнительной травмы.'));
    assert.ok(!result.rules.includes('Одна дополнительная травма.'));
    const custom=itemTooltipModel(template,{name:template.name,category:'ammo',description:'Ручное правило ГМа.'});
    assert.ok(custom.rules.includes('Ручное правило ГМа.'));
});
