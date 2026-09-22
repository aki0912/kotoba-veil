# ai4privacy/pii-masking-mini-10k の評価

## 出典・利用条件

- データセット: [ai4privacy/pii-masking-mini-10k](https://huggingface.co/datasets/ai4privacy/pii-masking-mini-10k)
- 固定リビジョン: `7b686c2e7475b02e10e38add1eb54a9f604f4361`
- 著作権表示: Copyright © 2026 Ai Suisse SA / ai4privacy
- ライセンス: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- 原本の説明: 合成PIIを含む多言語データ。翻訳・モデル学習は行わず、元の本文で評価する。
- このプロジェクトでの変更: 言語選択、タグの対応付け、必要に応じた隣接する姓・名の結合、
  評価用JSONLへの変換。元のタグ・本文・文字位置も保存する。

全体9,990件（train 9,000件、validation 990件）、30言語。
日本語はtrain 300件、validation 33件。主結果はvalidation、trainは参考結果とし、
混ぜて1つの独立評価スコアにしない。元のsplit名をそのまま保存する。
日本語検出器で他言語を実行した結果は、多言語対応能力の保証にはならない。

配布カードの概要は19ラベル・72,988注釈となっているが、固定したJSONLの実ファイルには
TIMEや少数の団体名などもあり、27ラベル・73,186注釈を確認した。
取り込みはカードのラベル一覧だけに依存せず、実データの件数をmanifestに記録する。
未知のラベル・本文と合わない位置・重複ID・元ファイルのハッシュ不一致はエラーとし、
行を黙って除外したり位置を推測して修正したりしない。

## 取得・測定

追加パッケージやHugging Faceアカウントは不要。最初の取得だけネットワークを使い、
以後は検証済みのキャッシュを利用する。取得するJSONLとREADMEはデータとして扱う。
アプリ本体の起動・文書処理で外部データを取得することはない。

```bash
python -m benchmarks.import_ai4privacy --languages ja --name-spans merged

python -m benchmarks.run \
  --dataset data/benchmarks/ai4privacy-pii-masking-mini-10k/converted-merged/ja-validation.jsonl \
  --warmup 10 --output build/ai4privacy/ja-validation.json

python -m benchmarks.run \
  --dataset data/benchmarks/ai4privacy-pii-masking-mini-10k/converted-merged/ja-train.jsonl \
  --warmup 10 --output build/ai4privacy/ja-train.json
```

完全オフラインで再変換するには取り込み時に `--offline` を指定する。
全言語を取り込むには `--languages all`、任意の言語は `--languages ja en` のように指定する。
全言語の場合は `all-validation.jsonl` / `all-train.jsonl` が生成され、
評価結果の `by_language` に言語別の精度・時間も出力される。
`--splits validation` で変換対象のsplitを限定することもできる。

氏名を結合せずに測る場合は `--name-spans separate` を使用する。
出力先も `converted-separate/` に分かれ、結合版を上書きしない。
速度を比較するときは同じ入力ハッシュ・タグ変換設定・辞書条件・ウォームアップ数で、
別プロセスの測定を複数回行い、中央値と範囲を記録する。
`--warmup 10` は最初の10件を追加で処理して準備する指定であり、評価対象から10件を除外しない。
モデル読み込みとウォームアップ時間は検出時間に含めない。

データは `data/benchmarks/`、測定結果は `build/` に保存し、Gitへデータ本体を追加しない。
原本README、原本SHA-256、変換後SHA-256、タグ対応表、元タグ件数、対応外件数、
ダウンロードを除いた変換時間はキャッシュとmanifestで確認できる。
変換時間やダウンロード時間は、検出器の処理時間とは別の指標である。

## タグの対応

| 元タグ | アプリの評価タグ |
| --- | --- |
| GIVENNAME / SURNAME | PERSON |
| EMAIL | EMAIL_ADDRESS |
| TELEPHONENUM | PHONE_NUMBER |
| ZIPCODE | POSTAL_CODE |
| DATE / TIME | DATE_TIME |
| CITY / COUNTRY | LOCATION |
| STREET / BUILDINGNUM | ADDRESS |
| DRIVERLICENSENUM | DRIVER_LICENSE |
| CREDITCARDNUMBER | CREDIT_CARD |
| BANKNAME / ORGANISATION | ORGANIZATION |
| URL | URL |

隣接したGIVENNAME・SURNAMEが同じ `label_index` を持ち、間が空または横方向の空白だけなら、
既定ではフルネーム1件に結合する。句読点や改行をまたいだ結合はしない。
住所は元のSTREET・BUILDINGNUMの範囲を維持し、周辺の地名や助詞を含めた住所の推測はしない。
このため、アプリの住所全体の検出と正解の部品単位の注釈は完全一致しないことがある。

TITLE、AGE、GENDER、SEX、IDCARDNUM、SOCIALNUM、PASSPORTNUM、TAXNUM、JOBTITLE、AMOUNT、SALARYは
現在のアプリ分類に直接対応しないため、カテゴリ別の完全一致・部分一致評価から除く。
一般的なID番号・税番号を日本のマイナンバーへ置き換えたり、CUSTOMにまとめたりしない。
対応外タグを含む行も残し、元注釈は `source_entities` にすべて保存する。
正解タグを辞書へ登録して検出することはない。

この評価では上表に対応する11種類の検出項目を全サンプルで有効にする。
その行に正解がない種類の予測も誤検出として数える。`enabled_entities` がサンプルに
設定されているので、この入力ではサンプルの設定がCLIの `--entities` より優先される。

## 結果の読み方

- `exact` / `overlap`: 対応タグに変換した正解についてのPrecision・Recall・F1。
  元データの全27種類に対するスコアではない。完全一致は分類・開始位置・終了位置すべてを要求する。
- `source_pii_coverage`: 対応外タグを含む元の全注釈が、出力されたマスク候補でどこまで覆われたか。
  分類を問わず、元タグ別の注釈件数・全範囲を覆えた件数・覆えた文字数を記録する。
- `character_recall`: 元PIIの文字のうちマスクできた割合。
  `character_precision`: マスクした文字のうち元PIIとして注釈されている割合。
  全文をマスクするだけで良い評価にならないよう、過剰なマスクも別途評価する。
  同じ文字の重複は文書単位の全体値では1回だけ数える。
- `latency_ms`: 1文ごとの平均・中央値・p95・最大、全件の合計、文字/秒。
  データの取得・変換・読み込み・スコア集計・HTTP通信・利用者の確認は含まない。
- `metadata`: モデル読み込み時間、ウォームアップ件数・時間、入力ハッシュ、実際の言語、
  split、Python・パッケージ、検出設定。元の注釈数も記録する。

マスク範囲の評価は全検出候補を受理した場合の文字範囲であり、PDF出力の安全性を
評価するものではない。正解の注釈漏れ・不自然な合成文・住所の粒度差も結果に影響する。
このデータセットでの低スコアを、元のレビュー済みPDFの精度と混同しない。
この評価を使って検出ルールを調整した後は、調整に使ったsplitを独立な最終評価と呼ばない。
