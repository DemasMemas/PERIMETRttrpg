"""Порядок создания, все записи каталога, бюджет и сохранение результата."""
from copy import deepcopy
import random
import pytest

from app.services.character_creation import (catalog,build_character,builtin_templates,
    generate_zone_history,monthly_income,preserve_creation,seed_templates)
from app.services.exceptions import ValidationError
from app.services.character_traits import passive_profile,action_cost
from test_grenade_rules import grenade_game
from test_consumable_use import pair


def plan(name='Пустышка',**changes):
    if name=='СБУшник':name='СБшник'
    background=next(b for b in catalog()['backgrounds'] if b['name']==name)
    preferred=['physical.awareness','physical.melee','social.barter','social.persuasion','social.deception','social.intimidation','other.engineering','other.stealth','other.tactics','other.survival','physical.strength','physical.will','physical.shooting','social.charisma','other.medicine','physical.agility']
    selected=list(changes.pop('specializations',[]))
    required=10+sum(e['amount'] for e in background['entries'] if e['type']=='specializations')
    for path in preferred:
        if len(selected)>=required:break
        if path not in selected:selected.append(path)
    result={'backgroundId':background['id'],'specializations':selected}
    result.update(changes);return result


def trait(name,**choices):
    return {'id':next(t['id'] for t in catalog()['traits'] if t['name']==name),**choices}


def test_complete_source_catalog_and_stable_ids():
    c=catalog()
    assert len(c['backgrounds'])==56 and len(c['traits'])==90
    assert len({t['id'] for t in builtin_templates()})==146
    assert {v['source']['cell'] for v in c['traits']}=={f'{col}{r}' for r in range(2,71,4) for col in 'BCDEF'}
    assert all(t['effect'] and isinstance(t['cost'],int) for t in c['traits'])
    c['backgrounds'].clear();assert len(catalog()['backgrounds'])==56


def test_cleaned_rule_linebreaks_do_not_block_player_save_or_allow_changed_rules():
    from copy import deepcopy
    from app.services.character_creation import preserve_creation
    current=build_character('Текст',plan(traits=[trait('Быстрые руки')]))
    current['basic']['background']['minuses']='Харизма -20,\nесли видят лицо'
    current['features']['specialTraits'][0]['effect']='Бонус\nпри условии'
    incoming=deepcopy(current)
    incoming['basic']['background']['minuses']='Харизма -20, если видят лицо'
    incoming['features']['specialTraits'][0]['effect']='Бонус при условии'
    preserve_creation(current,incoming,is_gm=False)
    assert incoming['basic']['background']==current['basic']['background']
    assert incoming['features']['specialTraits']==current['features']['specialTraits']
    incoming['basic']['background']['minuses']='Харизма -10, если видят лицо'
    with pytest.raises(ValidationError,match='доступно ГМу'):preserve_creation(current,incoming,is_gm=False)


def test_all_specializations_are_required_only_at_final_creation():
    partial=plan();partial['specializations']=[]
    assert build_character('Имя',partial,finalize=False)['skills']['specializations']==11
    with pytest.raises(ValidationError,match='все 11'):build_character('Имя',partial)


def test_completed_creation_grants_one_free_pda_without_changing_budget(app):
    from app.services.character_starting_purchase import grant_starting_pda
    draft = build_character('Черновик', plan(), finalize=False)
    assert not any(item['name'] == 'КПК' for item in draft['inventory']['pockets'])
    data = build_character('Имя', plan())
    pda = [item for item in data['inventory']['pockets'] if item['name'] == 'КПК']
    assert len(pda) == 1
    assert (pda[0]['price'], pda[0]['weight'], pda[0]['volume'], pda[0]['quantity']) == (1500, .25, .5, 1)
    assert data['inventory']['money'] == data['creation']['startingPurchase']['remaining']
    assert data['creation']['startingPdaGranted']
    assert not grant_starting_pda(data)
    assert len(data['inventory']['pockets']) == 1


def test_skill_and_equipment_books_keep_their_kind_in_starting_purchase(app):
    from app.extensions import db
    from app.models.templates import ItemTemplate
    from app.services.character_starting_purchase import catalog as purchase_catalog

    skill=ItemTemplate(name='Книга навыка',category='misc',subcategory='Книги',price=100,
        attributes={'book_kind':'skill','book_subject':'Физические'})
    equipment=ItemTemplate(name='Книга снаряжения',category='misc',subcategory='Книги',price=100,
        attributes={'book_kind':'equipment','book_subject':'Броня'})
    db.session.add_all([skill,equipment]);db.session.flush()
    books={entry['name']:entry for entry in purchase_catalog() if entry['id'] in {skill.id,equipment.id}}
    assert books['Книга навыка']['attributes']['book_kind']=='skill'
    assert books['Книга снаряжения']['attributes']['book_kind']=='equipment'


def test_background_capital_is_separate_from_purchase_and_granted_once(creation_case):
    from app.services.character import CharacterService
    from app.services.character_starting_purchase import grant_world_entry
    c=creation_case
    actor=CharacterService.create_character(c['lobby'].id,c['gm'].id,'Богатый',{'creationRequest':plan('Богат деньгами',cashPoints=2)})
    assert actor.data['inventory']['money']==60400
    assert actor.data['creation']['entryMoney']==50000
    assert actor.data['creation']['creationMoneyGranted']
    assert grant_world_entry(actor)
    assert actor.data['inventory']['money']==60400
    assert not grant_world_entry(actor)
    assert actor.data['inventory']['money']==60400


def test_history_money_is_granted_on_create_even_before_teacher_choices(creation_case,monkeypatch):
    from copy import deepcopy
    from app.services.character import CharacterService
    from app.services.character_history import settle_history
    from app.services.character_starting_purchase import grant_creation_money,grant_world_entry
    history={'months':1,'settled':False,'events':[{'month':1,'incomeOptions':[2000],'teacher':{'group':'physical','xp':2}}]}
    monkeypatch.setattr('app.services.character_creation.generate_zone_history',lambda data:deepcopy(history))
    c=creation_case
    actor=CharacterService.create_character(c['lobby'].id,c['gm'].id,'Доход сразу',{'creationRequest':plan(zoneHistory=True)})
    assert actor.data['inventory']['money']==12000 and actor.data['creation']['zoneHistory']['cashTotal']==2000
    assert not actor.data['creation']['zoneHistory']['settled']
    with pytest.raises(ValidationError,match='историю'):grant_world_entry(actor)
    assert not grant_creation_money(actor.data)
    # Покупки после создания не должны менять уже рассчитанные последствия истории.
    actor.data['inventory']['money']=100
    actor.data=settle_history(actor.data,{'1':{'skill':'physical.agility'}})
    assert actor.data['inventory']['money']==100 and actor.data['creation']['zoneHistory']['cashTotal']==2000
    assert grant_world_entry(actor) and actor.data['inventory']['money']==100
    assert not grant_world_entry(actor)


def test_starting_purchase_uses_catalog_prices_and_rejects_restricted_templates(app):
    from app.extensions import db
    from app.models.templates import ItemTemplate
    from app.services.character_starting_purchase import eligible
    gun=ItemTemplate(name='Пистолет 1',category='weapon',item_class='1',price=1000,attributes={'range':20,'damage':30})
    forbidden=ItemTemplate(name='Пистолет 2',category='weapon',item_class='2',price=1)
    special=ItemTemplate(name='9мм БП',category='ammo',price=1,attributes={'ammo_variant':'bp'})
    food=ItemTemplate(name='Еда',category='consumable',price=100,attributes={'section':'Еда'})
    knife=ItemTemplate(name='Нож',category='melee_weapon',price=300,attributes={'damage':10})
    db.session.add_all([gun,forbidden,special,food,knife]);db.session.flush()
    assert eligible(gun) and eligible(food) and eligible(knife) and not eligible(forbidden) and not eligible(special)
    data=build_character('Имя',plan('Бедный',purchases=[{'templateId':gun.id,'quantity':2},{'templateId':food.id,'quantity':3}]))
    assert data['inventory']['money']==2700
    assert len(data['weapons'])==2 and data['weapons'][0]['id']!=data['weapons'][1]['id']
    assert data['weapons'][0]['range']==20 and data['inventory']['pockets'][0]['quantity']==3
    assert data['inventory']['backpack']==[]
    for selection in ([{'templateId':forbidden.id,'quantity':1}],[{'templateId':special.id,'quantity':1}],
                      [{'templateId':gun.id,'quantity':6}],[{'templateId':gun.id,'quantity':1,'price':0}]):
        with pytest.raises(ValidationError):build_character('Имя',plan('Бедный',purchases=selection))


