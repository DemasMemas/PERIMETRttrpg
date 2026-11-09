// №228: настольные размеры, раскрытое оружие и длинные названия настоящих предметов.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,actor,lobby,offered,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'catalog');assert.ok(safeSession.verified);
    let character=await request('/lobbies/characters/'+actor.id,null,token,'GET');const data=character.data;
    const templates=offered.global,weapon=templates.find(t=>t.category==='weapon'),melee=templates.find(t=>t.category==='melee_weapon');
    const item=(template,id)=>({id,templateId:template.id,name:template.name+' с очень длинным пользовательским названием предмета',category:template.category,
        attributes:template.attributes,quantity:1,weight:template.weight,volume:template.volume});
    assert.ok(weapon&&melee);
    data.weapons=[item(weapon,'layout-primary'),item(weapon,'layout-secondary'),item(melee,'layout-melee')];
    data.inventory.pockets.push({id:'layout-pda',name:'КПК',category:'misc',quantity:1,volume:2});
    data.equipment||={};
    for(const [slot,category]of [['helmet','helmet'],['armor','armor'],['gasMask','gas_mask'],['detector','detector'],['headphones','headphones'],['glasses','glasses'],['gloves','gloves'],
        ['ring','jewelry'],['necklace','jewelry'],['earrings','jewelry'],['bracelet1','jewelry'],['bracelet2','jewelry']]){
        const template=templates.find(t=>t.category===category);assert.ok(template,category);data.equipment[slot]=item(template,'layout-'+slot);
    }
    await request('/lobbies/characters/'+actor.id,{data},token,'PUT');
    const results=[];
    for(const [width,height]of [[1920,1080],[1600,900],[1366,768],[1280,720]]){
        await page.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
        await page.evaluate(`window.openCharacterSheet(${actor.id},'equipment').then(()=>true)`);
        await page.until("document.querySelector('.equipment-loadout .loadout-slot')",'Экипировка загружена '+width);
        const selectPanel=key=>page.evaluate(`document.querySelector('.equipment-panel-tab[data-panel=${key}]').click();true`);
        await selectPanel('detector');
        await page.until("[...document.querySelectorAll('.portable-light-row')].some(row=>row.getClientRects().length&&row.textContent.includes('КПК')&&row.querySelector('button'))",'КПК и его кнопки загружены '+width);
        const measure=()=>page.evaluate("(()=>{const tab=document.getElementById('sheet-tab-equipment');tab.scrollTop=0;const box=e=>{const r=e.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}};return{viewport:[innerWidth,innerHeight],root:box(tab),figure:box(tab.querySelector('.loadout-figure')),loadout:box(tab.querySelector('.equipment-loadout')),slots:[...tab.querySelectorAll('.equipment-loadout .loadout-slot')].map(e=>({label:e.getAttribute('aria-label'),...box(e)})),devices:[...tab.querySelectorAll('.portable-light-row')].filter(e=>e.getClientRects().length).map(box),scrollWidth:tab.scrollWidth,clientWidth:tab.clientWidth,scrollHeight:tab.scrollHeight,clientHeight:tab.clientHeight}})()");
        const devices=await measure();
        assert.ok(devices.devices.length&&devices.devices.every(r=>r.y>=devices.root.y&&r.bottom<=Math.min(height,devices.root.bottom)),'КПК и его кнопки видны без прокрутки');
        await selectPanel('weapons');
        const before=await measure();results.push(before);
        before.devices=devices.devices;
        fs.writeFileSync(path.join(safeSession.artifacts,'desktop-layout.json'),JSON.stringify(results,null,2));
        assert.equal(before.slots.length,18,'Все быстрые слоты присутствуют');
        assert.ok(before.slots.every(r=>r.y>=Math.max(0,before.root.y)&&r.bottom<=Math.min(height,before.root.bottom)&&r.x>=0&&r.right<=width),`Все быстрые слоты видны на ${width}×${height}; геометрия сохранена в desktop-layout.json`);
        assert.ok(before.figure.y>=Math.max(0,before.root.y)&&before.figure.bottom<=Math.min(height,before.root.bottom),'Силуэт виден полностью');
        assert.ok(before.scrollWidth<=before.clientWidth+2,'Нет горизонтальной прокрутки');
        await page.evaluate("document.querySelectorAll('[data-equipment-panel^=weapon-]').forEach(e=>{e.open=true});true");
        const expanded=await measure();assert.ok(Math.abs(before.loadout.height-expanded.loadout.height)<2,'Левая часть не зависит от раскрытия оружия');
        await page.evaluate("document.querySelector('.loadout-slot[data-equipment-open=weapon-1]').click();true");
        assert.deepEqual(await page.evaluate("[...document.querySelectorAll('.loadout-weapon-details[open]')].map(e=>e.dataset.equipmentPanel)"),['weapon-1'],'Выбор второго оружия сворачивает остальные');
        const shot=await page.call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(safeSession.artifacts,`equipment-${width}x${height}.png`),Buffer.from(shot.data,'base64'));
        await page.evaluate(`window.openCharacterSheet(${actor.id},'inventory').then(()=>true)`);
        await page.evaluate("window.openInventoryTemplatePicker('pockets').then(()=>true)");
        assert.ok(await page.evaluate("(()=>{const r=document.querySelector('#inventory-template-picker-modal .modal-content').getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth})()"),'Каталог помещается '+width);
        await page.evaluate("document.querySelector('#inventory-template-picker-modal .close').click();true");
    }
    fs.writeFileSync(path.join(safeSession.artifacts,'desktop-layout.json'),JSON.stringify(results,null,2));
    console.log('Матрица экрана: экипировка со всеми быстрыми слотами, силуэт, длинные названия, независимость левой части и каталог — 1920×1080, 1600×900, 1366×768, 1280×720.');
};
