// Full scene regression with an isolated temporary SQLite database and Chrome.
// Run: node tests/world_object_editor.browser.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const net = require('node:net');
const { spawn } = require('node:child_process');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const root = path.resolve(__dirname, '..');
const isolation = require('./helpers/browser_isolation.cjs');
const safeSession = process.env.VTT_SAFE_RUN_ID ? isolation.createSession(process.env) : null;

async function connect(url) {
    const socket = new WebSocket(url);
    await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
    let nextId = 0;
    const waiting = new Map();
    const events = new Map();
    const networkEvents = [];
    socket.onclose = () => {
        for (const callback of waiting.values()) callback({ closed: true, result: {} });
        waiting.clear();
    };
    socket.onmessage = event => {
        const message = JSON.parse(event.data);
        if(message.method==='Fetch.requestPaused' && safeSession){
            safeSession.paused(message.params,call).catch(console.error);
        } else if(message.method==='Fetch.requestPaused'){
            const p=message.params,name=new URL(p.request.url).pathname.split('/').pop(),dir=new URL(p.request.url).pathname.startsWith('/static/js/')?process.env.VTT_WATER_BASELINE_DIR:process.env.VTT_BROWSER_VENDOR_DIR;
            const file=dir&&path.join(dir,name);
            (file&&fs.existsSync(file)?call('Fetch.fulfillRequest',{requestId:p.requestId,responseCode:200,responseHeaders:[{name:'Content-Type',value:'application/javascript'},{name:'Access-Control-Allow-Origin',value:'*'}],body:fs.readFileSync(file).toString('base64')}):call('Fetch.continueRequest',{requestId:p.requestId})).catch(console.error);
        }
        if (message.method) events.set(message.method, message.params);
        if (safeSession) {
            const diagnostic=isolation.networkDiagnostic(message.method,message.params||{});
            if(diagnostic&&networkEvents.length<2000)networkEvents.push(diagnostic);
        }
        if (process.env.VTT_NETWORK === '1' && /^(Network\.webSocket|Runtime\.exceptionThrown|Log\.entryAdded)/.test(message.method || '')) networkEvents.push(message);
        if (message.method === 'Runtime.exceptionThrown') console.error('Browser exception:', JSON.stringify(message.params.exceptionDetails));
        if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') {
            console.error('Browser console:', message.params.args.map(arg => arg.description || arg.value).join(' '));
        }
        if (message.method === 'Log.entryAdded' && message.params.entry.level === 'error') console.error('Browser log:', message.params.entry.url, message.params.entry.text);
        if (message.id) { waiting.get(message.id)?.(message); waiting.delete(message.id); }
    };
    const call = (method, params = {}) => new Promise((resolve, reject) => {
        const id = ++nextId;
        const timer = setTimeout(() => { waiting.delete(id); reject(new Error(`Timed out: ${method}`)); }, process.env.VTT_WATER_FINAL==='1'||process.env.VTT_WATER_GEOMETRY==='1'||process.env.VTT_SECONDARY_TOOLS==='1'||process.env.VTT_ROAD_TERRAIN==='1'||process.env.VTT_BRIDGE_BANK==='1'?90000:30000);
        waiting.set(id, message => {
            clearTimeout(timer);
            if (message.closed && method !== 'Browser.close') reject(new Error(`Browser connection closed: ${method}`));
            else message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result);
        });
        socket.send(JSON.stringify({ id, method, params }));
    });
    let vendorIntercepted = false;
    const evaluate = async expression => {
        if(process.env.VTT_BROWSER_VENDOR_DIR&&!vendorIntercepted) {
            await call('Fetch.enable',{patterns:[{urlPattern:'*/static/js/*',requestStage:'Request'},
                {urlPattern:'*unpkg.com/three@0.128.0/*',requestStage:'Request'},
                {urlPattern:'*cdnjs.cloudflare.com/ajax/libs/socket.io/*',requestStage:'Request'}]});
            vendorIntercepted = true;
        }
        const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
        assert.equal(result.exceptionDetails, undefined, JSON.stringify(result.exceptionDetails));
        return result.result.value;
    };
    const until = async (expression, label) => {
        for (let i = 0; i < 120; i++) {
            if (await evaluate(expression)) return;
            await pause(250);
        }
        console.error(await evaluate(`JSON.stringify({url:location.href,gm:window.isGM,token:Boolean(localStorage.getItem('access_token')),body:document.body?.innerText.slice(0,500)})`));
        throw new Error(`Timed out: ${label}`);
    };
    if ((safeSession && isolation.isPageTarget(url)) || process.env.VTT_NETWORK === '1') await call('Network.enable');
    const closed = new Promise(resolve => socket.addEventListener('close', resolve, { once: true }));
    const client = { call, evaluate, until, events, networkEvents, close: () => { socket.close(); return closed; } };
    return safeSession ? await safeSession.prepareClient(client,url) : client;
}

