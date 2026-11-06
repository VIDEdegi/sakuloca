# Third-party notices

Application code is newly authored for this project. The Google demo is a reference for task-prefix and runtime API usage; the demo UI, model download, browser cache, and index storage code were not copied wholesale.

- LiteRT-LM API 0.18.0 — Copyright Google LLC. Apache License 2.0. https://github.com/google-ai-edge/LiteRT-LM
- Lit and its runtime dependencies — Google LLC / Lit authors. BSD-3-Clause. See `licenses/npm/` for exact notices.
- Tauri and Rust dependencies — exact package names, versions, declared licenses, and upstream sources are in `licenses/rust-packages.json`; available license/notice files are in `licenses/rust/`.
- CPython 3.14.8 — Python Software Foundation and contributors. The complete bundled Python license is in `licenses/python/CPython/`.
- Pillow 12.3.0 — MIT-CMU, copyright contributors. Complete license/embedded library notices are in `licenses/python/Pillow/`.
- miniaudio 1.71 — Irmen de Jong (Python bindings), David Reid (miniaudio), Sean Barrett (stb_vorbis). The wheel's complete MIT license is in `licenses/python/miniaudio/`. Used for bounded MP3/FLAC decoding; no playback/capture devices are opened.
- CFFI 2.1.1 and pycparser 3.12 — their complete wheel licenses are in `licenses/python/cffi/` and `licenses/python/pycparser/`. Pinned decoder package versions are recorded in `licenses/python/audio-decoder-packages.json`.
- PyInstaller bootloader — GPL with the bootloader exception permitting distribution of the generated application. The full license and exception are in `licenses/python/PyInstaller/`. This is a build tool, not a requirement to install Python on the user's PC. https://pyinstaller.org/en/stable/license.html

The model is not redistributed or included in the installer. Users supply a model obtained under its applicable terms. No Google branding or endorsement is implied.

Development-only Rust/MSVC/SDK/npm/EdgeDriver/Python tooling is not bundled into the app. MSVC/Windows SDK were downloaded from Microsoft with the standalone extraction utility described in docs/PHASE0.md. The utility remains in ignored tools/ and is not redistributed as application code.

These notices accompany SakuLoca 1.1.0. Independently authored application code is Apache-2.0; see LICENSE and LICENSE_SCOPE.md. Third-party terms below are retained unchanged. See the release records for publication status.

## v1.0 development audit

Model acquisition uses the official Google guide-linked LiteRT Community repository, pinned revision 24d962e906c7d332c6428e71c9676855024569e2. The API declares Apache-2.0 for this EmbeddingGemma 2 artifact; the model remains excluded from distribution. Existing local file SHA-256 matches the published LFS SHA. Users review the source and terms before acquisition.

The tray uses Tauri tray-icon; its package and notices were already included in the recorded Rust dependency inventory. See docs/DISTRIBUTION_AUDIT.md for native DLL/Visual C++ redistribution checks that are still unresolved. The earlier native audit is retained as development history; current release status is docs/RELEASE_V1_FINAL.md.

DirectX Shader Compiler dxcompiler.dll/dxil.dll identify ProductVersion 1.9.2602.17 (21d28f727). The matching upstream license and embedded third-party notices are in licenses/native/DirectXShaderCompiler/LICENSE.TXT; SOURCE.md records the exact upstream revision. OpenVINO dispatch and individual Visual C++/UCRT DLLs are excluded from this distribution.

## dev.2 native notices

The actual Windows Cargo dependency graph (including build dependencies) has 248 packages. Six workspace license notices omitted by their published crates are now included from exact recorded upstream commits: alloc-stdlib, selectors, tauri-plugin, webview2-com, webview2-com-macros, webview2-com-sys. Unmodified MPL-covered selectors 0.38.0 source accompanies the license in licenses/rust/selectors-0.38.0/source/, with the fixed upstream source link in SOURCE.md. No modifications were made to that source.

