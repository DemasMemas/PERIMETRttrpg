const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const load=()=>{const src=fs.readFileSync(path.join(__dirname,'../app/static/js/prostheses.js'),'utf8').replace(/^import .*equipmentChoices.*;\r?\n/m,'');return import(`data:text/javascript;base64,${Buffer.from(src).toString('base64')}`);};

test('рабочая нога заменяет утрату, питание и поломка отключают замену',async()=>{
    const {prostheticLimb}=await load();
    const base={durability:75,energy_seconds:86400,attributes:{prosthesis:{efficiency:-3,batteries:1}}};
    const data={health:{effects:[{type:'amputation',area:'leftLeg'}],prostheses:{leftLeg:{base}}}};
    assert.equal(prostheticLimb(data,'leftLeg').efficiency,-3);
    base.energy_seconds=0;assert.equal(prostheticLimb(data,'leftLeg'),null);
    base.energy_seconds=100;base.broken=true;assert.equal(prostheticLimb(data,'leftLeg'),null);
});

test('база без кисти не заменяет руку; пропущенная смазка складывается с эффективностью',async()=>{
    const {prostheticLimb}=await load();
    const base={durability:50,attributes:{prosthesis:{efficiency:-4,provides_base:'strap'}}};
    const data={health:{effects:[{type:'amputation',area:'leftArm'}],prostheses:{leftArm:{base}}}};
    assert.equal(prostheticLimb(data,'leftArm'),null);
    data.health.prostheses.leftArm.hand={durability:25,missed_lubrications:1,attributes:{prosthesis:{efficiency:-5}}};
    assert.equal(prostheticLimb(data,'leftArm').efficiency,-10);
    data.health.effects[0].active=false;assert.equal(prostheticLimb(data,'leftArm'),null);
});
