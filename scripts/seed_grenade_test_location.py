"""Создание отдельного полигона гранат без изменения существующих сцен."""
import argparse
from copy import deepcopy
import json
import math
import uuid

from app import create_app
from app.constants import CHUNK_SIZE
from app.extensions import db
from app.models import Location, LocationCharacter, LocationObject, Lobby, MapChunk
from app.models.character import LobbyCharacter
from app.models.templates import ItemTemplate
from app.services.health import apply_health_maximums, health_zones_to_location
from app.services.location_lighting import normalize_location_lighting

SCENE_NAME = 'Гранаты — проверочный полигон'
SIZE = 96
DESCRIPTION = (
    'Девять секторов по 32 м. Северный ряд: открытый взрыв (16,16), укрытия 20/40/60 (48,16), '
    'позы и живой щит (80,16). Средний: дым/газ и проёмы (16,48), огонь/термозащита/вода (48,48), '
    'два этажа и пролом (80,48). Южный: гранатомёты (16,80), вспышка/противогаз (48,80), '
    'снабжение и решение ГМа о цели вне боя (80,80). Начните бой только с персонажами выбранного сектора. '
    'Задержки, травмы, тушение и расход боеприпасов проверяются обычными игровыми действиями. '
    'Персонажи и разрушения не восстанавливаются автоматически.'
)


def scene_layout():
    tiles = [[{'terrain': 'grass', 'height': 1, 'objects': [], 'radiation': 0}
              for _ in range(SIZE)] for _ in range(SIZE)]
    for y in range(SIZE):
        for x in range(SIZE):
            if 4 <= x % 32 <= 28 and 4 <= y % 32 <= 28:
                tiles[y][x]['terrain'] = 'rock'
            if 54 <= x <= 59 and 53 <= y <= 58:
                tiles[y][x]['terrain'] = 'water'
    objects = []
    def add(name,kind,x,y,w,d,h,**props):
        if isinstance(props.get('cover_class'),int):
            props['cover_class']={20:'medium',40:'strong',60:'very_strong'}[props['cover_class']]
        objects.append({'name':name,'type':kind,'tile_x':x,'tile_y':y,'properties':{
            'dimensions':{'width':w,'depth':d,'height':h}, 'color':'#8d8778', **props}})
    for x,cover in ((42,20),(48,40),(54,60)):
        add(f'Полное укрытие {cover}', 'wall',x,16,3,.25,2.4,cover_class=cover)
        add(f'Низкое укрытие {cover}', 'wall',x,22,3,.25,.8,cover_class=cover)
    def room(x,y,building,level=0):
        add('Пол комнаты','floor',x,y,12,12,.12,building_id=building,level=level,cover_class=60)
        for dx,dy,w,rotation in ((0,-6,12,0),(0,6,12,0),(-6,0,12,math.pi/2),(6,0,12,math.pi/2)):
            add('Стена комнаты','wall',x+dx,y+dy,w,.25,2.4,building_id=building,level=level,rotation=rotation,cover_class=40)
    room(16,48,'grenade-cloud-room')
    add('Дверь дыма/газа','door',16,54,1.4,.2,2.1,is_open=False,building_id='grenade-cloud-room')
    add('Окно дыма/газа','window',10,48,1.5,.2,1.2,elevation=.9,rotation=math.pi/2,is_open=False,building_id='grenade-cloud-room')
    add('Перегородка с обходом','wall',16,48,7,.2,2.4,cover_class=20,building_id='grenade-cloud-room')
    room(80,48,'grenade-storeys')
    room(80,48,'grenade-storeys',1)
    add('Межэтажный пролом','floor_opening',82,48,2,2,.12,building_id='grenade-storeys',level=1)
    add('Лестница на второй этаж','stairs',76,48,1.5,4,2.4,building_id='grenade-storeys',level=0,to_level=1)
    add('Вход двухэтажного дома','door',80,54,1.4,.2,2.1,is_open=True,building_id='grenade-storeys')
    add('Мост над водой','floor',56,55,2,6,.15,elevation=1,cover_class=20)
    room(48,80,'grenade-flash-room')
    add('Дверь вспышки','door',48,74,1.4,.2,2.1,is_open=False,building_id='grenade-flash-room')
    add('Окно вспышки','window',54,80,1.5,.2,1.2,elevation=.9,rotation=math.pi/2,is_open=True,building_id='grenade-flash-room')
    for x,y in ((77,76),(80,76),(83,76)):
        add('Ящик снабжения','military_crate',x,y,1,.8,.65,contents=[])
    return tiles,objects


