const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const rules=import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync(path.join(__dirname,'../app/static/js/hearingRules.js'),'utf8')).toString('base64'));
test('Шкала слуха совпадает на границах и сохраняет дробное оглушение',async()=>{
    const {hearingModifier}=await rules;
    for(const [level,modifier] of [[0,0],[20,0],[20.5,-1],[49,-1],[50,-3],[69,-3],[70,-5],[89,-5],[90,-5]])
        assert.equal(hearingModifier(level),modifier);
});
