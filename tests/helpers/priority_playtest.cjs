// Ручная приёмка №102 и №105 на локальном сервере с отдельной БД.
const assert = require('node:assert/strict');
const path = require('node:path');
const {randomUUID} = require('node:crypto');
const {createSession, cleanEnvironment} = require('./browser_isolation.cjs');
const {loadFixture} = require('../durable_reload.browser.cjs');

const root = path.resolve(__dirname, '../..');
const port = 8768;
const password = 'Perimetr-Playtest';

async function prepare(base) {
    async function request(route, data, token, method = 'POST') {
        const response = await fetch(base + route, {method, signal: AbortSignal.timeout(25000), headers: {
            'Content-Type': 'application/json', ...(token ? {Authorization: `Bearer ${token}`} : {}),
        }, body: data === undefined ? undefined : JSON.stringify(data)});
        assert.ok(response.ok, `${method} ${route}: ${response.status}`);
        return response.status === 204 ? null : response.json();
    }
    async function account(username) {
        await request('/auth/register', {username, email: `${username}@example.test`, password});
        return request('/auth/login', {username, password});
    }
    const gm = await account('playtest-gm');
    const player = await account('playtest-player');
    const token = gm.access_token;
    const lobby = await request('/lobbies/', {name: 'Приёмка №102 и №105', map_type: 'empty', chunks_width: 1, chunks_height: 1}, token);
    const api = `/lobbies/${lobby.id}`;
    await request(api + '/join', {}, player.access_token);
    const {fixture} = loadFixture();

    async function scene(name, marker, configure) {
        const copy = structuredClone(fixture);
        configure(copy);
        const {objects, ...spec} = copy;
        const location = await request(api + '/locations', {...spec, name, world_tile_x: marker, world_tile_z: 7}, token);
        const url = api + '/locations/' + location.id;
        await request(url + '/objects/batch', {objects}, token);
        return {url, href: base + api + '/page?location=' + location.id};
    }
    async function actor(location, name, x, y, data = {}, control = false) {
        const character = await request(api + '/characters', {name, data}, token);
        const placed = await request(location.url + '/spawn_character', {character_id: character.id,
            tile_x: x, tile_y: y, ...(control ? {assign_to_user_id: player.user_id} : {}),
        }, token);
        return {id: character.id, locationId: placed.location_character_id};
    }
    async function start(location, first, ...other) {
        await request(location.url + '/characters/' + first.id + '/facing', {facing_x: 1, facing_y: 0}, token, 'PATCH');
        await request(location.url + '/combat/start', {location_character_ids: [first, ...other].map(entry => entry.locationId),
            initiator_location_character_id: first.locationId, initiator_first: true}, token);
    }

    const surface = await scene('№102 · Граница снега', 7, ({tiles_data: tiles}) => {
        for (const row of tiles) {
            for (const [x, terrain] of [[42, 'snow'], [43, 'sand'], [44, 'swamp']]) {
                row[x].terrain = terrain;
                delete row[x].difficult_surface;
            }
        }
    });
    await start(surface, await actor(surface, '№102 Проводник', 41, 48, {}, true));

    async function sight(name, marker, hill) {
        const location = await scene(name, marker, ({tiles_data: tiles}) => {
            if (hill) for (let row = 19; row <= 21; row++) tiles[row][36].height = 7;
        });
        const rifle = {id:'priority-test-rifle',name:'Проверочная винтовка',category:'weapon',weight:1,ergonomics:0,
            accuracy:0,range:20,durability:100,maxDurability:100,ammo:3,fireModes:{single_shot_options:[1]},
            installedMagazine:{id:'priority-test-mag',ammo:[{id:'priority-test-ammo',name:'5.56x45',quantity:3,category:'ammo',
                attributes:{caliber:'5.56x45',damage:10,armor_piercing:0}}]}};
        const shooter = await actor(location, `№105 Стрелок (${hill ? 'холм' : 'открыто'})`, 33, 20, {
            health:{current:700,max:700,effects:[]},combat:{action_points:12},
            skills:{physical:{shooting:{base:20,bonus:0},strength:{base:10,bonus:0}}},weapons:[rifle],
        }, true);
        const target = await actor(location, '№105 Цель', 39, 20, {health:{current:700,max:700,effects:[]}});
        await start(location, shooter, target);
        for (const action of [{action_key:'draw_weapon',weapon_index:0},
            {action_key:'aim',weapon_index:0,target_character_id:target.id}]) {
            await request(location.url + '/combat/action', {location_character_id: shooter.locationId, ...action}, token);
        }
        return location;
    }
    const hill = await sight('№105 · Холм между стрелком и целью', 8, true);
    const clear = await sight('№105 · Открытая линия выстрела', 9, false);
    return {surface: surface.href, hill: hill.href, clear: clear.href};
}

async function main(args) {
    assert.ok(args.length === 1 && ['--start', '--check'].includes(args[0]),
        'Запуск: node tests/helpers/priority_playtest.cjs --start');
    const session = createSession({...cleanEnvironment(process.env), VTT_SAFE_RUN_ID: randomUUID(),
        VTT_SAFE_SCENARIO: 'terrain', VTT_BROWSER_GPU: '1', VTT_SAFE_DB_MODE: 'owned-file',
        VTT_SAFE_DB_CONFIRMATION: '--confirm-isolated-browser', VTT_MOVEMENT_ONLY: '1'});
    const server = session.spawnServer(path.join(root, '.venv/Scripts/python.exe'), port);
    let errors = '';
    server.stdout.resume();
    server.stderr.on('data', chunk => { errors = (errors + chunk).slice(-4000); });
    try {
        await session.ready();
        const links = await prepare(session.base);
        console.log(`Вход: ${session.base} · playtest-gm или playtest-player · пароль ${password}`);
        console.log(`№102 · Снег: ${links.surface}\n№105 · Холм: ${links.hill}\n№105 · Без холма: ${links.clear}`);
        console.log(`Идентификатор отдельной БД: ${session.runId}`);
        if (args[0] === '--start') {
            console.log('Enter в этом окне остановит тестовый сервер.');
            await new Promise(resolve => {
                const stop = () => {
                    process.stdin.off('data', stop);
                    process.off('SIGINT', stop);
                    process.off('SIGTERM', stop);
                    server.off('close', stop);
                    process.stdin.pause();
                    resolve();
                };
                process.stdin.once('data', stop);
                process.once('SIGINT', stop);
                process.once('SIGTERM', stop);
                server.once('close', stop);
            });
        }
    } catch (error) {
        if (errors) console.error(errors);
        throw error;
    } finally {
        await session.cleanup(null, server);
    }
}

main(process.argv.slice(2)).catch(error => { console.error(error.message); process.exitCode = 1; });
