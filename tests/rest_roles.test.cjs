const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function setup(characters, isGM=true) {
    class Element {
        constructor(){this.children=[];this.style={};this.events={};this.classList={toggle(){}};}
        set innerHTML(value){this.children=[];this.html=value;}
        append(...values){this.children.push(...values);}
        appendChild(value){this.children.push(value);}
        addEventListener(name,handler){this.events[name]=handler;}
    }
    const dialog=new Element(),list=new Element(),requests=[];
    const context=vm.createContext({window:{isGM,currentLobbyId:9},Number,
        document:{addEventListener(){},getElementById:id=>id==='lobby-rest-modal'?dialog:list,
            createElement:()=>new Element(),createTextNode:text=>({textContent:text})}});
    const api=new vm.SyntheticModule(['Server'],function(){this.setExport('Server',{
        getLobbyCharacters:async id=>{requests.push(id);return characters;},
    });},{context});
    const utils=new vm.SyntheticModule(['showNotification'],function(){this.setExport('showNotification',()=>{});},{context});
    const module=new vm.SourceTextModule(fs.readFileSync(path.join(__dirname,'../app/static/js/rest.js'),'utf8'),{context});
    await module.link(specifier=>specifier==='./api.js'?api:utils);await module.evaluate();
    return {api:module.namespace,dialog,list,requests};
}

test('ГМ может включить во время персонажа, НПС и мутанта независимо от исходного вида листа',async()=>{
    const characters=[{id:1,name:'Игрок',time_active:true,data:{}},
        {id:2,name:'НПС',time_active:false,data:{basic:{is_npc:true}}},
        {id:3,name:'Мутант',time_active:false,data:{basic:{is_mutant:true}}},
        {id:4,name:'Включённый ГМом НПС',time_active:true,data:{character_type:'npc'}}];
    const s=await setup(characters);await s.api.openLobbyRestModal();
    assert.deepEqual(s.requests,[9]);assert.equal(s.list.children.length,4);
    for(let i=0;i<4;i++){
        const row=s.list.children[i],participant=row.children[0].children[0],active=row.children[1].children[0];
        assert.equal(participant.value,characters[i].id);
        const enabled=i===0||i===3;
        assert.equal(participant.checked,enabled);assert.equal(participant.disabled,!enabled);
        assert.equal(active.checked,enabled);
    }
    const inactive=s.list.children[1],participant=inactive.children[0].children[0],active=inactive.children[1].children[0];
    active.checked=true;active.events.change();assert.equal(participant.disabled,false);assert.equal(participant.checked,true);
    active.checked=false;active.events.change();assert.equal(participant.disabled,true);assert.equal(participant.checked,false);
});

test('Игрок не открывает настройку общего сна и активности времени вместо ГМа',async()=>{
    const s=await setup([{id:1}],false);await s.api.openLobbyRestModal();
    assert.equal(s.requests.length,0);assert.equal(s.list.children.length,0);
});