def test_starting_purchase_grants_catalog_filters_at_their_price(app):
    from app.extensions import db
    from app.models.templates import ItemTemplate
    from app.services.character_starting_purchase import catalog, eligible
    from app.services.equipment_catalog import load_catalog
    from app.services.gas_mask_filters import filter_charges, is_gas_mask_filter

    names = {'Фильтр для противогаза', 'Кустарный фильтр для противогаза'}
    filters = [ItemTemplate(**entry) for entry in load_catalog() if entry['name'] in names]
    db.session.add_all(filters)
    db.session.flush()
    assert {entry['name']:entry['price'] for entry in catalog() if entry['category']=='gas_mask_module'} == {
        'Фильтр для противогаза':300, 'Кустарный фильтр для противогаза':200}
    assert not eligible(ItemTemplate(name='Не фильтр', category='gas_mask_module', subcategory='visor'))
    assert not eligible(ItemTemplate(name='Не продаётся', category='gas_mask_module',
        subcategory='filter', attributes={'not_for_sale':True}))
    data = build_character('Имя', plan('Бедный', purchases=[{'templateId':f.id,'quantity':1} for f in filters]))
    assert data['inventory']['money'] == 4500
    assert {item['name']:filter_charges(item) for item in data['inventory']['pockets'] if is_gas_mask_filter(item)} == {
        'Фильтр для противогаза':20, 'Кустарный фильтр для противогаза':10}


def test_starting_purchase_packs_fuel_into_individual_canisters(app):
    from app.extensions import db
    from app.models.templates import ItemTemplate
    from app.services.character_starting_purchase import apply_purchase
    fuel=ItemTemplate(name='Топливо(1 Литр)',category='misc',price=300,volume=1)
    vessel=ItemTemplate(name='Канистра(Пустая)(Объем 15 литров)(45 использований воды)',
        category='misc',price=1500,volume=20,attributes={'liquid_capacity_liters':15})
    db.session.add_all([fuel,vessel]);db.session.flush()
    data={'inventory':{'pockets':[],'backpack':[]},'creation':{}}
    apply_purchase(data,[{'templateId':fuel.id,'quantity':16},{'templateId':vessel.id,'quantity':2}],10000)
    canisters=data['inventory']['pockets']
    assert len(canisters)==2 and len({item['id'] for item in canisters})==2
    assert [sum(liquid['quantity'] for liquid in item['contents']) for item in canisters]==[15,1]
    assert all(item['fuelUsed'] and item['isContainer'] for item in canisters)
    with pytest.raises(ValidationError,match='канистры'):
        apply_purchase({'inventory':{'pockets':[]},'creation':{}},
            [{'templateId':fuel.id,'quantity':1}],10000)


def test_reused_creation_plan_creates_independent_items_and_rewards(creation_case):
    from app.extensions import db
    from app.models.templates import ItemTemplate
    from app.services.character import CharacterService
    from app.services.character_starting_purchase import grant_creation_money
    gun=ItemTemplate(name='Пистолет для шаблона',category='weapon',item_class='1',price=1000,attributes={'damage':30})
    db.session.add(gun);db.session.flush()
    selected=plan('Богат деньгами',traits=[trait('Быстрые руки'),trait('Быстрые руки')],
                  purchases=[{'templateId':gun.id,'quantity':1}])
    original=deepcopy(selected);c=creation_case
    first=CharacterService.create_character(c['lobby'].id,c['gm'].id,'Первый по шаблону',{'creationRequest':selected})
    second=CharacterService.create_character(c['lobby'].id,c['gm'].id,'Второй по шаблону',{'creationRequest':selected})
    assert selected==original
    assert first.data['skills']==second.data['skills']
    assert first.data['inventory']['money']==second.data['inventory']['money']==59000
    assert len(first.data['features']['specialTraits'])==len(second.data['features']['specialTraits'])==2
    assert first.data['weapons'][0]['id']!=second.data['weapons'][0]['id']
    assert not grant_creation_money(first.data) and not grant_creation_money(second.data)
    first_copy=deepcopy(first.data);second_before=deepcopy(second.data)
    first_copy['weapons'][0]['ammo']=0
    first.data=first_copy
    db.session.commit();db.session.refresh(second)
    assert second.data==second_before


def test_third_interested_multiplier_and_history_teacher_do_not_multiply():
    from app.services.character_development import add_skill_experience
    data=build_character('Имя',plan(traits=[trait('Заинтересованный',skill='physical.agility')]*3))
    add_skill_experience(data,'physical.agility',1)
    assert data['skills']['physical']['agility']['base']==8
    add_skill_experience(data,'physical.agility',1,apply_multiplier=False)
    assert data['skills']['physical']['agility']['base']==9


def test_history_traits_are_extra_slots_and_negative_income_cannot_make_debt():
    from app.services.character_history import settle_history
    data=build_character('Имя',plan(traits=[trait('Быстрые руки')]*2+[trait('Заинтересованный',skill='physical.agility')]))
    data['creation']['zoneHistory']={'months':3,'settled':False,'events':[
        {'month':1,'incomeOptions':[-20000],'teacher':{'group':'physical','xp':2}},
        {'month':2,'incomeOptions':[1000],'hardening':{'limit':5}},
        {'month':3,'incomeOptions':[1000],'hardening':{'limit':3}}]}
    resolved=settle_history(data,{'1':{'skill':'physical.agility'},'2':{'trait':trait('Пироманьяк')},'3':{'decline':True}})
    assert data['creation']['zoneHistory']['settled'] is False
    assert resolved['skills']['physical']['agility']['base']==7
    assert len(resolved['features']['specialTraits'])==4
    assert resolved['skills']['skillPoints']==data['skills']['skillPoints']+1
    assert resolved['creation']['zoneHistory']['cashTotal']==-8000
    from app.services.character_creation import validate_trait_selection
    validate_trait_selection({},resolved)
    assert settle_history(resolved,{})==resolved


def monthly_history():
    return {'months':3,'settled':False,'presentationRequired':True,'presentationComplete':False,'confirmedThrough':0,'events':[
        {'month':1,'title':'Первый месяц','details':['Привыкание к Зоне'],'incomeOptions':[1000],'addiction':'Сигареты'},
        {'month':2,'title':'Учитель','details':[],'incomeOptions':[1000],'teacher':{'group':'physical','xp':2}},
        {'month':3,'title':'Закалка','details':[],'incomeOptions':[1000],'hardening':{'limit':5}}]}


def test_monthly_history_applies_choices_once_in_order_and_keeps_money():
    from copy import deepcopy
    from app.services.character_history import confirm_history_month
    from app.services.character_starting_purchase import grant_creation_money
    from app.services.exceptions import ConflictError
    data=build_character('Путь',plan(traits=[trait('Быстрые руки')]*2+[trait('Заинтересованный',skill='physical.agility')]))
    data['creation']['zoneHistory']=monthly_history();grant_creation_money(data)
    assert data['inventory']['money']==13000
    with pytest.raises(ValidationError,match='по порядку'):confirm_history_month(data,2,{'skill':'physical.agility'})
    first=confirm_history_month(data,1,{})
    assert first['creation']['zoneHistory']['confirmedThrough']==1 and not first['creation']['zoneHistory']['settled']
    assert first['health']['addictions']['records']['nicotine']['active']
    assert confirm_history_month(first,1,{})==first and not data['creation']['zoneHistory']['events'][0].get('confirmed')
    with pytest.raises(ValidationError,match='категории'):confirm_history_month(first,2,{'skill':'social.persuasion'})
    second=confirm_history_month(first,2,{'skill':'physical.agility'})
    assert second['skills']['physical']['agility']!=first['skills']['physical']['agility']
    choice={'trait':trait('Пироманьяк')}
    final=confirm_history_month(second,3,choice)
    assert len(final['features']['specialTraits'])==4
    assert final['creation']['zoneHistory']['presentationComplete'] and final['creation']['zoneHistory']['settled']
    assert final['inventory']['money']==13000
    assert confirm_history_month(final,3,choice)==final
    with pytest.raises(ConflictError,match='другим решением'):confirm_history_month(final,3,{'decline':True})
    from app.services.character_creation import validate_trait_selection
    validate_trait_selection({},final)


def test_monthly_history_api_saves_progress_and_denies_other_users(creation_case,client,monkeypatch):
    from copy import deepcopy
    from app.services.character import CharacterService
    c=creation_case
    monkeypatch.setattr('app.services.character_creation.generate_zone_history',lambda data:deepcopy(monthly_history()))
    actor=CharacterService.create_character(c['lobby'].id,c['player'].id,'Помесячно',{'creationRequest':plan(zoneHistory=True)})
    url=f'/lobbies/{c["lobby"].id}/characters/{actor.id}/creation-history'
    assert client.patch(url,json={'month':1,'choice':{}},headers=c['headers'][c['outsider'].id]).status_code==403
    headers=c['headers'][c['player'].id]
    response=client.patch(url,json={'month':1,'choice':{}},headers=headers)
    assert response.status_code==200
    assert response.json['data']['creation']['zoneHistory']['confirmedThrough']==1
    saved=client.get(f'/lobbies/characters/{actor.id}',headers=headers).json['data']
    assert saved['creation']['zoneHistory']['events'][0]['confirmed']
    assert client.patch(url,json={'month':3,'choice':{'decline':True}},headers=headers).status_code==400
    assert client.patch(url,json={'month':2,'choice':{'skill':'physical.agility'}},headers=headers).status_code==200
    final=client.patch(url,json={'month':3,'choice':{'decline':True}},headers=headers)
    assert final.status_code==200 and final.json['data']['creation']['zoneHistory']['settled']
    assert final.json['data']['inventory']['money']==13000


