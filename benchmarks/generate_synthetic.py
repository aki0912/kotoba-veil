from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


SEED = 20260801
GENERATOR_VERSION = "1.0.0"
ENTITY_TYPES = (
    "PERSON",
    "ORGANIZATION",
    "LOCATION",
    "ADDRESS",
    "PHONE_NUMBER",
    "POSTAL_CODE",
    "EMAIL_ADDRESS",
    "PERSONAL_ID",
    "DRIVER_LICENSE",
    "BANK_ACCOUNT",
    "CREDIT_CARD",
    "DATE_TIME",
    "URL",
    "IP_ADDRESS",
    "CUSTOM",
)


@dataclass
class AnnotatedText:
    text: str = ""
    entities: list[dict[str, object]] = field(default_factory=list)
    dictionary_terms: list[dict[str, str]] = field(default_factory=list)

    def literal(self, value: str) -> None:
        self.text += value

    def entity(self, entity_type: str, value: str) -> None:
        start = len(self.text)
        self.text += value
        self.entities.append(
            {
                "entity_type": entity_type,
                "start": start,
                "end": len(self.text),
                "text": value,
            }
        )

    def merge(self, other: "AnnotatedText") -> None:
        offset = len(self.text)
        self.text += other.text
        for entity in other.entities:
            copied = dict(entity)
            copied["start"] = int(copied["start"]) + offset
            copied["end"] = int(copied["end"]) + offset
            self.entities.append(copied)
        self.dictionary_terms.extend(other.dictionary_terms)


def _full_width(value: str) -> str:
    table = str.maketrans("0123456789-", "０１２３４５６７８９－")
    return value.translate(table)


def _luhn_valid(digits: str) -> bool:
    checksum = 0
    parity = len(digits) % 2
    for index, character in enumerate(digits):
        value = int(character)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        checksum += value
    return checksum % 10 == 0


def _test_card_number(index: int) -> str:
    partial = f"411111{index % 1_000_000_000:09d}"
    for check_digit in range(10):
        candidate = partial + str(check_digit)
        if _luhn_valid(candidate):
            return candidate
    raise AssertionError("could not create Luhn-valid test number")


