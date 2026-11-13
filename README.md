# LawWeb

LawGitのHTTP Integration APIを利用する、Python + Flask製の一部公開・ログイン拡張型の閲覧専用Webアプリです。

## 公開範囲

### ログインなしで閲覧可能
- トップページと公開ダッシュボード
- 法令一覧・種別による絞り込み
- 法令名、法令番号、e-Gov ID、種別、カテゴリ
- 法令の基本メタデータ

### ログイン後に閲覧可能
- 法令XML全文、UUID・バージョン・ファイルパス
- ルール一覧・Markdown本文
- 法令・ルールの変更履歴と過去版
- 横断検索
- Gitコミット履歴、変更追従情報、ワークツリー状態、検証結果

Web UIおよびFlask APIに書き込み機能はありません。アカウントは閲覧専用です。

## 起動

必要なもの：Python 3.10以降、LawGit CLI、curl。

    sh run.sh

初回は .env が生成されます。LAWGIT_REPOをLawGitのデータリポジトリ（laws/ と rules/ を含む場所）の絶対パスに設定し、再実行してください。

初回の依存パッケージ取得時はPyPIへ接続します。その後は必要パッケージのimportを検査し、すでにインストール済みならpipを実行しません。ブラウザ側に外部CDN・解析サービスはありません。

起動後に http://127.0.0.1:5000 を開きます。停止は Ctrl+C です。このスクリプトが起動したLawGit APIも一緒に停止します。既に起動していたAPIは停止しません。

## アカウント登録・ログイン

トップページの「新規登録」からアカウントを作成できます。登録成功後は自動的にログインします。ユーザー名は3〜32文字、パスワードは14文字以上です。

同一接続元からの登録試行は15分間に3回までです。登録フォームにはCSRFトークンを要求します。

ログイン画面：

    http://127.0.0.1:5000/login

登録画面：

    http://127.0.0.1:5000/register

既存アカウントでログインできない場合は、ローカルターミナルでパスワードを再設定できます。

    .venv/bin/flask --app app change-password --username USERNAME

パスワード変更時には、そのユーザーの既存セッションをすべて失効させます。

アカウント単位のログイン試行制限に達してしまった場合は、15分待つか、ローカル管理者が次のコマンドで制限を解除できます。

    .venv/bin/flask --app app reset-login-limits

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

- 公開用APIは法令の基本メタデータだけを返します。XML本文・ルール本文・Git詳細は返しません。
- 詳細ページとプライベートAPIにはログインを要求します。
- 新規登録はWebから可能ですが、登録レート制限とCSRF検査を実施します。アカウントに書き込み権限はありません。
- パスワードはArgon2idでハッシュ化します。平文パスワードは保存しません。
- セッションIDは暗号学的乱数で発行し、DBにはSHA-256ダイジェストだけを保存します。
- セッションは8時間で絶対失効、30分無操作でも失効します。ログアウト・パスワード変更時にはサーバー側セッションを失効させます。
- アカウント単位・接続元単位のログイン試行制限を設けています。
- ログイン・登録・ログアウトにはCSRFトークンとOrigin検査を適用します。
- CookieはHttpOnly、SameSite=Lax。データ画面はno-storeです。
- CSP、X-Frame-Options、nosniff、Referrer-Policy、Permissions-Policyを設定しています。
- Hostと接続元をループバックに制限し、FlaskではなくWaitressで待ち受けます。
- FlaskからLawGit APIへの通信先もループバックに固定し、トークンはサーバー内でのみ使用します。
- Flask側のデータAPIはGETのみ、LawGitへの中継もGETだけです。
- DB・署名鍵・.envはローカル権限を制限します。

## セキュリティ上の限界

「完璧なセキュリティ」は保証できません。この構成は単一マシン上のローカル利用専用です。LAN・インターネットへ公開する構成ではありません。ローカルPCに侵入した別ユーザーやマルウェア、OS管理者権限を持つ攻撃者からは保護できません。LawGit本体、OS、Python依存パッケージの脆弱性も別途更新が必要です。

LANやインターネットへの公開、複数端末からのアクセス、外部リバースプロキシはこのビルドでは意図的に無効化しています。公開運用が必要になった場合は、TLS終端、プロキシ境界、バックアップ、監査ログ、アカウントロック解除手順を含めて別途設計してください。

## API連携

ブラウザは同一オリジンの /api/* を呼び、FlaskがLawGit /api/v1/* の読み取りAPIだけを中継します。公開APIは /api/public/* に分離し、返却フィールドをホワイトリストで制限します。LawGitのデータは変更しません。
