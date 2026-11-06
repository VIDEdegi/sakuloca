"""Installed PE files and embedded NSIS plug-ins; no guessed binary versions.

Run after packaging. Provenance is obtained from PyInstaller's actual Analysis
TOC and SHA-256 equality, not merely a same-named file on PATH.
"""
import argparse,ast,hashlib,json,os,re,subprocess,sys
from pathlib import Path
import pefile

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--label',default='CURRENT');args=parser.parse_args()
assert re.fullmatch('[A-Z0-9_-]+',args.label)
analysis=ast.literal_eval((root/'tmp/pyinstaller/media-search-worker/Analysis-00.toc').read_text())
origins={}
def walk(value):
    if isinstance(value,(tuple,list)):
        if len(value)==3 and isinstance(value[0],str) and isinstance(value[1],str) and value[0].lower().endswith(('.dll','.exe','.pyd')):origins[value[0].replace('\\','/')]=value[1]
        else:
            for item in value:walk(item)
walk(analysis)

def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def source_label(path):
    value=str(path)
    for original,replacement in ((str(root),'PROJECT'),(os.environ.get('USERPROFILE',''),'USERPROFILE')):
        if original:value=value.replace(original,replacement)
    return value.replace('\\','/')
def information(path):
    pe=pefile.PE(str(path));version={}
    for group in getattr(pe,'FileInfo',[]):
        for block in group:
            for table in getattr(block,'StringTable',[]):
                version.update({k.decode(errors='replace'):v.decode(errors='replace') for k,v in table.entries.items()})
    imports=sorted({entry.dll.decode() for attr in ('DIRECTORY_ENTRY_IMPORT','DIRECTORY_ENTRY_DELAY_IMPORT') for entry in getattr(pe,attr,[])})
    pe.close()
    return version,imports
def classification(relative):
    name=Path(relative).name.lower()
    if name.startswith('api-ms-win-') or name=='ucrtbase.dll':return 'Windows UCRT/API sets','Microsoft OS/SDK terms','同梱不要・除外対象','なし（OSのWindows Update経路）'
    if name.startswith(('msvcp','vcruntime')):return 'Microsoft VC runtime','Microsoft Software License Terms','未確定：取得元とREDIST権利の確認必要','公式VC Redistributableを優先。Apache通知で代替不可'
    if name in ('dxcompiler.dll','dxil.dll'):return 'Microsoft DXC','LLVM/University of Illinois + DXC notices','通知同梱','licenses/native/DirectXShaderCompiler/LICENSE.TXT'
    if 'litertdispatch' in name:return 'LiteRT Intel OpenVINO dispatch','Apache-2.0（ラッパー）/推移依存未確定','未確定：実ビルドSBOM/第三者通知','licenses/python/LiteRT-LM/LICENSE、licenses/native/LiteRT/LICENSE（完全性未確定）'
    if name=='litert-lm.dll':return 'LiteRT-LM API 0.18.0 wheel native library','Apache-2.0/推移依存は個別条件','本体許諾と宣言依存10件の本文あり・実DLLの追加必須通知は未確定','licenses/python/LiteRT-LM/LICENSE、licenses/native/LiteRT/、licenses/native/LiteRT-third-party/、docs/DISTRIBUTION_CLOSURE.md'
    if 'pil/' in relative.lower():return 'Pillow 13.0.0 wheel','MIT-CMU/HPND + third-party licenses','通知同梱','licenses/python/Pillow/LICENSE'
    if name=='exact-search.dll':return 'Local Photo Search exact dot helper','PSF-2.0（補償和）+ アプリ本体の権利','補償和の通知同梱。PE外部依存なし','licenses/python/CPython/LICENSE.txt、licenses/native/ExactSearch/SOURCE.md'
    if name=='microsoftedgewebview2setup.exe':return 'Microsoft official Evergreen Bootstrapper','Microsoft WebView2 Runtime terms（SDK BSD通知とは別）','公式署名確認済み・Runtime全文と同意/SmartScreen通知あり','licenses/native/WebView2/RUNTIME_EULA.html、RUNTIME_EULA.txt、SOURCE.md、docs/INSTALLER_NOTICE.txt'
    if name=='media-search-worker.exe':return 'PyInstaller 6.22.3 bootloader + application','GPL-2.0-or-later with bootloader exception + application','bootloader通知同梱','licenses/python/PyInstaller/COPYING.txt'
    if name=='embeddinggemma-media-search.exe':return 'Application + Rust/Tauri static dependencies','application + Cargo individual licenses','依存通知あり・本体の公開条件は所有者が決定','licenses/rust-packages.json、licenses/rust/'
    if name=='libffi-8.dll':return 'CPython bundled libffi','MIT','CPython組込通知同梱・DLL単独バージョン未報告','licenses/python/CPython/LICENSE.txt'
    if name in ('libssl-3.dll','libcrypto-3.dll'):return 'CPython bundled OpenSSL 3.5.9','Apache-2.0','通知同梱・旧3.0.18から公式runtime全体を更新','licenses/native/OpenSSL/LICENSE.txt、SOURCE.md'
    if name=='sqlite3.dll':return 'CPython bundled SQLite','Public domain','CPython組込通知同梱','licenses/python/CPython/LICENSE.txt'
    return 'CPython 3.14.8 distribution','PSF-2.0 + bundled third-party licenses','CPython組込通知同梱','licenses/python/CPython/LICENSE.txt'