def _clause(entity_type: str, index: int, rng: random.Random) -> AnnotatedText:
    item = AnnotatedText()
    names = (
        "山田太郎",
        "佐藤花子",
        "鈴木一郎",
        "高橋美咲",
        "伊藤健太",
        "やまだたろう",
        "サトウハナコ",
        "Taro Yamada",
        "Hanako Sato",
        "髙橋﨑子",
    )
    locations = (
        "東京",
        "大阪市",
        "札幌",
        "福岡県",
        "横浜市",
        "京都",
        "仙台市",
        "名古屋",
        "神戸市",
        "那覇市",
    )
    contexts = ("登録値は", "申請書には", "確認対象は", "記録された値は")
    item.literal(rng.choice(contexts))

    if entity_type == "PERSON":
        item.entity(entity_type, names[index % len(names)])
        item.literal("さんです。")
    elif entity_type == "ORGANIZATION":
        organizations = (
            "株式会社青空",
            "合同会社ことのは",
            "一般社団法人みらい",
            "東都データ研究所",
            "医療法人さくら会",
        )
        item.entity(entity_type, f"{organizations[index % len(organizations)]}{index % 17 + 1}")
        item.literal("です。")
    elif entity_type == "LOCATION":
        item.entity(entity_type, locations[index % len(locations)])
        item.literal("です。")
    elif entity_type == "ADDRESS":
        bases = (
            "東京都千代田区霞が関",
            "大阪府大阪市北区梅田",
            "北海道札幌市中央区北一条",
            "神奈川県横浜市西区みなとみらい",
            "京都府京都市中京区御池通",
        )
        number = index % 20 + 1
        endings = (
            f"{number}丁目{index % 30 + 1}番{index % 9 + 1}号",
            f"{number}-{index % 30 + 1}-{index % 9 + 1}",
            f"{_full_width(str(number))}丁目{_full_width(str(index % 30 + 1))}番地",
        )
        item.entity(entity_type, bases[index % len(bases)] + endings[index % len(endings)])
        item.literal("です。")
    elif entity_type == "PHONE_NUMBER":
        prefix = ("070", "080", "090")[index % 3]
        middle = f"{1000 + index * 37 % 9000:04d}"
        last = f"{1000 + index * 53 % 9000:04d}"
        formats = (
            f"{prefix}-{middle}-{last}",
            f"{prefix} {middle} {last}",
            _full_width(f"{prefix}-{middle}-{last}"),
            f"+81-{prefix[1:]}-{middle}-{last}",
            f"({prefix}){middle}-{last}",
        )
        item.entity(entity_type, formats[index % len(formats)])
        item.literal("です。")
    elif entity_type == "POSTAL_CODE":
        first = f"{100 + index * 7 % 900:03d}"
        second = f"{1000 + index * 31 % 9000:04d}"
        formats = (
            f"〒{first}-{second}",
            f"{first}-{second}",
            f"〒 {first}{second}",
            _full_width(f"〒{first}-{second}"),
        )
        item.entity(entity_type, formats[index % len(formats)])
        item.literal("です。")
    elif entity_type == "EMAIL_ADDRESS":
        domains = ("example.jp", "example.com", "sub.example.org")
        locals_ = (
            f"user{index}",
            f"taro.yamada{index}",
            f"contact+case{index}",
            f"INFO{index}",
        )
        item.entity(entity_type, f"{locals_[index % len(locals_)]}@{domains[index % len(domains)]}")
        item.literal("です。")
    elif entity_type == "PERSONAL_ID":
        item.literal("個人番号として")
        item.entity(entity_type, f"{100_000_000_000 + index * 7919:012d}")
        item.literal("が記載されています。")
    elif entity_type == "DRIVER_LICENSE":
        item.literal("免許証番号として")
        item.entity(entity_type, f"{200_000_000_000 + index * 6151:012d}")
        item.literal("が記載されています。")
    elif entity_type == "BANK_ACCOUNT":
        item.literal(rng.choice(("普通口座は", "当座口座は", "振込先口座は")))
        width = 8 if index % 5 == 0 else 7
        item.entity(entity_type, f"{1_000_000 + index * 101:0{width}d}")
        item.literal("です。")
    elif entity_type == "CREDIT_CARD":
        digits = _test_card_number(index)
        formats = (
            digits,
            " ".join(digits[position : position + 4] for position in range(0, 16, 4)),
            "-".join(digits[position : position + 4] for position in range(0, 16, 4)),
        )
        item.literal("カード番号は")
        item.entity(entity_type, formats[index % len(formats)])
        item.literal("です。")
    elif entity_type == "DATE_TIME":
        year = 2020 + index % 10
        month = index % 12 + 1
        day = index % 28 + 1
        formats = (
            f"{year}年{month}月{day}日",
            f"{year}/{month:02d}/{day:02d}",
            f"{year}-{month:02d}-{day:02d}",
            f"{month}月{day}日",
        )
        item.entity(entity_type, formats[index % len(formats)])
        item.literal("です。")
    elif entity_type == "URL":
        domains = ("example.jp", "example.com", "example.org")
        formats = (
            f"https://{domains[index % 3]}/case/{index}",
            f"http://sub.{domains[index % 3]}/p?q={index}",
            f"https://{domains[index % 3]}/資料/{index}",
        )
        item.entity(entity_type, formats[index % len(formats)])
        item.literal("です。")
    elif entity_type == "IP_ADDRESS":
        ipv4_blocks = ("192.0.2", "198.51.100", "203.0.113")
        formats = (
            f"{ipv4_blocks[index % 3]}.{index % 254 + 1}",
            f"2001:db8::{index % 65535 + 1:x}",
        )
        item.entity(entity_type, formats[index % len(formats)])
        item.literal("です。")
    elif entity_type == "CUSTOM":
        term = f"月影プロジェクト{index % 50 + 1}"
        item.entity(entity_type, term)
        item.dictionary_terms.append({"term": term, "entity_type": "CUSTOM"})
        item.literal("です。")
    else:
        raise ValueError(f"unsupported entity type: {entity_type}")
    return item


def _sample(
    sample_id: str,
    body: AnnotatedText,
    *,
    template_id: str,
    tags: list[str],
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": sample_id,
        "language": "ja",
        "split": "test",
        "source": "synthetic",
        "generator_version": GENERATOR_VERSION,
        "template_id": template_id,
        "text": body.text,
        "entities": body.entities,
        "tags": tags,
    }
    if body.dictionary_terms:
        result["dictionary_terms"] = body.dictionary_terms
    return result


