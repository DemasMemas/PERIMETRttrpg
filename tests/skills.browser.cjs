const assert = require('node:assert/strict');

module.exports = async ({ page, request, token, lobby, base, connect, debugPort, safeSession }) => {
    const character = await request(`/lobbies/${lobby.id}/characters`, {
        name: 'Проверка кнопок навыков', data: {
            skills: { skillPoints: 3, physical: { will: { base: 12, bonus: 0, xp: 0 } } },
        },
    }, token);
    const root = '#sheet-tab-skills';
    const input = skill => `${root} [name="skills.physical.${skill}.base"]`;
    const button = (skill, action) => `${root} button[onclick="${action}('physical.${skill}')"]`;
    const click = async (selector,client=page) => {
        await client.until(`document.querySelector(${JSON.stringify(selector)})?.getClientRects().length`, 'кнопка навыка видна');
        const point = await client.evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.scrollIntoView({block:'nearest'});const r=e.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
        await client.call('Input.dispatchMouseEvent', { type: 'mousePressed', ...point, button: 'left', buttons: 1, clickCount: 1 });
        await client.call('Input.dispatchMouseEvent', { type: 'mouseReleased', ...point, button: 'left', buttons: 0, clickCount: 1 });
    };
    const saved = expression => page.until(`(async()=>{const r=await fetch('/lobbies/characters/${character.id}',{headers:{Authorization:'Bearer '+localStorage.getItem('access_token')}});const data=(await r.json()).data;return ${expression}})()`, 'изменение навыка сохранено');
    const open = async (client=page) => {
        await client.evaluate(`window.openCharacterSheet(${character.id}, 'skills')`);
        await client.until(`document.querySelector(${JSON.stringify(input('will'))})?.getClientRects().length`, 'вкладка навыков открыта');
        await click('[data-tab="skills"]',client);
    };
    await open();
    const reward = '#session-reward-modal';
    const openReward = async () => {
        await click(`${root} button[onclick="openSessionReward()"]`);
        await page.until(`document.querySelector('${reward} form')`,'Окно награды открыто');
    };
    const training = async (skill = 'physical.will') => {
        await openReward();
        await page.evaluate(`(()=>{const input=document.querySelector('${reward} select');input.value=${JSON.stringify(skill)};
            input.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
        assert.equal(await page.evaluate(`Boolean(document.querySelector('${reward} [name=usedBackgroundWeapon]'))`),false);
        await click(`${reward} button[type=submit]`);
        await page.until(`!document.querySelector('${reward}')`,'Награда начислена');
    };
    assert.equal(await page.evaluate(`Boolean(document.querySelector(${JSON.stringify(button('will','addSkillXpFromUse'))}))`),false);
    const typeNumber=async(selector,value)=>{
        await click(selector);
        await page.call('Input.dispatchKeyEvent',{type:'rawKeyDown',key:'a',code:'KeyA',windowsVirtualKeyCode:65,nativeVirtualKeyCode:65,modifiers:2});
        await page.call('Input.dispatchKeyEvent',{type:'keyUp',key:'a',code:'KeyA',windowsVirtualKeyCode:65,nativeVirtualKeyCode:65,modifiers:2});
        await page.call('Input.insertText',{text:String(value)});
        assert.equal(await page.evaluate(`document.querySelector(${JSON.stringify(selector)}).value`),String(value),'Нативный ввод заменяет прежнее число');
    };
    await typeNumber(input('will'),14);
    assert.ok(await page.evaluate(`document.querySelector(${JSON.stringify(input('will'))}).parentElement.textContent.includes('0/5')`),'Ручной ввод сразу обновляет цену следующего уровня');
    await training();
    await saved('data.skills.physical.will.base===14 && data.skills.physical.will.xp===1');
    await typeNumber(input('will'),12);
    await page.call('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab'});
    await saved('data.skills.physical.will.base===12 && data.skills.physical.will.xp===0');
    assert.equal(await page.evaluate(`document.querySelector(${JSON.stringify(input('strength'))}).value`), '5');
    await click(button('strength', 'addSkillXpFromPoints'));
    await saved('data.skills.physical.strength?.base===6 && data.skills.skillPoints===3');

    for (let xp = 1; xp <= 2; xp++) {
        await training();
        await saved(`data.skills.physical.will.base===12 && data.skills.physical.will.xp===${xp}`);
    }
    await training();
    await saved('data.skills.physical.will.base===13 && data.skills.physical.will.xp===0 && data.skills.skillPoints===6');
    await click(button('will', 'addSkillXpFromPoints'));
    await saved('data.skills.physical.will.base===13 && data.skills.physical.will.xp===1 && data.skills.skillPoints===5');

    await click(`${root} [onclick="window.rollSkill('physical.will', 'Воля')"]`);
    await page.until(`[...document.querySelectorAll('#chat .message')].some(m=>m.textContent.includes('Воля: бросок'))`, 'бросок навыка выполнен');
    await click(`${root} button[onclick="addWeaponXp('pistols')"]`);
    await saved('data.skills.specialized.pistols.xp===1');

    await page.evaluate('window.__skillsBeforeReload = true');
    await page.call('Page.reload');
    await page.until('!window.__skillsBeforeReload && Boolean(window.openCharacterSheet && window.isGM)', 'страница загружена повторно');
    await open();
    assert.equal(await page.evaluate(`document.querySelector(${JSON.stringify(input('strength'))}).value`), '6');
    assert.equal(await page.evaluate(`document.querySelector(${JSON.stringify(input('will'))}).value`), '13');
    await saved('data.skills.physical.will.xp===1 && data.skills.skillPoints===5');
    // Второй клиент получает настоящие обновления Socket.IO и также может редактировать лист.
    await request('/auth/register',{username:'skills-peer',email:'skills-peer@example.test',password:'skills-test-password'});
    const player=await request('/auth/login',{username:'skills-peer',password:'skills-test-password'});
    await request(`/lobbies/${lobby.id}/join`,{},player.access_token);
    await request(`/lobbies/characters/${character.id}`,{visible_to:[player.user_id],editable_to:[player.user_id]},token,'PUT');
    const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();
    const protocol=await connect(version.webSocketDebuggerUrl);
    const {browserContextId}=await protocol.call('Target.createBrowserContext');
    const {targetId}=await protocol.call('Target.createTarget',{url:base,browserContextId});
    const tabs=await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
    const peer=await connect(tabs.find(t=>t.id===targetId).webSocketDebuggerUrl);
    try{
        await peer.call('Page.enable');await peer.call('Runtime.enable');
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(player.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(player.user_id))})`);
        await peer.call('Page.navigate',{url:`${base}/lobbies/${lobby.id}/page`});
        await peer.until('Boolean(window.openCharacterSheet)&&!window.isGM','клиент игрока открыт');
        await open(peer);
        await page.evaluate(`(()=>{const original=window.fetch;window.fetch=async(...args)=>{
            if(String(args[0]).endsWith('/characters/${character.id}')&&args[1]?.method==='PUT'&&!window.skillRequestHeld){
                window.skillRequestHeld=true;await new Promise(resolve=>window.releaseSkillRequest=resolve);
            }return original(...args);
        };window.restoreSkillFetch=()=>window.fetch=original;})()`);
        await typeNumber(input('will'),15);
        await page.until('Boolean(window.releaseSkillRequest)','первое сохранение задержано');
        await typeNumber(input('will'),14);
        await page.evaluate('releaseSkillRequest();restoreSkillFetch()');
        await saved('data.skills.physical.will.base===14 && data.skills.physical.will.xp===0');
        await training();
        await saved('data.skills.physical.will.base===14 && data.skills.physical.will.xp===1');
        await peer.until(`document.querySelector(${JSON.stringify(input('will'))}).value==='14'&&document.querySelector('[data-skill-progress="physical.will"]').textContent.includes('1/5')`,'второй клиент получил уровень и опыт');
        // Пустой ввод не превращается в ноль и не меняет сохранённый прогресс.
        await click(input('will'));
        await page.call('Input.dispatchKeyEvent',{type:'rawKeyDown',key:'a',code:'KeyA',windowsVirtualKeyCode:65,modifiers:2});
        await page.call('Input.dispatchKeyEvent',{type:'keyUp',key:'a',code:'KeyA',windowsVirtualKeyCode:65,modifiers:2});
        await page.call('Input.dispatchKeyEvent',{type:'rawKeyDown',key:'Backspace',code:'Backspace',windowsVirtualKeyCode:8});
        await page.call('Input.dispatchKeyEvent',{type:'keyUp',key:'Backspace',code:'Backspace',windowsVirtualKeyCode:8});
        await click('[data-tab="skills"]');
        assert.equal(await page.evaluate(`document.querySelector(${JSON.stringify(input('will'))}).value`),'14');
        await saved('data.skills.physical.will.base===14 && data.skills.physical.will.xp===1');
        await request(`/lobbies/characters/${character.id}`,{visible_to:[player.user_id],editable_to:[]},token,'PUT');
        await peer.evaluate('window.closeCharacterSheet()');await open(peer);
        assert.equal(await peer.evaluate(`document.querySelector(${JSON.stringify(input('will'))}).disabled`),true);
        const draft=(await request(`/lobbies/characters/${character.id}`,null,token,'GET')).data;
        draft.skills.physical.will.base=20;
        const denied=await fetch(`${base}/lobbies/characters/${character.id}`,{method:'PUT',headers:{'Content-Type':'application/json',Authorization:`Bearer ${player.access_token}`},body:JSON.stringify({data:draft})});
        assert.equal(denied.status,403);
        const fs=require('node:fs'),path=require('node:path');
        fs.writeFileSync(path.join(safeSession.artifacts,'skills.json'),JSON.stringify({manualLevel:true,xpReset:true,sessionTraining:true,slowSavePreservesTyping:true,twoClients:true,invalidInput:true,viewerRejected:true,reload:true},null,2));
    }finally{await peer.close();await protocol.call('Target.disposeBrowserContext',{browserContextId});await protocol.close();}
    await page.evaluate('window.closeCharacterSheet()');
    const security = await request(`/lobbies/${lobby.id}/characters`, {
        name: 'Проверка награды СБшника',
        data: { basic: { background: { name: 'СБшник', ruleId: 'background-af', choices: { backgroundWeapon: 'pistols' } } } },
    }, token);
    await page.evaluate(`window.openCharacterSheet(${security.id}, 'skills')`);
    await openReward();
    const weapon = `${reward} [name=usedBackgroundWeapon]`;
    assert.equal(await page.evaluate(`document.querySelector('${weapon}')?.getClientRects().length`),0);
    await page.evaluate(`(()=>{const select=document.querySelector('${reward} select');
        select.value='physical.shooting';select.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
    assert.equal(await page.evaluate(`Boolean(document.querySelector('${reward} [name=weaponType]'))`),false);
    await click(weapon);
    const fs = require('node:fs'), path = require('node:path');
    const shot = await page.call('Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(safeSession.artifacts,'session-reward.png'),Buffer.from(shot.data,'base64'));
    await click(`${reward} button[type=submit]`);
    await page.until(`!document.querySelector('${reward}')`,'Награда за стрельбу начислена');
    const latest = await request(`/lobbies/characters/${security.id}`,null,token,'GET');
    assert.equal(latest.data.characterRewards.at(-1).trainingPoints,2);
    assert.deepEqual(latest.data.characterRewards.at(-1).rolls,[]);
    await click(`${root} button[onclick="addSpecialTrait()"]`);
    const traits = `${root} #special-traits-container`;
    await page.until(`document.querySelectorAll('${traits} select option').length>1`,'Каталог особых черт открыт');
    const selectedTrait = await page.evaluate(`(()=>{const select=document.querySelector('${traits} select');
        const option=[...select.options].find(o=>o.textContent==='Подержи моё пиво')||select.options[1];
        select.value=option.value;select.dispatchEvent(new Event('change',{bubbles:true}));return option.textContent;})()`);
    await page.until(`document.querySelector('${traits} strong')?.textContent===${JSON.stringify(selectedTrait)}`,'Черта отображается в листе');
    assert.equal(await page.evaluate(`document.querySelectorAll('${traits} input, ${traits} select, ${traits} textarea').length`),0);
    assert.match(await page.evaluate(`document.querySelector('${traits} small').textContent`),/Стоимость: .*Не более/);
    await page.until(`fetch('/lobbies/characters/${security.id}',{headers:{Authorization:'Bearer '+localStorage.getItem('access_token')}})
        .then(response=>response.json()).then(sheet=>sheet.data?.features?.specialTraits?.[0]?.name===${JSON.stringify(selectedTrait)})`,
    'Черта сохранена через автосохранение');
    await page.evaluate(`document.querySelector('${traits}').scrollIntoView({block:'center',behavior:'instant'}); new Promise(requestAnimationFrame)`);
    const traitShot = await page.call('Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(safeSession.artifacts,'special-traits.png'),Buffer.from(traitShot.data,'base64'));
    await page.evaluate('window.closeCharacterSheet()');
    console.log('PASS: ручной ввод, сброс опыта, кнопки, отложенный ответ, два клиента, права и reload');
    await require('./sheet_appearance.browser.cjs')({ page, request, token, lobby, base, safeSession });
};
