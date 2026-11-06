# ローカルMCP / CLI

起動中のSakuLocaを、画面操作なしで検索・状態確認・解析開始/停止・復旧・終了できます。
アプリのRust側で既存のWorker brokerを共有し、別Worker・別AIエンジン・別SQLite接続を起動しません。

## 接続

Windows名前付きパイプを使い、接続権限はアプリを起動したWindowsユーザーのSIDだけに設定します。
リモート接続を拒否し、TCP/HTTPの待受ポートは開きません。保存先の`automation.json`は接続先とPIDだけを持ちます。
アプリの再起動ごとに接続先を更新し、CLI/MCPは呼び出しごとに読み直します。終了後の接続先ファイルは無効になり、アプリを起動するよう案内します。

Node.js 22以上で`mcp/server.mjs`を実行します。外部パッケージやクラウドAPIキーは不要です。
通常は`%LOCALAPPDATA%/LocalPhotoSearch/storage.json`からアプリの保存先を読みます。
隔離テストでは`--data <保存先>`を明示します。起動中のアプリと同じ保存先・Windowsユーザーを使ってください。

```powershell
node mcp/server.mjs --call status
node mcp/server.mjs --call search '{"text":"海辺","limit":5}'
node mcp/server.mjs --call analysis '{"action":"pause"}'
node mcp/server.mjs --call desktop '{"action":"quit"}'
```

実際のPowerShellでJSONを渡す場合、ネイティブ引数の引用規則は使用バージョンに従います。自動化スクリプトからはプロセスの引数配列で渡す方法を推奨します。

## Codexへの登録

インストール先に同梱されている `mcp/server.mjs` をSTDIOサーバーとして登録します。例のパスは実際のインストール先に置き換えてください。

```powershell
codex mcp add sakuloca -- node 'C:\path\to\SakuLoca\mcp\server.mjs'
```

登録後はCodex側のMCP接続を再読み込みします。追加したツールが現在の会話にまだ現れない場合も、上記CLIをシェル経由で呼び出せます。
サンドボックス内のシェルからの接続が`EPERM`になる場合は、アプリと同じWindowsユーザー権限でサンドボックス外からCLIを実行してください。MCP本体もアプリと同じWindowsユーザーで起動してください。
通常の状態確認・検索・操作はMCP/CLIを優先し、見た目や配置を確認するときだけ画面操作を使います。

ツールは`status`、`folders`、`errors`、`search`、`analysis`、`desktop`の6種類です。
検索は既定10件・最大60件で、画像本体は返さずファイル情報だけを返します。
名前・パス・検索文・エラー文など、返した情報はAIとの会話に渡ります。解析開始/停止/再試行と復旧/終了は利用者が指示した範囲で行います。
Worker再起動と完全終了は解析停止を確認してから実行し、解析中には画面ダイアログを出さずにエラーを返します。
任意コード実行、写真の削除、保存先移行、自動起動の変更、モデル取得はこのAPIから公開しません。

1.1.0の音声検索では、`search` と `errors` に `kind: "audio"` を指定できます。省略時は従来どおり写真です。音声結果はファイルIDに加えて `segment_id`、`start_ms`、`end_ms`、`duration_ms` と `kind` を返します。画像・音声本体やベクトルはMCPから返しません。音声の区間再生はアプリ内だけで行います。

`analysis` の開始・再試行にも `kind: "audio"` を指定します。開始時にkindを省略すると前回の解析対象を再開し、一時停止は対象共通です。`status` のトップレベル件数は写真を維持し、`audio` に音声ファイル数・区間ジョブ数・エラーを追加しています。`analysis_kind` は最後に開始した対象を示します。WAV（非圧縮PCM 16bit）・MP3・FLACに対応し、`format: "wav"` / `"mp3"` / `"flac"` で絞れます。1.0.1にはこれらの追加引数がないため、アプリと同じ版の `mcp/server.mjs` を使ってください。

新しいMCPは音声操作の前に対応状態を確認します。旧アプリでは音声操作を拒否し、旧Workerが引数を無視して写真解析へ進むことを防ぎます。一時停止は旧版でも対象共通で使えます。

```powershell
node mcp/server.mjs --call search '{"kind":"audio","text":"電子音","limit":5}'
node mcp/server.mjs --call analysis '{"action":"start","kind":"audio"}'
node mcp/server.mjs --call errors '{"kind":"audio"}'
```

## 検証

`node --test scripts/test-mcp.mjs`で、MCP初期化・ツール一覧・引数検証・名前付きパイプ通信・実STDIOプロセスを確認します。
実アプリとの検証は`data/release-tests/`内の隔離保存先と人工画像を使います。
