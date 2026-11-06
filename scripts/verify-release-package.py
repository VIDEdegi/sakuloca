"""Validate the local publication payload, checksums and updater signature."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verifier', type=Path, default=ROOT / 'tmp/verify-release-signature.exe')
    args = parser.parse_args()
    config = json.loads((ROOT / 'src-tauri/tauri.conf.json').read_text(encoding='utf-8'))
    version = config['version']; assert '-' not in version
    directory = ROOT / 'data/releases' / f'v{version}-public'
    installer = config['productName'].replace(' ', '-') + f'_{version}_x64-setup.exe'
    notes = next(Path(path).name for path, destination in config['bundle']['resources'].items() if destination == 'RELEASE_NOTES.md')
    expected = {installer, installer+'.sig', 'latest.json', 'SHA256SUMS', notes, 'LICENSE', 'LICENSE_SCOPE.md', 'THIRD_PARTY_NOTICES.md', 'USER_GUIDE.md', 'licenses.zip'}
    assert {p.name for p in directory.iterdir()} == expected
    checksums = {}
    for line in (directory / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        digest, name = line.split('  ', 1)
        assert name in expected - {'SHA256SUMS'} and name not in checksums
        with (directory / name).open('rb') as source:
            assert hashlib.file_digest(source, 'sha256').hexdigest() == digest, name
        checksums[name] = digest
    assert set(checksums) == expected - {'SHA256SUMS'}
    manifest = json.loads((directory / 'latest.json').read_text(encoding='utf-8'))
    assert manifest['version'] == version
    assert manifest['notes'] == (directory / notes).read_text(encoding='utf-8')
    platform = manifest['platforms']['windows-x86_64']
    assert platform['url'] == config['bundle']['homepage'].rstrip('/') + f'/releases/download/v{version}/' + installer
    assert platform['signature'] == (directory / (installer+'.sig')).read_text(encoding='utf-8').strip()
    output = ROOT / 'data/release-tests' / f'release-package-{version}-{time.time_ns()}'
    output.mkdir()
    key = output / 'public-key.txt'; signature = output / 'signature.txt'
    key.write_bytes(base64.b64decode(config['plugins']['updater']['pubkey'], validate=True))
    signature.write_bytes(base64.b64decode(platform['signature'], validate=True))
    result = subprocess.run([str(args.verifier.resolve(strict=True)), str(key), str(signature), str(directory / installer)],
                            capture_output=True, text=True, timeout=60, check=True)
    sources = {p.relative_to(ROOT/'licenses').as_posix():p for p in (ROOT/'licenses').rglob('*') if p.is_file()}
    with zipfile.ZipFile(directory/'licenses.zip') as archive:
        assert {name for name in archive.namelist() if not name.endswith('/')} == set(sources)
        for name,path in sources.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == hashlib.sha256(path.read_bytes()).hexdigest(), name
    report = dict(version=version, directory=str(directory), files=sorted(expected), checksums=checksums,
                  updater_signature_verified=True, tampered_artifact_rejected=True, manifest_consistent=True,
                  license_files=len(sources), exact_payload_allowlist=True, published=False)
    (ROOT/'data/release-tests'/f'release-package-{version}-verified.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(result.stdout.strip())
    print(json.dumps({k:v for k,v in report.items() if k not in ['checksums','files']}, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