def test_starting_debt_spends_available_money_once_and_preserves_the_remainder():
    from app.services.character_starting_purchase import grant_creation_money

    data=build_character('Должник',plan())
    data['creation']['zoneHistory']={'events':[
        {'month':1,'incomeOptions':[2500],'debt':{'amount':10000,'creditor':'Бандиты'}},
        {'month':2,'incomeOptions':[2500],'debt':{'amount':20000,'creditor':'Наёмники'}},
    ]}
    assert grant_creation_money(data)
    assert data['inventory']['money']==0
    debts=[event['debt'] for event in data['creation']['zoneHistory']['events']]
    assert [(debt['paidAtCreation'],debt['remaining']) for debt in debts]==[(10000,0),(5000,15000)]
    assert not grant_creation_money(data)


def test_history_debt_uses_world_creditor_and_only_gm_can_repay(creation_case,client,monkeypatch):
    from app.extensions import db
    from app.models import WorldFactionConfig
    from app.services.character import CharacterService

    class RolledDebt:
        values=iter([1,3,4,2])

        def randint(self,low,high):
            value=next(self.values)
            assert low<=value<=high
            return value

    c=creation_case
    lobby_id=c['lobby'].id
    group_id='creditor-test'
    db.session.add(WorldFactionConfig(lobby_id=lobby_id,groups=[{'id':group_id,'name':'Кредиторы','factions':[]}]))
    db.session.commit()
    monkeypatch.setattr('app.services.character_creation.generate_zone_history',
        lambda data:generate_zone_history(data,RolledDebt()))
    actor=CharacterService.create_character(lobby_id,c['player'].id,'Долг',{'creationRequest':plan(zoneHistory=True)})
    owner,gm=(c['headers'][c[role].id] for role in ('player','gm'))
    rules=client.get(f'/lobbies/{lobby_id}/character-creation',headers=owner)
    assert rules.json['debtCreditors']==[{'id':group_id,'name':'Кредиторы'}]
    url=f'/lobbies/{lobby_id}/characters/{actor.id}/creation-history'
    assert actor.data['inventory']['money']==0
    assert client.patch(url,headers=owner,json={'month':1,'choice':{'creditor':'other-world'}}).status_code==400
    result=client.patch(url,headers=owner,json={'month':1,'choice':{'creditor':group_id}})
    assert result.status_code==200,result.json
    debt=result.json['data']['creation']['zoneHistory']['events'][0]['debt']
    assert debt['creditor']=='Кредиторы' and debt['amount']==20000
    assert debt['paidAtCreation']+debt['remaining']==20000 and debt['remaining']>1000
    payload={'month':1,'amount':1000,'character_revision':actor.revision}
    assert client.post(url+'/debt',headers=owner,json=payload).status_code==403
    assert client.post(url+'/debt',headers=gm,json=payload).status_code==400
    data=deepcopy(actor.data)
    data['inventory']['money']=debt['remaining']
    actor.data=data
    db.session.commit()
    payload['character_revision']=actor.revision
    result=client.post(url+'/debt',headers=gm,json=payload)
    assert result.status_code==200,result.json
    assert result.json['data']['inventory']['money']==debt['remaining']-1000
    assert result.json['data']['creation']['zoneHistory']['events'][0]['debt']['remaining']==debt['remaining']-1000
    assert client.post(url+'/debt',headers=gm,json=payload).status_code==409
    result=client.post(url+'/debt',headers=gm,json={**payload,'amount':debt['remaining']-1000,
        'character_revision':actor.revision})
    assert result.status_code==200 and result.json['data']['inventory']['money']==0
    assert result.json['data']['creation']['zoneHistory']['events'][0]['debt']['remaining']==0


@pytest.mark.parametrize('background',catalog()['backgrounds'],ids=lambda b:b['name'])
def test_every_background_has_valid_creation_and_full_source_text(background):
    p=plan(background['name'],backgroundWeapon='pistols',favoredMutant='Кабан')
    if not background['available']:
        with pytest.raises(ValidationError):build_character('Имя',p)
        return
    data=build_character('Имя',p)
    assert data['basic']['background']['pluses']==background['pluses']
    assert data['basic']['background']['minuses']==background['minuses']
    assert min(s['base'] for g in ('physical','social','other') for s in data['skills'][g].values())>=1


@pytest.mark.parametrize('entry',catalog()['traits'],ids=lambda t:t['name'])
def test_every_trait_is_selectable_and_preserves_description(entry):
    choices={}
    if entry['name'] in {'Специалист','Заинтересованный'}:choices['skill']='physical.agility'
    if entry['name'] in {'Курс знакомства с оружием','Слияние с оружием'}:choices['weaponType']='pistols'
    if entry['name']=='Патронщик':choices['caliber']='12*70'
    if entry['name']=='Шапочка из фольги':choices['idea']='Зона разумна'
    data=build_character('Имя',plan(traits=[{'id':entry['id'],**choices}]))
    assert data['features']['specialTraits'][0]['effect']==entry['effect']
    assert data['skills']['skillPoints']==38-entry['cost']


def test_creation_order_and_all_negative_lines():
    data=build_character('Гора',plan('Гора',specializations=['physical.strength'],raises={'physical.agility':1},lowers={'physical.strength':1},finalRaises={'physical.agility':1}))
    stages={row['stage']:row for row in data['creation']['ledger']}
    assert stages['background_positive']['levels']['physical.strength']==9
    assert stages['specializations']['levels']['physical.strength']==12
    assert [row['stage'] for row in data['creation']['ledger']][:2]==['background_positive','specializations']
    assert stages['lowers']['levels']['physical.strength']==9
    assert stages['background_negative']['levels']['physical.agility']==4
    assert data['skills']['physical']['agility']['base']==5
    assert data['health']['max']==840 and data['health']['zones']['leftArm']['max']==125
    assert data['skills']['specialized']['shotguns']['level']=='familiar'


def test_target_cost_12_to_13_and_final_raise_budget():
    data=build_character('Имя',plan(raises={'physical.agility':7},finalRaises={'physical.agility':1}))
    assert data['skills']['physical']['agility']['base']==13
    assert data['skills']['skillPoints']==38-6-2-3


def test_group_and_all_skills_modifiers():
    helper=build_character('Имя',plan('Помощник'))
    assert helper['skills']['physical']['strength']['base']==9
    assert helper['skills']['specializations']==0
    science=build_character('Имя',plan('Полевой Ученый'))
    assert science['skills']['other']['medicine']['base']==7
    assert science['skills']['other']['engineering']['base']==10
    agent=build_character('Имя',plan('СБУшник',backgroundWeapon='pistols'))
    assert agent['skills']['social']['charisma']['base']==4
    assert agent['skills']['social']['barter']['base']==7


@pytest.mark.parametrize('changes',[
    {'traits':[trait('Амбидекстр')]*2}, {'traits':[trait('Ползун')]*4},
    {'lowers':{'physical.strength':3}}, {'lowers':{p:1 for p in ['physical.strength','physical.agility','physical.will','physical.shooting','physical.melee','physical.awareness']}},
    {'specializations':['physical.agility']*2},{'cashPoints':39}, {'raises':{'unknown':1}},
    {'traits':[trait('Специалист',skill='physical.agility')],'specializations':['physical.agility']},
    {'traits':[trait('Маленький рост')],'lowers':{'social.charisma':1}},
    {'cashPoints':1.5},{'cashPoints':True},{'raises':{'physical.agility':100}},
])
def test_invalid_distribution_rejected(changes):
    with pytest.raises(ValidationError):build_character('Имя',plan(**changes))


def test_traits_duplicates_and_cash_and_addictions():
    data=build_character('Имя',plan(traits=[trait('Узкие карманы')]*2+[trait('Курящий')],cashPoints=10))
    assert data['skills']['skillPoints']==38+10+3-10
    assert data['inventory']['money']==12000
    assert passive_profile(data)['pocket_cells']==-10
    assert data['health']['addictions']['records']['nicotine']['active']