(async () => {
    const keepAlive = setInterval(() => {}, 1000);
    let server, profile, browser, page, observer, playerPage, browserProtocol;
    let serverErrors = '';
    try {
    const reserve = net.createServer();
    await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve));
    const port = reserve.address().port;
    await new Promise(resolve => reserve.close(resolve));
    const base = `http://127.0.0.1:${port}`;
    const python = path.join(root, '.venv/Scripts/python.exe');
    server = safeSession ? safeSession.spawnServer(python, port) : spawn(python, ['-u', '-c', `import tempfile
from app.config import TestingConfig, DevelopmentConfig
test_directory = tempfile.TemporaryDirectory(prefix='vtt-browser-db-')
TestingConfig.SQLALCHEMY_DATABASE_URI = 'sqlite:///' + test_directory.name + '/test.sqlite'
DevelopmentConfig.SQLALCHEMY_DATABASE_URI = TestingConfig.SQLALCHEMY_DATABASE_URI
# Browser tests must use the same server selection as run.py, not Werkzeug.
TestingConfig.SOCKETIO_ASYNC_MODE = ${process.env.VTT_NETWORK_WERKZEUG === '1' ? "'threading'" : 'None'}
from app import create_app, socketio
from app.extensions import db
import os, runpy
if ${process.env.VTT_NAVIGATION_SOURCE ? 'True' : 'False'}:
    import app.services.location_navigation as navigation
    exec(compile(open(${JSON.stringify(process.env.VTT_NAVIGATION_SOURCE || '')},encoding='utf-8-sig').read(), 'navigation-baseline', 'exec'), navigation.__dict__)
original_run = socketio.run
def run_with_database(app, **kwargs):
    with app.app_context():
        db.create_all()
    print('NETWORK SERVER', socketio.async_mode, flush=True)
    original_run(app, **kwargs)
socketio.run = run_with_database
if ${process.env.VTT_NETWORK_NORMAL === '1' ? 'True' : 'False'}:
    os.environ.update(FLASK_ENV='development', FLASK_HOST='127.0.0.1', FLASK_PORT='${port}', FLASK_DEBUG='0')
    runpy.run_path('run.py', run_name='__main__')
else:
    app = create_app('testing')
    socketio.run(app, host='127.0.0.1', port=${port}, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
`], { cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    server.stderr.on('data', data => { serverErrors = process.env.VTT_NETWORK === '1' ? serverErrors + data : (serverErrors + data).slice(-10000); });
    server.stdout.on('data', data => { if(process.env.VTT_NETWORK === '1' && String(data).includes('NETWORK SERVER')) console.log(String(data).trim()); });
    profile = safeSession ? safeSession.profile() : process.env.VTT_BROWSER_PROFILE || fs.mkdtempSync(path.join(os.tmpdir(), 'vtt-object-editor-'));
    if (process.env.VTT_BROWSER_PROFILE) fs.rmSync(path.join(profile,'DevToolsActivePort'),{force:true});
        if (safeSession) await safeSession.ready();
        else for (let i = 0; i < 100; i++) {
            try { if ((await fetch(base)).ok) break; } catch {}
            await pause(100);
        }
        const request = async (url, payload, token, method = 'POST') => {
            const response = await fetch(base + url, { method,
                headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
                ...(payload ? { body: JSON.stringify(payload) } : {}),
            });
            const body = await response.text();
            assert.ok(response.headers.get('content-type')?.includes('application/json'), `${url}: ${body}`);
            const result = JSON.parse(body);
            assert.ok(response.ok, JSON.stringify(result));
            return result;
        };
        await request('/auth/register', { username: 'browser-gm', email: 'browser-gm@example.test', password: 'browser-test-password' });
        const login = await request('/auth/login', { username: 'browser-gm', password: 'browser-test-password' });
        const token = login.access_token;
        const waterGeometry=process.env.VTT_WATER_GEOMETRY==='1'||process.env.VTT_WATER_FINAL==='1'||process.env.VTT_WORLD_SCULPT==='1';
        const lobby = await request('/lobbies/', { name: 'Direct object browser test', map_type: 'empty', chunks_width: 2, chunks_height: waterGeometry?2:1 }, token);
        await request(`/lobbies/${lobby.id}/chunks?min_chunk_x=0&max_chunk_x=1&min_chunk_y=0&max_chunk_y=${waterGeometry?1:0}`, null, token, 'GET');
        await request(`/lobbies/${lobby.id}/chunks/0/0/tile/31/10`, { objects: [
            { type: 'factory', name: 'Factory with a long name that must wrap safely inside the selected object panel',
                scale: 1, rotation: 0, color: '#66747b', is_active: true },
            { type: 'tree', x: 0.4, z: 0.4, scale: 0.6 },
        ] }, token, 'PATCH');
        await request(`/lobbies/${lobby.id}/chunks/1/0/tile/0/10`, { objects: [{ type: 'house', x: -0.4, z: -0.4 }] }, token, 'PATCH');
        browser = (safeSession ? safeSession.spawn.bind(safeSession) : spawn)(process.env.BROWSER_EXECUTABLE || 'C:/Program Files/Google/Chrome/Application/chrome.exe', [
            '--headless=new', ...(safeSession ? [] : ['--no-sandbox', '--ignore-certificate-errors']), '--no-first-run',
            ...((process.env.VTT_ROTATION_ONLY === '1' || process.env.VTT_SHEET_REPRO_ONLY === '1' || process.env.VTT_MEDICAL_RETRY_ONLY === '1' || process.env.VTT_DAMAGE_REVOKE_ONLY === '1' || process.env.VTT_MAGAZINE_ONLY === '1' || process.env.VTT_MOVEMENT_ONLY === '1' || process.env.VTT_PRIPYAT === '1' || process.env.VTT_UI_REGRESSION === '1' || process.env.VTT_UI_LAYOUT === '1' || process.env.VTT_LOC_SCULPT === '1' || process.env.VTT_LOC_LIGHT_CLEANUP === '1' || process.env.VTT_LOC_NUMERIC === '1' || process.env.VTT_LOC_HISTORY === '1' || process.env.VTT_LOC_BRUSH === '1' || process.env.VTT_LOC_CLIPBOARD === '1') ? ['--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows'] : []),
            ...(process.env.VTT_PERF_UNCAPPED==='1'?['--disable-frame-rate-limit','--disable-gpu-vsync']:[]),
            ...(process.env.VTT_BROWSER_GPU==='1'?[]:['--use-angle=swiftshader','--enable-unsafe-swiftshader']), '--window-size=1440,900',
            '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank',
        ], { windowsHide: true, stdio: 'ignore' });
        let debugPort;
        for (let i = 0; i < 100; i++) {
            try { debugPort = +fs.readFileSync(path.join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]; break; }
            catch { await pause(100); }
        }
        assert.ok(debugPort, 'Chrome did not start');
        const pages = await (await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
        page = await connect(pages.find(item => item.type === 'page').webSocketDebuggerUrl);
        await page.call('Page.enable');
        await page.call('Runtime.enable');
        await page.call('Log.enable');
        if ((process.env.VTT_GM_RELOCATION_ONLY === '1' || process.env.VTT_UI_REGRESSION === '1' || process.env.VTT_PRIPYAT === '1' || process.env.VTT_MOVEMENT_ONLY === '1') && !process.env.VTT_WATER_BASELINE_DIR) {
            const modules = path.join(profile, 'regression-modules');
            fs.mkdirSync(modules,{recursive:true});
            const source = process.env.VTT_PRIPYAT_LEGACY === '1'
                ? fs.readFileSync(path.join(root,'tmp/ui-regression-modules/locationScene.js'),'utf8').split('\nexport {showCombatActionMenu,')[0]
                : fs.readFileSync(path.join(root,'app/static/js/locationScene.js'),'utf8');
            fs.writeFileSync(path.join(modules,'locationScene.js'), source + `
export {showCombatActionMenu, showFacingMenu, updateFacingPreview, closeFacingMenu, findMovementPath, hasStoreyNavigation, hasLineOfSight, beginCharacterMoveMode, updateMovementPreview, commitMovementPreview, resolveFacingCell, showMovementTypeMenu, selectEditorAsset};
export function sceneGraphicsForTest(){return {scene,renderer,camera,controls,sun:locationDirectionalLight,roots:locationObjectMeshes,roofs:roofMeshes,characters:characterModels,cache:locationShadowCache,shadowPass:locationShadowPass,batches:locationRenderBatches,pickAtScreen:getLocationObjectAtScreen};}
export function facingPreviewCount(){return facingPreviewMesh?.count || 0;}
export function navigationPreviewSettled(sx,sy,x,y,c,l){return [...navigationPreviews.entries()].some(([key,value])=>key.endsWith(\`:\${c}:\${sx}:\${sy}:\${x}:\${y}:\${l}\`)&&value!==null);}
export function characterPose(id){const e=getCharacterModelEntry(id);return {x:e.posX,y:e.posY,fx:e.facingX,fy:e.facingY};}
export function characterSpatialPose(id){const e=getCharacterModelEntry(id);return {level:e.floorLevel,height:e.physicalHeight,x:e.model.position.x,z:e.model.position.z,renderHeight:e.model.position.y,animating:Boolean(e.positionAnimation)};}
export function beginObjectClimbForTest(objectId,characterId){const object=currentLocationData.objects.find(o=>o.id===objectId);return performClimbAction(object,characterId);}
export function captureCurrentSceneForTest(){renderer.render(scene,camera);return renderer.domElement.toDataURL('image/png');}
export function objectRenderBoundsForTest(id){const root=locationObjectMeshes.find(m=>m.userData.locationObject?.id===id||m.userData.locationObjectId===id);if(!root)return null;const box=new THREE.Box3().setFromObject(root);return{center:box.getCenter(new THREE.Vector3()).toArray(),size:box.getSize(new THREE.Vector3()).toArray()};}
export function movementHintText(){return movementPreviewHint?.textContent;}
export async function placeLibraryAssetForTest(x,z){hoveredTileCoords={x,z};updateBuildPreview();const preview=buildPreviewMesh?.userData?.locationObject?.properties;const staged=await placeStructureAtTile(x,z);const object=staged&&currentLocationData.objects.find(o=>o.id>0&&o.tile_x===staged.tile_x&&o.tile_y===staged.tile_y&&o.properties.visual_asset===staged.properties.visual_asset);return {preview,object};}
export function gmRelocationStateForTest(){return {pending:Boolean(pendingCharacterPlacement?.gmRelocation),ghost:Boolean(movementPreviewGhost||previewSprite),level:locationViewLevel};}
export function characterIsMutantForTest(id){return Boolean(getCharacterModelEntry(id)?.isMutant);}
export function characterModelCenterForTest(id){return new THREE.Box3().setFromObject(getCharacterModelEntry(id).model).getCenter(new THREE.Vector3()).toArray();}
export function navigationPreviewStateForTest(){return {level:movementTargetLevel,route:movementPreviewLine?.userData?.route,points:movementPreviewLine?.userData?.points?.map(p=>p.toArray()),frustumCulled:movementPreviewLine?.frustumCulled};}
export function movementHintSnapshot(){return {text:movementPreviewHint?.textContent||'',visible:Boolean(movementPreviewHint&&getComputedStyle(movementPreviewHint).display!=='none'&&getComputedStyle(movementPreviewHint).visibility!=='hidden'&&movementPreviewHint.getClientRects().length)};}
export function movementReadinessSnapshot(){return {armedCharacterId:armedMoveCharacterId,mode:armedMovementType,previewCharacterId:movementPreviewCharacterId,startX:movementPreviewStartX,startY:movementPreviewStartY,previewInitialized:Boolean(movementPreviewGhost),menuClosed:!movementTypeMenu||getComputedStyle(movementTypeMenu).display==='none'||movementTypeMenu.getClientRects().length===0};}
export function previewDiagnostics(){return {count:facingPreviewMesh?.count||0,pending:!!pendingFacingSelection,pose:pendingFacingSelection&&characterPose(pendingFacingSelection.characterId)};}
export function previewComplete(){return facingPreviewState?.complete===true;}
export function previewTiles(){return Array.from({length:facingPreviewMesh?.count||0},(_,i)=>[Math.round(facingPreviewMesh.instanceMatrix.array[i*16+12]-.5),Math.round(facingPreviewMesh.instanceMatrix.array[i*16+14]-.5)]);}
export function expectedPreview(id,x,y){const s={...getCharacterModelEntry(id),facingX:x-getCharacterModelEntry(id).posX,facingY:y-getCharacterModelEntry(id).posY};return facingPreviewState.cells.filter(c=>isPointVisibleToPlayer(c.x,c.y,c.height+1.65,[s])).map(c=>[c.x,c.y]);}
export function visibilitySnapshot(){return JSON.stringify({fog:[...fogMemory],characters:[...characterModels.values()].map(e=>e.model.visible),objects:locationObjectMeshes.map(m=>m.visible),decor:objectMeshes.map(m=>m.visible)});}
export function characterVisible(id){return getCharacterModelEntry(id)?.model.visible;}
export function previewFixturePose(id,x,y,level){closeFacingMenu();const height=getVisionObjectIndex().floors.get(level)?.get(\`\${x}:\${y}\`)??getTileHeight(x,y);updateCharacterPosition(id,x,y,{floor_level:level,height});setLocationViewLevel(level);return height;}
if(typeof renderFacingPreview==='function'){const original=renderFacingPreview;renderFacingPreview=()=>{const t=performance.now();try{return original();}finally{(window.previewRenderSamples ||= []).push(performance.now()-t);}};}
`);
            if (safeSession) safeSession.captureCurrentModule(path.join(modules,'locationScene.js'));
            else process.env.VTT_WATER_BASELINE_DIR = modules;
        }
        if(process.env.VTT_WATER_BASELINE_DIR || process.env.VTT_BROWSER_VENDOR_DIR)await page.call('Fetch.enable',{patterns:[{urlPattern:'*/static/js/*',requestStage:'Request'},
            ...(process.env.VTT_BROWSER_VENDOR_DIR?[{urlPattern:'*unpkg.com/three@0.128.0/*',requestStage:'Request'},{urlPattern:'*cdnjs.cloudflare.com/ajax/libs/socket.io/*',requestStage:'Request'}]:[])]});
        await page.call('Page.navigate', { url: base });
        await page.until('location.origin === ' + JSON.stringify(base), 'origin');
        await page.evaluate(`localStorage.setItem('access_token', ${JSON.stringify(token)});localStorage.setItem('user_id', ${JSON.stringify(String(login.user_id))});`);
        if(process.env.VTT_FORCE_WEBGL1==='1')await page.call('Page.addScriptToEvaluateOnNewDocument',{source:"const originalGetContext=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,...args){return type==='webgl2'?null:originalGetContext.call(this,type,...args)}"});
        await page.call('Page.navigate', { url: `${base}/lobbies/${lobby.id}/page` });
        await page.until(`Boolean(window.toggleEditMode && window.beginDirectWorldObjectEdit && window.isGM)`, 'game startup');
        if(process.env.VTT_BROWSER_VENDOR_DIR) {
            fs.mkdirSync(process.env.VTT_BROWSER_VENDOR_DIR,{recursive:true});
            const {frameTree}=await page.call('Page.getResourceTree');
            for(const resource of frameTree.resources) {
                if(!/unpkg.com\/three@0.128.0\/|cdnjs.cloudflare.com\/ajax\/libs\/socket.io\//.test(resource.url))continue;
                const name=new URL(resource.url).pathname.split('/').pop();
                if(!['three.module.js','OrbitControls.js','CSS2DRenderer.js','socket.io.js','GLTFLoader.js','BufferGeometryUtils.js'].includes(name))continue;
                const body=await page.call('Page.getResourceContent',{frameId:frameTree.frame.id,url:resource.url});
                fs.writeFileSync(path.join(process.env.VTT_BROWSER_VENDOR_DIR,name),Buffer.from(body.content,body.base64Encoded?'base64':'utf8'));
            }
        }
        await page.evaluate(`(async()=>{window.sceneModule=await import('/static/js/lobby3d.js');window.threeModule=await import('three')})()`);
        await page.until(`sceneModule.chunksMap.size===${waterGeometry?4:2} && document.getElementById('scene-loading-screen').classList.contains('is-hidden')`, 'world chunks');
        assert.equal(await page.evaluate('Boolean(sceneModule.renderer.isUnavailableRenderer)'), false);
        if (safeSession) {
            const renderer = await page.evaluate(`(()=>{const gl=sceneModule.renderer.getContext(),ext=gl.getExtension('WEBGL_debug_renderer_info');return ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):null})()`);
            isolation.assertGpu(renderer);
            fs.writeFileSync(path.join(safeSession.artifacts,'gpu.json'),JSON.stringify({renderer},null,2));
        }
        if (safeSession && process.env.VTT_SAFE_SCENARIO === 'terrain') {
            await require('./terrain_los.browser.cjs')({page,request,token,lobby,base,safeSession});
            return;
        }
        if (safeSession && process.env.VTT_SAFE_SCENARIO === 'gm-relocation') {
            await require('./gm_relocation.browser.cjs')({page,request,token,lobby,base,connect,debugPort,safeSession});
            return;
        }
        if (safeSession && process.env.VTT_SAFE_SCENARIO === 'height') {
            await require('./movement_height.browser.cjs')({page,request,token,lobby,safeSession});
            return;
        }
        if (safeSession && process.env.VTT_SAFE_SCENARIO === 'rotation') {
            await require('./rotation_hold.browser.cjs')({page,request,token,lobby,safeSession});
            return;
        }
        if (safeSession && process.env.VTT_SAFE_SCENARIO === 'medical-retry') {
            await require('./medical_retry.browser.cjs')({page,request,token,lobby,base,connect,debugPort,safeSession});
            return;
        }
        if (safeSession && process.env.VTT_SAFE_SCENARIO === 'magazine') {
            await require('./magazine_regression.browser.cjs')({page,request,token,lobby,base,connect,debugPort,safeSession});
            return;
        }
        if (safeSession && process.env.VTT_SAFE_SCENARIO === 'durable-reload') {
            await require('./durable_reload.browser.cjs')({page,request,token,lobby,base,connect,debugPort,safeSession});
            return;
        }
        if (safeSession && process.env.VTT_SAFE_SCENARIO === 'damage-revoke') {
            await require('./damage_revoke.browser.cjs')({page,request,token,lobby,base,connect,debugPort,safeSession});
            return;
        }
        if (safeSession && process.env.VTT_SAFE_SCENARIO === 'sheet-repro') {
            await require('./character_widgets.browser.cjs')({page,request,token,lobby,login,profile,base,connect,debugPort,safeSession,focusRegression:true});
            return;
        }
        if (safeSession && process.env.VTT_SAFE_SCENARIO === 'widgets') {
            await require('./character_widgets.browser.cjs')({page,request,token,lobby,login,profile,base,connect,debugPort,safeSession});
            return;
        }
        if (safeSession && process.env.VTT_SAFE_SCENARIO === 'skills') {
            await require('./skills.browser.cjs')({page,request,token,lobby});
            return;
        }
        if (process.env.VTT_PRIPYAT === '1' || process.env.VTT_MOVEMENT_ONLY === '1') {
            await require('./pripyat_ui.browser.cjs')({page,request,token,lobby,base,connect,debugPort,safeSession});
            return;
        }
        if (process.env.VTT_UI_REGRESSION === '1') {
            await require('./ui_regression.browser.cjs')({ page, request, token, lobby, base, connect, debugPort });
            return;
        }
        if (process.env.VTT_LOCAL_MAP_TOOLS === '1') {
            await require('./local_map_tools.browser.cjs')({ page, request, token, lobby, base, connect, debugPort });
            return;
        }
        if (process.env.VTT_UI_LAYOUT === '1') {
            await require('./ui_layout.browser.cjs')({ page, request, token, lobby, base, connect, debugPort });
            return;
        }
        if (process.env.VTT_SCENE_LOADING === '1') {
            await require('./scene_loading.browser.cjs')({ page, request, token, lobby, base });
            return;
        }
        if (process.env.VTT_LOC_LIBRARY === '1') {
            await require('./location_asset_library.browser.cjs')({ page, request, token, lobby });
            return;
        }
        if (process.env.VTT_LOC_LIGHT_CLEANUP === '1') {
            await require('./location_light_cleanup.browser.cjs')({ page, request, token, lobby, base, connect, debugPort });
            return;
        }
        if (process.env.VTT_SCULPT_FOCUS === '1') {
            await require('./sculpt_focus.browser.cjs')({page,request,token,lobby});
            return;
        }
        if (process.env.VTT_WORLD_HISTORY === '1') {
            await require('./world_editor_history.browser.cjs')({page,request,token,lobby,base,connect,debugPort});
            return;
        }
        if (process.env.VTT_WORLD_SCULPT === '1') {
            await require('./world_sculpt.browser.cjs')({page,request,token,lobby,base,connect,debugPort});
            return;
        }
        if (process.env.VTT_LOC_SCULPT === '1') {
            await require('./location_sculpt.browser.cjs')({page,request,token,lobby,base,connect,debugPort});
            return;
        }
        if (process.env.VTT_LOC_CLIPBOARD === '1') {
            await require('./location_clipboard.browser.cjs')({ page, request, token, lobby, base, connect, debugPort });
            return;
        }
        if (process.env.VTT_LOC_BRUSH === '1') {
            await require('./location_brush_history.browser.cjs')({ page, request, token, lobby, base, connect, debugPort });
            return;
        }
        if (process.env.VTT_LOC_HISTORY === '1') {
            await require('./location_editor_history.browser.cjs')({ page, request, token, lobby, base, connect, debugPort });
            return;
        }
        if (process.env.VTT_LOC_PANEL === '1') {
            await require('./location_panel.browser.cjs')({ page, request, token, lobby, base });
            return;
        }
        if (process.env.VTT_LOC_NUMERIC === '1') {
            await require('./location_numeric_inputs.browser.cjs')({ page, request, token, lobby, base, connect, debugPort });
            return;
        }
        if(process.env.VTT_NETWORK==='1'){
            await require('./network_transport.browser.cjs')({page,request,token,lobby,base,connect,debugPort});
            let httpAborts=0;
            const unexpected=serverErrors.replace(/Traceback[\s\S]*?(?=\[\d{4}-|Removing descriptor|$)/g, failure=>{
                // Chrome cancels HTTP asset requests when changing scenes on Windows.
                if(failure.includes('eventlet\\wsgi.py')&&/Connection(?:Aborted|Reset)Error: \[WinError 1005[34]\]/.test(failure)&&!failure.includes('\\app\\')){httpAborts++;return '';}
                return failure;
            });
            console.log('SERVER LOG',JSON.stringify({http_connection_aborts:httpAborts,unexpected_errors:/Traceback|Invalid frame|AssertionError|Error on request/.test(unexpected)}));
            for(const failure of unexpected.match(/Traceback[\s\S]*?(?=\[\d{4}-|Removing descriptor|$)/g)||[]) console.error('UNEXPECTED SERVER FAILURE',failure);
            assert.doesNotMatch(unexpected, /Traceback|Invalid frame|AssertionError|Error on request/);
            return;
        }
        if(process.env.VTT_GPU_PROBE==='1'){
            console.log('GPU',await page.evaluate(`(()=>{const gl=sceneModule.renderer.getContext(),ext=gl.getExtension('WEBGL_debug_renderer_info');return ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER)})()`));return;
        }
        if(process.env.VTT_MANUAL_BUILDING==='1'){
            await require('./location_manual_building.browser.cjs')({page,request,token,lobby,base,connect,debugPort});return;
        }
        if(process.env.VTT_ROOF_SECTIONS==='1'){
            await require('./location_roof_sections.browser.cjs')({page,request,token,lobby,base,connect,debugPort});return;
        }
        if(process.env.VTT_ASSET_EXPANSION==='1'){
            await require('./location_asset_expansion.browser.cjs')({page,request,token,lobby,base,connect,debugPort});return;
        }
        if(process.env.VTT_FURNITURE==='1'){
            await require('./location_furniture.browser.cjs')({page,request,token,lobby,base,connect,debugPort});return;
        }
        if(process.env.VTT_BUILDING_SHELL==='1'){
            await require('./location_building_shell.browser.cjs')({page,request,token,lobby,base,connect,debugPort});return;
        }
        if(process.env.VTT_STOREYS==='1'){
            await require('./location_storeys.browser.cjs')({page,request,token,lobby,base,connect,debugPort});return;
        }
        if(process.env.VTT_VERTICAL_ACCESS==='1'){
            await require('./location_vertical_access.browser.cjs')({page,request,token,lobby,base,connect,debugPort});return;
        }
        if(process.env.VTT_WORLD_PERF==='1'){
            await require('./world_perf.browser.cjs')({page});return;
        }
        if(process.env.VTT_INSTANCE_BATCH_TEST==='1'){
            await require('./world_instance_batches.browser.cjs')(page);return;
        }
        if(process.env.VTT_HOUSEHOLD_FIX==='1'){
            await require('./location_household_fix.browser.cjs')({page,request,token,lobby,base,connect,debugPort});return;
        }
        if(process.env.VTT_HOUSEHOLD==='1'){
            await require('./location_household.browser.cjs')({page,request,token,lobby,base,connect,debugPort});return;
        }
        if(process.env.VTT_WATER_PROFILES==='1'){
            await require('./water_profiles.browser.cjs')({page,request,token,lobby,base,connect,debugPort});return;
        }
        if(process.env.VTT_WATER_SIMPLIFY==='1'){
            await require('./water_simplify.browser.cjs')({page,request,token,lobby});return;
        }
        if(process.env.VTT_WATER_FINAL==='1'){
            await require('./water_final.browser.cjs')({page,request,token,lobby,base,connect,debugPort});return;
        }
        if(process.env.VTT_LOCAL_WATER_AUDIT==='1'){
            await require('./local_water_audit.browser.cjs')({page,request,token,lobby});return;
        }
        if(waterGeometry){await require('./water_geometry.browser.cjs')({page,request,token,lobby,base,connect,debugPort});return;}
        if (process.env.VTT_WATER_AUDIT === '1') {
            await require('./water_shores_audit.browser.cjs')({page,request,token,lobby});return;
        }
        if (process.env.VTT_BRIDGE_ASSET === '1') {
            await require('./bridge_asset.browser.cjs')({page,request,token,lobby,base,connect,debugPort});return;
        }
        if (process.env.VTT_LICENSES === '1') {
            await require('./asset_credits.browser.cjs')({page,request,token,lobby,base,connect,debugPort});return;
        }
        if (process.env.VTT_BRIDGE_BANK === '1') {
            await require('./bridge_bank.browser.cjs')({page,request,token,lobby,base,connect,debugPort});return;
        }
        if (process.env.VTT_ROAD_TERRAIN === '1') {
            await require('./road_terrain.browser.cjs')({page,request,token,lobby,base,connect,debugPort});return;
        }
        if (process.env.VTT_SECONDARY_TOOLS === '1') {
            await require('./secondary_tools.browser.cjs')({page,request,token,lobby,base,connect,debugPort});return;
        }
        if (process.env.VTT_ROAD_ROUTE === '1') {
            await require('./world_road_route.browser.cjs')({page,request,token,lobby,base,connect,debugPort});
            return;
        }
        if (process.env.VTT_FENCE_LINE === '1') {
            await require('./world_fence_line.browser.cjs')({page,request,token,lobby,base,connect,debugPort});
            return;
        }
        if (process.env.VTT_PO2_FENCE === '1') {
            await require('./po2_fence.browser.cjs')({page,request,token,lobby,base,connect,debugPort});
            return;
        }
        if (process.env.VTT_WORLD_OBJECT_BUGS === '1') {
            await require('./world_object_editor_regressions.browser.cjs')({ page, request, token, lobby, base, connect, debugPort, profile });
            return;
        }
        if (process.env.VTT_SNOW_INTEGRATION === '1') {
            await require('./snow_integration.browser.cjs')({ page, request, token, lobby, base, connect, debugPort });
            return;
        }
        if (process.env.VTT_SNOW_CANDIDATES === '1') {
            await require('./snow_candidates.browser.cjs')({ page, request, token, lobby });
            return;
        }
        if (process.env.VTT_SWAMP_03 === '1') {
            await require('./world_swamp03.browser.cjs')({ page, request, token, lobby });
            return;
        }
        if (process.env.VTT_SWAMP_COMPONENTS === '1') {
            await require('./world_swamp_components.browser.cjs')({ page, request, token, lobby });
            return;
        }
        if (process.env.VTT_SWAMP_COMPOSITION === '1') {
            await require('./world_swamp_composition.browser.cjs')({ page, request, token, lobby });
            return;
        }
        if (process.env.VTT_SWAMP_CANDIDATES === '1') {
            await require('./world_swamp_candidates.browser.cjs')({ page, request, token, lobby });
            return;
        }
        if (process.env.VTT_SWAMP_DIAGNOSTICS === '1') {
            await require('./world_swamp_diagnostics.browser.cjs')({ page, request, token, lobby });
            return;
        }
        if (process.env.VTT_MATERIAL_CANDIDATES === '1') {
            await require('./terrain_material_candidates.browser.cjs')({ page, request, token, lobby, profile });
            return;
        }
        if (process.env.VTT_TERRAIN_ONLY === '1') {
            await require('./terrain_materials.browser.cjs')({ page, request, token, lobby, profile, base, connect, debugPort });
            return;
        }
        await page.until(`document.getElementById('panel-chat').dataset.chatInitialized==='true'`, 'chat panel initialized');
        assert.equal(await page.evaluate(`['panel-chat','panel-common-tools'].every(id=>document.getElementById(id).hidden && document.getElementById(id).getClientRects().length===0)`),true);
        await page.evaluate(`document.getElementById('common-tools-toggle').click()`);
        assert.equal(await page.evaluate(`['chat-toggle','common-tools-toggle','lobby-rest-btn','world-travel-btn','settings-btn','theme-toggle','templates-manager-btn','edit-toggle'].every(id=>!!document.getElementById(id))`),true);
        const toolsHeader=await page.evaluate(`(()=>{const r=document.querySelector('#panel-common-tools .panel-header').getBoundingClientRect();return {x:r.left+30,y:r.top+r.height/2}})()`);
        await page.call('Input.dispatchMouseEvent',{type:'mousePressed',...toolsHeader,button:'left',buttons:1,clickCount:1});
        await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',x:toolsHeader.x+20,y:toolsHeader.y-35,button:'left',buttons:1});
        await page.call('Input.dispatchMouseEvent',{type:'mouseReleased',x:toolsHeader.x+20,y:toolsHeader.y-35,button:'left',buttons:0,clickCount:1});
        const toolsPosition=await page.evaluate(`JSON.parse(localStorage.getItem('panelStates'))['panel-common-tools'].position`);
        await page.evaluate(`document.querySelector('#panel-common-tools .toggle-btn').click();document.getElementById('common-tools-toggle').click()`);
        assert.equal(await page.evaluate(`document.getElementById('panel-common-tools').getClientRects().length===0 && document.getElementById('common-tools-toggle').getAttribute('aria-expanded')==='false'`),true);
        await page.evaluate(`window.toolbarBeforeReload=true`);
        await page.call('Page.reload');
        await page.until(`!window.toolbarBeforeReload && window.isGM && document.getElementById('panel-common-tools').dataset.draggableInitialized==='true' && document.getElementById('scene-loading-screen').classList.contains('is-hidden')`, 'toolbar preferences restored');
        await page.evaluate(`(async()=>{window.sceneModule=await import('/static/js/lobby3d.js');window.threeModule=await import('three')})()`);
        assert.equal(await page.evaluate(`document.getElementById('panel-common-tools').hidden && document.getElementById('panel-common-tools').classList.contains('collapsed')`),true);
        await page.evaluate(`document.getElementById('common-tools-toggle').click();document.querySelector('#panel-common-tools .toggle-btn').click()`);
        await page.evaluate(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
        assert.deepEqual(await page.evaluate(`(()=>{const r=document.getElementById('panel-common-tools').getBoundingClientRect();return {left:r.left,top:r.top}})()`),toolsPosition);
        const toolbarScreenshot=path.join(profile,'room-toolbar.png');
        fs.writeFileSync(toolbarScreenshot,Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        console.log('Room toolbar screenshot:',toolbarScreenshot);
        console.log('PASS: room actions preserved; common toolbar launcher, drag, collapse, visibility and position restored after reload');
        const canvasBeforeChat=await page.evaluate(`(()=>{const r=sceneModule.renderer.domElement.getBoundingClientRect();
            return {width:r.width,height:r.height,camera:sceneModule.camera.position.toArray(),target:sceneModule.controls.target.toArray()}})()`);
        await page.evaluate(`document.getElementById('chat-toggle').click()`);
        assert.equal(await page.evaluate(`!document.getElementById('panel-chat').hidden && document.getElementById('panel-chat').dataset.chatDocked==='true'`),true);
        for(const text of ['Browser chat message','/roll 1d6','/roll invalid']){
            await page.evaluate(`document.getElementById('message-input').value=${JSON.stringify(text)};window.sendMessage()`);
        }
        await page.until(`document.querySelector('#chat .chat-human') && document.querySelector('#chat .chat-roll') && document.querySelector('#chat .chat-event')`, 'three chat message styles from sockets');
        await page.evaluate(`document.getElementById('chat-pin').click()`);
        const chatHeader=await page.evaluate(`(()=>{const r=document.querySelector('#panel-chat .panel-header').getBoundingClientRect();return {x:r.left+30,y:r.top+r.height/2}})()`);
        await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...chatHeader});
        await page.call('Input.dispatchMouseEvent',{type:'mousePressed',...chatHeader,button:'left',buttons:1,clickCount:1});
        await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',x:200,y:180,button:'left',buttons:1});
        await page.call('Input.dispatchMouseEvent',{type:'mouseReleased',x:200,y:180,button:'left',buttons:0,clickCount:1});
        const chatPosition=await page.evaluate(`(()=>{const r=document.getElementById('panel-chat').getBoundingClientRect();return {left:r.left,top:r.top}})()`);
        await page.evaluate(`document.getElementById('chat-pin').click();document.getElementById('chat-pin').click()`);
        assert.deepEqual(await page.evaluate(`(()=>{const r=document.getElementById('panel-chat').getBoundingClientRect();return {left:r.left,top:r.top}})()`),chatPosition);
        assert.deepEqual(await page.evaluate(`(()=>{const r=sceneModule.renderer.domElement.getBoundingClientRect();
            return {width:r.width,height:r.height,camera:sceneModule.camera.position.toArray(),target:sceneModule.controls.target.toArray()}})()`),canvasBeforeChat);
        await page.evaluate(`document.querySelector('#panel-chat .toggle-btn').click()`);
        await pause(50);
        await page.evaluate(`document.getElementById('chat-close').click()`);
        console.log('PASS: chat toolbar toggle, dock/float, native drag, saved position, message/roll/event styles and unchanged canvas/camera');
        const reloadPanels = async () => {
            await page.evaluate(`window.panelsBeforeReload=true`);
            await page.call('Page.reload');
            await page.until(`!window.panelsBeforeReload && document.getElementById('panel-chat')?.dataset.chatInitialized==='true' && document.getElementById('scene-loading-screen').classList.contains('is-hidden')`, 'both panel preferences restored');
            await page.evaluate(`(async()=>{window.sceneModule=await import('/static/js/lobby3d.js');window.threeModule=await import('three')})()`);
        };
        await page.evaluate(`document.getElementById('common-tools-toggle').click()`);
        await reloadPanels();
        assert.equal(await page.evaluate(`['panel-chat','panel-common-tools'].every(id=>document.getElementById(id).hidden && getComputedStyle(document.getElementById(id)).display==='none')`),true);
        assert.equal(await page.evaluate(`['chat-toggle','common-tools-toggle'].every(id=>document.getElementById(id).getAttribute('aria-expanded')==='false')`),true);
        await page.evaluate(`document.getElementById('chat-toggle').click();document.getElementById('common-tools-toggle').click();document.querySelector('#panel-common-tools .toggle-btn').click()`);
        await reloadPanels();
        assert.equal(await page.evaluate(`['panel-chat','panel-common-tools'].every(id=>{const p=document.getElementById(id);return !p.hidden && p.classList.contains('collapsed') && getComputedStyle(p.querySelector('.panel-content')).display==='none' && p.querySelector('.toggle-btn').textContent==='▶'})`),true);
        await page.evaluate(`document.getElementById('chat-toggle').click();document.querySelector('#panel-common-tools .toggle-btn').click()`);
        const panelsScreenshot=path.join(profile,'restored-panel-visibility.png');
        fs.writeFileSync(panelsScreenshot,Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        console.log('Restored panels screenshot:',panelsScreenshot);
        console.log('PASS: both panels stay closed after reload; collapsed panels stay collapsed; launchers agree with visibility');
        if (process.env.VTT_PANELS_ONLY === '1') return;
        if (process.env.VTT_WIDGETS_ONLY === '1') {
            await require('./character_widgets.browser.cjs')({ page, request, token, lobby, login, profile, base, connect, debugPort });
            return;
        }
        const playerCharacter=await request(`/lobbies/${lobby.id}/characters`,{name:'Игрок — разведчик',data:{notes:'Keep this note'}},token);
        const npcCharacter=await request(`/lobbies/${lobby.id}/characters`,{name:'НПС — проводник',data:{basic:{is_npc:true}}},token);
        const rules=await request(`/lobbies/${lobby.id}/world-rules`,null,token,'GET');
        const mutantCharacter=await request(`/lobbies/${lobby.id}/mutants`,{mutant_type:rules.mutants[0].name,name:'Мутант — дозорный'},token);
        const beforeFolders=await request(`/lobbies/${lobby.id}/characters`,null,token,'GET');
        await page.until(`document.querySelectorAll('#lobby-characters-list [data-character-id]').length===3`, 'three character categories');
        const dragCharacter = async (sourceSelector, targetSelector, cancel = false) => {
            const points = await page.evaluate(`(()=>{
                const source=document.querySelector(${JSON.stringify(sourceSelector)}),target=document.querySelector(${JSON.stringify(targetSelector)});
                let parent=source.parentElement;while(parent){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement}
                source.scrollIntoView({block:'nearest'});const s=source.getBoundingClientRect(),t=target.getBoundingClientRect();
                return {source:{x:s.left+Math.min(35,s.width/2),y:s.top+s.height/2},target:{x:t.left+t.width/2,y:t.top+Math.min(15,t.height/2)},closed:target.closest('.character-folder')?.open===false}
            })()`);
            await page.call('Input.setInterceptDrags',{enabled:true});page.events.delete('Input.dragIntercepted');
            await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...points.source});
            await page.call('Input.dispatchMouseEvent',{type:'mousePressed',...points.source,button:'left',buttons:1,clickCount:1});
            await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',x:points.source.x+10,y:points.source.y,button:'left',buttons:1});
            await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...points.target,button:'left',buttons:1});
            for(let i=0;i<30&&!page.events.has('Input.dragIntercepted');i++)await pause(50);
            const data=page.events.get('Input.dragIntercepted')?.data;assert.ok(data,'native character drag starts');
            for(const type of ['dragEnter','dragOver'])await page.call('Input.dispatchDragEvent',{type,...points.target,data});
            assert.equal(await page.evaluate(`document.querySelector(${JSON.stringify(targetSelector)}).closest('[data-character-folder-drop]').classList.contains('character-folder-drop-active')`),true);
            if(points.closed){
                await pause(700);
                assert.equal(await page.evaluate(`document.querySelector(${JSON.stringify(targetSelector)}).closest('.character-folder').open`),true);
            }
            await page.call('Input.dispatchDragEvent',{type:cancel?'dragCancel':'drop',...points.target,data});
            await page.call('Input.dispatchMouseEvent',{type:'mouseReleased',...points.target,button:'left',buttons:0,clickCount:1});
            await page.call('Input.setInterceptDrags',{enabled:false});
        };
        await page.evaluate(`window.originalPrompt=window.prompt;window.originalConfirm=window.confirm;
            document.getElementById('panel-characters').classList.remove('collapsed');
            window.prompt=()=> 'Разведка';window.confirm=()=>true;
            document.querySelector('#character-panel-players > .character-folder-create').click();`);
        await dragCharacter(`[data-character-id="${playerCharacter.id}"]`, '#character-panel-players .character-folder > summary', true);
        assert.equal(await page.evaluate(`document.querySelector('[data-character-id="${playerCharacter.id}"]').closest('.character-folder-root')!==null && !document.querySelector('.character-folder-drop-active')`),true);
        await dragCharacter(`[data-character-id="${playerCharacter.id}"]`, '#character-panel-players .character-folder > summary');
        assert.equal(await page.evaluate(`document.querySelector('[data-character-id="${playerCharacter.id}"]').closest('.character-folder')!==null`),true);
        await page.evaluate(`window.prompt=()=> 'Резерв';document.querySelector('#character-panel-players > .character-folder-create').click()`);
        await dragCharacter(`[data-character-id="${playerCharacter.id}"]`, '#character-panel-players .character-folder:last-child > summary');
        assert.equal(await page.evaluate(`document.querySelector('[data-character-id="${playerCharacter.id}"]').closest('.character-folder').querySelector('summary').textContent`),'Резерв (1)');
        await dragCharacter(`[data-character-id="${playerCharacter.id}"]`, '#character-panel-players .character-folder > summary');
        await page.evaluate(`document.querySelector('#character-panel-players .character-folder').open=true`);
        await pause(50);
        await page.evaluate(`window.prompt=()=> 'Отряд <А>';document.querySelector('#character-panel-players .character-folder-actions button').click()`);
        assert.equal(await page.evaluate(`document.querySelector('#character-panel-players .character-folder summary').textContent`),'Отряд <А> (1)');
        await page.evaluate(`document.querySelector('#character-panel-players .character-folder-actions button:last-child').click()`);
        assert.equal(await page.evaluate(`document.querySelector('[data-character-id="${playerCharacter.id}"]').closest('.character-folder-root')!==null`),true);
        await page.evaluate(`(()=>{document.querySelector('#character-tab-npcs').click();window.prompt=()=> 'Торговцы';
            document.querySelector('#character-panel-npcs > .character-folder-create').click();})()`);
        await dragCharacter(`[data-character-id="${npcCharacter.id}"]`, '#character-panel-npcs .character-folder > summary');
        await pause(50);
        await page.evaluate(`(()=>{document.querySelector('#character-tab-mutants').click();window.prompt=()=> 'Стая';
            document.querySelector('#character-panel-mutants > .character-folder-create').click();})()`);
        await dragCharacter(`[data-character-id="${mutantCharacter.id}"]`, '#character-panel-mutants .character-folder > summary');
        await dragCharacter('#character-panel-mutants .mutant-group > summary', '#character-panel-mutants .character-folder-root');
        assert.equal(await page.evaluate(`document.querySelector('[data-character-id="${mutantCharacter.id}"]').closest('.character-folder-root')!==null`),true);
        await dragCharacter(`[data-character-id="${mutantCharacter.id}"]`, '#character-panel-mutants .character-folder > summary');
        await pause(50);
        const dragPayload=await page.evaluate(`(()=>{const row=document.querySelector('[data-character-id="${mutantCharacter.id}"]');
            const transfer=new DataTransfer();row.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:transfer}));
            row.dispatchEvent(new DragEvent('dragend',{bubbles:true,dataTransfer:transfer}));return JSON.parse(transfer.getData('text/plain'))})()`);
        assert.equal(dragPayload.characterId,mutantCharacter.id);
        assert.equal(dragPayload.isMutant,true);
        assert.deepEqual(await request(`/lobbies/${lobby.id}/characters`,null,token,'GET'),beforeFolders,'folder changes never save gameplay data');
        await page.evaluate(`(async()=>{window.charactersModule=await import('/static/js/characters.js');await charactersModule.loadLobbyCharacters()})()`);
        assert.equal(await page.evaluate(`document.querySelector('#character-tab-mutants').getAttribute('aria-selected')==='true' && document.querySelector('#character-panel-mutants .character-folder').open && document.querySelector('#character-panel-mutants .mutant-group').open`),true);
        await page.evaluate(`(async()=>{window.isGM=false;await charactersModule.loadLobbyCharacters()})()`);
        assert.equal(await page.evaluate(`Boolean(document.querySelector('#character-panel-mutants > .character-folder-create')) && !document.querySelector('#lobby-characters-list select') && !document.querySelector('#character-panel-mutants .mutant-group-add')`),true);
        await page.evaluate(`(async()=>{window.isGM=true;await charactersModule.loadLobbyCharacters();
            sceneModule.controls.target.set(31.5,1,10.5);sceneModule.camera.position.set(35,9,19);sceneModule.controls.update();sceneModule.camera.updateMatrixWorld()})()`);
        await page.until(`sceneModule.chunksMap.get('0,0').landmarks.factory.visible`, 'character panel with normal game camera');
        await pause(700);
        const characterLayout=await page.evaluate(`(()=>{
            const result=[];
            for(const category of ['players','npcs','mutants']){
                document.getElementById('character-tab-'+category).click();
                const panel=document.getElementById('character-panel-'+category);
                const row=panel.querySelector('[data-character-id]');
                const visible=[...document.querySelectorAll('[data-character-create]')].filter(b=>b.getClientRects().length&&getComputedStyle(b).display!=='none');
                result.push({category,draggable:row.draggable,noSelect:!panel.querySelector('select'),
                    creation:visible.map(b=>b.dataset.characterCreate)});
            }
            return {width:document.getElementById('panel-characters').getBoundingClientRect().width,result};
        })()`);
        assert.ok(characterLayout.width>=320 && characterLayout.width<=360);
        for(const layout of characterLayout.result){
            assert.equal(layout.draggable,true,layout.category+' draggable character');
            assert.equal(layout.noSelect,true,layout.category+' no folder dropdown');
            assert.deepEqual(layout.creation,[layout.category]);
        }
        const foldersScreenshot=path.join(profile,'character-folders.png');
        fs.writeFileSync(foldersScreenshot,Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        console.log('Character folders screenshot:',foldersScreenshot);
        await page.evaluate(`window.prompt=originalPrompt;window.confirm=originalConfirm;document.getElementById('panel-characters').classList.add('collapsed')`);
        console.log('PASS: player/NPC/mutant tabs, personal folder create/rename/delete, move real records, root restoration, expanded state, unchanged server data and existing drag payload');
        await page.evaluate(`sceneModule.controls.target.set(31.5,1,10.5);sceneModule.camera.position.set(35,9,19);
            sceneModule.controls.update();sceneModule.camera.updateMatrixWorld();window.toggleEditMode();`);
        await page.until(`worldEditorTool==='select' && document.getElementById('gm-only-controls').style.display!=='none'`, 'select tool');
        await page.until(`sceneModule.chunksMap.get('0,0').landmarks.factory.visible`, 'visible factory');
        await pause(1000);
        await page.evaluate(`(async()=>{window.mapEditor=await import('/static/js/mapEdit.js')})()`);
        const ux = await page.evaluate(`(()=>{
            const root=document.getElementById('gm-only-controls');
            const shown=e=>getComputedStyle(e).display!=='none'&&e.getClientRects().length>0;
            const expected={select:['select'],terrain:['terrain','brush'],height:['height','brush'],
                radiation:['radiation','brush'],erase:['brush'],objects:['objects','brush']};
            const result=[];
            for(const tool of Object.keys(expected)){
                root.querySelector('[data-world-tool="'+tool+'"]').click();
                const actual=[...root.querySelectorAll('[data-world-settings]')].filter(shown).map(e=>e.dataset.worldSettings).sort();
                result.push({tool,actual,expected:expected[tool].sort()});
            }
            const road=document.getElementById('world-road-link-controls');
            const treeRoadHidden=!shown(road);
            root.querySelector('[data-world-object="road"]').click();const roadShown=shown(road);
            const roadBrushHidden=!shown(root.querySelector('[data-world-settings="brush"]'));
            root.querySelector('[data-world-object="forest"]').click();
            const forest={roadHidden:!shown(road),densityShown:shown(document.getElementById('world-vegetation-options'))};
            root.querySelector('[data-world-object="factory"]').click();
            const factory={densityHidden:!shown(document.getElementById('world-vegetation-options')),
                activeShown:shown(document.getElementById('world-object-active-base-option'))};
            root.querySelector('[data-world-tool="height"]').click();
            document.getElementById('tile-height-number').value='1.7';document.getElementById('tile-height-number').dispatchEvent(new Event('input',{bubbles:true}));
            root.querySelector('[data-world-tool="radiation"]').click();root.querySelector('[data-world-tool="height"]').click();
            const heightRetained=document.getElementById('tile-height-number').value==='1.7';
            root.querySelector('[data-world-tool="select"]').click();
            return {result,treeRoadHidden,roadShown,roadBrushHidden,forest,factory,heightRetained,
                helpClosed:!document.getElementById('world-editor-help').open,
                selectionHelpClosed:!document.getElementById('world-selection-help').open};
        })()`);
        for(const entry of ux.result)assert.deepEqual(entry.actual,entry.expected,entry.tool+' contextual settings');
        for(const value of [ux.treeRoadHidden,ux.roadShown,ux.roadBrushHidden,ux.forest.roadHidden,ux.forest.densityShown,
            ux.factory.densityHidden,ux.factory.activeShown,ux.heightRetained,ux.helpClosed,ux.selectionHelpClosed])assert.ok(value);
        await page.evaluate(`document.querySelector('#world-editor-help summary').focus()`);
        await page.call('Input.dispatchKeyEvent',{type:'keyDown',code:'Enter',key:'Enter',windowsVirtualKeyCode:13,text:'\r'});
        await page.call('Input.dispatchKeyEvent',{type:'keyUp',code:'Enter',key:'Enter',windowsVirtualKeyCode:13});
        assert.equal(await page.evaluate(`document.getElementById('world-editor-help').open`),true);
        const helpScreenshot=path.join(profile,'world-editor-help.png');
        fs.writeFileSync(helpScreenshot,Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        console.log('Editor help screenshot:',helpScreenshot);
        await page.evaluate(`document.querySelector('#world-editor-help summary').click();document.activeElement.blur()`);
        await page.evaluate(`document.querySelector('#panel-tools .toggle-btn').click()`);
        const headerPoint=await page.evaluate(`(()=>{const r=document.querySelector('#panel-tools .panel-header').getBoundingClientRect();return {x:r.left+35,y:r.top+r.height/2}})()`);
        await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...headerPoint});
        await page.call('Input.dispatchMouseEvent',{type:'mousePressed',...headerPoint,button:'left',buttons:1,clickCount:1});
        await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',x:100,y:650,button:'left',buttons:1});
        await page.call('Input.dispatchMouseEvent',{type:'mouseReleased',x:100,y:650,button:'left',buttons:0,clickCount:1});
        const panelPosition=await page.evaluate(`JSON.parse(localStorage.getItem('panelStates'))['panel-tools'].position`);
        assert.ok(Number.isFinite(panelPosition.left)&&Number.isFinite(panelPosition.top));
        assert.equal(await page.evaluate(`document.getElementById('panel-tools').classList.contains('collapsed')`),true);
        console.log('PASS: six tools, contextual brush/road/vegetation/base settings, retained values, collapsed keyboard-accessible help');
        const click = async point => {
            await page.call('Input.dispatchMouseEvent', { type: 'mouseMoved', ...point });
            await page.call('Input.dispatchMouseEvent', { type: 'mousePressed', ...point, button: 'left', buttons: 1, clickCount: 1 });
            await page.call('Input.dispatchMouseEvent', { type: 'mouseReleased', ...point, button: 'left', buttons: 0, clickCount: 1 });
        };
        const project = (x, y, z) => page.evaluate(`(()=>{const p=new threeModule.Vector3(${x},${y},${z}).project(sceneModule.camera);
            const r=sceneModule.renderer.domElement.getBoundingClientRect();return {x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2}})()`);
        const mapBeforeTools=await request(`/lobbies/${lobby.id}/chunks?min_chunk_x=0&max_chunk_x=1&min_chunk_y=0&max_chunk_y=0`,null,token,'GET');
        const exerciseCommonTools=async client=>{
            await client.evaluate(`if(document.getElementById('panel-common-tools').hidden)document.getElementById('common-tools-toggle').click()`);
            const clickAt=async point=>{
                await client.call('Input.dispatchMouseEvent',{type:'mouseMoved',...point});
                await client.call('Input.dispatchMouseEvent',{type:'mousePressed',...point,button:'left',buttons:1,clickCount:1});
                await client.call('Input.dispatchMouseEvent',{type:'mouseReleased',...point,button:'left',buttons:0,clickCount:1});
            };
            const pointAt=(x,z)=>client.evaluate(`(()=>{const p=new threeModule.Vector3(${x},1,${z}).project(sceneModule.camera);const r=sceneModule.renderer.domElement.getBoundingClientRect();return {x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2}})()`);
            const first=await pointAt(27.5,7.5),second=await pointAt(30.5,11.5);
            assert.equal(await client.evaluate(`document.getElementById('panel-tools').style.display==='none' && document.getElementById('panel-common-tools').getClientRects().length>0`),true);
            assert.equal(await client.evaluate(`document.getElementById('chat-toggle').textContent.trim()==='' && !!document.querySelector('#chat-toggle svg')`),true);
            await client.evaluate(`document.querySelector('[data-common-map-tool="ping"]').click()`);
            await client.evaluate(`window.receivedCommonPings=0;void window.socket.on('map_ping',()=>window.receivedCommonPings++)`);
            await clickAt(first);
            await client.until(`receivedCommonPings>0 && sceneModule.scene.children.some(m=>m.userData.commonMapPing?.tile_x===27 && m.userData.commonMapPing?.tile_y===7 && m.isSprite && !m.material.fog && !m.material.sizeAttenuation)`, 'shared screen-sized map ping rendered');
            await pause(550);
            await clickAt(second);
            await client.until(`receivedCommonPings===2 && sceneModule.scene.children.some(m=>m.userData.commonMapPing?.tile_x===30 && m.userData.commonMapPing?.tile_y===11)`, 'repeat ping without choosing the tool again');
            assert.equal(await client.evaluate(`document.querySelector('[data-common-map-tool="ping"]').getAttribute('aria-pressed')`),'true');
            const pingCamera=await client.evaluate(`({position:sceneModule.camera.position.toArray(),target:sceneModule.controls.target.toArray()})`);
            await client.evaluate(`sceneModule.controls.target.set(32,1,16);sceneModule.camera.position.set(64,100,64);sceneModule.controls.update();sceneModule.camera.updateMatrixWorld()`);
            await pause(100);
            const pingRole=await client.evaluate(`window.isGM?'gm':'player'`);
            const pingScreenshot=path.join(profile,`map-ping-overview-${pingRole}.png`);
            fs.writeFileSync(pingScreenshot,Buffer.from((await client.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
            console.log('Overview ping screenshot:',pingScreenshot);
            await client.evaluate(`sceneModule.camera.position.fromArray(${JSON.stringify(pingCamera.position)});sceneModule.controls.target.fromArray(${JSON.stringify(pingCamera.target)});sceneModule.controls.update();sceneModule.camera.updateMatrixWorld()`);
            await client.evaluate(`document.querySelector('[data-common-map-tool="measure"]').click()`);
            await clickAt(first);
            await client.call('Input.dispatchMouseEvent',{type:'mouseMoved',...second});
            await clickAt(second);
            await client.until(`document.getElementById('common-map-result').textContent.startsWith('5.00')`, 'five-cell ruler');
            assert.equal(await client.evaluate(`sceneModule.scene.children.filter(m=>m.userData.commonMapMeasure).length`),1);
            assert.equal(await client.evaluate(`document.querySelector('[data-common-map-tool="measure"]').getAttribute('aria-pressed')`),'true');
            await clickAt(second);
            await clickAt(first);
            await client.until(`document.getElementById('common-map-result').textContent.startsWith('5.00')`, 'repeat measurement without choosing the tool again');
            await pause(400);
            const rulerRole=await client.evaluate(`window.isGM?'gm':'player'`);
            const rulerScreenshot=path.join(profile,`common-ruler-${rulerRole}.png`);
            fs.writeFileSync(rulerScreenshot,Buffer.from((await client.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
            console.log('Common ruler screenshot:',rulerScreenshot);
            await client.evaluate(`document.getElementById('common-tools-toggle').click()`);
            assert.equal(await client.evaluate(`document.getElementById('panel-common-tools').hidden && !sceneModule.scene.children.some(m=>m.userData.commonMapMeasure)`),true);
            await client.evaluate(`document.getElementById('common-tools-toggle').click()`);
            await client.evaluate(`document.querySelector('[data-common-map-tool="marker"]').click();void window.socket.on('marker_added',m=>window.lastCommonMarker=m)`);
            await clickAt(first);
            await client.until(`document.getElementById('marker-create-modal').style.display==='flex'`, 'existing marker dialog from map click');
            assert.ok(Math.abs(await client.evaluate(`Number(document.getElementById('marker-create-pos-x').value)`)-27.5)<0.01);
            await client.evaluate(`document.getElementById('marker-create-name').value='Common tools marker';window.submitCreateMarker()`);
            await client.until(`window.lastCommonMarker?.name==='Common tools marker'`, 'marker created through existing socket');
            assert.equal(await client.evaluate(`document.querySelector('[data-common-map-tool="marker"]').getAttribute('aria-pressed')`),'true');
            await client.evaluate(`document.querySelector('[data-common-map-tool="dice"]').click()`);
            assert.equal(await client.evaluate(`document.getElementById('common-dice-count').value`),'1');
            assert.deepEqual(await client.evaluate(`[...document.querySelectorAll('[data-dice-sides]')].map(b=>Number(b.dataset.diceSides))`),[4,6,8,10,12,20]);
            await client.evaluate(`document.querySelector('[data-dice-count="1"]').click();document.querySelector('[data-dice-sides="6"]').click()`);
            await client.until(`[...document.querySelectorAll('#chat .chat-roll')].some(m=>m.textContent.includes('/roll 2d6'))`, 'two dice rolled immediately through chat');
            await client.evaluate(`document.querySelector('[data-dice-count="-1"]').click();document.querySelector('[data-dice-count="-1"]').click();document.querySelector('[data-dice-sides="20"]').click()`);
            assert.equal(await client.evaluate(`document.getElementById('common-dice-count').value`),'1');
            await client.until(`[...document.querySelectorAll('#chat .chat-roll')].some(m=>m.textContent.includes('/roll 1d20'))`, 'repeat roll with minimum count one');
            const diceScreenshot=path.join(profile,`dice-buttons-${pingRole}.png`);
            fs.writeFileSync(diceScreenshot,Buffer.from((await client.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
            console.log('Dice buttons screenshot:',diceScreenshot);
            await client.evaluate(`if(!document.getElementById('panel-chat').classList.contains('collapsed'))document.querySelector('#panel-chat .toggle-btn').click()`);
            await client.evaluate(`document.getElementById('chat-close').click();document.querySelector('[data-common-map-tool="measure"]').click()`);
            await clickAt(first);
            await client.call('Input.dispatchKeyEvent',{type:'keyDown',code:'Escape',key:'Escape'});
            await client.call('Input.dispatchKeyEvent',{type:'keyUp',code:'Escape',key:'Escape'});
            assert.equal(await client.evaluate(`sceneModule.scene.children.some(m=>m.userData.commonMapMeasure)`),false);
        };
        await page.evaluate(`window.toggleEditMode();void window.socket.on('map_ping',p=>window.lastCommonPing=p)`);
        if (process.env.VTT_PERF_EDITOR_ONLY !== '1') {
        await exerciseCommonTools(page);
        await request('/auth/register',{username:'browser-player',email:'browser-player@example.test',password:'browser-test-password'});
        const playerLogin=await request('/auth/login',{username:'browser-player',password:'browser-test-password'});
        await request(`/lobbies/${lobby.id}/join`,{},playerLogin.access_token);
        const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();
        browserProtocol=await connect(version.webSocketDebuggerUrl);
        const browserContext=await browserProtocol.call('Target.createBrowserContext');
        const playerTarget=await browserProtocol.call('Target.createTarget',{url:base,browserContextId:browserContext.browserContextId,background:true});
        const playerTargets=await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
        playerPage=await connect(playerTargets.find(t=>t.id===playerTarget.targetId).webSocketDebuggerUrl);
        await playerPage.call('Page.enable');await playerPage.call('Runtime.enable');
        await playerPage.until(`location.origin===${JSON.stringify(base)}`,'player origin');
        await playerPage.evaluate(`localStorage.setItem('access_token',${JSON.stringify(playerLogin.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(playerLogin.user_id))})`);
        await playerPage.call('Page.navigate',{url:`${base}/lobbies/${lobby.id}/page`});
        await playerPage.until(`window.isGM===false && document.getElementById('panel-common-tools').dataset.initialized==='true'`,'player tools startup');
        await playerPage.evaluate(`(async()=>{window.sceneModule=await import('/static/js/lobby3d.js');window.threeModule=await import('three');sceneModule.controls.target.set(31.5,1,10.5);sceneModule.camera.position.set(35,9,19);sceneModule.controls.update();sceneModule.camera.updateMatrixWorld()})()`);
        await playerPage.until(`sceneModule.chunksMap.size===2 && document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'player scene ready');
        await exerciseCommonTools(playerPage);
        await page.until(`window.lastCommonPing?.user_id===${playerLogin.user_id}`,'player ping reaches GM');
        const commonScreenshot=path.join(profile,'player-common-tools.png');
        fs.writeFileSync(commonScreenshot,Buffer.from((await playerPage.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        console.log('Player common tools screenshot:',commonScreenshot);
        assert.deepEqual(await request(`/lobbies/${lobby.id}/chunks?min_chunk_x=0&max_chunk_x=1&min_chunk_y=0&max_chunk_y=0`,null,token,'GET'),mapBeforeTools,'common tools never edit tiles');
        await browserProtocol.call('Target.closeTarget',{targetId:playerTarget.targetId});playerPage.close();playerPage=null;
        await browserProtocol.call('Target.disposeBrowserContext',{browserContextId:browserContext.browserContextId});
        await page.evaluate(`window.toggleEditMode()`);
        console.log('PASS: GM and actual player use ping, ruler, marker and dice without editor; player editor hidden, shared ping received and map unchanged');
        } else {
            // Focus this regression run on picking, editing and chunk updates.
            await page.evaluate(`window.toggleEditMode()`);
        }
        if (process.env.VTT_UI_ONLY === '1') return;
        const centerY = await page.evaluate(`1+sceneModule.getObjectHeightOffset('factory')`);
        const start = await project(31.5, centerY, 10.5);
        await click(start);
        await page.until(`!document.getElementById('world-selection-editor').hidden`, 'object selection');
        assert.equal(await page.evaluate(`document.elementFromPoint(${start.x},${start.y})===sceneModule.renderer.domElement`), true);
        assert.equal(await page.evaluate(`document.getElementById('tile-info').style.display`), 'none');
        assert.equal(await page.evaluate(`document.getElementById('world-selection-active-option').hidden`), false);
        const selectedTitle = await page.evaluate(`document.getElementById('world-selection-title').textContent`);
        assert.ok(selectedTitle.length > 0);
        await page.call('Input.dispatchMouseEvent', { type: 'mousePressed', ...start, button: 'right', buttons: 2 });
        await page.call('Input.dispatchMouseEvent', { type: 'mouseReleased', ...start, button: 'right', buttons: 0 });
        assert.equal(await page.evaluate(`document.getElementById('world-selection-editor').hidden && !window.hasDirectWorldObjectSelection() && sceneModule.controls.enabled && window.worldEditorTool==='select'`), true);
        await click(start);
        await page.until(`!document.getElementById('world-selection-editor').hidden`, 'reselect after RMB deselection');
        assert.equal(await page.evaluate(`(()=>{const t=document.getElementById('world-selection-title');
            const p=document.getElementById('world-selection-editor').getBoundingClientRect();const r=t.getBoundingClientRect();
            return r.left>=p.left && r.right<=p.right && r.top>=p.top && t.scrollWidth<=t.clientWidth})()`), true);
        const key = async (code, value) => {
            await page.call('Input.dispatchKeyEvent', { type: 'keyDown', code, key: value });
            await page.call('Input.dispatchKeyEvent', { type: 'keyUp', code, key: value });
        };
        const sourceObject = `sceneModule.chunksMap.get('0,0').tilesData[10][31].objects[0]`;
        const settled = `!document.getElementById('world-selection-editor').disabled`;
        await page.evaluate(`window.hotkeyRequests=0;window.hotkeyFetch=window.fetch;window.fetch=(...args)=>{
            if(String(args[0]).includes('/world-objects/edit'))hotkeyRequests++;return hotkeyFetch(...args)};`);
        await key('Equal', '+');
        await page.until(`${sourceObject}.scale===1.1 && ${settled}`, '+ scale');
        await key('Minus', '-');
        await page.until(`${sourceObject}.scale===1 && ${settled}`, '- scale');
        // Adjacent key presses queue instead of losing steps during the request.
        await key('KeyQ', 'q');
        await key('KeyE', 'e');
        await page.until(`${sourceObject}.rotation===0 && ${settled}`, 'Q/E rotation priority');
        assert.equal(await page.evaluate(`worldEditorTool==='select' && !document.querySelector('.map-editor-wheel')`), true);
        // Range/checkbox focus must not hand R or E back to global shortcuts.
        await page.evaluate(`document.getElementById('world-selection-scale').focus()`);
        let requestCount = await page.evaluate('hotkeyRequests');
        await page.call('Input.dispatchMouseEvent', { type: 'mouseMoved', ...start });
        await key('KeyR', 'r');
        assert.equal(await page.evaluate(`worldEditorTool==='select' && !window.eraserMode && !sceneModule.controls.enabled`), true);
        await page.call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: start.x + 20, y: start.y });
        assert.equal(await page.evaluate('hotkeyRequests'), requestCount);
        assert.equal(await page.evaluate(`Number(document.getElementById('world-selection-scale-number').value)`), 1.2);
        await click({ x: start.x + 20, y: start.y });
        await page.until(`${sourceObject}.scale===1.2 && ${settled}`, 'R confirm');
        requestCount = await page.evaluate('hotkeyRequests');
        await key('KeyR', 'r');
        await page.call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: start.x - 20, y: start.y });
        await page.call('Input.dispatchMouseEvent', { type: 'mousePressed', x: start.x - 20, y: start.y, button: 'right', buttons: 2 });
        await page.call('Input.dispatchMouseEvent', { type: 'mouseReleased', x: start.x - 20, y: start.y, button: 'right', buttons: 0 });
        assert.equal(await page.evaluate('hotkeyRequests'), requestCount);
        assert.equal(await page.evaluate(`${sourceObject}.scale`), 1.2);
        assert.equal(await page.evaluate(`Number(document.getElementById('world-selection-scale-number').value)`), 1.2);
        await key('KeyT', 't');
        await page.call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: start.x + 10, y: start.y });
        assert.equal(await page.evaluate(`Number(document.getElementById('world-selection-rotation-number').value)`), 345);
        await click({ x: start.x + 10, y: start.y });
        await page.until(`${sourceObject}.rotation===345 && ${settled}`, 'T confirm');
        requestCount = await page.evaluate('hotkeyRequests');
        await key('KeyT', 't');
        await page.call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: start.x - 30, y: start.y });
        await key('Escape', 'Escape');
        assert.equal(await page.evaluate('hotkeyRequests'), requestCount);
        assert.equal(await page.evaluate(`${sourceObject}.rotation`), 345);
        assert.equal(await page.evaluate(`document.getElementById('world-selection-editor').hidden`), false);
        await page.call('Input.dispatchMouseEvent', { type: 'mouseMoved', ...start });
        await key('KeyG', 'g');
        const gPoint = await project(31.7, 1, 10.6);
        await page.call('Input.dispatchMouseEvent', { type: 'mouseMoved', ...gPoint });
        assert.equal(await page.evaluate(`(()=>{const ground=sceneModule.worldPointOnTerrain(${gPoint.x},${gPoint.y});
            const preview=sceneModule.scene.children.find(mesh=>mesh.userData.previewType==='factory');
            return ground && preview && Math.hypot(ground.x-preview.position.x,ground.z-preview.position.z)<0.000001})()`), true);
        assert.equal(await page.evaluate('hotkeyRequests'), requestCount);
        await click(gPoint);
        await page.until(`Number(${sourceObject}.x)>0 && ${settled}`, 'G confirm');
        requestCount = await page.evaluate('hotkeyRequests');
        const beforeCancel = await page.evaluate(`JSON.stringify(${sourceObject})`);
        await key('KeyG', 'g');
        await page.call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: start.x + 40, y: start.y });
        await page.call('Input.dispatchMouseEvent', { type: 'mousePressed', x: start.x + 40, y: start.y, button: 'right', buttons: 2 });
        await page.call('Input.dispatchMouseEvent', { type: 'mouseReleased', x: start.x + 40, y: start.y, button: 'right', buttons: 0 });
        assert.equal(await page.evaluate('hotkeyRequests'), requestCount);
        assert.equal(await page.evaluate(`JSON.stringify(${sourceObject})`), beforeCancel);
        console.log('PASS: wrapped title, +/- scale, Q/E priority, R/G/T mouse preview, LMB confirmation, RMB/Esc cancellation');
        await page.evaluate(`document.getElementById('world-selection-active').click()`);
        await page.until(`sceneModule.chunksMap.get('0,0').tilesData[10][31].objects[0].is_active===false && !document.getElementById('world-selection-editor').disabled`, 'factory protection toggle');
        await page.evaluate(`window.originalHouse=sceneModule.chunksMap.get('1,0').houses;window.originalGround=sceneModule.chunksMap.get('0,0').ground;
            const n=document.getElementById('world-selection-rotation-number');n.focus();n.value='37.5';n.dispatchEvent(new Event('input',{bubbles:true}));n.dispatchEvent(new Event('change',{bubbles:true}));n.blur();`);
        await page.until(`sceneModule.chunksMap.get('0,0').tilesData[10][31].objects[0].rotation===37.5 && !document.getElementById('world-selection-editor').disabled`, 'rotation save');
        await page.evaluate(`(()=>{const n=document.getElementById('world-selection-scale-number');n.focus();n.value='1.25';n.dispatchEvent(new Event('input',{bubbles:true}));n.dispatchEvent(new Event('change',{bubbles:true}));n.blur()})()`);
        await page.until(`sceneModule.chunksMap.get('0,0').tilesData[10][31].objects[0].scale===1.25 && !document.getElementById('world-selection-editor').disabled`, 'scale save');
        assert.equal(await page.evaluate(`sceneModule.chunksMap.get('1,0').houses===originalHouse && sceneModule.chunksMap.get('0,0').ground===originalGround`), true);
        // A second complete client observes the existing Socket.IO update path.
        const target = await page.call('Target.createTarget', { url: `${base}/lobbies/${lobby.id}/page`, background: true });
        const tabs = await (await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
        observer = await connect(tabs.find(tab => tab.id === target.targetId).webSocketDebuggerUrl);
        await observer.until('Boolean(window.socket && window.isGM)', 'observer startup');
        await observer.evaluate(`(async()=>{window.sceneModule=await import('/static/js/lobby3d.js')})()`);
        await observer.until(`sceneModule.chunksMap.size===2`, 'observer chunks');
        await page.call('Page.bringToFront');
        await page.until(`document.hasFocus()`, 'GM focus restored');
        await page.evaluate(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
        const beforeDrag = await page.evaluate(sourceObject);
        const from = await project(31.5 + (beforeDrag.x || 0), 1 + (centerY - 1) * 1.25, 10.5 + (beforeDrag.z || 0));
        const to = await project(32.6, 1 + (centerY - 1) * 1.25, 10.5 + (beforeDrag.z || 0));
        assert.equal(await page.evaluate(`document.elementFromPoint(${from.x},${from.y})===sceneModule.renderer.domElement`),true,'drag source is not covered by UI');
        await page.call('Input.dispatchMouseEvent', { type: 'mouseMoved', ...from });
        await page.call('Input.dispatchMouseEvent', { type: 'mousePressed', ...from, button: 'left', buttons: 1, clickCount: 1 });
        for (let i = 1; i <= 10; i++) {
            await page.call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: from.x + (to.x-from.x)*i/10,
                y: from.y+(to.y-from.y)*i/10, button: 'left', buttons: 1 });
            await pause(30);
        }
        await page.call('Input.dispatchMouseEvent', { type: 'mouseReleased', ...to, button: 'left', buttons: 0, clickCount: 1 });
        const moved = `sceneModule.chunksMap.get('1,0').tilesData[10][0].objects.some(o=>o.type==='factory' && o.rotation===37.5 && o.scale===1.25)`;
        await page.until(moved, 'cross-chunk move');
        await observer.until(moved, 'socket observer move');
        assert.equal(await page.evaluate(`sceneModule.controls.enabled && sceneModule.chunksMap.get('0,0').tilesData[10][31].objects.length===1`), true);
        const screenshot = path.join(profile, 'selected-object.png');
        const capture = await page.call('Page.captureScreenshot');
        fs.writeFileSync(screenshot, Buffer.from(capture.data, 'base64'));
        console.log(`Scene screenshot: ${screenshot}`);
        // Reload reconstructs transforms from the stored chunk JSON.
        await page.call('Page.reload');
        await page.until('Boolean(window.beginDirectWorldObjectEdit && window.toggleEditMode)', 'reload startup');
        await page.evaluate(`(async()=>{window.sceneModule=await import('/static/js/lobby3d.js')})()`);
        await page.until(`sceneModule.chunksMap.size===2`, 'reload chunks');
        await page.until(`document.getElementById('panel-chat').dataset.chatInitialized==='true'`, 'chat restored after reload');
        assert.equal(await page.evaluate(`document.getElementById('panel-chat').hidden && document.getElementById('panel-chat').dataset.chatDocked==='false' && document.getElementById('panel-chat').classList.contains('collapsed')`),true);
        await page.evaluate(`document.getElementById('chat-toggle').click();document.querySelector('#panel-chat .toggle-btn').click()`);
        assert.deepEqual(await page.evaluate(`(()=>{const r=document.getElementById('panel-chat').getBoundingClientRect();return {left:r.left,top:r.top}})()`),chatPosition);
        await page.until(`document.querySelector('#chat .chat-human') && document.querySelector('#chat .chat-roll')`, 'chat history after reload');
        await page.evaluate(`document.getElementById('chat-pin').click()`);
        await page.until(`document.getElementById('scene-loading-screen').classList.contains('is-hidden')`, 'scene ready behind chat');
        await page.evaluate(`sceneModule.controls.target.set(31.5,1,10.5);sceneModule.camera.position.set(35,9,19);
            sceneModule.controls.update();sceneModule.camera.updateMatrixWorld();
            document.getElementById('message-input').value='/roll invalid';window.sendMessage()`);
        await page.until(`document.querySelector('#chat .chat-event') && sceneModule.chunksMap.get('1,0').landmarks.factory?.visible`, 'event and visible scene behind docked chat');
        await pause(500);
        const chatScreenshot=path.join(profile,'docked-chat.png');
        fs.writeFileSync(chatScreenshot,Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        console.log('Docked chat screenshot:',chatScreenshot);
        await page.evaluate(`document.getElementById('chat-pin').click()`);
        const floatingChatScreenshot=path.join(profile,'floating-chat.png');
        fs.writeFileSync(floatingChatScreenshot,Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        console.log('Floating chat screenshot:',floatingChatScreenshot);
        await page.evaluate(`document.getElementById('chat-close').click()`);
        await page.until(`document.querySelector('#character-tab-mutants')?.getAttribute('aria-selected')==='true'`, 'personal tab restored after reload');
        assert.equal(await page.evaluate(`document.querySelector('#character-panel-mutants .character-folder').open && document.querySelector('[data-character-id="${mutantCharacter.id}"]').closest('.character-folder')!==null`),true);
        assert.equal(await page.evaluate(`document.getElementById('panel-tools').classList.contains('collapsed')`),true);
        assert.deepEqual(await page.evaluate(`JSON.parse(localStorage.getItem('panelStates'))['panel-tools'].position`),panelPosition);
        assert.equal(await page.evaluate(moved), true);
        const chunks = await request(`/lobbies/${lobby.id}/chunks?min_chunk_x=1&max_chunk_x=1&min_chunk_y=0&max_chunk_y=0`, null, token, 'GET');
        assert.equal(chunks[0].data[10][0].objects.length, 2);
        const saved = chunks[0].data[10][0].objects.find(object => object.type === 'factory');
        assert.equal(saved.is_active, false);
        await page.evaluate(`(async()=>{window.threeModule=await import('three');sceneModule.controls.target.set(31.5,1,10.5);
            sceneModule.camera.position.set(35,9,19);sceneModule.controls.update();sceneModule.camera.updateMatrixWorld();window.toggleEditMode()})()`);
        await page.until(`sceneModule.chunksMap.get('1,0').landmarks.factory.visible`, 'reloaded factory visible');
        await pause(500);
        const savedPoint = await project(32.5 + saved.x, 1 + (centerY - 1) * saved.scale, 10.5 + saved.z);
        await page.evaluate(`window.editRequests=0;window.savedFetch=window.fetch;window.fetch=(...args)=>{
            if(String(args[0]).includes('/world-objects/edit'))editRequests++;return savedFetch(...args)};`);
        await page.call('Input.dispatchMouseEvent', { type: 'mouseMoved', ...savedPoint });
        await page.call('Input.dispatchMouseEvent', { type: 'mousePressed', ...savedPoint, button: 'left', buttons: 1, clickCount: 1 });
        await page.call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: savedPoint.x + 40, y: savedPoint.y - 15, button: 'left', buttons: 1 });
        await page.call('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape' });
        await page.call('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape' });
        await page.call('Input.dispatchMouseEvent', { type: 'mouseReleased', x: savedPoint.x + 40, y: savedPoint.y - 15, button: 'left', buttons: 0, clickCount: 1 });
        assert.equal(await page.evaluate('editRequests'), 0);
        assert.equal(await page.evaluate('sceneModule.controls.enabled'), true);
        assert.equal(await page.evaluate(moved), true);
        await click(savedPoint);
        await page.until(`!document.getElementById('world-selection-editor').hidden`, 'reselect after cancel');
        await page.evaluate(`document.getElementById('world-selection-delete').click()`);
        const deleted = `!sceneModule.chunksMap.get('1,0').tilesData[10][0].objects.some(o=>o.type==='factory')`;
        await page.until(deleted, 'direct delete');
        await observer.until(deleted, 'observer delete');
        assert.equal(await page.evaluate('editRequests'), 1);
        assert.equal(await page.evaluate(`sceneModule.chunksMap.get('1,0').tilesData[10][0].objects[0].type`), 'house');
        const treePoint = await project(31.9, 1.3, 10.9);
        await click(treePoint);
        await page.until(`!document.getElementById('world-selection-editor').hidden`, 'tree selection');
        assert.equal(await page.evaluate(`document.getElementById('world-selection-active-option').hidden`), true);
        await page.evaluate(`document.getElementById('world-selection-details').click()`);
        assert.equal(await page.evaluate(`document.getElementById('tile-edit-modal').style.display`), 'flex');
        assert.equal(await page.evaluate(`document.getElementById('world-existing-type').value`), 'tree');
        console.log('PASS: scene picking, exact rotation/scale, factory protection, cross-chunk drag, Socket.IO observer, reload, unchanged meshes, Esc cancellation, delete, contextual fields, legacy details');
        await page.evaluate(`(async()=>{window.closeTileEditModal();window.mapEditor=await import('/static/js/mapEdit.js');
            mapEditor.selectWorldObject('tree');mapEditor.selectWorldEditorTool('objects');mapEditor.setBrushRadius(2);
            const n=document.getElementById('world-vegetation-density-number');n.value='100';n.dispatchEvent(new Event('input',{bubbles:true}));
            sceneModule.controls.target.set(31.5,1,18.5);sceneModule.camera.position.set(35,15,28);
            sceneModule.controls.update();sceneModule.camera.updateMatrixWorld();
            window.brushGround=sceneModule.chunksMap.get('0,0').ground;
            const panel=document.getElementById('panel-tools');if(!panel.classList.contains('collapsed'))panel.querySelector('.toggle-btn').click();
        })()`);
        await request(`/lobbies/${lobby.id}/chunks/0/0/tile/30/18`, { terrain: 'water' }, token, 'PATCH');
        await request(`/lobbies/${lobby.id}/chunks/1/0/tile/0/18`, { terrain: 'rock' }, token, 'PATCH');
        await page.until(`sceneModule.chunksMap.get('0,0').tilesData[18][30].terrain==='water' && sceneModule.chunksMap.get('1,0').tilesData[18][0].terrain==='rock'`, 'ground restrictions setup');
        await pause(700);
        const vegetationCount = `(()=>{let n=0;for(const e of sceneModule.chunksMap.values())for(let y=16;y<=20;y++)for(let x=0;x<32;x++)
            n+=(e.tilesData[y][x].objects||[]).filter(o=>o.type==='tree'||o.type==='forest').length;return n})()`;
        const brushPoint = await project(31.5, 1, 18.5);
        assert.equal(await page.evaluate(`document.elementFromPoint(${brushPoint.x},${brushPoint.y})===sceneModule.renderer.domElement`), true);
        await click(brushPoint);
        await page.until(`(${vegetationCount})===23`, '5x5 vegetation brush across chunks');
        await observer.until(`(${vegetationCount})===23`, 'vegetation batch socket delivery');
        await click(brushPoint);
        assert.equal(await page.evaluate(vegetationCount), 23);
        await page.call('Input.dispatchMouseEvent', { type: 'mousePressed', ...brushPoint, button: 'left', buttons: 1, clickCount: 1 });
        const strokeEnd = await project(34.5, 1, 18.5);
        await page.call('Input.dispatchMouseEvent', { type: 'mouseMoved', ...strokeEnd, button: 'left', buttons: 1 });
        await page.call('Input.dispatchMouseEvent', { type: 'mouseReleased', ...strokeEnd, button: 'left', buttons: 0, clickCount: 1 });
        await page.until(`(${vegetationCount})===38`, 'fast vegetation stroke interpolation');
        await observer.until(`(${vegetationCount})===38`, 'stroke persisted and delivered');
        await page.evaluate(`mapEditor.selectWorldObject('forest');mapEditor.setBrushRadius(0)`);
        const waterPoint = await project(30.5, 1.01, 18.5);
        const rockPoint = await project(32.5, 1, 18.5);
        await click(waterPoint);await click(rockPoint);
        assert.equal(await page.evaluate(vegetationCount), 38);
        await page.evaluate(`document.getElementById('world-vegetation-ignore-ground').checked=true`);
        await click(waterPoint);await click(rockPoint);
        await page.until(`(${vegetationCount})===40`, 'ignore water and rock placement restriction');
        await observer.until(`(${vegetationCount})===40`, 'override saved');
        await page.evaluate(`mapEditor.setBrushRadius(4);const n=document.getElementById('world-vegetation-density-number');
            n.value='35';n.dispatchEvent(new Event('input',{bubbles:true}));
            sceneModule.controls.target.set(31.5,1,23);sceneModule.camera.position.set(35,17,34);sceneModule.controls.update();sceneModule.camera.updateMatrixWorld()`);
        await pause(500);
        await click(await project(31.5, 1, 25.5));
        const sparseCount = `(()=>{let n=0;for(const e of sceneModule.chunksMap.values())for(let y=21;y<=29;y++)for(let x=0;x<32;x++)
            n+=(e.tilesData[y][x].objects||[]).filter(o=>o.type==='forest').length;return n})()`;
        const sparse = await page.evaluate(sparseCount);
        assert.ok(sparse>0 && sparse<81, 'sparse 9x9 brush has groups and gaps');
        await observer.until(`(${sparseCount})===${sparse}`, 'sparse forest saved');
        const snapshot = await page.evaluate(`JSON.stringify([...sceneModule.chunksMap].map(([k,e])=>[k,e.tilesData.map(row=>row.map(t=>t.objects))]))`);
        const vegetationScreenshot=path.join(profile,'vegetation-brush.png');
        fs.writeFileSync(vegetationScreenshot,Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        console.log('Vegetation screenshot:',vegetationScreenshot);
        await page.call('Page.reload');
        await page.until(`Boolean(window.toggleEditMode && window.isGM)`, 'vegetation reload startup');
        await page.evaluate(`(async()=>{window.sceneModule=await import('/static/js/lobby3d.js')})()`);
        await page.until(`sceneModule.chunksMap.size===2`, 'vegetation reload chunks');
        assert.deepEqual(JSON.parse(await page.evaluate(`JSON.stringify([...sceneModule.chunksMap].map(([k,e])=>[k,e.tilesData.map(row=>row.map(t=>t.objects))]))`)),JSON.parse(snapshot));
        console.log('PASS: vegetation brush 1x1/5x5/9x9, fast stroke, cross-chunk batching, density, water/rock override, duplicate prevention, observer and exact saved transforms after reload');
        // Exercise the renderer on a 100x100 area without touching saved lobby data.
        const vegetationPerf = await page.evaluate(`(async()=>{
            sceneModule.setMapDimensions(4,4);
            for(let cy=0;cy<4;cy++)for(let cx=0;cx<4;cx++)if(!sceneModule.chunksMap.has(cx+','+cy))
                sceneModule.addChunk(cx,cy,Array.from({length:32},()=>Array.from({length:32},()=>({terrain:'grass',height:1,objects:[]}))));
            sceneModule.controls.target.set(50,1,50);sceneModule.camera.position.set(50,110,130);sceneModule.controls.update();
            await new Promise(r=>setTimeout(r,1500));
            const sample=()=>{sceneModule.renderer.shadowMap.needsUpdate=false;sceneModule.renderer.render(sceneModule.scene,sceneModule.camera);
                return {...sceneModule.renderer.info.render}};
            const before=sample();
            for(let y=0;y<100;y++)for(let x=0;x<100;x++)if((Math.imul(x,31)+Math.imul(y,17))%10<6)
                sceneModule.updateTileInChunk(Math.floor(x/32),Math.floor(y/32),x%32,y%32,{objects:[{type:x<50?'tree':'forest',scale:1,rotation:0}]});
            await new Promise(r=>setTimeout(r,1500));
            const after=sample();
            const meshes=()=>[...sceneModule.chunksMap.values()].flatMap(e=>[e.trees,e.landmarks.forest]).filter(Boolean);
            const active=meshes();const geometry=new Set(active.map(m=>m.geometry));
            const stale=()=>sceneModule.scene.children.filter(m=>m.isInstancedMesh&&geometry.has(m.geometry)&&!m.userData.worldInstanceBatch&&!meshes().includes(m)).length;
            for(let pass=0;pass<3;pass++){
                for(let y=0;y<100;y++)for(let x=0;x<100;x++)if((Math.imul(x,31)+Math.imul(y,17))%10<6)
                    sceneModule.updateTileInChunk(Math.floor(x/32),Math.floor(y/32),x%32,y%32,{objects:[{type:x<50?'tree':'forest',scale:1+pass*0.01,rotation:pass}]});
                await new Promise(r=>setTimeout(r,500));
            }
            const repeated=sample();
            sceneModule.renderer.shadowMap.needsUpdate=true;sceneModule.renderer.render(sceneModule.scene,sceneModule.camera);
            return {before,after,repeated,withShadows:{...sceneModule.renderer.info.render},meshCount:meshes().length,
                instances:meshes().reduce((n,m)=>n+m.count,0),stale:stale()};
        })()`);
        console.log('Vegetation 100x100 renderer measurements:',JSON.stringify(vegetationPerf));
        assert.ok(vegetationPerf.after.calls-vegetationPerf.before.calls<=32,'at most one vegetation draw per type and chunk');
        assert.equal(vegetationPerf.repeated.calls,vegetationPerf.after.calls,'repeated rebuilds do not accumulate draws');
        assert.equal(vegetationPerf.stale,0,'no detached vegetation batches left in scene');
        await page.call('Browser.close');
    } catch (error) {
        if (safeSession && page) {
            try {
                fs.writeFileSync(path.join(safeSession.artifacts,'failure.json'),JSON.stringify({error:String(error.message).replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g,'[test token]'),network:page.networkEvents},null,2));
                fs.writeFileSync(path.join(safeSession.artifacts,'failure.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
            } catch { console.error('Не удалось сохранить диагностику собственного запуска'); }
        }
        console.error(serverErrors.slice(-2500).replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g,'[test token]'));
        throw error;
    } finally {
        if (safeSession) {
            try { await safeSession.cleanup(browser,server); } finally { clearInterval(keepAlive); }
        } else {
        if (page && browser?.exitCode === null) {
            await page.call('Browser.close').catch(() => {});
            if (browser.exitCode === null) {
                await new Promise(resolve => {
                    const timer = setTimeout(resolve, 5000);
                    browser.once('exit', () => { clearTimeout(timer); resolve(); });
                });
            }
        }
        page?.close(); observer?.close(); playerPage?.close(); browserProtocol?.close(); browser?.kill(); server?.kill(); clearInterval(keepAlive);
        const profilePath = profile ? path.resolve(profile) : null;
        if (profilePath && !process.env.VTT_BROWSER_PROFILE && path.dirname(profilePath) === path.resolve(os.tmpdir())
            && path.basename(profilePath).startsWith('vtt-object-editor-')) {
            fs.rmSync(profilePath, { recursive: true, force: true, maxRetries: 20, retryDelay: 200 });
        }
        }
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
