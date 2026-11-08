"""НПС и мутанты подключаются ко времени только по отдельному выбору ГМа."""
from copy import deepcopy

import pytest

from app.services.character import CharacterService
from test_quick_skill_checks import checks


def make(checks, name, basic):
    return CharacterService.create_character(checks['lobby'].id, checks['actor_user']['id'], name, {
        'basic': basic, 'health': {'current': 700, 'max': 700, 'effects': [{
            'type': 'custom', 'source': 'manual-qa-clock', 'name': 'Проверка времени',
            'remaining': 2, 'remaining_seconds': 120, 'time_unit': 'minute', 'tick': 'time_elapsed',
        }]},
    })


@pytest.mark.parametrize('basic,expected', [({}, True), ({'is_npc': True}, False),
                                         ({'is_mutant': True}, False)])
def test_character_type_sets_time_default_but_does_not_hide_gm_control(client, checks, auth_headers, basic, expected):
    character = make(checks, 'Проверка исходного участия', basic)
    assert character.time_active is expected
    response = client.get(f'/lobbies/{checks["lobby"].id}/characters', headers=auth_headers(checks['actor_user']))
    assert response.status_code == 200
    row = next(item for item in response.json if item['id'] == character.id)
    assert row['time_active'] is expected


def test_npc_and_mutant_clocks_pause_until_gm_opts_them_in_and_can_be_disabled_again(client, checks, auth_headers):
    npc = make(checks, 'НПС с выбором времени', {'is_npc': True})
    mutant = make(checks, 'Мутант с выбором времени', {'is_mutant': True})
    endpoint = f'/lobbies/{checks["lobby"].id}'
    gm = auth_headers(checks['actor_user'])
    player = auth_headers(checks['target_user'])

    def clock(minute):
        response = client.patch(endpoint+'/time', headers=gm, json={'game_day': 1, 'game_time_minutes': minute})
        assert response.status_code == 200, response.json

    def remaining(character):
        response = client.get(f'/lobbies/characters/{character.id}', headers=gm)
        assert response.status_code == 200
        return next((e['remaining_seconds'] for e in response.json['data']['health']['effects']
                     if e.get('source') == 'manual-qa-clock'), 0)

    assert not npc.time_active and not mutant.time_active
    clock(1)
    assert remaining(npc) == remaining(mutant) == 120
    refused = client.patch(endpoint+'/characters/time-active', headers=player,
                           json={'character_ids': [npc.id, mutant.id]})
    assert refused.status_code == 403
    assert not npc.time_active and not mutant.time_active
    enabled = client.patch(endpoint+'/characters/time-active', headers=gm,
                           json={'character_ids': [npc.id, mutant.id]})
    assert enabled.status_code == 200, enabled.json
    clock(2)
    assert remaining(npc) == remaining(mutant) == 60
    disabled = client.patch(endpoint+'/characters/time-active', headers=gm, json={'character_ids': [mutant.id]})
    assert disabled.status_code == 200
    clock(3)
    assert remaining(npc) == 60
    assert remaining(mutant) == 0
