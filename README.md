# LawWeb

LawGitのHTTP Integration APIを利用する、Python + Flask製のログイン必須・閲覧専用Webアプリです。

## 機能

- ダッシュボード：法令数、ルール数、カテゴリ、接続状態
- 法令：一覧、種別絞り込み、詳細XML、識別子、変更履歴、過去版
- ルール：一覧、カテゴリ検索、Markdown本文、変更履歴、過去版
- 横断検索：LawGitのsearch API
- Git：コミット履歴、変更追従情報、ワークツリー状態、検証レポート
- 書き込みAPI・作成画面・編集画面・削除画面はありません

## 起動

必要なもの：Python 3.10以降、LawGit CLI、curl。

    sh run.sh

初回は .env が作成されます。LAWGIT_REPOをLawGitのデータリポジトリ（laws/ と rules/ を含む場所）の絶対パスに設定し、再実行してください。

初回の依存パッケージ取得時はPyPIへ接続します。その後は必要パッケージのimportを検査し、すでにインストール済みならpipを実行しません。ブラウザ側に外部CDN・解析サービスはありません。

最初の起動では、ターミナル上で初回アカウントの作成を求められます。ユーザー名と14文字以上のパスワードを入力してください。Web上に登録フォームはありません。

起動後に http://127.0.0.1:5000 を開きます。停止は Ctrl+C です。このスクリプトが起動したLawGit APIも一緒に停止します。既に起動していたAPIは停止しません。

## アカウント管理

アカウント作成（Web公開登録なし）：

    .venv/bin/flask --app app create-user

パスワード変更（該当ユーザーの全セッションを失効）：

    .venv/bin/flask --app app change-password --username USERNAME

全セッションを強制ログアウト：

    .venv/bin/flask --app app revoke-sessions

特定ユーザーの全セッションを強制ログアウト：

    .venv/bin/flask --app app revoke-sessions --username USERNAME

アカウント・パスワードハッシュ・セッション情報は instance/lawweb.sqlite3 に保存されます。署名鍵は instance/secret.key に自動生成されます。どちらもGitへ登録しないでください。

## 設定

| 変数 | 既定値 | 説明 |
|---|---|---|
| LAWGIT_REPO | 空 | laws/ と rules/ を含むデータリポジトリ |
| LAWGIT_BIN | lawgit | LawGit CLIコマンド |
| LAWGIT_API_HOST | 127.0.0.1 | 固定。外部待受不可 |
| LAWGIT_API_PORT | 8787 | LawGit APIポート |
| LAWGIT_TOKEN | 空 | LawGit APIトークン |
| LAWGIT_TIMEOUT | 10 | API接続タイムアウト |
| WEB_HOST | 127.0.0.1 | 固定。外部待受不可 |
| WEB_PORT | 5000 | Web UIポート |
| LAWWEB_SECRET_KEY | 空 | 任意の署名鍵。空欄ならinstance/secret.keyを生成 |
| LAWWEB_SESSION_HOURS | 8 | セッション絶対有効時間（最大24時間） |
| LAWWEB_IDLE_MINUTES | 30 | 無操作セッション期限（5〜240分） |

## セキュリティ設計

- すべての画面とデータAPIにログインを要求します。
- 公開登録はありません。ユーザー追加はローカルCLI限定です。
- パスワードはArgon2idでハッシュ化します。平文パスワードは保存しません。
- セッションIDは暗号学的乱数で発行し、DBにはSHA-256ダイジェストだけを保存します。
- セッションは8時間で絶対失効、30分無操作でも失効します。ログアウト・パスワード変更時にはサーバー側セッションを失効させます。
- アカウント単位・接続元単位のログイン試行制限を設けています。
- ログイン・ログアウトにはCSRFトークンとOrigin検査を適用します。
- CookieはHttpOnly、SameSite=Lax。データ画面はno-storeです。
- CSP、X-Frame-Options、nosniff、Referrer-Policy、Permissions-Policyを設定しています。
- Hostと接続元をループバックに制限し、FlaskではなくWaitressで待ち受けます。
- FlaskからLawGit APIへの通信先もループバックに固定し、トークンはサーバー内でのみ使用します。
- Flask側のルートはGET/HEADに限定し、書き込みHTTPメソッドを拒否します。LawGitへの中継もGETだけです。
- DB・署名鍵・.envはローカル権限を制限します。

## セキュリティ上の限界

「完璧なセキュリティ」は保証できません。この構成は単一マシン上のローカル利用専用です。LAN・インターネットへ公開する構成ではありません。ローカルPCに侵入した別ユーザーやマルウェア、OS管理者権限を持つ攻撃者からは保護できません。LawGit本体、OS、Python依存パッケージの脆弱性も別途更新が必要です。

LANやインターネットへの公開、複数端末からのアクセス、外部リバースプロキシはこのビルドでは意図的に無効化しています。公開運用が必要になった場合は、TLS終端、プロキシ境界、バックアップ、監査ログ、アカウントロック解除手順を含めて別途設計してください。

## API連携

ブラウザは同一オリジンの /api/* を呼び、FlaskがLawGit /api/v1/* の読み取りAPIだけを中継します。LawGitのデータは変更しません。
