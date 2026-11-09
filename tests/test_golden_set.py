"""The retrieval corpus and the golden query set are consistent."""

import re
from collections import Counter
from pathlib import Path

from aip.evals.retrieval import load_golden, parse_front_matter

ROOT = Path(__file__).resolve().parents[1] / "evals" / "retrieval"
CORPUS = ROOT / "corpus"
GOLDEN = ROOT / "golden.yaml"


def _headings() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in sorted(CORPUS.glob("*.md")):
        _, body = parse_front_matter(path.read_text())
        out[path.name] = {m.strip() for m in re.findall(r"^#{1,6} (.+)$", body, re.M)}
    return out


def _labels() -> dict[str, str]:
    return {p.name: parse_front_matter(p.read_text())[0] for p in CORPUS.glob("*.md")}


def test_corpus_has_15_files_with_valid_front_matter():
    labels = _labels()
    assert len(labels) >= 15
    assert set(labels.values()) <= {"public", "team_lead"}
    assert {n for n, v in labels.items() if v == "team_lead"} == {
        "team-lead-refund-guide.md",
        "fraud-signals.md",
    }
    for path in CORPUS.glob("*.md"):
        words = len(path.read_text().split())
        assert 300 <= words <= 900, f"{path.name} has {words} words"


def test_golden_has_40_plus_queries():
    golden = load_golden(GOLDEN)
    kinds = Counter(q["kind"] for q in golden)
    assert len(golden) >= 40
    assert kinds["keyword"] >= 8 and kinds["paraphrase"] >= 8
    assert kinds["team_lead"] >= 6 and kinds["none"] >= 4 and kinds["plain"] >= 18
    assert len({q["id"] for q in golden}) == len(golden)
    assert GOLDEN.read_text().startswith("labels_reviewed_by: pending owner review")


def test_every_relevant_ref_exists_in_corpus():
    headings = _headings()
    for item in load_golden(GOLDEN):
        for ref in item["relevant"]:
            doc, _, heading = ref.partition("#")
            assert doc in headings, f"{item['id']}: unknown file {doc}"
            assert heading in headings[doc], f"{item['id']}: no heading {heading!r} in {doc}"
        if item["kind"] == "none":
            assert item["relevant"] == []


def test_team_lead_queries_point_to_team_lead_docs():
    labels = _labels()
    for item in load_golden(GOLDEN):
        files = {ref.split("#")[0] for ref in item["relevant"]}
        if item["kind"] == "team_lead":
            assert item["role"] == "team_lead" and files
            assert all(labels[f] == "team_lead" for f in files)
        else:
            assert all(labels[f] == "public" for f in files), f"{item['id']} points to a team lead file"
