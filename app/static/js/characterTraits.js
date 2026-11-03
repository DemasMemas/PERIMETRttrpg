const normalized=value=>String(value??'').trim().toLocaleLowerCase('ru').replaceAll('ё','е');
export function traitCount(data,name){return(data?.features?.specialTraits||[]).filter(t=>t?.active!==false&&normalized(t?.name)===normalized(name)).length;}
export function backgroundIs(data,name){return normalized(data?.basic?.background?.name)===normalized(name);}
export function pocketCapacity(data){return Math.max(0,10+passiveTraits(data).pocketCells);}
export function backpackItemVolume(data,item){
    const volume=Number(item?.volume)||0,quantity=Number(item?.quantity??1)||0;
    const count=traitCount(data,'Игрок в тетрис');
    const effective=volume>=5&&count?Math.floor(volume*Math.max(0,100-20*count)/100+1e-9):volume;
    return effective*quantity+(Array.isArray(item?.contents)?item.contents.reduce((sum,child)=>sum+backpackItemVolume(data,child),0):0);
}
export function passiveTraits(data){
    const n=name=>traitCount(data,name);
    return{painIgnored:n('Спартанец')||backgroundIs(data,'Контрактник')?3:0,painReduction:n('Железная воля'),
        weightMultiplier:Math.max(0,1-.25*(n('Тяжеловес')+Number(backgroundIs(data,'Кладовщик')))),
        movementPenalty:n('Ползун')-n('Я - конченный. Бегите')+Number(backgroundIs(data,'Утиный стрелок')),
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