def test_preserve_creation_does_not_apply_bonuses_again():
    data=build_character('Имя',plan('Помощник'));updated=deepcopy(data)
    updated['creation']['completed']=False;updated['skills']['physical']['strength']['base']=8
    preserve_creation(data,updated)
    assert updated['creation']==data['creation']
    assert updated['skills']['physical']['strength']['base']==8
    updated['features']['specialTraits']=[trait('Ползун')]
    with pytest.raises(ValidationError):preserve_creation(data,updated)
    preserve_creation(data,updated,is_gm=True)


def test_monthly_income_uses_current_highest_skill_category():
    data=build_character('Имя',plan());data['skills']['physical']['strength']['base']=16
    assert monthly_income(data)['options']==[3250]
    data['skills']['other']['medicine']['base']=18
    assert monthly_income(data)['options']==[5000]


def test_zone_history_has_server_rolls_and_preserves_choices():
    data=build_character('Имя',plan());history=generate_zone_history(data,random.Random(14))
    assert 1<=history['months']<=12 and len(history['events'])==history['months']
    assert not history['settled']
    assert all(1<=event['event']<=12 and event['incomeOptions'] for event in history['events'])
    assert data['inventory']['money']==10000


def test_templates_seed_is_idempotent_without_overwriting_custom_templates(app):
    from app.extensions import db
    from app.models.templates import ItemTemplate
    custom=ItemTemplate(name='Моя предыстория',category='background',attributes={'pluses':'Мой эффект'})
    db.session.add(custom);db.session.flush()
    assert seed_templates(db.session)==146;db.session.flush()
    assert seed_templates(db.session)==0
    assert custom.attributes=={'pluses':'Мой эффект'}


def test_action_discounts_and_exceptions():
    data=build_character('Имя',plan(traits=[trait('Джохн Уык'),trait('Быстрые руки'),trait('Глухая оборона')]))
    assert action_cost(data,'attack',3,weapon_type='pistol',aimed=True)==2
    assert action_cost(data,'reload',4)==3
    assert action_cost(data,'block',1)==0


@pytest.fixture
def creation_case(app):
    from app.extensions import db
    from app.models import User,Lobby,LobbyParticipant
    from flask_jwt_extended import create_access_token
    users=[User(username=f'creation-{n}',email=f'creation-{n}@example.test',password_hash='x') for n in range(3)]
    db.session.add_all(users);db.session.flush()
    gm,player,outsider=users
    lobby=Lobby(name='Создание',gm_id=gm.id,invite_code='CREATION')
    db.session.add(lobby);db.session.flush()
    db.session.add_all([LobbyParticipant(lobby_id=lobby.id,user_id=u.id) for u in (gm,player)]);db.session.commit()
    return {'lobby':lobby,'gm':gm,'player':player,'outsider':outsider,
            'headers':{u.id:{'Authorization':'Bearer '+create_access_token(identity=str(u.id))} for u in users}}


def test_creation_api_membership_catalog_and_authoritative_budget(creation_case,client):
    c=creation_case;url=f'/lobbies/{c["lobby"].id}'
    assert client.get(url+'/character-creation',headers=c['headers'][c['outsider'].id]).status_code==403
    response=client.get(url+'/templates?category=special_trait',headers=c['headers'][c['player'].id])
    assert response.status_code==200 and len(response.json['global'])==90
    p=plan('Гора',specializations=['physical.agility'],cashPoints=2)
    preview=client.post(url+'/character-creation',json={'name':'Гора','plan':p},headers=c['headers'][c['player'].id])
    assert preview.status_code==200 and preview.json['data']['skills']['physical']['agility']['base']==6
    response=client.post(url+'/characters',json={'name':'Гора','data':{'creationRequest':p,'inventory':{'money':999999},'skills':{'skillPoints':999}}},headers=c['headers'][c['player'].id])
    assert response.status_code==201
    character=response.json
    assert character['data']['inventory']['money']==10400 and character['data']['skills']['skillPoints']==28
    draft=deepcopy(character['data']);draft['notes']='Записано'
    saved=client.put(f'/lobbies/characters/{character["id"]}',json={'data':draft},headers=c['headers'][c['player'].id])
    assert saved.status_code==200 and saved.json['data']['skills']['physical']['agility']['base']==6


def test_gm_session_reward_authorization_and_replay(creation_case,client):
    c=creation_case;url=f'/lobbies/{c["lobby"].id}'
    response=client.post(url+'/characters',json={'name':'Бедный','data':{'creationRequest':plan('Бедный',traits=[trait('Заинтересованный',skill='other.medicine')])}},headers=c['headers'][c['player'].id])
    assert response.status_code==201
    character=response.json;reward_url=url+f'/characters/{character["id"]}/session-reward'
    payload={'operationId':'reward-1','trainingSkill':'other.medicine','roleplay':True,'heroism':True,'contribution':True,'gmBonus':True}
    assert client.post(reward_url,json=payload,headers=c['headers'][c['player'].id]).status_code==403
    response=client.post(reward_url,json=payload,headers=c['headers'][c['gm'].id])
    assert response.status_code==200
    assert response.json['result']['freePoints']==5 and response.json['result']['trainingPoints']==2
    assert response.json['data']['skills']['other']['medicine']['base']==8
    points=response.json['data']['skills']['skillPoints']
    replay=client.post(reward_url,json=payload,headers=c['headers'][c['gm'].id])
    assert replay.status_code==200 and replay.json['data']['skills']['skillPoints']==points


@pytest.mark.parametrize('name,path,roll,expected',[
    ('Атлет','other.medicine',51,0),('Атлет','other.medicine',50,1),
    ('Давний ветеран','physical.shooting',90,0),('Призывник','physical.strength',90,2),
    ('Истинный пацифист','social.persuasion',90,2),('Истинный пацифист','other.medicine',90,1),
])
def test_background_training_rules(name,path,roll,expected):
    from app.services.character_development import award_session
    class Dice:
        def randint(self,a,b):return roll
    data=build_character('Имя',plan(name))
    result=award_session(data,{'operationId':'one','trainingSkill':path},rng=Dice())
    assert result['trainingPoints']==expected and result['freePoints']==1


def test_fractional_learning_does_not_lose_half_point():
    from app.services.character_development import add_skill_experience
    data=build_character('Имя',plan(traits=[trait('Заинтересованный',skill='physical.agility')]))
    add_skill_experience(data,'physical.agility',1)
    assert data['skills']['physical']['agility']['base']==6
    assert data['skills']['physical']['agility']['learningRemainder']==.5
    add_skill_experience(data,'physical.agility',1)
    assert data['skills']['physical']['agility']['base']==8
    assert data['skills']['physical']['agility']['learningRemainder']==0


def test_psi_traits_use_current_will_and_halve_received_states():
    from app.services.combat import CombatService
    data=build_character('Имя',plan(raises={'physical.will':11},traits=[trait('Пси-устойчивый'),trait('Шапочка из фольги',idea='Зона разумна')]))
    assert CombatService._psi_defense_profile(data)['will']==30
    received=CombatService._increase_psy_state(data,20,'test')
    assert received['received']==7 and data['health']['psyState']==7


def test_pain_traits_reduce_penalties_without_erasing_pain():
    from app.services.combat import CombatService
    data=build_character('Имя',plan('Контрактник',traits=[trait('Железная воля')]))
    data['health']['painLevel']=5
    assert CombatService._health_roll_modifier(data,'skills.physical.will')==-1
    assert data['health']['painLevel']==5


def test_weight_traits_and_movement_penalty_stack():
    from app.services.combat import CombatService
    data=build_character('Имя',plan('Кладовщик',traits=[trait('Тяжеловес'),trait('Ползун')]))
    data['inventory']['pockets']=[{'weight':50,'quantity':1}]
    details=CombatService._inventory_weight_details(data)
    assert details['penalty']==int(details['raw_penalty']*.5)
    assert CombatService._movement_penalty_breakdown(data)['traits']==1


@pytest.mark.parametrize('copies,multiplier',[(1,2),(2,3)])
def test_beer_trait_preserves_radiation_ceiling_and_catalog(copies,multiplier):
    from app.services.general_consumable import _apply_profile
    data=build_character('Имя',plan(traits=[trait('Пивец пива')]*copies));data['health']['radiation']=40
    direct={'is_alcohol':True,'radiation_delta':-1}
    _apply_profile(data['health'],{},direct,{'name':'Банка пива'},in_combat=False,character_data=data)
    assert data['health']['radiation']==40-multiplier
    assert direct=={'is_alcohol':True,'radiation_delta':-1}
    data['health']['radiation']=60
    _apply_profile(data['health'],{},direct,{'name':'Банка пива'},in_combat=False,character_data=data)
    assert data['health']['radiation']==60


