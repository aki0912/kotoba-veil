"""Local editor for text-only PII annotations, independent of the detector."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from benchmarks.reannotation import (
    APP_LABELS, LABELS, ROOT, atomic_json, dataset_info, digest, export_dataset, unresolved, validate_entities,
)

STATIC = Path(__file__).parent / "review_static"


class SpanEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_type: str
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)
    text: str = Field(min_length=1, max_length=100000)


class RowEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    keep: bool
    human_reviewed: bool
    entities: list[SpanEdit] = Field(max_length=2000)
    review_note: str = Field(default="", max_length=10000)


class PolicyEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    accepted: bool


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    finalize: bool = False


class ReviewStore:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.RLock()
        draft = json.loads((root / "codex-draft.json").read_text())
        if draft["status"] != "codex_draft" or len(draft["rows"]) != draft["source_rows"]:
            raise ValueError("A complete Codex draft is required")
        dataset_info(draft)
        if not (root / "policy.md").is_file():
            raise ValueError("A dataset requires its own policy.md")
        snapshot = root / "texts-only.jsonl"
        if digest(snapshot.read_bytes()) != draft["source_snapshot_sha256"]:
            raise ValueError("Source snapshot hash mismatch")
        self.originals = {row["id"]: row for row in map(json.loads, snapshot.read_text().splitlines())}
        self.draft_hash = digest((root / "codex-draft.json").read_bytes())
        path = root / "review.json"
        if not path.exists():
            draft["codex_draft_sha256"] = self.draft_hash
            draft["history"] = []
            atomic_json(path, draft)
        self.state = json.loads(path.read_text())
        if self.state.get("dataset") != draft.get("dataset"):
            raise ValueError("Review dataset metadata differs from the original draft")
        if self.state["codex_draft_sha256"] != self.draft_hash:
            raise ValueError("Codex draft changed; existing human review was preserved")
        if len(self.state["rows"]) != len(self.originals) or len({r["id"] for r in self.state["rows"]}) != len(self.originals):
            raise ValueError("Review row inventory mismatch")
        for row in self.state["rows"]:
            original = self.originals[row["id"]]
            if (row["text"] != original["text"] or row["split"] != original["split"]
                    or row["text_sha256"] != digest(row["text"].encode())):
                raise ValueError("Review changed original text or split")
            validate_entities(row["text"], row["entities"])
        self.indices = {row["id"]: i for i, row in enumerate(self.state["rows"])}

    def check_revision(self, revision: int):
        if revision != self.state["revision"]:
            raise HTTPException(409, "別の画面で更新されています。再読み込みしてから修正してください。")

    def summary(self):
        rows = self.state["rows"]
        return {
            "revision": self.state["revision"], "status": self.state["status"],
            "source_rows": len(rows), "retained_rows": sum(r["keep"] for r in rows),
            "excluded_rows": sum(not r["keep"] for r in rows),
            "issue_rows": sum(bool(r["issues"]) for r in rows),
            "unresolved_rows": sum(unresolved(r) for r in rows),
            "human_reviewed_rows": sum(r["human_reviewed"] for r in rows),
            "policy_accepted": self.state["policy_accepted"],
            "labels": sorted(LABELS), "app_labels": sorted(APP_LABELS),
            "source_snapshot_sha256": self.state["source_snapshot_sha256"],
            "dataset": dataset_info(self.state),
        }

    def save(self, updated: dict):
        updated["revision"] += 1
        updated["status"] = "codex_draft"
        atomic_json(self.root / "review.json", updated)
        self.state = updated

    def update_row(self, uid: str, edit: RowEdit):
        with self.lock:
            self.check_revision(edit.revision)
            if uid not in self.indices:
                raise HTTPException(404, "文書がありません")
            updated = deepcopy(self.state)
            row = updated["rows"][self.indices[uid]]
            spans = validate_entities(row["text"], [span.model_dump() for span in edit.entities])
            if not edit.keep and spans:
                raise ValueError("除外する文書のタグは削除してください")
            if edit.keep and (not row["keep"] or not row["codex_reviewed"]) and not edit.human_reviewed:
                raise ValueError("除外文書を戻す場合は本文・タグを確認し、確認済みにしてください")
            updated["history"].append({"revision": self.state["revision"] + 1, "id": uid,
                                       "before": {k: row.get(k) for k in ("keep", "entities", "human_reviewed", "review_note")},
                                       "after": edit.model_dump(exclude={"revision"})})
            row.update(keep=edit.keep, entities=spans, human_reviewed=edit.human_reviewed,
                       review_note=edit.review_note, review_required=True)
            self.save(updated)
            return {"row": row, "summary": self.summary()}


def create_app(root: Path = ROOT) -> FastAPI:
    store = ReviewStore(root)
    app = FastAPI(title="日本語PII 正解データのレビュー")
    app.state.review_store = store

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            return JSONResponse({"detail": "ローカル接続のみ利用できます"}, 403)
        if request.method not in {"GET", "HEAD"}:
            expected = f"{request.url.scheme}://{request.url.netloc}"
            if request.headers.get("origin") != expected:
                return JSONResponse({"detail": "同じ画面から操作してください"}, 403)
            try:
                size = int(request.headers.get("content-length", "0"))
            except ValueError:
                size = 2_000_001
            if size > 2_000_000:
                return JSONResponse({"detail": "更新内容が大きすぎます"}, 413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.exception_handler(ValueError)
    async def invalid_input(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, 422)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/review.js")
    def javascript():
        return FileResponse(STATIC / "review.js", media_type="text/javascript")

    @app.get("/review.css")
    def stylesheet():
        return FileResponse(STATIC / "review.css", media_type="text/css")

    @app.get("/api/policy")
    def policy():
        path = root / "policy.md"
        return {"text": path.read_text()}

    @app.get("/api/state")
    def state():
        with store.lock:
            return store.summary()

    @app.get("/api/rows")
    def rows(view: str = "unresolved", q: str = "", offset: int = 0, limit: int = 40):
        if view not in {"unresolved", "issues", "retained", "excluded", "all", "reviewed"}:
            raise HTTPException(422, "表示条件が不正です")
        if offset < 0 or not 1 <= limit <= 100 or len(q) > 1000:
            raise HTTPException(422, "一覧条件が不正です")
        with store.lock:
            chosen = [row for row in store.state["rows"] if (
                view == "all" or (view == "unresolved" and unresolved(row))
                or (view == "issues" and row["issues"]) or (view == "retained" and row["keep"])
                or (view == "excluded" and not row["keep"]) or (view == "reviewed" and row["human_reviewed"])
            ) and (not q or q.casefold() in (row["id"] + row["text"] + " ".join(row["issues"])).casefold())]
            return {"total": len(chosen), "revision": store.state["revision"], "rows": [
                {k: row[k] for k in ("id", "split", "keep", "human_reviewed", "issues")} | {"preview": row["text"][:110]}
                for row in chosen[offset:offset + limit]]}

    @app.get("/api/rows/{uid}")
    def row(uid: str):
        with store.lock:
            if uid not in store.indices:
                raise HTTPException(404, "文書がありません")
            return {"row": store.state["rows"][store.indices[uid]], "revision": store.state["revision"]}

    @app.put("/api/rows/{uid}")
    def save_row(uid: str, edit: RowEdit):
        return store.update_row(uid, edit)

    @app.put("/api/policy")
    def save_policy(edit: PolicyEdit):
        with store.lock:
            store.check_revision(edit.revision)
            updated = deepcopy(store.state)
            updated["policy_accepted"] = edit.accepted
            updated["history"].append({"revision": edit.revision + 1, "policy_accepted": edit.accepted})
            store.save(updated)
            return store.summary()

    @app.post("/api/export")
    def export(edit: ExportRequest):
        with store.lock:
            store.check_revision(edit.revision)
            if edit.finalize:
                if not store.state["policy_accepted"] or store.summary()["unresolved_rows"]:
                    raise HTTPException(409, "方針の確認と、要確認文書のレビューを完了してください")
                if store.state["status"] != "review_complete":
                    updated = deepcopy(store.state)
                    updated["revision"] += 1
                    updated["status"] = "review_complete"
                    updated["history"].append({"revision": updated["revision"], "action": "review_complete"})
                    atomic_json(root / "review.json", updated)
                    store.state = updated
            manifest = export_dataset(store.state, root)
            folder = f"revision-{store.state['revision']}-{store.state['status']}"
            return {"manifest": manifest, "summary": store.summary(), "downloads": {
                name: f"/exports/{folder}/{name}" for name in (*manifest["outputs"], "manifest.json")}}

    @app.get("/exports/{folder}/{filename}")
    def download(folder: str, filename: str):
        import re
        if not re.fullmatch(r"revision-\d+-(codex_draft|review_complete)", folder) or filename not in {
            "ja-train.jsonl", "ja-validation.jsonl", "ja-dev.jsonl", "ja-test.jsonl", "manifest.json"
        }:
            raise HTTPException(404)
        path = root / "exports" / folder / filename
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, filename=filename)

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--port", type=int, default=8013)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(args.root), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
