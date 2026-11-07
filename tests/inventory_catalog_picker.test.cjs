const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('app/static/js/characterSheet.js','utf8');
const context=vm.createContext({
    window:{isGM:true},escapeHtml:value=>String(value),
    ITEM_CATEGORY_ORDER:[],WEAPON_SUBCATEGORY_ORDER:[],CONSUMABLE_SECTION_ORDER:[],
    compareByFixedOrder:(a,b)=>a.localeCompare(b,'ru'),compareTemplatesBySourceOrder:()=>0,
});
for(const name of ['getCategoryDisplay','inventoryPickerSection','inventoryPickerGroup','renderInventoryTemplatePicker']){
    const code=source.match(new RegExp('(?:export )?function '+name+'\\([\\s\\S]*?\\n\\}'))?.[0];
    assert.ok(code,name);vm.runInContext(code.replace(/^export /,''),context);
}

test('шлемы сохраняют подкатегории, а баки и баллоны имеют отдельный раздел приборов',()=>{
    const html=context.renderInventoryTemplatePicker([
        {id:1,name:'Шлем',category:'helmet',attributes:{integrated_visor:true}},
        {id:2,name:'Бак',category:'device',attributes:{flamethrower_tank:'jet'}},
        {id:3,name:'Баллон',category:'device',attributes:{flamethrower_air:true}},
    ]);
    assert.match(html,/>Шлемы</);assert.match(html,/>Шлемы с забралом</);
    assert.match(html,/>Баки и Баллоны Огнеметов</);
    assert.doesNotMatch(html,/Головное снаряжение|Другие предметы/);
});

test('служебный отрицательный ID не показывается, но остаётся в выборе встроенного протеза',()=>{
    const html=context.renderInventoryTemplatePicker([
        {id:-33001,name:'Протез',category:'misc',subcategory:'Протезы',attributes:{}},
    ]);
    assert.doesNotMatch(html,/ID -33001/);
    assert.match(html,/selectInventoryTemplate\(-33001\)/);
});
