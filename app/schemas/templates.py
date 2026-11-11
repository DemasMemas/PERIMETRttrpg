from marshmallow import Schema, fields, post_dump

class ItemTemplateSchema(Schema):
    @post_dump
    def weapon_compatibility(self, data, **kwargs):
        from app.services.catalog_descriptions import glove_description
        from app.services.weapon_catalog import normalize_template
        gloves = glove_description(data)
        if gloves:
            data['description'] = gloves
        if data.get('category') == 'consumable':
            from app.services.consumable_effects import enrich_imported_consumable
            attributes = data.get('attributes') or {}
            profile = enrich_imported_consumable(attributes, data.get('description') or '')
            if profile is not None:
                data['attributes'] = {**attributes, 'consumable': profile}
        if data.get('name','').casefold() in {'фонарик','улучшенный фонарик','налобный фонарик','налобный фонарь'} and data.get('category')!='weapon_module':
            from app.services.catalog_descriptions import describe_template
            data['description']=describe_template(data)
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
