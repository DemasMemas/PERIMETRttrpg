"""Оплаченное указание союзнику и одноразовый бонус следующего броска."""
from datetime import datetime,timezone
from copy import deepcopy

from sqlalchemy.orm.attributes import flag_modified
from app.extensions import db
from app.models import CharacterInteractionRequest,LocationCharacter
from app.services.character_traits import background_is
from app.services.exceptions import ValidationError,NotFoundError,PermissionDenied,ConflictError


def prepare(actor,target_id,choice):
    from app.services.combat import CombatService as C
    if not background_is(actor.character.data,'Бригадир'):raise ValidationError('Указания доступны Бригадиру')
    if not isinstance(choice,dict) or set(choice)-{'detailed','text'} or type(choice.get('detailed')) is not bool:raise ValidationError('Выберите вид указания')
    text=' '.join(str(choice.get('text') or '').split())
    if not text or len(text)>500:raise ValidationError('Опишите указание: до 500 символов')
    target=LocationCharacter.query.filter_by(location_id=actor.location_id,character_id=target_id).first()
    if not target or not target.character:raise NotFoundError('Союзник не найден')
    from app.services.character_rules import allied_ids
    if target.id==actor.id or target.character_id not in allied_ids(actor):raise ValidationError('Выберите другого персонажа своей команды или группы')
    if C._character_condition(target.character.data)['state']!='active':raise ValidationError('Союзник должен быть в сознании')
    if CharacterInteractionRequest.query.filter_by(actor_location_character_id=actor.id,target_location_character_id=target.id,kind='order',status='pending').first():raise ConflictError('Предыдущее указание ещё ожидает ответа')
    return target,{'detailed':choice['detailed'],'text':text,'roll_bonus':3,'advantage':choice['detailed']}


def create(actor,user_id,target,payload):
    from app.services.character_interaction import CharacterInteractionService as I
    row=CharacterInteractionRequest(location_id=actor.location_id,actor_location_character_id=actor.id,target_location_character_id=target.id,
        actor_user_id=user_id,target_user_id=I._target_user_id(actor.location,target),kind='order',status='pending',payload=payload)
    db.session.add(row);db.session.flush()
    return I._serialize(row)


def respond(row,user_id,decision):
    from app.services.character_interaction import CharacterInteractionService as I
    from app.services.combat import CombatService as C
    actor=db.session.get(LocationCharacter,row.actor_location_character_id);target=db.session.get(LocationCharacter,row.target_location_character_id)
    if not actor or not target:raise NotFoundError('Участник указания покинул локацию')
    if not I._controlled_by(target,user_id,actor.location.lobby.gm_id==user_id):raise PermissionDenied('Вы не управляете адресатом')
    if decision not in {'accept','decline','stress','provoke'}:raise ValidationError('Выберите ответ на указание')
    target_meta=target.character.data.setdefault('health',{}).setdefault('combatMeta',{})
    if decision=='accept':
        if target_meta.get('brigadierOrder'):raise ConflictError('Уже есть принятое указание до следующего броска')
        target_meta['brigadierOrder']={**deepcopy(row.payload),'source_character_id':actor.character_id,'request_id':row.id}
        row.status='accepted';flag_modified(target.character,'data')
    else:
        row.status='rejected'
        actor_meta=actor.character.data.setdefault('health',{}).setdefault('combatMeta',{})
        actor_meta.setdefault('brigadierRefusals',{})[str(target.character_id)]={'request_id':row.id,'choice_pending':True}
        flag_modified(actor.character,'data')
    row.result={'decision':decision};row.resolved_at=datetime.now(timezone.utc);db.session.commit()
    return I._serialize(row)


def consume(data):
    order=((data.get('health') or {}).get('combatMeta') or {}).pop('brigadierOrder',None)
    if order:
        from flask import has_app_context
        from app.models import LobbyCharacter
        if has_app_context():
            for character in list(db.session.identity_map.values()):
                if isinstance(character,LobbyCharacter) and character.data is data:flag_modified(character,'data')
    return order


def boost_roll(data,rolls,*,advantage=False,disadvantage=False):
    import random
    order=consume(data)
    advantage=bool(advantage or order and order.get('advantage'))
    if not rolls:rolls=[random.randint(1,20)]
    if advantage==disadvantage:rolls=rolls[:1]
    if advantage!=disadvantage and len(rolls)<2:rolls=[*rolls,random.randint(1,20)]
    roll=max(rolls) if advantage and not disadvantage else min(rolls) if disadvantage and not advantage else rolls[0]
    return rolls,roll,3 if order else 0


def refusal(actor,target_id,choice):
    from app.services.combat import CombatService as C
    current=actor.character.data.setdefault('health',{}).setdefault('combatMeta',{})
    entry=current.get('brigadierRefusals',{}).get(str(target_id))
    if choice=='provoked' and entry and entry.get('must_provoke'):
        entry['must_provoke']=False;flag_modified(actor.character,'data');return deepcopy(entry)
    if not entry or not entry.get('choice_pending'):raise ConflictError('Нет нового отказа этого союзника')
    if choice not in {'stress','provoke'}:raise ValidationError('Выберите стресс или провокацию')
    entry.update(choice_pending=False,choice=choice)
    if choice=='stress':entry['stress']=C.apply_stress_trigger(actor,1,trigger='brigadier_refusal')
    else:entry['must_provoke']=True
    flag_modified(actor.character,'data')
    return deepcopy(entry)
