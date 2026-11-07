"""Названия обычных станков и проверка набора, указанного для НПС."""

NAMES = (
    'Самогонный Аппарат',
    'Печь',
    'Станок Оружейника',
    'Станок Бронника',
    'Смесительный Станок',
    'Станок Электрика',
)


def validate(value):
    from app.services.exceptions import ValidationError

    if not isinstance(value, list) or len(value) > len(NAMES) or any(
            not isinstance(name, str) or name not in NAMES for name in value
    ) or len(set(value)) != len(value):
        raise ValidationError('Выберите станки механика из каталога без повторов')
    return value


def require(settings, name):
    from app.services.exceptions import ValidationError

    if name not in (settings.get('workbenches') or []):
        raise ValidationError(f'У специалиста нет станка: {name}')


def for_item(category):
    if category in {'weapon', 'melee_weapon'}:
        return 'Станок Оружейника'
    return 'Станок Бронника'
