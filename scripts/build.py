"""Generate the Obsidian notes (People/, Institutions/, Experiments/, Groups/) from
data/seed.toml and the INSPIRE cache in data/raw/ (run scripts/fetch.py first).

Usage: python3 scripts/build.py [--prune]

--prune deletes notes that are no longer generated, but only if nothing was written
below GEN_END (otherwise they are reported and kept).

Each note has a generated part between GEN_START and GEN_END. Anything written below
GEN_END is hand-curated and kept across rebuilds. Notes for entities that drop out of
the graph are reported, not deleted.
"""
import json
import re
import sys
import tomllib
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
GEN_START = "<!-- generated:start (edits above GEN_END are overwritten by scripts/build.py) -->"
GEN_END = "<!-- generated:end -->"
INSPIRE = "https://inspirehep.net"

RANKS = {
    "UNDERGRADUATE": "Undergraduate", "MASTER": "Master's", "PHD": "PhD",
    "POSTDOC": "Postdoc", "JUNIOR": "Faculty (junior)", "SENIOR": "Faculty / senior",
    "STAFF": "Staff", "VISITOR": "Visitor", "OTHER": "Other",
}
GENERIC_INSTITUTIONS = {"University of California", "Unlisted", ""}
SKIP_INSTITUTIONS = {"Unlisted", "Unlisted Address"}
THIS_YEAR = date.today().year
ACTIVE_WINDOW = 3  # a person or project with no paper in this many years counts as inactive
ROLE_MAP = {"PI": "PI", "Postdoc": "Postdoc", "Postdoc (LLNL era)": "Postdoc",
            "Graduate student": "PhD student", "PhD student": "PhD student",
            "Undergraduate": "Undergrad", "Post-bacc": "Undergrad", "Group member": "Other member"}
ROLE_ORDER = ["PI", "Postdoc", "Graduate student", "PhD student", "Postdoc (LLNL era)",
              "Undergraduate", "Post-bacc", "Group member"]


def memberships(p):
    """All group memberships of a person: the main one (group/group_role/status) plus extra_groups."""
    out = []
    if p.get("group"):
        out.append({"group": p["group"], "role": p.get("group_role", "Group member"),
                    "status": p.get("status"), "destination": p.get("destination")})
    out += [{"destination": None, **m} for m in p.get("extra_groups", [])]
    return out


def is_current(p):
    return any(m["status"] == "current" for m in memberships(p))


# ------------------------------------------------------------------ loading

def load(kind, rid):
    path = RAW / kind / f"{rid}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return data.get("metadata", data) if isinstance(data, dict) else data


def ref_id(obj):
    ref = (obj or {}).get("record", {}).get("$ref", "")
    return int(ref.rsplit("/", 1)[-1]) if ref else None


def paper_year(p):
    for d in (p.get("preprint_date"), *(i.get("date") for i in p.get("imprints", [])),
              *(str(i.get("year", "")) for i in p.get("publication_info", [])),
              p.get("thesis_info", {}).get("date")):
        if d and d[:4].isdigit():
            return int(d[:4])
    return None


def safe(name):
    return re.sub(r'[\\/:*?"<>|#^\[\]]', "-", name).strip()


def link(name):
    return f"[[{safe(name)}]]" if safe(name) == name else f"[[{safe(name)}|{name}]]"


def span(a, b, current=False):
    a, b = str(a or "").strip(), str(b or "").strip()
    if current and not b:
        b = "present"
    if a and b:
        return a if a == b else f"{a}–{b}"
    return a or b or "dates unknown"


# ------------------------------------------------------------------ institutions

