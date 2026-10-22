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

## コンテナで起動する（Docker / Podman）

`.env.sample` を `.env` にコピーし、`INVITATION_CODE` を設定してから起動します。

```sh
docker compose up -d --build
```

Podmanを使う場合は、Composeプロバイダー（`podman-compose` など）をインストールし、`docker compose` を `podman compose` に読み替えてください。

起動後は http://localhost:8000/ を開きます。ログと停止は次のコマンドで確認・実行できます。

```sh
docker compose logs -f app
docker compose down
```

コンテナではSQLiteを名前付きボリューム `app-data` の `/app/data/db.sqlite3` に保存します。ホスト側の既存の `db.sqlite3` は自動では取り込みません。`down` だけではデータは削除されませんが、`down -v` はデータも削除するので注意してください。ソースコードを変更した場合は `up -d --build` で再ビルドします。

### Cloudflare Tunnelを使う

`.env` に `CLOUDFLARED_TOKEN` を設定し、公開URLに合わせて `RP_ID`（ホスト名のみ）と `RP_ORIGIN`（`https://` を含むURL）を変更します。Cloudflare側のサービスURLは `http://app:8000` に設定してください。

```sh
docker compose --profile tunnel up -d --build
```

通常の起動ではTunnelは起動しません。Tunnelも停止するときは `docker compose --profile tunnel down` を使います。トークンや `.env` はイメージには含めません。

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
- `PUBLIC_HOURS` は、外部サイトに台数を公開する期間です。詳しくは「[外部サイトから台数を取得する](#外部サイトから台数を取得する)」を参照してください。
- ホスト名やポートを変える場合は、`RP_ID` と `RP_ORIGIN` も合わせて変更してください。

データはSQLiteに保存します。初期設定の保存先は `db.sqlite3` です。

## 外部サイトから台数を取得する

`GET /public/parking-lots` で、駐車場IDごとの空き台数を取得できます。ログインは不要で、読み取り専用です。どのサイトのJavaScriptからも取得できます。

台数は `lots` に、区分ごとに入っています。`general` は一般駐車場、`accessible` は身体障害者用駐車場です。`accessible` は身体障害者用駐車場がある駐車場にだけ含まれます。

```json
{
  "open": true,
  "lots": {
    "parking-1": {"general": 3},
    "parking-3": {"general": 0},
    "parking-4": {"general": 12, "accessible": 1},
    "track-field": {"general": 5},
    "dormitory": {"general": 0},
    "library-rear": {"general": 7, "accessible": 0},
    "campus-road": {"general": 1}
  }
}
```

### 公開する時間を決める

`.env` の `PUBLIC_HOURS` に、台数を公開する期間（大学祭の時間）を書きます。「開始/終了」の形で書き、複数の期間はカンマで区切ります。時刻は日本時間です。

```sh
PUBLIC_HOURS='2026-11-01T09:00/2026-11-01T17:00,2026-11-02T09:00/2026-11-02T16:00'
```

- 開始時刻ちょうどから公開し、終了時刻ちょうどに非公開になります。
- 時間外は `open` が `false` になり、台数はすべて `null` になります。駐車場IDの一覧はそのまま返ります。
- `PUBLIC_HOURS` が未設定・空文字の場合は、常に公開します。本番では必ず設定してください。
- 書き方が間違っている場合は、サーバーが起動しません。
- マイページでは、時間外でも台数を確認・変更できます。

```json
{
  "open": false,
  "lots": {
    "parking-1": {"general": null},
    "parking-4": {"general": null, "accessible": null}
  }
}
```

（上の例は一部の駐車場を省略しています。）

| ID | 駐車場名 | 身体障害者用 |
|---|---|---|
| `parking-1` | 第1駐車場 | |
| `parking-3` | 第3駐車場 | |
| `parking-4` | 第4駐車場 | あり |
| `track-field` | トラック&フィールド駐車場 | |
| `dormitory` | 寮内駐車場 | |
| `library-rear` | 図書館裏駐車場 | あり |
| `campus-road` | 構内道路駐車場 | |

レスポンスは最大15秒キャッシュされます。そのため、台数の変化や公開・非公開の切り替えが反映されるまで、最大15秒程度かかります。

Next.js での例（サーバーコンポーネント）：

```js
const res = await fetch("https://<本番ドメイン>/public/parking-lots", {
  next: { revalidate: 15 },
});
const { open, lots } = await res.json();
if (!open) {
  // 大学祭の時間外。台数の代わりに「時間外」などと表示する
}
// lots["parking-4"].accessible → 1
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
