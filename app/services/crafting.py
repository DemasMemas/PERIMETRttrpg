"""Серверные работы: резерв компонентов, игровое время, к100 и решения ГМа."""
from copy import deepcopy
from math import ceil,floor
import random
import re
from uuid import uuid4
from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db
from app.models import LobbyCharacter,LocationCharacter,LocationCombatState,LocationObject
from app.models.templates import ItemTemplate
from app.services.character import CharacterService
from app.services.character_starting_purchase import item_from_template
from app.services.crafting_rules import catalog,creation_quote,resolve_creation
from app.services.exceptions import ValidationError,ConflictError,NotFoundError,PermissionDenied
from app.services.general_consumable import _available_uses,_consume_entry
from app.services.magazine import inventory_slot
from app.services.portable_lights import walk
from app.services.transaction import atomic_operation

CRAFTED_TOOLS={
    'recipe-m31':('Шлифовальные инструменты 50/50',20),
    'recipe-m32':('Паяльные инструменты 50/50',10),
    'recipe-m33':('Смесительные инструменты 50/50',30),
    'recipe-m35':('Набор инструментов Оружейника (Упрощёные) 15/15',5),
    'recipe-m36':('Набор инструментов Оружейника (Стандартные) 25/25',10),
    'recipe-m37':('Набор инструментов Бронника (Упрощёные) 10/10',5),
    'recipe-m38':('Набор инструментов Бронника (Стандартные) 20/20',10),
}


def preserve(current,incoming):
    if 'crafting' in (current or {}):incoming['crafting']=deepcopy(current['crafting'])
    else:incoming.pop('crafting',None)


def now(character):return ((character.lobby.game_day or 1)*1440+(character.lobby.game_time_minutes or 0))*60


def normalized(name):
    text=str(name or '').casefold().replace('ё','е').replace('металические','металлические')
    text=re.sub(r'\(\s*\d+\s*\)','',text)
    return re.sub(r'[^\w]+',' ',text).strip()


def item_names(item):
    names={normalized(item.get('name'))}
    catalog_component_names={
        'активированный уголь таблетка':'активированный уголь',
        'веревка 10м':'веревка',
        'стеклянная бутылка объем 1 литр 3 использования воды':'стеклянная бутылка',
        'пластиковая бутылка объем 1 литр 3 использования воды':'пластиковая бутылка',
        'канистра пустая объем 15 литров 45 использований воды':'канистра',
        'топливо 1 литр':'топливо',
        'хлорные таблетки 10 штук':'хлорные таблетки',
        'банка пива':'пиво любое',
        'котелок объем 2 литра 6 использований воды':'котелок',
        'кастрюля объем 5 литров 15 использований воды':'кастрюля',
    }
    alias=catalog_component_names.get(normalized(item.get('name')))
    if alias:names.add(alias)
    if item.get('category')=='tool':
        base=re.sub(r'\s+\d+\s*/\s*\d+\s*$','',str(item.get('name') or ''))
        names.add(normalized(base))
    return names


def entries(data):
    result=[]
    for path,item in walk(data,True):
        if not isinstance(item,dict) or not item.get('name'):continue
        if any(part in path for part in ('installedModules','installedMagazine','fixedAmmo','ammo','loadedGrenade')):continue
        # Установленное снаряжение и целые заполненные контейнеры не являются материалом.
        if path[:2]==['inventory','pockets'] or path[:2]==['inventory','backpack'] or 'contents' in path:
            result.append((path,item))
    return result


def recipe_by_id(recipe_id):
    recipe=next((r for r in catalog()['recipes'] if r['id']==recipe_id),None)
    if not recipe:raise ValidationError('Рецепт не найден')
    return recipe


def _template(name):
    from app.services.equipment_catalog import canonical_catalog_templates
    templates=canonical_catalog_templates(ItemTemplate.query.all());key=normalized(name)
    exact=next((t for t in templates if normalized(t.name)==key),None)
    if exact:return exact
    aliases={
        'анальгин':'Анальгин (Таблетка)',
        'пенициллин':'Пенициллин (Таблетка)',
        'ибупрофен':'Ибупрофен (Таблетка)',
        'хирург набор крест':'Хирургический набор "Крест"',
        'хирург набор айболит':'Кустарный набор "Айболит"',
        'жаропонижающее мороз':'Жаропонижающее средство "Мороз"',
        'рдг 6':'Дымовая граната РДГ-6',
        'napalm an':'Зажигательная граната Napalm-AN',
        'рг 60тб':'Термобарическая граната РГ-60ТБ',
    }
    if key in aliases:
        return next((t for t in templates if normalized(t.name)==normalized(aliases[key])),None)
    return next((t for t in templates if t.category=='grenade' and normalized(t.name).removeprefix('граната ')==key),None)


def _environment(component):return normalized(component['name']).startswith('доступ к ')


def _chlorine_uses(item):
    if normalized(item.get('name'))!='хлорные таблетки 10 штук':return None
    packet={**item,'maxUses':10}
    if packet.get('uses') is None:packet['uses']=10
    return _available_uses(packet)


def _craft_filter_charges(item):
    if item.get('category') != 'gas_mask_module' or normalized(item.get('name')) != 'фильтр для противогаза':
        return None
    from app.services.gas_mask_filters import filter_charges
    quantity=item.get('quantity',1)
    if type(quantity) is not int or quantity<1:return 0
    return int(filter_charges(item))*quantity


