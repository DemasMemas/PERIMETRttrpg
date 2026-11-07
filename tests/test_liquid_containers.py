import pytest

from app.services.exceptions import ValidationError
from app.services.liquid_containers import preserve_and_validate


def canister(contents=None):
    return {'id': 'canister-1', 'name': 'Канистра(Пустая)(Объем 15 литров)(45 использований воды)',
            'category': 'misc', 'attributes': {'liquid_capacity_liters': 15},
            'quantity': 1, 'contents': contents or []}


def sheet(item):
    return {'inventory': {'pockets': [item], 'backpack': []}}


def liquid(name='Топливо(1 Литр)', quantity=1):
    return {'id': 'liquid-1', 'name': name, 'category': 'misc', 'quantity': quantity, 'volume': 1}


def test_canister_accepts_fractional_fuel_and_keeps_its_history():
    current = sheet(canister())
    filled = sheet(canister([liquid(quantity=.5)]))
    preserve_and_validate(current, filled)
    assert filled['inventory']['pockets'][0]['fuelUsed'] is True
    assert filled['inventory']['pockets'][0]['isContainer'] is True

    emptied = sheet(canister())
    preserve_and_validate(filled, emptied)
    assert emptied['inventory']['pockets'][0]['fuelUsed'] is True
    with pytest.raises(ValidationError, match='После топлива'):
        preserve_and_validate(emptied, sheet(canister([liquid('Вода(1 Литр)')])))


@pytest.mark.parametrize('contents', [
    [liquid(quantity=15.1)],
    [liquid(name='Металлические детали')],
    [liquid(quantity=.25)],
    [liquid(), liquid(name='Вода(1 Литр)')],
])
def test_canister_rejects_invalid_contents(contents):
    with pytest.raises(ValidationError):
        preserve_and_validate(sheet(canister()), sheet(canister(contents)))
