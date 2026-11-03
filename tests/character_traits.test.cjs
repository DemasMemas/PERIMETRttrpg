const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const source=fs.readFileSync('app/static/js/characterTraits.js','utf8');
const rules=import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const character=(name,count=1)=>({features:{specialTraits:Array.from({length:count},()=>({name}))}});
test('Рюкзак: порог пяти ячеек, округление вниз и неизменный предмет',async()=>{
    const {backpackItemVolume}=await rules,item={volume:5.9,quantity:2};
    assert.equal(backpackItemVolume({},item),11.8);
    assert.equal(backpackItemVolume(character('Игрок в тетрис'),item),8);
    assert.equal(backpackItemVolume(character('Игрок в тетрис'),{volume:4.9,quantity:2}),9.8);
    assert.equal(backpackItemVolume(character('Игрок в тетрис',3),{volume:15,quantity:1}),6);
    assert.equal(item.volume,5.9);
});
test('Половина опыта переносится в следующее обучение',async()=>{
    const {addLearningXp}=await rules,data=character('Заинтересованный');data.features.specialTraits[0].choices={skill:'physical.agility'};
    const skill={base:12,xp:0};addLearningXp(data,'physical.agility',skill);
    assert.deepEqual([skill.xp,skill.learningRemainder],[1,.5]);
    addLearningXp(data,'physical.agility',skill);assert.deepEqual([skill.xp,skill.learningRemainder],[3,0]);
});
test('Повтор занимает слот и складывает указанное свойство',async()=>{
    const {passiveTraits,traitCount}=await rules,data=character('Железная воля',3);
    assert.equal(traitCount(data,'Железная воля'),3);assert.equal(passiveTraits(data).painReduction,3);
});
test('Вместимость карманов определяется чертами и не редактируемым значением',async()=>{
    const {pocketCapacity}=await rules;
    assert.equal(pocketCapacity({inventory:{pocketMaxVolume:999}}),10);
    assert.equal(pocketCapacity(character('Глубокие карманы')),40);
    assert.equal(pocketCapacity(character('Узкие карманы',3)),0);
    assert.equal(pocketCapacity({features:{specialTraits:[{name:'Глубокие карманы'},{name:'Узкие карманы'},{name:'Узкие карманы',active:false}]}}),35);
});