def _allocate(data,recipe,*,with_status=False):
    from app.services.trade import _contains_individual_items
    candidates=entries(data);spent={};allocations=[];missing=[];statuses=[]
    for index,component in enumerate(recipe['components']):
        if _environment(component):continue
        ammo_template=db.session.get(ItemTemplate,component['ammo_template_id']) if component.get('ammo_template_id') else None
        meat_component = index == 0 and recipe['id'] in {'recipe-aa15', 'recipe-aa16'}
        variants=component.get('alternatives') or [component]
        remaining=None;picked=None;options=[];prior_spent=spent.copy()
        for variant in variants:
            names={normalized(name) for name in variant['name'].split('/')}
            if 'ргд 5' in names:names.add('граната ргд 5')
            if 'ф 1' in names:names.add('граната ф 1')
            if 'анальгин' in names:names.add('анальгин таблетка')
            left=variant['quantity'];trial_spent=prior_spent.copy();trial=[];total=0
            for path,item in candidates:
                from app.services.flamethrower import charges
                attrs=item.get('attributes') or {};whole=bool(meat_component or component.get('whole_tank') or component.get('whole_air')
                    or normalized(variant['name']) in {'стеклянная бутылка','пластиковая бутылка','канистра','кастрюля','котелок'})
                if component.get('ammo_template_id'):
                    from app.services.crafting_ammo import is_plain_ammo
                    if not ammo_template or not is_plain_ammo(item,ammo_template):continue
                matches_box=(recipe['id']=='recipe-aa4' and variant['name']=='Спички'
                    and normalized(item.get('name'))=='спички 10 штук')
                if meat_component:
                    from app.services.cooked_meat import ingredient_matches
                    matches=ingredient_matches(recipe['id'],item)
                else:matches=(attrs.get('flamethrower_tank')==component.get('whole_tank') and charges(item)==0 if component.get('whole_tank')
                    else bool(attrs.get('flamethrower_air')) if component.get('whole_air') else matches_box or bool(item_names(item)&names))
                if not matches or _contains_individual_items(item):continue
                filter_available=_craft_filter_charges(item) if component.get('filter_charges') else None
                if component.get('filter_charges') and filter_available is None:continue
                packet_uses=_chlorine_uses(item)
                key=tuple(path);available=(item.get('quantity',1) if whole else
                    (filter_available if filter_available is not None else packet_uses if packet_uses is not None else _available_uses(item))
                    if item.get('quantity',1)>0 else 0)-prior_spent.get(key,0)
                unit=10 if matches_box else 1
                total+=max(0,available)*unit
                take=min(ceil(left/unit),available)
                if take>0:
                    trial.append({'component':index,'path':path,'amount':take,'item':deepcopy(item),
                                  'whole':whole,'filter_charges':bool(component.get('filter_charges'))})
                    trial_spent[key]=trial_spent.get(key,0)+take;left=max(0,left-take*unit)
                if not left and not with_status:break
            if with_status:options.append({'name':variant['name'],'available':total,'required':variant['quantity']})
            if not left and picked is None:
                picked=(trial_spent,trial)
                if not with_status:break
            if picked is None:remaining=left
        if picked:
            spent,chosen=picked;allocations.extend(chosen)
        else:
            if component.get('alternatives'):missing.append(f"{component['source_text']}: нет полного варианта")
            else:missing.append(f"{component['name']}: не хватает {remaining}")
        if with_status:statuses.append({'alternatives':options})
    return (allocations,missing,statuses) if with_status else (allocations,missing)


def _tools(character,data,recipe,option):
    options=recipe['difficulty_options'];count=max(1,len(options))
    if type(option) is not int or not 0<=option<count:raise ValidationError('Выберите инструмент по порядку из рецепта')
    raw=recipe['tools_text']
    if normalized(raw)=='обязателен' and recipe['group']=='Электроника':raw='Станок Электрика'
    if normalized(raw) in {'да','нужна'} and recipe['group']=='Ловушки':raw='Печь'
    required=[part.strip() for part in raw.split(',') if part.strip()]
    from app.services.workbenches import NAMES
    station_keys={normalized(name) for name in NAMES}
    names=set();stations=set()
    for _,item in entries(data):
        if item.get('quantity',1)<=0 or not (_available_uses(item)>0 or item_names(item)&{'котелок','кастрюля'}):
            continue
        for name in item_names(item):
            names.add(name)
            if item.get('category')=='tool' and name in station_keys:stations.add(name)
    # Станки/костёр на сцене доступны только рядом и на текущем этаже.
    placement=LocationCharacter.query.filter_by(character_id=character.id).order_by(LocationCharacter.last_action.desc().nullslast(),LocationCharacter.id.desc()).first()
    if placement:
        for obj in LocationObject.query.filter_by(location_id=placement.location_id).all():
            props=obj.properties or {}
            if (props.get('level',0) or 0)!=(placement.floor_level or 0):continue
            if ((obj.tile_x-placement.pos_x)**2+(obj.tile_y-placement.pos_y)**2)**.5>1.5:continue
            if obj.type in {'campfire','fire_barrel'}:
                if props.get('is_lit',True):names.add('костер')
            else:
                station_name=normalized(props.get('craft_workbench') or obj.name)
                if obj.type in {'workbench','household_workbench'} and station_name in station_keys:
                    stations.add(station_name)
                else:names.add(normalized(obj.name))
    missing=[]
    for part in required:
        if normalized(part) in {'не нужен','не нужна','не обязателен','не обязательна','не обязателен станок',''} or part in {'---','-'}:continue
        if normalized(part)=='самогонный апарат':part='Самогонный Аппарат'
        choices=part.split('/')
        chosen=choices[option] if len(choices)==count and count>1 else part
        if not any((normalized(choice) in stations if normalized(choice) in station_keys
                    else normalized(choice) in names) for choice in (chosen.split('/') if count==1 else [chosen])):
            missing.append(chosen)
    return missing


