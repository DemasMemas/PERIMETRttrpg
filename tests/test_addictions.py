import pytest

from app.services import addictions


def test_gm_removal_clears_only_selected_addiction_and_withdrawal():
    health = {
        'addictions': {
            'records': {
                'caffeine': {'key': 'caffeine', 'label': 'Кофеин'},
                'nicotine': {'key': 'nicotine', 'label': 'Никотин'},
            },
            'exposures': {'caffeine': {'dose': 2}, 'nicotine': {'dose': 1}},
        },
        'effects': [
            {'id': 'addiction-withdrawal-caffeine', 'type': 'addiction_withdrawal'},
            {'id': 'other-effect', 'type': 'custom'},
        ],
    }

    addictions.remove_addiction(health, 'caffeine')

    assert set(health['addictions']['records']) == {'nicotine'}
    assert set(health['addictions']['exposures']) == {'nicotine'}
    assert [effect['id'] for effect in health['effects']] == ['other-effect']
from app.services.combat import CombatService
from app.services.effects import advance_timed_effects


@pytest.mark.parametrize(
    ('name', 'price', 'chance'),
    [
        ('Банка пива', 100, 1),
        ('Самокрутка', 25, 5),
        ('Сигареты дешевые', 450, 4),
        ('Сигареты обычные', 600, 3),
        ('Сигареты дорогие', 800, 2),
        ('Сигары', 1500, 1),
        ('Сигареты Египетская сила', 2000, 4),
        ('Сигареты Гибралтарский пролив', 100, 3),
        ('Сигареты Пётр Второй', 100, 2),
    ],
)
def test_alcohol_and_tobacco_addiction_chances(name, price, chance):
    assert addictions.addiction_profile(name, price)['chance'] == chance


def test_stimulants_are_assigned_to_their_rule_groups():
    assert addictions.addiction_profile('Адреналин')['key'] == 'accelerating_stimulants'
    assert addictions.addiction_profile('Стимулятор Болид')['key'] == 'combat_stimulants'
    assert addictions.addiction_profile('Стимулятор Орёл')['key'] == 'combat_stimulants'
    assert addictions.addiction_profile('Стимулятор Бык')['key'] == 'combat_stimulants'
    assert addictions.addiction_profile('Стимулятор Геракл')['key'] == 'combat_stimulants'
    assert addictions.addiction_profile('Стимулятор Гора')['key'] == 'combat_stimulants'
    assert addictions.addiction_profile('Питательный стимулятор') is None


def test_catalog_stimulants_correct_legacy_profiles_and_keep_explicit_edits():
    from app.schemas.templates import ItemTemplateSchema

    old = addictions.addiction_profile('Адреналин')
    assert addictions.resolve_addiction_profile({'addiction': old}, 'Стимулятор Болид')['chance'] == 20
    assert addictions.resolve_addiction_profile({'addiction': None}, 'Стимулятор Воля-Н')['key'] == 'combat_stimulants'
    assert addictions.resolve_addiction_profile({'addiction': old}, 'Питательный стимулятор') is None
    assert addictions.resolve_addiction_profile({'editor_version': 2, 'addiction': old}, 'Стимулятор Болид') == old

    template = {'name': 'Болид (переименованный)', 'category': 'consumable',
                'attributes': {'catalog_name': 'Стимулятор Болид', 'consumable': {'addiction': old}}}
    shown = ItemTemplateSchema().dump(template)
    assert shown['attributes']['consumable']['addiction']['key'] == 'combat_stimulants'
    assert template['attributes']['consumable']['addiction'] == old


def test_saved_profile_string_multiplier_keys_are_used_for_repeat_doses(monkeypatch):
    monkeypatch.setattr(addictions.random, 'random', lambda: 0.99)
    health = {}
    profile = {
        'key': 'custom', 'label': 'Особая зависимость', 'chance': 2,
        'multipliers': {'2': 3}, 'kind': 'medicine',
    }

    addictions.record_exposure(health, 'Переименованный препарат', 0, 100, profile=profile)
    second = addictions.record_exposure(health, 'Переименованный препарат', 0, 105, profile=profile)

    assert second['chance'] == 6


def test_repeated_hawthorn_uses_ten_minute_multiplier(monkeypatch):
    monkeypatch.setattr(addictions.random, 'random', lambda: 0.99)
    health = {}

    first = addictions.record_exposure(health, 'Настойка боярышника', 0, 100)
    second = addictions.record_exposure(health, 'Настойка боярышника', 0, 110)
    reset = addictions.record_exposure(health, 'Настойка боярышника', 0, 121)

    assert first['chance'] == 3
    assert second['dose'] == 2
    assert second['chance'] == 15
    assert reset['dose'] == 1
    assert reset['chance'] == 3


