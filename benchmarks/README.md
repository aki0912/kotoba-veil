# Kotoba Veil benchmark

このフォルダは、PII検出精度を再現可能な方法で測定するための評価資産です。
`ai4privacy/pii-masking-mini-10k` の取り込み・日本語評価にも対応しています。
[取得方法・タグ対応・評価範囲](datasets/ai4privacy-mini.md)を参照してください。
[日本語での初回測定結果](results/2026-09-22-ai4privacy-mini.md)も保存しています。
配布元のタグに本文との不一致があるため、[本文から作り直した日本語版とレビュー画面](datasets/ai4privacy-reannotated.md)
を用意しています。再注釈の暫定版を測る場合は `--allow-draft` を明示してください。
実測結果は `results/` に保存します。現在の基準結果は
`results/2026-08-01.md` です。
利用者生成の日本語218文書のレビュー完了版で測った現状値は
[2026-09-23の測定結果](results/2026-09-23-generated-ja-218.md)に記録しています。
GiNZA有効・辞書なしで3回測定し、精度、処理時間、分類別の失敗傾向を保存しています。
`datasets/synthetic-v1.jsonl` は実在人物の情報を含まない、固定seedで生成した
1,000件の日本語合成評価セットです。`datasets/smoke.jsonl` は評価パイプライン自体の
小規模な動作確認に使用します。合成データだけでは実運用分布を再現できないため、
将来は利用条件を確認した公開コーパスや、適切に匿名化・管理された人手レビュー済み
社内評価データも同じ形式で追加します。

1,000件版の内訳は、単一PII文書750件、3種類のPIIを含む文書100件、PIIではない
番号などを含むハードネガティブ文書150件です。15種類のentity typeには、それぞれ
70個の正解スパンがあります。合計正解スパン数は1,050件です。管理用のサンプル番号は
本文へ埋め込まず、`id` と `template_id` で管理します。

## データ形式

利用者が生成した `id`・`text` のJSONLについても、本文から付けた正解タグを
レビュー画面で編集できます。今回の218件はローカルの
`data/annotation-review/generated-ja-218/README.md` に手順と注釈方針を記録しています。
全件devの暫定版で、確認後のrevisionを評価に使用します。レビュー画面は
`python -m benchmarks.review_reannotations --root data/annotation-review/generated-ja-218 --port 8013`
で起動します。出典・利用条件・出力区分はデータセットごとに保持されます。

1行を1文書とするUTF-8 JSONLです。正式な定義は `schema.json` にあります。
スパン位置はPython文字列のコードポイント単位で、`start` は包含、`end` は非包含です。
各スパンの `text` は `text[start:end]` と完全一致しなければなりません。

最低限必要なフィールドは次のとおりです。

```json
{
  "id": "ja-contact-001",
  "language": "ja",
  "split": "test",
  "source": "synthetic",
  "text": "連絡先は090-1234-5678です。",
  "entities": [
    {
      "entity_type": "PHONE_NUMBER",
      "start": 4,
      "end": 17,
      "text": "090-1234-5678"
    }
  ],
  "tags": ["contact"]
}
```

`source` は `synthetic`、`licensed`、`internal` のいずれかです。公開データを
追加するときは、データセットのライセンスと由来を同じフォルダのREADMEに記録します。
アプリのPII辞書を評価するケースでは、サンプル単位の `dictionary_terms` を指定できます。

## 実行方法

GiNZAを含む通常構成を評価します。

```bash
python -m benchmarks.run \
  --dataset benchmarks/datasets/synthetic-v1.jsonl \
  --output build/benchmark-report.json
```

`--dataset` を省略した場合も1,000件版を使用します。データとマニフェストは次の
コマンドで同じ内容に再生成できます。

```bash
python -m benchmarks.generate_synthetic
```

決定的ルールだけを評価する場合は `--disable-nlp` を付けます。CIで最低基準を
強制する場合は、例えば次のように指定できます。

```bash
python -m benchmarks.run --disable-nlp \
  --fail-under-recall 0.95 \
  --fail-under-core-zero-miss-rate 0.95 \
  --fail-under-hard-negative-pass-rate 1.00
```

CIではこのルール単体評価に加え、GiNZA込みの評価も実行します。GiNZA込みでは
Recall、通常文書の見逃しゼロ率、ハードネガティブ合格率をそれぞれ0.99以上に固定し、
既知ケースの回帰を検知します。これは合成データ上の回帰基準であり、実運用精度の
保証値ではありません。

## 出力指標

- 完全一致のPrecision、Recall、F1（micro、entity別）
- 同一entity typeでスパンが重なる場合の部分一致指標
- entity別macro F1
- PIIの見逃しがなかった文書の割合
- 1,000文字あたりの誤検出数
- 平均、中央値、p95、最大の推論時間
- 文字処理スループット、モデル読込時間、プロセスの最大RSS
- サンプル別の見逃しと誤検出
- Python・主要パッケージのバージョンとGiNZA利用状態
- 通常ケース `core` と誤検出評価 `hard_negative` の独立集計

PII用途では総合F1だけで合否を決めず、重要entityのRecall、文書単位の
見逃しゼロ率、誤検出内容を個別に確認してください。合成データを拡張するときは、
同じテンプレートの派生例をtrainとtestへ分散させず、テンプレート単位でsplitを
固定して評価リークを避けます。

