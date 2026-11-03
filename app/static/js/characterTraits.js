const normalized=value=>String(value??'').trim().toLocaleLowerCase('ru').replaceAll('ё','е');
export const ruleText=value=>String(value??'').replace(/\s+/gu,' ').trim();
export function traitCount(data,name){return(data?.features?.specialTraits||[]).filter(t=>t?.active!==false&&normalized(t?.name)===normalized(name)).length;}
export function backgroundIs(data,name){return normalized(data?.basic?.background?.name)===normalized(name);}
export function pocketCapacity(data){return Math.max(0,10+passiveTraits(data).pocketCells);}
export function carriedWeaponEntries(data){
    const entries=[];
    const visit=(value,path,weapon=false)=>{
        if(Array.isArray(value)){value.forEach((item,index)=>visit(item,[...path,index],weapon));return;}
        if(!value||typeof value!=='object')return;
        if((weapon||['weapon','melee_weapon'].includes(value.category))&&!value.isVirtual)entries.push({weapon:value,path});
        for(const key of ['contents','pouches','storedItem'])visit(value[key],[...path,key]);
    };
    visit(data?.weapons,['weapons'],true);
    for(const key of ['pockets','backpack'])visit(data?.inventory?.[key],['inventory',key]);
    for(const [key,item] of Object.entries(data?.equipment||{}))visit(item,['equipment',key]);
    return entries;
}
export function facialProfile(data){
    const health=data?.health||{},parts=new Set(['leftEye','rightEye','leftEar','rightEar','nose']);
    const missing=new Set(Object.entries(health.organs||{}).filter(([key,value])=>parts.has(key)&&value?.current===0).map(([key])=>key));
    for(const effect of health.effects||[])if(effect?.active!==false&&effect.type==='organ_loss'&&parts.has(effect.area))missing.add(effect.area);
    const eyes=['leftEye','rightEye'].filter(key=>missing.has(key)).length,ears=['leftEar','rightEar'].filter(key=>missing.has(key)).length;
    const beauty=traitCount(data,'Вечно красив');
    return {eyes,ears,charismaLevel:beauty?0:-(5*eyes+(ears===2?8:3*ears)+(missing.has('nose')?5:0)),
        charismaBonus:beauty*missing.size,shooting:eyes===1?-3:0,melee:eyes===2?-5:0,agility:eyes===2?-3:0,hearing:ears===1?-5:0};
}
export function backpackItemVolume(data,item){
    const volume=Number(item?.volume)||0,quantity=Number(item?.quantity??1)||0;
    const count=traitCount(data,'Игрок в тетрис');
    const effective=volume>=5&&count?Math.floor(volume*Math.max(0,100-20*count)/100+1e-9):volume;
    return effective*quantity+(Array.isArray(item?.contents)?item.contents.reduce((sum,child)=>sum+backpackItemVolume(data,child),0):0);
}
export function passiveTraits(data){
    const n=name=>traitCount(data,name);
    const drawn=Number.isInteger(data?.activeWeaponIndex)?data.weapons?.[data.activeWeaponIndex]:null;
    const breakthrough=backgroundIs(data,'Прорывник')&&drawn&&/пистолет.пулем[её]т|штурмов|карабин|дробов/i.test(drawn.subcategory||drawn.attributes?.subcategory||'');
    return{painIgnored:n('Спартанец')||backgroundIs(data,'Контрактник')?3:0,painReduction:n('Железная воля'),
        weightMultiplier:Math.max(0,1-.25*(n('Тяжеловес')+Number(backgroundIs(data,'Кладовщик')))),
        movementPenalty:n('Ползун')-n('Я - конченный. Бегите')+Number(backgroundIs(data,'Утиный стрелок'))-(breakthrough?2:0),
        pocketCells:30*n('Глубокие карманы')-5*n('Узкие карманы'),
        blockDiscount:n('Глухая оборона'),freeSwing:!!n('Никто не должен знать о черте'),
        noDarknessPenalty:backgroundIs(data,'Ночной дозор')};
}
export function addLearningXp(data,path,skill,amount=1){
    const interested=(data?.features?.specialTraits||[]).filter(t=>t?.active!==false&&normalized(t?.name)===normalized('Заинтересованный')&&t.choices?.skill===path).length;
    const multiplier=interested>=3?3:interested===2?2:interested?1.5:1;
    const total=amount*multiplier+(Number(skill.learningRemainder)||0);
    skill.xp=(Number(skill.xp)||0)+Math.floor(total);skill.learningRemainder=total-Math.floor(total);
    return skill;
}
