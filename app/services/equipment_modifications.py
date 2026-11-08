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
    'd5','d6','d9','d11','d15','d18','d19','d20','d31','g5','g7','g29','g30','g31','g41','g42','g50','g51','g52')}

SPECIAL_ARMOR={
 'a15':({}, {'auto_injector':True}),'a31':({}, {'auto_injector':True}),
 'a24':({'physical':5,'durability':5},{'incoming_trauma_disadvantage':True}),
 'a14':({}, {'stealth_hiding_bonus':4}),
 'd23':({}, {'anomaly_escape_reduction':3}),'a34':({}, {'fixed_reload_extra':1}),
 'd14':({}, {'electronics_insulation':True}),
 'd7':({}, {'incoming_trauma_disadvantage':True,'bleeding_per_effect_reduction':1,'anomaly_effect_cancel':True}),
 'd26':({}, {'polymer_regeneration':True}),
 'd30':({}, {'worn_burst_accuracy_reduction':2,'snatch_difficulty_bonus':6}),
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
 'g57':({}, {'extra_belt_pouches':2}),
 'g56':({}, {'free_pouch_mounts':1}),'g76':({}, {'magazine_pouch_mounts':2}),
 'g61':({}, {'belt_tool_mounts':4}),
 'g70':({}, {'belt_artifact_mount':True}),
 'g62':({}, {'belt_light_mount':True}),
 'g66':({}, {'medical_pouch_capacity_multiplier':2}),
 'g77':({}, {'belt_reload_ap_reduction':1}),'g78':({}, {'large_weapon_ergonomics':10}),
 'd27':({}, {'unpowered_weight_penalty_suppression':True}),
 'd28':({'movement_penalty':'=3'},{}),
 'a49':({}, {'spare_plates':True}),
 'a39':({}, {'liquid_crystal_layer':20}),
 'g25':({}, {'nvg_mount':True}),
 'g18':({}, {'nvg_mount':True,'visor_light_mount':True}),
 'g21':({}, {'stop_layer':True}),
 'g37':({}, {'neck_brace':True}),
 'd32':({}, {'exoskeleton_run':True})}


def supported(rule):
    if rule['kind']=='weapon':return True
    if rule['kind']=='belt':return rule['id'] in {'armor-g56','armor-g57','armor-g58','armor-g61','armor-g62','armor-g66','armor-g70','armor-g76','armor-g77','armor-g78'}
    return rule['id'] in DIRECT_ARMOR or rule['id'].removeprefix('armor-') in SPECIAL_ARMOR


def install(item,record,replacing=None):
    previous=next((r for r in installed(item) if r['ruleId']==replacing),None)
    proposed=[m for m in item.get('modifications') or [] if not isinstance(m,dict) or m.get('ruleId')!=replacing]+[record]
    if previous and previous['ruleId']=='armor-g70' and not any(
            mod.get('ruleId')=='armor-g70' for mod in proposed if isinstance(mod,dict)):
        from app.services.belt_attachments import loaded
        if loaded(item,'artifact'):raise ValidationError('Сначала снимите артефакт с крепления пояса')
    if previous and previous['ruleId']=='armor-g61' and not any(
            mod.get('ruleId')=='armor-g61' for mod in proposed if isinstance(mod,dict)):
        from app.services.belt_attachments import loaded
        if loaded(item):raise ValidationError('Сначала снимите инструменты с креплений пояса')
    if previous and previous.get('ruleId') in {'armor-a15','armor-a31'} and not any(
            mod.get('ruleId') in {'armor-a15','armor-a31'} for mod in proposed if isinstance(mod,dict)):
        from app.services.auto_injector import loaded
        if loaded(item):raise ValidationError('Сначала извлеките препарат из автоинъектора')
    if previous and (previous.get('rules') or {}).get('visor_light_mount') and not any(
            (mod.get('rules') or {}).get('visor_light_mount') for mod in proposed if isinstance(mod,dict)):
        if any(module.get('slotType')=='light' for module in item.get('installedModules') or [] if isinstance(module,dict)):
            raise ValidationError('Сначала снимите фонарь с крепления забрала')
    if previous and (previous.get('rules') or {}).get('extra_belt_pouches') and not any(
            (mod.get('rules') or {}).get('extra_belt_pouches') for mod in proposed if isinstance(mod,dict)):
        if item.get('extraStoredItem'):
            raise ValidationError('Сначала снимите дополнительный шлем или противогаз с пояса')
        if len(item.get('pouches') or [])>belt_pouch_limit({**item,'modifications':proposed}):
            raise ValidationError('Сначала снимите дополнительные подсумки с пояса')
    if previous and any(rule(item,key)>rule({**item,'modifications':proposed},key)
            for key in ('extra_belt_pouches','free_pouch_mounts','magazine_pouch_mounts')):
        after={**item,'modifications':proposed};pouches=item.get('pouches') or []
        if len(pouches)>belt_pouch_limit(after) or sum(not magazine_pouch(p) for p in pouches)>belt_general_pouch_limit(after):
            raise ValidationError('Сначала снимите дополнительные подсумки с креплений')
    if previous and (previous.get('rules') or {}).get('nvg_mount') and not any(
            (mod.get('rules') or {}).get('nvg_mount') for mod in proposed if isinstance(mod,dict)):
        from app.services.combat import CombatService
        native_slots=CombatService._template_attributes(item).get('slots') or []
        if not any(isinstance(slot,dict) and slot.get('type')=='nvg' for slot in native_slots) and any(
                isinstance(module,dict) and module.get('slotType')=='nvg' for module in item.get('installedModules') or []):
            raise ValidationError('Сначала снимите ПНВ с крепления')
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
    if record['ruleId']=='armor-d31':item['reanimationCharge']=True
    if record['ruleId']=='armor-a39':item['liquidCrystalState']={'charged':True,'remaining_seconds':0}
    elif previous and previous['ruleId']=='armor-a39':item.pop('liquidCrystalState',None)
    if record['ruleId']=='armor-g37':item['neckBraceState']={'charged':True,'remaining_seconds':0}
    elif previous and previous['ruleId']=='armor-g37':item.pop('neckBraceState',None)
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