class Institutions:
    def __init__(self, names):
        self.names = {int(k): v for k, v in names.items()}
        self.notes = {}  # display name -> info

    def parent(self, iid):
        rec = load("institutions", iid)
        for rel in (rec or {}).get("related_records", []):
            if rel.get("relation") == "parent":
                return ref_id(rel)
        return None

    def resolve(self, iid):
        """Walk up to the top-level institution; returns (display name, top record)."""
        chain, cur = [], iid
        while cur and cur not in chain:
            chain.append(cur)
            cur = self.parent(cur)
        name = next((self.names[c] for c in chain if c in self.names), None)
        top = load("institutions", chain[-1]) or {}
        if not name:
            hier = [h.get("name", "") for h in top.get("institution_hierarchy", [])]
            name = hier[-1] if hier and hier[-1] not in GENERIC_INSTITUTIONS else ""
            name = name or re.sub(r"\s*\(main\)$", "", top.get("legacy_ICN") or str(chain[-1]))
        if name not in self.notes:
            addr = (top.get("addresses") or [{}])[0]
            self.notes[name] = {
                "name": name, "id": chain[-1],
                "city": ", ".join(addr.get("cities", [])),
                "country": addr.get("country", ""),
                "url": next((u["value"] for u in top.get("urls", []) if not u.get("description")), ""),
                "people": defaultdict(list),
            }
        return name


# ------------------------------------------------------------------ main build

