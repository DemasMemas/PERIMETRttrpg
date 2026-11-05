"""Установленные модификации: численные эффекты и правила конкретного экземпляра."""
from copy import deepcopy
import re
from app.services.crafting_rules import catalog
from app.services.exceptions import ValidationError

WEAPON_EFFECTS={
 'b23':({'durability':10},{}),'c23':({'ergonomics':5},{}),
 'b24':({'fire_rate':2},{}),'c24':({},{'pistol_burst':True}),
 'b25':({},{'ergonomics_tiers':1}),'c25':({'accuracy':1},{}),
 'b27':({'min_strength':-2},{}),'c27':({},{'reload_start_ap':0}),
 'b28':({},{'ergonomics_tiers':1}),'c28':({'accuracy':1},{}),
 'b29':({'ergonomics':'=50'},{}),'c29':({},{'buckshot_all_targets':True}),
 'b32':({},{'burst_no_wear':.5}),'c32':({'fire_rate':3},{}),
 'b33':({},{'repair_discount':.5}),'c33':({'ergonomics':10},{}),
 'b34':({'burst_size':1},{}),'c34':({'accuracy':1},{}),
 'b36':({},{'rapid_penalty':1}),'c36':({'durability':10},{}),
 'b37':({'ergonomics':15},{}),'c37':({},{'burst_no_wear':.5}),
 'b38':({},{'burst_ap_delta':-1}),'c38':({},{'burst_accuracy_reduction':2}),
 'b40':({},{'repair_discount':.5}),'c40':({'ergonomics':15},{}),
 'b41':({},{'reload_start_ap':0}),'c41':({},{'ergonomics_tiers':1}),
 'b42':({},{'cycle_ap':0}),'c42':({'accuracy':2},{}),
 'b44':({'durability':15},{}),'c44':({},{'reload_ap_delta':-1}),
 'b45':({'ergonomics':15},{}),'c45':({},{'aimed_accuracy':1}),
 'b46':({'fire_rate':1},{}),'c46':({},{'blast_trauma_checks':2}),
 'b48':({'durability':15},{}),'c48':({'weight':-3},{}),
 'b49':({},{'repair_discount':.5}),'c49':({'ergonomics':15},{}),
 'b50':({'accuracy':1},{}),'c50':({'fire_rate':4},{})}

DIRECT_ARMOR={'armor-'+key for key in ('a5','a6','a7','a9','a10','a18','a19','a22','a23','a27','a28','a32','a38','a45','a47',
    'd5','d6','d9','d15','d18','d19','d20','g5','g7','g29','g30','g31','g41','g42','g50','g51','g52')}

SPECIAL_ARMOR={
 'a24':({'physical':5,'durability':5},{'incoming_trauma_disadvantage':True}),
 'a14':({}, {'stealth_hiding_bonus':4}),
 'd23':({}, {'anomaly_escape_reduction':3}),'a34':({}, {'fixed_reload_extra':1}),
 'd26':({}, {'polymer_regeneration':True}),
 'd24':({'radiation':30,'anomaly':-10},{}),'g40':({'explosive_physical':20},{}),
 'a20':({'explosive_physical':25},{'explosion_wear_reduction':.5}),
 'd10':({}, {'anomaly_wear_reduction':.5}),'a48':({}, {'supported_accuracy':2}),
 'a11':({'container_slots':1},{}),'a26':({'container_slots':1},{}),'a44':({'container_slots':1},{}),
 'd13':({'container_slots':1},{}),'d22':({'container_slots':1},{}),
 'a13':({}, {'ergonomics_tiers':1}),'a30':({'pocket_cells':10},{}),
 'a33':({}, {'radiation_with_mask':10}),'a35':({}, {'throwing_bonus':4}),
 'a41':({'physical':5},{'full_limb_armor':True}),'g6':({}, {'ear_protection':15}),
 'g11':({'charisma_penalty':'=0'},{}),'g12':({'accuracy_penalty':'=0'},{}),
 'g13':({'radiation':10},{'filter_wear_reduction':.5}),
 'g14':({'accuracy_penalty':1},{'flash_reduction':.5}),
 'g19':({'accuracy_penalty':'=0','charisma_penalty':'=0'},{}),'g20':({}, {'flash_reduction':.35}),
 'g26':({'ergonomics':3},{'hearing_advantage':True}),'g27':({}, {'aimed_head_difficulty':3}),
 'g35':({}, {'concussion_reduction':2}),'g36':({'ergonomics':3},{'hearing_advantage':True}),
 'g46':({'accuracy_penalty':-2},{}),'g47':({'charisma_penalty':-3},{}),
 'g48':({'movement_penalty':-1},{'filter_wear_reduction':.5}),'g58':({}, {'belt_weight_reduction':.5}),
 'g77':({}, {'belt_reload_ap_reduction':1}),'g78':({}, {'large_weapon_ergonomics':10}),
 'd27':({}, {'unpowered_weight_penalty_suppression':True}),
 'd32':({}, {'exoskeleton_run':True})}


