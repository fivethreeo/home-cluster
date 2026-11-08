import importlib.util
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml


SCRIPT = Path(__file__).resolve().parents[1] / "update-secret.py"
spec = importlib.util.spec_from_file_location("update_secret", SCRIPT)
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


class VaultUpdateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.password = self.root / "vault-password"
        self.password.write_text("test-only-vault-password")
        self.password.chmod(0o600)
        self.vault = self.root / "example-secrets.yml"
        self.options = ["--vault-password-file", str(self.password)]
        self.original = {
            "app_database_password": "old-test-password",
            "app_rabbitmq_password": "unchanged-test-password",
            "domeneshop_token": "unchanged-test-token",
        }
        subprocess.run(
            ["ansible-vault", "encrypt", "--output", str(self.vault), *self.options],
            input=yaml.safe_dump(self.original),
            text=True,
            check=True,
            capture_output=True,
        )

    def run_script(self, value, key="app_database_password"):
        return subprocess.run(
            [
                sys.executable, str(SCRIPT), "example", key,
                "--vault-dir", str(self.root), *self.options,
            ],
            input=value,
            text=True,
            capture_output=True,
        )

    def decrypt(self):
        result = subprocess.run(
            ["ansible-vault", "decrypt", str(self.vault), "--output", "-", *self.options],
            check=True,
            capture_output=True,
        )
        return yaml.safe_load(result.stdout)

    def test_piped_secret_preserves_other_keys_and_stays_encrypted(self):
        result = self.run_script("piped-test-secret\r\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = dict(self.original, app_database_password="piped-test-secret")
        self.assertEqual(self.decrypt(), expected)
        self.assertTrue(self.vault.read_bytes().startswith(b"$ANSIBLE_VAULT;"))
        self.assertNotIn("piped-test-secret", result.stdout + result.stderr)
        self.assertEqual(stat.S_IMODE(self.vault.stat().st_mode), 0o600)
        self.assertEqual(list(self.root.glob(".example-secrets.yml.*")), [])

    def test_no_input_generates_password_and_preserves_other_keys(self):
        result = self.run_script("")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.decrypt()
        value = data.pop("app_database_password")
        self.assertGreaterEqual(len(value), 32)
        self.assertNotEqual(value, self.original["app_database_password"])
        expected = dict(self.original)
        expected.pop("app_database_password")
        self.assertEqual(data, expected)

    def test_invalid_input_or_unknown_key_does_not_replace_vault(self):
        original = self.vault.read_bytes()
        for value, key in [
            ("\n", "app_database_password"),
            ("first\nsecond", "app_database_password"),
            ("test-secret", "misspelled_key"),
        ]:
            with self.subTest(key=key, value=value):
                result = self.run_script(value, key)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.vault.read_bytes(), original)
                if value.strip():
                    self.assertNotIn(value.strip(), result.stderr)

    def test_encryption_failure_keeps_original_and_cleans_temporary_file(self):
        original = self.vault.read_bytes()
        real_run = subprocess.run

        def fail_encrypt(command, **kwargs):
            if command[1] == "encrypt":
                raise subprocess.CalledProcessError(1, command)
            return real_run(command, **kwargs)

        with patch.object(updater.subprocess, "run", side_effect=fail_encrypt):
            with self.assertRaises(subprocess.CalledProcessError):
                updater.update_secret(
                    self.vault, "app_database_password", "new-test-password", self.options
                )
        self.assertEqual(self.vault.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".example-secrets.yml.*")), [])


if __name__ == "__main__":
    unittest.main()
