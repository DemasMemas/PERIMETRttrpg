// Локальный экран ручной приёмки. Запускает только разрешённые изолированные сценарии.
const http=require('node:http'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawn}=require('node:child_process');
const {CASES,root,codeVersion}=require('./playtest_registry.cjs');
const {cleanEnvironment}=require('./browser_isolation.cjs');
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
function safeFile(base,...parts){
    const candidate=path.resolve(base,...parts),prefix=path.resolve(base)+path.sep;
    if(!candidate.startsWith(prefix))throw Error('Недопустимый путь');
    const relative=path.relative(base,candidate).split(path.sep);let cursor=path.resolve(base);
    if(fs.lstatSync(cursor).isSymbolicLink())throw Error('Каталог перенаправлен');
    for(const part of relative){cursor=path.join(cursor,part);if(fs.lstatSync(cursor).isSymbolicLink())throw Error('Файл перенаправлен');}
    if(!fs.statSync(candidate).isFile())throw Error('Это не файл');
    return candidate;
}
function readJSON(file){return JSON.parse(fs.readFileSync(file,'utf8'));}
function atomicJSON(file,value){
    const temporary=file+'.'+crypto.randomUUID()+'.tmp';
    fs.writeFileSync(temporary,JSON.stringify(value,null,2)+'\n',{encoding:'utf8',flag:'wx'});
    fs.renameSync(temporary,file);
}
function createReview({reports=path.join(root,'.private-testing/reports'),stateDir=path.join(root,'.private-testing/playtest-review'),
        fingerprint=codeVersion,launch=launchProfile}={}){
    fs.mkdirSync(stateDir,{recursive:true});
    if(fs.lstatSync(stateDir).isSymbolicLink())throw Error('Каталог решений перенаправлен');
    const decisionsFile=path.join(stateDir,'decisions.json'),secret=crypto.randomBytes(32).toString('hex');
    const decisions=()=>fs.existsSync(decisionsFile)?readJSON(safeFile(stateDir,'decisions.json')):[];
    let job=null,closing=false;
    function scan(){
        const version=fingerprint(),votes=decisions(),runs=[];
        for(const entry of fs.readdirSync(reports,{withFileTypes:true})){
            if(!entry.isDirectory()||entry.isSymbolicLink()||!UUID.test(entry.name))continue;
            for(const [caseId,definition]of Object.entries(CASES)){
                try{
                    const record=readJSON(safeFile(reports,entry.name,'review-cases',caseId+'.json'));
                    if(record.schema!==1||record.caseId!==caseId||record.profile!==definition.profile)continue;
                    let cleaned=false;
                    try{const resources=readJSON(safeFile(reports,entry.name,'resources.json'));
                        cleaned=resources.runId===entry.name&&resources.browserStopped===true&&resources.serverStopped===true
                            &&resources.databaseClosure==='graceful'&&resources.cleanup==='confirmed';}catch{}
                    const current=record.codeVersion===version&&record.caseVersion===definition.version;
                    const decision=votes.filter(v=>v.runId===entry.name&&v.caseId===caseId&&v.codeVersion===record.codeVersion
                        &&v.caseVersion===record.caseVersion).at(-1)||null;
                    runs.push({...record,runId:entry.name,definition,current,cleaned,decision});
                }catch{}
            }
        }
        runs.sort((a,b)=>String(b.recordedAt).localeCompare(String(a.recordedAt)));
        return {cases:Object.entries(CASES).map(([id,definition])=>({id,...definition,run:runs.find(r=>r.caseId===id)||null})),
            job:job?{profile:job.profile,status:job.status,runId:job.runId,error:job.error,startedAt:job.startedAt}:null};
    }
    const server=http.createServer(async(req,res)=>{
        const json=(status,value)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store',
            'X-Content-Type-Options':'nosniff'});res.end(JSON.stringify(value));};
        try{
            const host=`127.0.0.1:${server.address().port}`;
            if(req.headers.host!==host)return json(403,{error:'Разрешён только локальный адрес этого окна'});
            const origin='http://'+host,url=new URL(req.url,origin);
            if(req.method==='GET'&&url.pathname==='/'){
                res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store',
                    'Content-Security-Policy':"default-src 'self'; img-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"});
                return res.end(fs.readFileSync(path.join(__dirname,'playtest_review.html')));
            }
            if(req.method==='GET'&&['/review.js','/review.css'].includes(url.pathname)){
                res.writeHead(200,{'Content-Type':url.pathname.endsWith('.js')?'application/javascript; charset=utf-8':'text/css; charset=utf-8'});
                return res.end(fs.readFileSync(path.join(__dirname,'playtest_review'+(url.pathname.endsWith('.js')?'.js':'.css'))));
            }
            if(req.method==='GET'&&url.pathname==='/api/state')return json(200,{service:'perimetr-playtest-review',...scan(),token:secret});
            if(req.method==='GET'&&url.pathname.startsWith('/image/')){
                const parts=url.pathname.split('/').slice(2);
                if(parts.length!==2||!UUID.test(parts[0])||!/^[-\w]+\.png$/.test(parts[1]))return json(404,{error:'Снимок не найден'});
                const permitted=scan().cases.some(c=>c.run?.runId===parts[0]&&c.run.images.includes(parts[1]));
                if(!permitted)return json(404,{error:'Снимок не принадлежит доступной проверке'});
                const file=safeFile(reports,...parts);res.writeHead(200,{'Content-Type':'image/png','X-Content-Type-Options':'nosniff'});
                return fs.createReadStream(file).pipe(res);
            }
            if(req.method!=='POST')return json(404,{error:'Не найдено'});
            if(req.headers.origin!==origin||req.headers['x-review-token']!==secret||req.headers['content-type']!=='application/json')
                return json(403,{error:'Отправьте решение из открытого окна плейтеста'});
            let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>16000)return json(413,{error:'Слишком длинное замечание'});}
            const body=JSON.parse(raw);
            if(!body||typeof body!=='object'||Array.isArray(body))return json(400,{error:'Некорректный запрос'});
            const definition=Object.hasOwn(CASES,body.caseId)?CASES[body.caseId]:null;
            if(url.pathname==='/api/shutdown'){
                if(job?.status==='running')return json(409,{error:'Дождитесь завершения собственного стенда'});
                closing=true;json(200,{closed:true});server.close();return;
            }
            if(url.pathname==='/api/decision'){
                const run=scan().cases.find(c=>c.id===body.caseId)?.run;
                if(!definition||!run||run.runId!==body.runId||!run.current||!run.cleaned||run.automaticResult!=='passed')
                    return json(409,{error:'Результат устарел или стенд ещё не завершён. Повторите проверку.'});
                if(body.codeVersion!==run.codeVersion||body.caseVersion!==run.caseVersion)return json(409,{error:'Открыта другая версия проверки'});
                if(!['accepted','rework'].includes(body.status)||typeof body.comment!=='string'||body.comment.length>4000)
                    return json(400,{error:'Некорректное решение'});
                if(body.status==='rework'&&!body.comment.trim())return json(400,{error:'Напишите, что нужно переделать'});
                const all=decisions(),last=all.at(-1);
                const value={id:crypto.randomUUID(),caseId:body.caseId,runId:run.runId,codeVersion:run.codeVersion,
                    caseVersion:run.caseVersion,status:body.status,comment:body.comment.trim(),decidedAt:new Date().toISOString()};
                if(last&&['caseId','runId','codeVersion','caseVersion','status','comment'].every(key=>last[key]===value[key]))return json(200,{decision:last});
                all.push(value);atomicJSON(decisionsFile,all);return json(200,{decision:value});
            }
            if(url.pathname==='/api/repeat'){
                if(!definition)return json(400,{error:'Неизвестная проверка'});
                if(closing||job?.status==='running')return json(409,{error:'Дождитесь завершения текущего стенда'});
                job={profile:definition.profile,status:'running',runId:null,error:null,startedAt:new Date().toISOString()};
                const current=job;
                // Список команд фиксирован; данные запроса не попадают в оболочку.
                Promise.resolve().then(()=>launch(definition.profile,runId=>current.runId=runId)).then(result=>{
                    current.status=result.ok?'finished':'failed';current.error=result.error||null;
                }).catch(error=>{current.status='failed';current.error=String(error.message).slice(0,1000);})
                    .finally(()=>{if(closing)server.close();});
                return json(202,{profile:definition.profile});
            }
            return json(404,{error:'Не найдено'});
        }catch(error){return json(error instanceof SyntaxError?400:500,{error:error instanceof SyntaxError?'Некорректный запрос':'Не удалось обработать локальную проверку'});}
    });
    return {server,scan,shutdown(){closing=true;if(job?.status!=='running')server.close();},stateDir};
}
function launchProfile(profile,onRunId){
    if(!Object.values(CASES).some(c=>c.profile===profile))throw Error('Неизвестный профиль');
    return new Promise(resolve=>{
        const child=spawn(process.execPath,[path.join(__dirname,'run_isolated_browser.cjs'),'--confirm-isolated-browser',profile],
            {cwd:root,env:cleanEnvironment(process.env),windowsHide:true,stdio:['ignore','pipe','pipe']});
        let tail='',runId=null;
        const consume=chunk=>{
            tail=(tail+chunk.toString('utf8')).slice(-12000);
            const match=tail.match(/Безопасный запуск ([0-9a-f-]{36})/);if(!runId&&match){runId=match[1];onRunId(runId);}
        };
        child.stdout.on('data',consume);child.stderr.on('data',consume);
        child.on('error',()=>resolve({ok:false,error:'Не удалось запустить собственный стенд'}));
        child.on('close',code=>resolve({ok:code===0,error:code===0?null:(tail.match(/Сбой браузерного сценария: ([^\n]+)/)?.[1]||'Сценарий завершился с ошибкой. Диагностика сохранена в папке запуска.')}));
    });
}
if(require.main===module){
    if(process.argv.length!==3||process.argv[2]!=='--confirm-local-review')throw Error('Запуск: node tests/helpers/playtest_review.cjs --confirm-local-review');
    const review=createReview();review.server.listen(8767,'127.0.0.1',()=>console.log('Плейтест: http://127.0.0.1:8767 · Ctrl+C закрывает окно после завершения собственного стенда.'));
    review.server.on('error',()=>{console.error('Не удалось открыть порт 8767. Другие процессы не останавливались.');process.exitCode=1;});
    process.on('SIGINT',()=>review.shutdown());process.on('SIGTERM',()=>review.shutdown());
}
module.exports={createReview,safeFile,launchProfile};