def main():
    seed = tomllib.loads((ROOT / "data" / "seed.toml").read_text())
    persons = seed["person"]
    by_key = {p["key"]: p for p in persons}
    id_owner = {r["id"]: p["key"] for p in persons for r in p["inspire"]}
    groups = {g["key"]: g for g in seed.get("group", [])}
    manual_exps = {e["key"]: e for e in seed.get("experiment", [])}
    aliases = seed.get("experiment_aliases", {})
    canon = lambda n: aliases.get(n, n)
    ignored = lambda n: any(pat in n for pat in seed.get("ignore_collaborations", []))
    insts = Institutions(seed.get("institution_names", {}))
    experiments = {}  # name -> info

    def exp_note(name, rec=None, manual=None):
        name = canon(name)
        if name not in experiments:
            rec = rec or {}
            manual = manual or manual_exps.get(name, {})
            experiments[name] = {
                "name": name,
                "id": rec.get("control_number"),
                "long_name": (rec.get("long_name") or manual.get("long_name") or "").replace("\xa0", " "),
                "description": (rec.get("description") or manual.get("description") or "").replace("\xa0", " "),
                "url": next((u["value"] for u in rec.get("urls", [])), manual.get("url", "")),
                "classes": rec.get("inspire_classification", []),
                "started": (rec.get("date_started") or rec.get("date_approved") or "")[:4],
                "completed": (rec.get("date_completed") or "")[:4],
                "years": [],
                "people": {},
            }
        return experiments[name]

    def exp_from_id(eid):
        rec = load("experiments", eid)
        if not rec:
            return None
        name = (rec.get("experiment", {}).get("short_name") or rec.get("collaboration", {}).get("value")
                or rec.get("legacy_name") or str(eid))
        info = exp_note(name, rec)
        if rec.get("collaboration", {}).get("value"):
            collab_map[rec["collaboration"]["value"]] = info["name"]
        return info

    def exp_from_name(name):
        name = collab_map.get(name, canon(name))
        return experiments.get(name) or exp_note(name)

    collab_map = {}

    # -------- per-person data
    data = {}
    for p in persons:
        own = {r["id"] for r in p["inspire"]}
        others = set(id_owner) - own
        authors, papers = [], {}
        for r in p["inspire"]:
            a = load("authors", r["id"])
            if a:
                authors.append(a)
            for paper in load("author_papers", r["id"]) or []:
                ids = {ref_id(a) for a in paper.get("authors", [])}
                filt = r.get("filter", "all")
                if filt in ("group", "berkeley") and not (ids & others):
                    continue
                if filt == "berkeley":
                    affs = {ref_id(af) for a in paper.get("authors", []) if ref_id(a) == r["id"]
                            for af in a.get("affiliations", [])}
                    if "UC Berkeley" not in {insts.resolve(i) for i in affs if i and load("institutions", i)}:
                        continue
                papers[paper["control_number"]] = paper
        papers = list(papers.values())

        # career
        positions = []
        if p.get("positions"):
            for pos in p["positions"]:
                inst = insts.resolve(pos["inst"])
                positions.append((inst, pos["title"], pos.get("start"), pos.get("end"), not pos.get("end")))
        else:
            for a in authors:
                if a.get("stub"):
                    continue
                for pos in a.get("positions", []):
                    iid = ref_id(pos)
                    inst = insts.resolve(iid) if iid and load("institutions", iid) else pos.get("institution", "?")
                    positions.append((inst, RANKS.get(pos.get("rank"), pos.get("rank") or ""),
                                      pos.get("start_date"), pos.get("end_date"), pos.get("current")))
        positions = list(dict.fromkeys(positions))

        # affiliations as they appear on the person's papers
        affil = defaultdict(list)
        exp_counts, coauth = Counter(), Counter()
        exp_years = defaultdict(list)
        theses = []
        for paper in papers:
            y = paper_year(paper)
            mine = [a for a in paper.get("authors", []) if ref_id(a) in own]
            for a in mine:
                for af in a.get("affiliations", []):
                    iid = ref_id(af)
                    if iid and load("institutions", iid):
                        inst = insts.resolve(iid)
                        if inst not in SKIP_INSTITUTIONS:
                            affil[inst].append(y)
            names = set()
            for e in paper.get("accelerator_experiments", []):
                eid = ref_id(e)
                info = exp_from_id(eid) if eid else None
                if info:
                    names.add(info["name"])
            for c in paper.get("collaborations", []):
                if c.get("value") and not ignored(c["value"]):
                    names.add(exp_from_name(c["value"])["name"])
            exp_counts.update(names)
            for n in names:
                exp_years[n].append(y)
            for a in paper.get("authors", []):
                k = id_owner.get(ref_id(a))
                if k and k != p["key"]:
                    coauth[k] += 1
            if "thesis" in paper.get("document_type", []) and mine:
                theses.append(paper)

        members = set()
        for a in authors:
            for m in a.get("project_membership", []):
                info = exp_from_id(ref_id(m)) if ref_id(m) else exp_from_name(m["name"])
                if info:
                    members.add(info["name"])
        for key in p.get("manual_experiments", []):
            members.add(exp_note(key, manual=manual_exps.get(key, {}))["name"])

        advisors = {}
        for a in authors:
            for adv in a.get("advisors", []):
                advisors[id_owner.get(ref_id(adv)) or adv["name"]] = adv.get("degree_type", "")
        for t in theses:
            for s in t.get("supervisors", []):
                advisors.setdefault(id_owner.get(ref_id(s)) or s["full_name"], "PhD")
        for k in p.get("supervisor_override", []):
            advisors.setdefault(k, "PhD")
        advisors.pop(p["key"], None)

        years = [y for y in map(paper_year, papers) if y]
        data[p["key"]] = dict(p=p, authors=authors, papers=papers, positions=positions,
                              affil=affil, exp_counts=exp_counts, exp_years=exp_years, members=members,
                              coauth=coauth, theses=theses, advisors=advisors,
                              first=min(years, default=None), last=max(years, default=None))

    # theses supervised by people in the graph, matched to students by INSPIRE id or surname
    def match_student(author):
        k = id_owner.get(ref_id(author))
        if k:
            return k
        last = author.get("full_name", "").split(",")[0].strip().lower()
        hits = [q["key"] for q in persons if q["name"].lower().endswith(" " + last)]
        return hits[0] if len(hits) == 1 else None

    for p in persons:
        for r in p["inspire"]:
            for thesis in load("supervised", r["id"]) or []:
                for a in thesis.get("authors", []):
                    k = match_student(a)
                    if k and k != p["key"]:
                        data[k]["advisors"].setdefault(p["key"], "PhD")
                        if all(t["control_number"] != thesis["control_number"] for t in data[k]["theses"]):
                            data[k]["theses"].append(thesis)

    # students (reverse of advisors, within the graph)
    students = defaultdict(list)
    for k, d in data.items():
        for adv in d["advisors"]:
            if adv in by_key:
                students[adv].append(k)

    # institution + experiment backlinks
    for k, d in data.items():
        name = d["p"]["name"]
        for inst, title, a, b, cur in d["positions"]:
            if inst in insts.notes:
                insts.notes[inst]["people"][name].append(f"{title} ({span(a, b, cur)})")
        for inst, years in d["affil"].items():
            ys = [y for y in years if y]
            insts.notes[inst]["people"][name].append(
                f"on {len(years)} paper{'s' * (len(years) != 1)} ({span(min(ys), max(ys)) if ys else 'n.d.'})")
        for e in set(d["exp_counts"]) | d["members"]:
            experiments[e]["people"][name] = (d["exp_counts"].get(e, 0), e in d["members"])
            experiments[e]["years"] += [y for y in d["exp_years"].get(e, []) if y]

    # -------- write notes
    written = defaultdict(set)
    for k, d in data.items():
        written["People"].add(write_note("People", d["p"]["name"], person_md(d, data, by_key, groups, students)))
    for name, info in insts.notes.items():
        if info["people"]:
            written["Places"].add(write_note("Places", name, institution_md(info)))
    for name, info in experiments.items():
        if info["people"]:
            written["Projects"].add(write_note("Projects", name, experiment_md(info)))
    for g in groups.values():
        written["Groups"].add(write_note("Groups", g["name"], group_md(g, data, by_key, insts)))
    write_note(".", "index", home_md(groups, written))
    export_graph(data, by_key, groups, insts, experiments, students)

    for folder, names in written.items():
        stale = [f for f in (ROOT / folder).glob("*.md") if f.stem not in names]
        kept = []
        for f in stale:
            hand = f.read_text().split(GEN_END, 1)[-1].strip()
            if "--prune" in sys.argv and hand in ("", "## Notes"):
                f.unlink()
            else:
                kept.append(f.stem)
        print(f"{folder}: {len(names)} notes" + (f" (stale, kept: {sorted(kept)})" if kept else ""))


