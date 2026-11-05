"""Серверные работы: резерв компонентов, игровое время, к100 и решения ГМа."""
from copy import deepcopy
from math import ceil
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


def preserve(current,incoming):
    if 'crafting' in (current or {}):incoming['crafting']=deepcopy(current['crafting'])
    else:incoming.pop('crafting',None)


def now(character):return ((character.lobby.game_day or 1)*1440+(character.lobby.game_time_minutes or 0))*60


def normalized(name):
    text=str(name or '').casefold().replace('ё','е').replace('металические','металлические')
    text=re.sub(r'\(\s*\d+\s*\)','',text)
    return re.sub(r'[^\w]+',' ',text).strip()


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
    templates=ItemTemplate.query.all();key=normalized(name)
    exact=next((t for t in templates if normalized(t.name)==key),None)
    if exact:return exact
    aliases={
        'анальгин':'Анальгин (Таблетка)',
        'пенициллин':'Пенициллин (Таблетка)',
        'ибупрофен':'Ибупрофен (Таблетка)',
        'хирург набор крест':'Хирургический набор "Крест"',
        'жаропонижающее мороз':'Жаропонижающее средство "Мороз"',
    }
    if key in aliases:
        return next((t for t in templates if normalized(t.name)==normalized(aliases[key])),None)
    return next((t for t in templates if t.category=='grenade' and normalized(t.name).removeprefix('граната ')==key),None)


def _environment(component):return normalized(component['name']).startswith('доступ к ')


def _allocate(data,recipe):
    from app.services.trade import _contains_individual_items
    candidates=entries(data);spent={};allocations=[];missing=[]
    for index,component in enumerate(recipe['components']):
        if _environment(component):continue
        names={normalized(name) for name in component['name'].split('/')}
        left=component['quantity']
        for path,item in candidates:
            from app.services.flamethrower import charges
            attrs=item.get('attributes') or {};whole=bool(component.get('whole_tank') or component.get('whole_air'))
            matches=(attrs.get('flamethrower_tank')==component.get('whole_tank') and charges(item)==0 if component.get('whole_tank')
                else bool(attrs.get('flamethrower_air')) if component.get('whole_air') else normalized(item['name']) in names)
            if not matches or _contains_individual_items(item):continue
            key=tuple(path);available=(item.get('quantity',1) if whole else _available_uses(item) if item.get('quantity',1)>0 else 0)-spent.get(key,0)
            take=min(left,available)
            if take>0:
                allocations.append({'component':index,'path':path,'amount':take,'item':deepcopy(item),'whole':whole})
                spent[key]=spent.get(key,0)+take;left-=take
            if not left:break
        if left:missing.append(f"{component['name']}: не хватает {left}")
    return allocations,missing


def _tools(character,data,recipe,option):
    options=recipe['difficulty_options'];count=max(1,len(options))
    if type(option) is not int or not 0<=option<count:raise ValidationError('Выберите инструмент по порядку из рецепта')
    raw=recipe['tools_text']
    if normalized(raw)=='обязателен' and recipe['group']=='Электроника':raw='Станок Электрика'
    if normalized(raw) in {'да','нужна'} and recipe['group']=='Ловушки':raw='Печь'
    required=[part.strip() for part in raw.split(',') if part.strip()]
    names={normalized(item['name']) for _,item in entries(data)}
    # Станки/костёр на сцене доступны только рядом и на текущем этаже.
    placement=LocationCharacter.query.filter_by(character_id=character.id).order_by(LocationCharacter.last_action.desc().nullslast(),LocationCharacter.id.desc()).first()
    if placement:
        for obj in LocationObject.query.filter_by(location_id=placement.location_id).all():
            props=obj.properties or {}
            if (props.get('level',0) or 0)!=(placement.floor_level or 0):continue
            if ((obj.tile_x-placement.pos_x)**2+(obj.tile_y-placement.pos_y)**2)**.5>1.5:continue
            names.add(normalized(obj.name))
            if obj.type in {'campfire','fire_barrel'} and props.get('is_lit',True):names.add('костер')
    missing=[]
    for part in required:
        if normalized(part) in {'не нужен','не нужна','не обязателен','не обязательна','не обязателен станок',''} or part in {'---','-'}:continue
        choices=part.split('/')
        chosen=choices[option] if len(choices)==count and count>1 else part
        if not any(normalized(choice) in names for choice in (chosen.split('/') if count==1 else [chosen])):missing.append(chosen)
    return missing