def quote(character,data,payload):
    from app.services.combat import CombatService
    from app.services.equipment_passives import craft_difficulty_modifier
    from app.services.crafting_ammo import chosen_ammo
    recipe=recipe_by_id(payload.get('recipe_id'));option=payload.get('tool_option',0)
    recipe,ammo_template,ammo_variant=chosen_ammo(recipe,payload)
    if recipe.get('requires_tool_crafting'):
        from app.services.character_traits import trait_count,background_is
        if not trait_count(data,'Мастер на все руки') and not background_is(data,'Полевой ученый'):raise ValidationError('Изготовление инструментов требует «Мастера на все руки» или предыстории «Полевой учёный»')
    missing_tools=_tools(character,data,recipe,option)
    allocations,missing,component_status=_allocate(data,recipe,with_status=True)
    component_missing_count=len(missing)
    state=data.get('crafting') or {};approval=next((r for r in state.get('tool_requests',[]) if r['id']==payload.get('approval_id')
        and r['recipe_id']==recipe['id'] and r['status']=='approved' and not r.get('used')),None)
    if approval and approval.get('tool_id'):
        if not any(item.get('id')==approval['tool_id'] for _,item in entries(data)):raise ConflictError('Одобренный инструмент больше не доступен')
    environment=[c['name'] for c in recipe['components'] if _environment(c)]
    if environment and not (approval and approval.get('environment')):missing.append('Нужно подтверждение условий ГМом: '+', '.join(environment))
    level=CombatService._skill_value(data,'skills.'+recipe['skill'])
    difficulty=recipe['difficulty_options'][option] if recipe['difficulty_options'] else 0
    result=({'difficulty':0,'automatic_success':True,'failure_loss':{'random':0,'chosen':0,'all':False}}
        if recipe.get('maintenance') else creation_quote(level,difficulty,base_automatic=recipe['automatic'],
            difficulty_modifier=(approval.get('modifier',0) if approval else 0)+craft_difficulty_modifier(data)))
    tool_output=CRAFTED_TOOLS.get(recipe['id'])
    output=ammo_template or _template(tool_output[0] if tool_output else recipe['output_name'])
    if not output:missing.append('В каталоге нет шаблона результата: '+recipe['output_name'])
    if tool_output and output and (output.category!='tool' or type((output.attributes or {}).get('uses')) is not int
            or not tool_output[1]<output.attributes['uses']):
        missing.append('Каталожный максимум зарядов инструмента не совпадает с рецептом')
    if missing_tools:missing.append('Нужны: '+', '.join(missing_tools))
    if recipe.get('duration_seconds') is None:missing.append('В рецепте не задан срок работы')
    return {**result,'recipe':recipe,'allocations':allocations,'missing':missing,
        'component_status':component_status,'other_missing':missing[component_missing_count:],
        'output_id':output.id if output else None,'output_variant':ammo_variant,
        'ammo_template_id':ammo_template.id if ammo_template else None,
        'output_uses':tool_output[1] if tool_output else None,
        'approval_id':approval['id'] if approval else None,'duration_seconds':recipe.get('duration_seconds')}


def _escrow(data,allocations):
    from app.services.character_interaction import CharacterInteractionService
    result=[]
    identities={}
    for allocation in allocations:
        parent,index=inventory_slot(data,allocation['path']);item=parent[index]
        item.setdefault('id',str(uuid4()));identity=item['id'];path=tuple(allocation['path'])
        if identity in identities and identities[identity]!=path:raise ConflictError('У выбранных материалов повторяется идентификатор')
        identities[identity]=path;allocation['item']['id']=identity
    # На этапе планирования одним и тем же предметом могут покрываться несколько строк.
    for allocation in allocations:
        original=allocation['item'];original.setdefault('id',str(uuid4()))
        path=CharacterInteractionService._resolve_item_path(data,allocation['item'].get('id'),allocation['path'])
        parent,index=inventory_slot(data,path);item=parent[index]
        item.setdefault('id',original['id'])
        if _chlorine_uses(item) is not None:
            item['maxUses']=10
            item.setdefault('uses',10)
        amount=allocation['amount'];piece=deepcopy(item)
        if allocation.get('filter_charges'):
            from app.services.gas_mask_filters import filter_charges
            charges=filter_charges(item);quantity=item.get('quantity',1)
            if int(charges)*quantity<amount:raise ConflictError('Заряды фильтров изменились')
            parent.pop(index)
            remaining=amount;kept=0
            for _ in range(quantity):
                taken=min(int(charges),remaining);remaining-=taken
                if taken:
                    reserved=deepcopy(piece);reserved.update(id=str(uuid4()),quantity=1,durability=taken)
                    reserved.setdefault('attributes',{})['durability']=taken
                    result.append({'component':allocation['component'],'amount':taken,'item':reserved,
                                   'maximum':charges,'filter_charges':True})
                if charges>taken:
                    leftover=deepcopy(piece);leftover.update(id=original['id'] if not kept else str(uuid4()),
                        quantity=1,durability=charges-taken)
                    leftover.setdefault('attributes',{})['durability']=charges-taken
                    parent.append(leftover);kept+=1
            continue
        maximum=1 if allocation.get('whole') else max(1,int(item.get('maxUses') or (item.get('attributes') or {}).get('uses') or 1))
        if allocation.get('whole'):
            if item.get('quantity',1)<amount:raise ConflictError('Ёмкости изменились')
            if item.get('quantity',1)==amount:parent.pop(index)
            else:item['quantity']-=amount
        elif not _consume_entry(parent,index,amount):raise ConflictError('Компоненты изменились')
        piece.update(id=str(uuid4()),quantity=ceil(amount/maximum))
        if maximum>1:
            piece.update(uses=amount%maximum or maximum,maxUses=maximum)
            piece.setdefault('attributes',{})['uses_remaining']=piece['uses']
        result.append({'component':allocation['component'],'amount':amount,'item':piece,'maximum':maximum})
    return result


