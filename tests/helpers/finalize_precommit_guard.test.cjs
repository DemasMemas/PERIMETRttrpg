// Только app-free: pure plans/fake IO; main/child/SQL не вызываются.
const {test}=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const launcher=require('./run_isolated_finalize.cjs');
const {prepareOwnedRun}=require('./browser_isolation.cjs');
const runId='12345678-1234-1234-1234-123456789abc';
test('Finalize CLI — ровно фиксированный флаг, без путей/env/SQL/selectors',()=>{
    launcher.parseArgs(['--confirm-owned-finalize']);
    for(const args of [[],['--confirm-memory-tests'],['--confirm-owned-finalize','extra'],['--confirm-owned-finalize','--db=x'],
        ['--confirm-owned-finalize','--sql=SELECT'],['--confirm-owned-finalize','test_file.py']])assert.throws(()=>launcher.parseArgs(args));
});
test('Finalize план не читает секреты, не наследует DB/env и фиксирует child -I -B',()=>{
    const source=new Proxy({PATH:'system',DATABASE_URL:'unread',OPENAI_API_KEY:'unread',VTT_ANY:'unread',PYTHONPATH:'unread'},
        {get(target,key){if(key!=='PATH')throw Error('Запрещённое чтение');return target[key];}});
    const plan=launcher.plan(['--confirm-owned-finalize'],source,runId);
    assert.deepEqual(plan.args,['-I','-B',path.join(__dirname,'finalize_precommit_probe.py'),runId]);
    assert.equal(plan.windowsHide,true);assert.ok(plan.command.endsWith(path.join('.venv','Scripts','python.exe')));
    assert.equal(plan.env.PATH,'system');assert.equal(plan.env.PYTHON_DOTENV_DISABLED,'1');
    assert.equal(plan.env.VTT_FINALIZE_RUN_ID,runId);
    for(const key of ['DATABASE_URL','OPENAI_API_KEY','PYTHONPATH','VTT_ANY'])assert.equal(plan.env[key],undefined);
    assert.throws(()=>launcher.plan(['--confirm-owned-finalize'],{},'../foreign'));
});
test('Finalize не считает чужой/переиспользованный/завершённый PID своим',()=>{
    const child={pid:123,exitCode:null,signalCode:null},identity={pid:123,created:'987654',matches:true};
    launcher.assertOwnership(child,identity,identity);
    for(const value of [{...identity,pid:124},{...identity,created:''},{...identity,matches:false}])assert.throws(()=>launcher.assertOwnership(child,value,identity));
    assert.throws(()=>launcher.assertOwnership(child,{...identity,created:'987655'},identity));
    assert.throws(()=>launcher.assertOwnership({...child,exitCode:0},identity));
    assert.throws(()=>launcher.assertOwnership({...child,signalCode:'SIGTERM'},identity));
});
test('Finalize переиспользует fresh owned manifest с fake IO без создания реальной БД',()=>{
    const directories=new Set(),files=new Map();
    const io={lstatSync:file=>({isDirectory:()=>!files.has(file),isFile:()=>files.has(file),isSymbolicLink:()=>false}),
        realpathSync:file=>file,mkdirSync:file=>{if(directories.has(file))throw Error('EEXIST');directories.add(file);},
        existsSync:file=>files.has(file),writeFileSync(file,bytes,options){assert.equal(options.flag,'wx');if(files.has(file))throw Error('EEXIST');files.set(file,bytes);},readFileSync:file=>files.get(file)};
    const owner=prepareOwnedRun(runId,io);
    assert.deepEqual(JSON.parse(files.get(owner.manifest)),{schema_version:1,run_id:runId,purpose:'test',mode:'owned-file',database:'test.sqlite'});
    assert.equal(files.has(owner.path),false);assert.throws(()=>prepareOwnedRun(runId,io));
});
test('Finalize общий предел parent имеет ограниченный резерв для остановки child',()=>{
    assert.equal(launcher.RUN_MS,65000);assert.equal(launcher.TOTAL_MS,75000);
    assert.equal(launcher.TOTAL_MS-launcher.RUN_MS,10000);
});

test('Finalize executable/probe не допускают ссылок/перенаправления/каталога',()=>{
    const io={lstatSync:()=>({isFile:()=>true,isSymbolicLink:()=>false}),realpathSync:file=>file};
    launcher.assertFixedFile('fixed',io);
    for(const change of [{realpathSync:()=>'/foreign'},{lstatSync:()=>({isFile:()=>false,isSymbolicLink:()=>false})},
        {lstatSync:()=>({isFile:()=>true,isSymbolicLink:()=>true})}])assert.throws(()=>launcher.assertFixedFile('fixed',{...io,...change}));
});
test('Finalize проверка layout/manifest не принимает подменённый purpose или путь',()=>{
    const layout=launcher.plan(['--confirm-owned-finalize'],{},runId).layout;
    const expected={schema_version:1,run_id:runId,purpose:'test',mode:'owned-file',database:'test.sqlite'};
    const io={lstatSync:file=>({isDirectory:()=>file!==layout.manifest,isFile:()=>file===layout.manifest,isSymbolicLink:()=>false}),
        realpathSync:file=>file,readFileSync:()=>JSON.stringify(expected)};
    launcher.assertLayout(layout,io);
    assert.throws(()=>launcher.assertLayout({...layout,path:'/foreign'},io));
    assert.throws(()=>launcher.assertLayout(layout,{...io,readFileSync:()=>JSON.stringify({...expected,purpose:'production'})}));
    assert.throws(()=>launcher.assertLayout(layout,{...io,realpathSync:file=>file===layout.directory?'/foreign':file}));
});

test('Finalize не принимает timeout/неполную очистку/неверную проверку за успех',()=>{
    const result={run_id:runId,status:'READY_FOR_REVIEW',cleanup:{status:'confirmed',workers_joined:true,sessions_removed:true,engine_disposed:true,checkedout:0},
        workers:{alive:false,connections:{W:{session_removed:true},C:{session_removed:true}}},
        verification:{winner:200,contender:409,receipt_count:1,fresh_get_matches:true}};
    assert.equal(launcher.acceptResult({code:0},result,runId),true);
    for(const exit of [{code:1},{code:0,watchdog:true},{code:0,signal:'SIGTERM'}])assert.equal(launcher.acceptResult(exit,result,runId),false);
    for(const change of [{run_id:'other'},{status:'BLOCKED'},{workers:{alive:true}}])assert.equal(launcher.acceptResult({code:0},{...result,...change},runId),false);
    for(const change of [{status:'unproven'},{workers_joined:false},{sessions_removed:false},{engine_disposed:false},{checkedout:1}])
        assert.equal(launcher.acceptResult({code:0},{...result,cleanup:{...result.cleanup,...change}},runId),false);
    for(const change of [{winner:500},{contender:500},{receipt_count:2},{fresh_get_matches:false}])
        assert.equal(launcher.acceptResult({code:0},{...result,verification:{...result.verification,...change}},runId),false);
});
