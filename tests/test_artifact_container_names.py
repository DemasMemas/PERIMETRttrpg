"""Новые названия не меняют свойства и старые экземпляры контейнеров."""
import importlib.util
import json
from pathlib import Path

from app.extensions import db
from app.models.templates import ItemTemplate
from app.services.equipment_catalog import retired_catalog_template


NAMES = ['Блок-А', 'Барьер', 'Щит', 'Стена']
LIMITS = [1, 3, 5, 8]


def test_snapshot_has_four_equipped_containers_and_keeps_storage_variants():
    path = Path(__file__).resolve().parents[1] / 'app/data/equipment_catalog.json'
    templates = json.loads(path.read_text(encoding='utf-8'))['templates']
    equipped = [item for item in templates if item['category'] == 'container'
                and item['attributes'].get('artifact_container_kind') == 'equipped']
    assert [(item['name'], item['attributes']['radiation_limit']) for item in equipped] == list(zip(NAMES, LIMITS))
    assert not any(retired_catalog_template(item) for item in equipped)
    assert retired_catalog_template({'name': 'Барьер', 'category': 'container',
                                     'attributes': {'artifact_container_kind': 'storage'}})
    assert not retired_catalog_template({'name': 'Контейнер для артефактов R-1', 'category': 'container',
                                         'attributes': {'artifact_container_kind': 'storage'}})


def test_migration_renames_only_equipped_templates_without_changing_ids_or_properties(app, monkeypatch):
    from app.services.character_starting_purchase import item_from_template
    from app.services.crafting_rules import catalog
    from app.services.equipment_catalog import canonical_catalog_templates
    from app.services.item_template_lookup import resolve_item_template
    from app.services.npc_loot import candidates

    old = [ItemTemplate(name=f'Контейнер для артефактов {level} ур', category='container',
                        price=level * 1000, attributes={'import_source': 'equipment_workbook',
                            'artifact_container_kind': 'equipped', 'radiation_limit': limit,
                            'manual_note': 'сохранить'})
           for level, limit in enumerate(LIMITS, 1)]
    storage = [ItemTemplate(name=name, category='container', price=777,
                            attributes={'artifact_container_kind': 'storage'}) for name in NAMES]
    db.session.add_all([*old, *storage]); db.session.flush()
    identities = [item.id for item in old]
    saved = [item_from_template(item) for item in old]
    path = Path(__file__).resolve().parents[1] / 'migrations/versions/d51a8b2e7c40_rename_artifact_containers.py'
    spec = importlib.util.spec_from_file_location('artifact_container_migration', path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    monkeypatch.setattr(migration.op, 'get_bind', lambda: db.session.connection())
    migration.upgrade(); migration.upgrade(); db.session.expire_all()
    for item, identity, name, limit in zip(old, identities, NAMES, LIMITS):
        assert (item.id, item.name, item.attributes['radiation_limit'], item.attributes['manual_note']) == (
            identity, name, limit, 'сохранить')
    assert [(item.name, item.price) for item in storage] == [(name, 777) for name in NAMES]
    for previous, name, limit in zip(saved, NAMES, LIMITS):
        linked = resolve_item_template(previous)
        assert (linked.id, linked.name, linked.attributes['radiation_limit']) == (previous['templateId'], name, limit)
        assert previous['attributes']['radiation_limit'] == limit
    assert [item.id for item in canonical_catalog_templates([*old, *storage])] == identities
    loot = candidates([*old, *storage], {'category': 'container', 'subcategory': '',
        'item_class': '', 'min_price': 1, 'max_price': 5000})
    assert {item.id for item in loot} == set(identities)
    former_names = {item['name'] for item in saved}
    for recipe in catalog()['recipes']:
        assert recipe['output_name'] not in former_names
        assert not any(name in recipe['components_text'] for name in former_names)
