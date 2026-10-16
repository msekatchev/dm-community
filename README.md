# dm-community

A map of the dark matter research community: people, the groups they belong to, the institutions they've worked at, and the experiments they work on. It is an Obsidian vault built mostly from [INSPIRE-HEP](https://inspirehep.net) data, starting from a small hand-curated seed list.

The first slice covers Karl van Bibber's group at UC Berkeley (current members and alumni) and every experiment they connect to.

## Layout

| Folder | Contents |
|---|---|
| `People/` | One note per person: group role, mentorship, thesis, career, affiliations on papers, experiments, coauthors in the graph, key papers |
| `Groups/` | Research groups with full rosters |
| `Institutions/` | Universities and labs, with who in the graph worked there and when |
| `Experiments/` | Experiments and collaborations, with who in the graph is involved |
| `Home.md` | Entry point |
| `data/seed.toml` | The hand-curated input: who's in the graph, their INSPIRE IDs, group roles, corrections |
| `scripts/` | `fetch.py` (INSPIRE → `data/raw/` cache) and `build.py` (cache → notes) |

## Updating

```bash
python3 scripts/fetch.py            # downloads only what isn't cached yet; --refresh to re-download everything
python3 scripts/build.py --prune    # regenerates notes; --prune removes notes that dropped out, if they have no hand-written content
```

Each note has a generated section between `<!-- generated:start -->` and `<!-- generated:end -->`. Write your own notes **below** `generated:end`; rebuilds keep them.

To add someone, add a `[[person]]` entry to `data/seed.toml` with their INSPIRE author ID(s), then fetch and build.

## How the data is assembled, and its limits

- **Duplicate INSPIRE records.** Many people have several author records ("stubs"), and some records mix papers from different people with the same name. Each person in the seed lists all their record IDs, each with a filter:
  - `all` trusts the whole record.
  - `group` keeps only papers coauthored with someone else in the graph.
  - `berkeley` also requires a UC Berkeley affiliation on that paper.
- **Career** comes from curated INSPIRE profiles, or from a manual list in the seed when INSPIRE is out of date (e.g. Karl van Bibber's, from his department page). **Affiliations on papers** is always inferred from paper metadata, so it reflects where someone published from, not formal appointments.
- **Advisors/students** come from INSPIRE thesis records (`supervisors`), curated advisor fields, and seed overrides. Current students have no thesis yet, so they aren't linked as students until they graduate.
- **Experiments** come from INSPIRE experiment records, collaboration names on papers and profile memberships. Variants are merged through `experiment_aliases` in the seed (e.g. DMRadio-50L and DMRadio-m3 → DMRadio). Working groups and reviews are ignored.
- **Roster** comes from the group's public website, retrieved on the date shown on the group page.

## Scope and privacy

Only public, professional information goes here: names, affiliations, roles, theses, experiments and publications. Nothing personal. INSPIRE metadata is CC0. If this goes public, add a way for people to request corrections or removal.
