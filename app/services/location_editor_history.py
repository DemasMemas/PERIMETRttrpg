"""Conditional, atomic object edits using the existing location storage and events."""
from copy import deepcopy
import math

from sqlalchemy import cast, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db
from app.models import Location, LocationCharacter
from app.models.location_object import LocationObject
from app.schemas.location import LocationObjectSchema
from app.services.exceptions import ValidationError, ConflictError
from app.services.location_lighting import normalize_location_lighting


def _json_snapshot_matches(column, snapshot, dialect_name):
    # PostgreSQL JSON preserves source text and has no equality operator.
    # Compare as JSONB without migrating the stored columns or losing the CAS.
    if dialect_name == 'postgresql':
        return cast(column, JSONB) == snapshot
    return column == snapshot


def apply_object_edit(location_id, lobby_id, data):
    location = db.session.query(Location).filter_by(id=location_id, lobby_id=lobby_id).with_for_update().first_or_404()
    if not isinstance(data, dict):
        raise ValidationError('Object edit must be an object')
    objects, tiles = data.get('objects', []), data.get('tiles', [])
    terrain_cells, darkness = data.get('cells', []), data.get('darkness', [])
    if not isinstance(objects, list) or not isinstance(tiles, list) or len(objects) > 5000 or len(tiles) > 5000:
        raise ValidationError('Invalid object edit')
    schema = LocationObjectSchema()
    # Existing saves may contain NULL in these nullable model columns. History
    # must round-trip them rather than turn restoration into a validation error.
    for field in ('name', 'type', 'properties'):
        schema.fields[field].allow_none = True
    existing = {o.id: o for o in LocationObject.query.filter_by(location_id=location_id).all()}
    changes, ids, cells = [], set(), set()
    original_tiles, original_lighting = deepcopy(location.tiles_data), deepcopy(location.lighting)
    next_tiles, next_lighting = deepcopy(original_tiles), deepcopy(original_lighting or {})
    lighting_changed = any(key in data for key in ('lights', 'environment')) or bool(darkness)

    def conflict():
        raise ConflictError('Затронутые данные уже изменены. Отмена или повтор не применены; обновите сцену.')

    # Paste and replay must not attach copies to a wall/floor that changed
    # after the preview. Lock dependencies even though this edit doesn't write them.
    checks = data.get('checks', {})
    if not isinstance(checks, dict):
        raise ValidationError('Invalid edit dependencies')
    versions, ground = checks.get('objects', []), checks.get('ground', [])
    if (not isinstance(versions, list) or len(versions) > 5000
            or any(not isinstance(v, list) or len(v) != 2 or any(type(n) is not int for n in v) for v in versions)
            or len({v[0] for v in versions}) != len(versions)):
        raise ValidationError('Invalid dependency revisions')
    if versions:
        locked = LocationObject.query.filter(LocationObject.location_id == location_id,
            LocationObject.id.in_([v[0] for v in versions])).populate_existing().with_for_update().all()
        if sorted([[o.id, o.revision] for o in locked]) != sorted(versions):
            conflict()
    if not isinstance(ground, list) or len(ground) > location.grid_width * location.grid_height:
        raise ValidationError('Invalid ground dependencies')
    ground_seen = set()
    for cell in ground:
        if (not isinstance(cell, dict) or type(cell.get('x')) is not int or type(cell.get('z')) is not int
                or not 0 <= cell['x'] < location.grid_width or not 0 <= cell['z'] < location.grid_height
                or (cell['x'], cell['z']) in ground_seen):
            raise ValidationError('Invalid ground dependency')
        ground_seen.add((cell['x'], cell['z']))
        if original_tiles[cell['z']][cell['x']].get('height') != cell.get('height'):
            conflict()

    for item in objects:
        if not isinstance(item, dict) or type(item.get('id')) is not int or item['id'] in ids:
            raise ValidationError('Expected unique object IDs')
        ident = item['id']; ids.add(ident)
        before, after = item.get('before'), item.get('after')
        obj = existing.get(ident)
        # Also check IDs globally: never restore over an object in another location.
        if before is None:
            if ident > 0 and db.session.get(LocationObject, ident) is not None:
                conflict()
        elif obj is None or schema.dump(obj) != before:
            conflict()
        spec = None
        if after is not None:
            if not isinstance(after, dict):
                raise ValidationError('Invalid object snapshot')
            spec = schema.load({k: v for k, v in after.items() if k not in {'id', 'revision'}})
            if (set(spec) != {'name', 'type', 'tile_x', 'tile_y', 'properties'}
                    or not 0 <= spec['tile_x'] < location.grid_width
                    or not 0 <= spec['tile_y'] < location.grid_height):
                raise ValidationError('Invalid object position or snapshot')
        changes.append((item, obj, spec))
    for item in tiles:
        if not isinstance(item, dict) or type(item.get('x')) is not int or type(item.get('z')) is not int:
            raise ValidationError('Invalid decoration cell')
        x, z = item['x'], item['z']
        if not 0 <= x < location.grid_width or not 0 <= z < location.grid_height or (x, z) in cells:
            raise ValidationError('Invalid or duplicate decoration cell')
        cells.add((x, z))
        if original_tiles[z][x].get('objects', []) != item.get('before'):
            conflict()
        if not isinstance(item.get('after'), list):
            raise ValidationError('Expected decorations array')
        next_tiles[z][x]['objects'] = deepcopy(item['after'])
    def coordinates(item, seen):
        if not isinstance(item, dict) or type(item.get('x')) is not int or type(item.get('z')) is not int:
            raise ValidationError('Invalid cell')
        x, z = item['x'], item['z']
        if not 0 <= x < location.grid_width or not 0 <= z < location.grid_height or (x, z) in seen:
            raise ValidationError('Invalid or duplicate cell')
        seen.add((x, z))
        return x, z

    if not isinstance(terrain_cells, list) or len(terrain_cells) > location.grid_width * location.grid_height:
        raise ValidationError('Invalid cell edit')
    for item in terrain_cells:
        x, z = coordinates(item, cells)
        if original_tiles[z][x] != item.get('before'):
            conflict()
        after = item.get('after')
        if (not isinstance(after, dict) or not isinstance(after.get('terrain'), str)
                or type(after.get('height')) not in (int, float)
                or ('objects' in after and not isinstance(after['objects'], list))
                or ('radiation' in after and (type(after['radiation']) not in (int, float) or not 0 <= after['radiation'] <= 10))):
            raise ValidationError('Invalid terrain snapshot')
        next_tiles[z][x] = deepcopy(after)
    if not isinstance(darkness, list) or len(darkness) > location.grid_width * location.grid_height:
        raise ValidationError('Invalid darkness edit')
    dark = {(t['x'], t['z']) for t in next_lighting.get('dark_tiles', [])}
    seen_dark = set()
    for item in darkness:
        cell = coordinates(item, seen_dark)
        if type(item.get('before')) is not bool or type(item.get('after')) is not bool:
            raise ValidationError('Expected darkness booleans')
        if (cell in dark) != item['before']:
            conflict()
        if item['after']: dark.add(cell)
        else: dark.discard(cell)
    if darkness:
        next_lighting['dark_tiles'] = [{'x': x, 'z': z} for x, z in sorted(dark, key=lambda c: (c[1], c[0]))]
    if 'environment' in data:
        env = data['environment']
        protected = {'local_lights', 'dark_tiles'}
        current_env = {k: v for k, v in next_lighting.items() if k not in protected}
        if not isinstance(env, dict) or current_env != env.get('before'):
            conflict()
        if not isinstance(env.get('after'), dict) or protected.intersection(env['after']):
            raise ValidationError('Invalid environment edit')
        next_lighting = {**env['after'], **{k: v for k, v in next_lighting.items() if k in protected}}
    if 'lights' in data:
        lights = data['lights']
        if not isinstance(lights, dict) or next_lighting.get('local_lights', []) != lights.get('before'):
            conflict()
        next_lighting['local_lights'] = lights.get('after')
    if lighting_changed:
        next_lighting = normalize_location_lighting(next_lighting, location.grid_width, location.grid_height)
    # Reject non-finite values anywhere, including snapshots stored in arbitrary properties.
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValidationError('Expected finite numbers')
        if isinstance(value, dict):
            for v in value.values(): finite(v)
        elif isinstance(value, list):
            for v in value: finite(v)
    finite(data)

    removed = {item['id'] for item, obj, spec in changes if obj and spec is None}
    if removed:
        from app.services.location_navigation import surface, contains, level_of
        remaining = [o for o in existing.values() if o.id not in removed]
        for actor in LocationCharacter.query.filter_by(location_id=location_id).all():
            if any(o.id in removed and o.type == 'floor' and level_of(o) == (actor.floor_level or 0)
                   and contains(o, actor.pos_x + .5, actor.pos_y + .5) for o in existing.values()) or (
                surface(location, list(existing.values()), actor.pos_x, actor.pos_y, actor.floor_level or 0)
                and not surface(location, remaining, actor.pos_x, actor.pos_y, actor.floor_level or 0)):
                raise ValidationError('Сначала переместите персонажей с удаляемой опоры')
            if actor.cover_object_id in removed:
                actor.cover_object_id = None
                actor.weapon_braced = False
                actor.braced_weapon_index = None
    saved, id_map, restored_revisions = [], {}, []
    for item, obj, spec in changes:
        if spec is None:
            if obj: db.session.delete(obj)
        elif obj:
            for key, value in spec.items(): setattr(obj, key, value)
            saved.append(obj)
        else:
            obj = LocationObject(location_id=location_id, **spec)
            if item['id'] > 0: obj.id = item['id']
            db.session.add(obj)
            saved.append(obj)
            id_map[item['id']] = obj
            if item['id'] > 0:
                revision = item['after'].get('revision')
                if type(revision) is not int or revision < 1:
                    raise ValidationError('Restoration requires the original revision')
                restored_revisions.append((obj, revision + 1))
    from app.services.weather_rules import unhide,unhide_tile
    peers=[o for o in existing.values() if o.id not in removed]+[o for o in saved if o.id not in existing]
    for obj in saved:unhide(location,obj,objects=peers)
    for item in tiles:
        x,z=item['x'],item['z']
        unhide_tile(location,next_tiles[z][x],x,z,peers)
        item['after']=deepcopy(next_tiles[z][x]['objects'])
    for item in terrain_cells:
        x,z=item['x'],item['z']
        unhide_tile(location,next_tiles[z][x],x,z,peers)
        item['after']=deepcopy(next_tiles[z][x])
    try:
        # JSON columns have no ORM revision. Compare-and-swap protects against
        # concurrent tile/light writes without changing the save format.
        if tiles or terrain_cells or lighting_changed:
            dialect = db.session.get_bind().dialect.name
            result = db.session.execute(update(Location).where(
                Location.id == location_id,
                _json_snapshot_matches(Location.tiles_data, original_tiles, dialect),
                _json_snapshot_matches(Location.lighting, original_lighting, dialect),
            ).values(tiles_data=next_tiles, lighting=next_lighting).execution_options(synchronize_session=False))
            if result.rowcount != 1: conflict()
        db.session.flush()
        # Reusing the original ID must not reset its revision to 1: a delayed
        # pre-deletion socket event could otherwise replace the restored state.
        for obj, revision in restored_revisions:
            obj.revision = revision
        if restored_revisions:
            db.session.flush()
        payload = {'location_id': location_id, 'objects': schema.dump(saved, many=True),
                   'deleted_ids': list(removed), 'id_map': {str(k): o.id for k, o in id_map.items()},
                   'updates': [{'x': i['x'], 'z': i['z'], 'objects': i['after']} for i in tiles]}
        payload['cells'] = [{'x': i['x'], 'z': i['z'], 'state': i['after']} for i in terrain_cells]
        if lighting_changed:
            payload['lighting'] = next_lighting
            payload['environment_changed'] = 'environment' in data
        db.session.commit()
    except (StaleDataError, IntegrityError):
        db.session.rollback()
        conflict()
    return payload
