const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../app/static/js/characterSheet.js'),'utf8');
const start=source.indexOf('async function applyRemoteSheetData('),end=source.indexOf('function queueRemoteSheetData(',start);
const artifactBonusSource=fs.readFileSync(path.join(__dirname,'../app/static/js/artifactSkillBonuses.js'),'utf8').replace(/^export /gm,'');

test('поля навыков и лимит веса обновляются при изменении соответствующих бонусов',async()=>{
    let skills=0,weight=0;
    const context=vm.createContext({currentCharacterId:1,currentCharacterData:{_revision:1},
        normalizeCharacterEffects(){},diffCharacterData:()=>({scalarPaths:[],structuralPaths:[]}),
        captureSheetViewState:()=>null,patchRemoteScalarFields:()=>new Set(),
        pathTouches:(p,r)=>p===r||p.startsWith(r+'.'),backgroundIs:()=>false,
        renderSkillsTab(){skills++;},recalculateInventoryTotals(){weight++;},
        applySheetEditPermissions(){},restoreSheetViewState(){}});
    vm.runInContext(artifactBonusSource+source.slice(start,end),context);
    await context.applyRemoteSheetData({_revision:2,_artifact_profile:{movement_penalty:2}},1);
    assert.deepEqual([skills,weight],[0,0]);
    await context.applyRemoteSheetData({_revision:3,_artifact_profile:{skill_other:5}},1);
    assert.deepEqual([skills,weight],[1,0]);
    await context.applyRemoteSheetData({_revision:4,_artifact_profile:{skill_other:5,skill_strength:2}},1);
    assert.deepEqual([skills,weight],[2,1]);
});

test('изменение протеза и фонаря перерисовывает экипировку без отдельного боевого события',async()=>{
    for(const changed of ['health.prostheses.leftLeg.base.energy_seconds','health.combatMeta.portableLights.0.on','inventory.pockets.0.lightPower.uses_left']){
        let equipment=0;
        const context=vm.createContext({currentCharacterId:1,currentCharacterData:{_revision:1},
            normalizeCharacterEffects(){},diffCharacterData:()=>({scalarPaths:[changed],structuralPaths:[]}),
            captureSheetViewState:()=>null,patchRemoteScalarFields:()=>new Set(),
            pathTouches:(p,r)=>p===r||p.startsWith(r+'.'),backgroundIs:()=>false,
            refreshHealthPanel(){},renderBasicTab(){},renderSkillsTab(){},renderInventoryTab(){},renderSettingsTab(){},renderNotesTab(){},
            renderEquipmentTab(){equipment++;},applySheetEditPermissions(){},restoreSheetViewState(){}});
        vm.runInContext(artifactBonusSource+source.slice(start,end),context);
        await context.applyRemoteSheetData({_revision:2,health:{prostheses:{}}},1);
        assert.equal(equipment,1,changed);
    }
});