def test_athlete_healing_and_toliks_intoxication():
    from app.services.general_consumable import _apply_profile
    athlete=build_character('Имя',plan('Атлет'));athlete['health']['current']=500
    _apply_profile(athlete['health'],{}, {'hp':100},{'name':'Лечение'},in_combat=False,character_data=athlete)
    assert athlete['health']['current']==650
    from app.services.effects import apply_periodic_effects_to_health
    regeneration={'effects':[{'type':'regeneration','value':10,'remaining':2,'tick':'turn_end'}]}
    _apply_profile(athlete['health'],regeneration,{}, {'name':'Регенеративный препарат'},in_combat=False,character_data=athlete)
    apply_periodic_effects_to_health(athlete['health'],athlete['health']['effects'])
    assert athlete['health']['current']==665
    assert regeneration['effects'][0]['value']==10
    tolik=build_character('Имя',plan('Дядя Толик'))
    _apply_profile(tolik['health'],{}, {'intoxication_delta':30},{'name':'Водка'},in_combat=False,character_data=tolik)
    assert tolik['health']['intoxication']==15


def test_pharmacist_has_no_medicine_speed_bonus():
    from app.services.medical_procedure import _minimum_treatment_cost
    data=build_character('Имя',plan('Фармацевт',raises={'other.medicine':7}))
    assert data['skills']['other']['medicine']['base']==15
    assert _minimum_treatment_cost(data,{'treatment_action_points':4})==4
    data['basic']['background']={}
    assert _minimum_treatment_cost(data,{'treatment_action_points':4})==3


def test_police_and_two_john_wick_traits_combine_with_ergonomics(app):
    from types import SimpleNamespace
    from app.services.combat import CombatService
    data=build_character('Имя',plan('Полицейский',traits=[trait('Джохн Уык')]*2))
    actor=SimpleNamespace(character=SimpleNamespace(data=data),posture='standing')
    weapon={'name':'Пистолет','subcategory':'Пистолет','ergonomics':50}
    raw=deepcopy(data);raw['features']['specialTraits']=[];raw['basic']['background']={}
    base=CombatService._weapon_ergonomics_profile(SimpleNamespace(character=SimpleNamespace(data=raw),posture='standing'),weapon)['aimed_shot_action_points']
    actual=CombatService._weapon_ergonomics_profile(actor,weapon)['aimed_shot_action_points']
    assert actual==max(1,base-3)


def test_gunsmith_does_not_get_tactics_ergonomics(app):
    from types import SimpleNamespace
    from app.services.combat import CombatService
    data=build_character('Имя',plan('Оружейник',backgroundWeapon='pistols'))
    actual=CombatService._weapon_ergonomics_profile(SimpleNamespace(character=SimpleNamespace(data=data),posture='standing'),{'name':'Пистолет','ergonomics':50})
    assert actual['tactics_value']==0


def test_duplicate_limits_in_existing_sheet_and_legacy_preservation():
    from app.services.character_creation import validate_trait_selection
    one={'name':'Амбидекстр'}
    with pytest.raises(ValidationError):validate_trait_selection({}, {'features':{'specialTraits':[one,one]}})
    legacy={'features':{'specialTraits':[one]*4}}
    validate_trait_selection(legacy,deepcopy(legacy))


def test_invalid_choice_types_are_validation_errors():
    for p in (plan(specializations=[{}]),plan('Оружейник',backgroundWeapon={}),plan(traits=[trait('Заинтересованный',skill=[])])):
        with pytest.raises(ValidationError):build_character('Имя',p)


def test_first_three_rounds_ap_includes_start_and_resets_on_new_combat(creation_case,client):
    from app.extensions import db
    from app.models import Location,LocationCharacter
    from app.services.character import CharacterService
    c=creation_case;lobby=c['lobby'];headers=c['headers'][c['gm'].id]
    character=CharacterService.create_character(lobby.id,c['gm'].id,'Математик',{'creationRequest':plan('Математик',traits=[trait('Укрощенный адреналин')])})
    other=CharacterService.create_character(lobby.id,c['gm'].id,'Другой',{})
    location=Location(lobby_id=lobby.id,name='Бой',world_tile_x=0,world_tile_z=0,grid_width=8,grid_height=8,
        tiles_data=[[{'terrain':'grass','height':0} for _ in range(8)] for _ in range(8)])
    db.session.add(location);db.session.flush()
    a=LocationCharacter(location_id=location.id,character_id=character.id,pos_x=1,pos_y=1)
    b=LocationCharacter(location_id=location.id,character_id=other.id,pos_x=3,pos_y=1)
    db.session.add_all([a,b]);db.session.commit()
    url=f'/lobbies/{lobby.id}/locations/{location.id}/combat'
    def start(first):
        r=client.post(url+'/start',json={'location_character_ids':[a.id,b.id],'initiator_location_character_id':first,'initiator_first':True},headers=headers)
        assert r.status_code==200,r.json
        return client.get(url,headers=headers).json
    state=start(a.id)
    assert state['current_character']['action_points_current']==8
    for round_number in range(1,4):
        for actor in (a,b):
            r=client.post(url+'/end_turn',json={'location_character_id':actor.id},headers=headers)
            assert r.status_code==200,r.json
        state=client.get(url,headers=headers).json
        assert state['current_character']['action_points_current']==(8 if round_number<3 else 5)
    assert client.post(url+'/end',json={},headers=headers).status_code==200
    start(b.id)
    db.session.refresh(a)
    assert a.action_points_current==6
    assert client.post(url+'/end',json={},headers=headers).status_code==200
    from sqlalchemy.orm.attributes import flag_modified
    from app.services.combat import CombatService
    character.data['basic']['background']={'name':'Агрессор'};flag_modified(character,'data');db.session.commit()
    start(a.id)
    assert CombatService._health_roll_modifier(character.data,'skills.physical.shooting')==3
    assert CombatService._health_roll_modifier(character.data,'skills.social.intimidation')==3
    assert CombatService._health_roll_modifier(character.data,'skills.physical.strength')==0
    for _ in range(3):
        for actor in (a,b):assert client.post(url+'/end_turn',json={'location_character_id':actor.id},headers=headers).status_code==200
    db.session.refresh(character)
    assert CombatService._health_roll_modifier(character.data,'skills.physical.shooting')==0
    assert client.post(url+'/end',json={},headers=headers).status_code==200
    character.data['basic']['background']={'name':'Стойкий'};flag_modified(character,'data');db.session.commit()
    assert CombatService._target_armor(character.data,'chest',target=a)[0]==0
    start(a.id)
    assert CombatService._target_armor(character.data,'chest',target=a)[0]==5
    assert CombatService._target_elemental_protection(character.data,'thermal',target=a)==5
    assert CombatService._equipped_radiation_protection(character.data,a)==5
    assert CombatService._psi_defense_profile(character.data,a)['armor']==0
    before=character.data['health']['current']
    CombatService._apply_attack_damage(a,100,'chest',{'damage_type':'physical'},round_number=1,trauma_checks=0,allow_bleeding=False)
    assert character.data['health']['current']==before-90
    assert character.data['health'].get('stress',0)==0
    from app.services.effects import apply_effect_to_health
    apply_effect_to_health(character.data['health'],{'type':'bleeding_external_extreme','value':8,'area':'chest','source':'test'})
    assert CombatService._bleeding_check_profile(character.data,a)['severity']==6
    assert client.post(url+'/end',json={},headers=headers).status_code==200
    assert CombatService._target_armor(character.data,'chest',target=a)[0]==0
    assert CombatService._bleeding_check_profile(character.data,a)['severity']==8


def test_pockets_and_launcher_weight_are_not_reapplied_on_save(app):
    from app.services.character_traits import sync_pocket_capacity
    from app.services.combat import CombatService
    data=build_character('Имя',plan(traits=[trait('Узкие карманы')]*2+[trait('Да не такой и тяжелый')]))
    assert data['inventory']['pocketMaxVolume']==0
    data['inventory']['backpack']=[{'name':'Кейс','weight':1,'contents':[{'name':'Гранатомёт','subcategory':'Гранатометы','weight':7,'quantity':1}]}]
    assert CombatService._inventory_weight_details(data)['total_weight']==5.25
    updated=deepcopy(data);sync_pocket_capacity(data,updated)
    assert updated['inventory']['pocketMaxVolume']==0
    updated['features']['specialTraits']=[];sync_pocket_capacity(data,updated)
    assert updated['inventory']['pocketMaxVolume']==10


def test_pocket_capacity_ignores_manual_numbers_and_tracks_trait_changes():
    from app.services.character_traits import sync_pocket_capacity
    data=build_character('Имя',plan(traits=[trait('Глубокие карманы'),trait('Узкие карманы')]))
    for value in (999,-1,None,'очень много'):
        updated=deepcopy(data);updated['inventory']['pocketMaxVolume']=value
        sync_pocket_capacity(data,updated)
        assert updated['inventory']['pocketMaxVolume']==35
    updated['features']['specialTraits']=[]
    sync_pocket_capacity(data,updated)
    assert updated['inventory']['pocketMaxVolume']==10


