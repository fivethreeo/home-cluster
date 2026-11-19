import contextlib
import fcntl
import fnmatch
import grp
import importlib.util
import io
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, StrictUndefined
import yaml


ROLE = Path(__file__).resolve().parents[1] / "roles/application"
spec = importlib.util.spec_from_file_location("application_backup", ROLE / "files/application-backup.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


def configuration(directory):
    root = Path(directory)
    media = root / "media"
    media.mkdir()
    return {
        "app": "prosango",
        "database": "prosango",
        "database_user": "prosango",
        "media_dir": str(media),
        "media_user": pwd.getpwuid(os.getuid()).pw_name,
        "media_group": grp.getgrgid(os.getgid()).gr_name,
        "state_dir": str(root / "state"),
        "repository": str(root / "repository"),
        "region": "eu-north-1",
        "retention_days": 14,
        "password": 'test-only restic "password" ${literal} \\',
        "access_key_id": "test-key",
        "secret_access_key": "test-only-aws-secret",
    }


class BackupRunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = configuration(self.directory.name)

    @patch.object(backup.subprocess, "run")
    def test_backup_exports_validates_uploads_then_prunes_only_this_app(self, run):
        backup.backup(self.config)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][:5], ["runuser", "--user", "postgres", "--", "pg_dump"])
        self.assertIn("--dbname=prosango", commands[0])
        self.assertEqual(commands[1][:2], ["pg_restore", "--list"])
        self.assertEqual(commands[2][:6], ["restic", "backup", "--host", "prosango", "--tag", "prosango"])
        self.assertIn(self.config["media_dir"], commands[2])
        pending = commands[2][7]
        self.assertEqual(commands[3], [
            "restic", "tag", "--host", "prosango", "--tag", "prosango," + pending,
            "--add", "app-backup-complete", "--remove", pending,
        ])
        self.assertEqual(commands[4], [
            "restic", "forget", "--host", "prosango", "--tag", "prosango,app-backup-complete",
            "--group-by", "host,tags", "--keep-within", "14d", "--prune",
        ])
        self.assertFalse((Path(self.config["state_dir"]) / "database.dump").exists())
        self.assertNotIn("RESTIC_PASSWORD", run.call_args_list[0].kwargs["env"])
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", run.call_args_list[0].kwargs["env"])
        self.assertEqual(run.call_args_list[2].kwargs["env"]["RESTIC_PASSWORD"], self.config["password"])
        self.assertNotIn(self.config["secret_access_key"], repr(commands))
        self.assertNotIn(self.config["password"], repr(commands))

    def test_failure_does_not_run_later_phases_and_removes_plaintext_dump(self):
        for failed_phase, exit_code in ((0, 1), (1, 1), (2, 1), (2, 3), (3, 1), (4, 1)):
            with self.subTest(failed_phase=failed_phase, exit_code=exit_code):
                def invoke(*args, **kwargs):
                    if invoke.count == failed_phase:
                        raise subprocess.CalledProcessError(exit_code, args[0])
                    invoke.count += 1
                invoke.count = 0
                with patch.object(backup.subprocess, "run", side_effect=invoke) as run:
                    with self.assertRaises(subprocess.CalledProcessError):
                        backup.backup(self.config)
                self.assertEqual(run.call_count, failed_phase + 1)
                self.assertFalse((Path(self.config["state_dir"]) / "database.dump").exists())

    @patch.object(backup.subprocess, "run")
    def test_overlapping_run_cannot_touch_the_active_runs_dump(self, run):
        state = Path(self.config["state_dir"])
        state.mkdir()
        dump = state / "database.dump"
        dump.write_bytes(b"active dump")
        with (state / "backup.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                backup.backup(self.config)
        run.assert_not_called()
        self.assertEqual(dump.read_bytes(), b"active dump")

    @patch.object(backup.subprocess, "run")
    def test_missing_media_or_invalid_retention_cannot_create_a_partial_backup(self, run):
        for replacement in ({"media_dir": self.directory.name + "/missing"}, {"retention_days": 0}):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                backup.backup(self.config | replacement)
        run.assert_not_called()

    def test_parent_credentials_cannot_override_the_repository_or_leak_into_dump(self):
        with patch.dict(os.environ, {
            "RESTIC_REPOSITORY_FILE": "/unrelated/repository",
            "RESTIC_PASSWORD_COMMAND": "unrelated-command",
            "AWS_SESSION_TOKEN": "unrelated-session",
            "PGHOST": "unrelated-database",
        }):
            environment = backup.restic_environment(self.config)
            self.assertNotIn("RESTIC_REPOSITORY_FILE", environment)
            self.assertNotIn("RESTIC_PASSWORD_COMMAND", environment)
            self.assertNotIn("AWS_SESSION_TOKEN", environment)
            self.assertNotIn("PGHOST", environment)

    def test_interruption_removes_plaintext_dump_without_upload_or_retention(self):
        def interrupt(*args, **kwargs):
            output = kwargs["stdout"]
            output.write(b"partial database dump")
            output.flush()
            self.assertEqual(os.fstat(output.fileno()).st_mode & 0o777, 0o600)
            backup.interrupted(15, None)

        with patch.object(backup.subprocess, "run", side_effect=interrupt) as run:
            with self.assertRaises(InterruptedError):
                backup.backup(self.config)
        self.assertEqual(run.call_count, 1)
        self.assertFalse((Path(self.config["state_dir"]) / "database.dump").exists())

    def test_restic_admin_commands_forward_options_without_a_shell(self):
        path = Path(self.directory.name) / "config.json"
        path.write_text(json.dumps(self.config))
        arguments = ["restore", "latest", "--target", "/separate/restore"]
        with (
            patch.object(sys, "argv", ["backup", str(path), "restic", *arguments]),
            patch.object(backup, "restic") as restic,
            patch.object(backup.signal, "signal"),
            patch.object(backup.os, "umask"),
        ):
            self.assertEqual(backup.main(), 0)
        restic.assert_called_once_with(self.config, arguments, display=True)

    def test_command_failure_never_logs_credentials(self):
        path = Path(self.directory.name) / "config.json"
        path.write_text(json.dumps(self.config))
        output = io.StringIO()
        error = subprocess.CalledProcessError(
            1, ["restic"], stderr=self.config["secret_access_key"] + self.config["password"],
        )
        with (
            patch.object(sys, "argv", ["backup", str(path), "run"]),
            patch.object(backup, "backup", side_effect=error),
            patch.object(backup.signal, "signal"),
            patch.object(backup.os, "umask"),
            contextlib.redirect_stderr(output),
        ):
            self.assertEqual(backup.main(), 1)
        self.assertNotIn(self.config["secret_access_key"], output.getvalue())
        self.assertNotIn(self.config["password"], output.getvalue())
        self.assertIn("failed", output.getvalue())


class RestoreRunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = configuration(self.directory.name)
        self.media = Path(self.config["media_dir"])
        (self.media / "live.txt").write_text("live data")
        self.snapshot = {
            "id": "a" * 64, "hostname": "prosango", "tags": ["prosango", "app-backup-complete"],
            "paths": [str(Path(self.config["state_dir"]) / "database.dump"), str(self.media)],
        }

    def stage_files(self, stage):
        dump = stage / Path(self.snapshot["paths"][0]).relative_to("/")
        dump.parent.mkdir(parents=True)
        dump.write_bytes(b"test archive")
        media = stage / self.media.relative_to("/")
        media.mkdir(parents=True)
        (media / "snapshot.txt").write_text("snapshot data")

    def prepared(self):
        stage = Path(self.config["state_dir"]) / "restore-test"
        stage.mkdir(mode=0o700, parents=True)
        self.stage_files(stage)
        (stage / "restore.json").write_text(json.dumps(
            backup.restore_context(self.config) | {"snapshot": self.snapshot["id"]},
        ))
        return stage

    def test_prepare_validates_application_and_archive_without_touching_live_data(self):
        def restic(config, arguments, **kwargs):
            if arguments[0] == "snapshots":
                return SimpleNamespace(stdout=json.dumps([self.snapshot]))
            self.assertIn("--verify", arguments)
            self.stage_files(Path(arguments[-1]))

        with patch.object(backup, "restic", side_effect=restic), patch.object(backup.subprocess, "run") as run:
            result = backup.prepare_restore(self.config, self.snapshot["id"][:8])
        self.assertEqual(result["snapshot"], self.snapshot["id"])
        self.assertEqual((self.media / "live.txt").read_text(), "live data")
        self.assertEqual(run.call_args.args[0][:2], ["pg_restore", "--list"])
        self.assertEqual(len(run.call_args_list), 1)

    @patch.object(backup, "restic")
    def test_latest_other_apps_and_incomplete_snapshots_are_rejected(self, restic):
        for invalid in ("latest", "--repository=other", "../snapshot"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                backup.prepare_restore(self.config, invalid)
        restic.assert_not_called()
        for snapshot in (
            self.snapshot | {"hostname": "template", "tags": ["template"]},
            self.snapshot | {"tags": ["prosango", "pending-upload"]},
            self.snapshot | {"paths": [str(self.media)]},
        ):
            restic.reset_mock()
            restic.return_value = SimpleNamespace(stdout=json.dumps([snapshot]))
            with self.subTest(snapshot=snapshot), self.assertRaises(ValueError):
                backup.prepare_restore(self.config, self.snapshot["id"])
            self.assertEqual(restic.call_count, 1)
        self.assertEqual((self.media / "live.txt").read_text(), "live data")

    @patch.object(backup, "postgres")
    def test_apply_swaps_complete_media_and_preserves_pre_restore_data(self, postgres):
        stage = self.prepared()
        outside = Path(self.directory.name) / "outside-media.txt"
        outside.write_text("outside data")
        outside.chmod(0o600)
        staged_media = stage / self.media.relative_to("/")
        (staged_media / "linked-upload").symlink_to(outside)
        result = backup.apply_restore(self.config, str(stage))
        self.assertFalse((self.media / "live.txt").exists())
        self.assertEqual((self.media / "snapshot.txt").read_text(), "snapshot data")
        self.assertEqual((Path(result["previous_media"]) / "live.txt").read_text(), "live data")
        self.assertEqual((self.media / "snapshot.txt").stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.media.stat().st_mode & 0o7777, 0o2750)
        self.assertTrue((self.media / "linked-upload").is_symlink())
        self.assertEqual(outside.stat().st_mode & 0o777, 0o600)
        commands = [call.args[0] for call in postgres.call_args_list]
        self.assertEqual([command[0] for command in commands], ["createdb", "pg_restore", "psql"])
        self.assertIn("--exit-on-error", commands[1])
        self.assertIn("--role=prosango", commands[1])
        self.assertIn("BEGIN;", commands[2][-1])
        self.assertIn("COMMIT;", commands[2][-1])
        self.assertIn("ALLOW_CONNECTIONS false", commands[2][-1])
        with self.assertRaises(ValueError):
            backup.apply_restore(self.config, str(stage))
        self.assertEqual(postgres.call_count, 3)

    def test_failed_database_import_keeps_live_media_untouched(self):
        stage = self.prepared()
        with patch.object(backup, "postgres", side_effect=[
            None, subprocess.CalledProcessError(1, ["pg_restore"]),
        ]) as postgres:
            with self.assertRaises(subprocess.CalledProcessError):
                backup.apply_restore(self.config, str(stage))
        self.assertEqual((self.media / "live.txt").read_text(), "live data")
        self.assertEqual(postgres.call_count, 2)
        self.assertTrue((stage / "restore.json").exists())

    def test_failed_database_swap_retains_both_media_versions_and_recovery_manifest(self):
        stage = self.prepared()
        with patch.object(backup, "postgres", side_effect=[
            None, None, subprocess.CalledProcessError(1, ["psql"]),
        ]):
            with self.assertRaises(subprocess.CalledProcessError):
                backup.apply_restore(self.config, str(stage))
        manifest = json.loads((stage / "restore.json").read_text())
        self.assertNotIn("applied", manifest)
        self.assertEqual((self.media / "snapshot.txt").read_text(), "snapshot data")
        self.assertEqual((Path(manifest["recovery"]["previous_media"]) / "live.txt").read_text(), "live data")

    @patch.object(backup, "postgres")
    def test_context_mismatch_or_active_backup_prevents_application(self, postgres):
        stage = self.prepared()
        with self.assertRaises(ValueError):
            backup.apply_restore(self.config | {"database": "template"}, str(stage))
        with (Path(self.config["state_dir"]) / "backup.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                backup.apply_restore(self.config, str(stage))
        postgres.assert_not_called()
        self.assertEqual((self.media / "live.txt").read_text(), "live data")


class BackupDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
        self.environment = Environment(loader=FileSystemLoader(ROLE / "templates"), undefined=StrictUndefined)
        self.environment.filters["bool"] = bool
        self.environment.filters["to_json"] = json.dumps
        self.environment.filters["from_json"] = json.loads
        self.environment.tests["match"] = lambda value, pattern: re.match(pattern, value) is not None
        self.variables = self.defaults | {
            "app_name": "prosango", "app_home": "/srv/prosango",
            "app_database": "prosango", "app_django_enabled": True, "app_media_enabled": True,
            "app_django_host": "sugar", "app_postgresql_host": "sugar",
            "app_database_host": "127.0.0.1", "inventory_hostname": "sugar",
            "app_secrets_dir": "/etc/ansible-app/prosango",
            "app_backup_endpoint": "s3.eu-north-1.amazonaws.com",
            "app_backup_repository": "s3:https://s3.eu-north-1.amazonaws.com/safe-and-sound/prosango",
            "app_backup_password": 'test-only "password" ${literal} \\ ' * 2,
            "app_backup_provision_aws": False,
            "app_backup_project_stack_name": "home-cluster-backup-prosango",
            "app_backup_aws_access_key_id": "test-key",
            "app_backup_aws_secret_access_key": 'test-only "secret" \\',
        }

    def permitted(self, **overrides):
        tasks = yaml.safe_load((ROLE / "tasks/backup-validate.yml").read_text())
        variables = self.variables | overrides
        return all(
            self.environment.compile_expression(condition)(**variables)
            for task in tasks for condition in task["ansible.builtin.assert"]["that"]
        )

    def test_each_app_has_an_isolated_repository_in_the_shared_bucket(self):
        repositories = []
        for app in ("prosango", "template", "enjoy"):
            endpoint = self.environment.from_string(self.defaults["app_backup_endpoint"]).render(
                app_backup_region=self.defaults["app_backup_region"],
            )
            repository = self.environment.from_string(self.defaults["app_backup_repository"]).render(
                app_backup_endpoint=endpoint, app_backup_bucket=self.defaults["app_backup_bucket"], app_name=app,
            )
            self.assertEqual(repository, f"s3:https://s3.eu-north-1.amazonaws.com/safe-and-sound/{app}")
            repositories.append(repository)
        self.assertEqual(len(set(repositories)), 3)

    def test_unsupported_topology_missing_credentials_and_unsafe_retention_are_rejected(self):
        self.assertTrue(self.permitted())
        self.assertTrue(self.permitted(
            app_backup_bucket="another-backup-bucket",
            app_backup_repository=self.variables["app_backup_repository"].replace(
                "safe-and-sound", "another-backup-bucket",
            ),
        ))
        for invalid in (
            {"app_django_enabled": False}, {"app_media_enabled": False},
            {"app_postgresql_host": "database"}, {"app_django_host": "django"},
            {"app_database_host": "192.0.2.1"},
            {"app_backup_repository": self.variables["app_backup_repository"].replace("/prosango", "/template")},
            {"app_backup_repository": self.variables["app_backup_repository"].replace("safe-and-sound", "other-bucket")},
            {"app_backup_repository": self.variables["app_backup_repository"].replace("/prosango", "/shared/prosango")},
            {"app_backup_bucket": "safe-and-sound/shared",
             "app_backup_repository": self.variables["app_backup_repository"].replace("/prosango", "/shared/prosango")},
            {"app_backup_retention_days": 0}, {"app_backup_retention_days": "1.5"},
            {"app_backup_schedule": "daily\nUnexpected=setting"},
            {"app_backup_password": ""}, {"app_backup_aws_secret_access_key": ""},
        ):
            with self.subTest(invalid=invalid):
                self.assertFalse(self.permitted(**invalid))

    def test_secret_config_round_trips_without_exposing_credentials_in_units(self):
        config = json.loads(self.environment.get_template("backup.json.j2").render(self.variables))
        self.assertEqual(config["password"], self.variables["app_backup_password"])
        self.assertEqual(config["secret_access_key"], self.variables["app_backup_aws_secret_access_key"])
        for filename in ("backup.service.j2", "backup.timer.j2"):
            unit = self.environment.get_template(filename).render(self.variables)
            self.assertNotIn(config["password"], unit)
            self.assertNotIn(config["secret_access_key"], unit)
        tasks = yaml.safe_load((ROLE / "tasks/backup-setup.yml").read_text())
        secret = next(task for task in tasks if "ansible.builtin.template" in task
                      and task["ansible.builtin.template"]["src"] == "backup.json.j2")
        self.assertTrue(secret["no_log"])
        self.assertEqual(secret["ansible.builtin.template"]["mode"], "0600")

    def test_managed_secret_overrides_legacy_keys_without_changing_the_restic_password(self):
        credentials = {
            "AccessKeyId": "generated-key",
            "SecretAccessKey": "generated-secret",
            "ProjectName": "prosango",
            "BucketName": "safe-and-sound",
        }
        variables = self.variables | {
            "app_backup_aws_secret_id": "project-secret",
            "app_backup_aws_secret_result": {"stdout": json.dumps(credentials)},
        }
        self.assertTrue(self.permitted(
            app_backup_aws_secret_id="project-secret",
            app_backup_aws_access_key_id="",
            app_backup_aws_secret_access_key="",
        ))
        config = json.loads(self.environment.get_template("backup.json.j2").render(variables))
        self.assertEqual(config["access_key_id"], credentials["AccessKeyId"])
        self.assertEqual(config["secret_access_key"], credentials["SecretAccessKey"])
        self.assertEqual(config["password"], self.variables["app_backup_password"])

    def test_automatic_provisioning_needs_only_the_restic_password_not_precreated_keys(self):
        variables = {
            "app_backup_provision_aws": True,
            "app_backup_aws_secret_id": "",
            "app_backup_aws_access_key_id": "",
            "app_backup_aws_secret_access_key": "",
        }
        self.assertTrue(self.permitted(**variables))
        self.assertFalse(self.permitted(**(variables | {"app_backup_password": ""})))
        self.assertFalse(self.permitted(**(variables | {"app_backup_access_key_serial": "1.5"})))

    @unittest.skipUnless(shutil.which("ansible-playbook"), "Requires ansible-playbook")
    def test_ansible_fetches_only_matching_secrets_and_never_logs_credentials(self):
        tasks = yaml.safe_load((ROLE / "tasks/backup-setup.yml").read_text())
        retrieval = next(task for task in tasks if task["name"] == "Retrieve managed backup credentials")
        fetch = retrieval["block"][0]
        self.assertTrue(retrieval["no_log"])
        self.assertEqual(fetch["delegate_to"], "localhost")
        self.assertFalse(fetch["become"])
        self.assertFalse(fetch["changed_when"])
        credentials = {
            "AccessKeyId": "test-managed-access-key",
            "SecretAccessKey": 'test-managed-secret-"quoted"-\\',
            "ProjectName": "prosango",
            "BucketName": "safe-and-sound",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "aws"
            executable.write_text(
                f"#!{sys.executable}\n"
                "import os, sys\n"
                "print(os.environ['TEST_BACKUP_SECRET'])\n"
                "sys.exit(int(os.environ['TEST_AWS_FAILURE']))\n"
            )
            executable.chmod(0o700)
            for index, (payload, command_failure, success) in enumerate((
                (json.dumps(credentials), False, True),
                (json.dumps(credentials | {"ProjectName": "template"}), False, False),
                (json.dumps(credentials | {"BucketName": "another-bucket"}), False, False),
                (json.dumps(credentials | {"SecretAccessKey": ""}), False, False),
                (json.dumps(credentials | {"AccessKeyId": None}), False, False),
                ("invalid JSON", False, False),
                (json.dumps(credentials), True, False),
            )):
                with self.subTest(payload=payload, command_failure=command_failure):
                    output = root / f"backup-{index}.json"
                    playbook = root / "playbook.yml"
                    playbook.write_text(yaml.safe_dump([{
                        "hosts": "localhost",
                        "gather_facts": False,
                        "vars": self.variables | {
                            "ansible_python_interpreter": sys.executable,
                            "app_backup_aws_secret_id": "project-secret",
                        },
                        "tasks": [
                            retrieval,
                            {
                                "name": "Render protected backup configuration",
                                "ansible.builtin.template": {
                                    "src": str(ROLE / "templates/backup.json.j2"),
                                    "dest": str(output), "mode": "0600",
                                },
                                "no_log": True,
                            },
                        ],
                    }]))
                    result = subprocess.run(
                        ["ansible-playbook", "-i", "localhost,", "-c", "local", str(playbook)],
                        env=os.environ | {
                            "PATH": str(root) + os.pathsep + os.environ["PATH"],
                            "TEST_BACKUP_SECRET": payload,
                            "TEST_AWS_FAILURE": str(int(command_failure)),
                            "ANSIBLE_NOCOLOR": "1",
                        },
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60,
                    )
                    self.assertEqual(result.returncode == 0, success, result.stdout)
                    self.assertNotIn(credentials["AccessKeyId"], result.stdout)
                    self.assertNotIn("test-managed-secret", result.stdout)
                    self.assertEqual(output.exists(), success)
                    if success:
                        config = json.loads(output.read_text())
                        self.assertEqual(config["secret_access_key"], credentials["SecretAccessKey"])
                        self.assertEqual(config["access_key_id"], credentials["AccessKeyId"])
                        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(shutil.which("ansible-playbook"), "Requires ansible-playbook")
    def test_deploy_bootstraps_idempotently_and_never_reinitializes_unverified_storage(self):
        setup = yaml.safe_load((ROLE / "tasks/backup-setup.yml").read_text())
        retrieval = next(task for task in setup if task["name"] == "Retrieve managed backup credentials")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = (
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "args = sys.argv[1:]\n"
                "program = Path(sys.argv[0]).name\n"
                "state = Path(os.environ['TEST_BACKUP_STATE'])\n"
                "mode = os.environ['TEST_BACKUP_MODE']\n"
                "with open(os.environ['TEST_BACKUP_LOG'], 'a') as log:\n"
                "    log.write(json.dumps([program, args]) + '\\n')\n"
                "if program == 'aws':\n"
                "    if args[:2] == ['cloudformation', 'deploy']:\n"
                "        print('No changes to deploy' if state.exists() else 'Successfully created/updated stack')\n"
                "    elif args[:2] == ['cloudformation', 'describe-stacks']:\n"
                "        print(json.dumps([\n"
                "            {'OutputKey': 'CredentialsSecretArn', 'OutputValue': 'test-project-secret'},\n"
                "            {'OutputKey': 'Repository', 'OutputValue':\n"
                "             's3:https://s3.eu-north-1.amazonaws.com/safe-and-sound/prosango'}]))\n"
                "    elif args[:2] == ['secretsmanager', 'get-secret-value']:\n"
                "        print(json.dumps({'AccessKeyId': 'test-managed-key', 'SecretAccessKey': 'test-managed-secret',\n"
                "                          'ProjectName': 'prosango', 'BucketName': 'safe-and-sound'}))\n"
                "    elif args[:2] == ['s3api', 'list-object-versions']:\n"
                "        assert os.environ['AWS_ACCESS_KEY_ID'] == 'test-managed-key'\n"
                "        assert os.environ['AWS_SECRET_ACCESS_KEY'] == 'test-managed-secret'\n"
                "        assert not os.environ.get('AWS_SESSION_TOKEN')\n"
                "        assert args[args.index('--prefix') + 1] == 'prosango/'\n"
                "        assert json.loads(args[args.index('--cli-input-json') + 1]) == {'MaxKeys': 1}\n"
                "        assert '--no-paginate' in args\n"
                "        if mode == 'lookup-failure':\n"
                "            print('test-managed-secret')\n"
                "            sys.exit(1)\n"
                "        print('{}' if mode == 'invalid' else\n"
                "              json.dumps(not state.exists() and mode != 'history'))\n"
                "    else:\n"
                "        sys.exit(2)\n"
                "elif args[-1] == 'init':\n"
                "    assert not state.exists() and mode == 'empty'\n"
                "    state.write_text('initialized')\n"
                "elif args[-2:] == ['cat', 'config']:\n"
                "    sys.exit(0 if state.exists() and mode != 'wrong-password' else 1)\n"
                "else:\n"
                "    sys.exit(2)\n"
            )
            for name in ("aws", "backup-runner"):
                executable = root / name
                executable.write_text(script)
                executable.chmod(0o700)
            initialize = yaml.safe_load((ROLE / "tasks/backup-initialize.yml").read_text())
            for task in initialize[0]["block"]:
                if "retries" in task:
                    task["retries"] = 1
                    task["delay"] = 0
                command = task.get("ansible.builtin.command")
                if command and command["argv"][0] == "/usr/local/sbin/ansible-app-backup":
                    command["argv"][0] = str(root / "backup-runner")
            initialization_file = root / "initialize.yml"
            initialization_file.write_text(yaml.safe_dump(initialize))
            for label, mode, preexisting, success, checking, manage_bucket in (
                ("new", "empty", False, True, False, True),
                ("new", "empty", False, True, False, True),
                ("existing", "empty", True, True, False, True),
                ("wrong-password", "wrong-password", True, False, False, True),
                ("history", "history", False, False, False, True),
                ("lookup-failure", "lookup-failure", False, False, False, True),
                ("invalid", "invalid", False, False, False, True),
                ("check", "empty", True, True, True, True),
                ("external-bucket", "empty", False, True, False, False),
                ("rotation", "empty", True, True, False, True),
                ("recovery", "empty", False, False, False, True),
                ("legacy", "empty", False, True, False, False),
            ):
                with self.subTest(label=label):
                    target = root / label
                    target.mkdir(exist_ok=True)
                    state = target / "repository-state"
                    if preexisting:
                        state.write_text("existing")
                    was_initialized = state.exists()
                    log = target / "commands.jsonl"
                    log.write_text("")
                    output = target / "backup.json"
                    configuration_existed = output.exists()
                    playbook = root / "playbook.yml"
                    playbook.write_text(yaml.safe_dump([{
                        "hosts": "localhost", "gather_facts": False,
                        "vars": self.variables | {
                            "role_path": str(ROLE),
                            "ansible_python_interpreter": sys.executable,
                            "app_backup_provision_aws": label != "legacy",
                            "app_backup_manage_bucket": manage_bucket,
                            "app_backup_access_key_serial": 2 if label == "rotation" else "",
                            "app_restore_snapshot": "a" * 8 if label == "recovery" else "",
                            "app_backup_aws_access_key_id": "test-managed-key" if label == "legacy" else "",
                            "app_backup_aws_secret_access_key": "test-managed-secret" if label == "legacy" else "",
                            "app_secrets_dir": str(target),
                        },
                        "tasks": [
                            {"ansible.builtin.include_tasks": str(ROLE / "tasks/backup-provision.yml"),
                             "when": "app_backup_provision_aws | bool"},
                            retrieval,
                            {
                                "ansible.builtin.template": {
                                    "src": str(ROLE / "templates/backup.json.j2"),
                                    "dest": str(output), "mode": "0600",
                                },
                                "no_log": True,
                            },
                            {"ansible.builtin.include_tasks": str(initialization_file)},
                        ],
                    }]))
                    result = subprocess.run(
                        ["ansible-playbook", "-i", "localhost,", "-c", "local", str(playbook)]
                        + (["--check"] if checking else []),
                        env=os.environ | {
                            "PATH": str(root) + os.pathsep + os.environ["PATH"],
                            "TEST_BACKUP_STATE": str(state), "TEST_BACKUP_MODE": mode,
                            "TEST_BACKUP_LOG": str(log), "AWS_SESSION_TOKEN": "unrelated-session",
                            "ANSIBLE_NOCOLOR": "1",
                        },
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60,
                    )
                    self.assertEqual(result.returncode == 0, success, result.stdout)
                    self.assertNotIn("test-managed-secret", result.stdout)
                    commands = [json.loads(line) for line in log.read_text().splitlines()]
                    initialized = any(program == "backup-runner" and args[-1] == "init"
                                      for program, args in commands)
                    self.assertEqual(initialized, success and not was_initialized and not checking)
                    deployments = [args for program, args in commands
                                   if program == "aws" and args[:2] == ["cloudformation", "deploy"]]
                    self.assertEqual(len(deployments),
                                     0 if checking or label == "legacy" else (2 if manage_bucket else 1))
                    serials = [value for args in deployments for value in args if value.startswith("AccessKeySerial=")]
                    self.assertEqual(serials, ["AccessKeySerial=2"] if label == "rotation" else [])
                    if success and not checking:
                        self.assertEqual(json.loads(output.read_text())["access_key_id"], "test-managed-key")
                    if success and was_initialized and configuration_existed:
                        self.assertRegex(result.stdout, r"changed=0\b")

    def test_normal_deploy_links_provisioning_and_initialization_before_scheduling(self):
        main = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
        self.assertTrue(any(task.get("ansible.builtin.include_tasks") == "backup.yml" for task in main))
        setup = yaml.safe_load((ROLE / "tasks/backup-setup.yml").read_text())
        includes = [task.get("ansible.builtin.include_tasks") for task in setup]
        self.assertIn("backup-provision.yml", includes)
        self.assertEqual(includes[-1], "backup-initialize.yml")
        provision = next(task for task in setup if task.get("ansible.builtin.include_tasks") == "backup-provision.yml")
        self.assertEqual(provision["when"], "app_backup_provision_aws | bool")
        tasks = yaml.safe_load((ROLE / "tasks/backup.yml").read_text())
        enabled = next(task for task in tasks if task["name"] == "Configure enabled application backups")
        self.assertEqual(enabled["when"], "app_backup_enabled | bool")
        self.assertEqual(enabled["block"][0]["ansible.builtin.include_tasks"], "backup-setup.yml")
        self.assertEqual(enabled["block"][-1]["name"], "Enable scheduled application backups")

    def test_restore_precedes_migrations_and_restart_and_list_bypasses_deployment(self):
        tasks = yaml.safe_load((ROLE / "tasks/code.yml").read_text())
        names = [task["name"] for task in tasks]
        self.assertLess(names.index("Replace database and media with the prepared recovery snapshot"),
                        names.index("Apply database migrations"))
        self.assertLess(names.index("Apply database migrations"), names.index("Enable and start the deployed backend"))
        purge = next(task for task in tasks if task["name"].startswith("Discard pre-restore queued"))
        self.assertIn("app_restore_discard_queued_tasks | bool", purge["when"])
        self.assertTrue(self.defaults["app_restore_discard_queued_tasks"])
        self.assertLess(names.index(purge["name"]), names.index("Enable and start the deployed backend"))
        play = yaml.safe_load((ROLE.parents[1] / "deploy-code.yml").read_text())[0]
        listing = play["tasks"][0]
        self.assertEqual(listing["block"][-1]["ansible.builtin.meta"], "end_host")
        tasks = yaml.safe_load((ROLE / "tasks/backup-list.yml").read_text())
        self.assertEqual(set(key for task in tasks for key in task if key.startswith("ansible.builtin.")),
                         {"ansible.builtin.assert", "ansible.builtin.command", "ansible.builtin.debug"})
        command = next(task for task in tasks if "ansible.builtin.command" in task)
        self.assertFalse(command["changed_when"])
        prepare = yaml.safe_load((ROLE / "tasks/restore-prepare.yml").read_text())
        conditions = prepare[0]["ansible.builtin.assert"]["that"]
        self.assertIn("not ansible_check_mode", conditions)
        self.assertIn("not (app_frontend_only_update | bool)", conditions)

    def test_restored_cache_namespace_is_distinct_and_normal_deployments_preserve_it(self):
        template = (ROLE / "templates/prosango.env.j2").read_text()
        line = next(line for line in template.splitlines() if line.startswith("DJANGO_CACHE_KEY_PREFIX="))
        normal = self.environment.from_string(line).render(app_name="prosango")
        restored = self.environment.from_string(line).render(
            app_name="prosango", app_runtime_cache_prefix="prosango:restore-example",
        )
        self.assertNotEqual(normal, restored)
        self.assertIn("prosango:restore-example", restored)
        tasks = yaml.safe_load((ROLE / "tasks/code.yml").read_text())
        namespace = next(task for task in tasks if "ansible.builtin.set_fact" in task
                         and "app_runtime_cache_prefix" in task["ansible.builtin.set_fact"])
        self.assertNotIn("when", namespace)


class BackupInfrastructureTests(unittest.TestCase):
    def setUp(self):
        self.aws = ROLE.parents[1] / "aws"
        self.project = yaml.safe_load((self.aws / "backup-project.yml").read_text())

    def substitute(self, value, project="prosango"):
        value = value["Fn::Sub"]
        for key, replacement in {
            "AWS::Partition": "aws", "BucketName": "safe-and-sound", "ProjectName": project,
        }.items():
            value = value.replace("${" + key + "}", replacement)
        return value

    def test_project_permissions_cannot_list_or_modify_sibling_repositories(self):
        statements = self.project["Resources"]["BackupUser"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        listing = next(item for item in statements if item["Sid"] == "ListProject")
        patterns = [self.substitute(value) for value in listing["Condition"]["StringLike"]["s3:prefix"]]
        objects = next(item for item in statements if item["Sid"] == "ManageProjectRepository")
        pattern = self.substitute(objects["Resource"])
        for prefix, allowed in (
            ("prosango/", True), ("prosango/data/", True), ("", False),
            ("prosango", False), ("prosango-other/", False), ("template/", False),
        ):
            with self.subTest(prefix=prefix):
                self.assertEqual(any(fnmatch.fnmatchcase(prefix, value) for value in patterns), allowed)
                self.assertEqual(fnmatch.fnmatchcase("arn:aws:s3:::safe-and-sound/" + prefix + "config", pattern), allowed)
        self.assertIn("s3:DeleteObject", objects["Action"])
        self.assertIn("s3:ListBucketVersions", listing["Action"])
        self.assertNotIn("s3:DeleteObjectVersion", objects["Action"])
        self.assertNotIn("LoginProfile", self.project["Resources"]["BackupUser"]["Properties"])

    def test_generated_credentials_go_to_secrets_manager_not_stack_outputs(self):
        resources = self.project["Resources"]
        key = resources["BackupAccessKey"]
        self.assertEqual(key["Type"], "AWS::IAM::AccessKey")
        self.assertEqual(key["Properties"]["UserName"], {"Ref": "BackupUser"})
        self.assertEqual(key["Properties"]["Serial"], {"Ref": "AccessKeySerial"})
        self.assertNotEqual(key.get("DeletionPolicy"), "Retain")
        self.assertNotEqual(key.get("UpdateReplacePolicy"), "Retain")
        secret = resources["BackupCredentials"]
        value = secret["Properties"]["SecretString"]["Fn::Sub"]
        self.assertIn("${BackupAccessKey}", value)
        self.assertIn("${BackupAccessKey.SecretAccessKey}", value)
        rendered = self.substitute(secret["Properties"]["SecretString"]).replace(
            "${BackupAccessKey}", "test-key",
        ).replace("${BackupAccessKey.SecretAccessKey}", "test-secret+/=")
        self.assertEqual(json.loads(rendered), {
            "AccessKeyId": "test-key", "SecretAccessKey": "test-secret+/=",
            "ProjectName": "prosango", "BucketName": "safe-and-sound",
        })
        self.assertNotIn("BackupAccessKey", json.dumps(self.project["Outputs"]))
        self.assertEqual(self.project["Outputs"]["CredentialsSecretArn"]["Value"], {"Ref": "BackupCredentials"})

    def test_bucket_retains_encrypted_versions_without_expiring_restic_objects(self):
        template = yaml.safe_load((self.aws / "backup-bucket.yml").read_text())
        bucket = template["Resources"]["BackupBucket"]
        self.assertEqual(bucket["DeletionPolicy"], "Retain")
        self.assertEqual(bucket["UpdateReplacePolicy"], "Retain")
        properties = bucket["Properties"]
        self.assertTrue(all(properties["PublicAccessBlockConfiguration"].values()))
        self.assertEqual(properties["OwnershipControls"]["Rules"][0]["ObjectOwnership"], "BucketOwnerEnforced")
        self.assertEqual(properties["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]
                         ["ServerSideEncryptionByDefault"]["SSEAlgorithm"], "AES256")
        self.assertEqual(properties["VersioningConfiguration"]["Status"], "Enabled")
        for rule in properties["LifecycleConfiguration"]["Rules"]:
            self.assertFalse({"ExpirationInDays", "ExpirationDate", "NoncurrentVersionExpiration",
                              "NoncurrentVersionExpirationInDays", "ExpiredObjectDeleteMarker"} & rule.keys())
        policy = template["Resources"]["BackupBucketPolicy"]["Properties"]["PolicyDocument"]["Statement"]
        self.assertTrue(any(statement["Effect"] == "Deny"
                            and statement["Condition"]["Bool"]["aws:SecureTransport"] is False
                            for statement in policy))


@unittest.skipUnless(
    shutil.which("restic") and shutil.which("initdb") and shutil.which("pg_ctl") and os.geteuid() != 0,
    "Offline restore integration requires restic, PostgreSQL server tools and a non-root user",
)
class LocalRestoreTests(unittest.TestCase):
    def test_real_postgresql_and_media_round_trip_with_scoped_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = configuration(directory)
            media = Path(config["media_dir"]) / "example-upload.txt"
            media.write_bytes(b"original uploaded content")
            data, sockets = root / "pgdata", root / "socket"
            sockets.mkdir()
            real_run = subprocess.run

            def execute(command, **kwargs):
                try:
                    return real_run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
                except subprocess.CalledProcessError as error:
                    self.fail(error.stderr.decode(errors="replace"))

            command = ["initdb", "-D", str(data), "--username=postgres", "--auth-local=trust",
                       "--auth-host=reject", "--no-locale", "--encoding=UTF8"]
            if os.environ.get("BACKUP_TEST_PG_SHARE"):
                command += ["-L", os.environ["BACKUP_TEST_PG_SHARE"]]
            execute(command)
            execute(["pg_ctl", "-D", str(data), "-l", str(root / "postgres.log"),
                     "-o", shlex.join(["-c", "listen_addresses=", "-k", str(sockets)]), "-w", "start"])
            try:
                connection = ["--host=" + str(sockets), "--username=postgres"]
                execute(["psql", *connection, "--dbname=postgres", "-c", "CREATE ROLE prosango;"])
                execute(["createdb", *connection, "prosango"])
                execute(["psql", *connection, "--dbname=prosango", "-c",
                         "CREATE TABLE backup_probe (value text); INSERT INTO backup_probe VALUES ('restored');"])
                backup.restic(config, ["init"])
                for app in ("prosango", "template"):
                    backup.restic(config, [
                        "backup", "--host", app, "--tag", app, "--tag", "app-backup-complete",
                        "--time", "2000-01-01 00:00:00",
                        config["media_dir"],
                    ])
                pending_dump = Path(config["state_dir"]) / "database.dump"
                pending_dump.parent.mkdir(mode=0o700, exist_ok=True)
                pending_dump.write_bytes(b"partial snapshot fixture")
                backup.restic(config, [
                    "backup", "--host", "prosango", "--tag", "prosango", "--tag", "pending-upload",
                    "--time", "2000-01-01 00:00:00", str(pending_dump), config["media_dir"],
                ])

                def invoke(command, **kwargs):
                    if command[:4] == ["runuser", "--user", "postgres", "--"]:
                        command = [command[4], *connection,
                                   *(argument for argument in command[5:] if not argument.startswith("--host="))]
                    return real_run(command, **kwargs)

                with patch.object(backup.subprocess, "run", side_effect=invoke):
                    backup.backup(config)
                environment = backup.restic_environment(config)
                snapshots = json.loads(execute(["restic", "snapshots", "--json"], env=environment).stdout)
                self.assertEqual(len(snapshots), 3)
                own = next(snapshot for snapshot in snapshots
                           if snapshot["hostname"] == "prosango" and "app-backup-complete" in snapshot["tags"])
                self.assertFalse(own["time"].startswith("2000"))
                self.assertTrue(any(snapshot["hostname"] == "template" for snapshot in snapshots))
                backup.restic(config, ["check", "--read-data"])
                target = root / "restore"
                backup.restic(config, ["restore", own["id"], "--target", str(target)])
                self.assertEqual((target / media.relative_to("/")).read_bytes(), media.read_bytes())
                dump = target / (Path(config["state_dir"]) / "database.dump").relative_to("/")
                execute(["createdb", *connection, "restored"])
                execute(["pg_restore", *connection, "--dbname=restored", "--no-owner", str(dump)])
                result = execute(["psql", *connection, "--dbname=restored", "-Atc", "SELECT value FROM backup_probe"])
                self.assertEqual(result.stdout.strip(), b"restored")
                self.assertFalse((Path(config["state_dir"]) / "database.dump").exists())
                self.assertEqual(len(backup.snapshots(config)), 1)
                config_path = root / "backup.json"
                config_path.write_text(json.dumps(config))
                listed = execute([sys.executable, str(ROLE / "files/application-backup.py"),
                                  str(config_path), "list"])
                self.assertEqual([entry["id"] for entry in json.loads(listed.stdout)], [own["id"]])
                partial = next(snapshot for snapshot in snapshots if "pending-upload" in snapshot["tags"])
                with self.assertRaises(ValueError):
                    backup.prepare_restore(config, partial["id"])
                prepared = backup.prepare_restore(config, own["id"])
                execute(["psql", *connection, "--dbname=prosango", "-c",
                         "UPDATE backup_probe SET value = 'live changes';"])
                media.write_bytes(b"live media changes")
                (media.parent / "new-upload.txt").write_bytes(b"new live upload")
                with patch.object(backup.subprocess, "run", side_effect=invoke):
                    recovery = backup.apply_restore(config, prepared["stage"])
                self.assertEqual(media.read_bytes(), b"original uploaded content")
                self.assertFalse((media.parent / "new-upload.txt").exists())
                self.assertEqual((Path(recovery["previous_media"]) / media.name).read_bytes(), b"live media changes")
                result = execute(["psql", *connection, "--dbname=prosango", "-Atc", "SELECT value FROM backup_probe"])
                self.assertEqual(result.stdout.strip(), b"restored")
                result = execute(["psql", *connection, "--dbname=postgres", "-Atc",
                                  "SELECT datallowconn FROM pg_database WHERE datname = '"
                                  + recovery["previous_database"] + "'"])
                self.assertEqual(result.stdout.strip(), b"f")
            finally:
                execute(["pg_ctl", "-D", str(data), "-m", "immediate", "-w", "stop"])


if __name__ == "__main__":
    unittest.main()
