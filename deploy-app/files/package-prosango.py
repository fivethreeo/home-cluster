import argparse
import gzip
import json
from pathlib import Path
import subprocess
import tarfile


def build_bundle(source, frontend, output, components=('django',)):
    source = Path(source).resolve()
    frontend = Path(frontend).resolve()
    if not components or set(components) - {'django', 'frontend', 'typescript-backend', 'typescript-ws-backend'}:
        raise ValueError('Select Django, frontend, TypeScript HTTP and/or TypeScript WebSocket components')
    django = 'django' in components
    include_frontend = django or 'frontend' in components
    if django and not (source / 'django-project/manage.py').is_file():
        raise ValueError('Source must be an application checkout containing django-project/manage.py')
    manifests = [frontend / 'manifest.json'] if include_frontend else []
    editor_manifest = frontend / 'editor/editor-manifest.json'
    if include_frontend and ((source / 'typescript-frontend/src/editor.ts').is_file() or editor_manifest.exists()):
        manifests.append(editor_manifest)
    for manifest in manifests:
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
    ) if django else None
    members = {}
    for name in (set(tracked.stdout.decode().split('\0')) - {''} if tracked else []):
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
    for path in (frontend.rglob('*') if include_frontend else []):
        if path.is_symlink():
            raise ValueError(f'Frontend symlinks are not supported: {path}')
        if path.is_file():
            name = Path('typescript-frontend/dist') / path.relative_to(frontend)
            members[name.as_posix()] = path

    for component in set(components) - {'django', 'frontend'}:
        root = source / component
        if root.is_symlink() or (root / 'dist').is_symlink():
            raise ValueError(f'Backend directory symlinks are not supported: {root}')
        for required in ['package.json', 'package-lock.json', 'dist/index.js']:
            if not (root / required).is_file():
                raise ValueError(f'Build {component} locally before deployment: missing {required}')
        for path in [root / 'package.json', root / 'package-lock.json', *root.joinpath('dist').rglob('*')]:
            if path.is_symlink():
                raise ValueError(f'Backend symlinks are not supported: {path}')
            if path.is_file():
                members[path.relative_to(source).as_posix()] = path

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
    parser = argparse.ArgumentParser(description='Bundle Django source and prebuilt application assets.')
    parser.add_argument('source')
    parser.add_argument('frontend')
    parser.add_argument('output')
    parser.add_argument('--components', nargs='+', default=['django'],
                        choices=['django', 'frontend', 'typescript-backend', 'typescript-ws-backend'])
    args = parser.parse_args()
    build_bundle(args.source, args.frontend, args.output, args.components)
