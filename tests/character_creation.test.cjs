const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../app/static/js/characterCreation.js'),'utf8');
const start=source.indexOf('    async function preview() {'),end=source.indexOf('    function queuePreview()',start);
assert.ok(start>0&&end>start);
function previewContext(plan,cached){
    const state={textContent:'Считаем…'},context=vm.createContext({
        clearTimeout:()=>{},timer:null,readPlan:()=>plan,latestKey:cached?JSON.stringify(plan):'previous',
        latestData:{skills:{}},latestError:'Старая ошибка',error:()=>{},refreshView:()=>{},find:()=>state,
    });
    vm.runInContext(source.slice(start,end),context);
    return{context,state};
}
test('возврат к уже рассчитанному распределению снимает устаревшее ожидание и ошибку',async()=>{
    const {context,state}=previewContext({backgroundId:'background'},true);
    assert.equal(await context.preview(),true);
    assert.equal(state.textContent,'');assert.equal(context.latestError,'');
});
test('сброс предыстории снимает ожидание расчёта и устаревшие значения',async()=>{
    const {context,state}=previewContext({backgroundId:''},false);
    assert.equal(await context.preview(),false);
    assert.equal(state.textContent,'');assert.equal(context.latestData,null);
});
