const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('app/static/js/characterSheet.js','utf8');
const context=vm.createContext({
    window:{isGM:true},escapeHtml:value=>String(value),
    ITEM_CATEGORY_ORDER:[],WEAPON_SUBCATEGORY_ORDER:[],CONSUMABLE_SECTION_ORDER:[],
    compareTemplatesBySourceOrder:()=>0,
});
for(const name of ['compareRussianNames','compareByFixedOrder','getCategoryDisplay','inventoryPickerSection','inventoryPickerGroup','renderInventoryTemplatePicker']){
    const code=source.match(new RegExp('(?:export )?function '+name+'\\([\\s\\S]*?\\n\\}'))?.[0];
    assert.ok(code,name);vm.runInContext(code.replace(/^export /,''),context);
}

test('каталог артефактов показывает мусорный класс первым и подписывает класс X', () => {
    const html = context.renderInventoryTemplatePicker(['2', 'x', '3', 'trash', '1'].map((item_class, index) => ({
        id: index + 1,
        name: `Артефакт ${index + 1}`,
        category: 'artifact',
        subcategory: 'Гравитационные аномалии',
        item_class,
        attributes: {},
    })));
    const headings = Array.from(html.matchAll(/<summary>([^<]+) · 1<\/summary>/g), match => match[1]);
    assert.deepEqual(headings, ['Мусорный класс', 'Класс 1', 'Класс 2', 'Класс 3', 'Класс X']);
});

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

test('старая ушанка отображается в головных уборах с прежним семейством модификаций', () => {
    const template = { id: 12, name: 'Ушанка', category: 'helmet', attributes: { modification_category: 'Ушанка' } };
    assert.equal(context.inventoryPickerSection(template), 'Головные уборы');
    assert.match(context.renderInventoryTemplatePicker([template], 'Головные уборы'), /selectInventoryTemplate\(12\)/);
    assert.equal(template.attributes.modification_category, 'Ушанка');
});

test('ПНВ и все фонари находятся в одном разделе приборов, детекторы отдельно',()=>{
    const html=context.renderInventoryTemplatePicker([
        {id:1,name:'Очки ПНВ',category:'device',attributes:{night_vision:true}},
        {id:2,name:'Налобный фонарь',category:'device',attributes:{portable_light:true}},
        {id:3,name:'Фонарик',category:'misc',attributes:{}},
        {id:4,name:'ДА-2',category:'detector',attributes:{}},
    ]);
    assert.equal((html.match(/<span>ПНВ и фонари<\/span>/g)||[]).length,1);
    assert.match(html,/<span>Детекторы<\/span>/);
    assert.doesNotMatch(html,/<span>(?:ПНВ|Фонари)<\/span>/);
});
