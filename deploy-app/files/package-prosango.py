import argparse
import gzip
import json
from pathlib import Path
import subprocess
import tarfile


def build_bundle(source, frontend, output):
    source = Path(source).resolve()
    frontend = Path(frontend).resolve()
    if not (source / 'django-project/manage.py').is_file():
        raise ValueError('Source must be a Prosango checkout containing django-project/manage.py')
    for manifest in [frontend / 'manifest.json', frontend / 'editor/editor-manifest.json']:
        if not manifest.is_file():
            raise ValueError(f'Missing prebuilt frontend manifest: {manifest}')
        with manifest.open() as file:
            entries = json.load(file)['entries']
        if not entries:
            raise ValueError(f'Empty frontend manifest: {manifest}')
        for entry in entries.values():
            for assets in entry.get('initial', {}).values():
                if any(str(asset).startswith(('http://', 'https://')) for asset in assets):
                    raise ValueError('Build production assets before deploying, not dev-server assets')

    tracked = subprocess.run(
        ['git', '-C', str(source), 'ls-files', '--cached', '--others',
         '--exclude-standard', '-z', '--', 'django-project'],
        check=True,
        stdout=subprocess.PIPE,
    )
    members = {}
    for name in set(tracked.stdout.decode().split('\0')) - {''}:
        path = source / name
        relative = Path(name).relative_to('django-project')
        if (
            any(part in {'.venv', 'venv', '__pycache__', 'media', 'test-media', 'staticfiles'}
                for part in relative.parts)
            or (path.name.startswith('.env') and path.name != '.env.example')
            or path.suffix in {'.sqlite', '.sqlite3', '.pyc'}
        ):
            continue
        if path.is_symlink():
            raise ValueError(f'Source symlinks are not supported: {name}')
        if path.is_file():
            members[name] = path
    for path in frontend.rglob('*'):
        if path.is_symlink():
            raise ValueError(f'Frontend symlinks are not supported: {path}')
        if path.is_file():
            name = Path('typescript-frontend/dist') / path.relative_to(frontend)
            members[name.as_posix()] = path

    def metadata(info):
        info.uid = info.gid = 0
        info.uname = info.gname = ''
        info.mode = 0o755 if info.mode & 0o111 else 0o644
        return info

    with Path(output).open('wb') as file:
        with gzip.GzipFile(fileobj=file, filename='', mode='wb', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as archive:
                for name, path in sorted(members.items()):
                    archive.add(path, arcname=name, recursive=False, filter=metadata)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Bundle Django source and prebuilt Prosango assets.')
    parser.add_argument('source')
    parser.add_argument('frontend')
    parser.add_argument('output')
    args = parser.parse_args()
    build_bundle(args.source, args.frontend, args.output)
