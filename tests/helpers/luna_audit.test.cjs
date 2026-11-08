const {test}=require('node:test'),assert=require('node:assert/strict');
const {corpus,validate}=require('./luna_audit.cjs');
test('очередь сохраняет каждую запись источников без слияния одинаковых названий',()=>{
    const records=corpus();assert.equal(new Set(records.map(r=>r.id)).size,records.length);
    assert.ok(records.some(r=>r.kind==='contract'));assert.ok(records.some(r=>r.kind==='modification'));
    assert.ok(records.filter(r=>r.kind==='item').length>600);
});
test('агент не может принять случай или сослаться на другую версию',()=>{
    const manifest={snapshotHash:'current',batches:[{id:'001',records:['item-1']}]};
    const report={schema:1,model:'gpt-6-luna',batchId:'001',snapshotHash:'current',results:[{recordId:'item-1',disposition:'not-tested'}]};
    assert.equal(validate(report,manifest),report);
    assert.throws(()=>validate({...report,snapshotHash:'old'},manifest));
    assert.throws(()=>validate({...report,results:[{recordId:'item-1',disposition:'passed'}]},manifest));
    assert.throws(()=>validate({...report,results:[{recordId:'item-1',disposition:'candidate'}]},manifest));
    assert.throws(()=>validate({...report,results:[]},manifest));
});
