const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../app/static/js/creationTemplates.js'),'utf8').replace(/export function /g,'function ');
const context=vm.createContext({});vm.runInContext(source,context);
const rules={backgrounds:[{id:'bg',available:true}],skillOrder:['physical.agility'],traits:[{id:'repeat'}],startingPurchaseTemplates:[{id:42}]};
const plan=()=>({backgroundId:'bg',specializations:['physical.agility'],raises:{'physical.agility':2},lowers:{},finalRaises:{},traits:[{id:'repeat'},{id:'repeat'}],cashPoints:1,zoneHistory:true,purchases:[{templateId:42,quantity:2}]});
const roundtrip=value=>context.readCharacterPlanFile(context.characterPlanFile(value),rules);
test('шаблон повторно использует решения, без имени, здоровья, денег и наград готового листа',()=>{
    const original={...plan(),name:'Барсик',health:{current:999},inventory:{money:999999},creation:{zoneHistory:{settled:true}}};
    const result=roundtrip(original);
    assert.deepEqual(JSON.parse(JSON.stringify(result)),plan());
    assert.equal(original.health.current,999);
    result.traits[0].id='changed';assert.equal(original.traits[0].id,'repeat');
});
test('шаблон отклоняет удалённый товар, неизвестный навык, четвёртую черту и некорректный формат',()=>{
    for(const mutate of [p=>p.purchases[0].templateId=99,p=>p.specializations.push('unknown'),p=>p.traits.push({id:'repeat'},{id:'repeat'}),p=>p.raises['physical.agility']=1.5,p=>p.purchases[0].quantity=-1]){
        const value=plan();mutate(value);assert.throws(()=>roundtrip(value));
    }
    for(const text of ['{}','null','not-json',JSON.stringify({format:'perimeter-character-plan',version:2,plan:plan()}),' '.repeat(256*1024+1)])assert.throws(()=>context.readCharacterPlanFile(text,rules));
});
