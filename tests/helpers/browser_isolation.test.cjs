const {test}=require('node:test'),assert=require('node:assert/strict'),path=require('node:path'),os=require('node:os');
const guard=require('./browser_isolation.cjs');
const {main}=require('./run_isolated_browser.cjs');
const runId='12345678-1234-1234-1234-123456789abc';
const valid=()=>({PYTHON_DOTENV_DISABLED:'1',VTT_SAFE_RUN_ID:runId,VTT_SAFE_SCENARIO:'pripyat',VTT_BROWSER_GPU:'1',VTT_SAFE_DB_MODE:'memory',VTT_SAFE_DB_CONFIRMATION:'--confirm-memory-browser',VTT_PRIPYAT:'1'});
test('Окружение не читает DB/API/авторизацию или произвольные VTT',()=>{
    const source=new Proxy({PATH:'system',DATABASE_URL:'unread',OPENAI_API_KEY:'unread',VTT_NAVIGATION_SOURCE:'unread'}, {
        get(target,key){if(key!=='PATH')throw Error('Чтение запрещённого значения');return target[key];}
    });
    const env=guard.cleanEnvironment(source);
    assert.equal(env.PATH,'system');assert.equal(env.PYTHON_DOTENV_DISABLED,'1');
    assert.equal(env.DATABASE_URL,undefined);assert.equal(env.OPENAI_API_KEY,undefined);assert.equal(env.VTT_NAVIGATION_SOURCE,undefined);
});
test('Допуск только текущей Припяти/GPU; legacy и внешние пути отвергаются',()=>{
    guard.validateEnvironment(valid());
    for(const [key,value]of Object.entries({VTT_NAVIGATION_SOURCE:'x',VTT_NETWORK_NORMAL:'1',VTT_PRIPYAT_LEGACY:'1',VTT_PRIPYAT_BASELINE:'1',VTT_BROWSER_PROFILE:'x',VTT_PRIPYAT_FIXTURE:'x',VTT_UNKNOWN:'1',BROWSER_EXECUTABLE:'x'})){
        assert.throws(()=>guard.validateEnvironment({...valid(),[key]:value}));
    }
    for(const key of ['VTT_SAFE_RUN_ID','VTT_SAFE_SCENARIO','VTT_BROWSER_GPU','PYTHON_DOTENV_DISABLED'])assert.throws(()=>guard.validateEnvironment({...valid(),[key]:'0'}));
    assert.throws(()=>guard.validateEnvironment({...valid(),VTT_SAFE_SCENARIO:'ui'}));
    for(const args of [[],['--confirm-memory-browser','ui'],['--confirm-memory-browser','pripyat','--profile=x'],['pripyat']])assert.throws(()=>main(args));
});
test('HTTP200 недостаточен: обязательна собственная identity',()=>{
    const identity={run_id:runId,purpose:'test',database:'memory',testing:true,threading:true};
    guard.assertIdentity(identity,runId);
    for(const [key,value]of Object.entries({run_id:'foreign',database:'file',testing:false,threading:false}))assert.throws(()=>guard.assertIdentity({...identity,[key]:value},runId));
    assert.throws(()=>guard.assertIdentity({},runId));
});
test('Software/SwiftShader и неизвестный renderer не допускаются',()=>{
    guard.assertGpu('ANGLE (NVIDIA GeForce RTX 3060 Direct3D11)');
    for(const renderer of [null,'','SwiftShader','llvmpipe','software rasterizer','Microsoft Basic Render Driver'])assert.throws(()=>guard.assertGpu(renderer));
});
test('Серверный шаблон не содержит development/файл БД или reloader',()=>{
    const code=guard.pythonServer(12345,runId);
    for(const text of ["create_app('testing')",'memory_database_scope(app, db)',"make_server('127.0.0.1', 12345",'StaticPool','server.server_close()','assert_memory_database(app, db)'])assert.ok(code.includes(text));
    for(const text of ['DevelopmentConfig','sqlite:///','runpy','use_reloader=True'])assert.ok(!code.includes(text));
    assert.throws(()=>guard.pythonServer(0,runId));assert.throws(()=>guard.pythonServer(12345,'foreign'));
});
test('Чужой PID не останавливается, удаление профиля требует владения и остановки',async()=>{
    const child={pid:123,exitCode:null,signalCode:null};let calls=0;
    const io={snapshot:async()=>{calls++;return[];},execute:async()=>{calls++;},waitExit:async()=>true};
    await assert.rejects(guard.stopOwnedTree(child,new Set(),io));assert.equal(calls,0);
    const profile=path.join(os.tmpdir(),'specific-owned-profile');
    assert.throws(()=>guard.removeOwnedProfile(profile,profile,false,()=>{calls++;}));
    assert.throws(()=>guard.removeOwnedProfile(profile,profile+'foreign',true,()=>{calls++;}));
    const outside=path.join(path.parse(os.tmpdir()).root,'outside-profile');
    assert.throws(()=>guard.removeOwnedProfile(outside,outside,true,()=>{calls++;}));
    assert.equal(calls,0);guard.removeOwnedProfile(profile,profile,true,()=>{calls++;});assert.equal(calls,1);
});
test('Остановка собственного дерева ждёт exit и проверяет PID+создание',async()=>{
    const child={pid:123,exitCode:null,signalCode:null};const calls=[];
    const io={snapshot:async(pid)=>{calls.push('snapshot');return[{pid,created:'original'},{pid:456,created:'descendant'}];},
        execute:async(command,args)=>{calls.push(command);if(command==='taskkill.exe')assert.deepEqual(args,['/PID','123','/T','/F']);return{stdout:'[]'};},
        waitExit:async()=>{calls.push('wait');return true;}};
    assert.equal(await guard.stopOwnedTree(child,new Set([child]),io),true);
    assert.deepEqual(calls,['snapshot','taskkill.exe','wait','powershell.exe']);
    child.exitCode=1;calls.length=0;
    assert.equal(await guard.stopOwnedTree(child,new Set([child]),io),false);assert.deepEqual(calls,[]);
});
test('Неподтверждённое завершение дерева не разрешает удаление',async()=>{
    const child={pid:123,exitCode:null,signalCode:null};
    const io={snapshot:async()=>[{pid:123,created:'original'}],execute:async()=>({stdout:'[]'}),waitExit:async()=>false};
    assert.equal(await guard.stopOwnedTree(child,new Set([child]),io),false);
});