def stop_layer_candidate(data,head_impact):
    if not isinstance(head_impact,dict) or head_impact.get('armor_zone')!='face':return None
    helmet=((data or {}).get('equipment') or {}).get('helmet') or {}
    if not isinstance(helmet,dict) or helmet.get('protectionDisabled'):return None
    for visor in helmet.get('installedModules') or []:
        if not isinstance(visor,dict) or visor.get('slotType')!='visor' or visor.get('protectionDisabled') or visor.get('stopLayerBroken'):continue
        if visor.get('stage',1)==5 or type(visor.get('durability')) in {int,float} and visor['durability']<=0:continue
        if rule(visor,'stop_layer'):return visor
    return None


def consume_stop_layer(visor,data):
    # Сохраняем экземпляр и вложенные приборы для снятия; разрушенное забрало
    # больше не защищает, не служит креплением и не восстанавливается листом.
    visor.update(stopLayerBroken=True,protectionDisabled=True,durability=0,currentStageDurability=0,stage=5)
    child_ids=set()
    for module in visor.get('installedModules') or []:
        if not isinstance(module,dict):continue
        if module.get('slotType')=='nvg':module['active']=False
        if module.get('id'):child_ids.add(module['id'])
    for binding in (((data.get('health') or {}).get('combatMeta') or {}).get('portableLights') or []):
        if binding.get('id') in child_ids:binding['on']=False
    return {'item_id':visor.get('id'),'name':visor.get('name') or 'Забрало','destroyed':True}


def visor_light_mount(data,path):
    """Фонарь действительно установлен на модифицированном забрале надетого шлема."""
    if not isinstance(path,list) or len(path)!=6 or path[:3]!=['equipment','helmet','installedModules'] or path[4]!='installedModules':return False
    helmet=((data or {}).get('equipment') or {}).get('helmet') or {}
    if not isinstance(helmet,dict) or helmet.get('protectionDisabled'):return False
    modules=helmet.get('installedModules') or []
    if not isinstance(modules,list) or type(path[3]) is not int or not 0<=path[3]<len(modules):return False
    visor=modules[path[3]]
    if not isinstance(visor,dict):return False
    lights=visor.get('installedModules') or []
    return bool(visor.get('slotType')=='visor' and not visor.get('protectionDisabled') and rule(visor,'visor_light_mount')
        and isinstance(lights,list) and type(path[5]) is int and 0<=path[5]<len(lights)
        and isinstance(lights[path[5]],dict) and lights[path[5]].get('slotType')=='light')


def helmet_module_available(data,path):
    if not isinstance(path,list) or path[:3]!=['equipment','helmet','installedModules']:return False
    node=data
    for key in path:
        if isinstance(node,dict) and isinstance(key,str):node=node.get(key)
        elif isinstance(node,list) and type(key) is int and 0<=key<len(node):node=node[key]
        else:return False
        if isinstance(node,dict) and (node.get('protectionDisabled') or node.get('stopLayerBroken')):return False
    return isinstance(node,dict)


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
        if isinstance(value,str) and value.startswith('='):
            return value if key=='movement_penalty' else '='+str(float(value[1:])*factor)
        reduction=key in {'min_strength','weight','movement_penalty','weight_penalty','accuracy_penalty','charisma_penalty'}
        beneficial=value<0 if reduction else value>0
        return value*(factor if beneficial else 1)
    stats={key:amplified(key,value) for key,value in stats.items()}
    for key in ('burst_no_wear','repair_discount','aimed_accuracy','burst_accuracy_reduction','radiation_with_mask','throwing_bonus',
                'ear_protection','filter_wear_reduction','flash_reduction','aimed_head_difficulty','concussion_reduction','belt_weight_reduction',
                'explosion_wear_reduction','anomaly_wear_reduction','supported_accuracy','anomaly_escape_reduction',
                'stealth_hiding_bonus','large_weapon_ergonomics','worn_burst_accuracy_reduction','snatch_difficulty_bonus',
                'medical_pouch_capacity_multiplier','liquid_crystal_layer','bleeding_per_effect_reduction'):
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


