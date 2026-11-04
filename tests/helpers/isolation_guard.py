"""Чистые проверки допуска: без приложения, SQLAlchemy и подключений к БД."""
from contextlib import contextmanager
from pathlib import Path
import os

ALLOWED_MODULES = (
    'test_equipment_importer.py',
    'test_catalog_sweep.py',
    'test_character_creation.py',
    'test_character_traits_runtime.py',
    'test_weapon_modules.py',
    'test_weapon_grip.py',
    'test_action_journal.py',
    'test_portable_lights.py',
    'test_prostheses.py',
    'test_attack_latency.py',
    'test_noise.py',
    'test_combat_explosions.py', 'test_grenade_rules.py',
    'test_combat_movement.py', 'test_combat_attacks.py',
    'test_character_jumps.py',
    'test_ice_movement.py',
    'test_effect_lifecycle.py', 'test_regression_combat.py',
    'test_consumable_rules.py', 'test_consumable_catalog_rules.py',
    'test_inventory_ammo_stacks.py', 'test_inventory_access.py',
    'test_character_access.py', 'test_combat_anomalies.py',
    'test_magazine_installation.py', 'test_ammo_loading.py',
    'test_equipment_actions.py', 'test_equipment_modules.py', 'test_atomic_inventory.py',
    'test_medical_procedure.py',
    'test_deferred_consumables.py',
    'test_treatment_movement_lock.py',
    'test_general_consumable.py',
    'test_regression_time_stress.py',
    'test_regression_anomaly_boundary.py',
    'test_regression_access_damage.py',
    'test_regression_deferred_invalidation.py',
    'test_regression_medical_boundaries.py',
    'test_regression_inventory_boundaries.py',
    'test_regression_consumable_boundaries.py',
    'test_regression_completion_boundaries.py',
    'test_regression_paid_attack_boundaries.py',
    'test_regression_end_combat_operations.py',
    'test_regression_medical_retry.py',
    'test_regression_inventory_cost_remainder.py',
    'test_regression_catalog_application.py',
)
ALLOWED_NODEIDS = (
    'test_combat_movement.py::test_new_turn_resets_distance_but_keeps_run_and_sprint_exhaustion',
    'test_combat_movement.py::test_pending_action_spends_future_turn_points_until_complete',
    'test_combat_movement.py::test_gm_can_restore_psi_zombie_sheet_with_current_loot_and_without_heal',
    'test_combat_movement.py::test_pripyat_ice_planner_avoids_mixed_direction_detour',
    'test_combat_movement.py::test_direction_constrained_planner_on_weighted_terrain',
    'test_combat_movement.py::test_fractional_heading_route_across_45_degree_boundary',
    'test_lobby_api.py::test_gm_rest_event_advances_lobby_time_for_selected_characters',
    'test_location_storeys.py::test_server_preview_is_read_only_and_cover_above_does_not_block_same_floor',
    'test_location_storeys.py::test_hatch_action_validates_reach_lock_and_controls_and_persists_movement',
    'test_location_storeys.py::test_stairs_connect_adjacent_levels_in_both_directions_without_side_entry',
    'test_location_storeys.py::test_rotated_and_stacked_stair_flights_do_not_teleport_through_the_slab',
    'test_location_storeys.py::test_explicit_drop_without_a_ladder_on_signed_levels',
    'test_location_storeys.py::test_drop_applies_existing_fall_rules_and_saves_landing',

    'test_character_realtime_sync.py::test_magazine_install_http_fallback_is_broadcast_to_character_viewers',
    'test_lobby_api.py::test_weapon_fire_rate_rejects_excess_shots_without_spending_ammo_or_ap',
    'test_deferred_actions.py::test_reconnect_resumes_saved_arguments_once_without_repaying',
    'test_deferred_actions.py::test_drawing_weapon_waits_for_full_payment',
    'test_deferred_actions.py::test_large_cost_spans_several_turns_before_becoming_ready',
    'test_deferred_actions.py::test_unauthorized_resume_and_cancel_cannot_claim_paid_action',
    'test_deferred_actions.py::test_cancel_during_payment_does_not_refund_or_charge_next_turn',
    'test_deferred_actions.py::test_resume_failure_rolls_back_claim_and_result',
    'test_deferred_actions.py::test_ending_combat_or_removing_actor_cancels_old_action',
    'test_deferred_actions.py::test_sheet_save_cannot_change_pending_payment',
    'test_deferred_actions.py::test_duplicate_start_and_old_unsaved_resume_rejected',
    'test_deferred_actions.py::test_competing_database_claims_have_one_winner',
    'test_deferred_actions.py::test_weapon_replacement_rejects_resume_without_consuming_paid_action',
    'test_consumable_use.py::test_self_consumable_saves_health_expense_exposure_and_ap_once',
    'test_consumable_use.py::test_old_item_snapshot_rejects_new_operation_id_with_stale_result',
    'test_consumable_use.py::test_self_failure_after_flush_restores_resources_and_receipt',
    'test_consumable_use.py::test_ended_combat_rolls_back_attempt_to_grant_ap',
    'test_consumable_use.py::test_consumable_receipt_rejects_a_racing_session',
    'test_character_interactions.py::test_refused_treatment_uses_strength_and_locks_patient_while_in_progress',
    'test_combat_explosions.py::test_hand_grenade_action_spends_two_ap_and_consumes_one_item',
    'test_lobby_api.py::test_only_gm_or_controller_can_end_combat_turn',
    'test_lobby_api.py::test_only_gm_can_update_persisted_lobby_time',
)
ALLOWED_SELECTORS = ALLOWED_MODULES + ALLOWED_NODEIDS
POLICY = ('Разрешены целые модули: ' + ', '.join(ALLOWED_MODULES)
          + '; только точные тесты: ' + ', '.join(ALLOWED_NODEIDS)
          + '. Иные селекторы, параметры и дополнительные аргументы запрещены.')
