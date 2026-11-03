"""Крепкий орешек: ограниченный запас спасений и постоянный минимум боли."""
from copy import deepcopy

from app.services.character_traits import background_is
from app.services.effects import apply_effect_to_health


def survive_wound(data,before_health,zone):
    if not background_is(data,'Крепкий орешек') or float(before_health.get('current',0))<=0:return None
    health=data['health'];meta=health.setdefault('combatMeta',{})
    state=meta.setdefault('toughNut',{'uses':1,'minimumPain':0})
    if int(state.get('uses',0))<=0 or int(state.get('minimumPain',0))>=5:return None
    state['uses']-=1;state['minimumPain']=min(5,int(state.get('minimumPain',0))+1)
    if state['minimumPain']>=5:return None
    health['effects']=deepcopy(before_health.get('effects') or [])
    health['organs']=deepcopy(before_health.get('organs') or {})
    health['current']=max(1,float(health.get('current',0)))
    key={'left_arm':'leftArm','right_arm':'rightArm','left_leg':'leftLeg','right_leg':'rightLeg'}.get(zone,zone)
    part=(health.get('zones') or {}).get(key)
    if isinstance(part,dict) and float(part.get('current',0))<=0:part['current']=1
    health['painLevel']=max(state['minimumPain'],float(health.get('painLevel') or 0))
    apply_effect_to_health(health,{'type':'shock','source':'tough_nut_survival','name':'Болевой шок: Крепкий орешек'})
    return {'survived':True,'uses':state['uses'],'minimumPain':state['minimumPain']}
