# Kotoba Veil benchmark

このフォルダは、PII検出精度を再現可能な方法で測定するための評価資産です。
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
