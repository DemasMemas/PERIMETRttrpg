const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

module.exports = async ({ page, request, token, safeSession }) => {
    const payload = { name: 'Авторский набор — проверочная копия', map_type: 'empty',
        chunks_width: 1, chunks_height: 1, faction_source: 'ready' };
    const world = await request('/lobbies/', payload, token);
    const url = `/lobbies/${world.id}/world-factions`;
    const ready = await request(url, null, token, 'GET');
    assert.equal(ready.source, 'ready');
    assert.equal(ready.groups.length, 13);
    assert.equal(ready.relations.length, 34);
    await page.evaluate(`(async()=>{
        const {openWorldFactionSettings}=await import('/static/js/worldFactions.js');
        await openWorldFactionSettings(${world.id});return true;
    })()`);
    await page.until('document.querySelector(".world-faction-modal .faction-copy-ready")?.hidden===false',
        'готовый набор открыт');
    assert.equal(await page.evaluate('document.querySelector(".world-faction-modal .faction-group-name").disabled'),true);
    await page.evaluate('document.querySelector(".world-faction-modal .faction-copy-ready").click();true');
    assert.equal(await page.evaluate('document.querySelector(".world-faction-modal .faction-group-name").disabled'),false);
    await page.evaluate(`(()=>{const input=document.querySelector('.world-faction-modal .faction-group-name');
        input.value='Название ГМа';input.dispatchEvent(new Event('input',{bubbles:true}));
        document.querySelector('.world-faction-modal .faction-save').click();return true})()`);
    await page.until('document.querySelector(".world-faction-modal .faction-error").textContent.includes("сохранены")',
        'копия авторского набора сохранена');
    const copy = await request(url, null, token, 'GET');
    assert.equal(copy.source, 'ready_customized');
    assert.equal(copy.groups[0].name, 'Название ГМа');
    assert.equal(copy.groups[0].id, ready.groups[0].id);
    const another = await request('/lobbies/', { ...payload, name: 'Второй готовый мир' }, token);
    const original = await request(`/lobbies/${another.id}/world-factions`, null, token, 'GET');
    assert.deepEqual(original.groups, ready.groups);
    assert.deepEqual(original.relations, ready.relations);
    console.log(JSON.stringify({ readyWorldFactions: true, groups: 13, relations: 34,
        copiedWithoutSourceMutation: true }));
    await page.evaluate('document.querySelector(".world-faction-modal .faction-close").click();true');
    await page.evaluate(`(async()=>{const {openSettings}=await import('/static/js/ui.js');
        openSettings();return true})()`);
    for (const width of [1280, 480, 320]) {
        await page.call('Emulation.setDeviceMetricsOverride',
            {width,height:900,deviceScaleFactor:1,mobile:false});
        const layout=await page.evaluate(`(()=>{const panel=document.querySelector('#settings-panel');
            const tabs=panel.querySelector('.settings-tabs');
            const bounds=panel.getBoundingClientRect();
            const close=panel.querySelector('.settings-header button').getBoundingClientRect();
            return {panelWithinViewport:bounds.left>=0&&bounds.right<=innerWidth,
                tabsBelowClose:tabs.getBoundingClientRect().top>=close.bottom-1,
                allTabsInside:[...tabs.querySelectorAll('button')].every(button=>{
                    const box=button.getBoundingClientRect();
                    return box.left>=bounds.left&&box.right<=bounds.right&&box.bottom<=bounds.bottom;
                })};})()`);
        assert.deepEqual(layout,{panelWithinViewport:true,tabsBelowClose:true,allTabsInside:true});
        if (width===480 && safeSession) {
            await page.evaluate(`document.querySelectorAll('.notification-stack').forEach(stack=>stack.remove());true`);
            const shot=await page.call('Page.captureScreenshot',{format:'png'});
            fs.writeFileSync(path.join(safeSession.artifacts,'settings-tabs-480.png'),
                Buffer.from(shot.data,'base64'));
        }
    }
    await page.evaluate(`(()=>{document.querySelector('[data-settings-tab="export"]').focus();return true})()`);
    assert.equal(await page.evaluate(`document.activeElement?.dataset.settingsTab`),'export');
    await page.call('Input.dispatchKeyEvent',
        {type:'rawKeyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
    await page.call('Input.dispatchKeyEvent',
        {type:'char',key:'Enter',code:'Enter',text:'\r',windowsVirtualKeyCode:13});
    await page.call('Input.dispatchKeyEvent',
        {type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
    assert.equal(await page.evaluate(`document.querySelector('#export-settings').style.display`),'block');
    await page.evaluate(`document.querySelector('#settings-panel .settings-header button').click();true`);
    await page.call('Emulation.clearDeviceMetricsOverride');
    console.log(JSON.stringify({settingsTabsVisibleAt:[1280,480,320],keyboardTab:true}));
};