test('Сетевая диагностика исключает credentials/query/headers/body',()=>{
    const request=guard.networkDiagnostic('Network.requestWillBeSent',{requestId:'1',request:{
        url:'https://user:password@example.test/library.js?token=secret#hidden',
        headers:{Authorization:'secret'},postData:'secret'}});
    assert.deepEqual(request,{method:'Network.requestWillBeSent',requestId:'1',url:'https://example.test/library.js'});
    const response=guard.networkDiagnostic('Network.responseReceived',{requestId:'1',response:{url:'https://example.test/library.js?key=secret',status:200,headers:{secret:'hidden'}}});
    assert.equal(response.status,200);assert.ok(!JSON.stringify(response).includes('secret'));
    assert.deepEqual(guard.networkDiagnostic('Network.loadingFailed',{requestId:'1',errorText:'net::ERR_CONNECTION_TIMED_OUT'}),
        {method:'Network.loadingFailed',requestId:'1',error:'net::ERR_CONNECTION_TIMED_OUT'});
    assert.equal(guard.networkDiagnostic('Network.webSocketFrameReceived',{response:{payloadData:'secret'}}),null);
});


const vendor=require('./fixed_vendor.cjs'),fs=require('node:fs'),{createHash}=require('node:crypto');
test('Пять фиксированных файлов приняты по regular/realpath/SHA256 и точным URL',()=>{
    const lookup=vendor.loadFixedVendor();assert.equal(vendor.MANIFEST.length,5);
    for(const item of vendor.MANIFEST){
        const body=lookup(item.origin+item.pathname);
        assert.equal(createHash('sha256').update(Buffer.from(body,'base64')).digest('hex').toUpperCase(),item.sha256);
        for(const url of [item.origin+item.pathname+'?x=1',item.origin+item.pathname+'#x',
            item.origin.replace('https:','http:')+item.pathname,item.origin.replace('://','://user:password@')+item.pathname,
            'https://evil.test/'+item.file,item.origin+'/wrong/'+item.file,
            item.origin.replace('://','://sub.')+item.pathname,item.origin+item.pathname.replace('/three@0.128.0/','/three@0.127.0/').replace('/4.0.1/','/4.0.0/')]){
            assert.equal(lookup(url),null,url);
        }
    }
});
test('Отсутствие/подмена bytes/необычный файл/redirect отклоняются без fallback',()=>{
    const target=path.join(vendor.VENDOR_ROOT,vendor.MANIFEST[0].file);
    const cases=[
        {lstatSync(file){if(file===target)throw Error('ENOENT');return fs.lstatSync(file);}},
        {lstatSync(file){return file===target?{isSymbolicLink:()=>true}:fs.lstatSync(file);}},
        {lstatSync(file){return file===target?{isSymbolicLink:()=>false,isFile:()=>false}:fs.lstatSync(file);}},
        {realpathSync(file){return file===target?path.join(os.tmpdir(),'redirect.js'):fs.realpathSync(file);}},
        {realpathSync(file){return file===vendor.VENDOR_ROOT?path.join(os.tmpdir(),'redirect-dir'):fs.realpathSync(file);}},
        {readFileSync(fd){const bytes=Buffer.from(fs.readFileSync(fd));bytes[0]^=1;return bytes;}},
        {fstatSync(fd){const stat=fs.fstatSync(fd);return{isFile:()=>true,dev:stat.dev,ino:stat.ino+1};}},
    ];
    for(const overrides of cases)assert.throws(()=>vendor.loadFixedVendor({...fs,...overrides}));
});
test('После preflight используются неизменяемые байты памяти без повторного чтения',()=>{
    const originals=[];let reads=0;
    const lookup=vendor.loadFixedVendor({...fs,readFileSync(fd){reads++;const bytes=Buffer.from(fs.readFileSync(fd));originals.push(bytes);return bytes;}});
    const before=lookup(vendor.MANIFEST[0].origin+vendor.MANIFEST[0].pathname);
    for(const bytes of originals)bytes.fill(0);
    assert.equal(lookup(vendor.MANIFEST[0].origin+vendor.MANIFEST[0].pathname),before);assert.equal(reads,5);
});
test('CDP выдаёт только exact vendor и отдельный текущий модуль',async()=>{
    const policy=vendor.createInterceptionPolicy(vendor.loadFixedVendor(),{getBase:()=> 'http://127.0.0.1:1234',getCurrentModule:()=>Buffer.from('current module').toString('base64')});
    const calls=[],call=async(method,args)=>calls.push({method,args});
    for(const item of vendor.MANIFEST){await policy.paused({requestId:'vendor',request:{url:item.origin+item.pathname}},call);assert.equal(calls.at(-1).method,'Fetch.fulfillRequest');}
    await policy.paused({requestId:'module',request:{url:'http://127.0.0.1:1234/static/js/locationScene.js'}},call);
    assert.equal(Buffer.from(calls.at(-1).args.body,'base64').toString(),'current module');
    await policy.paused({requestId:'query',request:{url:vendor.MANIFEST[0].origin+vendor.MANIFEST[0].pathname+'?x=1'}},call);
    assert.equal(calls.at(-1).method,'Fetch.failRequest');
    await policy.paused({requestId:'foreign',request:{url:'https://evil.test/static/js/locationScene.js'}},call);
    assert.equal(calls.at(-1).method,'Fetch.continueRequest');assert.equal(calls.at(-1).args.body,undefined);
});
test('Interception включён до новой HTTP-навигации, повторный enable сохраняет vendor',async()=>{
    const policy=vendor.createInterceptionPolicy(()=>null,{getBase:()=> 'http://127.0.0.1:1234',getCurrentModule:()=>null});
    const calls=[];
    const call=policy.wrap(async(method,args)=>{calls.push({method,args});return method==='Target.createTarget'?{targetId:'new-page'}:{};});
    await call('Target.createTarget',{url:'http://127.0.0.1:1234',browserContextId:'own-context'});
    assert.equal(calls[0].args.url,'about:blank');
    const client={call,until:async()=>calls.push({method:'origin ready'})};
    await policy.prepare(client,'ws://127.0.0.1:1/devtools/page/new-page');
    assert.deepEqual(calls.map(item=>item.method),['Target.createTarget','Fetch.enable','Page.navigate','origin ready']);
    await call('Fetch.enable',{patterns:[{urlPattern:'own-pattern',requestStage:'Request'}]});
    for(const pattern of ['https://unpkg.com/*','https://cdnjs.cloudflare.com/*','*/static/js/*','own-pattern'])assert.ok(calls.at(-1).args.patterns.some(p=>p.urlPattern===pattern));
    await assert.rejects(call('Fetch.disable'));
    await assert.rejects(call('Target.createTarget',{url:'https://foreign.test'}));
    const count=calls.length;await policy.prepare(client,'ws://127.0.0.1:1/devtools/browser/root');assert.equal(calls.length,count);
    await policy.prepare(client,'ws://127.0.0.1:1/devtools/page/another-page');assert.equal(calls.at(-1).method,'Fetch.enable');
});


