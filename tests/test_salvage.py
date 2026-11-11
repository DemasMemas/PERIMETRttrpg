from copy import deepcopy
import pytest
from app.extensions import db
from app.models.templates import ItemTemplate
from app.services.salvage import resolve,plan
from app.services.exceptions import ValidationError
from test_crafting import workshop,call
from test_quick_skill_checks import checks


class Die:
    def __init__(self,*values):self.values=iter(values)
    def randint(self,*_):return next(self.values)


@pytest.fixture
def salvage_workshop(workshop):
    db.session.add(ItemTemplate(name='Металлические детали',category='crafting_material',weight=.5,volume=1,attributes={}))
    data=deepcopy(workshop['actor_character'].data)
    data['inventory']['pockets'].append({'id':'scrap-gun','name':'Пистолет','category':'weapon','subcategory':'Пистолеты',
        'itemClass':'1','quantity':1,'durability':90,'maxDurability':100,'weight':2})
    workshop['actor_character'].data=data;db.session.commit();return workshop


@pytest.mark.parametrize('roll,expected',[(30,2),(31,3),(45,3),(46,4),(99,10),(100,12),(149,18),(150,20)])
def test_weapon_table_boundaries(roll,expected):
    result=resolve({}, {'salvage_kind':'weapon','bonus':0,'modifications':0,'escrow':[{'item':{'name':'Пистолет'}}]},rng=Die(roll))
    assert result['materials']==[{'name':'Металлические детали','quantity':expected}]


def test_actual_start_wait_cancel_and_finish_once(client,salvage_workshop,auth_headers,monkeypatch):
    case=salvage_workshop
    selected={'path':['inventory','pockets',4],'tool_path':['inventory','pockets',3]}
    quote=call(client,case,auth_headers,'quote_salvage',**selected)
    assert quote.status_code==200,quote.json
    assert quote.json['result']['duration_seconds']==1200
    started=call(client,case,auth_headers,'salvage',**selected)
    assert started.status_code==200,started.json
    assert not any(i.get('id')=='scrap-gun' for i in started.json['data']['inventory']['pockets'])
    job=started.json['result']
    assert call(client,case,auth_headers,'finish',job_id=job['id']).status_code==400
    case['lobby'].game_time_minutes+=20;db.session.commit()
    original=resolve
    monkeypatch.setattr('app.services.salvage.resolve',lambda data,job:original(data,job,rng=Die(100)))
    finished=call(client,case,auth_headers,'finish',job_id=job['id'])
    assert finished.status_code==200,finished.json
    assert finished.json['result']['kind']=='salvage' and finished.json['result']['materials']
    repeated=call(client,case,auth_headers,'finish',job_id=job['id'],character_revision=0)
    assert repeated.json['result']['replayed']
    assert repeated.json['data']['inventory']==finished.json['data']['inventory']


def test_salvage_without_tool_is_available_with_lower_bonus(client,salvage_workshop,auth_headers):
    case=salvage_workshop
    path=['inventory','pockets',4]
    with_tool=call(client,case,auth_headers,'quote_salvage',path=path,tool_path=['inventory','pockets',3])
    without_tool=call(client,case,auth_headers,'quote_salvage',path=path)
    assert without_tool.status_code==200,without_tool.json
    assert with_tool.json['result']['bonus']>without_tool.json['result']['bonus']
    started=call(client,case,auth_headers,'salvage',path=path)
    assert started.status_code==200,started.json
    assert started.json['result']['kind']=='salvage'


def test_field_engineer_salvage_uses_personal_tool_bonus_in_quote_and_job(client, salvage_workshop, auth_headers):
    case = salvage_workshop
    data = deepcopy(case['actor_character'].data)
    data.setdefault('basic', {})['background'] = {'ruleId': 'background-z'}
    data['inventory']['pockets'][3]['name'] = 'Нож'
    case['actor_character'].data = data
    db.session.commit()
    selected = {'path': ['inventory', 'pockets', 4], 'tool_path': ['inventory', 'pockets', 3]}
    without = call(client, case, auth_headers, 'quote_salvage', path=selected['path'])
    quote = call(client, case, auth_headers, 'quote_salvage', **selected)
    assert quote.status_code == 200, quote.json
    assert quote.json['result']['bonus'] - without.json['result']['bonus'] == 8
    snapshot = client.get(f"/lobbies/characters/{case['actor_character'].id}/crafting", headers=auth_headers(case['actor_user']))
    assert snapshot.status_code == 200, snapshot.json
    knife = next(entry for entry in snapshot.json['inventory'] if entry['path'] == selected['tool_path'])
    assert knife['salvage_tool_bonus'] == 8
    started = call(client, case, auth_headers, 'salvage', **selected)
    assert started.status_code == 200, started.json
    assert started.json['result']['bonus'] == quote.json['result']['bonus']


