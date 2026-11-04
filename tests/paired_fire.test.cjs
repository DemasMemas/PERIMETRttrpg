const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const load=()=>import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync(path.join(__dirname,'../app/static/js/pairedFire.js'),'utf8')).toString('base64'));
test('общие режимы исключают прицельный и несовместимый дуплет; цена определяется худшей эргономикой',async()=>{
    const {pairedFireOptions}=await load();
    const result=pairedFireOptions([{},{}],[{single_shot_options:[1,2],supports_burst:true,burst_size:2},{single_shot_options:[1],supports_burst:true,burst_size:3}],['submachine_gun','assault_rifle'],[{value:90},{value:10}]);
    assert.deepEqual(result.map(option=>option.mode),['unaimed','rapid','burst']);
    assert.equal(result.find(option=>option.mode==='burst').cost,3);
    assert.ok(result.every(option=>option.mode==='burst'||option.shots===1));
});
test('револьвер в паре сохраняет одиночные режимы, без очередей; гранатомёт не получает общий режим',async()=>{
    const {pairedFireOptions}=await load();
    assert.deepEqual(pairedFireOptions([{name:'Револьвер'},{}],[{supports_burst:true},{supports_burst:true}],['pistol','pistol'],[]).map(option=>option.mode),['unaimed','rapid']);
    assert.equal(pairedFireOptions([{},{}],[{},{}],['grenade_launcher','pistol'],[]).length,0);
});