records=[]
worker=root/'dist-worker/media-search-worker'
paths=[('embeddinggemma-media-search.exe',root/'target/release/embeddinggemma-media-search.exe')]
paths.extend(('worker/'+p.relative_to(worker).as_posix(),p) for p in worker.rglob('*') if p.suffix.lower() in ('.dll','.exe','.pyd'))
nsis=root/'target/release/nsis/x64/installer.nsi'
if nsis.exists():
    nsis_text=nsis.read_text(encoding='utf-8')
    if '!define INSTALLWEBVIEW2MODE "embedBootstrapper"' in nsis_text:
        bootstrap=re.search(r'!define WEBVIEW2BOOTSTRAPPERPATH "([^"]+)"',nsis_text)[1]
        paths.append(('$TEMP/MicrosoftEdgeWebview2Setup.exe',Path(bootstrap)))
    # /PPO fails to resolve the Unicode additional plug-in on NSIS 3.11.
    # Compile the same instructions uncompressed to an E: temporary artifact
    # and use the compiler's actual embedded-file list, not textual guesses.
    temporary=root/'tmp/inventory-nsis.exe';script=root/'tmp/inventory-compile.nsi'
    script.write_text(nsis.read_text(encoding='utf-8').replace('"lzma"','"none"').replace('!define OUTFILE "nsis-output.exe"',f'!define OUTFILE "{temporary}"'),encoding='utf-8')
    expanded=subprocess.check_output([str(root/'target/.tauri/NSIS/makensis.exe'),'/V4','/NOCD',str(script)],cwd=nsis.parent).decode('utf-8',errors='replace')
    (root/'tmp/inventory-compile.log').write_text(expanded,encoding='utf-8')
    temporary.unlink()
    for name in sorted(set(re.findall(r'^File: "[^"]+"->"\$PLUGINSDIR\\([^"\\]+\.dll)"',expanded,re.M))):
        candidates=list((root/'target/.tauri/NSIS/Plugins/x86-unicode').rglob(name))
        assert len(candidates)==1,(name,candidates)
        paths.append(('$PLUGINSDIR/'+name,candidates[0]))
