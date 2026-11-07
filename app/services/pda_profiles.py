"""Два набора данных на одном физическом КПК; файлы не копируются."""
from app.services.exceptions import ValidationError

PROFILES={'main':'Основной профиль','decoy':'Ложный профиль'}


def enabled(item):
    from app.services.equipment_modifications import installed
    return any(mod['ruleId']=='pda-b15' for mod in installed(item))


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
