// Матрица использует настоящий конструктор вариантов из листа, без второй копии формул.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'../..');
const report=path.join(root,'.private-testing/reports/catalog-sweep-2026-11-04');
const source=fs.readFileSync(path.join(root,'app/static/js/characterSheet.js'),'utf8');
const names=['normalizeAmmoVariant','getAmmoVariantLabel','getAmmoVariantStats','applyAmmoVariantToItem'];
const definitions=names.map(name=>{
    const match=source.match(new RegExp('function '+name+'\\b[\\s\\S]*?\\n}\\r?\\n'));
    if(!match)throw Error('Не найдена настоящая функция '+name);
    return match[0];
});
const modifiers=source.match(/const AMMO_VARIANT_MODIFIERS = \{[\s\S]*?\r?\n\};/);
if(!modifiers)throw Error('Не найдены модификаторы боеприпасов');
const context=vm.createContext({});vm.runInContext([modifiers[0],...definitions].join('\n'),context);
const catalog=JSON.parse(fs.readFileSync(path.join(report,'source.json'),'utf8'));
const items=[];
for(const template of catalog.equipment.filter(t=>t.category==='ammo')){
    const variants=[template.attributes?.ammo_variant||null,...(template.attributes?.ammo_variants||[])];
    for(const variant of [...new Set(variants)]){
        context.item=JSON.parse(JSON.stringify({...template,quantity:1,templateId:null,id:'sweep-ammo'}));
        context.template=template;context.variant=variant;
        const item=vm.runInContext('applyAmmoVariantToItem(item,template,variant)',context);
        items.push({base_name:template.name,variant:variant||'standard',item:JSON.parse(JSON.stringify(item))});
    }
}
fs.writeFileSync(path.join(report,'ammo-variants.json'),JSON.stringify(items,null,2));