def test_pocket_capacity_is_authoritative_on_create_read_and_save(creation_case,client):
    from app.services.character import CharacterService
    c=creation_case
    character=CharacterService.create_character(c['lobby'].id,c['player'].id,'Карманы',{'inventory':{'pocketMaxVolume':999}})
    assert character.data['inventory']['pocketMaxVolume']==10
    # Чтение старого сохранения не переписывает исходные игровые данные.
    character.data['inventory']['pocketMaxVolume']=777
    assert character.data_snapshot()['inventory']['pocketMaxVolume']==10
    assert character.data['inventory']['pocketMaxVolume']==777
    for user in (c['player'],c['gm']):
        draft=character.data_snapshot();draft['inventory']['pocketMaxVolume']=999
        response=client.put(f'/lobbies/characters/{character.id}',json={'data':draft},headers=c['headers'][user.id])
        assert response.status_code==200,response.json
        assert response.json['data']['inventory']['pocketMaxVolume']==10
        assert character.data['inventory']['pocketMaxVolume']==10


def test_migration_adds_catalog_without_duplicates(app,monkeypatch):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.extensions import db
    from app.models.templates import ItemTemplate
    spec=importlib.util.spec_from_file_location('creation_migration',Path('migrations/versions/e8c4f6021b93_character_creation_catalog.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'op',Operations(MigrationContext.configure(db.session.connection())))
    module.upgrade()
    assert ItemTemplate.query.filter(ItemTemplate.category.in_(['background','special_trait'])).count()==146
    module.upgrade()
    assert ItemTemplate.query.filter(ItemTemplate.category.in_(['background','special_trait'])).count()==146


def test_medical_background_doubles_addiction_chance(creation_case):
    from app.services.character import CharacterService
    from app.services.consumable_use import apply_consumable_use
    c=creation_case
    character=CharacterService.create_character(c['lobby'].id,c['gm'].id,'Врач',{'creationRequest':plan('Врач по бумагам')})
    profile={'key':'test','label':'Тест','kind':'medicine','chance':10,'multipliers':{}}
    result=apply_consumable_use(character,character,{'id':'creation-addiction-test','side_effects':{'exposure':{'item_name':'Лекарство','price':100}}},addiction_profile=profile)
    assert result['addiction']['chance']==20
    assert profile['chance']==10


def test_help_bonus_changes_total_without_changing_natural_die(monkeypatch):
    from app.services.combat import CombatService
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:12)
    data=build_character('Имя',plan())
    plain=CombatService._narrative_skill_check(data,'skills.other.engineering')
    helped=CombatService._narrative_skill_check(data,'skills.other.engineering',roll_bonus=2)
    assert plain['roll']==helped['roll']==12
    assert helped['total']==plain['total']+2


def test_clinical_trait_changes_only_existing_jam_chance(monkeypatch):
    from app.services.combat import CombatService
    data=build_character('Имя',plan(traits=[trait('Клинически неуравновешенный')]*2))
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:a)
    assert CombatService._roll_character_weapon_jam(data,{'durability':100},1)['chance']==0
    result=CombatService._roll_character_weapon_jam(data,{'durability':90},5)
    assert result['chance']==5 and result['triggered']
    assert not CombatService._roll_character_weapon_jam(data,{'durability':90},6)['triggered']


def test_bipod_uses_real_prone_or_braced_position_and_post_background(app):
    from types import SimpleNamespace
    from app.services.combat import CombatService
    data=build_character('Имя',plan('Пост принял'))
    actor=SimpleNamespace(character=SimpleNamespace(data=data),posture='standing',weapon_braced=False)
    weapon={'ergonomics':50,'installedModules':[{'name':'Сошки','slotType':'handguard','bipod':True,'deployed':True}]}
    assert CombatService._weapon_ergonomics_profile(actor,weapon,0)['module_modifier']==0
    actor.posture='prone'
    assert CombatService._weapon_ergonomics_profile(actor,weapon,0)['module_modifier']==50
    data['basic']['background']={}
    assert CombatService._weapon_ergonomics_profile(actor,weapon,0)['module_modifier']==75


def test_helper_penalty_begins_round_four_and_paid_help_clears_it(grenade_game):
    from app.services.combat import CombatService
    from app.models import LocationCombatState
    from app.extensions import db
    actor=grenade_game['actor_location'];target=grenade_game['target_location']
    actor.character.data['basic']={'background':{'name':'Помощник'}}
    state=LocationCombatState.query.filter_by(location_id=actor.location_id).one()
    state.round_number=3;CombatService._update_helper_penalties(state)
    assert CombatService._health_roll_modifier(actor.character.data,'skills.other.engineering')==0
    state.round_number=4;CombatService._update_helper_penalties(state)
    assert CombatService._health_roll_modifier(actor.character.data,'skills.other.engineering')==-3
    db.session.commit()
    CombatService.reserve_reaction(actor.location_id,grenade_game['actor_user']['id'],actor.id,action_points=2,
        kind='help',help_target_character_id=target.character_id,help_action_label='Проверка',help_skill_path='skills.other.engineering')
    assert CombatService._health_roll_modifier(actor.character.data,'skills.other.engineering')==0
    assert actor.character.data['health']['combatMeta']['lastPaidHelpRound']==4


def test_doctor_profile_boosts_only_requested_benefits_and_keeps_source():
    from app.services.character_medicine import prepare_profile
    doctor=build_character('Имя',plan('Врач по бумагам'))
    direct={'hp':101,'pain_delta':-3,'exhaustion_delta':-3,'radiation_delta':-3,'infection_delta':-3,
        'psy_delta':-3,'bleeding_modifier_delta':-3,'weight_delta':-3,'movement_points_delta':-3,
        'intoxication_delta':-3,'strength_delta':3,'duration':4,'med_bonus':2,'stress_delta':-1}
    original=deepcopy(direct)
    profile,adjusted=prepare_profile({},direct,{'name':'Лекарство'},doctor)
    assert adjusted['hp']==151 and adjusted['strength_delta']==4
    assert all(adjusted[key]==-4 for key in ('pain_delta','exhaustion_delta','radiation_delta','infection_delta','psy_delta','bleeding_modifier_delta','weight_delta','movement_points_delta','intoxication_delta'))
    assert adjusted['duration']==4 and adjusted['med_bonus']==2 and adjusted['stress_delta']==-1
    assert direct==original


def test_ampoule_trait_extends_positive_round_effects_without_delaying_harm():
    from app.services.character_medicine import prepare_profile
    from app.services.general_consumable import _apply_profile
    doctor=build_character('Имя',plan('Гений медицинской мысли',traits=[trait('Главное знать куда ставить')]))
    source={'effects':[{'type':'regeneration','value':10,'remaining':3,'tick':'turn_end'},
                       {'type':'pain','value':1,'remaining':3,'tick':'turn_end'}]}
    profile,direct=prepare_profile(source,{'application_form':'injectable','duration':3,'strength_delta':2,'agility_delta':-2,'post_duration_hp_delta':-50,'med_bonus':8}, {'name':'Ампула'},doctor)
    assert direct['medical_difficulty']==8 and direct['med_bonus']==0
    assert profile['effects'][0]['remaining']==4 and profile['effects'][1]['remaining']==3
    assert source['effects'][0]['remaining']==3
    target=build_character('Цель',plan());_apply_profile(target['health'],profile,direct,{'name':'Ампула'},in_combat=True,character_data=target)
    modifiers=target['health']['combatMeta']['consumableModifiers']
    assert next(m for m in modifiers if m['stat']=='strength')['remaining']==4
    assert next(m for m in modifiers if m['stat']=='agility')['remaining']==3
    assert next(e for e in target['health']['effects'] if e['type']=='stimulant_crash')['remaining']==3


@pytest.mark.parametrize('background',['Гений медицинской мысли','Врач по бумагам'])
def test_bandage_treats_one_stage_higher_with_original_application(client,pair,auth_headers,monkeypatch,background):
    from test_medical_procedure import install_item,selected_bleeding,payload,apply
    from app.extensions import db
    from sqlalchemy.orm.attributes import flag_modified
    effect={'id':'medium-bleed','type':'bleeding_external_medium','area':'leftArm','source':'cut'}
    _,profile,item=install_item(pair,'Бинт','Останавливает Слабое кровотечение.',quantity=1,effects=[effect])
    actor=pair['actor_character'];actor.data['basic']={'background':{'name':background}};flag_modified(actor,'data');db.session.commit()
    monkeypatch.setattr('app.services.medical_procedure.random.randint',lambda a,b:20)
    response=apply(client,pair,auth_headers,payload(pair,item,selected_bleeding(profile,effect,0)))
    assert response.status_code==200,response.json
    assert response.json['medical_result']['outcome']['mode']=='close'