test('Widgets разрешён только фиксированным переключателем без смешивания сценариев',()=>{
    assert.deepEqual(guard.BROWSER_SCENARIOS,{pripyat:'VTT_PRIPYAT',widgets:'VTT_WIDGETS_ONLY',skills:'VTT_SKILLS_ONLY',creation:'VTT_SKILLS_ONLY',weapons:'VTT_SKILLS_ONLY',traits:'VTT_SKILLS_ONLY',movement:'VTT_MOVEMENT_ONLY',magazine:'VTT_MAGAZINE_ONLY','damage-revoke':'VTT_DAMAGE_REVOKE_ONLY','durable-reload':'VTT_DURABLE_RELOAD_ONLY','medical-retry':'VTT_MEDICAL_RETRY_ONLY','health-audit':'VTT_MOVEMENT_ONLY','morning-answers':'VTT_MOVEMENT_ONLY','fire-rate':'VTT_MOVEMENT_ONLY','sheet-repro':'VTT_SHEET_REPRO_ONLY',rotation:'VTT_ROTATION_ONLY',surface:'VTT_MOVEMENT_ONLY',height:'VTT_MOVEMENT_ONLY','gm-relocation':'VTT_GM_RELOCATION_ONLY',terrain:'VTT_MOVEMENT_ONLY',walls:'VTT_MOVEMENT_ONLY',psi:'VTT_MOVEMENT_ONLY',climb:'VTT_MOVEMENT_ONLY',jump:'VTT_MOVEMENT_ONLY',graphics:'VTT_MOVEMENT_ONLY','anomaly-boar':'VTT_MOVEMENT_ONLY',ice:'VTT_MOVEMENT_ONLY',slippery:'VTT_MOVEMENT_ONLY',grenades:'VTT_MOVEMENT_ONLY','world-loading':'VTT_MOVEMENT_ONLY'});
    const widgets={...valid(),VTT_SAFE_SCENARIO:'widgets',VTT_WIDGETS_ONLY:'1'};delete widgets.VTT_PRIPYAT;
    guard.validateEnvironment(widgets);
    for(const extra of [{VTT_PRIPYAT:'1'},{VTT_WIDGETS_FOCUSED:'1'},{VTT_BROWSER_VENDOR_DIR:'x'},{VTT_WIDGETS_ONLY:'0'}])assert.throws(()=>guard.validateEnvironment({...widgets,...extra}));
    assert.throws(()=>guard.validateEnvironment({...widgets,VTT_SAFE_SCENARIO:'widgets-other'}));
});

