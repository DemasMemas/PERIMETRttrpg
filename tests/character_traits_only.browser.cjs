// Короткий профиль действующих свойств на копии Припяти с двумя клиентами.
const assert=require('node:assert/strict');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession})=>{
    const rules=await request(`/lobbies/${lobby.id}/character-creation`,null,token,'GET');
    const character=await request(`/lobbies/${lobby.id}/characters`,{name:'Проверка боевых черт',data:{
        health:{current:700,max:700,effects:[]},inventory:{pockets:[],backpack:[]},skills:{physical:{strength:{base:10},agility:{base:10}}},equipment:{}}},token);
    const click=async(selector,client=page)=>{
        await client.until(`document.querySelector(${JSON.stringify(selector)})?.getClientRects().length`,'элемент доступен');
        let point;
        for(let attempt=0;attempt<30&&!point;attempt++){
            point=await client.evaluate(`(async()=>{const e=document.querySelector(${JSON.stringify(selector)});e.scrollIntoView({block:'center',behavior:'instant'});await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));const r=e.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;return e.isConnected&&e.contains(document.elementFromPoint(x,y))?{x,y}:null})()`);
            if(!point)await pause(50);
        }
        assert.ok(point,'Кнопка не перекрыта: '+selector);
        for(const type of ['mousePressed','mouseReleased'])await client.call('Input.dispatchMouseEvent',{type,...point,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1});
    };
    const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();
    const protocol=await connect(version.webSocketDebuggerUrl);
    const {browserContextId}=await protocol.call('Target.createBrowserContext');
    const {targetId}=await protocol.call('Target.createTarget',{url:base,browserContextId});
    const tabs=await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
    const peer=await connect(tabs.find(tab=>tab.id===targetId).webSocketDebuggerUrl);
    try{
        await peer.call('Page.enable');await peer.call('Runtime.enable');
        await peer.until('document.readyState==="complete"','второй клиент загружен');
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(token)});localStorage.setItem('user_id',${JSON.stringify(String(lobby.gm_id))})`);
        await peer.call('Page.navigate',{url:`${base}/lobbies/${lobby.id}/page`});
        for(let attempt=0;attempt<3;attempt++){
            try{await peer.until('Boolean(window.openCharacterSheet)','второй клиент авторизован');break;}
            catch(error){if(attempt===2||!/navigated|Cannot find context/.test(error.message))throw error;await pause(200);}
        }
        await require('./character_traits_combat.browser.cjs')({page,peer,request,token,lobby,character,rules,safeSession,click});
    }finally{await peer.close();await protocol.call('Target.disposeBrowserContext',{browserContextId});await protocol.close();}
};
