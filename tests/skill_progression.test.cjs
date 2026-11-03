const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../app/static/js/characterSheet.js'), 'utf8');

function sheet(data) {
    const container = { innerHTML: '' };
    let saves = 0;
    const context = vm.createContext({
        window: { isGM: true }, currentCharacterData: data, currentCharacterCanEdit:true,sheetSaveBlocked:false,
        document: { getElementById: () => container },
        loadTemplatesForLobby: async () => [], getExoskeletonPowerProfile: () => ({ powered: false }),
        renderSpecialTraits() {}, applySheetEditPermissions(){},updateDataFromFields(){},showNotification() {}, scheduleAutoSave() { saves++; },
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname,'../app/static/js/characterTraits.js'),'utf8').replaceAll('export ',''),context);
    vm.runInContext(source.slice(source.indexOf('function getRequiredXp('), source.indexOf('// ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ')), context);
    vm.runInContext(source.slice(source.indexOf('async function renderSkillsTab('), source.indexOf('function renderSpecialTraits(')), context);
    return { context, container, saves: () => saves, render: () => context.renderSkillsTab(data) };
}

test('цена повышения определяется целевым значением, включая 12→13 за 3 XP', () => {
    const { context } = sheet({});
    for (const [base, expected] of [[5, 1], [10, 1], [11, 2], [12, 3], [13, 4], [19, 10]]) {
        assert.equal(context.getRequiredXp(base), expected);
    }
});

test('отрисованный навык по умолчанию доступен кнопкам всех трёх категорий', async () => {
    const data = {};
    const ui = sheet(data);
    await ui.render();
    for (const skill of ['physical.strength', 'social.charisma', 'other.medicine']) {
        ui.context.window.addSkillXpFromPoints(skill);
        const [category, name] = skill.split('.');
        assert.equal(data.skills[category][name].base, 6);
        assert.equal(data.skills[category][name].xp, 0);
    }
    assert.equal(data.skills.skillPoints, 27);
    assert.equal(ui.saves(), 3);
});

test('двух XP не хватает на 13, третье очко повышает навык без потери остатка', async () => {
    const data = { skills: { skillPoints: 1, physical: { will: { base: 12, bonus: 0, xp: 1 } } } };
    const ui = sheet(data);
    await ui.render();
    ui.context.window.addSkillXpFromUse('physical.will');
    assert.equal(data.skills.physical.will.base, 12);
    assert.equal(data.skills.physical.will.xp, 2);
    ui.context.window.addSkillXpFromPoints('physical.will');
    assert.equal(data.skills.physical.will.base, 13);
    assert.equal(data.skills.physical.will.xp, 0);
    assert.equal(data.skills.skillPoints, 0);
    ui.context.window.addSkillXpFromPoints('physical.will');
    assert.equal(data.skills.physical.will.xp, 0);
    assert.equal(ui.saves(), 2);
});

test('ручной ввод немедленно меняет модель и цену, сбрасывает XP и не теряется перед кнопкой',async()=>{
    const persistence=await import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync(path.join(__dirname,'../app/static/js/characterPersistence.js'),'utf8')).toString('base64'));
    const data={skills:{physical:{will:{base:12,bonus:0,xp:2}}}};
    const ui=sheet(data),progress={textContent:''};
    const input={type:'number',name:'skills.physical.will.base',value:'14',defaultValue:'12',tagName:'INPUT',getAttribute(name){return this[name];}};
    Object.assign(ui.context,{consumeChangedSheetInputs:persistence.consumeChangedSheetInputs,
        pendingManualFieldPaths:new Set(),normalizeCharacterEffects(){},syncHealthDerivedStatuses(){},forceSyncCharacter(){},CSS:{escape:s=>s},
        document:{getElementById:id=>id==='character-sheet-form'?{querySelectorAll:()=>[input]}:ui.container,querySelector:()=>progress}});
    const start=source.indexOf('function setValueByPath('),end=source.indexOf('function renderCreatedByPlayerBadge(',start);
    vm.runInContext(source.slice(start,end),ui.context);
    ui.context.characterValueAtPath=(value,path)=>path.split('.').reduce((v,p)=>v?.[p],value);
    ui.context.handleManualFormEdit({type:'input',target:input,isTrusted:true});
    assert.deepEqual(data.skills.physical.will,{base:14,bonus:0,xp:0,learningRemainder:0});
    assert.equal(progress.textContent,'Опыт: 0/5');
    ui.context.window.addSkillXpFromUse('physical.will');
    assert.equal(data.skills.physical.will.xp,1);
    assert.deepEqual([...ui.context.consumeManualFieldPaths()].sort(),['skills.physical.will.base','skills.physical.will.xp']);
    input.value='';ui.context.handleManualFormEdit({type:'input',target:input,isTrusted:true});
    assert.equal(data.skills.physical.will.base,14);
    ui.context.handleManualFormEdit({type:'change',target:input,isTrusted:true});
    assert.equal(input.value,'14');assert.equal(data.skills.physical.will.xp,1);
    ui.context.currentCharacterCanEdit=false;
    ui.context.window.addSkillXpFromUse('physical.will');
    assert.equal(data.skills.physical.will.xp,1);
    assert.equal(ui.context.validSkillField({name:'skills.physical.will.bonus',value:'1e100'}),false);
});

test('ожидание шаблонов черт не задерживает строки навыков и не перерисовывает новый ввод',async()=>{
    const data={skills:{physical:{will:{base:12,bonus:0,xp:2}}}},ui=sheet(data),pending=[],rendered=[];
    let root={};
    Object.defineProperty(ui.container,'innerHTML',{set(value){this.html=value;root={};},get(){return this.html;}});
    ui.context.document.getElementById=id=>id==='special-traits-container'?root:ui.container;
    ui.context.loadTemplatesForLobby=()=>new Promise(resolve=>pending.push(resolve));
    ui.context.renderSpecialTraits=(traits,templates)=>rendered.push(templates);
    const first=ui.render();assert.ok(ui.container.innerHTML.includes('Опыт: 2/3'));
    data.skills.physical.will={base:14,bonus:0,xp:1};
    const second=ui.render();assert.ok(ui.container.innerHTML.includes('Опыт: 1/5'));
    pending[0](['старый ответ']);await first;
    assert.equal(rendered.length,0);
    pending[1](['актуальный ответ']);await second;
    assert.deepEqual(rendered,[['актуальный ответ']]);
});