test('Skills — отдельный owned-file сценарий без смешивания и внешних параметров',()=>{
    const {parseBrowserArgs}=require('./run_isolated_browser.cjs');
    assert.equal(parseBrowserArgs(['--confirm-isolated-browser','skills']).mode,'owned-file');
    assert.throws(()=>parseBrowserArgs(['--confirm-memory-browser','skills']));
    assert.throws(()=>parseBrowserArgs(['--confirm-isolated-browser','skills','--db=foreign']));
    const env={...valid(),VTT_SAFE_SCENARIO:'skills',VTT_SKILLS_ONLY:'1',
        VTT_SAFE_DB_MODE:'owned-file',VTT_SAFE_DB_CONFIRMATION:'--confirm-isolated-browser'};
    delete env.VTT_PRIPYAT;
    guard.validateEnvironment(env);
    assert.throws(()=>guard.validateEnvironment({...env,VTT_WIDGETS_ONLY:'1'}));
});

for(const scenario of ['creation','weapons','traits'])test(`${scenario}: только собственная БД и фиксированный запуск листа`,()=>{
    const {parseBrowserArgs}=require('./run_isolated_browser.cjs');
    assert.equal(parseBrowserArgs(['--confirm-isolated-browser',scenario]).mode,'owned-file');
    assert.throws(()=>parseBrowserArgs(['--confirm-memory-browser',scenario]));
    const env={...valid(),VTT_SAFE_SCENARIO:scenario,VTT_SKILLS_ONLY:'1',VTT_SAFE_DB_MODE:'owned-file',VTT_SAFE_DB_CONFIRMATION:'--confirm-isolated-browser'};
    delete env.VTT_PRIPYAT;
    guard.validateEnvironment(env);
    assert.throws(()=>guard.validateEnvironment({...env,VTT_SAFE_DB_MODE:'memory',VTT_SAFE_DB_CONFIRMATION:'--confirm-memory-browser'}));
    assert.throws(()=>guard.validateEnvironment({...env,VTT_WIDGETS_ONLY:'1'}));
});

for(const scenario of ['walls','psi','climb','jump','graphics','ice','slippery','grenades','world-loading']) test(`${scenario} проверяет фиксированный сценарий в собственной файловой БД`,()=>{
    const {parseBrowserArgs}=require('./run_isolated_browser.cjs');
    assert.equal(parseBrowserArgs(['--confirm-isolated-browser',scenario]).mode,'owned-file');
    assert.throws(()=>parseBrowserArgs(['--confirm-memory-browser',scenario]));
    assert.throws(()=>parseBrowserArgs(['--confirm-isolated-browser',scenario,'--fixture=foreign']));
    const env={...valid(),VTT_SAFE_SCENARIO:scenario,VTT_MOVEMENT_ONLY:'1',
        VTT_SAFE_DB_MODE:'owned-file',VTT_SAFE_DB_CONFIRMATION:'--confirm-isolated-browser'};
    delete env.VTT_PRIPYAT;
    guard.validateEnvironment(env);
    assert.throws(()=>guard.validateEnvironment({...env,VTT_WIDGETS_ONLY:'1'}));
});


