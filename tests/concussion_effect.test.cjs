const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const effects=import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync(path.join(__dirname,'../app/static/js/effects.js'),'utf8')).toString('base64'));
test('Контузия из старого сохранения сохраняет тип, штраф и длительность',async()=>{
    const E=await effects;
    const effect=E.normalizeEffect({type:'generic',name:'Контузия',remaining:5,tick:'round_end',roll_modifier:-3});
    assert.equal(effect.type,'concussion');assert.equal(effect.rollPenalty,3);
    assert.equal(E.tickEffect(effect,'turn_end').remaining,5);
    assert.equal(E.tickEffect(effect,'round_end').remaining,4);
});
test('Повторная контузия не умножает штраф и не сокращает текущую длительность',async()=>{
    const E=await effects,health={current:700,max:700,effects:[]};
    E.applyEffectToHealth(health,{type:'concussion',remaining:5,tick:'round_end',source:'grenade'});
    E.applyEffectToHealth(health,{type:'concussion',remaining:3,tick:'round_end',source:'wave'});
    assert.equal(health.effects.filter(e=>e.type==='concussion').length,1);
    assert.equal(health.effects.find(e=>e.type==='concussion').remaining,5);
    assert.equal(health.effects.find(e=>e.type==='concussion').rollPenalty,3);
});
