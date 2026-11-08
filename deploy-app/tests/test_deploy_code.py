import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'files/package-prosango.py'
spec = importlib.util.spec_from_file_location('package_prosango', SCRIPT)
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class CodeBundleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        self.write('django-project/manage.py', '# Django entry point\n')
        self.write('django-project/requirements.txt', 'Django\n')
        self.write('django-project/prosango/new_module.py', '# uncommitted source\n')
        self.write('django-project/.env.example', 'DJANGO_SECRET_KEY=\n')
        self.write('django-project/.env', 'test-only-private-value')
        self.write('django-project/db.sqlite3', 'test-only-local-database')
        self.write('django-project/media/upload.txt', 'test-only-upload')
        self.write('django-project/.venv/bin/python', 'test-only-local-environment')
        self.frontend = self.source / 'typescript-frontend/dist'
        manifest = json.dumps({'entries': {'index': {'initial': {'js': ['/static/js/app.js']}}}})
        self.write('typescript-frontend/dist/manifest.json', manifest)
        self.write('typescript-frontend/dist/editor/editor-manifest.json', manifest)
        self.write('typescript-frontend/dist/static/js/app.js', '// prebuilt output\n')
        self.output = self.root / 'bundle.tar.gz'

    def write(self, name, contents):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        return path

    def test_bundle_includes_new_source_and_prebuilt_assets_not_local_state(self):
        packager.build_bundle(self.source, self.frontend, self.output)
        with tarfile.open(self.output) as archive:
            names = archive.getnames()
        self.assertIn('django-project/prosango/new_module.py', names)
        self.assertIn('django-project/.env.example', names)
        self.assertIn('typescript-frontend/dist/static/js/app.js', names)
        for name in [
            'django-project/.env', 'django-project/db.sqlite3',
            'django-project/media/upload.txt', 'django-project/.venv/bin/python',
        ]:
            self.assertNotIn(name, names)

    def test_same_sources_produce_same_release_checksum(self):
        packager.build_bundle(self.source, self.frontend, self.output)
        checksum = hashlib.sha256(self.output.read_bytes()).hexdigest()
        packager.build_bundle(self.source, self.frontend, self.output)
        self.assertEqual(hashlib.sha256(self.output.read_bytes()).hexdigest(), checksum)

    def test_missing_or_development_assets_fail_before_packaging(self):
        (self.frontend / 'editor/editor-manifest.json').unlink()
        with self.assertRaises(ValueError):
            packager.build_bundle(self.source, self.frontend, self.output)
        self.assertFalse(self.output.exists())
        self.write('typescript-frontend/dist/editor/editor-manifest.json', json.dumps({
            'entries': {'editor': {'initial': {'js': ['http://localhost:3000/editor.js']}}},
        }))
        with self.assertRaises(ValueError):
            packager.build_bundle(self.source, self.frontend, self.output)

    def test_symlinks_cannot_copy_files_outside_the_checkout(self):
        (self.source / 'django-project/prosango/leak.py').symlink_to(self.root / 'private.py')
        with self.assertRaises(ValueError):
            packager.build_bundle(self.source, self.frontend, self.output)

    def test_node_only_bundle_requires_only_selected_prebuilt_backend(self):
        for name in ['package.json', 'package-lock.json']:
            self.write(f'typescript-backend/{name}', '{}')
        self.write('typescript-backend/dist/index.js', '// compiled backend')
        self.write('typescript-backend/src/index.ts', '// do not build on target')
        self.write('typescript-backend/.env', 'test-only-secret')
        packager.build_bundle(
            self.source, self.root / 'missing-frontend', self.output, ['typescript-backend'],
        )
        with tarfile.open(self.output) as archive:
            self.assertEqual(set(archive.getnames()), {
                'typescript-backend/package.json', 'typescript-backend/package-lock.json',
                'typescript-backend/dist/index.js',
            })
        with self.assertRaises(ValueError):
            packager.build_bundle(self.source, self.frontend, self.output, ['typescript-ws-backend'])

    def test_backend_artifact_symlinks_are_rejected(self):
        self.write('typescript-backend/package.json', '{}')
        self.write('typescript-backend/package-lock.json', '{}')
        path = self.write('typescript-backend/dist/index.js', '// compiled backend')
        self.write('outside.js', 'test-only-private-file')
        path.unlink()
        path.symlink_to(self.source / 'outside.js')
        with self.assertRaises(ValueError):
            packager.build_bundle(self.source, self.frontend, self.output, ['typescript-backend'])

    def test_backend_dist_directory_cannot_escape_the_checkout(self):
        self.write('typescript-backend/package.json', '{}')
        self.write('typescript-backend/package-lock.json', '{}')
        outside = self.root / 'external-dist'
        outside.mkdir()
        (outside / 'index.js').write_text('test-only-external-file')
        (self.source / 'typescript-backend/dist').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            packager.build_bundle(self.source, self.frontend, self.output, ['typescript-backend'])


if __name__ == '__main__':
    unittest.main()