def template_item(template,quantity=1):
    attrs=deepcopy(template.attributes or {})
    item={'id':uuid.uuid4().hex,'templateId':template.id,'name':template.name,'category':template.category,
          'subcategory':template.subcategory,'weight':template.weight,'volume':template.volume,
          'quantity':quantity,'attributes':attrs}
    for key in ('accuracy','range','caliber','ergonomics','damage','fire_rate','min_strength','protection','fixedMagazine'):
        if key in attrs:item[key]=deepcopy(attrs[key])
    if attrs.get('max_durability'):
        item['durability']=item['maxDurability']=attrs['max_durability']
    if attrs.get('uses'):
        item['uses']=item['maxUses']=attrs['uses']
    return item


def populate(location,lobby,templates):
    def item(identifier,quantity=1):return template_item(templates[identifier],quantity)
    def person(name,x,y,*,armor=False,thermal=None,gas=False,launcher=None,level=0,posture='standing',supplies=False):
        data={'basic':{'species':'Человек','is_npc':True},'is_npc':True,
              'skills':{'physical':{key:{'base':14 if launcher else 10} for key in ('strength','agility','will','accuracy','shooting')},
                        'other':{'tactics':{'base':10}}},
              'health':{'current':700,'max':700,'painLevel':0,'stress':0,'effects':[]},
              'equipment':{},'weapons':[],'inventory':{'pockets':[],'backpack':[]},
              '_grenade_test_scene':SCENE_NAME}
        if armor:
            data['equipment']={'armor':item(561),'helmet':item(582)}
        if thermal is not None:
            armor_item=item(561)
            armor_item['name']=f'Проверочная термозащита {thermal}%'
            armor_item['protection']['thermal']=thermal/100
            data['equipment']['armor']=armor_item
        if gas:
            mask=item(572);fil=item(331)
            mask['installedModules']=[fil]
            mask['filterCharges']=20
            data['equipment']['gasMask']=mask
        if supplies:
            data['inventory']['pockets']=[item(tid,3) for tid in range(156,171)]
            data['inventory']['pockets'].append(item(243,5))
            data['inventory']['backpack']=[item(tid,6) for tid in range(171,177)]
        if launcher:
            weapon=item(launcher)
            ammo_id={517:172,519:173,520:171,521:171}[launcher]
            weapon['fixedAmmo']=[item(ammo_id,6 if launcher==520 else 1)]
            data['weapons']=[weapon]
            data['inventory']['pockets']=[item(tid,5) for tid in range(171,177)]
        apply_health_maximums(data)
        character=LobbyCharacter(lobby_id=lobby.id,owner_id=lobby.gm_id,name=f'Гранаты: {name}',
                                 data=data,visible_to=[],editable_to=[],time_active=False)
        db.session.add(character);db.session.flush()
        db.session.add(LocationCharacter(location_id=location.id,character_id=character.id,pos_x=x,pos_y=y,
            floor_level=level,posture=posture,drawn_weapon_index=0 if launcher else None,
            team_name='Полигон гранат',team_color='#b6a36c',facing_x=0,facing_y=1,
            hp_zones=health_zones_to_location(data['health'])))
    for x,y in ((16,10),(48,10),(80,10),(16,40),(48,40),(80,40),(16,72),(48,72),(80,72)):
        person(f'испытатель {x}:{y}',x,y,armor=True,supplies=True)
    for name,x,y in (('открытая 1 м',17,16),('открытая 3 м',16,19),('открытая 6 м',10,16),('открытая 10 м',16,6)):
        person(name,x,y)
    for x,cover in ((42,20),(48,40),(54,60)):
        person(f'за укрытием {cover}',x,18,armor=True)
    person('стоит',76,18);person('сидит',80,18,posture='sitting');person('лежит',84,18,posture='prone')
    person('живой щит',79,21);person('за живым щитом',79,22)
    person('в газовой комнате',13,50);person('за дверью газа',16,56)
    for x,thermal in ((43,0),(46,35),(49,40),(52,65)):
        person(f'термозащита {thermal}%',x,48,thermal=thermal,supplies=True)
    person('под целым перекрытием',78,48);person('на целом перекрытии',78,48,level=1)
    person('под проломом',82,48);person('рядом с проломом',84,48,level=1)
    for x,launcher in ((10,517),(14,519),(18,520),(22,521)):
        person(f'гранатомёт {templates[launcher].name}',x,80,armor=True,launcher=launcher)
    person('цель гранатомётов',16,94,armor=True)
    person('вспышка без защиты',46,80);person('противогаз с фильтром',50,80,gas=True)
    person('цель вне очереди',80,82,armor=True)