def test_contents_and_tool_self_scrapping_are_rejected(salvage_workshop):
    data=deepcopy(salvage_workshop['actor_character'].data)
    data['inventory']['pockets'][4]['installedModules']=[{'name':'Прицел'}]
    with pytest.raises(ValidationError,match='извлеките'):plan(data,{'path':['inventory','pockets',4],'tool_path':['inventory','pockets',3]})


def test_ammo_powder_half_is_rounded_only_after_multiplier():
    job={'salvage_kind':'ammo','bonus':0,'quantity':30,'escrow':[{'item':{'name':'9х18','itemClass':'1'}}]}
    result=resolve({},job,rng=Die(80))
    assert result['materials']==[{'name':'Металлические детали','quantity':1}]


def test_armor_inherits_previous_band_without_duplicate_rows():
    job={'salvage_kind':'armor','bonus':0,'modifications':0,'escrow':[{'item':{'name':'Костюм'}}]}
    result=resolve({},job,rng=Die(108))
    assert {m['name']:m['quantity'] for m in result['materials']}=={'Тканные материалы':13,'Металлические детали':4,'Резина':4}


def test_unfilled_high_band_uses_last_defined_result():
    job={'salvage_kind':'armor','bonus':100,'modifications':0,'escrow':[{'item':{'name':'Костюм'}}]}
    result=resolve({},job,rng=Die(100))
    assert result['total']==200
    decided=resolve({},job,rng=Die(100),band_override=8)
    assert result['source_band']==decided['source_band'] and result['materials']==decided['materials']
    assert not result.get('awaiting_gm')


def test_low_empty_ammo_band_stays_empty():
    job={'salvage_kind':'ammo','bonus':0,'quantity':20,'escrow':[{'item':{'name':'9х18','itemClass':'1'}}]}
    result=resolve({},job,rng=Die(20))
    assert result['total']==20 and result['materials']==[]


def test_utilizer_failure_does_not_wait_for_an_empty_table_row():
    job={'salvage_kind':'armor','bonus':100,'modifications':0,'escrow':[{'item':{'name':'Костюм'}}]}
    data={'basic':{'background':{'name':'Утилизатор','ruleId':'background-o'}}}
    result=resolve(data,job,rng=Die(1,100))
    assert result['salvage_lost'] and not result.get('awaiting_gm') and not result['materials']


@pytest.mark.parametrize('background', [{'name':'Утилизатор'}, {'ruleId':'background-o'}])
@pytest.mark.parametrize('risk,lost', [(10,True),(11,False)])
def test_utilizer_background_risk_and_yield(background,risk,lost):
    job={'salvage_kind':'weapon','bonus':0,'modifications':1,
         'escrow':[{'item':{'name':'Пистолет'}}]}
    result=resolve({'basic':{'background':background}},job,rng=Die(risk,31))
    assert result['roll']==31
    assert result['salvage_lost'] is lost
    assert result['materials']==([] if lost else [{'name':'Металлические детали','quantity':5}])


def test_custom_trait_with_background_name_does_not_grant_utilizer_yield():
    job={'salvage_kind':'weapon','bonus':0,'modifications':0,
         'escrow':[{'item':{'name':'Пистолет'}}]}
    data={'features':{'specialTraits':[{'name':'Утилизатор'}]}}
    result=resolve(data,job,rng=Die(31))
    assert result['materials']==[{'name':'Металлические детали','quantity':3}]


@pytest.mark.parametrize('traits,roll,expected', [
    ([{'name':'Руки-ножи'}],30,3),
    ([{'ruleId':'trait-b10'}],30,3),
    ([{'name':'Торопыга'}],31,2),
    ([{'ruleId':'trait-b38'}],31,2),
    ([{'name':'Торопыга'}],1,2),
    ([{'name':'Руки-ножи'}],150,20),
    ([{'name':'Руки-ножи'},{'name':'Торопыга'}],31,3),
    ([{'name':'Руки-ножи','active':False}],30,2),
])
def test_salvage_category_traits_shift_table_row(traits,roll,expected):
    data={'features':{'specialTraits':traits}}
    job={'salvage_kind':'weapon','bonus':0,'modifications':0,'escrow':[{'item':{'name':'Пистолет'}}]}
    result=resolve(data,job,rng=Die(roll))
    assert result['roll']==roll and result['total']==roll
    assert result['materials']==[{'name':'Металлические детали','quantity':expected}]


def test_utilizer_ammo_totals_are_rounded_after_combining_materials():
    job={'salvage_kind':'ammo','bonus':0,'quantity':50,
         'escrow':[{'item':{'name':'9х18','itemClass':'1'}}]}
    data={'basic':{'background':{'ruleId':'background-o'}}}
    result=resolve(data,job,rng=Die(11,80))
    # 0,5 пороха из строки и 1 за количество суммируются до умножения на1,5.
    assert {m['name']:m['quantity'] for m in result['materials']}=={'Металлические детали':3,'Порох':2}