def test_medicine_extra_use_is_real_and_partial_single_use_cannot_be_transferred(client,pair,auth_headers,monkeypatch):
    from test_medical_procedure import install_item,selected_bleeding,payload,apply
    from app.services.combat import CombatService
    from app.extensions import db
    from sqlalchemy.orm.attributes import flag_modified
    effect={'id':'light-bleed','type':'bleeding_external_light','area':'leftArm','source':'cut'}
    _,profile,item=install_item(pair,'Бинт','Останавливает Слабое кровотечение.',quantity=1,effects=[effect])
    actor=pair['actor_character'];actor.data['features']={'specialTraits':[{'name':'На жизни сэкономим'}]};flag_modified(actor,'data');db.session.commit()
    monkeypatch.setattr('app.services.medical_procedure.random.randint',lambda a,b:20)
    response=apply(client,pair,auth_headers,payload(pair,item,selected_bleeding(profile,effect)))
    assert response.status_code==200,response.json
    remaining=actor.data['inventory']['backpack'][0]
    assert remaining['uses']==1 and remaining['maxUses']==2 and remaining['medicineEconomy']['baseMax']==1
    with pytest.raises(ValidationError,match='нельзя передать'):CombatService._take_inventory_item(actor.data,['inventory','backpack',0],1)
    from app.services.effects import apply_effect_to_health
    effect={**effect,'id':'another-bleed'};apply_effect_to_health(actor.data['health'],effect);flag_modified(actor,'data');db.session.commit()
    response=apply(client,pair,auth_headers,payload(pair,remaining,selected_bleeding(profile,effect),operation='medical-second'))
    assert response.status_code==200,response.json
    assert not actor.data['inventory']['backpack']


def test_transfer_of_multiuse_medicine_removes_personal_extra_doses():
    from app.services.combat import CombatService
    item={'id':'med','quantity':2,'uses':7,'maxUses':10,'medicineEconomy':{'baseMax':5},'attributes':{'uses_remaining':7}}
    data={'inventory':{'backpack':[item]}}
    transferred=CombatService._take_inventory_item(data,['inventory','backpack',0],1)
    assert transferred['uses']==3 and transferred['maxUses']==5 and 'medicineEconomy' not in transferred
    assert data['inventory']['backpack'][0]['uses']==10 and data['inventory']['backpack'][0]['quantity']==1


def test_tough_nut_survives_actual_fatal_wound_and_keeps_pain_floor(grenade_game):
    from app.services.combat import CombatService
    from app.services.effects import apply_effect_to_health,sync_health_derived_statuses
    actor=grenade_game['actor_location']
    actor.character.data['basic']={'background':{'name':'Крепкий орешек'}}
    actor.character.data['health']['combatMeta']={'toughNut':{'uses':1,'minimumPain':0}}
    apply_effect_to_health(actor.character.data['health'],{'type':'fracture','area':'leftLeg','source':'old'})
    outcome=CombatService._apply_attack_damage(actor,900,'head',{'damage_type':'physical'},round_number=1,trauma_checks=0,allow_bleeding=False)
    assert outcome['_attackOutcome']['tough_nut']['survived']
    assert outcome['current']==1 and outcome['zones']['head']['current']==1
    assert not any(e['type']=='death' for e in outcome['effects'])
    assert any(e['type']=='fracture' and e.get('source')=='old' for e in outcome['effects'])
    assert outcome['combatMeta']['toughNut']=={'uses':0,'minimumPain':1}
    outcome['painLevel']=0;sync_health_derived_statuses(outcome)
    assert outcome['painLevel']==1


def test_tough_nut_usage_refill_is_gm_only_and_does_not_reset_minimum(creation_case,client):
    from app.services.character import CharacterService
    from app.extensions import db
    from sqlalchemy.orm.attributes import flag_modified
    c=creation_case;character=CharacterService.create_character(c['lobby'].id,c['player'].id,'Орешек',{'creationRequest':plan('Крепкий орешек')})
    character.data['health']['combatMeta']['toughNut']={'uses':0,'minimumPain':2};flag_modified(character,'data');db.session.commit()
    draft=character.data_snapshot();draft['health']['combatMeta']['toughNut']={'uses':5,'minimumPain':0}
    response=client.put(f'/lobbies/characters/{character.id}',json={'data':draft},headers=c['headers'][c['player'].id])
    assert response.status_code==200
    assert response.json['data']['health']['combatMeta']['toughNut']=={'uses':0,'minimumPain':2}
    draft=response.json['data'];draft['health']['combatMeta']['toughNut']={'uses':1,'minimumPain':0}
    response=client.put(f'/lobbies/characters/{character.id}',json={'data':draft},headers=c['headers'][c['gm'].id])
    assert response.status_code==200,response.json
    assert response.json['data']['health']['combatMeta']['toughNut']=={'uses':1,'minimumPain':2}


def test_butchering_bonuses_and_favored_species_are_specific():
    from app.services.character_traits import butchering_bonus
    hunter=build_character('Имя',plan('Охотник',backgroundWeapon='pistols',favoredMutant='Кабан',traits=[trait('Мастер охоты')]*2))
    assert butchering_bonus(hunter,'кабан')==9
    assert butchering_bonus(hunter,'плоть')==6
    forest=build_character('Имя',plan('Хозяин леса',traits=[trait('Мастер охоты')]))
    assert butchering_bonus(forest,'плоть')==7


def test_seeded_templates_do_not_duplicate_runtime_catalog(creation_case,client):
    from app.extensions import db
    c=creation_case;seed_templates(db.session);db.session.commit()
    response=client.get(f'/lobbies/{c["lobby"].id}/templates?category=background',headers=c['headers'][c['player'].id])
    assert response.status_code==200
    assert len(response.json['global'])==56
    assert all(t['id']>0 for t in response.json['global'])


def test_small_height_requires_actual_evasive_movement():
    from types import SimpleNamespace
    from app.services.combat import CombatService
    data=build_character('Имя',plan(traits=[trait('Маленький рост')]))
    data['health']['combatMeta']={'movementEvasion':True}
    target=SimpleNamespace(character=SimpleNamespace(data=data),movement_mode_this_turn='walk')
    assert CombatService._target_evasion_trait_penalty(target)==2
    target.movement_mode_this_turn='backward_sideways'
    assert CombatService._target_evasion_trait_penalty(target)==0
    target.movement_mode_this_turn='run';data['health']['combatMeta']['movementEvasion']=False
    assert CombatService._target_evasion_trait_penalty(target)==0


def test_radio_background_reduces_only_electric_protection():
    from app.services.combat import CombatService
    data=build_character('Имя',plan('Радиолюбитель'))
    data['equipment']={'armor':{'protection':{'electric':30,'thermal':30}}}
    assert CombatService._target_elemental_protection(data,'electric')==20
    assert CombatService._target_elemental_protection(data,'thermal')==30
    data['equipment']={}
    assert CombatService._target_elemental_protection(data,'electric')==0


@pytest.mark.parametrize('selected',[None,'Мастер вскидывания'])
def test_launcher_unaimed_trait_changes_paid_attack_difficulty(grenade_game,monkeypatch,selected):
    from app.extensions import db
    from sqlalchemy.orm.attributes import flag_modified
    from app.services.combat import CombatService
    actor=grenade_game['actor_location'];data=deepcopy(actor.character.data)
    data['features']={'specialTraits':[{'name':selected}] if selected else []}
    data['weapons']=[{'name':'Автомат с подствольником','subcategory':'Штурмовые винтовки','ergonomics':50,
        'installedModules':[{'name':'ГП-25','attributes':{'type':'grenade_launcher','range':30},
                             'loaded':True,'loadedGrenade':{'name':'ВОГ-25','quantity':1}}]}]
    actor.character.data=data;actor.drawn_weapon_index=0;flag_modified(actor.character,'data');db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:b)
    result=CombatService.perform_action(actor.location_id,grenade_game['actor_user']['id'],actor.id,'explosive_attack',
        explosive_source='underbarrel',explosive_fire_mode='unaimed',weapon_index=0,target_x=10,target_y=5.2)
    check=result['explosive']
    # Стрельба отсутствует в фикстуре: базовая сложность 12 и штраф +4/+3.
    assert check['difficulty']==(15 if selected else 16)
    assert actor.action_points_current==18
    assert actor.character.data['weapons'][0]['installedModules'][0]['loadedGrenade'] is None


def test_stoic_thermal_bonus_protects_from_fire_only_in_combat(grenade_game):
    from app.services.burning import ignite,tick
    from app.services.combat import CombatService
    from app.extensions import db
    from app.models import LocationCombatState
    actor=grenade_game['actor_location'];data=deepcopy(actor.character.data)
    data['basic']={'background':{'name':'Стойкий'}}
    data['equipment']={'armor':{'protection':{'thermal':31},'durability':100,'maxDurability':100}}
    actor.character.data=data;db.session.flush()
    ignite(actor,5,source='Молотов');tick(actor,1)
    assert actor.character.data['health']['current']==700
    assert actor.character.data['health']['painLevel']==0
    LocationCombatState.query.filter_by(location_id=actor.location_id).one().status='idle'
    tick(actor,2)
    assert actor.character.data['health']['current']==650