def _return_materials(data,escrow,lost):
    destination=data.setdefault('inventory',{}).setdefault('pockets',[])
    for index,entry in enumerate(escrow):
        amount=entry['amount']-lost.get(index,0)
        if not amount:continue
        item=deepcopy(entry['item']);maximum=entry['maximum']
        if entry.get('filter_charges'):
            item.update(quantity=1,durability=amount)
            item.setdefault('attributes',{})['durability']=amount
            destination.append(item)
            continue
        item['quantity']=ceil(amount/maximum)
        if maximum>1:
            item['uses']=amount%maximum or maximum
            item.setdefault('attributes',{})['uses_remaining']=item['uses']
        destination.append(item)


def snapshot(character,user_id):
    from app.services.character_creation import _catalog
    from app.services.crafting_ammo import available_ammo
    from app.services.equipment_modifications import supported
    from app.services.flamethrower import resources
    from app.services.salvage import kind_of,tool_bonus
    CharacterService.check_access(character,user_id)
    data=character.data or {};state=deepcopy(data.get('crafting') or {})
    current=state.get('current')
    if current:current['remaining_seconds']=max(0,current['ready_at']-now(character))
    rules=catalog()
    placement=LocationCharacter.query.filter_by(character_id=character.id).order_by(LocationCharacter.last_action.desc().nullslast(),LocationCharacter.id.desc()).first()
    for entry in rules['modifications']:entry['automatic_installation']=supported(entry)
    return {'catalog':rules,'ammo_options':available_ammo(),'state':state,'inventory':[{'path':path,'item':item,'uses':_available_uses(item),'salvage_kind':kind_of(item),'salvage_tool_bonus':tool_bonus(item)} for path,item in entries(data)],
        'targets':modification_targets(data),'is_gm':character.lobby.gm_id==user_id,'revision':character.revision,
        'placement':{'lobby_id':character.lobby_id,'location_id':placement.location_id,'actor_id':placement.id} if placement else None,
        'flamethrowers':[{'index':index,'weapon':weapon,'resources':[deepcopy(i) for i in resources(data,weapon)]}
            for index,weapon in enumerate(data.get('weapons') or []) if (weapon.get('attributes') or {}).get('flamethrower')],
        'skills':[{'path':group+'.'+key,'label':label} for group,skills in _catalog()['skills'].items() for key,label in skills.items()]}


def modification_targets(data):
    from app.services.equipment_modifications import family,slots
    result=[]
    for path,item in walk(data,True):
        if not item.get('name') and not item.get('model'):continue
        if family(item):result.append({'path':path,'item':item,'slots':slots(item)})
    return result


def modification_target_plan(data,payload):
    from app.services.equipment_modifications import definition,supported,installed,slots
    rule=definition(payload.get('rule_id'))
    if not supported(rule):raise ValidationError('Эта модификация ожидает подключения своего специального механизма')
    target=next((entry['item'] for entry in modification_targets(data) if entry['path']==payload.get('target_path')),None)
    if not target:raise ValidationError('Выберите предмет для модификации')
    slot=slots(target)
    if slot['family']!=rule['family']:raise ValidationError('Модификация предназначена для другого вида снаряжения')
    existing=installed(target);replacing=next((m for m in existing if m['ruleId']==payload.get('replace_rule_id')),None)
    if payload.get('replace_rule_id') and (not replacing or replacing['level']!=rule['level']):raise ValidationError('Заменить можно модификацию того же уровня')
    others=[m for m in existing if m is not replacing]
    if any(m['ruleId']==rule['id'] for m in others):raise ValidationError('Модификация уже установлена')
    if len(others)>=slot['available']:raise ValidationError('Нет открытого слота модификации')
    if rule['kind']=='weapon' and any(m['level']==rule['level'] for m in others):raise ValidationError('У оружия может быть одна модификация каждого уровня')
    if rule['kind']=='weapon' and any(not any(m['level']==level for m in others) for level in range(1,rule['level'])):
        raise ValidationError('Уровни модификаций оружия устанавливаются последовательно')
    proposed=[*others,rule]
    if rule['kind']!='weapon':
        for candidate in proposed:
            if candidate['level']>1 and any(not any(m.get('branch')==candidate.get('branch') and m['level']==level for m in proposed) for level in range(1,candidate['level'])):
                raise ValidationError('Модификации ветки устанавливаются последовательно')
    return {'rule':rule,'target':target,'slots':slot,'previous':[m['ruleId'] for m in existing]}


