const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');

async function fixture(request){
    const alerts=[],notifications=[],storage=new Map(),timeouts=[],socketHandlers=new Map();
    const window=new EventTarget(),document=new EventTarget();Object.assign(window,{currentCharacterId:12,isLocationActive:false});document.hidden=false;
    window.addEventListener('pda-proximity-alert',event=>alerts.push(event.detail));
    const context=vm.createContext({window,document,CustomEvent,Event,EventTarget,Set,Date,Promise,
        localStorage:{getItem:()=> '7'},sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
        setInterval:()=>1,clearInterval(){},setTimeout:fn=>{timeouts.push(fn);return timeouts.length;},clearTimeout(){}});
    const module=new vm.SourceTextModule(fs.readFileSync(path.join(__dirname,'../app/static/js/pdaProximity.js'),'utf8'),{context});
    await module.link(async name=>new vm.SyntheticModule(name==='./api.js'?['Server']:['showNotification'],function(){
        this.setExport(name==='./api.js'?'Server':'showNotification',name==='./api.js'?{getPdaProximity:request}:(...args)=>notifications.push(args));
    },{context}));await module.evaluate();
    const socket={on:(name,handler)=>socketHandlers.set(name,handler),off:name=>socketHandlers.delete(name)};
    return {api:module.namespace,window,document,alerts,notifications,socket,storage,timeouts,socketHandlers};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const signal=(sound=true)=>({devices:[{id:'physical-pda',sound,signals:[{id:'unknown',login:'Незнакомый КПК'}]}]});

test('детектор работает без открытого КПК, не повторяет тревогу и замечает повторное приближение',async()=>{
    let snapshot=signal(false);const f=await fixture(async()=>snapshot),watcher=f.api.initPdaProximity(5,f.socket);await flush();
    assert.equal(f.alerts.length,1);assert.equal(f.alerts[0].sound,false);
    await watcher.refresh();assert.equal(f.alerts.length,1);
    snapshot={devices:[{id:'physical-pda',sound:true,signals:[]}]};await watcher.refresh();
    snapshot=signal();await watcher.refresh();assert.equal(f.alerts.length,2);assert.equal(f.alerts[1].sound,true);
    watcher.close();
    const reloaded=f.api.initPdaProximity(5,f.socket);await flush();assert.equal(f.alerts.length,2,'перезагрузка наблюдателя не повторяет сохранённую тревогу');
    reloaded.close();assert.equal(f.socketHandlers.size,0);
});

test('поздний ответ для ранее выбранного персонажа не вызывает чужую тревогу',async()=>{
    let finish;const f=await fixture(()=>new Promise(resolve=>finish=resolve)),watcher=f.api.initPdaProximity(5,f.socket);
    f.window.currentCharacterId=13;finish(signal());await flush();assert.equal(f.alerts.length,0);assert.equal(f.notifications.length,0);watcher.close();
});

test('частые события движения объединяются, ошибка сети не создаёт сигналов',async()=>{
    let requests=0;const f=await fixture(async()=>{requests++;throw new Error('Нет связи');}),watcher=f.api.initPdaProximity(5,f.socket);await flush();
    for(let i=0;i<30;i++)f.socketHandlers.get('character_moved')();
    assert.equal(requests,1);assert.equal(f.timeouts.length,1);assert.equal(f.alerts.length,0);watcher.close();
});
