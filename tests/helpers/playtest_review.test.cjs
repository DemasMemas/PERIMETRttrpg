const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {createReview,safeFile}=require('./playtest_review.cjs');
const http=require('node:http');
const runId='12345678-1234-1234-1234-123456789abc';
async function setup(t,{cleaned=true,launch}={}){
    const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'playtest-review-')),reports=path.join(temporary,'reports'),stateDir=path.join(temporary,'state');
    fs.mkdirSync(path.join(reports,runId,'review-cases'),{recursive:true});
    const record={schema:1,caseId:'belt-tools',caseVersion:1,profile:'equipment',codeVersion:'test-version',recordedAt:'2026-11-08T01:00:00Z',
        automaticResult:'passed',steps:[{title:'Шаг',before:{quantity:5},after:{mounted:4}}],images:[],limitations:[],roles:['ГМ']};
    fs.writeFileSync(path.join(reports,runId,'review-cases/belt-tools.json'),JSON.stringify(record));
    fs.writeFileSync(path.join(reports,runId,'resources.json'),JSON.stringify({runId,browserStopped:cleaned,serverStopped:cleaned,databaseClosure:'graceful',cleanup:'confirmed'}));
    let version='test-version';const review=createReview({reports,stateDir,fingerprint:()=>version,launch});
    await new Promise(resolve=>review.server.listen(0,'127.0.0.1',resolve));
    const base='http://127.0.0.1:'+review.server.address().port;
    const initial=await(await fetch(base+'/api/state')).json();
    t.after(async()=>{await new Promise(resolve=>review.server.close(resolve));
        assert.ok(path.resolve(temporary).startsWith(path.resolve(os.tmpdir())+path.sep+'playtest-review-'));
        assert.equal(fs.realpathSync(temporary),path.resolve(temporary));fs.rmSync(temporary,{recursive:true});});
    const post=(endpoint,body,headers={})=>fetch(base+endpoint,{method:'POST',headers:{Origin:base,'Content-Type':'application/json','X-Review-Token':initial.token,...headers},body:JSON.stringify(body)});
    const decision={caseId:'belt-tools',runId,caseVersion:1,codeVersion:'test-version',status:'accepted',comment:''};
    return {review,base,post,decision,version:value=>version=value,initial,stateDir,reports};
}
test('новый результат ожидает автора; обычный просмотр ничего не принимает',async t=>{
    const {initial,stateDir}=await setup(t);const run=initial.cases.find(c=>c.id==='belt-tools').run;
    assert.equal(run.current,true);assert.equal(run.cleaned,true);assert.equal(run.decision,null);
    assert.equal(fs.existsSync(path.join(stateDir,'decisions.json')),false);
});
test('решение сохраняется с запуском и версией, повтор не дублирует его',async t=>{
    const {post,decision,review,stateDir}=await setup(t);
    assert.equal((await post('/api/decision',decision)).status,200);
    assert.equal((await post('/api/decision',decision)).status,200);
    const saved=JSON.parse(fs.readFileSync(path.join(stateDir,'decisions.json')));assert.equal(saved.length,1);
    assert.equal(saved[0].runId,runId);assert.equal(saved[0].codeVersion,'test-version');assert.equal(saved[0].status,'accepted');
    assert.equal(review.scan().cases.find(c=>c.id==='belt-tools').run.decision.status,'accepted');
});
test('изменение кода аннулирует актуальность, сохраняя историю решения',async t=>{
    const {post,decision,review,version}=await setup(t);await post('/api/decision',decision);version('changed');
    const run=review.scan().cases.find(c=>c.id==='belt-tools').run;assert.equal(run.current,false);assert.equal(run.decision.status,'accepted');
    assert.equal((await post('/api/decision',decision)).status,409);
});
test('старый экран не принимает другой запуск или версию сценария',async t=>{
    const {post,decision}=await setup(t);
    assert.equal((await post('/api/decision',{...decision,runId:'another'})).status,409);
    assert.equal((await post('/api/decision',{...decision,caseVersion:2})).status,409);
});
test('без подтверждения очистки стенда решение запрещено',async t=>{
    const {post,decision,initial}=await setup(t,{cleaned:false});assert.equal(initial.cases.find(c=>c.id==='belt-tools').run.cleaned,false);
    assert.equal((await post('/api/decision',decision)).status,409);
});
test('возврат на доработку требует замечание и сохраняет его',async t=>{
    const {post,decision,review}=await setup(t);
    assert.equal((await post('/api/decision',{...decision,status:'rework'})).status,400);
    assert.equal((await post('/api/decision',{...decision,status:'rework',comment:'Не видна кнопка'})).status,200);
    assert.equal(review.scan().cases.find(c=>c.id==='belt-tools').run.decision.comment,'Не видна кнопка');
});
test('чужая веб-страница и неверный локальный токен не отправляют решения',async t=>{
    const {post,decision,base}=await setup(t);
    assert.equal((await post('/api/decision',decision,{Origin:'https://example.com'})).status,403);
    assert.equal((await post('/api/decision',decision,{'X-Review-Token':'wrong'})).status,403);
    const status=await new Promise(resolve=>http.get(base+'/api/state',{headers:{Host:'example.com'}},response=>{response.resume();resolve(response.statusCode);}));
    assert.equal(status,403);
});
test('повтор запускает только фиксированный профиль и один стенд за раз',async t=>{
    let release,calls=0;const launch=(profile,onRunId)=>{calls++;assert.equal(profile,'equipment');onRunId(runId);return new Promise(resolve=>release=resolve);};
    const {post,review}=await setup(t,{launch});
    assert.equal((await post('/api/repeat',{caseId:'arbitrary-command'})).status,400);
    assert.equal((await post('/api/repeat',{caseId:'__proto__'})).status,400);
    assert.equal((await post('/api/repeat',{caseId:'constructor'})).status,400);
    assert.equal((await post('/api/repeat',{caseId:'belt-tools'})).status,202);
    assert.equal((await post('/api/repeat',{caseId:'belt-light'})).status,409);assert.equal(calls,1);
    assert.equal(review.scan().job.runId,runId);release({ok:true});await new Promise(resolve=>setImmediate(resolve));
    assert.equal(review.scan().job.status,'finished');assert.equal(review.scan().cases.find(c=>c.id==='belt-tools').run.decision,null);
});
test('ошибка запуска сохраняется без автоматического решения',async t=>{
    const {post,review}=await setup(t,{launch:async()=>({ok:false,error:'Ошибка сценария'})});
    await post('/api/repeat',{caseId:'belt-tools'});await new Promise(resolve=>setImmediate(resolve));
    assert.equal(review.scan().job.status,'failed');assert.equal(review.scan().job.error,'Ошибка сценария');
    assert.equal(review.scan().cases.find(c=>c.id==='belt-tools').run.decision,null);
});
test('экран и его ресурсы локальны; доступ к чужим файлам отсутствует',async t=>{
    const {base,reports}=await setup(t);
    for(const resource of ['/','/review.js','/review.css'])assert.equal((await fetch(base+resource)).status,200);
    assert.equal((await fetch(base+'/image/'+runId+'/../decisions.json')).status,404);
    assert.throws(()=>safeFile(reports,'..','outside.json'),/путь/);
});
test('пустой или массивный запрос отклоняется без запуска',async t=>{
    const {post}=await setup(t);assert.equal((await post('/api/repeat',null)).status,400);
    assert.equal((await post('/api/repeat',[])).status,400);
});
test('локальное закрытие не обрывает работающий стенд',async t=>{
    let release;const {post,review}=await setup(t,{launch:()=>new Promise(resolve=>release=resolve)});
    await post('/api/repeat',{caseId:'belt-tools'});assert.equal((await post('/api/shutdown',{})).status,409);
    release({ok:true});await new Promise(resolve=>setImmediate(resolve));
    assert.equal((await post('/api/shutdown',{})).status,200);assert.equal(review.server.listening,false);
});
