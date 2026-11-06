# 音声検索の修正と公開候補の評価（2026-10-30）

対象はSakuLoca 1.1.0の未公開候補。以前のdev.2評価を残したまま、実装側の問題を修正し、別録音で確認した。実写真・私的録音は使用していない。

## 修正した点

- 音声の検索文prefixを `task: search result | query: ` に修正。[公式モデルカード](https://huggingface.co/google/embeddinggemma-2#1-task-instruction-prefixes) と [公式検索デモの実装](https://github.com/google-ai-edge/LiteRT-LM/blob/main/js/apps/embedding_search/src/app_config.ts) に合わせた。[LiteRTガイド](https://developers.google.com/edge/litert-lm/embedding_models) の別の記載を使っていた。写真のprefixと既存ベクトルは変更しない。
- 一般的な短い日本語の音名に、ローカルの同義語補助を追加。完全一致する短い音名だけを英語の表現で検索し、適用した音名をUI/MCPへ返す。否定・複合音・自由な文章や発話内容は変更しない。追加モデル、外部翻訳API、音声送信はない。
- 検索区間の前後5秒を含めて再生する。検索区間と実際の再生範囲を別々に表示し、元ファイルの時刻も再生範囲に合わせる。最大40秒に制限し、ファイル先頭・末尾へクランプする。解析窓は30秒・5秒重複のままで、解析量を増やさない。

固定1024長と可変入力長の比較では元14検索の順位改善がなかったため、入力長は変えていない。語彙やファイル名を使って正解音源を直接上位へ上げる処理、評価音源IDへの例外は加えていない。

## 評価の順序と音源

1. 既存8音源・14検索でprefix候補を比較。モデルカードの指定を採用した。
2. [ESC-50](https://github.com/karolpiczak/ESC-50) の20カテゴリーについて、fold2/5内でファイル名順の最初の音源を各1件、計40件取得。別のlibrosa音楽4件とWindows Harukaによる日本語人工発話8件を加え、計52音源・日英64検索で問題を診断し、短い音名の補助を開発した。これは開発用評価で、未見テストとはしない。
3. 仕様固定後、同じカテゴリーの未使用fold1/4から同じ選択規則で40件を取得。音楽4件・人工発話8件は回帰対象として再使用した。新しい録音を使う確認であり、未知カテゴリーや未知の話者・自然な日本語会話への一般化試験ではない。

ESC-50はCC BY-NC 3.0（ESC-10の対象分はCC BY）。提供元READMEと各クリップの著作権表示を隔離領域に保存し、このPCでの評価だけに利用した。音楽は[librosa公式の音源](https://librosa.org/doc/latest/recordings.html)の別のジャズ・ドラム/ベース・オーケストラ・ピアノを12秒に区切った。出典テキストを同じ場所に保持した。日本語人工発話は `speech.json` に文と日英クエリを事前に保存してから生成。TTSをアプリ機能として追加したわけではない。元音源はGit・インストーラー・公開用ZIPに含めない。

## 結果

| 評価 | 修正前 1位 / 3位以内 | 修正後 1位 / 3位以内 |
|---|---:|---:|
| 開発用52音源・64検索 | 31 / 51 | 37 / 59 |
| 別録音40件＋回帰12件・64検索 | 36 / 48 | 37 / 55 |
| 従来8音源・14検索（GPU） | 8 / 12 | 10 / 12 |
| 従来8音源・14検索（CPU） | 8 / 11 | 12 / 12 |

同義語を使う日英の音名は最終的に同じ英語ラベルになるため、各検索を独立した統計標本とは扱わない。環境音では同じカテゴリーの2ファイルのどちらかが該当した最上位の順位を数え、全52音源の中で比較した。小規模な手選択カテゴリーでの診断値で、一般的な正解率ではない。

別録音の環境音だけでは1位が19→18件、3位以内は30→34件だった。全検索が改善したわけではない。従来のTensorFlow猫音源は修正後も4位で、騒音・言語・音源によるモデルの弱さは残る。これをアプリ側の特例で隠さず、候補検索として公開ガイドへ明記した。

## 境界と操作

6.731秒の音を24/28/49/53秒へ配置した4ケースで、CPU/GPUとも上位結果の再生範囲が音全体を含むことを確認。検索区間自体に全体が含まれるとは限らず、前後を含めても長い音が切れる場合は隣区間・元ファイルで確認する。検索の完全性を保証するものではない。

実WebView2で、英語音名の解釈表示、MCPで同じ解釈が返ること、各形式の40秒再生、検索区間25〜55秒に対する再生20〜60秒、前後移動、閉じた際の停止、写真切替を確認。1280×820、860×820、860×620で横はみ出しなし。スクリーンショットも目視確認した。

## 負荷と回帰

最終配布WorkerのCPUで、各10分の48kHz stereo WAV/MP3/FLACを処理。72区間、エラー0、停止・終了・再開、強制終了からの復旧、末尾の再生、DB整合性、元音源SHA、人工写真ベクトル、保存済みインデックス再読込を確認。実行382.0秒、Peak Working Set 1,234,604,032 bytes（約1177.4MiB）。これはこのCPU試験全体の値。

GPU配布Workerでは全形式、無音・不正WAVの分離、写真回帰、検索・前後付き再生を再確認。区間デコードとインデックス保存の基本経路は変更せず、dev.2で実施した各2時間×3形式・864区間のGPU負荷記録も保持する。修正版で同じ6時間分を再実行したという意味ではない。CPUで2時間×3形式を評価済みとはしない。

音声回帰13件、MCP8件、UI回帰8ケース、安全性9件、TypeScript/Vite build、Rust checkが成功。依存やDB schemaは今回変更していない。

## 記録と再現

- `data/release-tests/audio-independent-final-gpu-20261030/results.json`
- `data/release-tests/audio-confirmation-gpu-20261030/results.json`
- `data/release-tests/audio-quality-release-gpu-20261030/results.json` / `audio-quality-release-cpu-20261030/results.json`
- `data/release-tests/audio-release-cpu-endurance-20261030/results.json`
- `data/release-tests/audio-release-runtime-gpu-20261030/results.json` / `audio-release-desktop-20261030/results.json`

`scripts/evaluate-audio-independent.py`、`evaluate-audio-quality.py`、`test-audio-long.py --backend cpu --seconds 600`、`test-audio-runtime.py --packed --formats` と `test-audio-desktop.py --formats` が再現手順。出力は新しい `data/release-tests/` 内のフォルダーを指定する。

## 公開範囲と既知の制限

Windows 11 x64 / RTX 2070で確認。他GPU、クリーンWindows、実際のスピーカー出音、自然な日本語会話・騒音混在の大規模品質評価は未実施。AAC/M4A・Ogg、文字起こし・話者特定・動画は非対応。利用者向けの対応範囲と更新手順は [1.1.0リリースノート](RELEASE_NOTES_V1_1.md) を参照。