def belt_general_pouch_limit(item,lobby_id=None):
    from app.services.item_template_lookup import resolve_item_template
    template=resolve_item_template(item,lobby_id)
    attrs=(template.attributes or {}) if template else (item.get('attributes') or {})
    base=attrs.get('max_pouches',2)
    if type(base) is not int or not 0<=base<=20:base=2
    return base+(0 if item.get('extraStoredItem') else int(rule(item,'extra_belt_pouches')))+int(rule(item,'free_pouch_mounts'))


def belt_pouch_limit(item,lobby_id=None):
    return belt_general_pouch_limit(item,lobby_id)+int(rule(item,'magazine_pouch_mounts'))


def magazine_pouch(item,lobby_id=None):
    from app.services.item_template_lookup import resolve_item_template
    template=resolve_item_template(item,lobby_id)
    attrs=(template.attributes or {}) if template else (item.get('attributes') or {})
    if template and template.category!='pouch' or item.get('category') not in {None,'pouch'}:return False
    allowed=attrs.get('allowed_categories',[]) if template else attrs.get('allowed_categories',item.get('allowed_categories',[]))
    return isinstance(allowed,list) and len(allowed)==1 and allowed[0]=='magazine'


def validate_belt_pouches(current,incoming,lobby_id):
    previous=((current or {}).get('equipment') or {}).get('belt') or {}
    belt=((incoming or {}).get('equipment') or {}).get('belt') or {}
    if not belt.get('templateId'):return
    pouches=belt.get('pouches') or []
    if not isinstance(pouches,list):raise ValidationError('Подсумки пояса должны быть списком')
    limit=belt_pouch_limit(belt,lobby_id)
    extra=belt.get('extraStoredItem')
    if extra:
        from app.services.item_template_lookup import resolve_item_template
        extra_template=resolve_item_template(extra,lobby_id) if isinstance(extra,dict) else None
        if not extra_template or extra_template.category not in {'helmet','gas_mask'} or extra.get('category')!=extra_template.category:
            raise ValidationError('На дополнительном креплении должен быть шлем или противогаз')
        if not isinstance(extra.get('id'),str) or not extra['id'] or extra.get('quantity',1)!=1:
            raise ValidationError('На дополнительном креплении нужен один отдельный предмет')
        from app.services.portable_lights import walk
        if any(item is not belt and item.get('id')==extra['id'] for _,item in walk(incoming,True)):
            raise ValidationError('Предмет на дополнительном креплении повторяется в инвентаре')
        if not rule(belt,'extra_belt_pouches'):
            raise ValidationError('Для второго предмета на поясе нужна модификация «Расширенный пояс»')
        if len(pouches)>limit:
            raise ValidationError('Дополнительный предмет занимает места двух подсумков')
    general_limit=belt_general_pouch_limit(belt,lobby_id)
    general_count=sum(not magazine_pouch(p,lobby_id) for p in pouches)
    if len(pouches)<=limit and general_count<=general_limit:return
    old_pouches=previous.get('pouches') or []
    same_belt=(belt.get('id') and belt.get('id')==previous.get('id')) or (
        not belt.get('id') and not previous.get('id') and belt.get('templateId')==previous.get('templateId'))
    if not same_belt or len(pouches)>len(old_pouches) or general_count>sum(not magazine_pouch(p,lobby_id) for p in old_pouches):
        if len(pouches)>limit:raise ValidationError(f'На поясе доступно не больше {limit} подсумков')
        raise ValidationError('Дополнительные магазинные крепления подходят только для магазинных подсумков')


def preserve(current,incoming,*,is_gm=True):
    from app.services.portable_lights import walk
    if not is_gm:
        previous_pda=(((current or {}).get('modifications') or {}).get('pda') or {}).get('items') or []
        next_pda=(((incoming or {}).get('modifications') or {}).get('pda') or {}).get('items') or []
        if next_pda!=previous_pda:raise ValidationError('Модификации КПК устанавливаются через игровой процесс')
    def items(data):
        for path,item in walk(data or {},True):
            yield item
            if item.get('category')!='belt' and path!=['equipment','belt']:continue
            stored=item.get('storedItem') or {}
            primary=stored.get('savedAttributes') if isinstance(stored,dict) else None
            if isinstance(primary,dict):yield primary
            extra=item.get('extraStoredItem')
            if isinstance(extra,dict):yield extra
    previous={item.get('id'):item for item in items(current) if item.get('id')}
    for item in items(incoming):
        old=previous.get(item.get('id'));canonical=deepcopy(installed(old)) if old else []
        if old and old.get('stopLayerBroken'):
            item.update(stopLayerBroken=True,protectionDisabled=True,durability=0,currentStageDurability=0,stage=5)
        else:item.pop('stopLayerBroken',None)
        manual=[m for m in item.get('modifications') or [] if not isinstance(m,dict) or not m.get('ruleId')]
        if not is_gm:
            old_manual=[m for m in old.get('modifications') or [] if not isinstance(m,dict) or not m.get('ruleId')] if old else []
            if manual!=old_manual:raise ValidationError('Модификации снаряжения устанавливаются через игровой процесс')
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