The file-level binary inventory is docs/DISTRIBUTION_FILES_CURRENT.json. NSIS 3.11 full COPYING and nsis-tauri-utils 0.5.3 MIT are in licenses/native/NSIS and Tauri. Microsoft WebView2 SDK 1.0.3800.47 LICENSE and complete NOTICE are in licenses/native/WebView2; the Microsoft-signed Evergreen Bootstrapper is separate from the SDK and installs the separately licensed Runtime. No VC Redistributable, UCRT/API-set shims, individual VC DLLs, OpenVINO runtime, or Intel NPU dispatch DLL is bundled. Users may explicitly obtain Microsoft VC Runtime from Microsoft under its own terms.

OpenSSL 3.5.9 license is in licenses/native/OpenSSL. CPython runtime components are taken together from the official 3.14.8 distribution, rather than mixing OpenSSL DLLs from another supplier. The exact dot helper derives compensated summation from CPython 3.14.3; PSF terms and its source/modification notice are retained. LiteRT source LICENSE does not prove the complete Windows native wheel SBOM. The unused OpenVINO license folder is a clearly identified reference, not proof of permission to redistribute unidentified binaries.

The current independently authored application source code and documentation are provided under Apache-2.0, as stated in LICENSE_SCOPE.md. Older development distribution decisions are historical and do not alter third-party terms.

## Final dev.2 notice additions

LiteRT-LM v0.18.0 and its pinned LiteRT source declare these dependencies. Their complete official license texts now accompany this build in licenses/native/LiteRT-third-party/: Abseil 20260526.0 (Abseil Authors, Apache-2.0); SentencePiece 0.2.2 (Google / contributors, Apache-2.0); nlohmann/json 3.12.0 (Niels Lohmann, MIT); miniaudio 0.11.22 (David Reid, MIT-0 OR Unlicense); stb f58f558c (Sean Barrett, MIT OR Unlicense); Skia 226ae9d8 (Google Inc., BSD-3-Clause); TensorFlow d9a8da74 (TensorFlow Authors and additional owners in LICENSE, Apache-2.0 and additional notices); tokenizers-cpp 0.1.1 (MLC-AI / contributors, Apache-2.0); Dawn v20260720.160313 (Dawn & Tint Authors, BSD-3-Clause with Apache-2.0 for specified files); XNNPACK d89ef666 (Facebook, Inc. and affiliates / Google LLC, BSD-3-Clause). sources.json records exact source revisions, URLs and text hashes. Source-declared revisions are not independently attested Windows DLL versions. Applicable copyrights, conditions and disclaimers in these texts are retained, without implying endorsement.

The LiteRT-LM native DLL reports 0.2.0, while its official Python wheel is 0.18.0. Its core Apache permission is confirmed. The remaining question is whether the actual DLL contains additional mandatory third-party notices beyond the texts supplied here, including transitive Dawn/Tint, XNNPACK and tokenizer components. A missing SBOM by itself does not prohibit redistribution. Source-declared espeak-ng belongs to a separate Kokoro TTS target; its declaration alone does not establish GPL coverage of this photo embedding DLL. We have not established the complete binary closure and do not assert it. Concrete evidence and an external official-runtime acquisition alternative are described in docs/DISTRIBUTION_CLOSURE.md.

The complete Microsoft Evergreen WebView2 Runtime terms, obtained from Microsoft's official download-page API, are in licenses/native/WebView2/RUNTIME_EULA.html and RUNTIME_EULA.txt, separately from SDK LICENSE/NOTICE. They are also presented with the installation consent. WebView2 includes Microsoft Defender SmartScreen and may collect and send end-user information to Microsoft as described in https://aka.ms/privacy and https://learn.microsoft.com/en-us/microsoft-edge/privacy-whitepaper#smartscreen. The Runtime also has its own diagnostic/update behavior. This application does not implement photo/query upload or its own telemetry. Clean-Windows installation remains unverified. The owner ended repeated native-license investigation for 1.0.0; existing notices are retained without asserting an exhaustive binary SBOM or overriding any third-party rights.
