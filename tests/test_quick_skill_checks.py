"""Обычные броски из листа используют текущую погоду и экипировку сервера."""
from copy import deepcopy

import pytest

from app.extensions import db
from app.models import LocationObject,Location,LocationCharacter
from app.services.combat import CombatService
from test_character_interactions import _setup_pair


@pytest.fixture
def checks(client,create_user,auth_headers,monkeypatch):
    pair=_setup_pair(client,create_user,auth_headers)
    data=deepcopy(pair['actor_character'].data)
    data['skills']['physical'].update(awareness={'base':10},shooting={'base':10})
    data['equipment']={'helmet':{'installedModules':[{'slotType':'nvg','attributes':{'night_vision':True,'accuracy_penalty':-2,'awareness_penalty':-2}}]},
                       'headphones':{'attributes':{'awareness_bonus':3}}}
    pair['actor_character'].data=data
    pair['lobby'].game_time_minutes=0
    pair['lobby'].weather_settings={'sun':{'enabled':True,'intensity':.5},'fog':{'enabled':True,'intensity':.5},'rain':{'enabled':False,'intensity':0}}
    db.session.commit()
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:10)
    return pair


def run(client,checks,auth_headers,**changes):
    body={'operation_id':'quick-once','skill_path':'skills.physical.awareness.visual','difficulty':None,
          'location_id':checks['location'].id,**changes}
    return client.post(f"/lobbies/characters/{checks['actor_character'].id}/skill-check",
                      headers=auth_headers(checks['actor_user']),json=body)


def test_stealth_check_reports_continuous_his_conversation_sound(client,checks,auth_headers):
    data=deepcopy(checks['actor_character'].data)
    data['skills'].setdefault('other',{})['stealth']={'base':10,'bonus':0}
    data['inventory']['pockets']=[{'id':'his-stealth','name':'ХИС',
        'glowState':{'activated':True,'remaining_seconds':900}}]
    checks['actor_character'].data=data;db.session.commit()
    result=run(client,checks,auth_headers,operation_id='his-stealth-check',
        skill_path='skills.other.stealth',difficulty=10)
    assert result.status_code==200,result.json
    check=result.json['check']
    assert check['ambient_noise_sources'][0]['kind']=='conversation'
    assert any('ХИС' in line for line in check['rules'])


@pytest.mark.parametrize('path,equipment,weather',[
    ('skills.physical.awareness.visual',-2,-3),('skills.physical.shooting',-2,-2),
    ('skills.physical.awareness.hearing',3,-2),
])
def test_quick_roll_uses_channel_nvg_and_weather_without_inventing_a_dc(client,checks,auth_headers,path,equipment,weather):
    response=run(client,checks,auth_headers,skill_path=path)
    assert response.status_code==200,response.json
    check=response.json['check']
    assert check['difficulty'] is None and check['success'] is None
    assert check['equipment_modifier']==equipment and check['weather_modifier']==weather
    assert check['total']==10+equipment+weather
    assert not check['disadvantage']
    assert checks['actor_location'].action_points_current==5


def test_indoor_quick_roll_does_not_receive_outdoor_weather(client,checks,auth_headers):
    db.session.add(LocationObject(location_id=checks['location'].id,type='floor',name='Пол комнаты',tile_x=1,tile_y=1,
        properties={'building_id':'test-room','dimensions':{'width':4,'depth':4,'height':.2},'level':0}))
    db.session.commit()
    response=run(client,checks,auth_headers)
    assert response.status_code==200,response.json
    assert response.json['check']['weather_modifier']==0


def test_quick_roll_is_replayed_once_and_does_not_apply_success_failure_consequences(client,checks,auth_headers,monkeypatch):
    calls=[]
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:calls.append((a,b)) or 10)
    monkeypatch.setattr(CombatService,'_apply_stress_check_consequences',lambda *a:pytest.fail('Для броска без СЛ нельзя придумывать успех или провал'))
    messages=[]
    monkeypatch.setattr('app.lobbies._emit_lobby_chat_message',lambda *a,**kw:messages.append(a[2]))
    first=run(client,checks,auth_headers)
    second=run(client,checks,auth_headers)
    assert first.status_code==second.status_code==200
    assert second.json['replayed'] is True and first.json['check']==second.json['check']
    assert len(calls)==1 and len(messages)==1
    assert 'None' not in messages[0] and 'провал' not in messages[0]


@pytest.mark.parametrize('changes',[
    {'skill_path':'skills.social.persuasion'},{'target_character_id':2},{'skill_path':[]},
    {'location_id':True},{'location_id':999999},
])
def test_invalid_context_does_not_produce_a_quick_roll(client,checks,auth_headers,changes):
    before=deepcopy(checks['actor_character'].data)
    response=run(client,checks,auth_headers,**changes)
    assert response.status_code in {400,404},response.json
    assert checks['actor_character'].data==before


