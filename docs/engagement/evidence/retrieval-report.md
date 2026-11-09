# Evidence: Retrieval report: vector, keyword and hybrid search, tuning and a miss attribution
label: VERIFIED LOCALLY
date: 2026-11-09 10:01 UTC
code commit: 5579056e6ea287b277e803aa7f6d460a5adce7dc
working tree clean at run time: yes
machine: MacBook Pro (mid 2015), 8 logical CPUs, 16 GB RAM, Fedora 44, podman 5.8.7 (measured on one laptop)
command: scripts/pg-test.sh start; AIP_ALLOW_SCHEMA_RESET=1 aip eval retrieval --db-url $(scripts/pg-test.sh url) --embedder local_onnx --modes vector,keyword,hybrid --k 5 --json
notes: Also run with --rrf-k 10, 30, 60 and 100 (hybrid only) and scripts/attribute_miss.py q10 --embedder local_onnx --db-url <same url>. Raw runs are in retrieval-runs.json. Measured on one laptop, small corpus, labels not reviewed by the owner.
---
# Retrieval report: vector, keyword and hybrid search

All numbers were measured on one laptop. They show how this code behaves on a small corpus. They are not a benchmark.

## Setup

| Item | Value |
|---|---|
| Corpus | 15 policy files of the fictional customer OutdoorCo (two are team lead only), 353 to 419 words each, 5 703 words in total |
| Chunking | `chunk_text`, 500 characters per chunk, 50 characters overlap, heading kept at the top of each chunk |
| Embedder | `BAAI/bge-small-en-v1.5` through fastembed (quantized ONNX file), 384 dimensions, CPU only |
| Database | PostgreSQL 16.10 with pgvector 0.8.0, in a local podman container |
| Search | cosine distance on `embedding_vec` (HNSW index), `websearch_to_tsquery` on a generated `tsvector`, fusion with reciprocal rank fusion (k = 60) |
| Golden set | `evals/retrieval/golden.yaml`, 48 queries. 44 have a relevant section, 4 have none. Labels are binary: a hit is relevant when its chunk heading is one of the listed headings. |
| Labels | `labels_reviewed_by: pending owner review`. Nobody but the author has checked them. |
| Metric | recall@5 and MRR over the 44 queries with a relevant section. Latency is the time of the search call only, per query, p50 and p95 over 48 queries. |
| Machine | MacBook Pro (mid 2015), 8 logical CPUs, 16 GB RAM, Fedora 44, podman 5.8.7 |

## Result: three search modes (PostgreSQL, run 1)

| Mode | recall@5 | MRR | p50 ms | p95 ms | Queries that miss |
|---|---|---|---|---|---|
| vector | 0.9583 | 0.8614 | 2.04 | 2.37 | 3 (q05, q10, q11) |
| keyword | 0.4318 | 0.4205 | 1.18 | 1.53 | 25 |
| hybrid (RRF k = 60) | 0.9659 | 0.8557 | 3.00 | 3.17 | 2 (q10, q11) |

Run 2 gave the same recall and MRR. Latency differed by up to about 0.7 ms (vector p50 2.04 ms, then 1.38 ms). So differences below about 1 ms are noise.

What the table shows:

- Hybrid finds one more relevant section than vector alone (recall 0.9659 against 0.9583) but its MRR is slightly lower (0.8557 against 0.8614). Fusion adds a section and moves some first hits down.
- Keyword search alone is weak here (recall 0.4318). `websearch_to_tsquery` needs every query term in the chunk. Questions that use other words than the policy text find nothing. Fusion with vector search hides this weakness.
- The corpus is small. Three queries decide the difference between modes. Do not read more into it.

## Tuning: one parameter, RRF k

Only `--rrf-k` changed. The corpus, embedder, queries and k = 5 stayed the same.

| RRF k | recall@5 | MRR |
|---|---|---|
| 10 | 0.9659 | 0.8557 |
| 30 | 0.9659 | 0.8557 |
| 60 | 0.9659 | 0.8557 |
| 100 | 0.9659 | 0.8557 |

The four values give the same result on this golden set. With 5 results and two short lists, the order of the fused list does not depend on k here. The default 60 stays. A larger corpus or a longer candidate pool could show a difference.

## Miss attribution: query q10

Query: "What happens if a parcel shows up crushed?" (kind paraphrase). The relevant section is `shipping-damage.md#Damage reporting window`. The hybrid run misses it at k = 5.

Raw output of `scripts/attribute_miss.py q10 --embedder local_onnx`:

```
miss=q10
query q10 (paraphrase, role agent): What happens if a parcel shows up crushed?
relevant: shipping-damage.md#Damage reporting window
  parse: file found=True label=public
  chunk: 1 chunk(s) carry the heading
  filter: role agent may read label public: True
  vector rank: 6   keyword rank: None   hybrid (fused) rank: 6   k=5
  first stage that lost it: vector
relevant: shipping-damage.md#Photo evidence requirements
  parse: file found=True label=public
  chunk: 1 chunk(s) carry the heading
  filter: role agent may read label public: True
  vector rank: 5   keyword rank: None   hybrid (fused) rank: 5   k=5
  first stage that lost it: keyword
```

The second relevant section of q10, `Photo evidence requirements`, ranked 5th in vector search and was found. The script prints "keyword" for it because the keyword list lacks it. That line names a weak stage, not a miss.

First stage that lost the first section: **vector** (the embedding ranks it 6th, one place outside k = 5). The chunk exists, the label filter allows it, and the keyword list does not contain it because none of the query words appear in the section ("parcel" and "crushed" are not in the policy text). So the keyword stage could not rescue it and the fused rank stayed 6.

What would help: a larger candidate pool, a query rewrite, or a synonym list for shipping words. These changes were not tried.

## Limits

- Measured on one laptop with a corpus of 15 files. The numbers do not predict production behavior.
- The labels are not reviewed by the owner.
- The queries were written by the same author who wrote the corpus, so paraphrases may be easier than real user questions.
- Latency covers only the search call. It does not include query embedding time or network time.
- The run used PostgreSQL on the same machine, with no other traffic.
