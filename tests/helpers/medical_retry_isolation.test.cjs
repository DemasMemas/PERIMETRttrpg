const {test}=require('node:test'),assert=require('node:assert/strict');
const guard=require('./browser_isolation.cjs'),{parseBrowserArgs}=require('./run_isolated_browser.cjs');
const run='12345678-1234-1234-1234-123456789abc';
test('Medical retry requires explicit owned-file gate and rejects external controls',()=>{
    assert.equal(parseBrowserArgs(['--confirm-isolated-browser','medical-retry']).scenario,'medical-retry');
    assert.throws(()=>parseBrowserArgs(['--confirm-memory-browser','medical-retry']));
    assert.throws(()=>parseBrowserArgs(['--confirm-isolated-browser','medical-retry','--db=x']));
    const env={PYTHON_DOTENV_DISABLED:'1',VTT_SAFE_RUN_ID:run,VTT_SAFE_SCENARIO:'medical-retry',VTT_BROWSER_GPU:'1',VTT_SAFE_DB_MODE:'owned-file',VTT_SAFE_DB_CONFIRMATION:'--confirm-isolated-browser',VTT_MEDICAL_RETRY_ONLY:'1'};
    guard.validateEnvironment(env);
    for(const extra of [{VTT_SAFE_DB_MODE:'memory',VTT_SAFE_DB_CONFIRMATION:'--confirm-memory-browser'},{VTT_MEDICAL_RETRY_ONLY:'0'},{VTT_DB_PATH:'x'},{VTT_PRIPYAT:'1'},{VTT_BROWSER_PROFILE:'x'},{BROWSER_EXECUTABLE:'x'}])assert.throws(()=>guard.validateEnvironment({...env,...extra}));
    assert.throws(()=>guard.pythonServer(12345,run,'memory','medical-retry'));
    const normal=guard.pythonServer(12345,run,'owned-file'),medical=guard.pythonServer(12345,run,'owned-file','medical-retry');
    assert.ok(!normal.includes('medical_retry_fixture'));
    assert.ok(medical.indexOf('install(app, db, check_database')>medical.indexOf('with database_scope():'));
    assert.ok(medical.includes('assert_owned_database(app, db, owner)'));
});
test('Private medical fields are detected recursively; import has no app/process side effects',()=>{
    const {privatePaths}=require('../medical_retry.browser.cjs');
    assert.deepEqual(privatePaths({data:{health:{combatMeta:{mustDoRetry:{kind:'medical',difficulty:4}}}}}),[]);
    assert.deepEqual(privatePaths({data:[{medical_retry:{attack_details:{}}}]}),['$.data.0.medical_retry','$.data.0.medical_retry.attack_details']);
});