def quote(character,data,payload):
    from app.services.combat import CombatService
    recipe=recipe_by_id(payload.get('recipe_id'));option=payload.get('tool_option',0)
    if recipe.get('requires_tool_crafting'):
        from app.services.character_traits import trait_count,background_is
        if not trait_count(data,'Мастер на все руки') and not background_is(data,'Полевой ученый'):raise ValidationError('Изготовление инструментов требует «Мастера на все руки» или предыстории «Полевой учёный»')
    missing_tools=_tools(character,data,recipe,option)
    allocations,missing=_allocate(data,recipe)
    state=data.get('crafting') or {};approval=next((r for r in state.get('tool_requests',[]) if r['id']==payload.get('approval_id')
        and r['recipe_id']==recipe['id'] and r['status']=='approved' and not r.get('used')),None)
    if approval and approval.get('tool_id'):
        if not any(item.get('id')==approval['tool_id'] for _,item in entries(data)):raise ConflictError('Одобренный инструмент больше не доступен')
    environment=[c['name'] for c in recipe['components'] if _environment(c)]
    if environment and not (approval and approval.get('environment')):missing.append('Нужно подтверждение условий ГМом: '+', '.join(environment))
    level=CombatService._skill_value(data,'skills.'+recipe['skill'])
    difficulty=recipe['difficulty_options'][option] if recipe['difficulty_options'] else 0
    result=({'difficulty':0,'automatic_success':True,'failure_loss':{'random':0,'chosen':0,'all':False}}
        if recipe.get('maintenance') else creation_quote(level,difficulty,base_automatic=recipe['automatic'],difficulty_modifier=approval.get('modifier',0) if approval else 0))
    output=_template(recipe['output_name'])
    if not output:missing.append('В каталоге нет шаблона результата: '+recipe['output_name'])
    if missing_tools:missing.append('Нужны: '+', '.join(missing_tools))
    if recipe.get('duration_seconds') is None:missing.append('В рецепте не задан срок работы')
    return {**result,'recipe':recipe,'allocations':allocations,'missing':missing,'output_id':output.id if output else None,
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
        amount=allocation['amount'];piece=deepcopy(item)
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
        item=deepcopy(entry['item']);maximum=entry['maximum'];item['quantity']=ceil(amount/maximum)
        if maximum>1:
            item['uses']=amount%maximum or maximum
            item.setdefault('attributes',{})['uses_remaining']=item['uses']
        destination.append(item)


def snapshot(character,user_id):
    from app.services.character_creation import _catalog
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
    return {'catalog':rules,'state':state,'inventory':[{'path':path,'item':item,'uses':_available_uses(item),'salvage_kind':kind_of(item),'salvage_tool_bonus':tool_bonus(item)} for path,item in entries(data)],
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


def modification_plan(character,data,payload):
    from app.services.crafting_rules import modification_quote
    from app.services.equipment_modifications import definition,supported,installed,slots
    from app.services.combat import CombatService
    rule=definition(payload.get('rule_id'))
    if not supported(rule):raise ValidationError('Эта модификация ожидает подключения своего специального механизма')
    target=next((entry['item'] for entry in modification_targets(data) if entry['path']==payload.get('target_path')),None)
    if not target:raise ValidationError('Выберите предмет для модификации')
    slot=slots(target)
    if slot['family']!=rule['family']:raise ValidationError('Модификация предназначена для другого вида снаряжения')
    if rule.get('scientist_only'):raise ValidationError('Модификации экзоскелета устанавливаются только у учёных')
    existing=installed(target);replacing=next((m for m in existing if m['ruleId']==payload.get('replace_rule_id')),None)
    if payload.get('replace_rule_id') and (not replacing or replacing['level']!=rule['level']):raise ValidationError('Заменить можно модификацию того же уровня')
    others=[m for m in existing if m is not replacing]
    if any(m['ruleId']==rule['id'] for m in others):raise ValidationError('Модификация уже установлена')
    if len(others)>=slot['available']:raise ValidationError('Нет открытого слота модификации')
    if rule['kind']=='weapon' and any(m['level']==rule['level'] for m in others):raise ValidationError('У оружия может быть одна модификация каждого уровня')
    proposed=[*others,rule]
    if rule['kind']!='weapon':
        for candidate in proposed:
            if candidate['level']>1 and any(not any(m.get('branch')==candidate.get('branch') and m['level']==level for m in proposed) for level in range(1,candidate['level'])):
                raise ValidationError('Модификации ветки устанавливаются последовательно')
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
    is_gm=character.lobby.gm_id==user_id
    if action=='approve_tool':
        if not is_gm:raise PermissionDenied('Инструмент одобряет ГМ')
    else:CharacterService.check_access(character,user_id,edit=True)
    data=deepcopy(character.data or {});state=data.setdefault('crafting',{})
    if action=='quote':return character,quote(character,data,payload)
    if action=='quote_modification':return character,modification_plan(character,data,payload)
    if action=='quote_salvage':
        from app.services.salvage import plan
        return character,plan(data,payload)
    operation_id=payload.get('operation_id')
    if not isinstance(operation_id,str) or not 1<=len(operation_id)<=120:raise ValidationError('Нужен идентификатор операции')
    previous=next((r['result'] for r in state.get('receipts',[]) if r['id']==operation_id),None)
    if previous is not None:return character,{**deepcopy(previous),'replayed':True}
    if type(payload.get('character_revision')) is not int or payload['character_revision']!=character.revision:raise ConflictError()
    if action in {'start','read_book','modify','unlock_slot','finish','salvage'}:
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
    elif action=='unlock_slot':
        from app.services.trade import balance
        from app.services.equipment_modifications import slots
        target=next((entry['item'] for entry in modification_targets(data) if entry['path']==payload.get('target_path')),None)
        if not target:raise ValidationError('Предмет не найден')
        status=slots(target)
        if status['available']>=status['maximum']:raise ValidationError('Все слоты уже открыты')
        if balance(data)<2500:raise ValidationError('Не хватает 2500 рублей')
        target.setdefault('id',str(uuid4()));target['unlockedModificationSlots']=int(target.get('unlockedModificationSlots') or 0)+1
        data['inventory']['money']=balance(data)-2500
        result={'slots':slots(target),'price':2500}
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
        if not any(normalized(item['name'])=='кпк' for _,item in entries(data)):raise ValidationError('Для электронной книги нужен КПК')
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
            if not any(normalized(item['name'])=='кпк' for _,item in entries(data)):raise ConflictError('КПК больше не доступен')
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
        if success and job['kind']=='recipe':
            output=db.session.get(ItemTemplate,job['output_id'])
            if not output:raise ConflictError('Шаблон результата удалён')
            data.setdefault('inventory',{}).setdefault('pockets',[]).append(item_from_template(output,job['output_count']))
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
