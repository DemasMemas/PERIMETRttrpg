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
    old = [ItemTemplate(name=f'Контейнер для артефактов {level} ур', category='container',
                        price=level * 1000, attributes={'import_source': 'equipment_workbook',
                            'artifact_container_kind': 'equipped', 'radiation_limit': limit,
                            'manual_note': 'сохранить'})
           for level, limit in enumerate(LIMITS, 1)]
    storage = ItemTemplate(name='Барьер', category='container', price=777,
                           attributes={'artifact_container_kind': 'storage'})
    db.session.add_all([*old, storage]); db.session.flush()
    identities = [item.id for item in old]
    path = Path(__file__).resolve().parents[1] / 'migrations/versions/d51a8b2e7c40_rename_artifact_containers.py'
    spec = importlib.util.spec_from_file_location('artifact_container_migration', path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    monkeypatch.setattr(migration.op, 'get_bind', lambda: db.session.connection())
    migration.upgrade(); migration.upgrade(); db.session.expire_all()
    for item, identity, name, limit in zip(old, identities, NAMES, LIMITS):
        assert (item.id, item.name, item.attributes['radiation_limit'], item.attributes['manual_note']) == (
            identity, name, limit, 'сохранить')
    assert (storage.name, storage.price) == ('Барьер', 777)
