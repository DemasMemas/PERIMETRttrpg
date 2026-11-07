// Реальные b14/b15: раздельные данные, одна книга, оба профиля после взлома, очистка.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,peer,request,playerToken,player,safeSession,bookName})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'electronics');assert.ok(safeSession.verified);
    const tab=async(client,name)=>client.evaluate(`[...document.querySelectorAll('#pda-shell-modal .pda-shell-nav button')].find(b=>b.textContent===${JSON.stringify(name)}).click();true`);
    const profile=async(client,id)=>client.evaluate(`document.querySelector('[data-pda-profile="${id}"]').click();true`);
    await page.evaluate("document.querySelector('[data-pda-account]').click();true");
    await profile(page,'decoy');
    await page.until("document.querySelector('.pda-account-form')",'У ложного профиля свой вход');
    await page.evaluate("document.querySelector('.pda-account-mode-link').click();true");
    await page.evaluate("document.querySelector('[aria-label=\"Логин КПК\"]').value='pda-decoy-browser';document.querySelector('[aria-label=\"Пароль КПК\"]').value='synthetic-secret';document.querySelector('.pda-account-form').requestSubmit();true");
    await page.until("document.querySelector('[data-pda-account]')?.textContent.includes('pda-decoy-browser')&&!document.querySelector('.pda-account-form')",'Ложный аккаунт действительно создан');
    await tab(page,'Заметки');
    await page.until("document.querySelector('.pda-shell-main .pda-account-notes')",'Заметки ложного профиля');
    await page.evaluate("document.querySelector('.pda-shell-main .pda-account-notes').value='Ложные данные';[...document.querySelectorAll('.pda-shell-main button')].find(b=>b.textContent==='Сохранить заметки').click();true");
    await page.until("[...document.querySelectorAll('.pda-shell-main button')].some(b=>b.textContent==='Сохранить заметки'&&!b.disabled)",'Ложные заметки сохранены');
    await tab(page,'Файлы');await page.until("document.querySelector('.pda-shell-main')?.textContent.includes('В КПК пока нет книг')",'Настоящая книга скрыта в ложном списке');
    await profile(page,'main');
    await page.until(`document.querySelector('.pda-shell-main')?.textContent.includes(${JSON.stringify(bookName)})`,'Основной список сохранил оригинальный файл');
    await page.evaluate("[...document.querySelectorAll('.pda-shell-main button')].find(b=>b.textContent==='Перенести в ложный профиль').click();true");
    await page.until(`!document.querySelector('.pda-shell-main')?.textContent.includes(${JSON.stringify(bookName)})`,'Перенос изменил видимость, без копирования');
    await profile(page,'decoy');
    await page.until(`document.querySelector('.pda-shell-main')?.textContent.includes(${JSON.stringify(bookName)})`,'Файл появился только в ложном списке');
    const dimensions=[];
    for(const [width,height]of [[1920,1080],[1600,900],[1366,768],[1280,720]]){
        await page.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
        const box=await page.evaluate("(()=>{const root=document.querySelector('.pda-shell'),r=root.getBoundingClientRect();return {viewport:[innerWidth,innerHeight],x:r.x,y:r.y,right:r.right,bottom:r.bottom,scrollWidth:root.scrollWidth,clientWidth:root.clientWidth,buttons:['[data-pda-close]','.pda-shell-footer','[data-pda-profile=main]','[data-pda-profile=decoy]'].map(selector=>{const b=root.querySelector(selector).getBoundingClientRect();return {selector,x:b.x,y:b.y,right:b.right,bottom:b.bottom}})}})()");
        dimensions.push(box);assert.ok(box.x>=0&&box.y>=0&&box.right<=width+1&&box.bottom<=height+1&&box.scrollWidth<=box.clientWidth+2);
        assert.ok(box.buttons.every(b=>b.x>=box.x&&b.right<=box.right+1&&b.y>=box.y&&b.bottom<=box.bottom+1),'Рамка, оба профиля и нижние кнопки КПК доступны');
        if(width===1280){const shot=await page.call('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(safeSession.artifacts,'pda-profiles-1280x720.png'),Buffer.from(shot.data,'base64'));}
    }
    fs.writeFileSync(path.join(safeSession.artifacts,'pda-profiles-layout.json'),JSON.stringify(dimensions,null,2));
    await peer.evaluate("[...document.querySelectorAll('.pda-access button')].find(b=>b.textContent==='Обновить устройство').click();true");
    await peer.until("document.querySelector('.pda-access [aria-label=\"Профили подключённого КПК\"]')",'Взломщик видит оба профиля');
    await peer.evaluate("[...document.querySelectorAll('.pda-access-tabs button')].find(b=>b.textContent==='Аккаунт и заметки').click();true");
    await peer.until("document.querySelector('[aria-label=\"Заметки подключённого КПК\"]')?.value==='Ложные данные'",'Взломщик читает ложные данные');
    await peer.evaluate("[...document.querySelectorAll('.pda-access [aria-label=\"Профили подключённого КПК\"] button')].find(b=>b.textContent==='Основной профиль').click();true");
    await peer.until("document.querySelector('[aria-label=\"Заметки подключённого КПК\"]')?.value==='Запись через взломанный КПК'",'Основной профиль тоже доступен после взлома');
    await tab(page,'Подключения');
    return async()=>{
        await tab(page,'Модификации');
        await page.until("[...document.querySelectorAll('.pda-shell-main button')].some(b=>b.textContent==='Чистая запись · очистить КПК')",'Установленная программа очистки доступна');
        await page.evaluate("[...document.querySelectorAll('.pda-shell-main button')].find(b=>b.textContent==='Чистая запись · очистить КПК').click();true");
        assert.ok(await page.evaluate("document.querySelector('[data-pda-wipe]')&&document.querySelector('.pda-shell-main')?.textContent.includes('аккаунты обоих профилей')"),'Перед удалением видны конкретные последствия');
        await page.evaluate("document.querySelector('[data-pda-wipe]').click();true");
        await page.until("document.querySelector('[data-pda-account]')?.textContent.includes('(гость)')&&!document.querySelector('[data-pda-wipe]')",'Очистка завершилась, сохранённого входа нет');
        const fresh=await request('/lobbies/characters/'+player.id,null,playerToken,'GET'),pda=fresh.data.inventory.pockets.find(item=>item.id==='radio-player');
        assert.equal(pda.contents.length,0);assert.equal(pda.pdaNetwork.account_id,null);
        assert.ok(['pda-b14','pda-b15','pda-b24','pda-b5'].every(rule=>pda.modifications.some(mod=>mod.ruleId===rule)),'Модификации сохранены');
        await tab(page,'Файлы');await page.until("document.querySelector('.pda-shell-main')?.textContent.includes('В КПК пока нет книг')",'Удалённые книги больше не показываются');
        console.log('КПК b14/b15: свой ложный аккаунт и заметки; книга физически одна, отдельные списки; взломщик читает оба профиля; очистка удаляет данные и сохраняет устройство/модификации. Четыре настольных размера.');
    };
};
