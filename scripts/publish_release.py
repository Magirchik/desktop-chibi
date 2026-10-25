#!/usr/bin/env python3
"""Attach verified build artifacts to a draft; never overwrite a published release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_chibi import __version__


def request(url, token, method='GET', payload=None, binary=None):
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'Desktop-Chibi-release'}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
    if binary is not None:
        data = binary.read_bytes()
        headers['Content-Type'] = 'application/octet-stream'
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers, method=method), timeout=120) as response:
        content = response.read()
        return json.loads(content) if content else None


def verified_artifacts(directory, version):
    expected = [f'desktop-chibi-v{version}-windows-x64.zip', f'desktop-chibi-v{version}-ubuntu-x64.tar.gz',
                f'desktop-chibi-v{version}-ubuntu-amd64.deb']
    manifests = [directory / f'build-{platform}.json' for platform in ('windows-x64', 'ubuntu-x64')]
    digests = {}
    for manifest in manifests:
        data = json.loads(manifest.read_text())
        if not data.get('ok') or not data.get('frozen') or data.get('version') != version:
            raise ValueError('Invalid standalone smoke-test manifest: ' + manifest.name)
        digests.update(data['artifacts'])
    for name in expected:
        file = directory / name
        with file.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digests.get(name) != digest:
            raise ValueError('Checksum mismatch: ' + name)
    checksum = directory / 'SHA256SUMS'
    checksum.write_text(''.join(f'{digests[name]}  {name}\n' for name in sorted(expected)), encoding='utf-8')
    return [directory / name for name in expected] + manifests + [checksum]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args(argv)
    repository = os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', repository) or not re.fullmatch(r'[0-9a-f]{40}', args.commit):
        parser.error('Invalid repository or commit SHA')
    token = os.environ['GH_TOKEN']
    files = verified_artifacts(args.artifacts, __version__)
    base = f'https://api.github.com/repos/{repository}'
    tag = 'v' + __version__
    try:
        release = request(base + '/releases/tags/' + urllib.parse.quote(tag, safe=''), token)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        release = None
    if release and not release['draft']:
        print(f'{tag} is already published; leaving it unchanged. Bump __version__ for a new release.')
        return 0
    body = ('## Downloads\n\n'
            '- **Windows 10/11 x64:** unpack the ZIP and run `desktop-chibi.exe`. Keep `_internal` beside it.\n'
            '- **Ubuntu 22.04+ amd64:** install the `.deb`, or unpack the `.tar.gz` and run `desktop-chibi`.\n\n'
            'Python is included. Themes, GIF workshop, reminders and local events are available in both builds. '
            'Windows uses native hotkeys, active-window rules and PCM WAV sounds. '
            'Linux notifications, PipeWire and the Vesktop plugin remain Linux integrations.\n\n'
            'Built and smoke-tested separately on Windows and Ubuntu. '
            'Check `SHA256SUMS` and `build-*.json` for hashes and build details.\n\n'
            f'Commit: `{args.commit}`\n')
    payload = {'tag_name': tag, 'target_commitish': args.commit, 'name': 'Desktop Chibi ' + tag,
               'body': body, 'draft': True, 'prerelease': '-' in __version__}
    if release:
        release = request(base + f'/releases/{release["id"]}', token, 'PATCH', payload)
    else:
        release = request(base + '/releases', token, 'POST', payload)
    names = {file.name for file in files}
    for asset in request(base + f'/releases/{release["id"]}/assets?per_page=100', token):
        if asset['name'] in names:
            request(base + f'/releases/assets/{asset["id"]}', token, 'DELETE')
    upload = release['upload_url'].split('{', 1)[0]
    for file in files:
        request(upload + '?name=' + urllib.parse.quote(file.name), token, 'POST', binary=file)
    print('Draft prepared:', release['html_url'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