for relative,path in sorted(paths):
    version,imports=information(path);source=path;equal=True
    if relative.startswith('worker/_internal/'):
        original=origins.get(relative[len('worker/_internal/'):])
        if original:source=Path(original);equal=source.is_file() and sha(source)==sha(path)
        else:equal=False
    if relative.startswith('$PLUGINSDIR/'):
        if path.name=='nsis_tauri_utils.dll':
            group='nsis-tauri-utils 0.5.3';license='MIT';status='公式リリースSHA1との一致を検証';notice='licenses/native/Tauri/LICENSE_MIT、SOURCE.md'
            assert hashlib.sha1(path.read_bytes()).hexdigest()=='75197fee3c6a814fe035788d1c34ead39349b860'
        else:
            group='NSIS 3.11 built-in plug-in';license='zlib/libpng';status='通知同梱';notice='licenses/native/NSIS/COPYING、SOURCE.md'
    else:group,license,status,notice=classification(relative)
    owners={'CPython 3.14.8 distribution':'Python Software Foundation and contributors','Pillow 13.0.0 wheel':'Secret Labs AB; Fredrik Lundh; Jeffrey Alex Clark and contributors; embedded dependency owners in full LICENSE','CPython bundled libffi':'Anthony Green, Red Hat, Inc and others','CPython bundled OpenSSL 3.5.9':'OpenSSL project authors','CPython bundled SQLite':'Public domain','Microsoft DXC':'Microsoft Corporation; LLVM and embedded dependency authors','Microsoft official Evergreen Bootstrapper':'Microsoft Corporation','LiteRT-LM API 0.18.0 wheel native library':'Google LLC; actual linked third-party copyright list unresolved','LiteRT Intel OpenVINO dispatch':'Google LLC; Intel/linked third-party list unresolved','NSIS 3.11 built-in plug-in':'1999-2025 NSIS Contributors','nsis-tauri-utils 0.5.3':'2019-2022 Tauri Programme within The Commons Conservancy','Local Photo Search exact dot helper':'Python Software Foundation; project copyright holder','Application + Rust/Tauri static dependencies':'Project copyright holder; exact Cargo package notices','PyInstaller 6.22.3 bootloader + application':'PyInstaller Development Team; project copyright holder'}
    records.append({'path':relative,'bytes':path.stat().st_size,'sha256':sha(path),'version_resource':version,'origin':source_label(source),'origin_sha256_matches':equal,'group':group,'license':license,'status':status,'notice':notice,'copyright_notice':version.get('LegalCopyright') or owners.get(group,'Microsoft Corporation; see component terms'),'imports':imports})
document={'label':args.label,'method':'PyInstaller Analysis TOC + SHA-256 + PE VERSIONINFO/imports; actual NSIS compile log identifies embedded plug-ins','files':records,'generated_uninstaller':'uninstall.exe is generated by NSIS WriteUninstaller from the same NSIS stub/plugins; see licenses/native/NSIS/COPYING. Its final bytes depend on install path and must be inventoried in a test installation.'}
(root/f'docs/DISTRIBUTION_FILES_{args.label}.json').write_text(json.dumps(document,ensure_ascii=False,indent=2),encoding='utf-8')
lines=[f'# 配布PEファイル一覧 ({args.label})','', 'DLL/EXEに加えてPython拡張の`.pyd`も列挙。バージョン欄「未報告」はPEリソースがないことを意味し、上位パッケージの版をDLLの版と推測しない。SHA-256・PEインポート・取得元の一致判定は同名JSONに記録。著作権欄は識別用、配布時は指定の通知全文を残す。','', '| インストール先 / 展開先 | 製品バージョン | 出所 | ライセンス / 判定 | 著作権表示 | 必要な通知 |','|---|---|---|---|---|---|']
for r in records:
    lines.append('| '+ ' | '.join([r['path'],r['version_resource'].get('ProductVersion','未報告'),r['origin'],r['license']+' / '+r['status'],r['copyright_notice'],r['notice']])+' |')
lines+=['',document['generated_uninstaller'],'','CURRENTにはMicrosoft署名済みWebView2 Bootstrapperを同梱する。WebView2 Runtime本体、VC Redistributable、モデル本体、OpenVINO runtimeは同梱しない。取得する場合は公式配布の別コンポーネントとして扱う。NSISインストーラーEXE自身と生成したアンインストーラーの最終SHAは検証レポートに記録する。']
(root/f'docs/DISTRIBUTION_FILES_{args.label}.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'files':len(records),'label':args.label,'unmatched_origins':[r['path'] for r in records if not r['origin_sha256_matches']]},ensure_ascii=False))
