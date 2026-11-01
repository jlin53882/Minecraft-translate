# Query plans

EXPLAIN QUERY PLAN on a read-only connection (`mode=ro`). Database: jar-derived ATM10 lang, 158,317 entries, existing schema indexes only; no index was added for this probe.

## Tier A structural (IN on key)

```sql
SELECT e.id, e.key, e.en_us, f.zh_tw, f.review_status FROM entry e LEFT JOIN effective f ON f.entry_id = e.id WHERE e.kind = ? AND e.mc_version = ? AND e.mod_id = ? AND e.key IN (?,?,?,?,?)
```

Plan:
- `SEARCH e USING INDEX idx_smart_review_context_key (kind=? AND mod_id=? AND key=? AND mc_version=?)`
- `SEARCH f USING INTEGER PRIMARY KEY (rowid=?) LEFT-JOIN`

- temp B-tree: no
- full-table scan: no

## Tier B lexical (LIKE per word, LIMIT 200)

```sql
SELECT e.id, e.key, e.en_us, f.zh_tw, f.review_status FROM entry e LEFT JOIN effective f ON f.entry_id = e.id WHERE e.kind = ? AND e.mc_version = ? AND e.mod_id = ? AND e.key LIKE ? ESCAPE '\' LIMIT 200
```

Plan:
- `SEARCH e USING INDEX idx_smart_review_glossary_order (kind=? AND mod_id=? AND mc_version=?)`
- `SEARCH f USING INTEGER PRIMARY KEY (rowid=?) LEFT-JOIN`

- temp B-tree: no
- full-table scan: no

## Target fetch by id

```sql
SELECT id, key, mod_id, mc_version, en_us FROM entry WHERE id = ?
```

Plan:
- `SEARCH entry USING INTEGER PRIMARY KEY (rowid=?)`

- temp B-tree: no
- full-table scan: no

