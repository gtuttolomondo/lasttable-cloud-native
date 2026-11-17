"""Verify the current Git build inputs; historical release checks are opt-in."""
import hashlib
import json
from pathlib import Path
import subprocess

APPLICATION_PATHS = ['app', 'bootstrap', 'frontend', 'notifications']


def current_source(root, declared_commit=None):
    root = Path(root)
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root)
    commit = git('rev-parse', 'HEAD').decode().strip()
    if declared_commit and declared_commit != commit:
        raise ValueError('Declared source commit differs from the checked-out commit')
    if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', *APPLICATION_PATHS], cwd=root).returncode:
        raise ValueError('Application inputs have uncommitted changes')
    names = git('ls-files', '-z', '--', *APPLICATION_PATHS).decode().split('\0')
    manifest = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names if name}
    if not manifest:
        raise ValueError('No tracked application inputs found')
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    return commit, manifest, digest


def verify_baseline(root, baseline):
    for name, digest in baseline['files'].items():
        path = Path(root) / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Application differs from historical release baseline: ' + name)


def verify_packages(actual, lock_path):
    expected = [line.strip() for line in Path(lock_path).read_text().splitlines()
                if line.strip() and not line.lstrip().startswith('#')]
    if sorted(actual) != sorted(expected):
        raise ValueError('Installed runtime packages differ from lock file: ' + str(lock_path))


def frontend_assets(hashes, baseline=None):
    assets = {line.split(None, 1)[1].removeprefix('/srv/'): line.split(None, 1)[0]
              for line in hashes.splitlines()}
    if 'index.html' not in assets or not any(name.endswith('.js') for name in assets):
        raise ValueError('Built frontend is missing its index or JavaScript bundle')
    if baseline is not None and assets != baseline:
        raise ValueError('Frontend assets differ from historical release baseline')
    return assets
