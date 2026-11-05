const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');

async function setup(perform){
    let id=0;const notices=[],context=vm.createContext({crypto:{randomUUID:()=>`check-${++id}`}});
    const api=new vm.SyntheticModule(['Server'],function(){this.setExport('Server',{performSkillCheck:perform});},{context});
    const utils=new vm.SyntheticModule(['showNotification'],function(){this.setExport('showNotification',text=>notices.push(text));},{context});
    const module=new vm.SourceTextModule(fs.readFileSync(path.join(__dirname,'../app/static/js/quickSkillCheck.js'),'utf8'),{context});
    await module.link(name=>name==='./api.js'?api:utils);await module.evaluate();
    return{run:module.namespace.rollQuickSkill,notices};
}
const options={characterId:7,skillPath:'skills.physical.shooting',skillLabel:'Стрельба',locationId:12};
const result={data:{_revision:5},check:{rolls:[10],roll:10,total:5,equipment_modifier:-2,rules:['Погода на улице: -3'],difficulty:null,success:null}};

test('быстрый бросок берёт итог сервера, сохраняет выбранную сцену и не придумывает исход',async()=>{
    let saved;const calls=[];
    const {run,notices}=await setup(async(id,payload)=>{calls.push([id,payload]);return result;});
    assert.equal(await run({...options,onSaved:data=>{saved=data;}}),result);
    assert.equal(calls[0][0],7);assert.equal(calls[0][1].location_id,12);assert.equal(calls[0][1].difficulty,null);
    assert.equal(saved,result.data);assert.match(notices[0],/итог 5/);assert.match(notices[0],/экипировка -2/);
    assert.doesNotMatch(notices[0],/успех|провал|None/);
});

test('повторное нажатие во время ответа не создаёт второй бросок',async()=>{
    let finish,calls=0;
    const {run}=await setup(()=>{calls++;return new Promise(resolve=>{finish=resolve;});});
    const first=run(options);
    assert.equal(await run(options),null);assert.equal(calls,1);
    finish(result);assert.equal(await first,result);
});

test('после потери ответа повтор использует прежний номер операции, следующий бросок — новый',async()=>{
    const ids=[];
    const {run}=await setup(async(_,payload)=>{ids.push(payload.operation_id);if(ids.length===1)throw Error('Связь прервалась');return result;});
    assert.equal(await run(options),null);assert.equal(await run(options),result);assert.equal(await run(options),result);
    assert.equal(ids[0],ids[1]);assert.notEqual(ids[1],ids[2]);
});
