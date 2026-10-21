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

## 外部サイトから台数を取得する

`GET /public/parking-lots` で、駐車場IDごとの空き台数を取得できます。ログインは不要で、読み取り専用です。どのサイトのJavaScriptからも取得できます。

台数は区分ごとに返します。`general` は一般駐車場、`accessible` は身体障害者用駐車場です。`accessible` は身体障害者用駐車場がある駐車場にだけ含まれます。

```json
{
  "parking-1": {"general": 3},
  "parking-3": {"general": 0},
  "parking-4": {"general": 12, "accessible": 1},
  "track-field": {"general": 5},
  "dormitory": {"general": 0},
  "library-rear": {"general": 7, "accessible": 0},
  "campus-road": {"general": 1},
  "gym-rear": {"general": 4}
}
```

| ID | 駐車場名 | 身体障害者用 |
|---|---|---|
| `parking-1` | 第1駐車場 | |
| `parking-3` | 第3駐車場 | |
| `parking-4` | 第4駐車場 | あり |
| `track-field` | トラック&フィールド駐車場 | |
| `dormitory` | 寮内駐車場 | |
| `library-rear` | 図書館裏駐車場 | あり |
| `campus-road` | 構内道路駐車場 | |
| `gym-rear` | 体育館裏駐車場 | |

レスポンスは最大15秒キャッシュされます。そのため、台数が変わってから反映されるまで、最大15秒程度かかります。

Next.js での例（サーバーコンポーネント）：

```js
const res = await fetch("https://<本番ドメイン>/public/parking-lots", {
  next: { revalidate: 15 },
});
const counts = await res.json(); // counts["parking-4"].accessible → 1
```

ブラウザ側（クライアントコンポーネント）で取得する場合も、同じURLを `fetch` すれば取得できます。最新の台数を表示し続けるには、数十秒ごとに取り直してください。

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
