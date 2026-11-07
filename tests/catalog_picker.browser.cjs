// №207: настоящая выдача каталога со старыми дубликатами, без миграции сохранённых вещей.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {loadFixture,waitForDocument}=require('./durable_reload.browser.cjs');
module.exports=async({page,request,token,lobby,base,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'catalog');assert.ok(safeSession.verified);
    const api=`/lobbies/${lobby.id}`,{fixture}=loadFixture();
    const location=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:7,world_tile_z:8},token),loc=api+'/locations/'+location.id;
    await request(loc+'/objects/batch',{objects:fixture.objects},token);
    const actor=await request(api+'/characters',{name:'Каталог без дублей',data:{inventory:{pockets:[{
        id:'preserved-legacy-grenade',templateId:156,name:'РГД-5',category:'grenade',quantity:1,weight:.3,volume:1,attributes:{}}],backpack:[]}}},token);
    await request(loc+'/spawn_character',{character_id:actor.id,tile_x:41,tile_y:48},token);
    const href=base+api+'/page?location='+location.id,epoch=await page.evaluate('performance.timeOrigin');
    await page.call('Page.navigate',{url:href});await waitForDocument(page,{href,afterEpoch:epoch,label:'Каталог на копии Припяти'});
    await page.until(`window.currentLocationId===${location.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Припять готова');
    await page.evaluate(`window.openCharacterSheet(${actor.id},'inventory').then(()=>true)`);
    await page.until("document.getElementById('character-sheet-modal')",'Лист открыт');
    await page.evaluate("window.openInventoryTemplatePicker('pockets').then(()=>true)");
    await page.until("document.querySelector('#inventory-template-picker-content [data-item-template-id]')",'Каталог загружен настоящим API');
    const offered=await request(api+'/templates',null,token,'GET'),grenades=offered.global.filter(t=>t.category==='grenade');
    const displayed=await page.evaluate("[...document.querySelectorAll('#inventory-template-picker-content [data-item-template-id]')].map(b=>({id:Number(b.dataset.itemTemplateId),name:b.firstElementChild.firstElementChild.textContent.trim(),text:b.textContent.trim()}))");
    assert.ok(grenades.length>10);assert.equal(new Set(grenades.map(t=>t.name)).size,grenades.length);
    assert.ok(!displayed.some(row=>row.id>=156&&row.id<200||row.id>=9500&&row.id<=9508),'Старые шаблоны не предлагаются');
    for(const grenade of grenades)assert.equal(displayed.filter(row=>row.id===grenade.id).length,1,grenade.name);
    for(const name of ['Вещевой подсумок — 1','Медицинские','ДА-2','Барьер'])assert.equal(displayed.filter(row=>row.name===name).length,1,name);
    assert.ok(!displayed.some(row=>/ID -\d/.test(row.text)),'Служебные отрицательные номера не выводятся');
    const labels=await page.evaluate("document.getElementById('inventory-template-picker-content').textContent");
    assert.ok(labels.includes('Шлемы')&&labels.includes('ПНВ и фонари')&&labels.includes('Детекторы'));
    assert.ok(labels.includes('Баки и Баллоны Огнеметов')&&!labels.includes('Модули шлемов'));
    const filtered=await request(api+'/templates?category=grenade',null,token,'GET');
    assert.deepEqual(filtered.global.map(t=>t.id).sort((a,b)=>a-b),grenades.map(t=>t.id).sort((a,b)=>a-b));
    await page.evaluate("(()=>{const q=document.getElementById('inventory-template-picker-search');q.value='РГД-5';q.dispatchEvent(new Event('input'))})()");
    await page.until("document.querySelectorAll('#inventory-template-picker-content [data-item-template-id]').length===1",'Поиск РГД-5 возвращает одну каноническую гранату');
    for(const [width,height]of [[1920,1080],[1366,768]]){
        await page.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
        assert.ok(await page.evaluate("(()=>{const r=document.querySelector('#inventory-template-picker-modal .modal-content').getBoundingClientRect(),q=document.getElementById('inventory-template-picker-search').getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight&&q.top>=r.top&&q.bottom<=r.bottom})()"),'Окно и поиск видимы на '+width+'×'+height);
    }
    const shot=await page.call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(safeSession.artifacts,'catalog-picker.png'),Buffer.from(shot.data,'base64'));
    await page.evaluate("document.querySelector('#inventory-template-picker-content [data-item-template-id]').click();true");
    await page.until(`(async()=>{const result=await(await import('/static/js/api.js')).Server.getCharacter(${actor.id});return result.data.inventory.pockets.some(item=>item.templateId===${grenades.find(t=>t.name==='Граната РГД-5').id})})()`,'Каноническая граната добавлена и сохранена');
    const fresh=await request('/lobbies/characters/'+actor.id,null,token,'GET'),items=fresh.data.inventory.pockets;
    assert.ok(items.some(item=>item.id==='preserved-legacy-grenade'&&item.templateId===156&&item.name==='РГД-5'&&item.quantity===1),'Старая сохранённая граната не потеряна и не переписана');
    assert.ok(items.some(item=>item.templateId===grenades.find(t=>t.name==='Граната РГД-5').id&&item.quantity===1),'Новая выдача использует канонический шаблон');
    console.log('Каталог: старые и новые гранаты/подсумки/перчатки/детекторы/контейнеры вместе в тестовой БД; в интерфейсе один вариант, новая выдача каноническая, старый предмет сохранён. Окно 1920×1080 и 1366×768.');
};