def _negative(index: int) -> tuple[str, str]:
    order_12 = f"{300_000_000_000 + index * 3571:012d}"
    product_7 = f"{4_000_000 + index * 97:07d}"
    valid_card = _test_card_number(5000 + index)
    invalid_card = valid_card[:-1] + str((int(valid_card[-1]) + 1) % 10)
    variants = (
        ("order-number", f"ケース{index + 1}：注文番号{order_12}を照合しました。"),
        ("product-code", f"ケース{index + 1}：商品コード{product_7}は在庫管理用です。"),
        ("invalid-card", f"ケース{index + 1}：無効なカード番号{invalid_card}を拒否しました。"),
        ("invalid-email", f"ケース{index + 1}：入力値user{index}@localhostはメールではありません。"),
        ("invalid-ip", f"ケース{index + 1}：入力値999.999.999.{index % 999}はIPではありません。"),
        ("phone-shaped-product", f"ケース{index + 1}：型番090-{1000 + index:04d}-{2000 + index:04d}を出荷しました。"),
        ("plain", f"ケース{index + 1}：青い封筒を受付窓口へ移動しました。"),
    )
    return variants[index % len(variants)]


def generate(seed: int = SEED) -> list[dict[str, object]]:
    rng = random.Random(seed)
    samples: list[dict[str, object]] = []

    for entity_type in ENTITY_TYPES:
        for index in range(50):
            body = AnnotatedText()
            body.literal(f"単独ケース{index + 1}：")
            body.merge(_clause(entity_type, index, rng))
            samples.append(
                _sample(
                    f"ja-{entity_type.lower()}-{index + 1:03d}",
                    body,
                    template_id=f"single-{entity_type.lower()}-{index % 5}",
                    tags=["single-entity", entity_type.lower()],
                )
            )

    for index in range(100):
        body = AnnotatedText()
        body.literal(f"複合ケース{index + 1}：")
        selected = [ENTITY_TYPES[(index * 3 + offset) % len(ENTITY_TYPES)] for offset in range(3)]
        for offset, entity_type in enumerate(selected):
            if offset:
                body.literal("／")
            body.merge(_clause(entity_type, 1000 + index * 3 + offset, rng))
        samples.append(
            _sample(
                f"ja-mixed-{index + 1:03d}",
                body,
                template_id=f"mixed-{index % 10}",
                tags=["multiple-entities", *[item.lower() for item in selected]],
            )
        )

    for index in range(150):
        template_id, text = _negative(index)
        body = AnnotatedText(text=text)
        samples.append(
            _sample(
                f"ja-negative-{index + 1:03d}",
                body,
                template_id=f"negative-{template_id}",
                tags=["negative", template_id],
            )
        )

    assert len(samples) == 1000
    assert len({str(item["id"]) for item in samples}) == len(samples)
    assert len({str(item["text"]) for item in samples}) == len(samples)
    return samples


def write_dataset(output: Path, manifest: Path, seed: int = SEED) -> None:
    samples = generate(seed)
    rendered = "".join(
        json.dumps(sample, ensure_ascii=False, separators=(",", ":")) + "\n"
        for sample in samples
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")

    entity_distribution: Counter[str] = Counter()
    tag_distribution: Counter[str] = Counter()
    for sample in samples:
        entity_distribution.update(
            str(entity["entity_type"]) for entity in sample["entities"]  # type: ignore[index]
        )
        tags = sample["tags"]
        assert isinstance(tags, list)
        if "negative" in tags:
            tag_distribution["negative"] += 1
        elif "multiple-entities" in tags:
            tag_distribution["multiple-entities"] += 1
        else:
            tag_distribution["single-entity"] += 1
    metadata = {
        "name": "kotoba-veil-japanese-pii-synthetic-v1",
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "sample_count": len(samples),
        "gold_entity_count": sum(entity_distribution.values()),
        "sample_distribution": dict(sorted(tag_distribution.items())),
        "entity_distribution": dict(sorted(entity_distribution.items())),
        "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "contains_real_pii": False,
        "license": "MIT",
    }
    manifest.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the Japanese synthetic PII benchmark")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmarks/datasets/synthetic-v1.jsonl"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("benchmarks/datasets/synthetic-v1.manifest.json"),
    )
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    write_dataset(args.output, args.manifest, args.seed)
    print(f"Wrote 1000 samples to {args.output}")


if __name__ == "__main__":
    main()