def test_explicit_scene_selects_weather_instead_of_another_placement(client,checks,auth_headers):
    location=Location(lobby_id=checks['lobby'].id,name='Вторая сцена',world_tile_x=5,world_tile_z=5)
    db.session.add(location);db.session.flush()
    db.session.add(LocationCharacter(location_id=location.id,character_id=checks['actor_character'].id,pos_x=1,pos_y=1))
    db.session.add(LocationObject(location_id=location.id,type='floor',name='Пол второй комнаты',tile_x=1,tile_y=1,
        properties={'building_id':'other-room','dimensions':{'width':4,'depth':4,'height':.2},'level':0}))
    db.session.commit()
    first=run(client,checks,auth_headers)
    second=run(client,checks,auth_headers,operation_id='other-scene',location_id=location.id)
    assert first.status_code==second.status_code==200
    assert first.json['check']['weather_modifier']==-3
    assert second.json['check']['weather_modifier']==0


def test_sheet_save_cannot_erase_the_result_needed_for_retry(client,checks,auth_headers,monkeypatch):
    calls=[]
    monkeypatch.setattr('app.services.combat.random.randint',lambda a,b:calls.append(1) or 10)
    response=run(client,checks,auth_headers)
    assert response.status_code==200
    stale=deepcopy(response.json['data']);stale['health']['combatMeta']['socialCheckReceipts']=[]
    changed=client.put(f"/lobbies/characters/{checks['actor_character'].id}",headers=auth_headers(checks['actor_user']),json={'data':stale})
    assert changed.status_code==200,changed.json
    repeated=run(client,checks,auth_headers)
    assert repeated.status_code==200 and repeated.json['replayed']
    assert len(calls)==1


@pytest.mark.parametrize('bonus,expected',[(0,-5),(3,-2),(5,0)])
@pytest.mark.parametrize('path,stat',[('skills.physical.shooting','darkness_accuracy'),('skills.physical.awareness.visual','darkness_awareness')])
def test_night_without_nvg_is_numeric_and_owl_reduces_it(client,checks,auth_headers,path,stat,bonus,expected):
    data=deepcopy(checks['actor_character'].data)
    data['equipment'].pop('helmet')
    data['health'].setdefault('combatMeta',{})['consumableModifiers']=[{'stat':stat,'value':bonus,'remaining':240}]
    checks['actor_character'].data=data;db.session.commit()
    response=run(client,checks,auth_headers,skill_path=path)
    assert response.status_code==200,response.json
    check=response.json['check']
    assert check['equipment_modifier']==expected
    assert not check['disadvantage']


@pytest.mark.parametrize('custom',[False,True])
def test_legacy_local_owl_keeps_remaining_time_and_does_not_rewrite_custom_effect(checks,custom):
    from app.models.lobby_templates import LobbyItemTemplate
    from app.services.consumable_effects import normalize_owl_modifiers
    template=LobbyItemTemplate(lobby_id=checks['lobby'].id,created_by=checks['lobby'].gm_id,
        name='Стимулятор Сова',category='medicine',description='Исходное описание',
        attributes={} if custom else {'catalog_name':'Стимулятор Сова','raw_description':'Исходное описание'})
    db.session.add(template);db.session.flush()
    source=f'consumable-template:{1000000+template.id}'
    modifiers=[{'stat':'darkness_visual_immunity','value':1,'remaining':37,'source':source},
        {'stat':'darkness_awareness','value':3,'remaining':37,'source':source}]
    data={'health':{'combatMeta':{'consumableModifiers':deepcopy(modifiers)}}}
    normalize_owl_modifiers(data)
    result=data['health']['combatMeta']['consumableModifiers']
    if custom:assert result==modifiers
    else:
        assert {m['stat']:m['value'] for m in result}=={'darkness_awareness':3,'darkness_accuracy':3,'light_awareness':-3}
        assert all(m['remaining']==37 for m in result)


@pytest.mark.parametrize('kind,path,minute,expected,equipment',[
    ('hand','skills.physical.awareness.visual',0,-3,0),
    ('hand','skills.physical.shooting',0,-3,0),
    ('improved','skills.physical.awareness.visual',0,0,0),
    ('hand','skills.physical.awareness.visual',720,0,0),
    ('hand','skills.physical.awareness.hearing',0,0,3),
])
def test_illuminated_quick_roll_preserves_portable_light_rules(client,checks,auth_headers,kind,path,minute,expected,equipment):
    source=checks['target_location']
    source.facing_x,source.facing_y=-1,0
    data=deepcopy(source.character.data)
    data['inventory']['pockets']=[{'id':'check-light','name':'Фонарик','attributes':{'portable_light':kind}}]
    data['health'].setdefault('combatMeta',{})['portableLights']=[{'id':'check-light','mount':'hand','on':True}]
    source.character.data=data
    observer=deepcopy(checks['actor_character'].data)
    observer['equipment'].pop('helmet')
    checks['actor_character'].data=observer
    checks['lobby'].game_time_minutes=minute
    db.session.commit()
    response=run(client,checks,auth_headers,skill_path=path)
    assert response.status_code==200,response.json
    check=response.json['check']
    assert check['illumination_modifier']==expected
    assert check['equipment_modifier']==equipment
    assert not check['disadvantage']
    assert check['total']==10+equipment+expected+check['weather_modifier']
