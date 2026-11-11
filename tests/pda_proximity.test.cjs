const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');

async function fixture(request,{audio=false}={}){
    const alerts=[],notifications=[],storage=new Map(),timeouts=[],socketHandlers=new Map();
    const window=new EventTarget(),document=new EventTarget();Object.assign(window,{currentCharacterId:12,isLocationActive:false});document.hidden=false;
    const tones=[];
    if(audio)window.AudioContext=class{
        state='running';currentTime=0;destination={};async resume(){}
        createGain(){return{gain:{setValueAtTime(){},linearRampToValueAtTime(){}},connect(){}};}
        createOscillator(){return{frequency:{setValueAtTime(){}},connect(){},start(){tones.push(1);},stop(){}};}
    };
    window.addEventListener('pda-proximity-alert',event=>alerts.push(event.detail));
    const context=vm.createContext({window,document,CustomEvent,Event,EventTarget,Set,Date,Promise,
        localStorage:{getItem:()=> '7'},sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
        setInterval:()=>1,clearInterval(){},setTimeout:fn=>{timeouts.push(fn);return timeouts.length;},clearTimeout(){}});
    const module=new vm.SourceTextModule(fs.readFileSync(path.join(__dirname,'../app/static/js/pdaProximity.js'),'utf8'),{context});
    await module.link(async name=>new vm.SyntheticModule(name==='./api.js'?['Server']:['showNotification'],function(){
        this.setExport(name==='./api.js'?'Server':'showNotification',name==='./api.js'?{getPdaProximity:request}:(...args)=>notifications.push(args));
    },{context}));await module.evaluate();
    const socket={on:(name,handler)=>socketHandlers.set(name,handler),off:name=>socketHandlers.delete(name)};
    return {api:module.namespace,window,document,alerts,notifications,socket,storage,timeouts,socketHandlers,tones};
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

test('на подлокации детектор выбирает действующего персонажа, а не посторонний открытый лист',async()=>{
    const requests=[];
    const f=await fixture(async(...args)=>{requests.push(args);return {devices:[]};});
    const placed = new Set([23]);
    Object.assign(f.window,{isLocationActive:true,currentLocationId:8,currentLocationCharacterId:null,
        getLocationCharacterPosition: id => placed.has(id) ? {x: 1, y: 2} : null});
    const watcher=f.api.initPdaProximity(5,f.socket);await flush();
    assert.equal(requests.length,0,'Открытый лист персонажа вне сцены не запускает запросы к этой подлокации');
    f.window.currentLocationCharacterId=23;await watcher.refresh();
    assert.deepEqual(requests.at(-1),[23,5,8]);
    f.window.currentCharacterId=99;await watcher.refresh();
    assert.deepEqual(requests.at(-1),[23,5,8],'Просмотр другого листа не переключает детектор');
    placed.delete(23);await watcher.refresh();
    assert.equal(requests.length,2,'После удаления персонажа со сцены запросы прекращаются');
    placed.add(99);await watcher.refresh();
    assert.deepEqual(requests.at(-1),[99,5,8],'Лист присутствующего на сцене персонажа подходит');
    f.window.isLocationActive=false;await watcher.refresh();
    assert.deepEqual(requests.at(-1),[99,5,null],'На глобальной карте используется выбранный лист');
    watcher.close();
});

test('частые события движения объединяются, ошибка сети не создаёт сигналов',async()=>{
    let requests=0;const f=await fixture(async()=>{requests++;throw new Error('Нет связи');}),watcher=f.api.initPdaProximity(5,f.socket);await flush();
    for(let i=0;i<30;i++)f.socketHandlers.get('character_moved')();
    assert.equal(requests,1);assert.equal(f.timeouts.length,1);assert.equal(f.alerts.length,0);watcher.close();
});

test('пять спам-сигналов звучат независимо от отключённого детектора; повторы и чужие события не звучат',async()=>{
    const f=await fixture(async()=>signal(false),{audio:true}),played=[];
    f.window.addEventListener('pda-spam-sound',e=>played.push(e.detail.message_id));
    const watcher=f.api.initPdaProximity('5',f.socket);await flush();assert.equal(f.tones.length,0);
    const deliver=f.socketHandlers.get('pda_spam_received');
    deliver({lobby_id:5,character_id:99,message_id:'foreign'});
    deliver({lobby_id:6,character_id:12,message_id:'another-world'});
    for(let i=1;i<=5;i++)deliver({lobby_id:5,character_id:12,message_id:`spam:${i}`,noise:10});
    deliver({lobby_id:5,character_id:12,message_id:'spam:1',noise:10});
    await flush();assert.equal(f.tones.length,1,'Быстро пришедшие игровые сигналы идут очередью');
    for(let i=1;i<5;i++){assert.ok(f.timeouts.length);f.timeouts.shift()();await flush();}
    assert.equal(f.tones.length,5);assert.deepEqual(played,['spam:1','spam:2','spam:3','spam:4','spam:5']);
    watcher.close();assert.equal(f.socketHandlers.size,0);
});