def test_kotik_blocks_a_later_addiction_roll_for_24_hours(monkeypatch):
    monkeypatch.setattr(addictions.random, 'random', lambda: 0)
    health = {}

    block = addictions.record_exposure(
        health, 'Стимулятор Котик', 0, 100,
        addiction_block_hours=24,
    )
    result = addictions.record_exposure(health, 'Борщевик', 0, 101)

    assert block['applicable'] is False
    assert block['blocked_until'] == 1540
    assert result['blocked'] is True
    assert result['acquired'] is False


def test_missing_daily_dose_starts_withdrawal_and_penalizes_rolls(monkeypatch):
    monkeypatch.setattr(addictions.random, 'random', lambda: 0)
    health = {}
    addictions.record_exposure(health, 'Борщевик', 0, 1440)
    addictions.advance_addictions(health, 1, 2)

    changed = addictions.advance_addictions(health, 2, 3)
    record = health['addictions']['records']['borshevik']
    effect = next(effect for effect in health['effects'] if effect['type'] == 'addiction_withdrawal')

    assert changed == ['borshevik']
    assert record['withdrawal_stage'] == 1
    assert record['withdrawal_remaining'] == 27
    assert effect['modifiers']['skills.physical.will'] == 1
    character_data = {'health': health}
    assert CombatService._health_roll_modifier(
        character_data, 'skills.physical.will', include_pain=False,
        include_blood=False, include_psy=False,
    ) == -1


def test_daily_dose_cancels_active_withdrawal(monkeypatch):
    monkeypatch.setattr(addictions.random, 'random', lambda: 0)
    health = {}
    addictions.record_exposure(health, 'Борщевик', 0, 1440)
    addictions.advance_addictions(health, 1, 2)
    addictions.advance_addictions(health, 2, 3)

    addictions.record_exposure(health, 'Борщевик', 0, 3 * 1440)

    record = health['addictions']['records']['borshevik']
    assert record['withdrawal_days'] == 0
    assert not any(effect['type'] == 'addiction_withdrawal' for effect in health['effects'])


def test_hawthorn_intoxication_counts_towards_alcohol_daily_requirement(monkeypatch):
    rolls = iter((0, 0.99, 0.99))
    monkeypatch.setattr(addictions.random, 'random', lambda: next(rolls))
    health = {}
    addictions.record_exposure(
        health, 'Водка', 0, 1440, intoxication=15,
    )

    addictions.record_exposure(
        health, 'Настойка боярышника', 0, 1450, intoxication=25,
    )
    addictions.record_exposure(
        health, 'Настойка боярышника', 0, 1461, intoxication=25,
    )

    record = health['addictions']['records']['alcohol']
    assert record['daily_progress']['intoxication'] == 65
    assert 1 in record['satisfied_days']


def test_recovered_addiction_can_be_acquired_again(monkeypatch):
    monkeypatch.setattr(addictions.random, 'random', lambda: 0)
    health = {
        'addictions': {
            'records': {
                'caffeine': {
                    'key': 'caffeine', 'label': 'Кофеин', 'kind': 'caffeine',
                    'active': False, 'recovered_day': 4,
                },
            },
            'exposures': {},
        },
    }

    result = addictions.record_exposure(health, 'Кофе', 600, 5 * 1440)

    assert result['acquired'] is True
    assert health['addictions']['records']['caffeine']['active'] is True


def test_five_successful_checks_reduce_withdrawal_by_seven_days(monkeypatch):
    monkeypatch.setattr(addictions.random, 'randint', lambda _low, _high: 20)
    health = {
        'addictions': {
            'records': {
                'caffeine': {
                    'key': 'caffeine', 'label': 'Кофеин', 'kind': 'caffeine',
                    'active': True, 'withdrawal_days': 1, 'withdrawal_stage': 1,
                    'withdrawal_remaining': 28,
                },
            },
            'exposures': {},
        },
    }

    results = [addictions.withdrawal_check(health, 'caffeine', 2, -3) for _ in range(5)]

    assert [result['days_reduced'] for result in results] == [0, 0, 1, 2, 4]
    assert results[-1]['remaining'] == 21
    with pytest.raises(ValueError, match='пять проверок'):
        addictions.withdrawal_check(health, 'caffeine', 2, -3)


def test_withdrawal_support_activates_after_one_minute():
    health = {}
    pending = {
        'type': 'withdrawal_support_pending',
        'remaining': 1,
        'remaining_seconds': 60,
        'time_unit': 'minute',
        'tick': 'time_elapsed',
        'activate_effects': [{
            'type': 'withdrawal_support',
            'remaining': 60,
            'remaining_seconds': 3600,
            'time_unit': 'minute',
            'tick': 'time_elapsed',
            'withdrawal_check_difficulty_reduction': 5,
        }],
    }

    effects = advance_timed_effects(health, [pending], 60)

    assert len(effects) == 1
    assert effects[0]['type'] == 'withdrawal_support'
    assert effects[0]['withdrawal_check_difficulty_reduction'] == 5
