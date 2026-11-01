const assert = require('node:assert/strict');

module.exports = async ({ page, request, token, lobby }) => {
    const character = await request(`/lobbies/${lobby.id}/characters`, {
        name: 'Проверка кнопок навыков', data: {
            skills: { skillPoints: 3, physical: { will: { base: 12, bonus: 0, xp: 0 } } },
        },
    }, token);
    const root = '#sheet-tab-skills';
    const input = skill => `${root} [name="skills.physical.${skill}.base"]`;
    const button = (skill, action) => `${root} button[onclick="${action}('physical.${skill}')"]`;
    const click = async selector => {
        await page.until(`document.querySelector(${JSON.stringify(selector)})?.getClientRects().length`, 'кнопка навыка видна');
        const point = await page.evaluate(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
        await page.call('Input.dispatchMouseEvent', { type: 'mousePressed', ...point, button: 'left', buttons: 1, clickCount: 1 });
        await page.call('Input.dispatchMouseEvent', { type: 'mouseReleased', ...point, button: 'left', buttons: 0, clickCount: 1 });
    };
    const saved = expression => page.until(`(async()=>{const r=await fetch('/lobbies/characters/${character.id}',{headers:{Authorization:'Bearer '+localStorage.getItem('access_token')}});const data=(await r.json()).data;return ${expression}})()`, 'изменение навыка сохранено');
    const open = async () => {
        await page.evaluate(`window.openCharacterSheet(${character.id}, 'skills')`);
        await page.until(`document.querySelector(${JSON.stringify(input('will'))})?.value==='12' || document.querySelector(${JSON.stringify(input('will'))})?.value==='13'`, 'вкладка навыков открыта');
        await click('[data-tab="skills"]');
    };
    await open();
    assert.equal(await page.evaluate(`document.querySelector(${JSON.stringify(input('strength'))}).value`), '5');
    await click(button('strength', 'addSkillXpFromPoints'));
    await saved('data.skills.physical.strength?.base===6 && data.skills.skillPoints===2');

    for (let xp = 1; xp <= 2; xp++) {
        await click(button('will', 'addSkillXpFromUse'));
        await saved(`data.skills.physical.will.base===12 && data.skills.physical.will.xp===${xp}`);
    }
    await click(button('will', 'addSkillXpFromUse'));
    await saved('data.skills.physical.will.base===13 && data.skills.physical.will.xp===0 && data.skills.skillPoints===2');
    await click(button('will', 'addSkillXpFromPoints'));
    await saved('data.skills.physical.will.base===13 && data.skills.physical.will.xp===1 && data.skills.skillPoints===1');

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
    await saved('data.skills.physical.will.xp===1 && data.skills.skillPoints===1');
    await page.evaluate('window.closeCharacterSheet()');
    console.log('PASS: кнопки навыков, отсутствующий навык, 12→13 за 3 XP, бросок, владение и reload');
};
