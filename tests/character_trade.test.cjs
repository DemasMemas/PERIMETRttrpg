const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {pathToFileURL}=require('node:url');
const load=()=>import(pathToFileURL(path.join(__dirname,'../app/static/js/characterTrade.js')).href);
const base={actorOffer:[],targetOffer:[],actorMoney:100,targetMoney:0,actorBalance:1000,targetBalance:500};
test('денежный подарок разрешён, пустое предложение и неверные суммы отклоняются',async()=>{
    const {validateTrade}=await load();
    assert.equal(validateTrade(base),'');
    for(const actorMoney of [-1,1.5,NaN,Infinity,1001])assert.ok(validateTrade({...base,actorMoney}));
    assert.ok(validateTrade({...base,actorMoney:0}));
});
test('выбор скрытых поиском предметов сохраняет точный путь и количество',async()=>{
    const {tradeOffer,tradeEntryKey}=await load();
    const entries=[{item:{id:'one',name:'Бинт'},path:['inventory','pockets',0]},
        {item:{id:'two',name:'Вода'},path:['inventory','backpack',1]}];
    const selected=new Map([[tradeEntryKey(entries[1]),2]]);
    assert.deepEqual(tradeOffer(entries,selected),[{item_id:'two',name:'Вода',path:['inventory','backpack',1],amount:2}]);
});
test('вещь и её содержимое не предлагаются дважды',async()=>{
    const {validateTrade}=await load();
    assert.ok(validateTrade({...base,actorOffer:[{amount:1,path:['inventory','pockets',0]},
        {amount:1,path:['inventory','pockets',0,'contents',0]}]}));
    assert.equal(validateTrade({...base,actorOffer:[{amount:1,path:['inventory','pockets',0]},
        {amount:1,path:['inventory','pockets',1]}]}),'');
});

test('торговля вычитает стоимость проданного из покупки и не подставляет наценку',async()=>{
    const {merchantTotals,tradeEntryKey}=await load();
    const a={item:{id:'bandage',templateId:1},path:['inventory','pockets',0]};
    const b={item:{id:'water',templateId:2},path:['inventory','backpack',0]};
    const selected={actor:new Map([[tradeEntryKey(a),1]]),target:new Map([[tradeEntryKey(b),2]])};
    const prices={'1':{buy:25,sell:50},'2':{buy:40,sell:75}};
    assert.deepEqual(merchantTotals({actor:[a],target:[b]},selected,prices),{sale:25,purchase:150,actorMoney:125,targetMoney:0});
});

test('неполный инструмент оценивается по зарядам с округлением вниз для обеих цен',async()=>{
    const {merchantTotals,merchantUnitPrice,tradeEntryKey}=await load();
    const actor={item:{id:'tool-actor',templateId:42,category:'tool',uses:7,maxUses:12},path:['inventory','pockets',0]};
    const target={item:{id:'tool-target',templateId:42,category:'tool',uses:3,maxUses:12},path:['inventory','backpack',0]};
    const prices={'42':{buy:101,sell:211}};
    assert.equal(merchantUnitPrice(actor.item,prices,'buy'),58);
    assert.equal(merchantUnitPrice(target.item,prices,'sell'),52);
    assert.deepEqual(merchantTotals({actor:[actor],target:[target]},
        {actor:new Map([[tradeEntryKey(actor),1]]),target:new Map([[tradeEntryKey(target),1]])},prices),
        {sale:58,purchase:52,actorMoney:0,targetMoney:6});
});

test('ответ сервера и сетевое событие показывают одно уведомление о сделке',async()=>{
    const {tradeResultNotice}=await load();
    const result={id:'notice-test',status:'completed',payload:{_merchant:true}};
    assert.equal(tradeResultNotice(result).message,'Сделка завершена');
    assert.equal(tradeResultNotice(result),null);
    assert.equal(tradeResultNotice({...result,status:'pending'}),null);
});