# ------------------------------------------------------------------ note bodies

def frontmatter(**kw):
    lines = ["---"]
    for k, v in kw.items():
        if v in (None, "", []):
            continue
        if isinstance(v, list):
            lines.append(f"{k}:")
            lines += [f"  - {json.dumps(x, ensure_ascii=False)}" for x in v]
        else:
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines)


def paper_line(paper):
    title = re.sub(r"<[^>]+>", "", paper["titles"][0]["title"]).strip()
    y = paper_year(paper) or "n.d."
    arx = (paper.get("arxiv_eprints") or [{}])[0].get("value")
    extra = f", arXiv:{arx}" if arx else ""
    cites = paper.get("citation_count", 0)
    return f"- [{title}]({INSPIRE}/literature/{paper['control_number']}) ({y}{extra}; {cites} citation{'s' * (cites != 1)})"


def person_md(d, data, by_key, groups, students):
    p = d["p"]
    ms = memberships(p)
    name_of = lambda k: link(by_key[k]["name"]) if k in by_key else k
    status = ("current" if is_current(p) else "alumni") if ms else None
    out = [frontmatter(type="person", title=p["name"],
                       inspire=[f"{INSPIRE}/authors/{r['id']}" for r in p["inspire"]],
                       groups=[groups[m["group"]]["name"] for m in ms],
                       roles=[m["role"] for m in ms], status=status,
                       tags=["person", *[f"group/{m['group']}" for m in ms],
                             *([f"status/{status}"] if status else [])]),
           GEN_START, ""]
    for m in ms:
        when = "current" if m["status"] == "current" else "former"
        out.append(f"**{m['role']}** ({when}) in the {link(groups[m['group']]['name'])}.")
    for m in ms:
        if m.get("destination"):
            dst = m["destination"]
            out.append(f"**Went on to:** {dst['text']} ([source]({dst['source']})).")
    out.append("")

    if d["advisors"] or students.get(p["key"]):
        out.append("## Mentorship")
        for adv, kind in d["advisors"].items():
            label = {"phd": "PhD", "PhD": "PhD", "other": "", "": ""}.get(kind, kind)
            out.append(f"- Advisor{f' ({label})' if label else ''}: {name_of(adv)}")
        for s in sorted(students.get(p["key"], []), key=lambda k: by_key[k]["name"]):
            out.append(f"- Student: {name_of(s)}")
        out.append("")

    if d["theses"]:
        out.append("## Thesis")
        for t in d["theses"]:
            ti = t.get("thesis_info", {})
            out.append(paper_line(t) + f" {ti.get('degree_type', '')}".rstrip())
        out.append("")

    if d["positions"]:
        out.append("## Career")
        src = p.get("positions_source")
        out.append(f"*Source: {f'[{src}]({src})' if src else 'INSPIRE author profile'}.*")
        for inst, title, a, b, cur in d["positions"]:
            out.append(f"- {span(a, b, cur)}: {title or 'Position'}, {link(inst)}")
        out.append("")

    if d["affil"]:
        out.append("## Affiliations on papers")
        out.append("*Inferred from the affiliations listed on this person's papers.*")
        rows = sorted(d["affil"].items(), key=lambda kv: -max([y for y in kv[1] if y] or [0]))
        for inst, years in rows:
            ys = [y for y in years if y]
            out.append(f"- {link(inst)}: {len(years)} paper{'s' * (len(years) != 1)}"
                       f" ({span(min(ys), max(ys)) if ys else 'n.d.'})")
        out.append("")

    exps = set(d["exp_counts"]) | d["members"]
    if exps:
        out.append("## Experiments")
        for e in sorted(exps, key=lambda e: (-d["exp_counts"].get(e, 0), e)):
            n = d["exp_counts"].get(e, 0)
            bits = [f"{n} paper{'s' * (n != 1)}"] if n else []
            if e in d["members"]:
                bits.append("listed member")
            out.append(f"- {link(e)} ({', '.join(bits)})")
        out.append("")

    if d["coauth"]:
        out.append("## Coauthors in this graph")
        for k, n in d["coauth"].most_common():
            out.append(f"- {name_of(k)} ({n})")
        out.append("")

    papers = d["papers"]
    if papers:
        cites = sum(x.get("citation_count", 0) for x in papers)
        out.append("## Publications")
        out.append(f"{len(papers)} records on INSPIRE, {cites} citations in total. "
                   + " · ".join(f"[INSPIRE profile]({INSPIRE}/authors/{r['id']})" for r in p["inspire"]))
        out.append("")
        out.append("**Most cited**")
        top = sorted(papers, key=lambda x: -x.get("citation_count", 0))[:5]
        out += [paper_line(x) for x in top]
        recent = [x for x in sorted(papers, key=lambda x: -(paper_year(x) or 0)) if x not in top][:5]
        if recent:
            out += ["", "**Most recent**"] + [paper_line(x) for x in recent]
        out.append("")
    return "\n".join(out)


