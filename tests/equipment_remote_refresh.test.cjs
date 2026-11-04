const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../app/static/js/characterSheet.js'),'utf8');
const start=source.indexOf('async function applyRemoteSheetData('),end=source.indexOf('function queueRemoteSheetData(',start);

test('изменение протеза и фонаря перерисовывает экипировку без отдельного боевого события',async()=>{
    for(const changed of ['health.prostheses.leftLeg.base.energy_seconds','health.combatMeta.portableLights.0.on','inventory.pockets.0.lightPower.uses_left']){
        let equipment=0;
        const context=vm.createContext({currentCharacterId:1,currentCharacterData:{_revision:1},
            normalizeCharacterEffects(){},diffCharacterData:()=>({scalarPaths:[changed],structuralPaths:[]}),
            captureSheetViewState:()=>null,patchRemoteScalarFields:()=>new Set(),
            pathTouches:(p,r)=>p===r||p.startsWith(r+'.'),backgroundIs:()=>false,
            refreshHealthPanel(){},renderBasicTab(){},renderSkillsTab(){},renderInventoryTab(){},renderSettingsTab(){},renderNotesTab(){},
            renderEquipmentTab(){equipment++;},applySheetEditPermissions(){},restoreSheetViewState(){}});
        vm.runInContext(source.slice(start,end),context);
        await context.applyRemoteSheetData({_revision:2,health:{prostheses:{}}},1);
        assert.equal(equipment,1,changed);
    }
});