CONFIRMATION = 'memory-only-02A'


class IsolationError(RuntimeError):
    pass


def require_environment(env):
    if (env.get('PYTHON_DOTENV_DISABLED') != '1'
            or env.get('TTRPG_TEST_CONFIRMATION') != CONFIRMATION
            or env.get('PYTEST_DISABLE_PLUGIN_AUTOLOAD') != '1'):
        raise IsolationError('Нет явного допуска изолированного запуска; используйте run_isolated_python.py')


def validated_modules(names):
    # Сохраняем имя функции для прежних вызывающих сторон.
    if not names or any(name not in ALLOWED_SELECTORS for name in names):
        raise IsolationError(POLICY)
    return list(dict.fromkeys(names))


def absolute_test_paths(selectors):
    root = Path(__file__).resolve().parents[1]
    result = []
    for selector in validated_modules(selectors):
        name, separator, node = selector.partition('::')
        result.append(str(root / name) + (separator + node if separator else ''))
    validate_test_paths(result)
    return result


def validate_test_paths(paths):
    root = Path(__file__).resolve().parents[1]
    if not paths:
        raise IsolationError('Нужны явные абсолютные пути согласованных селекторов')
    selectors = []
    for value in paths:
        filename, separator, node = str(value).partition('::')
        path = Path(filename)
        if (not path.is_absolute() or '..' in path.parts
                or path.parent != root or path.resolve() != root / path.name):
            raise IsolationError('Путь вне разрешённого каталога tests/')
        selectors.append(path.name + (separator + node if separator else ''))
    return validated_modules(selectors)


def isolated_pytest_arguments(selectors):
    root = Path(__file__).resolve().parents[2]
    return ['-c', str(root / 'pytest.ini'), '-o', 'addopts=',
            '-p', 'no:cacheprovider', '-p', 'isolation_guard',
            '--rootdir', str(root), '--confcutdir', str(root / 'tests'),
            *absolute_test_paths(selectors)]


def validate_invocation(config, paths):
    selectors = validate_test_paths(paths)
    invocation = getattr(config, 'invocation_params', None)
    if (invocation is None
            or list(invocation.args) != isolated_pytest_arguments(selectors)):
        raise IsolationError('Запрещены дополнительные аргументы pytest; используйте фиксированный launcher')


def pytest_addoption(parser):
    # Регистрация opt-in плагина происходит до initial conftest.
    require_environment(os.environ)


def pytest_load_initial_conftests(early_config):
    require_environment(os.environ)
    options = early_config.known_args_namespace
    if getattr(options, 'pyargs', False) or getattr(options, 'noconftest', False):
        raise IsolationError('Изолированный запуск требует файлов и безопасной conftest-фикстуры')
    validate_invocation(early_config, options.file_or_dir)


def pytest_configure(config):
    require_environment(os.environ)
    validate_invocation(config, config.args)


def child_environment(source):
    # Значения DB/API/секретов не читаются: переносится только фиксированный список.
    keys = ('SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT', 'TEMP', 'TMP',
            'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'SYSTEMDRIVE', 'LANG', 'LC_ALL')
    result = {key: source[key] for key in keys if key in source}
    result.update(PYTHON_DOTENV_DISABLED='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
                  PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8',
                  TTRPG_TEST_CONFIRMATION=CONFIRMATION)
    return result


def assert_memory_database(app, db):
    config = app.config
    if (config.get('TESTING') is not True
            or config.get('SQLALCHEMY_DATABASE_URI') not in ('sqlite://', 'sqlite:///:memory:')
            or config.get('SQLALCHEMY_BINDS')):
        raise IsolationError('Отказ: требуется TESTING=True и только SQLite в памяти без binds')
    engine = db.engine
    url = engine.url
    if (engine.dialect.name != 'sqlite' or url.drivername not in ('sqlite', 'sqlite+pysqlite')
            or url.database not in (None, '', ':memory:')
            or any((url.host, url.port, url.username, url.password, url.query))
            or set(db.engines) != {None} or db.engines[None] is not engine):
        raise IsolationError('Отказ: фактический engine/URL не является единственной SQLite в памяти')
    return engine


@contextmanager
def memory_database_scope(app, db):
    engine = assert_memory_database(app, db)
    try:
        db.create_all()
        yield
    finally:
        try:
            db.session.remove()
            if assert_memory_database(app, db) is not engine:
                raise IsolationError('Отказ очистки: движок заменён')
            db.drop_all()
        finally:
            try:
                db.session.remove()
            finally:
                engine.dispose()
