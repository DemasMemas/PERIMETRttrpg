const assert = require('node:assert/strict');

module.exports = async ({ page, request, token }) => {
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
};
