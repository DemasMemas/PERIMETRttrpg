from marshmallow import Schema, fields, validate, post_dump
from app.utils.defaults import default_location_lighting

class LocationCreateSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=1, max=100))
    preset = fields.Str(validate=validate.OneOf(['swamp_bank']))
    description = fields.Str(load_default='')
    type = fields.Str(load_default='exploration', validate=validate.OneOf(['battle', 'social', 'exploration']))
    grid_width = fields.Int(load_default=20, validate=validate.Range(min=5, max=200))
    grid_height = fields.Int(load_default=20, validate=validate.Range(min=5, max=200))
    tiles_data = fields.List(fields.List(fields.Dict()), load_default=list)
    lighting = fields.Dict(load_default=default_location_lighting)
    world_tile_x = fields.Int(required=True)
    world_tile_z = fields.Int(required=True)
    world_radius = fields.Int(load_default=0)
    spawn_points = fields.List(fields.Dict(), load_default=list)

class LocationSchema(Schema):
    id = fields.Int(dump_only=True)
    lobby_id = fields.Int(dump_only=True)
    name = fields.Str()
    description = fields.Str()
    type = fields.Str()
    grid_width = fields.Int(load_default=20, validate=validate.Range(min=5, max=200))
    grid_height = fields.Int(load_default=20, validate=validate.Range(min=5, max=200))
    tiles_data = fields.List(fields.Dict())
    lighting = fields.Dict()
    world_tile_x = fields.Int()
    world_tile_z = fields.Int()
    world_radius = fields.Int()
    spawn_points = fields.List(fields.Dict())
    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)

class LocationObjectSchema(Schema):
    def __init__(self,*args,reveal_locked=False,reveal_stashes=False,**kwargs):
        super().__init__(*args,**kwargs)
        self.reveal_locked=reveal_locked
        self.reveal_stashes=reveal_stashes

    @post_dump
    def protect_contents(self,data,**kwargs):
        return data if self.reveal_locked else public_location_objects(data, reveal_stashes=self.reveal_stashes)

    revision = fields.Int(dump_only=True)
    id = fields.Int(dump_only=True)
    location_id = fields.Int(load_only=True)
    name = fields.Str()
    type = fields.Str()
    tile_x = fields.Int()
    tile_y = fields.Int()
    properties = fields.Dict()


def public_location_objects(value, *, reveal_stashes=False):
    """Редактирование и история остаются полными; сетевой общий снимок скрывает закрытые вещи."""
    if isinstance(value,list):return [public_location_objects(item, reveal_stashes=reveal_stashes) for item in value]
    if not isinstance(value,dict):return value
    props = value.get('properties')
    if isinstance(props, dict) and props.get('stash_mask') and not reveal_stashes:
        return {'id':value.get('id'), 'properties':{'stash_obscured':True}}
    result={key:public_location_objects(item, reveal_stashes=reveal_stashes) for key,item in value.items()}
    props=result.get('properties')
    if isinstance(props,dict):
        props.pop('stash_compartment', None)
        if mask := props.pop('stash_mask', None):
            props['stash_masked'] = True
            if mask.get('mode') == 'buried':
                props['stash_buried'] = True
                props['contents'] = []
        if props.get('locked') and 'contents' in props:
            result['properties']={**props,'contents':[],'contents_hidden':True}
    return result
