# fastapi-passkey-impl

## 起動する

1. 設定ファイルを作ります。すでに `.env` があれば、そのまま使います。

   ```sh
   cp -n .env.sample .env
   ```

2. `.env` の `INVITATION_CODE` を、自分で決めた推測されにくい半角英数字のコードに変更します。

3. 依存パッケージをインストールします。Python 3.13以上とuvが必要です。

   ```sh
   uv sync
   ```

4. サーバーを起動します。

   ```sh
   uv run uvicorn main:app --reload --port 8000
   ```

5. パスキー対応ブラウザーで [ログイン画面](http://localhost:8000/) を開きます。

## 使い方

- **新規登録：** 招待コードとニックネームを入力し、パスキーを登録します。同じ招待コードを複数の登録に使えます。
- **ログイン：**「パスキーでログイン」を押して、パスキーを選びます。ニックネームの入力は不要です。
- **ログアウト：** マイページの「ログアウト」を押します。

ニックネームは1〜100文字の文字・数字・`_`・`.`・`-`で指定します。日本語も使えます。メールアドレスは使えません。

## 設定を変えるとき

設定項目は [.env.sample](.env.sample) を参照してください。

- `.env` は起動時に読み込みます。変更後はサーバーを再起動してください。
- シェルで設定した環境変数は `.env` より優先されます。
- `INVITATION_CODE` が未設定・空文字の場合、新規登録を停止します。
- ホスト名やポートを変える場合は、`RP_ID` と `RP_ORIGIN` も合わせて変更してください。

データはSQLiteに保存します。初期設定の保存先は `db.sqlite3` です。

<details>
<summary>開発者向け：API仕様とテスト</summary>

API仕様は、起動後に [Swagger UI](http://localhost:8000/docs) で確認できます。

```sh
uv run python -m unittest discover -s tests -v
node --test tests/test_web.mjs
uv run ruff check main.py db/__init__.py db/auth.py lib handlers tests
uv run ty check main.py db lib handlers tests
```

</details>
