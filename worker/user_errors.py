"""User-facing causes/actions; native details stay in the local technical log."""
import sqlite3


def friendly_error(error):
    if isinstance(error, FileNotFoundError):
        return 'ファイルまたは保存先が見つかりません。ドライブの接続とモデルの保存場所を確認してください'
    if isinstance(error, PermissionError):
        return 'ファイルにアクセスできません。読み取り権限と保存先の書き込み権限を確認してください'
    if isinstance(error, sqlite3.DatabaseError):
        return '検索データを読み書きできません。保存先の空き容量・接続・権限を確認してください。初期化せず、アプリを完全終了して再起動してください'
    message = str(error)
    if isinstance(error, ValueError) and any('\u3040' <= c <= '\u9fff' for c in message):
        return message
    if isinstance(error, OSError):
        return 'ファイルの処理に失敗しました。保存先の空き容量・ドライブ接続・アクセス権限を確認して再試行してください'
    return 'AIまたはメディアの処理に失敗しました。モデル設定・GPUドライバー・画像や音声の形式を確認してください。GPUで失敗する場合はCPUに変更できます。詳細は技術ログで確認できます'
