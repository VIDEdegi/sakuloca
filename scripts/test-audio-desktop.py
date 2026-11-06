"""Isolated QA identifier + real WebView2/audio controls and same-Worker MCP."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--data', type=Path, required=True, help='Index made by test-audio-runtime.py')
    parser.add_argument('--formats', action='store_true', help='Index includes MP3/FLAC from test-audio-runtime.py --formats')
    args = parser.parse_args()
    output, data = args.output.resolve(), args.data.resolve(strict=True)
    allowed = (ROOT / 'data/release-tests').resolve()
    assert allowed in output.parents and allowed in data.parents
    output.mkdir(parents=True, exist_ok=False)
    runtime = output / 'runtime'; runtime.mkdir()
    exe = runtime / 'SakuLoca.exe'
    assert b'com.local.embeddinggemma.audio-qa' in (ROOT / 'target/release/SakuLoca.exe').read_bytes(), 'Build with scripts/audio-qa.conf.json; do not test the real app identifier'
    shutil.copy2(ROOT / 'target/release/SakuLoca.exe', exe)
    shutil.copytree(ROOT / 'dist-worker/media-search-worker', runtime / 'worker')
    # Verify the packaged Worker copied into the QA app, not a stale release.
    source = ROOT / 'dist-worker/media-search-worker/media-search-worker.exe'
    assert hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256((runtime / 'worker/media-search-worker.exe').read_bytes()).digest()
    with socket.socket() as sock, socket.socket() as native:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        native.bind(('127.0.0.1', 0)); native_port = native.getsockname()[1]
    endpoint = f'http://127.0.0.1:{port}'
    log = (output / 'driver.log').open('w', encoding='utf-8')
    driver = subprocess.Popen([str(ROOT / 'tools/tauri-driver/bin/tauri-driver.exe'), '--port', str(port),
                               '--native-port', str(native_port), '--native-driver', str(ROOT / 'tools/edgedriver/msedgedriver.exe')],
                              env=dict(os.environ, MEDIA_SEARCH_DATA=str(data)), stdout=log, stderr=log,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    base = None
    report = {'isolated_data': str(data), 'separate_qa_identifier': 'com.local.embeddinggemma.audio-qa', 'packaged_worker': True}
    def request(method, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        try:
            with urllib.request.urlopen(urllib.request.Request(endpoint + path, data=body, method=method,
                                       headers={'Content-Type': 'application/json'}), timeout=120) as response:
                result = json.load(response).get('value')
        except urllib.error.HTTPError as exc:
            raise RuntimeError(exc.read().decode('utf-8', errors='replace')) from exc
        if isinstance(result, dict) and result.get('error'): raise RuntimeError(result)
        return result
    def wait(action, seconds=90):
        deadline = time.monotonic() + seconds; last = None
        while time.monotonic() < deadline:
            try:
                value = action()
                if value: return value
            except (OSError, urllib.error.URLError) as exc: last = exc
            time.sleep(.2)
        raise TimeoutError(f'Desktop condition not reached: {last}')
    def js(script): return request('POST', base + '/execute/sync', {'script': script, 'args': []})
    def mcp(method, params=None):
        result = subprocess.run(['node', str(ROOT / 'mcp/server.mjs'), '--data', str(data), '--call', method,
                                 *([json.dumps(params, ensure_ascii=False)] if params is not None else [])],
                                capture_output=True, text=True, encoding='utf-8', timeout=180,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    def screenshot(name):
        (output / name).write_bytes(base64.b64decode(request('GET', base + '/screenshot')))
    app = "document.querySelector('media-search-app')"
    root = app + '.shadowRoot'
    try:
        wait(lambda: request('GET', '/status'))
        session = request('POST', '/session', {'capabilities': {'alwaysMatch': {'tauri:options': {'application': str(exe)}}}})['sessionId']
        base = '/session/' + session
        request('POST', base + '/timeouts', {'script': 120000})
        wait(lambda: js(f"return !!{app}?.shadowRoot?.querySelector('#query')"))
        status = mcp('status'); assert Path(status['data_path']).resolve() == data
        worker = status['automation']['worker_pid']
        wait(lambda: js(f"return {app}.results.total===1 && !{app}.busy"))
        wait(lambda: js(f"return !!{root}.querySelector('media-thumbnail')?.shadowRoot?.querySelector('img')?.complete"))
        report['photo_thumbnail'] = True
        js(f"{root}.querySelectorAll('.media-switch button')[1].click()")
        total = 11 if args.formats else 5
        semantic_total = total - 1
        wait(lambda: js(f"return {app}.mediaKind==='audio' && !{app}.busy && {app}.results.total==={total}"))
        assert js(f"return {root}.querySelectorAll('media-thumbnail').length") == 0
        assert js(f"return {root}.querySelectorAll('.audio-mark').length") == total
        screenshot('audio-results.png')
        js(f"const q={root}.querySelector('#query');q.value='電子音';q.dispatchEvent(new Event('input',{{bubbles:true}}));{root}.querySelector('form').requestSubmit()")
        wait(lambda: js(f"return !{app}.busy && {app}.results.semantic && {app}.results.total==={semantic_total}"))
        via_mcp = mcp('search', {'kind': 'audio', 'text': '電子音', 'limit': 2})
        assert via_mcp['total'] == semantic_total
        assert all(item['kind'] == 'audio' and 'start_ms' in item and 'segment_id' in item for item in via_mcp['items'])
        assert all(not any(key in item for key in ['vector', 'thumbnail', 'url']) for item in via_mcp['items'])
        report['same_worker_mcp_audio_search'] = mcp('status')['automation']['worker_pid'] == worker
        js(f"const a={app};a.query='猫の鳴き声';void a.search()")
        wait(lambda: js(f"return !{app}.busy && {app}.results.interpreted_query==='A cat meowing'"))
        assert js(f"return {root}.textContent.includes('A cat meowing')")
        assert mcp('search', {'kind':'audio','text':'猫の鳴き声','limit':2})['interpreted_query'] == 'A cat meowing'
        report['sound_synonym_interpretation_visible'] = True
        if args.formats:
            for extension in ['mp3', 'flac']:
                js(f"const a={app};a.query='';a.filename='tone';a.format='{extension}';void a.search()")
                wait(lambda: js(f"return !{app}.busy && {app}.results.total===3"))
                assert mcp('search', {'kind': 'audio', 'format': extension})['total'] == 3
                js(f"{root}.querySelectorAll('.card')[1].click()")
                wait(lambda: js(f"return {root}.querySelector('#audio-preview').open && ({root}.querySelector('#audio-preview audio')?.readyState||0)>=2"))
                assert js(f"return Math.abs({root}.querySelector('#audio-preview audio').duration-40)<.05")
                js(f"{root}.querySelector('[aria-label=音声プレビューを閉じる]').click()")
            report['mp3_flac_filters_and_preview'] = True
        js(f"const a={app};a.query='';a.filename='tone';a.format='wav';void a.search()")
        wait(lambda: js(f"return !{app}.busy && {app}.results.total===3"))
        js(f"{root}.querySelectorAll('.card')[1].click()")
        wait(lambda: js(f"return !!{root}.querySelector('#audio-preview')?.open"))
        wait(lambda: js(f"return ({root}.querySelector('#audio-preview audio')?.readyState||0)>=2"))
        geometry = js(f"const a={root}.querySelector('#audio-preview audio');return {{duration:a.duration,error:a.error?.message||null}}")
        assert abs(geometry['duration'] - 40) < .05 and geometry['error'] is None, geometry
        assert js(f"return {app}.audioPreview.file.start_ms") == 20000
        screenshot('audio-preview.png')
        js(f"const a={root}.querySelector('#audio-preview audio');window.__audioTest=a;a.muted=true;a.currentTime=2;a.play().catch(e=>window.__audioError=String(e))")
        wait(lambda: js("return window.__audioTest.currentTime>2.3 && !window.__audioTest.paused"))
        assert js(f"return {app}.audioPosition") > 22000
        js(f"Array.from({root}.querySelectorAll('#audio-preview button')).find(b=>b.textContent==='次の区間').click()")
        wait(lambda: js(f"return {app}.audioPreview?.file.match_start_ms===50000"))
        assert js('return window.__audioTest.paused')
        wait(lambda: js(f"return Math.abs(({root}.querySelector('#audio-preview audio')?.duration||0)-15)<.05"))
        assert js(f"return Array.from({root}.querySelectorAll('#audio-preview button')).find(b=>b.textContent==='次の区間').disabled")
        js(f"Array.from({root}.querySelectorAll('#audio-preview button')).find(b=>b.textContent==='前の区間').click()")
        wait(lambda: js(f"return {app}.audioPreview?.file.match_start_ms===25000"))
        report['neighboring_windows_same_source'] = True
        report['muted_playback_clock_advances'] = True
        report['interval_duration_seconds'] = geometry['duration']
        js(f"{root}.querySelector('[aria-label=音声プレビューを閉じる]').click()")
        wait(lambda: js(f"return !{root}.querySelector('#audio-preview').open && !{root}.querySelector('#audio-preview audio')"))
        assert js('return window.__audioTest.paused')
        report['close_stops_playback'] = True
        js(f"{root}.querySelector('footer .linkish').click()")
        wait(lambda: js(f"return {root}.querySelector('release-controls[mode=assistant]').shadowRoot.querySelector('#errors').open"))
        error_text = js(f"return {root}.querySelector('release-controls[mode=assistant]').shadowRoot.querySelector('#errors').textContent")
        assert '音声の解析エラー 1件' in error_text and 'broken.wav' in error_text, error_text
        js(f"{root}.querySelector('release-controls[mode=assistant]').shadowRoot.querySelector('#errors').close()")
        # Start through the UI; routine running-state checks use the MCP/CLI.
        previous_start = mcp('status')['progress']['started']
        js(f"Array.from({root}.querySelectorAll('footer button')).find(b=>b.textContent==='差分更新 / 解析').click()")
        wait(lambda: mcp('status')['progress']['started'] > previous_start)
        wait(lambda: not mcp('status')['running'])
        assert mcp('status')['analysis_kind'] == 'audio'
        js(f"void {app}.refresh()")
        wait(lambda: js(f"return !{app}.status.running"))
        report['ui_starts_audio_analysis'] = True
        for width, height in [(1280, 820), (860, 820), (860, 620)]:
            request('POST', base + '/window/rect', {'width': width, 'height': height})
            time.sleep(.3)
            assert not js(f"return {root}.querySelector('main').scrollWidth>{root}.querySelector('main').clientWidth"), f'Overflow at {width}'
            assert js(f"return Array.from({root}.querySelectorAll('.pager button')).every(b=>b.getBoundingClientRect().height<55)"), f'Cramped pager at {width}'
            screenshot(f'audio-{width}x{height}.png')
            geometry = js(f"const r={root};return {{results:r.querySelector('.results').clientHeight,header:r.querySelector('.top').clientHeight,footer:r.querySelector('footer').clientHeight,viewport:innerHeight}}")
            assert geometry['results'] >= 100, f'No usable results at {width}x{height}: {geometry}'
        report['no_horizontal_overflow'] = True
        screenshot('audio-860.png')
        js(f"{app}.theme='light';{app}.applyTheme()")
        screenshot('audio-light-860.png')
        js(f"{root}.querySelectorAll('.media-switch button')[0].click()")
        wait(lambda: js(f"return !{app}.busy && {app}.mediaKind==='image'"))
        js(f"{app}.clearFilters()")
        wait(lambda: js(f"return !{app}.busy && {app}.results.total===1"))
        assert mcp('status')['automation']['worker_pid'] == worker
        report['photo_audio_toggle_keeps_worker'] = True
        report['os_open_actions_executed'] = False
        report['verified'] = True
        (output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        if base:
            try: mcp('desktop', {'action': 'quit'})
            except Exception: pass
            try: request('DELETE', base)
            except Exception: pass
        driver.terminate()
        try: driver.wait(timeout=15)
        except subprocess.TimeoutExpired: driver.kill(); driver.wait()
        log.close()


if __name__ == '__main__': main()