def supported(rule):
    if rule['kind']=='weapon':return True
    if rule['kind']=='belt':return rule['id'] in {'armor-g58','armor-g77','armor-g78'}
    return rule['id'] in DIRECT_ARMOR or rule['id'].removeprefix('armor-') in SPECIAL_ARMOR


def install(item,record,replacing=None):
    previous=next((r for r in installed(item) if r['ruleId']==replacing),None)
    proposed=[m for m in item.get('modifications') or [] if not isinstance(m,dict) or m.get('ruleId')!=replacing]+[record]
    from math import floor
    container_delta=floor(stat({**item,'modifications':proposed},'container_slots',0))-floor(stat(item,'container_slots',0))
    containers=list(item.get('containers') or [])
    if container_delta<0:
        if sum(not c.get('item') for c in containers)<-container_delta:raise ValidationError('Сначала освободите дополнительные слоты контейнеров')
        for _ in range(-container_delta):
            index=next(i for i in range(len(containers)-1,-1,-1) if not containers[i].get('item'));containers.pop(index)
    elif container_delta>0:containers.extend({'item':None} for _ in range(container_delta))
    delta=float(record['modifiers'].get('durability',0))-float((previous or {}).get('modifiers',{}).get('durability',0))
    if delta:
        for key in ('durability','maxDurability','stageDurability','currentStageDurability'):
            if key in item:item[key]=max(0,item[key]+delta)
    item['modifications']=proposed
    if container_delta:item['containers']=containers


def worn_stat(data,key):
    return sum(stat(item,key,0) for item in ((data or {}).get('equipment') or {}).values() if isinstance(item,dict) and not item.get('protectionDisabled'))


def worn_items(data):
    for item in ((data or {}).get('equipment') or {}).values():
        if not isinstance(item,dict) or item.get('protectionDisabled'):continue
        yield item
        for module in item.get('installedModules') or []:
            if isinstance(module,dict) and not module.get('protectionDisabled'):yield module


def worn_rule(data,key):return sum(rule(item,key) for item in worn_items(data))


def gear_stat(item,key,base):
    value=stat(item,key,base)
    for module in item.get('installedModules') or []:
        if isinstance(module,dict):value=stat(module,key,value)
    return value


def charisma_bonus(item,base):
    value=base if base>=0 else -max(0,gear_stat(item,'charisma_penalty',-base))
    return value+gear_stat(item,'charisma',0)


def fire_profile(item,base,fire_rate):
    from math import floor
    result=deepcopy(base or {})
    if rule(item,'pistol_burst'):
        result.update(supports_burst=True,supports_suppression=True,supports_area_fire=True,
            burst_size=floor((floor((fire_rate or 0)/2)+1)*rule(item,'pistol_burst_factor',1)))
    result['burst_size']=max(0,floor(stat(item,'burst_size',result.get('burst_size',0))))
    return result


def explosive_profile(item,base):
    result=deepcopy(base)
    if rule(item,'blast_trauma_checks'):
        result['blast_trauma_checks']=2
    return result


def definition(rule_id):
    result=next((m for m in catalog()['modifications'] if m['id']==rule_id),None)
    if not result:raise ValidationError('Модификация не найдена')
    return result


def make_record(rule_id,*,gunsmith=False,installer_id=None):
    rule=definition(rule_id);factor=1.5 if gunsmith else 1
    if rule['kind']=='weapon':
        stats,flags=deepcopy(WEAPON_EFFECTS[rule_id.removeprefix('weapon-')])
    elif rule_id.removeprefix('armor-') in SPECIAL_ARMOR:
        stats,flags=deepcopy(SPECIAL_ARMOR[rule_id.removeprefix('armor-')])
    else:
        stats={};flags={}
        text=rule['description'].casefold().replace('физ. защита','физическая защита').replace('физ защита','физическая защита')
        fields={'физическая защита':'physical','аномальная защита':'anomaly','радиационная защита':'radiation','термо защита':'thermal',
            'прочность':'durability','штраф перемещения':'movement_penalty','штраф к весу':'weight_penalty','эргономика':'ergonomics','харизма':'charisma'}
        for phrase,key in fields.items():
            match=re.search(re.escape(phrase)+r'\s*([+-]\d+(?:[.,]\d+)?)\s*%?',text)
            if not match:match=re.search(r'([+-]\d+(?:[.,]\d+)?)\s*'+re.escape(phrase),text)
            if match:stats[key]=float(match[1].replace(',','.'))
        # Условные эффекты не превращаются в постоянные численные бонусы.
        if any(phrase in text for phrase in ('до первого попадания','при использовании','прицельный выстрел')):stats={}
        flags['special_rule']=rule_id
    def amplified(key,value):
        if isinstance(value,str) and value.startswith('='):return '='+str(float(value[1:])*factor)
        reduction=key in {'min_strength','weight','movement_penalty','weight_penalty','accuracy_penalty','charisma_penalty'}
        beneficial=value<0 if reduction else value>0
        return value*(factor if beneficial else 1)
    stats={key:amplified(key,value) for key,value in stats.items()}
    for key in ('burst_no_wear','repair_discount','aimed_accuracy','burst_accuracy_reduction','radiation_with_mask','throwing_bonus',
                'ear_protection','filter_wear_reduction','flash_reduction','aimed_head_difficulty','concussion_reduction','belt_weight_reduction',
                'explosion_wear_reduction','anomaly_wear_reduction','supported_accuracy','anomaly_escape_reduction',
                'stealth_hiding_bonus','large_weapon_ergonomics'):
        if key in flags:flags[key]*=factor
    if flags.get('pistol_burst'):flags['pistol_burst_factor']=factor
    return {'ruleId':rule_id,'name':rule.get('name') or rule['description'],'description':rule['description'],
        'family':rule['family'],'branch':rule.get('branch'),'level':rule.get('level',1),'installerId':installer_id,
        'gunsmith':gunsmith,'modifiers':stats,'rules':flags}


