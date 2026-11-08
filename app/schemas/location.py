from marshmallow import Schema, fields, validate, post_dump
from app.utils.defaults import default_location_lighting

class LocationCreateSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=1, max=100))
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
    def __init__(self,*args,reveal_locked=False,**kwargs):
        super().__init__(*args,**kwargs)
        self.reveal_locked=reveal_locked

    @post_dump
    def protect_contents(self,data,**kwargs):
        return data if self.reveal_locked else public_location_objects(data)

    revision = fields.Int(dump_only=True)
    id = fields.Int(dump_only=True)
    location_id = fields.Int(load_only=True)
    name = fields.Str()
    type = fields.Str()
    tile_x = fields.Int()
    tile_y = fields.Int()
    properties = fields.Dict()


def public_location_objects(value):
    """Редактирование и история остаются полными; сетевой общий снимок скрывает закрытые вещи."""
    if isinstance(value,list):return [public_location_objects(item) for item in value]
    if not isinstance(value,dict):return value
    result={key:public_location_objects(item) for key,item in value.items()}
    props=result.get('properties')
    if isinstance(props,dict) and props.get('locked') and 'contents' in props:
        result['properties']={**props,'contents':[],'contents_hidden':True}
    return result