test('File и memory подтверждаются разными честными флагами',()=>{
    const {parseBrowserArgs}=require('./run_isolated_browser.cjs');
    assert.equal(parseBrowserArgs(['--confirm-isolated-browser','widgets']).mode,'owned-file');
    assert.equal(parseBrowserArgs(['--confirm-memory-browser','pripyat']).mode,'memory');
    const file={...valid(),VTT_SAFE_DB_MODE:'owned-file',VTT_SAFE_DB_CONFIRMATION:'--confirm-isolated-browser'};
    guard.validateEnvironment(file);
    assert.throws(()=>guard.validateEnvironment({...file,VTT_SAFE_DB_CONFIRMATION:'--confirm-memory-browser'}));
    assert.throws(()=>guard.validateEnvironment({...file,VTT_DB_PATH:'foreign'}));
    const identity={run_id:runId,purpose:'test',database:'owned-file',database_path:guard.ownedLayout(runId).path.split(path.sep).join('/'),testing:true,threading:true};
    guard.assertIdentity(identity,runId,'owned-file');
    for(const change of [{purpose:'production'},{run_id:'foreign'},{database:'memory'},{database_path:'foreign.sqlite'}])assert.throws(()=>guard.assertIdentity({...identity,...change},runId,'owned-file'));
    assert.throws(()=>guard.assertIdentity(identity,runId,'memory'));
});
function fakeRunFs(){
    const directories=new Set(),files=new Map();
    return {directories,files,
        lstatSync(file){return{isDirectory:()=>!files.has(file),isFile:()=>files.has(file),isSymbolicLink:()=>false};},
        realpathSync:file=>file,
        mkdirSync(file){if(directories.has(file)||files.has(file))throw Error('EEXIST');directories.add(file);},
        existsSync:file=>directories.has(file)||files.has(file),
        writeFileSync(file,bytes,options){assert.equal(options.flag,'wx');if(files.has(file))throw Error('EEXIST');files.set(file,bytes);},
        readFileSync:file=>files.get(file)};
}
test('Owned run имеет только фиксированный путь и manifest без реальных файлов',()=>{
    const io=fakeRunFs(),layout=guard.prepareOwnedRun(runId,io);
    assert.ok(layout.path.endsWith(path.join(runId,'db','test.sqlite')));
    assert.deepEqual(JSON.parse(io.files.get(layout.manifest)),{schema_version:1,run_id:runId,purpose:'test',mode:'owned-file',database:'test.sqlite'});
    assert.equal(io.files.has(layout.path),false,'Реальная БД не создаётся preflight');
    assert.throws(()=>guard.prepareOwnedRun(runId,io),'Существующий run запрещён');
    assert.throws(()=>guard.ownedLayout('../foreign'));
});
test('Существующий db-dir/файл/перенаправление/подменённый manifest отклоняются',()=>{
    const layout=guard.ownedLayout(runId);
    for(const type of ['dbdir','database','redirect','symlink','manifest']){
        const io=fakeRunFs();
        if(type==='dbdir')io.directories.add(layout.directory);
        if(type==='database')io.files.set(layout.path,'not a database');
        if(type==='redirect')io.realpathSync=file=>file===layout.runDir?path.dirname(file):file;
        if(type==='symlink')io.lstatSync=file=>({isDirectory:()=>true,isFile:()=>false,isSymbolicLink:()=>file===layout.runDir});
        if(type==='manifest'){const write=io.writeFileSync;io.writeFileSync=(file,bytes,options)=>write(file,JSON.stringify({purpose:'production'}),options);}
        assert.throws(()=>guard.prepareOwnedRun(runId,io),type);
    }
});
test('File-server preflight расположен до app/engine и требует QueuePool',()=>{
    const code=guard.pythonServer(12345,runId,'owned-file');
    assert.ok(code.indexOf('owner = preflight_owned_database')<code.indexOf('from app.config'));
    assert.ok(code.indexOf('TestingConfig.SQLALCHEMY_DATABASE_URI = owner.uri')<code.indexOf("app = create_app('testing')"));
    assert.ok(code.includes("'poolclass': QueuePool"));assert.ok(code.includes('assert_owned_database(app, db, owner)'));
    assert.ok(code.includes("database='owned-file'"));assert.ok(code.includes("purpose='test'"));
    assert.ok(guard.pythonServer(12345,runId,'memory').includes('owner = None'));
});

test('Ненулевое завершение file-сервера не считается корректным закрытием БД',()=>{
    assert.equal(guard.databaseShutdownConfirmed('owned-file',{exitCode:0}),true);
    for(const exitCode of [1,null])assert.equal(guard.databaseShutdownConfirmed('owned-file',{exitCode}),false);
    assert.equal(guard.databaseShutdownConfirmed('memory',{exitCode:1}),true);
});

test('Movement — фиксированный owned-file сценарий без произвольных флагов',()=>{
    const {parseBrowserArgs}=require('./run_isolated_browser.cjs');
    assert.equal(parseBrowserArgs(['--confirm-isolated-browser','movement']).mode,'owned-file');
    assert.throws(()=>parseBrowserArgs(['--confirm-memory-browser','movement']));
    const env={...valid(),VTT_SAFE_SCENARIO:'movement',VTT_MOVEMENT_ONLY:'1',VTT_SAFE_DB_MODE:'owned-file',VTT_SAFE_DB_CONFIRMATION:'--confirm-isolated-browser'};delete env.VTT_PRIPYAT;
    guard.validateEnvironment(env);
    assert.throws(()=>guard.validateEnvironment({...env,VTT_SAFE_DB_MODE:'memory',VTT_SAFE_DB_CONFIRMATION:'--confirm-memory-browser'}));
    assert.throws(()=>guard.validateEnvironment({...env,VTT_NAVIGATION_SOURCE:'x'}));
});


test('Magazine допускается только отдельным fixed owned-file сценарием',()=>{
    const {parseBrowserArgs}=require('./run_isolated_browser.cjs');
    assert.equal(parseBrowserArgs(['--confirm-isolated-browser','magazine']).mode,'owned-file');
    assert.throws(()=>parseBrowserArgs(['--confirm-memory-browser','magazine']));
    const env={...valid(),VTT_SAFE_SCENARIO:'magazine',VTT_MAGAZINE_ONLY:'1',VTT_SAFE_DB_MODE:'owned-file',VTT_SAFE_DB_CONFIRMATION:'--confirm-isolated-browser'};delete env.VTT_PRIPYAT;
    guard.validateEnvironment(env);
    for(const extra of [{VTT_WIDGETS_ONLY:'1'},{VTT_MOVEMENT_ONLY:'1'},
        {VTT_SAFE_DB_MODE:'memory',VTT_SAFE_DB_CONFIRMATION:'--confirm-memory-browser'},
        {VTT_BROWSER_VENDOR_DIR:'x'},{VTT_MAGAZINE_ONLY:'0'}])assert.throws(()=>guard.validateEnvironment({...env,...extra}));
    for(const args of [['--confirm-isolated-browser','magazine','extra'],['--confirm-isolated-browser','magazine-other']])assert.throws(()=>parseBrowserArgs(args));
});


