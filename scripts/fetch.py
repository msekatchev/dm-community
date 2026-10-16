"""Download everything the graph needs from INSPIRE-HEP into data/raw/ (a local cache).

Usage: python3 scripts/fetch.py [--refresh]

Fetches, for every person in data/seed.toml:
  - their author record(s)
  - all literature on those records
  - every institution in their positions / paper affiliations, plus parent institutions
  - every experiment on their papers / project memberships
Files already cached are skipped unless --refresh is given.
"""
import json
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
API = "https://inspirehep.net/api"
REFRESH = "--refresh" in sys.argv

LIT_FIELDS = ",".join([
    "control_number", "titles", "preprint_date", "imprints", "publication_info",
    "document_type", "authors.full_name", "authors.record", "authors.affiliations",
    "collaborations", "accelerator_experiments", "thesis_info", "supervisors",
    "citation_count", "arxiv_eprints", "dois",
])

_last = 0.0


def get(url):
    """GET JSON, staying under INSPIRE's rate limit (15 requests / 5 s)."""
    global _last
    wait = 0.4 - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                _last = time.time()
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError(f"giving up on {url}")


def cached(kind, rid, url):
    path = RAW / kind / f"{rid}.json"
    if path.exists() and not REFRESH:
        return json.loads(path.read_text())
    data = get(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))
    return data


def ref_id(obj):
    ref = (obj or {}).get("record", {}).get("$ref", "")
    return int(ref.rsplit("/", 1)[-1]) if ref else None


def fetch_papers(author_id, kind="author_papers", field="authors"):
    """All literature on one author record, cached as data/raw/<kind>/<id>.json.
    With field="supervisors", the theses this author supervised."""
    path = RAW / kind / f"{author_id}.json"
    if path.exists() and not REFRESH:
        return json.loads(path.read_text())
    q = urllib.parse.quote(f"{field}.record.$ref:{author_id}")
    url = f"{API}/literature?q={q}&size=250&sort=mostrecent&fields={LIT_FIELDS}"
    hits = []
    while url:
        page = get(url)
        hits += [h["metadata"] for h in page["hits"]["hits"]]
        url = page.get("links", {}).get("next")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hits, ensure_ascii=False))
    return hits


def main():
    seed = tomllib.loads((ROOT / "data" / "seed.toml").read_text())
    inst_ids, exp_ids = set(), set()
    for g in seed.get("group", []):
        inst_ids.add(g["institution"])
    for p in seed["person"]:
        inst_ids |= {pos["inst"] for pos in p.get("positions", [])}
        for rec in p["inspire"]:
            aid = rec["id"]
            print(f"{p['name']}: author {aid}", flush=True)
            author = cached("authors", aid, f"{API}/authors/{aid}")["metadata"]
            inst_ids |= {ref_id(x) for x in author.get("positions", [])}
            exp_ids |= {ref_id(x) for x in author.get("project_membership", [])}
            fetch_papers(aid, "supervised", "supervisors")
            for paper in fetch_papers(aid):
                exp_ids |= {ref_id(x) for x in paper.get("accelerator_experiments", [])}
                for a in paper.get("authors", []):
                    if ref_id(a) == aid:
                        inst_ids |= {ref_id(x) for x in a.get("affiliations", [])}
                inst_ids |= {ref_id(x) for x in paper.get("thesis_info", {}).get("institutions", [])}

    # Institutions, walking up to parents so departments can be merged into their university.
    todo = {i for i in inst_ids if i}
    seen = set()
    while todo:
        iid = todo.pop()
        seen.add(iid)
        rec = cached("institutions", iid, f"{API}/institutions/{iid}")["metadata"]
        for rel in rec.get("related_records", []):
            if rel.get("relation") == "parent":
                pid = ref_id(rel)
                if pid and pid not in seen:
                    todo.add(pid)
    print(f"institutions: {len(seen)}")

    exp_ids.discard(None)
    for eid in sorted(exp_ids):
        cached("experiments", eid, f"{API}/experiments/{eid}")
    print(f"experiments: {len(exp_ids)}")


if __name__ == "__main__":
    main()