@pytest.mark.parametrize('risk', [10,11])
def test_actual_utilizer_job_consumes_source_and_finishes_once(client,salvage_workshop,auth_headers,monkeypatch,risk):
    case=salvage_workshop
    data=deepcopy(case['actor_character'].data)
    data.setdefault('basic',{})['background']={'ruleId':'background-o'}
    case['actor_character'].data=data
    db.session.commit()
    started=call(client,case,auth_headers,'salvage',path=['inventory','pockets',4])
    assert started.status_code==200,started.json
    job=started.json['result']
    case['lobby'].game_time_minutes+=20
    db.session.commit()
    original=resolve
    baseline=original({},job,rng=Die(100))
    calls=[]

    def fixed(data,job):
        calls.append(1)
        return original(data,job,rng=Die(risk,100))

    monkeypatch.setattr('app.services.salvage.resolve',fixed)
    finished=call(client,case,auth_headers,'finish',job_id=job['id'])
    assert finished.status_code==200,finished.json
    result=finished.json['result']
    assert result['roll']==100
    assert result['salvage_lost'] is (risk==10)
    expected=[] if risk==10 else [{'name':m['name'],'quantity':int(m['quantity']*1.5)} for m in baseline['materials']]
    assert result['materials']==expected
    assert not any(item.get('id')=='scrap-gun' for item in finished.json['data']['inventory']['pockets'])
    repeated=call(client,case,auth_headers,'finish',job_id=job['id'],character_revision=0)
    assert repeated.status_code==200,repeated.json
    assert repeated.json['result']['replayed'] and len(calls)==1
    assert repeated.json['data']['inventory']==finished.json['data']['inventory']


@pytest.mark.parametrize('lost', [False,True])
def test_legacy_saved_utilizer_roll_keeps_roll_and_risk(client,salvage_workshop,auth_headers,lost):
    case=salvage_workshop
    data=deepcopy(case['actor_character'].data)
    data.setdefault('basic',{})['background']={'name':'Утилизатор'}
    case['actor_character'].data=data
    db.session.commit()
    started=call(client,case,auth_headers,'salvage',path=['inventory','pockets',4])
    assert started.status_code==200,started.json
    data=deepcopy(case['actor_character'].data)
    data['crafting']['current']['salvage_roll']={'roll':31,'salvage_lost':lost,'awaiting_gm':True}
    case['actor_character'].data=data
    case['lobby'].game_time_minutes+=20
    db.session.commit()
    # Старая отложенная работа сохраняет оба броска при выборе строки ГМом.
    finished=call(client,case,auth_headers,'finish',job_id=started.json['result']['id'],salvage_band=0)
    assert finished.status_code==200,finished.json
    assert finished.json['result']['roll']==31
    assert finished.json['result']['salvage_lost'] is lost
    assert finished.json['result']['materials']==([] if lost else [{'name':'Металлические детали','quantity':3}])


def test_high_result_finishes_once_without_gm_ruling(client,salvage_workshop,auth_headers,monkeypatch):
    case=salvage_workshop
    db.session.add_all([ItemTemplate(name=name,category='crafting_material',price=100,weight=.5,volume=1,attributes={}) for name in ('Тканные материалы','Резина')])
    data=deepcopy(case['actor_character'].data);data['skills']['other']['engineering']={'base':20}
    data['inventory']['pockets'][4]={'id':'uncertain-armor','name':'Костюм','category':'armor','itemClass':'Н','stage':1,
        'durability':100,'maxDurability':100,'quantity':1,'attributes':{'modification_category':'Лёгкая'}}
    case['actor_character'].data=data;db.session.commit()
    original=resolve;calls=[]
    def fixed(data,job,**kwargs):calls.append(1);return original(data,job,**{'rng':Die(100),**kwargs})
    monkeypatch.setattr('app.services.salvage.resolve',fixed)
    started=call(client,case,auth_headers,'salvage',path=['inventory','pockets',4],tool_path=['inventory','pockets',3])
    assert started.status_code==200,started.json
    job=started.json['result'];case['lobby'].game_time_minutes+=30;db.session.commit()
    finished=call(client,case,auth_headers,'finish',job_id=job['id'],operation_id='high-result')
    assert finished.status_code==200,finished.json
    assert finished.json['result']['roll']==100 and finished.json['result']['materials']
    assert 'current' not in finished.json['data']['crafting']
    again=call(client,case,auth_headers,'finish',job_id=job['id'],operation_id='high-result',character_revision=0)
    assert again.status_code==200 and again.json['result']['replayed'] and len(calls)==1