def institution_md(info):
    out = [frontmatter(type="place", title=info["name"], inspire=f"{INSPIRE}/institutions/{info['id']}",
                       city=info["city"], country=info["country"], url=info["url"], tags=["place"]),
           GEN_START, ""]
    loc = ", ".join(x for x in (info["city"], info["country"]) if x)
    if loc:
        out.append(f"{loc}.")
    links = [f"[INSPIRE]({INSPIRE}/institutions/{info['id']})"] + ([f"[Website]({info['url']})"] if info["url"] else [])
    out += [" · ".join(links), "", "## People in this graph"]
    for name in sorted(info["people"]):
        out.append(f"- {link(name)}: " + "; ".join(info["people"][name]))
    out.append("")
    return "\n".join(out)


def experiment_md(info):
    out = [frontmatter(type="project", title=info["name"], long_name=info["long_name"],
                       inspire=f"{INSPIRE}/experiments/{info['id']}" if info["id"] else None,
                       url=info["url"], tags=["project"]),
           GEN_START, ""]
    if info["long_name"] and info["long_name"] != info["name"]:
        out += [f"*{info['long_name']}*", ""]
    if info["description"]:
        out += [info["description"], ""]
    links = ([f"[INSPIRE]({INSPIRE}/experiments/{info['id']})"] if info["id"] else []) + \
            ([f"[Website]({info['url']})"] if info["url"] else [])
    if links:
        out += [" · ".join(links), ""]
    if info["classes"]:
        out += ["Classification: " + "; ".join(c.replace("|", " › ") for c in info["classes"]), ""]
    out.append("## People in this graph")
    for name, (n, member) in sorted(info["people"].items(), key=lambda kv: (-kv[1][0], kv[0])):
        bits = [f"{n} paper{'s' * (n != 1)}"] if n else []
        if member:
            bits.append("listed member")
        out.append(f"- {link(name)} ({', '.join(bits)})")
    out.append("")
    return "\n".join(out)