def installed(item):return [m for m in item.get('modifications') or [] if isinstance(m,dict) and m.get('ruleId')] if isinstance(item,dict) else []


def stat(item,key,base):
    value=float(base or 0)
    for record in installed(item):
        change=(record.get('modifiers') or {}).get(key)
        if change is None:continue
        if isinstance(change,str) and change.startswith('='):value=float(change[1:])
        else:value+=float(change)
    return value


def rule(item,key,default=0):
    values=[(record.get('rules') or {})[key] for record in installed(item) if key in (record.get('rules') or {})]
    if not values:return default
    if key in {'ergonomics_tiers','burst_ap_delta','reload_ap_delta','aimed_accuracy','burst_accuracy_reduction'}:return sum(values)
    return max(values)


def preserve(current,incoming):
    from app.services.portable_lights import walk
    previous={item.get('id'):item for _,item in walk(current or {},True) if item.get('id')}
    for _,item in walk(incoming,True):
        old=previous.get(item.get('id'));canonical=deepcopy(installed(old)) if old else []
        manual=[m for m in item.get('modifications') or [] if not isinstance(m,dict) or not m.get('ruleId')]
        if canonical or item.get('modifications') is not None:item['modifications']=manual+canonical
        if old and 'unlockedModificationSlots' in old:item['unlockedModificationSlots']=old['unlockedModificationSlots']
        else:item.pop('unlockedModificationSlots',None)


def family(item):
    from app.services.combat import CombatService
    template=CombatService._equipment_template(item)
    category=item.get('category') or (template.category if template else None)
    if category=='weapon' or item.get('model'):
        names={'pistol':'Пистолеты','shotgun':'Дробовики','submachine_gun':'Пистолеты-пулеметы','assault_rifle':'Штурмовые винтовки и карабины',
            'sniper_rifle':'Снайперские винтовки','grenade_launcher':'Гранатометы','machine_gun':'Пулемёты'}
        if re.search(r'штурмов|карабин',CombatService._weapon_subcategory(item)):return 'Штурмовые винтовки и карабины'
        return names.get(CombatService._weapon_class(item))
    attrs=CombatService._template_attributes(item)
    if attrs.get('soft_headwear') and str(item.get('name','')).casefold()!='ушанка':return None
    if attrs.get('modification_category'):return attrs['modification_category']
    if category=='gas_mask':return 'Противогазы'
    if category=='belt':return 'Пояс'
    if item.get('slotType')=='visor':return 'Забрала'
    if category=='helmet':
        if attrs.get('is_helmet_gas_mask') or 'противогаз' in str(item.get('name','')).casefold():return 'Шлема-противогазы'
        return 'Шлема с забралом' if CombatService._helmet_blocks_gas_mask(item,CombatService._equipment_template(item)) else 'Шлема без забрала'
    return None


def slots(item):
    from app.services.combat import CombatService
    attrs=CombatService._template_attributes(item);raw=item.get('itemClass',item.get('item_class',attrs.get('item_class')))
    template=CombatService._equipment_template(item)
    if raw is None and template:raw=template.item_class
    name=family(item);weapon=name in {d['family'] for d in catalog()['modifications'] if d['kind']=='weapon'}
    factor=1 if name=='Пояс' else 6 if name=='Экзоскелет' else 4 if str(raw).upper() in {'Н','H'} else int(raw) if str(raw).isdigit() else None
    maximum=3 if weapon else 5 if name=='Экзоскелет' else 4
    initial=min(maximum,5 if name=='Экзоскелет' else factor or 1)
    count=min(maximum,initial+int(item.get('unlockedModificationSlots') or 0))
    return {'family':name,'factor':factor,'maximum':maximum,'available':count,'used':len(installed(item))}
