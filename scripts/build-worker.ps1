$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/env.ps1"
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
  if (-not (Test-Path -LiteralPath $env:MEDIA_SEARCH_BUILD_PYTHON)) { throw 'Run scripts/prepare-python-runtime.ps1 before the release build.' }
  & $env:MEDIA_SEARCH_BUILD_PYTHON -c "import sys,ssl,importlib.metadata,miniaudio,_cffi_backend; assert sys.version_info[:3]==(3,14,8); assert ssl.OPENSSL_VERSION.startswith('OpenSSL 3.5.9 '); assert importlib.metadata.version('miniaudio')=='1.71'; assert importlib.metadata.version('cffi')=='2.1.1'"
  if ($LASTEXITCODE -ne 0) { throw 'The pinned CPython/OpenSSL runtime and worker/requirements.txt dependencies are required.' }
  & "$PSScriptRoot/build-search.ps1"
  $nativeBinary = Join-Path (Get-Location).Path 'worker/native/exact-search.dll'
  & $env:MEDIA_SEARCH_BUILD_PYTHON -m PyInstaller --noconfirm --clean --onedir --name media-search-worker --collect-all litert_lm --hidden-import _cffi_backend --add-binary "$nativeBinary;native" --distpath dist-worker --workpath tmp/pyinstaller --specpath tmp worker/main.py
  if ($LASTEXITCODE -ne 0) { throw 'Worker packaging failed' }
  # UCRT/API sets are OS components on supported Windows 10/11. PyInstaller
  # otherwise discovers unrelated copies on the developer's PATH.
  $internal = (Resolve-Path 'dist-worker/media-search-worker/_internal').Path
  $osCopies = Get-ChildItem -LiteralPath $internal -File | Where-Object { $_.Name -like 'api-ms-win-*.dll' -or $_.Name -eq 'ucrtbase.dll' -or $_.Name -in @('MSVCP140.dll','VCRUNTIME140.dll','VCRUNTIME140_1.dll') }
  foreach ($osCopy in $osCopies) {
    if ($osCopy.DirectoryName -ne $internal) { throw 'Unexpected packaging path' }
    Remove-Item -LiteralPath $osCopy.FullName
  }
  # Intel NPU is not selected by this app (GPU WebGPU / CPU XNNPACK only).
  # The wheel dispatch imports openvino.dll, which the wheel does not ship.
  $unusedDispatch = Join-Path $internal 'litert_lm/vendors/intel_openvino/dispatch/LiteRtDispatch.dll'
  if (Test-Path -LiteralPath $unusedDispatch) { Remove-Item -LiteralPath $unusedDispatch }
} finally { Pop-Location }