def group_md(g, data, by_key, insts):
    out = [frontmatter(type="group", title=g["name"], url=g["url"], tags=["group"]),
           GEN_START, "", f"*{g['long_name']}*", "", g["description"], "",
           f"[Group website]({g['url']}) · Based at {link(insts.resolve(g['institution']))}", ""]
    members = [(d["p"], m) for d in data.values() for m in memberships(d["p"]) if m["group"] == g["key"]]
    roster = g.get("roster_only", [])
    for status, title in (("current", "Current members"), ("alumni", "Alumni")):
        out.append(f"## {title}")
        ms = [(p, m) for p, m in members if m["status"] == status]
        ms.sort(key=lambda pm: (ROLE_ORDER.index(pm[1]["role"]) if pm[1]["role"] in ROLE_ORDER else 99, pm[0]["name"]))
        for p, m in ms:
            dst = f" → {m['destination']['text']}" if m.get("destination") else ""
            out.append(f"- {link(p['name'])}: {m['role']}{dst}")
        rs = [r for r in roster if r["status"] == status]
        if rs:
            out += ["", "*Also listed on the group website (no INSPIRE publications, so no note):*"]
            out += [f"- {r['name']}: {r['role']}" for r in rs]
        out.append("")
    out.append(f"*Roster source: [{g['url']}]({g['url']}), retrieved {date.today().isoformat()}.*")
    out.append("")
    return "\n".join(out)


def home_md(groups, written):
    out = [frontmatter(type="index", title="Dark Matter Community Map", tags=["index"]), GEN_START, "",
           '<iframe class="explorer-frame" src="static/explorer/index.html" title="Community graph explorer" loading="lazy"></iframe>',
           "",
           '<p class="explorer-links">People, places and projects in dark matter research, from '
           '<a href="https://inspirehep.net">INSPIRE-HEP</a> and a hand-curated seed list · '
           '<a href="static/explorer/index.html" target="_blank">Open the graph full screen ↗</a> · '
           '<a href="https://github.com/msekatchev/dm-community/issues">Request a correction</a></p>',
           "", "## Groups"]
    out += [f"- {link(g['name'])}" for g in groups.values()]
    out += ["", f"**{len(written['People'])}** people · **{len(written['Places'])}** places · "
            f"**{len(written['Projects'])}** projects", "",
            "## Projects"]
    out += [f"- {link(n)}" for n in sorted(written["Projects"], key=str.lower)]
    out.append("")
    return "\n".join(out)


# ------------------------------------------------------------------ graph data for the explorer

