const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

async function planning(server={},globals={}){
    const source=fs.readFileSync(path.join(__dirname,'../app/static/js/timePlanning.js'),'utf8');
    const context=vm.createContext(globals);
    const module=new vm.SourceTextModule(source,{context});
    await module.link(specifier=>new vm.SyntheticModule(
        specifier==='./api.js'?['Server']:['showNotification'],function(){
            if(specifier==='./api.js')this.setExport('Server',server);
            else this.setExport('showNotification',()=>{});
        },{context}));
    await module.evaluate();
    return module.namespace;
}

test('шкала оставляет свободный остаток и показывает границы блоков',async()=>{
    const api=await planning();
    const plan={window_minutes:120,start_minute:1920};
    const result=api.timelineSegments(plan,[
        {id:'repair',name:'Ремонт',duration_minutes:40},
        {id:'rest',name:'Отдых',duration_minutes:30},
    ]);
    assert.deepEqual(result.segments.map(part=>[part.start,part.end]),[[0,40],[40,70],[70,120]]);
    assert.equal(result.free,50);
    assert.equal(result.overflow,0);
    assert.equal(api.clockLabel(1920),'Сутки 1, 08:00');
});

test('окно показывает независимый план владельца и безопасно выводит имена',async()=>{
    const elements=new Map();
    for(const id of ['time-plans-modal','time-plans-btn','time-plans-list','time-plans-create','time-plans-count']){
        elements.set(id,{style:{display:'none'},hidden:false,innerHTML:'',addEventListener(){},
            querySelector(){return {addEventListener(){}};}});
    }
    const plan={id:7,request_id:'request-001',character_id:11,character_name:'<script>тест</script>',
        owner_id:3,start_minute:1920,window_minutes:120,activities:[],
        occupied_minutes:0,free_minutes:120,overflow_minutes:0,overflow_state:'none',
        confirmed:false,revision:1};
    const api=await planning({getTimePlans:async()=>({plans:[plan],owners:[],skills:{},is_gm:false})},{
        document:{getElementById:id=>elements.get(id)},console,
    });
    api.initTimePlanning(1,{on(){}});
    await new Promise(resolve=>setImmediate(resolve));
    assert.match(elements.get('time-plans-list').innerHTML,/&lt;script&gt;тест&lt;\/script&gt;/);
    assert.match(elements.get('time-plans-list').innerHTML,/Свободно 120 мин/);
    assert.equal(elements.get('time-plans-modal').style.display,'flex');
    assert.equal(elements.get('time-plans-count').textContent,'1');
});

test('превышение выделенного времени не скрывается',async()=>{
    const api=await planning();
    const result=api.timelineSegments({window_minutes:120,start_minute:1920},[
        {id:'repair',name:'Ремонт',duration_minutes:80},
        {id:'rest',name:'Отдых',duration_minutes:60},
    ]);
    assert.equal(result.occupied,140);
    assert.equal(result.overflow,20);
    assert.equal(result.free,0);
    assert.equal(result.segments[1].over,true);
    assert.equal(api.clockLabel(2060),'Сутки 1, 10:20');
});