def create(lobby_id,apply=False):
    lobby=db.session.get(Lobby,lobby_id)
    if not lobby or lobby.name!='Трава3':raise ValueError('Ожидалась карта «Трава3»')
    existing=Location.query.filter_by(lobby_id=lobby.id,name=SCENE_NAME).first()
    if existing:return {'location_id':existing.id,'created':False,'name':existing.name}
    x=y=280
    if not MapChunk.query.filter_by(lobby_id=lobby.id,chunk_x=x//CHUNK_SIZE,chunk_y=y//CHUNK_SIZE).first():
        raise ValueError('Чанк для полигона отсутствует; карта не изменена')
    if any(abs(o.world_tile_x-x)<=o.world_radius and abs(o.world_tile_z-y)<=o.world_radius for o in lobby.locations):
        raise ValueError('Точка карты уже занята другой подлокацией')
    ids=set(range(156,177))|{243,331,561,582,572,517,519,520,521}
    templates={t.id:t for t in ItemTemplate.query.filter(ItemTemplate.id.in_(ids)).all()}
    if set(templates)!=ids:raise ValueError('Отсутствуют нужные шаблоны снаряжения')
    manifest={'name':SCENE_NAME,'world_tile':[x,y],'size':[SIZE,SIZE],'created':False}
    if not apply:return manifest
    tiles,objects=scene_layout()
    location=Location(lobby_id=lobby.id,name=SCENE_NAME,description=DESCRIPTION,type='battle',
        grid_width=SIZE,grid_height=SIZE,tiles_data=tiles,world_tile_x=x,world_tile_z=y,world_radius=0,
        lighting=normalize_location_lighting({'mode':'manual','ambient_intensity':.65,'directional_intensity':.8,
            'directional_elevation':58,'directional_azimuth':50},SIZE,SIZE),spawn_points=[{'x':80,'y':72}])
    db.session.add(location);db.session.flush()
    db.session.add_all([LocationObject(location_id=location.id,**obj) for obj in objects])
    populate(location,lobby,templates)
    db.session.commit()
    return {**manifest,'created':True,'location_id':location.id,
            'characters':LocationCharacter.query.filter_by(location_id=location.id).count(),
            'objects':LocationObject.query.filter_by(location_id=location.id).count()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lobby-id',type=int,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    app=create_app()
    with app.app_context():
        try:print(json.dumps(create(args.lobby_id,args.apply),ensure_ascii=True))
        finally:db.session.rollback();db.session.remove();db.engine.dispose()


if __name__=='__main__':main()
