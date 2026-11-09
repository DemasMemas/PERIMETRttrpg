"""Два набора данных на одном физическом КПК; файлы не копируются."""
from app.services.exceptions import PermissionDenied, ValidationError

PROFILES={'main':'Основной профиль','decoy':'Ложный профиль'}


def _modification(item):
    from app.services.equipment_modifications import installed
    return next((mod for mod in installed(item) if mod['ruleId']=='pda-b15'), None)


def enabled(item):
    return _modification(item) is not None


def owner_id(item):
    return (_modification(item) or {}).get('profileOwnerId')


def require_owner(item, character_id):
    if not enabled(item):
        raise ValidationError('На КПК нет «Второго профиля»')
    if owner_id(item) != character_id:
        raise PermissionDenied('Переключение профилей доступно только владельцу КПК')


def bind_legacy_owner(item, character_id):
    modification = _modification(item)
    if modification is None:
        raise ValidationError('На КПК нет «Второго профиля»')
    if owner_id(item) is not None:
        raise ValidationError('Владелец второго профиля уже назначен')
    modification['profileOwnerId'] = character_id


def active(item):
    value=(item.get('pdaNetwork') or {}).get('active_profile','main')
    return value if value in PROFILES and enabled(item) else 'main'


def account_id(item,profile=None):
    network=item.get('pdaNetwork') or {};profile=profile or active(item)
    if profile==active(item):return network.get('account_id')
    return (network.get('profile_accounts') or {}).get(profile)


def assign(item,identity):
    network=item.setdefault('pdaNetwork',{})
    if enabled(item) or 'profile_accounts' in network:
        profiles=network.setdefault('profile_accounts',{})
        profiles.setdefault(active(item),network.get('account_id'))
        profiles[active(item)]=identity
    network['account_id']=identity


def switch(item,profile):
    if not enabled(item):raise ValidationError('На КПК нет «Второго профиля»')
    if profile not in PROFILES:raise ValidationError('Выберите основной или ложный профиль')
    network=item.setdefault('pdaNetwork',{})
    network.setdefault('profile_accounts',{})[active(item)]=network.get('account_id')
    network['active_profile']=profile
    network['account_id']=network['profile_accounts'].get(profile)


def file_profile(item,file_id):
    value=((item.get('pdaNetwork') or {}).get('file_profiles') or {}).get(file_id,'main')
    return value if value in PROFILES else 'main'


def assign_file(item,file_id,profile):
    if profile not in PROFILES:raise ValidationError('Выберите профиль для файла')
    item.setdefault('pdaNetwork',{}).setdefault('file_profiles',{})[file_id]=profile


def public(item):
    if not enabled(item):return {}
    return {'profile':active(item),'profiles':[{'id':key,'label':label} for key,label in PROFILES.items()],
        'file_profiles':dict((item.get('pdaNetwork') or {}).get('file_profiles') or {})}
