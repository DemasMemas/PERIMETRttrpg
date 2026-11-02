// Защиты opt-in маршрута; импорт не запускает процессы или приложение.
const fs = require('node:fs'), path = require('node:path'), os = require('node:os');
const {spawn, execFile} = require('node:child_process');
const {promisify} = require('node:util');
const execute = promisify(execFile);
const root = path.resolve(__dirname, '../..');
const fixedVendor=require('./fixed_vendor.cjs');
const BROWSER_SCENARIOS=Object.freeze({pripyat:'VTT_PRIPYAT',widgets:'VTT_WIDGETS_ONLY',skills:'VTT_SKILLS_ONLY',movement:'VTT_MOVEMENT_ONLY',magazine:'VTT_MAGAZINE_ONLY','damage-revoke':'VTT_DAMAGE_REVOKE_ONLY','durable-reload':'VTT_DURABLE_RELOAD_ONLY','medical-retry':'VTT_MEDICAL_RETRY_ONLY','sheet-repro':'VTT_SHEET_REPRO_ONLY',rotation:'VTT_ROTATION_ONLY',surface:'VTT_MOVEMENT_ONLY',height:'VTT_MOVEMENT_ONLY','gm-relocation':'VTT_GM_RELOCATION_ONLY',terrain:'VTT_MOVEMENT_ONLY',walls:'VTT_MOVEMENT_ONLY',psi:'VTT_MOVEMENT_ONLY',climb:'VTT_MOVEMENT_ONLY',jump:'VTT_MOVEMENT_ONLY',graphics:'VTT_MOVEMENT_ONLY',ice:'VTT_MOVEMENT_ONLY',slippery:'VTT_MOVEMENT_ONLY',grenades:'VTT_MOVEMENT_ONLY','world-loading':'VTT_MOVEMENT_ONLY'});
const BROWSER_DB_MODES=Object.freeze({memory:'--confirm-memory-browser','owned-file':'--confirm-isolated-browser'});
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
function cleanEnvironment(source) {
    const result = {};
    for (const key of ['SYSTEMROOT','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA','SYSTEMDRIVE']) {
        if (Object.hasOwn(source,key)) result[key] = source[key];
    }
    return Object.assign(result, {PYTHON_DOTENV_DISABLED:'1',PYTHONDONTWRITEBYTECODE:'1',PYTHONIOENCODING:'utf-8'});
}
function validateEnvironment(env) {
    if (!/^[0-9a-f-]{36}$/.test(env.VTT_SAFE_RUN_ID || '') || env.PYTHON_DOTENV_DISABLED !== '1'
        || env.VTT_BROWSER_GPU !== '1' || !Object.hasOwn(BROWSER_SCENARIOS,env.VTT_SAFE_SCENARIO)) throw Error('Нет безопасного допуска браузера');
    if(!Object.hasOwn(BROWSER_DB_MODES,env.VTT_SAFE_DB_MODE)||env.VTT_SAFE_DB_CONFIRMATION!==BROWSER_DB_MODES[env.VTT_SAFE_DB_MODE])throw Error('Режим БД не подтверждён соответствующим флагом');
    if(['movement','magazine','damage-revoke','durable-reload','medical-retry','sheet-repro','skills','rotation','surface','height','gm-relocation','terrain','walls','psi','climb','jump','graphics','ice','slippery','grenades','world-loading'].includes(env.VTT_SAFE_SCENARIO)&&env.VTT_SAFE_DB_MODE!=='owned-file')throw Error('Сценарий требует owned-file маршрут');
    const flag=BROWSER_SCENARIOS[env.VTT_SAFE_SCENARIO];
    const allowed = new Set(['VTT_SAFE_RUN_ID','VTT_SAFE_SCENARIO','VTT_BROWSER_GPU','VTT_SAFE_DB_MODE','VTT_SAFE_DB_CONFIRMATION',flag]);
    for (const key of Object.keys(env)) if (key.startsWith('VTT_') && !allowed.has(key)) throw Error('Запрещённая VTT-настройка');
    if (env[flag] !== '1') throw Error('Сценарий не подтверждён');
    if (Object.hasOwn(env,'BROWSER_EXECUTABLE')) throw Error('Произвольный Chrome запрещён');
}
function ownedLayout(runId) {
    if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(runId))throw Error('Неверный UUID');
    const runDir=path.join(root,'.private-testing/reports',runId),directory=path.join(runDir,'db');
    return {runId,runDir,directory,path:path.join(directory,'test.sqlite'),manifest:path.join(directory,'manifest.json')};
}
function prepareOwnedRun(runId,io=fs) {
    const layout=ownedLayout(runId);
    const check=directory=>{const info=io.lstatSync(directory);if(!info.isDirectory()||info.isSymbolicLink()||io.realpathSync(directory)!==directory)throw Error('Каталог браузерной БД перенаправлен');};
    for(const directory of [root,path.join(root,'.private-testing'),path.join(root,'.private-testing/reports')])check(directory);
    io.mkdirSync(layout.runDir);check(layout.runDir); // EEXIST — отказ, повторного использования нет.
    io.mkdirSync(layout.directory);check(layout.directory);
    if(io.existsSync(layout.path)||io.existsSync(layout.manifest))throw Error('Браузерная БД/manifest уже существуют');
    const manifest={schema_version:1,run_id:runId,purpose:'test',mode:'owned-file',database:'test.sqlite'};
    io.writeFileSync(layout.manifest,JSON.stringify(manifest),{encoding:'utf8',flag:'wx'});
    if(io.lstatSync(layout.manifest).isSymbolicLink()||!io.lstatSync(layout.manifest).isFile()||io.realpathSync(layout.manifest)!==layout.manifest
        ||JSON.stringify(JSON.parse(io.readFileSync(layout.manifest,'utf8')))!==JSON.stringify(manifest))throw Error('Неверный manifest браузерной БД');
    return layout;
}
function databaseShutdownConfirmed(mode,server){return mode==='memory'||!server||server.exitCode===0;}
function assertIdentity(value, runId, mode='memory') {
    if(!Object.hasOwn(BROWSER_DB_MODES,mode))throw Error('Неизвестный режим identity');
    if (value?.run_id !== runId || value?.purpose !== 'test' || value?.database !== mode || value?.testing !== true || value?.threading !== true) throw Error('Неизвестный сервер: identity не совпадает');
    if(mode==='owned-file'&&value.database_path!==ownedLayout(runId).path.split(path.sep).join('/'))throw Error('Identity чужого файла БД');
    if(mode==='memory'&&value.database_path!=null)throw Error('Memory identity содержит файловое назначение');
}
function networkDiagnostic(method, params) {
    const record={method,requestId:params.requestId};
    if(method==='Network.loadingFailed') {
        record.error=/^net::[A-Z_]+$/.test(params.errorText||'')?params.errorText:'network error';
        return record;
    }
    if(!['Network.requestWillBeSent','Network.responseReceived'].includes(method))return null;
    const raw=params.request?.url||params.response?.url;
    try {const url=new URL(raw);record.url=url.origin+url.pathname;}catch{return null;}
    if(method==='Network.responseReceived')record.status=params.response.status;
    return record;
}
function assertGpu(renderer) {
    if (typeof renderer !== 'string' || !renderer.trim() || /swiftshader|software|llvmpipe|softpipe|basic render|microsoft basic/i.test(renderer)) throw Error('Аппаратный GPU не подтверждён');
}
function pythonServer(port, runId, mode='memory', scenario='pripyat') {
    if(!Object.hasOwn(BROWSER_SCENARIOS,scenario))throw Error('Unknown safe server scenario');
    if(scenario==='medical-retry'&&mode!=='owned-file')throw Error('Medical retry requires owned-file');
    if (!Number.isInteger(port) || port < 1 || port > 65535 || !/^[0-9a-f-]{36}$/.test(runId)||!Object.hasOwn(BROWSER_DB_MODES,mode)) throw Error('Неверный порт/run_id/режим');
    const setup=mode==='owned-file'?`from browser_db_guard import preflight_owned_database, assert_owned_database, owned_database_scope
owner = preflight_owned_database('${runId}')
from app.config import TestingConfig
from sqlalchemy.pool import QueuePool
TestingConfig.SQLALCHEMY_DATABASE_URI = owner.uri
TestingConfig.SQLALCHEMY_ENGINE_OPTIONS = {'poolclass': QueuePool, 'connect_args': {'check_same_thread': False, 'timeout': 30}}
`:`owner = None
`;
    return `import os, sys, threading
assert os.environ.get('PYTHON_DOTENV_DISABLED') == '1'
assert os.environ.get('VTT_SAFE_RUN_ID') == '${runId}'
sys.path.insert(0, ${JSON.stringify(path.join(root,'tests/helpers'))})
from isolation_guard import assert_memory_database, memory_database_scope
${setup}from app import create_app, socketio
from app.extensions import db
from sqlalchemy.pool import StaticPool
from werkzeug.serving import make_server
app = create_app('testing')
app.debug = False
assert socketio.async_mode == 'threading'
def check_database():
    if owner is not None:
        return assert_owned_database(app, db, owner)
    engine = assert_memory_database(app, db)
    assert isinstance(engine.pool, StaticPool)
    return engine
def database_scope():
    return owned_database_scope(app, db, owner) if owner is not None else memory_database_scope(app, db)
server = make_server('127.0.0.1', ${port}, app, threaded=True)
server.daemon_threads = False
@app.get('/__testing_identity/${runId}')
def identity():
    check_database()
    return dict(run_id='${runId}', purpose='test', database='${mode}',
                database_path=owner.path.as_posix() if owner is not None else None,
                testing=True, threading=True)
@app.post('/__testing_shutdown/${runId}')
def shutdown():
    threading.Thread(target=server.shutdown, daemon=True).start()
    return dict(stopping=True)
with app.app_context():
    with database_scope():
        check_database()
${scenario==='medical-retry'?`        from medical_retry_fixture import install
        install(app, db, check_database, '${runId}')
`:''}${['jump','slippery'].includes(scenario)?`        from jump_fixture import install
        install(app, db, check_database, '${runId}')
`:''}        try:
            server.serve_forever()
        finally:
            server.server_close()
`;
}
// Все PID для остановки получены только из собственных ChildProcess и снимка их потомков.
async function snapshot(pid) {
    const script = `$todo=@(${pid});$out=@();while($todo.Count){$id=$todo[0];$todo=@($todo|Select-Object -Skip 1);$p=Get-CimInstance Win32_Process -Filter "ProcessId=$id";if($p){$out+=@{pid=[int]$p.ProcessId;created=$p.CreationDate.ToUniversalTime().Ticks.ToString()};$todo+=@(Get-CimInstance Win32_Process -Filter "ParentProcessId=$id"|ForEach-Object{[int]$_.ProcessId})}};ConvertTo-Json -InputObject @($out) -Compress`;
    const {stdout} = await execute('powershell.exe',['-NoProfile','-NonInteractive','-Command',script],{windowsHide:true,timeout:15000});
    return JSON.parse(stdout || '[]');
}
function waitExit(child, timeout=10000) {
    if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve(true);
    return new Promise(resolve => {const timer=setTimeout(()=>{child.off('exit',done);resolve(false);},timeout);function done(){clearTimeout(timer);resolve(true);}child.once('exit',done);});
}
async function stopOwnedTree(child, owners, io={snapshot,execute,waitExit}) {
    if (!owners.has(child) || !Number.isInteger(child.pid) || child.pid <= 0) throw Error('Чужой процесс запрещено останавливать');
    // Если родитель уже исчез, происхождение оставшихся потомков не доказывается: профиль сохраняем.
    if (child.exitCode !== null || child.signalCode !== null) return false;
    const before = await io.snapshot(child.pid);
    if (child.exitCode !== null || child.signalCode !== null || !before.some(item=>item.pid===child.pid)) return false;
    await io.execute('taskkill.exe',['/PID',String(child.pid),'/T','/F'],{windowsHide:true}).catch(()=>{});
    if (!await io.waitExit(child)) return false;
    const ids=before.map(item=>item.pid);
    // Проверка PID+времени создания исключает ошибочное признание повторно использованного PID своим.
    for (let attempt=0;attempt<30;attempt++) {
        const script = `$ids=@(${ids.join(',')});$out=@();foreach($id in $ids){$p=Get-CimInstance Win32_Process -Filter "ProcessId=$id";if($p){$out+=@{pid=[int]$p.ProcessId;created=$p.CreationDate.ToUniversalTime().Ticks.ToString()}}};ConvertTo-Json -InputObject @($out) -Compress`;
        const {stdout} = await io.execute('powershell.exe',['-NoProfile','-NonInteractive','-Command',script],{windowsHide:true,timeout:15000});
        const remaining=JSON.parse(stdout||'[]');
        if (!remaining.some(p=>before.some(old=>old.pid===p.pid&&old.created===p.created))) return true;
        await pause(100);
    }
    return false;
}
function removeOwnedProfile(profile, ownedProfile, stopped, remove=fs.rmSync) {
    if (profile !== ownedProfile || !stopped) throw Error('Удаление профиля без владения/остановки запрещено');
    const absolute=path.resolve(profile);
    if (path.dirname(absolute)!==path.resolve(os.tmpdir())) throw Error('Профиль вне временного каталога');
    remove(absolute,{recursive:true,force:true,maxRetries:10,retryDelay:200});
}
function createSession(env) {
    validateEnvironment(env);
    const vendor=fixedVendor.loadFixedVendor(); // Все проверки/байты до mkdir и любого spawn.
    const runId=env.VTT_SAFE_RUN_ID;
    const artifacts=path.join(root,'.private-testing/reports',runId);
    const mode=env.VTT_SAFE_DB_MODE;
    const database=mode==='owned-file'?prepareOwnedRun(runId):null;
    if(mode==='memory')fs.mkdirSync(artifacts); // Новый каталог, без переиспользования.
    const owners=new Set(),clients=new Map();
    let ownedProfile, appBase=null, currentModule=null;
    const policy=fixedVendor.createInterceptionPolicy(vendor,{getBase:()=>appBase,getCurrentModule:()=>currentModule});
    return {runId,artifacts,base:null,verified:false,
        async prepareClient(client,url){this.track(client);await policy.prepare(client,url);return client;},
        paused(params,call){return policy.paused(params,call);},
        captureCurrentModule(file){
            if(!ownedProfile||path.resolve(file)!==path.join(ownedProfile,'regression-modules','locationScene.js'))throw Error('Неизвестный инструментированный модуль');
            currentModule=fs.readFileSync(file).toString('base64');
        },
        profile(){if(ownedProfile)throw Error('Профиль уже создан');return ownedProfile=fs.mkdtempSync(path.join(os.tmpdir(),'vtt-safe-'));},
        spawn(command,args,options){const child=spawn(command,args,{...options,env:cleanEnvironment(env),windowsHide:true});owners.add(child);child.on('error',()=>{});return child;},
        spawnServer(python,port){
            this.base=appBase=`http://127.0.0.1:${port}`;
            const child=spawn(python,['-I','-B','-u','-c',`import sys;sys.path.insert(0,${JSON.stringify(root)});`+pythonServer(port,runId,mode,env.VTT_SAFE_SCENARIO)],
                {cwd:root,windowsHide:true,env:{...cleanEnvironment(env),VTT_SAFE_RUN_ID:runId},stdio:['ignore','pipe','pipe']});
            owners.add(child);child.on('error',()=>{});return child;
        },
        async ready(){
            for(let i=0;i<100;i++){
                let response;
                try{response=await fetch(`${this.base}/__testing_identity/${runId}`,{signal:AbortSignal.timeout(1000)});}catch{await pause(100);continue;}
                if(!response.ok)throw Error('Неизвестный сервер: identity отсутствует');
                const identity=await response.json();assertIdentity(identity,runId,mode);
                fs.writeFileSync(path.join(artifacts,'identity.json'),JSON.stringify(identity,null,2));
                this.verified=true;return;
            }
            throw Error('Собственный сервер не подтвердил готовность');
        },
        track(client){
            const contexts=new Set();clients.set(client,contexts);
            const original=policy.wrap(client.call);
            client.call=async(method,args)=>{const result=await original(method,args);if(method==='Target.createBrowserContext')contexts.add(result.browserContextId);if(method==='Target.disposeBrowserContext')contexts.delete(args.browserContextId);return result;};
            return client;
        },
        async cleanup(browser,server){
            const manifest=state=>{try{fs.writeFileSync(path.join(artifacts,'resources.json'),JSON.stringify({runId,profile:ownedProfile,pids:[...owners].map(child=>child.pid),database:{mode,purpose:'test',path:database?.path||null,retained:database?fs.existsSync(database.path):false},...state},null,2));}catch{console.error('Не удалось записать журнал собственных ресурсов');}};
            manifest({cleanup:'started'});
            for(const [client,contexts]of clients)for(const id of contexts)await client.call('Target.disposeBrowserContext',{browserContextId:id}).catch(()=>{});
            const closes=[...clients.keys()].map(client=>Promise.resolve().then(()=>client.close()));
            let browserStopped=!browser;
            if(browser) { try { browserStopped=await stopOwnedTree(browser,owners); } catch { browserStopped=false; } }
            await Promise.race([Promise.allSettled(closes),pause(3000)]);
            let serverStopped=!server;
            if(server){
                if(this.verified)await fetch(`${this.base}/__testing_shutdown/${runId}`,{method:'POST',signal:AbortSignal.timeout(2000)}).catch(()=>{});
                serverStopped=await waitExit(server,5000);
                if(!serverStopped) { try { serverStopped=await stopOwnedTree(server,owners); } catch { serverStopped=false; } }
            }
            const databaseConfirmed=databaseShutdownConfirmed(mode,server);
            manifest({browserStopped,serverStopped,databaseClosure:!server?'not-started':server.exitCode===0?'graceful':'process-exit-or-unproven',cleanup:browserStopped&&serverStopped&&databaseConfirmed?'confirmed':'unproven'});
            if(ownedProfile)removeOwnedProfile(ownedProfile,ownedProfile,browserStopped&&serverStopped);
            if(!browserStopped||!serverStopped||!databaseConfirmed)throw Error('Остановка процессов/закрытие БД не подтверждены; ресурсы отражены в журнале');
        }
    };
}
module.exports={databaseShutdownConfirmed,BROWSER_DB_MODES,ownedLayout,prepareOwnedRun,BROWSER_SCENARIOS,isPageTarget:fixedVendor.isPageTarget,networkDiagnostic,cleanEnvironment,validateEnvironment,assertIdentity,assertGpu,pythonServer,stopOwnedTree,removeOwnedProfile,createSession};
