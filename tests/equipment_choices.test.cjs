const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const load=()=>import(`data:text/javascript;base64,${Buffer.from(fs.readFileSync(path.join(__dirname,'../app/static/js/equipmentChoices.js'),'utf8')).toString('base64')}`);
const select=values=>({options:values.map(value=>({value})),value:values[0],addEventListener(type,handler){this.change=handler;}});

test('выбранная батарейка сохраняется при обновлении и перестановке расходников',async()=>{
    const {rememberEquipmentChoice}=await load(),first=select(['grease','battery']);
    rememberEquipmentChoice(first,'actor:base');first.value='battery';first.change();
    const refreshed=select(['battery-2','grease','battery']);rememberEquipmentChoice(refreshed,'actor:base');
    assert.equal(refreshed.value,'battery');
    const other=select(['grease','battery']);rememberEquipmentChoice(other,'other-actor:base');
    assert.equal(other.value,'grease');
});

test('исчезнувший экземпляр не восстанавливается по старому индексу',async()=>{
    const {rememberEquipmentChoice}=await load(),first=select(['removed-battery','grease']);
    rememberEquipmentChoice(first,'removed-test');first.change();
    const refreshed=select(['grease']);rememberEquipmentChoice(refreshed,'removed-test');
    assert.equal(refreshed.value,'grease');
});
