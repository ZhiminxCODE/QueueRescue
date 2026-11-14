"""Build a deterministic Lambda zip from pinned pure-Python AWS dependencies.

Dependency installation occurs in a temporary directory. The output contains
only the cloud runtime, shared masking helper, and SDK; never local credentials,
tests, Terraform state, databases, or the complete working tree.
"""
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def build(output, install=True):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        dependencies = Path(directory) / "dependencies"
        dependencies.mkdir()
        if install:
            subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-compile",
                "--only-binary=:all:", "--no-deps", "--target", str(dependencies), "-r", str(ROOT / "cloud/requirements.txt")], check=True)
        files = {path.relative_to(dependencies).as_posix():path for path in dependencies.rglob('*') if path.is_file() and '__pycache__' not in path.parts and '.pyc' != path.suffix and 'bin' not in path.relative_to(dependencies).parts}
        for path in (ROOT / 'cloud').glob('*.py'):
            files['cloud/' + path.name] = path
        for name in ['__init__.py', 'service.py']:
            files['backend/' + name] = ROOT / 'backend' / name
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, path in sorted(files.items()):
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes())
    return hashlib.sha256(output.read_bytes()).hexdigest()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'infra/aws/.build/lambda.zip'))
    args = parser.parse_args()
    print('Lambda package SHA-256:', build(args.output))
