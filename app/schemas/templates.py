from marshmallow import Schema, fields, post_dump

class ItemTemplateSchema(Schema):
    @post_dump
    def weapon_compatibility(self, data, **kwargs):
        from app.services.weapon_catalog import normalize_template
        return normalize_template(data)

    id = fields.Int(dump_only=True)
    name = fields.Str(required=True)
    category = fields.Str(required=True)
    subcategory = fields.Str(load_default=None)
    item_class = fields.Str(load_default=None)
    description = fields.Str(load_default='')
    price = fields.Int(load_default=0)
    weight = fields.Float(load_default=0.0)
    volume = fields.Float(load_default=0.0)
    attributes = fields.Dict(load_default=dict)
    compatible_ids = fields.List(fields.Int(), load_default=list)
    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)