@pytest.mark.parametrize('sniper',[False,True])
def test_only_sniper_can_aim_at_sprinting_character(grenade_game,monkeypatch,sniper):
    from app.extensions import db
    from app.services.combat import CombatService
    actor=grenade_game['actor_location'];target=grenade_game['target_location']
    data=deepcopy(actor.character.data)
    data['basic']={'background':{'name':'Снайпер' if sniper else 'Пустышка'}}
    data['weapons']=[{'name':'СВД','subcategory':'Снайперские винтовки','range':100,'damage':20,'ergonomics':50,
        'fixedMagazine':True,'ammo':5,'fixedAmmo':[{'name':'7.62x54','quantity':5,'attributes':{'damage':20}}]}]
    actor.character.data=data;actor.drawn_weapon_index=0;target.movement_mode_this_turn='sprint'
    actor.aimed_target_character_id=target.character_id;actor.aimed_weapon_index=0;db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:b)
    kwargs=dict(weapon_index=0,fire_mode='aimed',shot_count=1,
        action_points=CombatService._weapon_ergonomics_profile(actor,data['weapons'][0])['aimed_shot_action_points'],
        target_character_id=target.character_id,target_zone='chest')
    if not sniper:
        with pytest.raises(ValidationError,match='sprinting'):CombatService.perform_action(actor.location_id,grenade_game['actor_user']['id'],actor.id,'attack',**kwargs)
    else:
        result=CombatService.perform_action(actor.location_id,grenade_game['actor_user']['id'],actor.id,'attack',**kwargs)
        assert result['attack']['target_movement_mode']=='sprint'
        assert result['attack']['shot_count']==1


@pytest.mark.parametrize('invalid',[{},[],True,123])
def test_reward_rejects_invalid_training_without_partial_award(invalid):
    from app.services.character_development import award_session
    data=build_character('Имя',plan());before=deepcopy(data)
    with pytest.raises(ValidationError):award_session(data,{'operationId':'invalid','trainingSkill':invalid})
    assert data==before


def test_all_sixteen_skills_allow_unspent_specializations(monkeypatch):
    import app.services.character_creation as creation
    source=deepcopy(creation._catalog())
    background=next(b for b in source['backgrounds'] if b['name']=='Помощник')
    background['entries'].append({'type':'specializations','amount':10})
    monkeypatch.setattr(creation,'_catalog',lambda:source)
    paths=[f'{g}.{k}' for g,items in source['skills'].items() for k in items]
    data=creation.build_character('Все навыки',{'backgroundId':background['id'],'specializations':paths})
    assert len(paths)==16
    assert data['skills']['specializations']>0
    assert all(data['skills'][g][k]['specialized'] for g,items in source['skills'].items() for k in items)
    with pytest.raises(ValidationError,match='все'):
        creation.build_character('Не все навыки',{'backgroundId':background['id'],'specializations':paths[:-1]})


def test_helper_and_three_specialists_fill_all_skills_but_hardening_cannot_add_specialist():
    from app.services.character_history import settle_history
    selected=plan('Помощник')
    remaining=[path for path in catalog()['skillOrder'] if path not in selected['specializations']]
    assert len(remaining)==3
    selected['traits']=[trait('Специалист',skill=path) for path in remaining]
    data=build_character('Помощник со всеми навыками',selected)
    assert all(data['skills'][g][k]['specialized'] for g,items in catalog()['skills'].items() for k in items)
    data['creation']['zoneHistory']={'months':1,'settled':False,'events':[
        {'month':1,'incomeOptions':[1000],'hardening':{'limit':10}}]}
    before=deepcopy(data)
    with pytest.raises(ValidationError,match='нельзя получить через Закалку'):
        settle_history(data,{'1':{'trait':trait('Специалист',skill=remaining[0])}})
    negative=next(entry for entry in catalog()['traits'] if entry['cost']<0)
    with pytest.raises(ValidationError,match='положительной стоимостью'):
        settle_history(data,{'1':{'trait':{'id':negative['id']}}})
    assert data==before
    resolved=settle_history(data,{'1':{'decline':True}})
    assert resolved['skills']['skillPoints']==data['skills']['skillPoints']+5


def test_purchase_preview_allows_debt_but_final_creation_rejects_it(app):
    from app.extensions import db
    from app.models.templates import ItemTemplate
    gun=ItemTemplate(name='Дорогой пистолет',category='weapon',item_class='1',price=6000,attributes={})
    db.session.add(gun);db.session.flush()
    selected=plan('Бедный',purchases=[{'templateId':gun.id,'quantity':1}])
    draft=build_character('Черновик',selected,finalize=False)
    assert draft['inventory']['money']==-1000
    assert draft['creation']['startingPurchase']['completed'] is False
    with pytest.raises(ValidationError,match='бюджет'):build_character('Готовый',selected)
    selected['cashPoints']=5
    done=build_character('Готовый',selected)
    assert done['inventory']['money']==0
    assert not done['weapons'][0].get('createdByPlayer')


def test_hunter_accepts_only_mutants_from_existing_bestiary():
    from app.services.world_rules import mutant_catalog
    assert [m['name'] for m in catalog()['mutants']]==[m['name'] for m in mutant_catalog()]
    selected=plan('Охотник')
    build_character('Черновик охотника',selected,finalize=False)
    with pytest.raises(ValidationError,match='избранного'):build_character('Охотник',selected)
    with pytest.raises(ValidationError,match='бестиария'):
        build_character('Охотник',plan('Охотник',favoredMutant='Не существующий мутант'))
    valid=build_character('Охотник',plan('Охотник',backgroundWeapon='pistols',favoredMutant=catalog()['mutants'][0]['name']))
    assert valid['basic']['background']['choices']['favoredMutant']==catalog()['mutants'][0]['name']


def test_player_starting_equipment_is_not_marked_player_created_after_save(creation_case,client):
    from app.extensions import db
    from app.models.templates import ItemTemplate
    c=creation_case
    gun=ItemTemplate(name='Покупка игрока',category='weapon',item_class='1',price=500,attributes={})
    db.session.add(gun);db.session.flush()
    response=client.post(f'/lobbies/{c["lobby"].id}/characters',headers=c['headers'][c['player'].id],json={
        'name':'Со стартовым оружием','data':{'creationRequest':plan(purchases=[{'templateId':gun.id,'quantity':1}])}})
    assert response.status_code==201
    character=response.json
    assert not character['data']['weapons'][0].get('createdByPlayer')
    assert character['data']['weapons'][0]['model']==gun.name
    saved=client.put(f'/lobbies/characters/{character["id"]}',headers=c['headers'][c['player'].id],json={'data':character['data']})
    assert saved.status_code==200
    assert not saved.json['data']['weapons'][0].get('createdByPlayer')


def test_final_raise_price_continues_before_manual_decreases():
    selected=plan(raises={'physical.agility':7},lowers={'physical.agility':2},finalRaises={'physical.agility':1})
    data=build_character('Цена после понижений',selected)
    ledger={row['stage']:row for row in data['creation']['ledger']}
    assert ledger['raises']['levels']['physical.agility']==12
    assert ledger['lowers']['levels']['physical.agility']==6
    assert data['skills']['physical']['agility']['base']==7
    assert ledger['traits']['points']-ledger['final_raises']['points']==3
    assert data['creation']['finalRaisePricing']['nextCosts']['physical.agility']==4
    selected['finalRaises']['physical.agility']=2
    second=build_character('Два повышения',selected)
    assert second['skills']['physical']['agility']['base']==8
    assert second['skills']['skillPoints']==data['skills']['skillPoints']-4


def test_background_penalties_do_not_discount_final_raise():
    data=build_character('Цена после минусов',plan('Гора',raises={'physical.agility':7},finalRaises={'physical.agility':1}))
    ledger={row['stage']:row for row in data['creation']['ledger']}
    assert ledger['raises']['levels']['physical.agility']==12
    assert ledger['background_negative']['levels']['physical.agility']==10
    assert data['skills']['physical']['agility']['base']==11
    assert ledger['traits']['points']-ledger['final_raises']['points']==3


def test_positive_trait_increases_pricing_level_without_discount_from_lowering():
    data=build_character('Специалист после понижений',plan(raises={'physical.agility':7},lowers={'physical.agility':2},
        traits=[trait('Специалист',skill='physical.agility')],finalRaises={'physical.agility':1}))
    ledger={row['stage']:row for row in data['creation']['ledger']}
    assert ledger['traits']['levels']['physical.agility']==9
    assert data['skills']['physical']['agility']['base']==10
    assert ledger['traits']['points']-ledger['final_raises']['points']==6