test('Damage-revoke — только fixed owned-file, без смешивания и внешних параметров',()=>{
    const {parseBrowserArgs}=require('./run_isolated_browser.cjs');
    assert.equal(parseBrowserArgs(['--confirm-isolated-browser','damage-revoke']).mode,'owned-file');
    for(const args of [['--confirm-memory-browser','damage-revoke'],['--confirm-isolated-browser','damage-revoke','extra'],
        ['--confirm-isolated-browser','damage-revoke-other'],['--confirm-isolated-browser','damage-revoke','--db=foreign']])assert.throws(()=>parseBrowserArgs(args));
    const env={...valid(),VTT_SAFE_SCENARIO:'damage-revoke',VTT_DAMAGE_REVOKE_ONLY:'1',VTT_SAFE_DB_MODE:'owned-file',VTT_SAFE_DB_CONFIRMATION:'--confirm-isolated-browser'};delete env.VTT_PRIPYAT;
    guard.validateEnvironment(env);
    for(const extra of [{VTT_PRIPYAT:'1'},{VTT_WIDGETS_ONLY:'1'},{VTT_MOVEMENT_ONLY:'1'},{VTT_MAGAZINE_ONLY:'1'},
        {VTT_SAFE_DB_MODE:'memory',VTT_SAFE_DB_CONFIRMATION:'--confirm-memory-browser'},
        {VTT_BROWSER_VENDOR_DIR:'x'},{VTT_DAMAGE_REVOKE_ONLY:'0'},{VTT_DAMAGE_FIXTURE:'x'},{VTT_DB_PATH:'x'}])assert.throws(()=>guard.validateEnvironment({...env,...extra}));
});

test('Damage-revoke читает только фиксированный обычный JSON Припяти; проверка на fake IO',()=>{
    const scenario=require('../damage_revoke.browser.cjs');
    const fixture={name:'Припять — заросший квартал',grid_width:72,grid_height:72,objects:[{id:1}]};
    const io={lstatSync:file=>{assert.equal(file,scenario.FIXTURE);return{isFile:()=>true,isSymbolicLink:()=>false};},
        realpathSync:file=>file,readFileSync:file=>{assert.equal(file,scenario.FIXTURE);return Buffer.from(JSON.stringify(fixture));}};
    assert.deepEqual(scenario.loadFixture(io).fixture,fixture);assert.match(scenario.loadFixture(io).sha256,/^[a-f0-9]{64}$/);
    for(const change of [{lstatSync:()=>({isFile:()=>true,isSymbolicLink:()=>true})},
        {lstatSync:()=>({isFile:()=>false,isSymbolicLink:()=>false})},{realpathSync:()=>'/foreign'},
        {readFileSync:()=>Buffer.from('{}')},{readFileSync:()=>Buffer.from(JSON.stringify({...fixture,objects:[]}))}])assert.throws(()=>scenario.loadFixture({...io,...change}));
});

test('Damage-revoke требует реальный положительный урон, ревизию и точное совпадение сохранённого HP',()=>{
    const {assertDamage,projectHealth,zoneKey}=require('../damage_revoke.browser.cjs');
    const before=projectHealth({_character_id:7,_revision:4,health:{current:700,max:700}}),after={...before,revision:5,hp:690};
    const attack={damage_total:10,results:[{target_character_id:7,hit:true,damage:10}]};
    assertDamage(before,after,attack,7);
    for(const change of [{hp:700},{hp:0},{revision:4},{id:8},{hp:689}])assert.throws(()=>assertDamage(before,{...after,...change},attack,7));
    for(const change of [{damage_total:11},{results:[]},{results:[{target_character_id:8,hit:true,damage:10}]},
        {results:[{target_character_id:7,hit:false,damage:10}]}])assert.throws(()=>assertDamage(before,after,{...attack,...change},7));
    assert.equal(zoneKey({b:{z:1,a:2},a:0}),zoneKey({a:0,b:{a:2,z:1}}));
});

test('Damage-revoke: отсутствие события принимается лишь с живым владельцем, точным барьером и ограниченным окном',()=>{
    const {assertRevoked,WINDOW_MS}=require('../damage_revoke.browser.cjs');
    const after={id:7,revision:6,hp:680},barriers={owner:10,viewer:12},controlKey='matching-zones';
    const own={seq:11,payload:{character_id:7,source:'combat'},snapshot:after};
    const owner={private:[own],public:[{seq:12,characterId:7,zonesKey:controlKey}],disconnects:0,connected:true,sameSocket:true};
    const viewer={private:[],public:[{seq:13,characterId:7,zonesKey:controlKey}],disconnects:0,connected:true,sameSocket:true};
    const live={elapsed:WINDOW_MS+10,samples:18,connected:true,sameSocket:true,disconnects:0,serverTransport:'polling'};
    const windows={owner:live,viewer:{...live,serverTransport:'websocket'}};
    const check=(o=owner,v=viewer,w=windows)=>assertRevoked(o,v,after,barriers,w,controlKey);
    check();
    assert.throws(()=>check({...owner,private:[]}));
    assert.throws(()=>check(owner,{...viewer,private:[{...own,seq:14}]}));
    assert.throws(()=>check({...owner,public:[{seq:11,characterId:7,zonesKey:controlKey}]}));
    for(const role of ['owner','viewer']){
        for(const change of [{elapsed:WINDOW_MS-1},{elapsed:10000},{samples:1},{connected:false},{sameSocket:false},{disconnects:1},{serverTransport:null}])
            assert.throws(()=>check(owner,viewer,{...windows,[role]:{...live,...change}}));
    }
    for(const change of [{public:[]},{public:[{seq:13,characterId:7,zonesKey:'old-zones'}]},
        {public:[{seq:12,characterId:7,zonesKey:controlKey}]},{connected:false},{sameSocket:false},{disconnects:1}])assert.throws(()=>check(owner,{...viewer,...change}));
    assert.throws(()=>check({...owner,private:[{...own,snapshot:{...after,hp:690}}]}));
});


