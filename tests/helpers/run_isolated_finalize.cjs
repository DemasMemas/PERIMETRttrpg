// Только один фиксированный child; импорт не создаёт файлы/процессы.
const fs=require('node:fs'),path=require('node:path'),{randomUUID}=require('node:crypto');
const {spawn,execFile}=require('node:child_process'),{promisify}=require('node:util');
const execute=promisify(execFile);
const {cleanEnvironment,prepareOwnedRun,ownedLayout,stopOwnedTree}=require('./browser_isolation.cjs');
const ROOT=path.resolve(__dirname,'../..'),FLAG='--confirm-owned-finalize';
const RUN_MS=65000,TOTAL_MS=75000;
function parseArgs(args){if(args.length!==1||args[0]!==FLAG)throw Error('Нужен только '+FLAG);}
function plan(args,source,runId){
    parseArgs(args);const layout=ownedLayout(runId);
    return{layout,cwd:ROOT,command:path.join(ROOT,'.venv/Scripts/python.exe'),
        args:['-I','-B',path.join(__dirname,'finalize_precommit_probe.py'),runId],
        env:{...cleanEnvironment(source),VTT_FINALIZE_RUN_ID:runId},windowsHide:true};
}
function assertOwnership(child,identity,expected){
    if(!child||!Number.isInteger(child.pid)||child.pid<=0||child.exitCode!==null||child.signalCode!==null
        ||!identity||identity.pid!==child.pid||!/^\d+$/.test(identity.created||'')||identity.matches!==true
        ||(expected&&(identity.pid!==expected.pid||identity.created!==expected.created)))throw Error('Владение child не доказано');
}
function assertFixedFile(file,io=fs){
    const info=io.lstatSync(file);
    if(!info.isFile()||info.isSymbolicLink()||io.realpathSync(file)!==file)throw Error('Перенаправленный executable/probe');
}
function assertLayout(layout,io=fs){
    const expected=ownedLayout(layout.runId);
    if(JSON.stringify(layout)!==JSON.stringify(expected))throw Error('Чужой layout');
    for(const dir of [ROOT,path.join(ROOT,'.private-testing'),path.join(ROOT,'.private-testing/reports'),layout.runDir,layout.directory]){
        const info=io.lstatSync(dir);if(!info.isDirectory()||info.isSymbolicLink()||io.realpathSync(dir)!==dir)throw Error('Layout перенаправлен');
    }
    assertFixedFile(layout.manifest,io);
    const value=JSON.parse(io.readFileSync(layout.manifest,'utf8'));
    if(JSON.stringify(value)!==JSON.stringify({schema_version:1,run_id:layout.runId,purpose:'test',mode:'owned-file',database:'test.sqlite'}))throw Error('Manifest подменён');
}
function acceptResult(exit,result,runId){
    const cleanup=result?.cleanup,workers=result?.workers,verified=result?.verification;
    return Boolean(exit?.code===0&&!exit.signal&&!exit.watchdog&&result?.run_id===runId&&result.status==='READY_FOR_REVIEW'
        &&cleanup?.status==='confirmed'&&cleanup.workers_joined===true&&cleanup.sessions_removed===true
        &&cleanup.engine_disposed===true&&cleanup.checkedout===0&&workers?.alive===false
        &&workers.connections?.W?.session_removed===true&&workers.connections?.C?.session_removed===true
        &&verified?.winner===200&&verified.contender===409&&verified.receipt_count===1&&verified.fresh_get_matches===true);
}
const quote=s=>"'"+s.replaceAll("'","''")+"'";
async function main(args=process.argv.slice(2),source=process.env){
    const config=plan(args,source,randomUUID());
    assertFixedFile(config.command);assertFixedFile(config.args[2]);
    const layout=prepareOwnedRun(config.layout.runId);
    const state={task:'FINALIZE-PRECOMMIT',run_id:layout.runId,status:'STARTING',ownership:null,cleanup:'not-started'};
    const save=()=>{assertLayout(layout);const output=path.join(layout.runDir,'parent-finalize.json');
        if(fs.existsSync(output))assertFixedFile(output);fs.writeFileSync(output,JSON.stringify(state,null,2));};save();
    const started=performance.now(),deadline=started+TOTAL_MS;
    let child,identity,exited=false,exitInfo,watchdogTimer,stopOwned;
    const exec=async(file,argv,options={})=>{
        const budget=Math.floor(deadline-performance.now());if(budget<=0)throw Error('Общий deadline parent исчерпан');
        return execute(file,argv,{...options,env:config.env,windowsHide:true,timeout:Math.max(1,Math.min(15000,budget)),maxBuffer:1024*1024});
    };
    const getIdentity=async()=>{
        const script=`$targetProcess=Get-CimInstance Win32_Process -Filter "ProcessId=${child.pid}";if($targetProcess){@{pid=[int]$targetProcess.ProcessId;created=$targetProcess.CreationDate.ToUniversalTime().Ticks.ToString();matches=($targetProcess.CommandLine.IndexOf(${quote(config.args[2])},[StringComparison]::OrdinalIgnoreCase)-ge 0 -and $targetProcess.CommandLine.Contains(${quote(layout.runId)}))}|ConvertTo-Json -Compress}`;
        const result=await exec('powershell.exe',['-NoProfile','-NonInteractive','-Command',script]);
        return result.stdout.trim()?JSON.parse(result.stdout):null;
    };
    try{
        child=spawn(config.command,config.args,{cwd:config.cwd,env:config.env,windowsHide:true,stdio:['ignore','pipe','pipe']});
        const owners=new Set([child]);
        const exitPromise=new Promise(resolve=>{
            child.once('error',error=>{exited=true;exitInfo={code:null,error:error.code||error.name};resolve(exitInfo);});
            child.once('exit',(code,signal)=>{exited=true;exitInfo={code,signal};resolve(exitInfo);});
        });
        // Child не пишет credentials/SQL; сырой stdout/stderr не переносится в журналы parent.
        child.stdout.resume();child.stderr.resume();
        identity=await getIdentity();assertOwnership(child,identity);
        state.ownership=identity;save();
        stopOwned=async()=>{
            const current=await getIdentity();assertOwnership(child,current,identity);
            const snapshot=async pid=>{
                const script=`$todo=@(${pid});$out=@();while($todo.Count){$targetId=$todo[0];$todo=@($todo|Select-Object -Skip 1);$targetProcess=Get-CimInstance Win32_Process -Filter "ProcessId=$targetId";if($targetProcess){$out+=@{pid=[int]$targetProcess.ProcessId;created=$targetProcess.CreationDate.ToUniversalTime().Ticks.ToString()};$todo+=@(Get-CimInstance Win32_Process -Filter "ParentProcessId=$targetId"|ForEach-Object{[int]$_.ProcessId})}};ConvertTo-Json -InputObject @($out) -Compress`;
                const result=await exec('powershell.exe',['-NoProfile','-NonInteractive','-Command',script]);
                const processes=JSON.parse(result.stdout||'[]');
                if(!processes.some(p=>p.pid===identity.pid&&p.created===identity.created))throw Error('Исходный child отсутствует/подменён');
                return processes;
            };
            const waitExit=async()=>{
                let timer;try{return await Promise.race([exitPromise.then(()=>true),new Promise(resolve=>{
                    timer=setTimeout(()=>resolve(false),Math.max(1,deadline-performance.now()));})]);}finally{clearTimeout(timer);}
            };
            return stopOwnedTree(child,owners,{snapshot,execute:exec,waitExit});
        };
        const permit={schema_version:1,run_id:layout.runId,purpose:'test',child_pid:child.pid,created:identity.created};
        const temporary=path.join(layout.runDir,'finalize-start.tmp'),destination=path.join(layout.runDir,'finalize-start.json');
        fs.writeFileSync(temporary,JSON.stringify(permit),{encoding:'utf8',flag:'wx'});
        if(fs.existsSync(destination))throw Error('Permit уже существует');
        fs.renameSync(temporary,destination);state.status='RUNNING';save();
        const timeoutPromise=new Promise(resolve=>{watchdogTimer=setTimeout(()=>resolve({watchdog:true}),Math.max(1,RUN_MS-(performance.now()-started)));});
        const end=await Promise.race([exitPromise,timeoutPromise]);clearTimeout(watchdogTimer);
        if(end.watchdog){
            state.status='BLOCKED_TIMEOUT';state.cleanup='unproven';
            try{state.owned_tree_stopped=await stopOwned();}catch(error){state.stop_error=error.message;}
            save();return 1;
        }
        state.exit=end;assertLayout(layout);
        const output=path.join(layout.runDir,'finalize-precommit.json');
        if(!fs.existsSync(output)||!fs.lstatSync(output).isFile()||fs.lstatSync(output).isSymbolicLink()||fs.realpathSync(output)!==output)throw Error('Нет обычного отчёта child');
        const result=JSON.parse(fs.readFileSync(output,'utf8'));
        state.cleanup=result.cleanup?.status||'unproven';
        state.status=acceptResult(end,result,layout.runId)?'READY_FOR_REVIEW':'BLOCKED';
        save();console.log('Результат '+layout.runId+': '+state.status);return state.status==='READY_FOR_REVIEW'?0:1;
    }catch(error){
        state.status='BLOCKED';state.cleanup='unproven';state.failure=String(error.message).replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g,'[токен]');save();
        // До permit child сам заканчивает guard не позднее10с. Без доказанного владения не останавливаем.
        if(child&&!exited&&identity&&stopOwned){
            try{state.owned_tree_stopped=await stopOwned();}catch(stopError){state.stop_error=stopError.message;}
            save();
        }else if(child&&!exited){
            const budget=Math.max(1,Math.min(11000,deadline-performance.now()));
            await new Promise(resolve=>{const timer=setTimeout(resolve,budget);child.once('exit',()=>{clearTimeout(timer);resolve();});});
        }
        return 1;
    }finally{
        clearTimeout(watchdogTimer);
        if(child&&!exited){child.stdout?.destroy();child.stderr?.destroy();child.unref();}
    }
}
if(require.main===module)main().then(code=>{process.exitCode=code;}).catch(()=>{console.error('Отказ до запуска child');process.exitCode=1;});
module.exports={parseArgs,plan,assertOwnership,assertFixedFile,assertLayout,acceptResult,FLAG,RUN_MS,TOTAL_MS};
