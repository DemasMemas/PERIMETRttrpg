// Явный запуск безопасного маршрута; произвольные VTT/пути не наследуются.
const path=require('node:path'),{spawn}=require('node:child_process'),{randomUUID}=require('node:crypto');
const {cleanEnvironment,BROWSER_SCENARIOS,BROWSER_DB_MODES}=require('./browser_isolation.cjs');
function parseBrowserArgs(args) {
    const mode=Object.keys(BROWSER_DB_MODES).find(mode=>BROWSER_DB_MODES[mode]===args[0]);
    if(args.length!==2||!mode||!Object.hasOwn(BROWSER_SCENARIOS,args[1]))throw Error('Нужно --confirm-isolated-browser или --confirm-memory-browser и pripyat|widgets|skills|movement|magazine|damage-revoke|durable-reload|medical-retry|sheet-repro|rotation|surface|height|gm-relocation|terrain|walls|psi|climb|jump|graphics|ice|slippery|grenades|world-loading');
    if(['trade','fire-rate','health-audit','weather','morning-answers','stations','covers','traps','electronics','improvised','catalog','locks','equipment','lights','sideview','effects','controllers','movement','magazine','damage-revoke','durable-reload','medical-retry','sheet-repro','skills','rotation','surface','height','gm-relocation','terrain','walls','psi','climb','jump','graphics','anomaly-boar','ice','slippery','grenades','world-loading'].includes(args[1])&&mode!=='owned-file')throw Error('Сценарий требует --confirm-isolated-browser');
    if(['creation','weapons','traits'].includes(args[1])&&mode!=='owned-file')throw Error('Создание персонажа требует --confirm-isolated-browser');
    return {mode,scenario:args[1],confirmation:args[0]};
}
function main(args=process.argv.slice(2)) {
    const {mode,scenario,confirmation}=parseBrowserArgs(args);
    const root=path.resolve(__dirname,'../..'),runId=randomUUID();
    const env={...cleanEnvironment(process.env),VTT_SAFE_RUN_ID:runId,VTT_SAFE_SCENARIO:scenario,VTT_BROWSER_GPU:'1',VTT_SAFE_DB_MODE:mode,VTT_SAFE_DB_CONFIRMATION:confirmation,
        [BROWSER_SCENARIOS[scenario]]:'1'};
    console.log(`Безопасный запуск ${runId}; GPU обязателен, режим БД: ${mode}.`);
    const child=spawn(process.execPath,[path.join(root,'tests/world_object_editor.browser.cjs')],{cwd:root,env,windowsHide:true,stdio:'inherit'});
    child.on('error',error=>{console.error(error.message);process.exitCode=1;});
    child.on('exit',code=>{process.exitCode=code??1;});
}
if(require.main===module)main();
module.exports={main,parseBrowserArgs};