test('Damage-revoke: сборщик сохраняет исходные поля события и живой порядок без секретов',()=>{
    const {collector,projectHealth,zoneKey}=require('../damage_revoke.browser.cjs'),{runInNewContext}=require('node:vm');
    const handlers={},documentHandlers={},context={window:{},socket:{id:'synthetic-session',connected:true,
        io:{engine:{transport:{name:'polling'}}},on:(name,handler)=>handlers[name]=handler},
        document:{addEventListener:(name,handler)=>documentHandlers[name]=handler}};
    runInNewContext(`(${collector.toString()})(7,${projectHealth.toString()},${zoneKey.toString()})`,context);
    documentHandlers['character-sheet-opened']();
    const wire={character_id:7,source:'combat',updated_by:2,updates:{data:{_character_id:7,_revision:6,
        health:{current:680,max:700,privateField:'excluded'},password:'excluded',inventory:{}}},token:'excluded'};
    handlers.character_data_updated(wire);
    handlers.combat_character_updated({character_id:7,hp_zones:{chest:{current:130,max:150}}});
    handlers.character_data_updated({...wire,character_id:8});
    const e=JSON.parse(JSON.stringify(context.window.damageEvidence));
    assert.equal(e.opens,1);assert.equal(e.private.length,1);assert.equal(e.private[0].transport,'polling');
    assert.deepEqual(e.private[0].payload,{character_id:7,source:'combat',updated_by:2,
        updates:{data:{_character_id:7,_revision:6,health:{current:680,max:700}}}});
    assert.deepEqual(e.private[0].snapshot,{id:7,revision:6,hp:680,max:700});
    assert.ok(e.public[0].seq>e.private[0].seq);assert.equal(e.public[0].zonesKey,zoneKey({chest:{current:130,max:150}}));
    assert.ok(!JSON.stringify(e).includes('excluded'));handlers.disconnect();assert.equal(context.window.damageEvidence.disconnects,1);
});


test('Durable-reload — только точный owned-file gate без смешивания и произвольного источника',()=>{
    const {parseBrowserArgs}=require('./run_isolated_browser.cjs');
    assert.equal(parseBrowserArgs(['--confirm-isolated-browser','durable-reload']).scenario,'durable-reload');
    for(const args of [['--confirm-memory-browser','durable-reload'],['--confirm-isolated-browser','durable-reload','extra'],
        ['--confirm-isolated-browser','durable-reload-other'],['--confirm-isolated-browser','durable-reload','--sql=x']])assert.throws(()=>parseBrowserArgs(args));
    const env={...valid(),VTT_SAFE_SCENARIO:'durable-reload',VTT_DURABLE_RELOAD_ONLY:'1',VTT_SAFE_DB_MODE:'owned-file',VTT_SAFE_DB_CONFIRMATION:'--confirm-isolated-browser'};delete env.VTT_PRIPYAT;
    guard.validateEnvironment(env);
    for(const flag of ['VTT_PRIPYAT','VTT_WIDGETS_ONLY','VTT_MOVEMENT_ONLY','VTT_MAGAZINE_ONLY','VTT_DAMAGE_REVOKE_ONLY'])assert.throws(()=>guard.validateEnvironment({...env,[flag]:'1'}));
    for(const extra of [{VTT_SAFE_DB_MODE:'memory',VTT_SAFE_DB_CONFIRMATION:'--confirm-memory-browser'},{VTT_DURABLE_RELOAD_ONLY:'0'},
        {VTT_DURABLE_FIXTURE:'x'},{VTT_DB_PATH:'x'},{VTT_BROWSER_VENDOR_DIR:'x'}])assert.throws(()=>guard.validateEnvironment({...env,...extra}));
});

test('Durable-reload: fixture фиксирован, импорт helper не запускает приложение; fake IO',()=>{
    const scenario=require('../durable_reload.browser.cjs');
    assert.deepEqual(scenario.CASES,['narrative','attack','clear-jam','equip','general']);
    const fixture={name:'Припять — заросший квартал',grid_width:72,grid_height:72,objects:[{}]};
    const io={lstatSync:file=>{assert.equal(file,scenario.FIXTURE);return{isFile:()=>true,isSymbolicLink:()=>false};},
        realpathSync:file=>file,readFileSync:()=>Buffer.from(JSON.stringify(fixture))};
    assert.deepEqual(scenario.loadFixture(io).fixture,fixture);assert.match(scenario.loadFixture(io).sha256,/^[a-f0-9]{64}$/);
    for(const change of [{realpathSync:()=>'/foreign'},{lstatSync:()=>({isFile:()=>true,isSymbolicLink:()=>true})},
        {readFileSync:()=>Buffer.from(JSON.stringify({...fixture,grid_width:1}))},{readFileSync:()=>Buffer.from('{}')}])assert.throws(()=>scenario.loadFixture({...io,...change}));
});

