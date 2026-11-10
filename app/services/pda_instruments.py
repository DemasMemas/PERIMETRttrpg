"""КПК объединяет доступные показания активных приборов своего носителя."""
from app.services.portable_lights import walk


def readings(source):
    if 'pda-b6' not in source['mods']:return None
    from app.services.artifact_effects import device_block_reason
    from app.services.electronics import anomaly_detector_bonus,detector_mount_usable,is_dosimeter,state
    from app.services.combat import CombatService as C
    data=getattr(source.get('character'),'data',None) or {}
    if device_block_reason(data,source['item'],jammed_by=source.get('jammed_by')):return []
    result=[];radiation=None
    for _,item in walk(data,True):
        if not detector_mount_usable(data,item) or device_block_reason(data,item,jammed_by=source.get('jammed_by')):continue
        attrs=item.get('attributes') or {}
        if is_dosimeter(item):
            power=state(item)
            if not power['on'] or power['remaining_seconds']<=0:continue
            if radiation is None:
                position=source['position']
                if position['location_id']:
                    from app.extensions import db
                    from app.models import Location
                    scene=db.session.get(Location,position['location_id'])
                    radiation=C._location_tile_radiation(scene,position['x'],position['y'])
                else:
                    from app.services.map import MapService
                    tile=MapService.get_tile_data(source['character'].lobby_id,position['tile_x'],position['tile_y'])
                    radiation=max(0,C._coerce_float(tile.get('radiation'),0))
            result.append({'name':item.get('name') or 'Дозиметр','kind':'dosimeter','radiation':radiation})
        elif (bonus := anomaly_detector_bonus(data, item, jammed_by=source.get('jammed_by'))) is not None:
            reading={'name':item.get('name') or 'Детектор аномалий','kind':'anomaly'}
            # Характеристики работающего прибора не раскрывают координаты скрытых аномалий.
            if 'avoidance_chance' in attrs:reading['avoidance_chance']=C._coerce_float(attrs['avoidance_chance'],0)
            if bonus or 'bonus' in item or 'bonus' in attrs:reading['bonus']=bonus
            result.append(reading)
    return result