def modification_plan(character,data,payload):
    from app.services.crafting_rules import modification_quote
    from app.services.combat import CombatService
    common=modification_target_plan(data,payload)
    rule,target,slot=common['rule'],common['target'],common['slots']
    if rule.get('scientist_only'):raise ValidationError('Модификации экзоскелета устанавливаются только у учёных')
    if rule['family'] not in {'Ушанка','Забрала','Пояс'} and not any(book.get('book_kind')=='equipment' and normalized(book.get('book_subject')).replace(' броня','')==normalized(rule['family']) for book in (data.get('crafting') or {}).get('library',[])):
        raise ValidationError('Сначала прочитайте книгу об этом виде снаряжения')
    tool=next((item for path,item in entries(data) if path==payload.get('tool_path')),None)
    if not tool:raise ValidationError('Выберите набор инструментов или станок')
    kind='weapon' if rule['kind']=='weapon' else 'armor';word='оружейник' if kind=='weapon' else 'бронник'
    if word not in normalized(tool['name']):raise ValidationError('Нужны инструменты соответствующего мастера')
    workbench=bool((tool.get('attributes') or {}).get('craft_workbench'))
    level=CombatService._skill_value(data,'skills.other.engineering')
    contributions=payload.get('material_contributions',[])
    if not isinstance(contributions,list) or len(contributions)>64:raise ValidationError('Некорректный список дополнительных материалов')
    if kind!='armor' and contributions:raise ValidationError('Материалы уменьшают сложность только модификаций снаряжения')
    from app.services.item_template_lookup import resolve_item_template
    material_value=0;allocations=[];used_paths=set()
    for part in contributions:
        if not isinstance(part,dict):raise ValidationError('Выберите материал и количество')
        path=part.get('path');amount=part.get('quantity')
        source=next((i for p,i in entries(data) if p==path),None)
        template=resolve_item_template(source,character.lobby_id) if source else None
        if not template or template.category!='crafting_material':raise ValidationError('Доплачивать можно материалами для создания и ремонта')
        from app.services.trade import _contains_individual_items
        if _contains_individual_items(source):raise ValidationError('Материал содержит другие предметы')
        if not template.price or 'price' in ((template.attributes or {}).get('unspecified_fields') or []):raise ValidationError('Цена этого материала пока не задана')
        if type(amount) is not int or not 1<=amount<=_available_uses(source):raise ValidationError('Недостаточно выбранного материала')
        if tuple(path) in used_paths:raise ValidationError('Один материал выбран повторно')
        used_paths.add(tuple(path))
        maximum=max(1,int((template.attributes or {}).get('uses') or 1))
        material_value+=amount*max(0,template.price or 0)/maximum
        allocations.append({'component':len(allocations),'path':path,'amount':amount,'item':deepcopy(source)})
    plan=modification_quote(kind,rule['level'],'Н' if slot['factor']==4 else slot['factor'],level,double_charges=payload.get('double_charges') is True,workbench=workbench,material_value=material_value)
    from app.services.equipment_passives import craft_difficulty_modifier
    plan['difficulty']+=craft_difficulty_modifier(data)
    if rule['kind']=='belt':plan['professional_price']=1000
    if not plan['skill_sufficient']:raise ValidationError(f"Нужна Инженерия {plan['required_engineering']}")
    grade={'simplified':1,'standard':2,'advanced':3}[plan['tool_grade']]
    name=normalized(tool['name']);actual=3 if workbench or 'расшир' in name else 2 if 'стандарт' in name else 1 if 'упрощ' in name else 0
    if actual<grade:raise ValidationError('Нужен более высокий набор инструментов')
    uses=tool.get('uses',tool.get('maxUses',(tool.get('attributes') or {}).get('uses',0)))
    if type(uses) not in {int,float} or uses<plan['tool_uses']:raise ValidationError('Не хватает зарядов инструментов')
    approval=next((r for r in (data.get('crafting') or {}).get('tool_requests',[]) if r['id']==payload.get('approval_id') and r['recipe_id']==rule['id'] and r['status']=='approved' and not r.get('used')),None)
    if approval:
        if not any(item.get('id')==approval['tool_id'] for _,item in entries(data)):raise ConflictError('Дополнительный инструмент больше не доступен')
        if any(entry['item'].get('id')==approval['tool_id'] for entry in allocations):raise ValidationError('Дополнительный инструмент нельзя одновременно потратить как материал')
        plan['difficulty']+=approval['modifier']
    return {**plan,'rule':rule,'target':target,'tool':tool,'target_id':target.get('id'),'slots':slot,'approval_id':approval['id'] if approval else None,
        'material_value':material_value,'material_allocations':allocations}