test('Durable-reload: GET или старый/скрытый документ не считается восстановленным видимым HUD',()=>{
    const {assertReloadRecovery}=require('../durable_reload.browser.cjs'),expected={id:'saved',label:'Ремонт двери',remaining_action_points:12};
    const before={epoch:100,url:'/page?location=1',id:'saved',text:'Исполнитель: Ремонт двери\nОсталось оплатить: 12 ОД',visible:true,connected:true};
    const after={...before,epoch:200};assertReloadRecovery(before,after,expected,'Исполнитель');
    for(const change of [{epoch:100},{epoch:99},{epoch:null},{url:'/other'},{id:'other'},{visible:false},{connected:false},
        {text:'Исполнитель: Ремонт двери\nОсталось оплатить: 7 ОД'},{text:'Чужой: Ремонт двери\nОсталось оплатить: 12 ОД'}])
        assert.throws(()=>assertReloadRecovery(before,{...after,...change},expected,'Исполнитель'));
    assert.throws(()=>assertReloadRecovery({...before,visible:false},after,expected,'Исполнитель'));
});

test('Durable-reload: оплата сохраняет ID/имя/ключ и уменьшает долг только на штатный запас ОД',()=>{
    const {assertPayment,assertSummary}=require('../durable_reload.browser.cjs');
    const before={id:'saved',action_key:'clear_weapon_jam',label:'Устранить клин',status:'paying',remaining_action_points:7};
    const next={...before,remaining_action_points:2};assertPayment(before,next,5);assertSummary(next,next);
    assertPayment(next,{...next,status:'ready',remaining_action_points:0},5);
    for(const change of [{id:'other'},{label:'Подмена'},{action_key:'attack'},{remaining_action_points:1},{status:'ready'}])assert.throws(()=>assertPayment(before,{...next,...change},5));
    assert.throws(()=>assertSummary({...next,status:'ready'}, {...next,status:'ready'}));
});

test('Durable-reload: эффект связывается с исходным именем, оружием, целью и предметом, не с подменой',()=>{
    const {assertEffect,rifle}=require('../durable_reload.browser.cjs');
    const ids={narrativeName:'Ремонт двери',targetId:7,total:6,itemId:'chosen',decoyItemId:'decoy',itemName:'Расходник'};
    const narrative={action:'narrative_action',narrative_action:{name:ids.narrativeName,action_points:6,roll_required:false}};
    assertEffect('narrative',{}, {},narrative,ids);
    assert.throws(()=>assertEffect('narrative',{}, {},{...narrative,narrative_action:{...narrative.narrative_action,name:'Подмена'}},ids));
    const guns={weapons:[rifle('original'),rifle('other')]},used=structuredClone(guns);used.weapons[0].ammo--;
    const attack={action:'attack',attack:{weapon_index:0,target_character_id:7,fire_mode:'aimed',target_zone:'chest',shot_count:1,action_points:6,results:[{target_character_id:7}]}};
    assertEffect('attack',guns,used,attack,ids);
    assert.throws(()=>assertEffect('attack',guns,guns,attack,ids));
    assert.throws(()=>assertEffect('attack',guns,used,{...attack,attack:{...attack.attack,weapon_index:1}},ids));
    assert.throws(()=>assertEffect('attack',guns,used,{...attack,attack:{...attack.attack,target_character_id:8}},ids));
    const jammed={weapons:[rifle('original',true),rifle('other',true)]},cleared=structuredClone(jammed);delete cleared.weapons[0].jam;delete cleared.weapons[0].jams;
    const jam={action:'clear_weapon_jam',attack:null,clear_weapon_jam:{weapon_index:0,result:10,action_points:6}};
    assertEffect('clear-jam',jammed,cleared,jam,ids);assert.throws(()=>assertEffect('clear-jam',jammed,jammed,jam,ids));
    const helmet={equipment:{helmet:{id:'chosen',durability:27,customNote:'Сохранить метку'}},inventory:{backpack:[{id:'decoy'}]}};
    const equip={action:'change_equipment',equipment_change:{operation:'equip',slot:'helmet',action_points:6}};
    assertEffect('equip',{},helmet,equip,ids);assert.throws(()=>assertEffect('equip',{}, {...helmet,equipment:{}},equip,ids));
    const before={radiation:0,inventory:{backpack:[{id:'chosen',quantity:2},{id:'decoy'}]}},after={radiation:1,inventory:{backpack:[{id:'chosen',quantity:1,customNote:'Сохранить метку'},{id:'decoy'}]}};
    const general={consumable_result:{item_name:'Расходник',payment_details:{total_action_points:6}}};
    assertEffect('general',before,after,general,ids);assert.throws(()=>assertEffect('general',before,{...after,radiation:2},general,ids));
});
