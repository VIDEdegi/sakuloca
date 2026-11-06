# SakuLoca（サクロカ）

名前の由来は「サクッと、ローカル、サーチ。」開発元はVIDEdegi（ビデデジ）です。

Windows内の写真と音声を言葉で探す無料のローカルAI検索アプリです。写真は参照画像からも探せます。EmbeddingGemma 2による処理はPC内で行い、写真・音声・検索語を外部へ送信しません。

1.1.0ではWAV（非圧縮PCM 16bit）・MP3・FLACを時刻付きの区間として検索し、前後を含めて再生できます。対応範囲は [リリースノート](docs/RELEASE_NOTES_V1_1.md)、検索品質と負荷の実測は [音声機能の評価](docs/AUDIO_RELEASE_EVALUATION.md) を参照してください。

![検索画面（人工画像による動作例）](docs/images/search.png)

## ダウンロード

インストールから初回設定・写真検索までの [公式ブログ解説（1.0.1の画面）](https://blog.videdegi.com/2026/10/sakuloca.html) もご覧ください。

**[GitHub Releases](https://github.com/VIDEdegi/sakuloca/releases/latest)** から Windows x64 用のセットアップ（`SakuLoca_1.1.0_x64-setup.exe`）を取得してください。「Source code」は開発者向けで、通常のインストーラーではありません。

1. セットアップを実行。未署名のためWindows/SmartScreenの警告が出る場合があります。公式配布元とSHA256SUMSを確認してください。
2. 初回案内でPCを診断し、既存の `.litertlm` モデルを指定するか、配布元・容量・条件を確認して公式モデルを取得。
3. フォルダーを登録し、写真または音声を選んで「差分更新 / 解析」。
4. 「海辺の風景」などで日本語検索、または「画像で探す」で類似画像検索。

<small>旧称『Local Photo Search』v1.0.0をご利用の方で、更新・移行に問題がある場合は [GitHub Issues](https://github.com/VIDEdegi/sakuloca/issues) からお問い合わせください。</small>

詳しくは [利用ガイド](docs/USER_GUIDE.md)。モデルは別途準備します。Python・Rust・Node.jsの導入は利用者に不要です。WebView2とMicrosoft Visual C++ v14 x64が不足する場合はMicrosoft公式の導入方法を案内します。

## 対応環境

- Windows 11 64bit。
- GPU確認済み：NVIDIA RTX 2070（8GB）。他GPUは未検証で、アプリ内診断が必要です。
- CPU：4スレッドの診断と少数人工画像で確認。大量写真の実用速度は未評価。
- Windows 10、ARM、32bitは初期サポート対象外。
- モデル約462MiB、AIキャッシュ最大1GiB、サムネイル最大512MiBと検索データ用の空き容量が必要。

クリーンWindows導入、スリープ・実OS再起動の受入確認は残っています。更新署名はWindowsコード署名とは別で、SmartScreen警告をなくすものではありません。

## 主な機能

- JPEG / PNG / WebP、サブフォルダー、追加・変更分の解析。
- 日本語・類似画像検索、名前・形式・フォルダー・更新日フィルター、並び順、最近の検索。
- グリッド/リスト、ページ番号ジャンプ・ページサイズ、プレビュー前後移動・ズーム、元ファイル/保存場所を開く、パスをコピー。
- 画像/フォルダーのドラッグ＆ドロップ、解析の停止・再開・エラー確認、初回設定、モデル情報、ライト/ダークテーマ。
- 許可した場合のトレイ常駐・バックグラウンド解析・Windowsログイン時の起動。
- 保存先の安全なコピー移行（再生成可能なAIキャッシュは除外）、容量・キャッシュ・サムネイル管理。
- ウィンドウのサイズ/位置の記憶、完了/失敗の通知、失敗した更新からのWorker復帰。

背景実行は既定OFF。許可すると×でトレイへ格納し、開始済みの解析が続きます。トレイの「完全終了」でWorkerも停止します。低負荷/アイドル設定により待機する場合があります。

## 更新・保存データ・プライバシー

起動時の更新確認は設定で無効化できます。更新があれば画面内で通知します。設定から手動確認し、バージョン・変更内容を見て署名付き更新を実行できます。更新前に解析を安全に停止します。セットアップの起動に失敗した場合は解析Workerを自動で再起動します。その場合は完全終了し、Releasesのセットアップで手動更新してください。

更新・アンインストールで写真・モデル・検索データを削除しません。初期保存先は `%LOCALAPPDATA%/LocalPhotoSearch/data`、既存の保存先指定を優先します。再インストールでも既存データを利用できます。

外部通信は、確認後のモデル取得、GitHubへの更新確認/取得、必要なMicrosoft Runtime導入に使用します。写真・音声・検索語を送信するクラウド検索やアプリ独自テレメトリーはありません。OS/WebView2の通信は各提供元の設定に従います。

## 報告・開発・ライセンス

[Issues](https://github.com/VIDEdegi/sakuloca/issues) でバグ・提案を受け付けます。写真・個人用パスを投稿せず、ログは必要箇所を伏せ字にしてください。脆弱性は [非公開報告](SECURITY.md) を使ってください。

[CONTRIBUTING](CONTRIBUTING.md)、[行動指針](CODE_OF_CONDUCT.md)、[開発環境](docs/SETUP.md) を参照してください。Tauri 2 / Rust / Lit / TypeScript / SQLite / Python LiteRT-LMで構成しています。PRのCIは型・軽量安全性テスト・Rustチェックで、モデル/GPUは不要です。

独自コードは **Apache-2.0**。適用範囲は [LICENSE_SCOPE](LICENSE_SCOPE.md)、第三者の条件は [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md) と `licenses/` を参照してください。AIモデルは別の条件で取得します。

[リリース運用](docs/RELEASE_PROCESS.md)と[変更履歴](CHANGELOG.md)を参照してください。
