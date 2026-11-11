# LawWeb

LawGitのHTTP Integration APIを利用する、Python + Flask製のローカルWeb UIです。アカウント機能はありません。

## 主な機能

- ダッシュボード：法令数・ルール数・カテゴリ・最近のコミット・API接続状態
- 法令：一覧、種別による絞り込み、詳細XMLの閲覧、履歴・過去版の参照、新規登録
- ルール：一覧、検索、Markdown本文の作成・編集・削除、履歴・過去版の参照
- 横断検索：LawGitのsearch APIで法令とルールを検索
- Git：コミット履歴、変更追従API、ワークツリー状態、検証、一括コミット
- 競合検出：ルール更新・削除で取得時のversionをif_versionとして送信
- 認証トークンはFlask側だけで保持し、ブラウザへは渡しません

## 起動

必要なもの：Python 3.10以降、LawGit CLI、curl。

初回はプロジェクト直下で次を実行します。

    sh run.sh

初回実行時に .env が生成されるので、LAWGIT_REPOをLawGitのデータリポジトリの絶対パスに設定してください。対象ディレクトリにはLawGitが管理する laws/ と rules/ が必要です。

設定後、再度実行します。

    sh run.sh

このスクリプトが以下をまとめて行います。

1. LawGit APIが起動済みか確認
2. 起動していなければ lawgit --repo DIR serve をバックグラウンド起動
3. Python仮想環境を作成
4. requirements.txtをインストール
5. Flask Web UIを起動

スクリプトが起動したLawGit APIは、Web UIを終了するときに一緒に停止します。すでに起動していたAPIは停止しません。

ブラウザで http://127.0.0.1:5000 を開きます。

## 設定項目

| 変数 | 既定値 | 説明 |
|---|---|---|
| LAWGIT_REPO | 空 | laws/ と rules/ を含むデータリポジトリ |
| LAWGIT_BIN | lawgit | LawGit CLIのコマンドまたは実行ファイル |
| LAWGIT_API_HOST | 127.0.0.1 | LawGit APIの待受アドレス |
| LAWGIT_API_PORT | 8787 | LawGit APIのポート |
| LAWGIT_API_URL | http://127.0.0.1:8787 | Flaskから接続するAPIのベースURL |
| LAWGIT_TOKEN | 空 | LawGit APIのトークン。ブラウザには公開されない |
| LAWGIT_COMMIT | 0 | 1にするとLawGit serveを--commit付きで起動 |
| WEB_HOST | 127.0.0.1 | Flaskの待受アドレス |
| WEB_PORT | 5000 | Flaskのポート |

## セキュリティ上の注意

- アカウント機能・ユーザー認証は実装していません。
- 既定のWEB_HOSTは127.0.0.1です。LANやインターネットへ公開しないでください。
- WEB_HOSTを0.0.0.0に変更すると、ネットワーク上の他端末から操作可能になります。認証・TLS・アクセス制御を追加するまでは変更しないでください。
- LawGit APIのトークンはFlaskプロセス内でのみ使用します。HTMLやJavaScriptへ埋め込みません。
- .envには秘密情報を置けます。Gitにコミットしないでください（.gitignoreで除外）。
- 既定では自動コミットを有効にしません。必要な場合だけLAWGIT_COMMIT=1を指定するか、LawGit側で設定してください。

## API設計

ブラウザは同一オリジンの /api/* にアクセスし、FlaskがLawGitの /api/v1/* へ中継します。LawGitのレスポンス形式（ok/result または ok/error）を検査し、HTTPステータスを維持して返します。

LawGit APIに存在しない法令本文の更新・削除UIは追加していません。法令の作成フォームはlaw.createが受け付けるメタデータのみを扱います。ルールの作成・更新・削除はrule APIの仕様に従います。