@atomic_operation
def operate(character_id,user_id,payload):
    character=db.session.get(LobbyCharacter,character_id)
    if not character:raise NotFoundError('Персонаж не найден')
    if not isinstance(payload,dict):raise ValidationError('Некорректная работа')
    action=payload.get('operation')
    if action=='unlock_slot':raise ValidationError('Открыть слот можно только у механика')
    is_gm=character.lobby.gm_id==user_id
    if action=='approve_tool':
        if not is_gm:raise PermissionDenied('Инструмент одобряет ГМ')
    else:CharacterService.check_access(character,user_id,edit=True)
    data=deepcopy(character.data or {});state=data.setdefault('crafting',{})
    if action=='quote':return character,quote(character,data,payload)
    if action=='quote_modification':return character,modification_plan(character,data,payload)
    if action=='quote_pda_modification':
        from app.services.pda_modifications import plan
        return character,plan(character,data,payload)
    if action=='quote_salvage':
        from app.services.salvage import plan
        return character,plan(data,payload)
    operation_id=payload.get('operation_id')
    if not isinstance(operation_id,str) or not 1<=len(operation_id)<=120:raise ValidationError('Нужен идентификатор операции')
    previous=next((r['result'] for r in state.get('receipts',[]) if r['id']==operation_id),None)
    if previous is not None:return character,{**deepcopy(previous),'replayed':True}
    if type(payload.get('character_revision')) is not int or payload['character_revision']!=character.revision:raise ConflictError()
    if action in {'start','read_book','modify','finish','salvage','install_pda_modification'}:
        from app.services.combat import CombatService
        if CombatService._character_condition(data)['state']!='active':raise ValidationError('Персонаж не может работать')
        if LocationCharacter.query.join(LocationCombatState,LocationCombatState.location_id==LocationCharacter.location_id).filter(
            LocationCharacter.character_id==character.id,LocationCombatState.status=='active').first():raise ValidationError('Работа с длительным сроком доступна вне боя')
    if action=='request_tool':
        from app.services.equipment_modifications import definition
        subject_id=payload.get('recipe_id')
        recipe=recipe_by_id(subject_id) if str(subject_id).startswith('recipe-') else definition(subject_id)
        path=payload.get('tool_path')
        tool=next((item for p,item in entries(data) if p==path),None) if path else None
        environment=any(_environment(c) for c in recipe.get('components',[]))
        if not tool and not environment:raise ValidationError('Выберите дополнительный инструмент')
        if tool:tool.setdefault('id',str(uuid4()))
        result={'id':str(uuid4()),'recipe_id':recipe['id'],'recipe_name':recipe.get('name') or recipe.get('description'),'status':'pending',
            'tool_id':tool.get('id') if tool else None,'tool_name':tool.get('name') if tool else None,'environment':environment}
        state.setdefault('tool_requests',[]).append(result)
        state['tool_requests']=state['tool_requests'][-50:]
    elif action=='approve_tool':
        pending=next((r for r in state.get('tool_requests',[]) if r['id']==payload.get('request_id')),None)
        if not pending or pending['status']!='pending':raise ConflictError('Заявка уже рассмотрена')
        modifier=payload.get('modifier')
        if type(payload.get('approved')) is not bool:raise ValidationError('Одобрите или отклоните инструмент')
        if type(modifier) is not int or (not -15<=modifier<=-5 if pending['tool_id'] else modifier!=0):raise ValidationError('Скидка инструмента: от −5 до −15')
        pending.update(status='approved' if payload.get('approved') is True else 'rejected',modifier=modifier)
        result=deepcopy(pending)
    elif action=='install_pda_modification':
        from app.services.pda_modifications import plan, record
        from app.services.equipment_modifications import installed
        if state.get('current'):raise ConflictError('Сначала завершите или отмените текущую работу')
        prepared=plan(character,data,payload)
        if prepared['missing']:raise ValidationError('; '.join(prepared['missing']))
        target=next(item for _,item in walk(data,True) if item.get('id')==prepared['target_id'])
        previous=[m['ruleId'] for m in installed(target)]
        if prepared['tool_path']:
            tool=next(item for path,item in entries(data) if path==prepared['tool_path'])
            tool['uses']=_available_uses(tool)-1
            tool.setdefault('attributes',{})['uses_remaining']=tool['uses']
        reserve=_escrow(data,prepared['allocations'])
        result={'id':str(uuid4()),'kind':'pda_modification','name':prepared['rule']['name'],
            'record':record(prepared['rule'],character.id),'target_id':prepared['target_id'],
            'previous':previous,
            'quote':{k:deepcopy(prepared[k]) for k in ('difficulty','automatic_success','failure_loss')},
            'escrow':reserve,'output_count':0,'started_at':now(character),'ready_at':now(character)+3600}
        state['current']=result
    elif action=='modify':
        from app.services.character_traits import background_is
        from app.services.equipment_modifications import make_record,installed
        if state.get('current'):raise ConflictError('Сначала завершите или отмените текущую работу')
        plan=modification_plan(character,data,payload);target=plan['target'];target.setdefault('id',str(uuid4()))
        tool=plan['tool'];available=tool.get('uses',tool.get('maxUses',(tool.get('attributes') or {}).get('uses',0)))
        tool['uses']=available-plan['tool_uses'];tool.setdefault('attributes',{})['uses_remaining']=tool['uses']
        paid_materials=_escrow(data,plan['material_allocations'])
        record=make_record(plan['rule']['id'],gunsmith=background_is(data,'Оружейник'),installer_id=character.id)
        result={'id':str(uuid4()),'kind':'modification','name':record['name'],'record':record,'target_id':target['id'],
            'replace_rule_id':payload.get('replace_rule_id'),'previous':[m['ruleId'] for m in installed(target)],
            'quote':{'difficulty':plan['difficulty'],'automatic_success':plan['automatic_success'],'failure_loss':{'random':0,'chosen':0,'all':False}},
            'escrow':[],'spent_materials':paid_materials,'material_value':plan['material_value'],'output_count':0,'started_at':now(character),'ready_at':now(character)+plan['duration_seconds']}
        state['current']=result
        if plan['approval_id']:next(r for r in state['tool_requests'] if r['id']==plan['approval_id'])['used']=True
    elif action=='salvage':
        from app.services.salvage import plan
        if state.get('current'):raise ConflictError('Сначала завершите или отмените текущую работу')
        prepared=plan(data,payload)
        reserve=_escrow(data,[{'component':0,'path':prepared['path'],'item':prepared['item'],'amount':prepared['quantity']}])
        result={'id':str(uuid4()),'kind':'salvage','salvage_kind':prepared['kind'],'name':'Разбор: '+prepared['name'],
            'quantity':prepared['quantity'],'bonus':prepared['bonus'],'modifications':prepared['modifications'],
            'quote':{'difficulty':0,'automatic_success':True,'failure_loss':{'random':0,'chosen':0,'all':False}},
            'escrow':reserve,'output_count':0,'started_at':now(character),'ready_at':now(character)+prepared['duration_seconds']}
        state['current']=result
    elif action=='read_book':
        from app.services.character_creation import _catalog
        from app.services.item_template_lookup import resolve_item_template
        if state.get('current'):raise ConflictError('Сначала завершите или отмените текущую работу')
        item=next((item for path,item in entries(data) if path==payload.get('path')),None)
        template=resolve_item_template(item,character.lobby_id) if item else None
        attrs=(template.attributes or {}) if template else {}
        if attrs.get('book_kind') not in {'skill','equipment'}:raise ValidationError('Выберите электронную книгу')
        from app.services.portable_lights import pda_ready
        if not pda_ready(data):raise ValidationError('Для электронной книги нужен включённый заряженный КПК')
        item.setdefault('id',str(uuid4()));path=payload.get('skill_path')
        if attrs['book_kind']=='skill':
            subject=str(attrs.get('book_subject','')).casefold()
            group='physical' if 'физическ' in subject else 'social' if 'социальн' in subject else 'other'
            if not isinstance(path,str) or path not in {group+'.'+key for key in _catalog()['skills'][group]}:raise ValidationError('Выберите навык из категории книги')
            assigned=state.setdefault('book_assignments',{}).get(item['id'])
            if assigned and assigned!=path:raise ValidationError('Этот экземпляр книги посвящён другому навыку')
            state['book_assignments'][item['id']]=path
        else:path=None
        result={'id':str(uuid4()),'kind':'book','name':template.name,'book_id':item['id'],
            'book_kind':attrs['book_kind'],'book_subject':attrs.get('book_subject'),'skill_path':path,
            'quote':{'difficulty':0,'automatic_success':True,'failure_loss':{'random':0,'chosen':0,'all':False}},
            'escrow':[],'output_count':0,'started_at':now(character),'ready_at':now(character)+int(attrs.get('reading_seconds',14400))}
        state['current']=result
    elif action=='start':
        if state.get('current'):raise ConflictError('Сначала завершите или отмените текущую работу')
        for _,item in entries(data):item.setdefault('id',str(uuid4()))
        plan=quote(character,data,payload)
        if plan['missing']:raise ValidationError('; '.join(plan['missing']))
        reserve=_escrow(data,plan['allocations'])
        result={'id':str(uuid4()),'kind':'recipe','recipe_id':plan['recipe']['id'],'name':plan['recipe']['name'],
            'quote':{k:deepcopy(plan[k]) for k in ('difficulty','automatic_success','failure_loss')},
            'escrow':reserve,'output_id':plan['output_id'],'output_count':plan['recipe']['output_count'],
            'output_uses':plan['output_uses'],'output_variant':plan['output_variant'],
            'ammo_template_id':plan['ammo_template_id'],
            'started_at':now(character),'ready_at':now(character)+plan['duration_seconds']}
        state['current']=result
        if plan['approval_id']:
            next(r for r in state['tool_requests'] if r['id']==plan['approval_id'])['used']=True
    elif action in {'finish','cancel'}:
        job=state.get('current')
        if not job or payload.get('job_id')!=job['id']:raise ConflictError('Работа уже завершена или изменена')
        if action=='finish' and now(character)<job['ready_at']:raise ValidationError('Время работы ещё не прошло')
        lost={};roll=None;success=bool(action=='finish' and job['kind']=='book')
        if success:
            if not any(item.get('id')==job['book_id'] for _,item in entries(data)):raise ConflictError('Книга больше не доступна')
            from app.services.portable_lights import pda_ready
            if not pda_ready(data):raise ConflictError('КПК выключен или разряжен')
            learned={key:job[key] for key in ('book_kind','book_subject','skill_path','name')}
            library=state.setdefault('library',[])
            if not any(e['book_kind']==learned['book_kind'] and e.get('skill_path')==learned['skill_path'] and e.get('book_subject')==learned['book_subject'] for e in library):library.append(learned)
        salvage_result=None
        if action=='finish' and job['kind']=='salvage':
            from app.services.salvage import resolve
            salvage_result=job.get('salvage_roll') or resolve(data,job)
            override=payload.get('salvage_band')
            if override is not None:
                if not is_gm:raise PermissionDenied('Строку таблицы с отсутствующим результатом выбирает ГМ')
                bands=catalog()['salvage'][job['salvage_kind']]
                if not salvage_result.get('awaiting_gm') or type(override) is not int or not 0<=override<len(bands) or not bands[override]['materials']:
                    raise ValidationError('Выберите заполненную строку таблицы')
                class SavedRoll:
                    def __init__(self,values):self.values=iter(values)
                    def randint(self,*_):return next(self.values)
                from app.services.character_traits import trait_count
                values=([1 if salvage_result['salvage_lost'] else 100] if trait_count(data,'Утилизатор') else [])+[salvage_result['roll']]
                salvage_result=resolve(data,job,rng=SavedRoll(values),band_override=override)
            if salvage_result.get('awaiting_gm'):
                job['salvage_roll']=salvage_result
                result={**salvage_result,'kind':'salvage','name':job['name'],'job_id':job['id']}
                state.setdefault('receipts',[]).append({'id':operation_id,'result':deepcopy(result)});state['receipts']=state['receipts'][-50:]
                character.data=data;flag_modified(character,'data')
                return character,result
            roll=salvage_result['roll'];success=True
            lost={index:entry['amount'] for index,entry in enumerate(job['escrow'])}
            for material in salvage_result['materials']:
                output=_template(material['name'])
                if not output:raise ConflictError('Шаблон компонента результата удалён')
                data.setdefault('inventory',{}).setdefault('pockets',[]).append(item_from_template(output,material['quantity']))
        if action=='finish' and job['kind'] not in {'book','salvage'}:
            loss=job['quote']['failure_loss'];choices=payload.get('chosen_loss',[])
            pool=[index for index,entry in enumerate(job['escrow']) for _ in range(entry['amount'])]
            count=0 if job['quote']['automatic_success'] or loss['all'] else min(loss['chosen'],len(pool))
            if not isinstance(choices,list) or len(choices)!=count or any(type(i) is not int or i not in pool for i in choices):raise ValidationError('Выберите компоненты, которые будут потеряны при неудаче')
            for index in choices:
                if index not in pool:raise ValidationError('Недостаточно выбранных компонентов')
                pool.remove(index);lost[index]=lost.get(index,0)+1
            outcome=resolve_creation(job['quote']);roll=outcome['roll'];success=outcome['success']
            if not success:
                if loss['all']:lost={index:entry['amount'] for index,entry in enumerate(job['escrow'])}
                else:
                    for index in random.SystemRandom().sample(pool,min(loss['random'],len(pool))):lost[index]=lost.get(index,0)+1
            else:lost={index:entry['amount'] for index,entry in enumerate(job['escrow'])}
        _return_materials(data,job['escrow'],lost)
        if success and job['kind']=='modification':
            from app.services.equipment_modifications import installed,install
            target=next((item for _,item in walk(data,True) if item.get('id')==job['target_id']),None)
            if not target or [m['ruleId'] for m in installed(target)]!=job['previous']:raise ConflictError('Предмет или его модификации изменились')
            install(target,job['record'],job['replace_rule_id'])
        if success and job['kind']=='pda_modification':
            from app.services.equipment_modifications import installed,install
            from app.services.portable_lights import definition
            target=next((item for _,item in walk(data,True) if item.get('id')==job['target_id']
                and definition(item)=='pda'),None)
            if not target or [m['ruleId'] for m in installed(target)]!=job['previous']:
                raise ConflictError('КПК или его модификации изменились')
            install(target,job['record'])
        if success and job['kind']=='recipe':
            output=db.session.get(ItemTemplate,job['output_id'])
            if not output:raise ConflictError('Шаблон результата удалён')
            if job['recipe_id']=='recipe-aa12':
                maximum=(output.attributes or {}).get('uses')
                if type(maximum) is not int or maximum<1:
                    raise ConflictError('Каталожное число порций воды изменилось')
                produced=item_from_template(output,ceil(job['output_count']/maximum))
                produced['uses']=job['output_count']%maximum or maximum
                produced['attributes']['uses_remaining']=produced['uses']
            else:produced=item_from_template(output,job['output_count'])
            if job.get('ammo_template_id'):
                variant=job.get('output_variant')
                produced['ammo_variant']=variant if variant!='base' else None
                produced['attributes']['ammo_variant']=produced['ammo_variant']
                if variant in {'bp','ep'}:produced['name']=f"{output.name} {'БП' if variant=='bp' else 'ЭП'}"
                elif variant in {'handmade','improved'}:
                    produced['name']=f"{output.name} {'кустарные' if variant=='handmade' else 'улучшенные'}"
                    produced['price']=floor((output.price or 0)*(.25 if variant=='handmade' else 1.5))
            if job['recipe_id'] in {'recipe-aa15', 'recipe-aa16'}:
                from app.services.cooked_meat import cooked_output
                source=next(entry['item'] for entry in job['escrow'] if entry['component']==0)
                cooked_output(source,job['recipe_id'],produced)
            if job.get('output_uses') is not None:
                if type(produced.get('maxUses')) is not int or not 0<job['output_uses']<produced['maxUses']:
                    raise ConflictError('Каталожный максимум зарядов инструмента изменился')
                produced['uses']=job['output_uses']
                produced['attributes']['uses_remaining']=job['output_uses']
            data.setdefault('inventory',{}).setdefault('pockets',[]).append(produced)
        result={'job_id':job['id'],'kind':job['kind'],'name':job['name'],'cancelled':action=='cancel','success':success,'roll':roll,
            'difficulty':job['quote']['difficulty'],'lost':lost,
            'lost_items':[{'name':entry['item']['name'],'amount':lost.get(index,0)} for index,entry in enumerate(job['escrow']) if lost.get(index,0) and not success],
            'output_count':job['output_count'] if success else 0}
        if salvage_result:result.update(salvage_result)
        state.pop('current');state.setdefault('history',[]).append(result);state['history']=state['history'][-30:]
    else:raise ValidationError('Неизвестное действие создания')
    state.setdefault('receipts',[]).append({'id':operation_id,'result':deepcopy(result)});state['receipts']=state['receipts'][-50:]
    from app.services.action_journal import append_work
    append_work(data,operation_id,action,result)
    character.data=data;flag_modified(character,'data')
    return character,result
