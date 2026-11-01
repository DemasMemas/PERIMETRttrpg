"""Проверки защиты на подставных объектах: без app, pytest и реальной БД."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import ast

sys.path.insert(0, str(Path(__file__).resolve().parent))
import isolation_guard as guard
from isolation_guard import (ALLOWED_MODULES, CONFIRMATION, IsolationError,
                             assert_memory_database, child_environment,
                             memory_database_scope, require_environment, validated_modules)


def fake_database():
    calls = []
    url = NS(drivername='sqlite', database=None, host=None, port=None,
             username=None, password=None, query={})
    engine = NS(dialect=NS(name='sqlite'), url=url, dispose=lambda: calls.append('dispose'))
    db = NS(engine=engine, engines={None: engine},
            session=NS(remove=lambda: calls.append('remove')),
            create_all=lambda: calls.append('create'), drop_all=lambda: calls.append('drop'))
    app = NS(config={'TESTING': True, 'SQLALCHEMY_DATABASE_URI': 'sqlite://'})
    return app, db, calls


class IsolationChecks(unittest.TestCase):
    def test_memory_uris_are_allowed(self):
        for uri, database in [('sqlite://', None), ('sqlite:///:memory:', ':memory:')]:
            app, db, calls = fake_database()
            app.config['SQLALCHEMY_DATABASE_URI'] = uri
            db.engine.url.database = database
            self.assertIs(assert_memory_database(app, db), db.engine)
            self.assertEqual(calls, [])

    def test_unsafe_targets_rejected_before_any_cleanup_or_ddl(self):
        mutations = [
            lambda a, d: a.config.update(TESTING=False),
            lambda a, d: a.config.update(TESTING=1),
            lambda a, d: a.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///file.db'),
            lambda a, d: a.config.update(SQLALCHEMY_DATABASE_URI='postgresql://host/db'),
            lambda a, d: setattr(d.engine.url, 'database', 'file.db'),
            lambda a, d: setattr(d.engine.url, 'drivername', 'postgresql'),
            lambda a, d: setattr(d.engine.dialect, 'name', 'postgresql'),
            lambda a, d: setattr(d.engine.url, 'host', 'server'),
            lambda a, d: setattr(d.engine.url, 'query', {'mode': 'memory'}),
            lambda a, d: a.config.update(SQLALCHEMY_BINDS={'other': 'sqlite:///other.db'}),
            lambda a, d: d.engines.update(other=d.engine),
        ]
        for mutate in mutations:
            app, db, calls = fake_database()
            mutate(app, db)
            with self.subTest(change=mutate):
                with self.assertRaises(IsolationError):
                    with memory_database_scope(app, db):
                        self.fail('Небезопасное назначение было принято')
                self.assertEqual(calls, [])

    def test_normal_cleanup(self):
        app, db, calls = fake_database()
        with memory_database_scope(app, db):
            calls.append('body')
        self.assertEqual(calls, ['create', 'body', 'remove', 'drop', 'remove', 'dispose'])

    def test_cleanup_survives_create_drop_and_body_errors(self):
        for failure in ['create', 'drop', 'body', 'remove']:
            app, db, calls = fake_database()
            def fail():
                calls.append(failure)
                raise RuntimeError('Имитированная ошибка')
            if failure == 'remove':
                db.session.remove = fail
            elif failure != 'body':
                setattr(db, failure + '_all', fail)
            with self.subTest(failure=failure):
                with self.assertRaises(RuntimeError):
                    with memory_database_scope(app, db):
                        if failure == 'body':
                            fail()
                self.assertEqual(calls[-1], 'dispose')
                self.assertIn('remove', calls)

    def test_revalidation_prevents_drop_after_target_change(self):
        for change in ['uri', 'url', 'engine']:
            app, db, calls = fake_database()
            original = db.engine
            with self.subTest(change=change):
                with self.assertRaises(IsolationError):
                    with memory_database_scope(app, db):
                        if change == 'uri':
                            app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///unknown.db'
                        elif change == 'url':
                            db.engine.url.database = 'unknown.db'
                        else:
                            _, other, _ = fake_database()
                            db.engine = other.engine
                            db.engines[None] = other.engine
                self.assertNotIn('drop', calls)
                self.assertEqual(calls[-1], 'dispose')
                self.assertIsNot(db.engine, original) if change == 'engine' else None

    def test_environment_secrets_are_never_read(self):
        class Protected(dict):
            def __getitem__(self, key):
                if key not in {'PATH', 'SYSTEMROOT'}:
                    raise AssertionError('Попытка чтения запрещённого значения')
                return super().__getitem__(key)
        env = child_environment(Protected(PATH='local-path', SYSTEMROOT='windows',
                                         DATABASE_URL='hidden', API_KEY='hidden',
                                         OPENAI_API_KEY='hidden', PYTEST_ADDOPTS='hidden',
                                         PYTEST_PLUGINS='hidden', PYTHONPATH='hidden'))
        require_environment(env)
        self.assertEqual(set(env), {'PATH', 'SYSTEMROOT', 'PYTHON_DOTENV_DISABLED',
                                    'PYTEST_DISABLE_PLUGIN_AUTOLOAD', 'PYTHONDONTWRITEBYTECODE',
                                    'PYTHONIOENCODING', 'TTRPG_TEST_CONFIRMATION'})

    def test_confirmation_and_module_allowlist(self):
        valid = {'PYTHON_DOTENV_DISABLED': '1', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
                 'TTRPG_TEST_CONFIRMATION': CONFIRMATION}
        require_environment(valid)
        for key in valid:
            with self.assertRaises(IsolationError):
                require_environment({**valid, key: '0'})
        self.assertEqual(validated_modules(ALLOWED_MODULES), list(ALLOWED_MODULES))
        for names in [[], ['test_other.py'], ['--collect-only'], ['-p'], ['../test_combat_attacks.py']]:
            with self.assertRaises(IsolationError):
                validated_modules(names)


class OptInPluginChecks(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.paths = [str(self.root / name) for name in ALLOWED_MODULES]
        self.env = child_environment({})

    def config(self, paths=None, **options):
        return NS(known_args_namespace=NS(file_or_dir=self.paths if paths is None else paths,
                                         **options),
                  invocation_params=NS(args=guard.isolated_pytest_arguments(ALLOWED_MODULES)))

    def test_opt_in_hooks_accept_only_confirmed_absolute_paths(self):
        with patch.object(guard, 'os', NS(environ=self.env)):
            guard.pytest_addoption(NS())
            guard.pytest_load_initial_conftests(self.config())
            guard.pytest_configure(NS(args=self.paths, invocation_params=NS(
                args=guard.isolated_pytest_arguments(ALLOWED_MODULES))))

    def test_missing_confirmation_rejects_direct_plugin_registration(self):
        for key in ['TTRPG_TEST_CONFIRMATION', 'PYTHON_DOTENV_DISABLED', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD']:
            env = {name: value for name, value in self.env.items() if name != key}
            with self.subTest(missing=key), patch.object(guard, 'os', NS(environ=env)):
                with self.assertRaises(IsolationError):
                    guard.pytest_addoption(NS())
                with self.assertRaises(IsolationError):
                    guard.pytest_load_initial_conftests(self.config())
                with self.assertRaises(IsolationError):
                    guard.pytest_configure(NS(args=self.paths, invocation_params=NS(
                args=guard.isolated_pytest_arguments(ALLOWED_MODULES))))

    def test_bad_paths_refused_before_simulated_conftest_load(self):
        bad = [[], [ALLOWED_MODULES[0]], [str(self.root.parent / ALLOWED_MODULES[0])],
               [str(self.root / 'test_other.py')], [str(self.root)],
               [self.paths[0] + '::test_case']]
        with patch.object(guard, 'os', NS(environ=self.env)):
            for paths in bad:
                loaded = []
                with self.subTest(paths=paths), self.assertRaises(IsolationError):
                    guard.pytest_load_initial_conftests(self.config(paths))
                    loaded.append('conftest')
                self.assertEqual(loaded, [])
                with self.assertRaises(IsolationError):
                    guard.pytest_configure(NS(args=paths, invocation_params=NS(
                        args=guard.isolated_pytest_arguments(ALLOWED_MODULES))))
            for option in ['pyargs', 'noconftest']:
                with self.assertRaises(IsolationError):
                    guard.pytest_load_initial_conftests(self.config(**{option: True}))

    def test_ordinary_conftest_has_no_opt_in_restrictions(self):
        source = (self.root / 'conftest.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        self.assertFalse(any(isinstance(node, ast.FunctionDef) and node.name.startswith('pytest_')
                             for node in tree.body))
        self.assertNotIn('require_environment', source)
        self.assertNotIn('TTRPG_TEST_CONFIRMATION', source)
        self.assertNotIn('validated_modules', source)
        self.assertNotIn('validate_test_paths', source)
        self.assertIn("os.environ['PYTHON_DOTENV_DISABLED'] = '1'", source)
        self.assertIn('with memory_database_scope(test_app, db):', source)
        self.assertFalse(any(isinstance(node, ast.ImportFrom) and node.module
                             and node.module.startswith('app') for node in tree.body))


class ExactSelectorChecks(unittest.TestCase):
    def setUp(self):
        self.env = child_environment({})
        self.root = Path(__file__).resolve().parents[1]

    def config(self, paths, invocation=None, **options):
        return NS(known_args_namespace=NS(file_or_dir=paths, **options), args=paths,
                  invocation_params=NS(args=guard.isolated_pytest_arguments(guard.ALLOWED_SELECTORS)
                                       if invocation is None else invocation))

    def test_policy_preserves_old_modules_and_adds_only_requested_selectors(self):
        self.assertEqual(guard.ALLOWED_MODULES, (
            'test_combat_movement.py', 'test_combat_attacks.py',
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
        ))
        self.assertEqual(guard.ALLOWED_NODEIDS, (
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
        ))
        self.assertEqual(validated_modules(guard.ALLOWED_SELECTORS * 2), list(guard.ALLOWED_SELECTORS))

    def test_both_exact_nodes_and_whole_modules_pass_before_simulated_conftest(self):
        paths = guard.absolute_test_paths(guard.ALLOWED_SELECTORS)
        config = self.config(paths)
        with patch.object(guard, 'os', NS(environ=self.env)):
            loaded = []
            guard.pytest_load_initial_conftests(config)
            loaded.append('simulated-conftest')
            guard.pytest_configure(config)
            self.assertEqual(loaded, ['simulated-conftest'])
        for selector, path in zip(guard.ALLOWED_SELECTORS, paths):
            name, separator, node = selector.partition('::')
            self.assertEqual(path, str(self.root / name) + (separator + node if separator else ''))

    def test_other_nodes_whole_restricted_modules_and_suffixes_rejected_early(self):
        bad = ['test_location_storeys.py', 'test_character_realtime_sync.py', 'test_lobby_api.py',
               'test_lobby_api.py::test_other', 'test_character_realtime_sync.py::test_other',
               guard.ALLOWED_NODEIDS[0] + '[case]', guard.ALLOWED_NODEIDS[1] + '::other',
               'test_combat_attacks.py::test_any', 'test_combat_explosions.py',
               '../test_combat_attacks.py', 'test_regression_combat.py::test_other',
               '-k', '--pyargs', '--noconftest', '-p', '.', 'tests']
        with patch.object(guard, 'os', NS(environ=self.env)):
            for selector in bad:
                with self.subTest(selector=selector):
                    with self.assertRaises(IsolationError):
                        validated_modules([selector])
                    with self.assertRaises(IsolationError):
                        guard.absolute_test_paths([selector])
                    paths = [str(self.root / selector)]
                    loaded = []
                    with self.assertRaises(IsolationError):
                        guard.pytest_load_initial_conftests(self.config(paths))
                        loaded.append('simulated-conftest')
                    self.assertEqual(loaded, [])
                    with self.assertRaises(IsolationError):
                        guard.pytest_configure(self.config(paths))

    def test_traversal_foreign_and_relative_paths_rejected_before_conftest(self):
        name, _, node = guard.ALLOWED_NODEIDS[1].partition('::')
        suffix = '::' + node
        bad = [str(self.root.parent / name) + suffix,
               str(self.root / '..' / 'tests' / name) + suffix,
               name + suffix, str(self.root) + suffix,
               str(self.root / 'subdir' / name) + suffix]
        with patch.object(guard, 'os', NS(environ=self.env)):
            for path in bad:
                loaded = []
                with self.subTest(path=path), self.assertRaises(IsolationError):
                    guard.pytest_load_initial_conftests(self.config([path]))
                    loaded.append('simulated-conftest')
                self.assertEqual(loaded, [])

    def test_extra_pytest_arguments_rejected_before_conftest(self):
        paths = guard.absolute_test_paths(guard.ALLOWED_SELECTORS)
        base = guard.isolated_pytest_arguments(guard.ALLOWED_SELECTORS)
        extras = [['-k', 'test'], ['-m', 'mark'], ['--collect-only'], ['--pyargs'],
                  ['--noconftest'], ['-p', 'foreign'], ['-o', 'addopts=-k test']]
        with patch.object(guard, 'os', NS(environ=self.env)):
            for extra in extras:
                config = self.config(paths, base + extra)
                loaded = []
                with self.subTest(extra=extra), self.assertRaises(IsolationError):
                    guard.pytest_load_initial_conftests(config)
                    loaded.append('simulated-conftest')
                self.assertEqual(loaded, [])
                with self.assertRaises(IsolationError):
                    guard.pytest_configure(config)

    def test_launcher_bootstrap_passes_absolute_nodes_to_fake_pytest_only(self):
        import run_isolated_python as launcher
        selectors = list(guard.ALLOWED_SELECTORS)
        with patch.object(sys, 'argv', ['launcher', '--confirm-memory-tests', *selectors]), \
                patch.object(launcher.subprocess, 'run', return_value=NS(returncode=0)) as child, \
                patch.object(Path, 'is_file', return_value=True):
            self.assertEqual(launcher.main(), 0)
        command = child.call_args.args[0]
        self.assertEqual(command[1:4], ['-I', '-B', '-c'])
        self.assertEqual(command[6:], selectors)
        require_environment(child.call_args.kwargs['env'])
        calls = []
        fake = NS(main=lambda arguments: calls.append(arguments) or 0)
        with patch.dict(sys.modules, {'pytest': fake}), \
                patch.object(sys, 'argv', ['-c', *command[5:]]), \
                patch.object(sys, 'path', list(sys.path)):
            with self.assertRaises(SystemExit) as result:
                exec(command[4], {'__name__': '__isolated_bootstrap__'})
        self.assertEqual(result.exception.code, 0)
        self.assertEqual(calls, [guard.isolated_pytest_arguments(selectors)])
        self.assertNotIn('app', sys.modules)

    def test_launcher_rejects_extra_cli_before_any_child(self):
        from contextlib import redirect_stderr
        from io import StringIO
        import run_isolated_python as launcher
        bad = [['test_lobby_api.py'], [guard.ALLOWED_NODEIDS[1] + '[x]'],
               ['../test_combat_attacks.py'], ['-k', 'test'], ['--noconftest'],
               ['--pyargs'], ['-p', 'foreign']]
        for extra in bad:
            with self.subTest(extra=extra), \
                    patch.object(sys, 'argv', ['launcher', '--confirm-memory-tests', *extra]), \
                    patch.object(launcher.subprocess, 'run') as child, redirect_stderr(StringIO()):
                with self.assertRaises((IsolationError, SystemExit)):
                    launcher.main()
                child.assert_not_called()


class AutotestGatePChecks(unittest.TestCase):
    modules = ('test_consumable_rules.py', 'test_consumable_catalog_rules.py',
               'test_inventory_ammo_stacks.py', 'test_inventory_access.py')

    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.env = child_environment({})

    def config(self, paths, invocation=None):
        return NS(known_args_namespace=NS(file_or_dir=paths), args=paths,
                  invocation_params=NS(args=guard.isolated_pytest_arguments(self.modules)
                                       if invocation is None else invocation))

    def test_four_modules_pass_individually_and_as_cp_ci_before_simulated_collection(self):
        groups = [(module,) for module in self.modules] + [self.modules[:3], self.modules[3:], self.modules]
        with patch.object(guard, 'os', NS(environ=self.env)):
            for modules in groups:
                with self.subTest(modules=modules):
                    paths = guard.absolute_test_paths(modules)
                    config = self.config(paths, guard.isolated_pytest_arguments(modules))
                    reached = []
                    guard.pytest_load_initial_conftests(config)
                    reached.append('simulated-conftest')
                    guard.pytest_configure(config)
                    reached.append('simulated-collection')
                    self.assertEqual(reached, ['simulated-conftest', 'simulated-collection'])
                    self.assertEqual(paths, [str(self.root / module) for module in modules])

    def test_unapproved_modules_nodes_suffixes_and_directories_rejected_before_collection(self):
        bad = ['test_deferred_actions.py',
               'test_consumable_use.py', 'test_consumable_profile_catalog_coverage.py',
               'test_lobby_api.py', 'test_unknown.py', '-k', '--collect-only', 'tests']
        for module in self.modules:
            bad.extend([module + '::test_any', module + '[case]', module + '::test_any[case]'])
        with patch.object(guard, 'os', NS(environ=self.env)):
            for selector in bad:
                with self.subTest(selector=selector):
                    with self.assertRaises(IsolationError):
                        validated_modules([selector])
                    config = self.config([str(self.root / selector)])
                    reached = []
                    with self.assertRaises(IsolationError):
                        guard.pytest_load_initial_conftests(config)
                        reached.append('simulated-conftest')
                    with self.assertRaises(IsolationError):
                        guard.pytest_configure(config)
                        reached.append('simulated-collection')
                    self.assertEqual(reached, [])
            bad_paths = [str(self.root), str(self.root.parent),
                         str(self.root / '..' / 'tests' / self.modules[0]),
                         str(self.root.parent / self.modules[0]), self.modules[0]]
            for value in bad_paths:
                reached = []
                config = self.config([value])
                with self.subTest(path=value), self.assertRaises(IsolationError):
                    guard.pytest_load_initial_conftests(config)
                    reached.append('simulated-conftest')
                with self.assertRaises(IsolationError):
                    guard.pytest_configure(config)
                    reached.append('simulated-collection')
                self.assertEqual(reached, [])

    def test_extra_arguments_still_block_new_modules_before_simulated_collection(self):
        paths = guard.absolute_test_paths(self.modules)
        base = guard.isolated_pytest_arguments(self.modules)
        with patch.object(guard, 'os', NS(environ=self.env)):
            for extra in [['-k', 'test'], ['--pyargs'], ['--noconftest'], ['-p', 'foreign']]:
                config = self.config(paths, base + extra)
                reached = []
                with self.subTest(extra=extra), self.assertRaises(IsolationError):
                    guard.pytest_load_initial_conftests(config)
                    reached.append('simulated-conftest')
                with self.assertRaises(IsolationError):
                    guard.pytest_configure(config)
                    reached.append('simulated-collection')
                self.assertEqual(reached, [])


class AutotestGateANChecks(unittest.TestCase):
    modules = ('test_character_access.py', 'test_combat_anomalies.py')

    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.env = child_environment({})

    def config(self, paths, invocation):
        return NS(known_args_namespace=NS(file_or_dir=paths), args=paths,
                  invocation_params=NS(args=invocation))

    def test_modules_pass_individually_and_together_before_conftest(self):
        with patch.object(guard, 'os', NS(environ=self.env)):
            for modules in [(name,) for name in self.modules] + [self.modules]:
                with self.subTest(modules=modules):
                    paths = guard.absolute_test_paths(modules)
                    config = self.config(paths, guard.isolated_pytest_arguments(modules))
                    reached = []
                    guard.pytest_load_initial_conftests(config)
                    reached.append('simulated-conftest')
                    guard.pytest_configure(config)
                    reached.append('simulated-collection')
                    self.assertEqual(reached, ['simulated-conftest', 'simulated-collection'])
                    self.assertEqual(paths, [str(self.root / name) for name in modules])

    def test_foreign_migration_nodes_parameters_paths_and_options_rejected_early(self):
        bad = ['test_deferred_actions.py', 'test_consumable_rename.py', 'test_lobby_api.py',
               'test_consumable_profile_catalog_coverage.py', 'test_location_building_anomalies.py',
               'test_deferred_actions.py::test_deferred_table_migration_round_trip',
               'test_consumable_use.py::test_consumable_migration_round_trip',
               'test_character_access.py::test_editor_can_save_but_viewer_cannot',
               'test_combat_anomalies.py::test_anomaly_trigger_uses_at_least_three_quarters_of_cell',
               '-k', '--pyargs', '--noconftest', 'tests']
        for module in self.modules:
            bad.extend([module + '[case]', module + '::test_any', module + '::test_any[case]'])
        invocation = guard.isolated_pytest_arguments(self.modules)
        with patch.object(guard, 'os', NS(environ=self.env)):
            for selector in bad:
                with self.subTest(selector=selector):
                    with self.assertRaises(IsolationError):
                        validated_modules([selector])
                    paths = [str(self.root / selector)]
                    self.assert_early_rejection(self.config(paths, invocation))
            for value in [str(self.root), str(self.root.parent / self.modules[0]),
                          str(self.root / '..' / 'tests' / self.modules[0]), self.modules[0]]:
                with self.subTest(path=value):
                    self.assert_early_rejection(self.config([value], invocation))
            paths = guard.absolute_test_paths(self.modules)
            for extra in [['-k', 'test'], ['--pyargs'], ['--noconftest'], ['-p', 'foreign']]:
                with self.subTest(extra=extra):
                    self.assert_early_rejection(self.config(paths, invocation + extra))

    def assert_early_rejection(self, config):
        reached = []
        with self.assertRaises(IsolationError):
            guard.pytest_load_initial_conftests(config)
            reached.append('simulated-conftest')
        with self.assertRaises(IsolationError):
            guard.pytest_configure(config)
            reached.append('simulated-collection')
        self.assertEqual(reached, [])


class AutotestGateIChecks(AutotestGateANChecks):
    modules = ('test_magazine_installation.py', 'test_ammo_loading.py',
               'test_equipment_actions.py', 'test_equipment_modules.py', 'test_atomic_inventory.py')


class AutotestGateMDChecks(unittest.TestCase):
    modules = ('test_medical_procedure.py', 'test_deferred_consumables.py', 'test_treatment_movement_lock.py', 'test_general_consumable.py')
    nodes = (
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
    )

    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.env = child_environment({})

    def config(self, paths, invocation):
        return NS(known_args_namespace=NS(file_or_dir=paths), args=paths,
                  invocation_params=NS(args=invocation))

    def test_four_whole_and_eleven_exact_selectors_pass_before_conftest(self):
        selectors = self.modules + self.nodes
        self.assertEqual(len(self.nodes), 11)
        self.assertNotIn('test_deferred_actions.py', guard.ALLOWED_MODULES)
        with patch.object(guard, 'os', NS(environ=self.env)):
            for group in [(value,) for value in selectors] + [self.modules, self.nodes, selectors]:
                with self.subTest(selectors=group):
                    paths = guard.absolute_test_paths(group)
                    config = self.config(paths, guard.isolated_pytest_arguments(group))
                    reached = []
                    guard.pytest_load_initial_conftests(config)
                    reached.append('simulated-conftest')
                    guard.pytest_configure(config)
                    reached.append('simulated-collection')
                    self.assertEqual(reached, ['simulated-conftest', 'simulated-collection'])
                    expected = []
                    for value in group:
                        name, separator, node = value.partition('::')
                        expected.append(str(self.root / name) + (separator + node if separator else ''))
                    self.assertEqual(paths, expected)

    def test_full_deferred_migration_foreign_nodes_suffixes_options_and_paths_rejected(self):
        selectors = self.modules + self.nodes
        invocation = guard.isolated_pytest_arguments(selectors)
        bad = ['test_deferred_actions.py', 'test_deferred_actions.py::test_deferred_table_migration_round_trip',
               'test_deferred_actions.py::test_other', 'test_consumable_use.py', 'test_consumable_rename.py',
               'test_lobby_api.py', 'test_location_building_anomalies.py',
               'test_consumable_profile_catalog_coverage.py', '-k', '--pyargs', '--noconftest', 'tests']
        for node in self.nodes:
            bad.extend([node + '[case]', node + '::other'])
        for module in self.modules:
            bad.extend([module + '::test_any', module + '[case]'])
        with patch.object(guard, 'os', NS(environ=self.env)):
            for value in bad:
                with self.subTest(selector=value):
                    with self.assertRaises(IsolationError):
                        validated_modules([value])
                    self.assert_early_rejection(self.config([str(self.root / value)], invocation))
            name, _, test_name = self.nodes[0].partition('::')
            for path in [str(self.root), str(self.root.parent / name) + '::' + test_name,
                         str(self.root / '..' / 'tests' / name) + '::' + test_name, self.nodes[0]]:
                with self.subTest(path=path):
                    self.assert_early_rejection(self.config([path], invocation))
            paths = guard.absolute_test_paths(selectors)
            for extra in [['-k', 'test'], ['--collect-only'], ['--pyargs'], ['--noconftest'], ['-p', 'foreign']]:
                with self.subTest(extra=extra):
                    self.assert_early_rejection(self.config(paths, invocation + extra))

    def assert_early_rejection(self, config):
        reached = []
        with self.assertRaises(IsolationError):
            guard.pytest_load_initial_conftests(config)
            reached.append('simulated-conftest')
        with self.assertRaises(IsolationError):
            guard.pytest_configure(config)
            reached.append('simulated-collection')
        self.assertEqual(reached, [])


class AutotestGateXChecks(unittest.TestCase):
    nodes = (
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

    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.env = child_environment({})

    def config(self, paths, invocation):
        return NS(known_args_namespace=NS(file_or_dir=paths), args=paths,
                  invocation_params=NS(args=invocation))

    def test_nine_exact_and_four_command_groups_pass_before_conftest(self):
        self.assertEqual(len(self.nodes), 9)
        files = {node.partition('::')[0] for node in self.nodes}
        self.assertTrue(files.isdisjoint(guard.ALLOWED_MODULES))
        groups = [(node,) for node in self.nodes] + [self.nodes[:5], self.nodes[5:6], self.nodes[6:7], self.nodes[7:], self.nodes]
        with patch.object(guard, 'os', NS(environ=self.env)):
            for group in groups:
                with self.subTest(selectors=group):
                    paths = guard.absolute_test_paths(group)
                    config = self.config(paths, guard.isolated_pytest_arguments(group))
                    reached = []
                    guard.pytest_load_initial_conftests(config)
                    reached.append('simulated-conftest')
                    guard.pytest_configure(config)
                    reached.append('simulated-collection')
                    self.assertEqual(reached, ['simulated-conftest', 'simulated-collection'])
                    self.assertEqual(paths, [str(self.root / node.partition('::')[0]) + '::' + node.partition('::')[2] for node in group])

    def test_full_files_migrations_other_tests_suffixes_arguments_and_paths_blocked(self):
        invocation = guard.isolated_pytest_arguments(self.nodes)
        bad = sorted({node.partition('::')[0] for node in self.nodes})
        bad += ['test_consumable_use.py::test_consumable_migration_round_trip',
                'test_deferred_actions.py::test_deferred_table_migration_round_trip',
                'test_consumable_use.py::test_outside_combat_consumable_checks_addiction_without_ap',
                'test_character_interactions.py::test_trade_moves_both_offers_atomically_and_costs_two_ap',
                'test_combat_explosions.py::test_projectile_detonates_at_ammunition_range',
                'test_lobby_api.py::test_gm_full_heal_restores_body_and_rejects_player',
                'test_consumable_profile_catalog_coverage.py', 'test_location_building_anomalies.py',
                '-k', '--collect-only', '--pyargs', '--noconftest', 'tests']
        for node in self.nodes:
            bad.extend([node + '[case]', node + '::other'])
        with patch.object(guard, 'os', NS(environ=self.env)):
            for value in bad:
                with self.subTest(selector=value):
                    with self.assertRaises(IsolationError):
                        validated_modules([value])
                    self.assert_early_rejection(self.config([str(self.root / value)], invocation))
            name, _, test_name = self.nodes[0].partition('::')
            for value in [str(self.root), self.nodes[0],
                          str(self.root.parent / name) + '::' + test_name,
                          str(self.root / '..' / 'tests' / name) + '::' + test_name]:
                with self.subTest(path=value):
                    self.assert_early_rejection(self.config([value], invocation))
            paths = guard.absolute_test_paths(self.nodes)
            for extra in [['-k', 'test'], ['--collect-only'], ['--pyargs'], ['--noconftest'], ['-p', 'foreign']]:
                with self.subTest(extra=extra):
                    self.assert_early_rejection(self.config(paths, invocation + extra))

    def assert_early_rejection(self, config):
        reached = []
        with self.assertRaises(IsolationError):
            guard.pytest_load_initial_conftests(config)
            reached.append('simulated-conftest')
        with self.assertRaises(IsolationError):
            guard.pytest_configure(config)
            reached.append('simulated-collection')
        self.assertEqual(reached, [])


class AutotestTplusGateChecks(unittest.TestCase):
    module = 'test_regression_time_stress.py'

    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.paths = [str(self.root / self.module)]
        self.invocation = guard.isolated_pytest_arguments([self.module])
        self.env = child_environment({})

    def config(self, paths, invocation):
        return NS(known_args_namespace=NS(file_or_dir=paths), args=paths,
                  invocation_params=NS(args=invocation))

    def assert_early_rejection(self, paths, invocation):
        config = self.config(paths, invocation)
        reached = []
        with self.assertRaises(IsolationError):
            guard.pytest_load_initial_conftests(config)
            reached.append('simulated-conftest')
        with self.assertRaises(IsolationError):
            guard.pytest_configure(config)
            reached.append('simulated-collection')
        self.assertEqual(reached, [])

    def test_exact_absolute_module_passes_both_hooks(self):
        self.assertIn(self.module, guard.ALLOWED_MODULES)
        self.assertEqual(validated_modules([self.module]), [self.module])
        self.assertEqual(guard.absolute_test_paths([self.module]), self.paths)
        self.assertEqual(guard.validate_test_paths(self.paths), [self.module])
        with patch.object(guard, 'os', NS(environ=self.env)):
            config = self.config(self.paths, self.invocation)
            guard.pytest_load_initial_conftests(config)
            guard.pytest_configure(config)

    def test_foreign_modules_selectors_paths_and_extra_arguments_rejected(self):
        bad_selectors = ['test_character_interactions.py', 'test_lobby_api.py',
                         'test_consumable_use.py', 'test_deferred_actions.py',
                         'test_other.py', self.module + '[case]',
                         self.module + '::test_any', self.module + '::test_any[case]',
                         '--collect-only', '-k']
        with patch.object(guard, 'os', NS(environ=self.env)):
            for selector in bad_selectors:
                with self.subTest(selector=selector):
                    with self.assertRaises(IsolationError):
                        validated_modules([selector])
                    self.assert_early_rejection([str(self.root / selector)], self.invocation)
            for path in [self.module, str(self.root),
                         str(self.root.parent / self.module),
                         str(self.root / 'subdir' / self.module),
                         str(self.root / '..' / 'tests' / self.module)]:
                with self.subTest(path=path):
                    self.assert_early_rejection([path], self.invocation)
            for extra in [['-k', 'time'], ['--collect-only'], ['--pyargs'],
                          ['--noconftest'], ['-p', 'foreign'], ['--override-ini', 'addopts=-x']]:
                with self.subTest(extra=extra):
                    self.assert_early_rejection(self.paths, self.invocation + extra)

    def test_redirected_module_path_rejected_without_real_link(self):
        # Подмена resolve имитирует ссылку, не создавая её и не открывая модуль.
        original_resolve = Path.resolve
        module_path = self.root / self.module
        foreign_path = self.root.parent / self.module

        def redirected_resolve(path, *args, **kwargs):
            if path == module_path:
                return foreign_path
            return original_resolve(path, *args, **kwargs)

        with patch.object(guard, 'os', NS(environ=self.env)):
            with patch.object(Path, 'resolve', redirected_resolve):
                self.assert_early_rejection(self.paths, self.invocation)
                with self.assertRaises(IsolationError):
                    guard.absolute_test_paths([self.module])


class AutotestNplusGateChecks(AutotestTplusGateChecks):
    module = 'test_regression_anomaly_boundary.py'


class AutotestAccessDamageGateChecks(AutotestTplusGateChecks):
    module = 'test_regression_access_damage.py'


class AutotestDeferredInvalidationGateChecks(AutotestTplusGateChecks):
    module = 'test_regression_deferred_invalidation.py'

    def test_both_modules_pass_together_but_mixed_paths_and_arguments_fail_early(self):
        modules = ['test_regression_access_damage.py', self.module]
        paths = guard.absolute_test_paths(modules)
        self.assertEqual(paths, [str(self.root / module) for module in modules])
        self.assertEqual(guard.validate_test_paths(paths), modules)
        self.assertNotIn('test_deferred_actions.py', guard.ALLOWED_MODULES)
        invocation = guard.isolated_pytest_arguments(modules)
        with patch.object(guard, 'os', NS(environ=self.env)):
            config = self.config(paths, invocation)
            guard.pytest_load_initial_conftests(config)
            guard.pytest_configure(config)
            for foreign in ['test_deferred_actions.py', 'test_consumable_use.py',
                            'test_deferred_actions.py::test_deferred_table_migration_round_trip']:
                with self.subTest(foreign=foreign):
                    self.assert_early_rejection(paths + [str(self.root / foreign)], invocation)
            for module in modules:
                for changed in [str(self.root.parent / module),
                                str(self.root / '..' / 'tests' / module),
                                str(self.root / 'nested' / module), module]:
                    replaced = [changed if path == str(self.root / module) else path for path in paths]
                    with self.subTest(module=module, changed=changed):
                        self.assert_early_rejection(replaced, invocation)
            for extra in [['-k', 'test'], ['--collect-only'], ['--pyargs'],
                          ['--noconftest'], ['-p', 'foreign']]:
                with self.subTest(extra=extra):
                    self.assert_early_rejection(paths, invocation + extra)


class AutotestMedicalBoundariesGateChecks(AutotestTplusGateChecks):
    module = 'test_regression_medical_boundaries.py'


class AutotestInventoryBoundariesGateChecks(AutotestTplusGateChecks):
    module = 'test_regression_inventory_boundaries.py'


class AutotestConsumableBoundariesGateChecks(AutotestTplusGateChecks):
    module = 'test_regression_consumable_boundaries.py'


class AutotestCompletionBoundariesGateChecks(AutotestTplusGateChecks):
    module = 'test_regression_completion_boundaries.py'


class PaidAttackBoundariesGateChecks(AutotestTplusGateChecks):
    module = 'test_regression_paid_attack_boundaries.py'


class EndCombatOperationsGateChecks(AutotestTplusGateChecks):
    module = 'test_regression_end_combat_operations.py'


class MedicalRetryGateChecks(AutotestTplusGateChecks):
    module = 'test_regression_medical_retry.py'


class InventoryCostRemainderGateChecks(AutotestTplusGateChecks):
    module = 'test_regression_inventory_cost_remainder.py'


class CatalogApplicationGateChecks(AutotestTplusGateChecks):
    module = 'test_regression_catalog_application.py'


class BrowserOwnedFileChecks(unittest.TestCase):
    def fixture(self):
        import json
        import stat
        import browser_db_guard as owned
        owner = owned.owned_layout('12345678-1234-1234-1234-123456789abc')
        class FakeIO:
            created = False
            manifest = owned.expected_manifest(owner.run_id)
            redirects = {}
            junctions = set()
            links = set()
            def lstat(self, path):
                mode = stat.S_IFREG if path in {owner.path, owner.manifest} else stat.S_IFDIR
                return NS(st_mode=stat.S_IFLNK if path in self.links else mode)
            def resolve(self, path):
                return self.redirects.get(path, path)
            def junction(self, path):
                return path in self.junctions
            def exists(self, path):
                return self.created if path == owner.path else True
            def read(self, path):
                return json.dumps(self.manifest)
        io = FakeIO()
        io.redirects, io.junctions, io.links = {}, set(), set()
        io.manifest = dict(io.manifest)
        calls = []
        url = NS(drivername='sqlite', database=owner.path.as_posix(), host=None,
                 port=None, username=None, password=None, query={})
        engine = NS(dialect=NS(name='sqlite'), url=url, pool=type('QueuePool', (), {'checkedout': lambda self: 0})(),
                    dispose=lambda: calls.append('dispose'))
        def create():
            calls.append('create')
            io.created = True
        db = NS(engine=engine, engines={None: engine}, create_all=create,
                session=NS(remove=lambda: calls.append('remove')))
        app = NS(config={'TESTING': True, 'SQLALCHEMY_DATABASE_URI': owner.uri})
        return owned, owner, io, app, db, calls

    def test_owned_file_preflight_identity_and_retained_cleanup(self):
        owned, owner, io, app, db, calls = self.fixture()
        self.assertEqual(owned.preflight_owned_database(owner.run_id, io), owner)
        with owned.owned_database_scope(app, db, owner, io):
            self.assertIs(owned.assert_owned_database(app, db, owner, io=io), db.engine)
            calls.append('body')
        self.assertEqual(calls, ['create', 'body', 'remove', 'remove', 'dispose'])
        self.assertTrue(io.created, 'Синтетический файл сохранён, не удалён')
        with self.assertRaises(IsolationError):
            assert_memory_database(app, db)
        with self.assertRaises(IsolationError):
            owned.preflight_owned_database(owner.run_id, io)

    def test_manifest_run_redirect_and_existing_file_refused_before_ddl(self):
        changes = [
            lambda o, i: setattr(i, 'created', True),
            lambda o, i: i.manifest.update(run_id='foreign'),
            lambda o, i: i.manifest.update(purpose='production'),
            lambda o, i: i.manifest.update(database='foreign.sqlite'),
            lambda o, i: i.manifest.update(mode='memory'),
            lambda o, i: i.redirects.update({o.directory: o.run_dir.parent}),
            lambda o, i: i.redirects.update({o.manifest: o.run_dir.parent / 'manifest.json'}),
            lambda o, i: i.junctions.add(o.run_dir),
            lambda o, i: i.links.add(o.manifest),
        ]
        for change in changes:
            owned, owner, io, app, db, calls = self.fixture()
            change(owner, io)
            with self.subTest(change=change), self.assertRaises(IsolationError):
                with owned.owned_database_scope(app, db, owner, io):
                    self.fail('Неверный manifest/путь допущен')
            self.assertEqual(calls, [])
        owned, owner, io, app, db, calls = self.fixture()
        with self.assertRaises(IsolationError):
            owned.preflight_owned_database('../foreign', io)
        from dataclasses import replace
        with self.assertRaises(IsolationError):
            owned.assert_owned_database(app, db, replace(owner, path=owner.run_dir.parent / 'foreign.sqlite'), 'create', io)
        self.assertEqual(calls, [])

    def test_real_engine_uri_pool_and_bind_substitutions_refused(self):
        changes = [
            lambda a, d: a.config.update(TESTING=False),
            lambda a, d: a.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///foreign.sqlite'),
            lambda a, d: a.config.update(SQLALCHEMY_BINDS={'foreign': 'sqlite://'}),
            lambda a, d: setattr(d.engine.url, 'database', 'foreign.sqlite'),
            lambda a, d: setattr(d.engine.url, 'drivername', 'postgresql'),
            lambda a, d: setattr(d.engine.url, 'query', {'mode': 'memory'}),
            lambda a, d: setattr(d.engine.url, 'host', 'server'),
            lambda a, d: setattr(d.engine.url, 'password', 'dummy'),
            lambda a, d: setattr(d.engine, 'pool', type('StaticPool', (), {})()),
        ]
        for change in changes:
            owned, owner, io, app, db, calls = self.fixture()
            change(app, db)
            with self.subTest(change=change), self.assertRaises(IsolationError):
                with owned.owned_database_scope(app, db, owner, io):
                    self.fail('Чужой engine допущен')
            self.assertEqual(calls, [])

    def test_owned_cleanup_closes_even_after_create_body_remove_or_guard_error(self):
        for failure in ['create', 'body', 'remove', 'manifest', 'uri', 'checkedout']:
            owned, owner, io, app, db, calls = self.fixture()
            def fail():
                calls.append(failure)
                raise RuntimeError('Имитированная ошибка')
            if failure == 'create':
                db.create_all = fail
            if failure == 'remove':
                db.session.remove = fail
            with self.subTest(failure=failure), self.assertRaises((RuntimeError, IsolationError)):
                with owned.owned_database_scope(app, db, owner, io):
                    if failure == 'body':
                        fail()
                    if failure == 'manifest':
                        io.manifest.update(run_id='foreign')
                    if failure == 'checkedout':
                        db.engine.pool.checkedout = lambda: 1
                    if failure == 'uri':
                        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///foreign.sqlite'
            self.assertEqual(calls[-1], 'dispose')
            self.assertIn('remove', calls)
            self.assertNotIn('drop', calls)


class FinalizePrecommitPreparationChecks(unittest.TestCase):
    def probe(self):
        import finalize_precommit_probe
        return finalize_precommit_probe

    def test_probe_import_and_ast_do_not_start_runtime(self):
        probe = self.probe()
        tree = ast.parse(Path(probe.__file__).read_text(encoding='utf-8'))
        forbidden = ('app', 'sqlalchemy', 'threading', 'sqlite3', 'pytest')
        for node in tree.body:
            if isinstance(node, ast.Import):
                self.assertFalse(any(alias.name.startswith(forbidden) for alias in node.names))
            if isinstance(node, ast.ImportFrom):
                self.assertFalse((node.module or '').startswith(forbidden))
        self.assertIn("if __name__ == '__main__':", Path(probe.__file__).read_text(encoding='utf-8'))

    def test_probe_exact_argv_clean_env_and_cwd(self):
        probe = self.probe()
        identity = '12345678-1234-1234-1234-123456789abc'
        env = {'VTT_FINALIZE_RUN_ID': identity, 'PYTHON_DOTENV_DISABLED': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
        probe.validate_invocation([identity], env, probe.ROOT)
        for args in ([], [identity, 'extra'], ['../foreign']):
            with self.assertRaises(IsolationError):
                probe.validate_invocation(args, env, probe.ROOT)
        for change in ({'DATABASE_URL': 'unread'}, {'VTT_ANY': 'x'}, {'PYTHONPATH': 'x'}, {'PYTHON_DOTENV_DISABLED': '0'}):
            with self.assertRaises(IsolationError):
                probe.validate_invocation([identity], {**env, **change}, probe.ROOT)
        with self.assertRaises(IsolationError):
            probe.validate_invocation([identity], env, probe.ROOT.parent)

    def test_probe_parent_permit_cannot_change_identity_or_purpose(self):
        probe = self.probe()
        value = dict(schema_version=1, run_id='own', purpose='test', child_pid=123, created='456')
        probe.validate_permit(value, 'own', 123)
        for change in ({'run_id': 'other'}, {'child_pid': 124}, {'created': 'bad'}, {'purpose': 'production'}, {'path': 'foreign'}):
            with self.assertRaises(IsolationError):
                probe.validate_permit({**value, **change}, 'own', 123)

    def model(self):
        w = dict(thread=1, context=2, session=3, connection=4, transaction=5, driver=6,
                 in_transaction=True, claim_flushed=True, receipt_flushed=1, initial_revision=7, engine=99)
        c = dict(thread=11, context=12, session=13, connection=14, transaction=15, driver=16,
                 in_transaction=True, before_flush_in_transaction=False, read_revision=7, engine=99)
        return w, c, dict(winner_precommit=1., contender_update=2.)

    def test_probe_rejects_same_connection_postcommit_and_wrong_claim(self):
        probe = self.probe()
        w, c, phases = self.model()
        probe.validate_overlap(w, c, phases)
        for key in ('thread', 'context', 'session', 'connection', 'transaction', 'driver'):
            with self.assertRaises(IsolationError):
                probe.validate_overlap(w, {**c, key: w[key]}, phases)
        for change in ({'in_transaction': False}, {'read_revision': 8}, {'before_flush_in_transaction': True}, {'engine': 100}):
            with self.assertRaises(IsolationError):
                probe.validate_overlap(w, {**c, **change}, phases)
        for change in ({'winner_committed': 3.}, {'contender_update': .5}, {'winner_precommit': None}):
            with self.assertRaises(IsolationError):
                probe.validate_overlap(w, c, {**phases, **change})
        with self.assertRaises(IsolationError):
            probe.validate_overlap({**w, 'receipt_flushed': 0}, c, phases)

    def test_probe_shared_deadline_not_two_independent_join_budgets(self):
        probe = self.probe()
        first = probe.remaining(60, 0, 35)
        second = probe.remaining(60, first, 35)
        self.assertEqual(first + second, 60)
        self.assertEqual(probe.remaining(60, 70, 35), 0)

    def test_probe_cleanup_refuses_alive_threads_or_outstanding_connections(self):
        probe = self.probe()
        probe.validate_cleanup(False, 0)
        for alive, checkedout in ((True, 0), (False, 1), (True, 2)):
            with self.assertRaises(IsolationError):
                probe.validate_cleanup(alive, checkedout)
        text = Path(probe.__file__).read_text(encoding='utf-8')
        self.assertIn('os._exit(2)', text)

    def test_probe_trace_predicate_never_requires_sql_execution(self):
        probe = self.probe()
        self.assertTrue(probe.trace_is_claim('UPDATE deferred_combat_actions SET status=?'))
        for sql in ('SELECT * FROM deferred_combat_actions', 'UPDATE lobby_characters SET data=?', 'COMMIT', 'BEGIN'):
            self.assertFalse(probe.trace_is_claim(sql))

    def test_probe_owned_configuration_rejected_before_any_fake_ddl(self):
        owned, owner, io, app, db, calls = BrowserOwnedFileChecks().fixture()
        for change in ({'TESTING': False}, {'SQLALCHEMY_DATABASE_URI': 'sqlite://'}, {'SQLALCHEMY_BINDS': {'other': 'sqlite://'}}):
            original = dict(app.config)
            app.config.update(change)
            with self.assertRaises(IsolationError):
                owned.assert_owned_database(app, db, owner, 'create', io)
            app.config = original
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
