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
    data={'features':{'specialTraits':[{'name':'Утилизатор','active':True}]}}
    result=resolve(data,job,rng=Die(1,100))
    assert result['salvage_lost'] and not result.get('awaiting_gm') and not result['materials']


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
