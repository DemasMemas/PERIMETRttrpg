const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
async function setup(){
    const requests=[],listeners={},window={currentLobbyId:1,currentCharacterId:10,isLocationActive:false,isGM:false,addEventListener:(key,cb)=>listeners[key]=cb};
    const context=vm.createContext({window,Date,Number,encodeURIComponent});
    const api=new vm.SyntheticModule(['Server'],function(){this.setExport('Server',{getRadiationAccess:(lobbyId,characterId)=>new Promise(resolve=>requests.push({lobbyId,characterId,resolve}))});},{context});
    const source=fs.readFileSync(path.join(__dirname,'../app/static/js/radiationReadings.js'),'utf8');
    const module=new vm.SourceTextModule(source,{context});await module.link(()=>api);await module.evaluate();
    return {window,requests,listeners,api:module.namespace};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
test('Показания скрыты до разрешения сервера и сбрасываются при смене персонажа',async()=>{
    const s=await setup();assert.equal(s.api.radiationTooltipLine(5),'');assert.equal(s.requests.length,1);
    s.requests[0].resolve({can_read:true});await flush();assert.match(s.api.radiationTooltipLine(5),/Радиация: 5/);
    s.window.currentCharacterId=11;assert.equal(s.api.radiationTooltipLine(5),'');
    s.requests[1].resolve({can_read:false});await flush();assert.equal(s.api.radiationTooltipLine(5),'');
    s.window.isGM=true;assert.match(s.api.radiationTooltipLine(5),/Радиация: 5/);
});
test('Запоздалое разрешение и выключенный дозиметр не возвращают показания',async()=>{
    const s=await setup();s.api.radiationTooltipLine(3);s.window.currentCharacterId=11;s.api.radiationTooltipLine(3);
    s.requests[0].resolve({can_read:true});await flush();assert.equal(s.api.radiationTooltipLine(3),'');
    s.requests[1].resolve({can_read:true});await flush();assert.match(s.api.radiationTooltipLine(3),/Радиация/);
    s.listeners['character-item-operation-finished']();assert.equal(s.api.radiationTooltipLine(3),'');
});
