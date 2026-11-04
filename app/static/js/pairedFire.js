// Общий режим; длина очереди остаётся собственной характеристикой каждого оружия.
export function pairedFireOptions(weapons,profiles,classes,ergonomics,police=false,minimumMachineGunBurst=2){
    if(weapons.length!==2||classes.includes('grenade_launcher')||weapons.some(item=>item.category==='melee_weapon'))return [];
    const cost=(mode,index)=>mode==='unaimed'?(classes[index]==='pistol'?1:2):mode==='rapid'?1:
        mode==='burst'?Math.max(1,(classes[index]==='submachine_gun'?2:3)-(police&&['pistol','submachine_gun'].includes(classes[index])?1:0)):mode==='suppression'?3:5;
    const options=[],first=(profiles[0].single_shot_options||[1]).filter(shots=>(profiles[1].single_shot_options||[1]).includes(shots));
    const price=mode=>{
        const worst=(ergonomics[0]?.value||0)<(ergonomics[1]?.value||0)?0:(ergonomics[1]?.value||0)<(ergonomics[0]?.value||0)?1:cost(mode,0)>=cost(mode,1)?0:1;
        return cost(mode,worst);
    };
    for(const shots of first)for(const [mode,label] of [['unaimed','Неприцельный'],['rapid','Беглый']])
        options.push({mode,label:label+(shots>1?` · ${shots} выстр. каждым`:''),shots,cost:price(mode),volleys:1});
    const automatic=profiles.every(profile=>profile.supports_burst)&&!weapons.some(item=>/револьвер/i.test((item.name||'')+' '+(item.subcategory||'')));
    if(automatic){
        const shots=profiles[0].machine_gun_burst?minimumMachineGunBurst:Number(profiles[0].burst_size);
        options.push({mode:'burst',label:'Очередь',shots,cost:price('burst'),volleys:1});
        if(profiles.every(profile=>profile.supports_suppression)){
            options.push({mode:'suppression',label:'Подавление · 1 очередь',shots,cost:3,volleys:1});
            options.push({mode:'suppression',label:'Подавление · 2 очереди',shots:shots*2,cost:5,volleys:2});
        }
        if(profiles.every(profile=>profile.supports_area_fire))options.push({mode:'area',label:'По области',shots:profiles[0].machine_gun_burst?10:shots*2,cost:5,volleys:2});
    }
    return options;
}