def export_graph(data, by_key, groups, insts, experiments, students):
    """Write site/quartz/static/explorer/graph.json: typed nodes with activity years, and typed links."""
    nodes, links = [], []
    yr = lambda ys: (min(ys), max(ys)) if ys else (None, None)

    for k, d in data.items():
        p = d["p"]
        ms = memberships(p)
        main = next((m for m in ms if m["status"] == "current"), ms[0] if ms else None)
        active = is_current(p) or (d["last"] or 0) >= THIS_YEAR - ACTIVE_WINDOW
        nodes.append({
            "id": f"person:{k}", "type": "person", "label": p["name"], "file": f"People/{safe(p['name'])}.md",
            "role": ROLE_MAP.get(main["role"], "Other member") if main else None,
            "status": ("current" if is_current(p) else "alumni") if ms else None,
            "groups": [m["group"] for m in ms],
            "memberships": [{"group": groups[m["group"]]["name"], "role": m["role"], "status": m["status"]} for m in ms],
            "first": d["first"], "last": d["last"],
            "inField": active and not p.get("left_field", False),
            "destination": next((m["destination"]["text"] for m in ms if m.get("destination")), None),
            "papers": len(d["papers"]),
            "inspire": f"{INSPIRE}/authors/{p['inspire'][0]['id']}",
        })
        for inst, years in d["affil"].items():
            a, b = yr([y for y in years if y])
            links.append({"source": f"person:{k}", "target": f"place:{inst}", "kind": "affiliation",
                          "first": a, "last": b, "weight": len(years)})
        for inst, title, a, b, cur in d["positions"]:
            if inst not in d["affil"]:
                links.append({"source": f"person:{k}", "target": f"place:{inst}", "kind": "affiliation",
                              "first": int(a) if a and a[:4].isdigit() else None,
                              "last": THIS_YEAR if cur else (int(b) if b and b[:4].isdigit() else None), "weight": 1})
        for e in set(d["exp_counts"]) | d["members"]:
            a, b = yr([y for y in d["exp_years"].get(e, []) if y])
            links.append({"source": f"person:{k}", "target": f"project:{e}", "kind": "project",
                          "first": a, "last": b, "weight": d["exp_counts"].get(e, 0)})
        for m in ms:
            links.append({"source": f"person:{k}", "target": f"group:{m['group']}", "kind": "member",
                          "first": d["first"], "last": THIS_YEAR if m["status"] == "current" else d["last"],
                          "weight": 1, "status": m["status"]})
        for s in students.get(k, []):
            links.append({"source": f"person:{k}", "target": f"person:{s}", "kind": "advisor",
                          "first": data[s]["first"], "last": data[s]["last"], "weight": 1})
        for other, n in d["coauth"].items():
            if k < other:
                links.append({"source": f"person:{k}", "target": f"person:{other}", "kind": "coauthor",
                              "first": None, "last": None, "weight": n})

    for name, info in insts.notes.items():
        if not info["people"]:
            continue
        ys = [l[e] for l in links if l["target"] == f"place:{name}" for e in ("first", "last") if l[e]]
        a, b = yr(ys)
        nodes.append({"id": f"place:{name}", "type": "place", "label": name, "file": f"Places/{safe(name)}.md",
                      "first": a, "last": b, "inField": (b or 0) >= THIS_YEAR - ACTIVE_WINDOW,
                      "location": ", ".join(x for x in (info["city"], info["country"]) if x)})

    for name, info in experiments.items():
        if not info["people"]:
            continue
        a, b = yr(info["years"])
        done = int(info["completed"]) if info["completed"][:4].isdigit() and info["completed"] != "9999" else None
        start = int(info["started"]) if info["started"].isdigit() else None
        manual = info["id"] is None
        completed = bool(done and done <= THIS_YEAR)
        recent = manual or info["completed"] == "9999" or (b or 0) >= THIS_YEAR - ACTIVE_WINDOW
        nodes.append({"id": f"project:{name}", "type": "project", "label": name, "file": f"Projects/{safe(name)}.md",
                      "longName": info["long_name"],
                      "first": min([y for y in (start, a) if y], default=None),
                      "last": THIS_YEAR if manual else (done if completed else b),
                      "completed": completed, "inField": recent and not completed,
                      "darkMatter": manual or any("Dark matter" in c for c in info["classes"])})

    for g in groups.values():
        nodes.append({"id": f"group:{g['key']}", "type": "group", "label": g["name"], "file": f"Groups/{safe(g['name'])}.md",
                      "first": None, "last": THIS_YEAR, "inField": True, "longName": g["long_name"]})
        links.append({"source": f"group:{g['key']}", "target": f"place:{insts.resolve(g['institution'])}",
                      "kind": "based", "first": None, "last": THIS_YEAR, "weight": 1})

    ids = {n["id"] for n in nodes}
    links = [l for l in links if l["source"] in ids and l["target"] in ids]
    out = ROOT / "site" / "quartz" / "static" / "explorer" / "graph.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated": date.today().isoformat(), "thisYear": THIS_YEAR,
                               "activeWindow": ACTIVE_WINDOW, "nodes": nodes, "links": links},
                              ensure_ascii=False, indent=1))
    print(f"graph.json: {len(nodes)} nodes, {len(links)} links")


# ------------------------------------------------------------------ file writing

def write_note(folder, name, body):
    path = ROOT / folder / f"{safe(name)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    hand = "\n## Notes\n\n"
    if path.exists():
        old = path.read_text()
        if GEN_END in old:
            hand = old.split(GEN_END, 1)[1]
    path.write_text(body.rstrip() + "\n" + GEN_END + hand)
    return safe(name)


if __name__ == "__main__":
    main()