DOCX、PPTX、PDFについては、検出精度とは別に、出力ファイルから受理済みPIIを
コピー、検索、テキスト抽出、内部XML・PDFオブジェクト解析で復元できないことを
評価する文書漏えいスイートを追加します。文書スイートの合格条件は復元率0%です。

## 名簿・役割分担表の回帰確認

`datasets/roster-fields.jsonl` は実在資料の氏名・地名を含めずに作成した22件の
合成データです。字間の空白、和暦併記、団体名・地区名の列挙、役職と業務名の除外、
氏名と地名が混在する列を確認します。正解スパンは27件です。
ルール開発用のため `split` は `dev` です。

```bash
python -m benchmarks.run \
  --dataset benchmarks/datasets/roster-fields.jsonl \
  --output build/benchmark-report-roster.json \
  --fail-under-recall 1.00 --fail-under-precision 1.00
```

利用者が確定したPDFの正解データは `data/annotation-review/` にローカル保存し、
Gitには含めません。`review.json` が `human_confirmed` であり、そのrevisionと
原本SHA-256が `gold.manifest.json` に一致することを確認してから評価します。
検出処理は正解ファイルを読み込まず、評価時のPII辞書も空です。

```bash
python -m benchmarks.run \
  --dataset data/annotation-review/r8-roles/gold.jsonl \
  --output build/benchmark-report-reviewed.json \
  --fail-under-recall 1.00 --fail-under-precision 1.00
```

同じ資料を使って改善した結果は、その資料での回帰確認です。未知の資料への
精度保証には使いません。地区名の補完は「丁目を含む地名の列＋班番号」の形式に
限定し、班番号そのものは地名に含めません。姓・名の形態素による補完にはGiNZAが
必要です。氏名・組織名をPII辞書へ自動登録する処理はありません。

## 処理変更時の速度比較

今回の実測結果は [PDF・検出処理の速度比較](results/2026-09-22-performance-comparison.md)
に記録しています。

検出・文書の読み取り・マスク処理を変更したときは、精度確認と合わせて
変更前後の速度を同じ端末・Python環境・入力・辞書条件で測定します。
別の日の単発計測だけで速度の増減を判断しません。

今回の確認済みPDFと既存1,000件を使う比較は、次のコマンドで実行できます。
`--baseline` には比較したい変更前のコミットを指定してください。

```bash
python -m benchmarks.compare_performance \
  --baseline af80f62 --trials 6 --pdf-repeats 5 \
  --output build/performance-comparison
```

比較対象の検出器・文書処理コードを保存し、別プロセスで前→後、後→前の順を
交互に実行します。計測中は負荷の高いテストやビルドを並行実行しません。
モデル読み込み、初回推論、ウォームアップ後のPDF検出、PDF読み取りから
マスクファイルの保存まで、既存1,000件の検出を分けて記録します。
PDF検出単体は両版とも現在の正常な読み取り結果を使います。
全体処理は各版の読み取りを使うため、変更前が文字化けする場合は出力品質が
異なる比較であることを明記します。HTTP通信・利用者の確認時間は含みません。

生の時間・精度・コードと入力のハッシュは出力先の `raw.json` に保存します。
この出力先には比較用コードとマスクPDFも生成されるため、`build/` 内で管理します。
代表値は複数プロセスの中央値とし、増減率、実時間差、ばらつき、精度の変化を
`results/` に記録します。モデル読み込みはOSのキャッシュを消去しない新規プロセスでの
値です。端末や他アプリの負荷による差があるため、共有CIでは絶対時間の合否基準を
設定せず、同じ環境での比較を使います。

## 確定済みJSONLの修正前後比較

保存したアプリのコードと現在のコードを、同じ評価器・同じデータで比較する。
比較対象には今回の確定版を使い、辞書は空、全分類有効とする。

```bash
.venv/bin/python -m benchmarks.compare_detection \
  --baseline-dir build/benchmark-generated-ja-218-rev77-20260923-075758/code-snapshot \
  --dataset data/annotation-review/generated-ja-218/exports/revision-77-review_complete/ja-dev.jsonl \
  --output build/detection-comparison \
  --trials 3 --warmup 10
```

出力先は既存結果を上書きしない新規ディレクトリを指定する。前後各3回以上を別プロセスで交互に実行し、各回10文書のウォームアップ後に全件を測る。モデル読込・入力読込・採点・保存は検出時間に含めない。PDFの読み取り・出力時間も別の測定対象となる。

`summary.json` に総検出時間・1文書の中央値・p95・モデル読込の前後比較と合否を出力する。初期改善目標は文字被覆率95%以上、人名・住所の全範囲被覆率それぞれ95%以上、検出文字の適合率98%以上、総検出時間2倍以内。未達の場合も結果を保存して終了コード1を返す。各回の精度と誤り一覧、アプリ・評価器のコードとハッシュ、入力ハッシュも保存し、測定中の変更や回ごとの検出結果の不一致はエラーにする。

被覆率は候補をすべて採用した場合の文字範囲の評価であり、出力PDFの安全性や未知の文書への性能を保証するものではない。郵便番号の「〒」を余分に覆う境界の違いと、氏名や住所の文字が残る見逃しは分けて確認する。`source_pii_coverage.fully_covered_documents` は全正解文字が検出範囲に含まれる文書数を示す。
