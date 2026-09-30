# Finding documents more accurately, reading the whole device, and learning over time: research report
*Generated: 2026-10-01 | Sources: 24 | Confidence: Medium (strong evidence for the retrieval techniques; weaker, blog-level evidence for CPU training times)*

## Executive summary

The biggest measured accuracy gains in 2025–26 come from three steps that stack. The app already has one of them (keyword + semantic search), and the other two, reranking and adding context to chunks, can run on this laptop without a GPU. A few cheaper gaps in the current code probably cost accuracy before any of that matters: no word stemming, file names left out of the semantic index, only 4 document formats, and results returned as text chunks instead of files.

"Read all the data on the device" is best split in two:
- **Finding files:** a fast file-name and metadata search over the whole drive, using the index Windows already maintains.
- **Reading files:** full-text reading of folders you opt into, with sensitive locations excluded by default.

Microsoft's Recall shows how hard automatic sensitive-data filtering is. Testers found it still captured credit card details with its filter turned on.

"Train himself" should not mean fine-tuning the chat model on your files. A peer-reviewed comparison found retrieval consistently beats that kind of fine-tuning for new facts, and your files change daily. Learning belongs in the search layer: remember which results you open, boost them next time, and grow the eval set from real questions. Adapting the embedding model to your documents is possible later, but it needs a GPU-class budget.

## 1. Where accuracy is lost today (from reading this codebase)

| Gap | Where | Why it matters |
|---|---|---|
| No stemming in keyword search: "invoices" won't match "invoice" | `backend/app/db/sqlite_fts.py:29` uses FTS5's default `unicode61` tokenizer | SQLite ships a `porter` wrapper that stems tokens: `tokenize = 'porter unicode61'` ([SQLite FTS5](https://www.sqlite.org/fts5.html)) |
| Semantic search embeds only chunk text; file name, folder and title aren't in it | `backend/app/core/indexer.py:90` embeds `c.text` | A query like "my 2024 resume" can't match a chunk whose body never says "resume". Keyword search does match the path, because `path` is an indexed FTS5 column |
| Few candidates, no reranking | `backend/app/core/search/hybrid.py:37,47` fetch `limit+2` (8) from each side and fuse to 6 | Rerankers need a larger pool to reorder (Section 2) |
| Only `.pdf .docx .pptx .xlsx` (+ images with OCR) | `backend/app/core/parsers/__init__.py:8` | Everything in `.txt`, `.md`, `.csv`, `.html`, `.eml`/`.msg`, `.rtf`, `.odt` is invisible |
| Results are chunks, not files | `hybrid_search` returns chunks | "Find the document about X" wants a ranked list of files: group chunks by file and rank by the best chunk |
| Embedder `granite-embedding:278m` reads at most 512 tokens and scores lowest of the models compared | `backend/app/config.py:22` | Scored 48.2 on MTEB English retrieval ([d-central](https://d-central.tech/local-embedding-models/)); a reviewer calls it chosen for "licensing and enterprise support posture rather than benchmark wins" ([morphllm](https://www.morphllm.com/ollama-embedding-models)) |

*The table records what the code does. That these gaps cost accuracy is a hypothesis; measure each one with the eval harness (Section 5, phase 0).*

## 2. Techniques with measured gains

### 2.1 Hybrid search + context on each chunk + reranking stack
Anthropic's tests, measured as the share of queries where the right chunk isn't in the top 20 ([Anthropic](https://www.anthropic.com/engineering/contextual-retrieval)):
- Baseline: 5.7% of queries fail.
- **Contextual embeddings:** a short explanation of where the chunk sits in its document is added to each chunk before embedding. Failures drop to 3.7% (−35%).
- **Plus contextual BM25**, the same context added to the keyword index: 2.9% (−49%).
- **Plus reranking:** 1.9% (−67%).

The Claude cookbook reports Pass@10 rising from 87% to 92% with contextual embeddings, and to about 95% with reranking ([Claude cookbook](https://platform.claude.com/cookbook/capabilities-contextual-embeddings-guide)).

**Caveat for this laptop (inference, not measured):** Anthropic generated each chunk's context with a hosted model. Running `qwen3-vl:2b` on a CPU over every chunk of a whole drive would take hours to days. A cheap version is to prepend deterministic context to each chunk before indexing: `File: <name> · Folder: <parent> · Title/heading: <first heading>`. This variant isn't benchmarked in the sources, so measure it. LLM-written context can be kept for small, high-value folders and run overnight.

### 2.2 Rerankers: large gains, but the retriever sets the ceiling
AIMultiple benchmark: 300 queries over about 145k reviews, top-100 candidates from `multilingual-e5-base`, run on an H100 GPU ([AIMultiple](https://aimultiple.com/rerankers)).
- **Best result:** a reranker lifted Hit@1 (right answer ranked first) from 62.67% to 83.00%.
- **Size isn't what matters:** `gte-reranker-modernbert-base` (149M parameters) tied the 1.2B `nemotron-rerank-1b`. `Qwen3-Reranker-4B` came fourth at 77.67% and took over a second per query.
- **A tiny model barely helped:** `mxbai-rerank-xsmall` (70M) scored 64.67%, within noise. The authors conclude "A reranker is not automatically beneficial. Test it on your actual data."
- **The retriever caps the result:** all top rerankers plateaued at about 88% Hit@10, because the retriever never surfaced the rest. Taking 250 candidates instead of 100 didn't help.

**Published numbers conflict.** One leaderboard gives `bge-reranker-v2-m3` about 71.5 BEIR nDCG@10 ([Presenc](https://presenc.ai/research/best-open-weight-reranker-models-2026)); another reports 51.8 ([Anas Rabhi](https://ianas.fr/en/blog/2026/06/07/reranker-comparatif-cohere-bge-jina-voyage/)). Treat leaderboard numbers as rough and decide with your own eval.

**CPU fit (single source, unverified):** `bge-reranker-v2-m3` is "usable for batches of fewer than 50 pairs" on a CPU ([Anas Rabhi](https://ianas.fr/en/blog/2026/06/07/reranker-comparatif-cohere-bge-jina-voyage/)). So rerank the top 20–30 candidates, not 100.

**How it could run here.** The backend avoids torch (`requirements.txt`: "embeddings come from granite-embedding:278m via Ollama, so no torch").
- **Not through Ollama:** Ollama has no native rerank endpoint, according to community write-ups and a third-party adapter built to fill the gap ([adapter](https://github.com/jtianling/dify-ollama-rerank-adapter), [Glukhov](https://www.glukhov.org/rag/embeddings/qwen3-embedding-qwen3-reranker-on-ollama/)). Check this against current Ollama docs before relying on it.
- **Through ONNX:** FastEmbed runs cross-encoders on ONNX Runtime, CPU-only, without torch ([Qdrant](https://qdrant.tech/documentation/fastembed/fastembed-rerankers/)). Its supported models ([list](https://qdrant.github.io/fastembed/examples/Supported_Models/)):

| Model | Size | License |
|---|---|---|
| `Xenova/ms-marco-MiniLM-L-6-v2` | 0.08 GB | Apache-2.0 |
| `Xenova/ms-marco-MiniLM-L-12-v2` | 0.12 GB | Apache-2.0 |
| `jinaai/jina-reranker-v1-tiny-en` | 0.13 GB | Apache-2.0 |
| `jinaai/jina-reranker-v1-turbo-en` | 0.15 GB | Apache-2.0 |
| `BAAI/bge-reranker-base` | 1.04 GB | MIT |
| `jinaai/jina-reranker-v2-base-multilingual` | 1.11 GB | CC-BY-NC-4.0 (non-commercial) |

The app's optional OCR already depends on ONNX Runtime, so this adds no new kind of runtime.

### 2.3 Embedding model choice
Scores come from different benchmarks, and one source warns that "MTEB scores are only comparable WITHIN the same benchmark" ([d-central](https://d-central.tech/local-embedding-models/)).

| Model (Ollama tag) | Download | Context | Reported score | Notes |
|---|---|---|---|---|
| `qwen3-embedding:0.6b` | 639 MB | 32K | 70.7 MTEB eng v2; 64.33 multilingual | Apache-2.0 ([Ollama](https://ollama.com/library/qwen3-embedding), [d-central](https://d-central.tech/local-embedding-models/), [morphllm](https://www.morphllm.com/ollama-embedding-models)) |
| `embeddinggemma` (300M) | ~622 MB | 2K | 69.67 MTEB eng v2 | Needs `task:`/`title:` prefixes ("skip the prefixes and you leave quality on the table"); Gemma license ([SurrealDB](https://surrealdb.com/blog/embedding-models-comparison)) |
| `nomic-embed-text` v1.5 | 274 MB | 8K | 62.28 MTEB eng (older 56-task version) | Apache-2.0; Ollama's default context is 2K, raise `num_ctx` ([d-central](https://d-central.tech/local-embedding-models/)) |
| `granite-embedding:278m` (current) | ~0.6 GB | 512 | 48.2 MTEB eng retrieval mean | 12 languages ([d-central](https://d-central.tech/local-embedding-models/)) |

The two leading candidates are `qwen3-embedding:0.6b` and `embeddinggemma`. Switching models means re-embedding everything; the schema-upgrade rebuild from issue V3 already does that. Also check RAM use against the 16 GB budget.

### 2.4 Keyword search: stemming and field weights
- Enable stemming with `tokenize = 'porter unicode61'`.
- Weight columns with `bm25(chunks, <path weight>, …)` so a match in the file name counts more than a match in the body ([SQLite FTS5](https://www.sqlite.org/fts5.html)).
- The `trigram` tokenizer adds substring and `LIKE` matching, useful for partial file names. It's a separate index and costs extra disk.

## 3. Reading the whole device

### 3.1 Split "finding files" from "reading files"
- **Discovery through the Windows Search index.** The index Windows already maintains can be queried with SQL over OLE DB, for example `SELECT … FROM SystemIndex WHERE …` ([Microsoft Learn: querying the index](https://learn.microsoft.com/en-us/windows/win32/search/-search-3x-wds-qryidx-overview), [Windows Search SQL](https://learn.microsoft.com/en-us/windows/win32/search/-search-sql-ovwofsearchquery)). It gives instant file-name, metadata and (where Windows indexed it) full-text hits across the drive, with no crawling by this app. *Inference:* it only knows locations Windows indexes, so results depend on the user's Windows Search settings.
- **File names only, through Everything.** Everything (voidtools) exposes near-instant file-name search to other programs through its SDK, but "Everything is required to be running" ([voidtools SDK](https://www.voidtools.com/support/everything/sdk/)). That's a third-party dependency, so make it optional at most.
- **Deep reading with the app's own index.** Text extraction, embedding and citations stay limited to folders you opt into. At whole-drive scale, CPU embedding and OCR are the bottleneck; the Index page already shows files per minute, so measure before promising "everything".
- **Change tracking.** The NTFS USN change journal "is much more efficient than checking time stamps or registering for file notifications" ([Microsoft Learn: fsutil usn](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/fsutil-usn)). It's the right tool once the scope is a whole volume; the current watchdog + size/mtime approach is fine per folder. *Open question:* check what privileges reading the journal needs before designing around it.

### 3.2 Privacy and security: Recall is the cautionary example
- **Automatic filters leak.** Microsoft added on-device filtering of passwords and card numbers to Recall ([Windows Central](https://www.windowscentral.com/software-apps/windows-11/windows-recall-automatically-filter-passwords-and-credit-card-information)), yet independent tests found it still captured credit card and ID numbers with the filter enabled ([The Register](https://www.theregister.com/2025/08/01/microsoft_recall_captures_credit_card_info/), [Tom's Hardware](https://www.tomshardware.com/software/windows/microsoft-recall-screenshots-credit-cards-and-social-security-numbers-even-with-the-sensitive-information-filter-enabled)).
- **Every file becomes model input.** Indirect prompt injection, meaning instructions planted in content the app ingests ("a poisoned PDF…"), is the top LLM risk ([OWASP LLM01:2025](https://genai.owasp.org/llmrisk/llm01-prompt-injection/), [BSG summary](https://bsg.tech/blog/owasp-llm-top-10/)). OWASP's RAG cheat sheet asks for red-team tests of it ([OWASP RAG Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html)). Indexing the whole drive multiplies the untrusted text the model reads. What limits the damage here: answers are only text, citations are verified, and organizer moves need your approval.
- **Design implication:**
  - Opt-in per folder or drive.
  - A default denylist: system folders, `AppData`, browser profiles, `.ssh`, password databases, `.env` and key files.
  - A "never index" list the user controls.
  - No automatic actions based on file contents.

## 4. "Train himself": what actually works

### 4.1 Don't fine-tune the chat model on your files
- **Retrieval wins for new facts.** "RAG consistently outperforms [unsupervised fine-tuning], both for existing knowledge … and entirely new knowledge", and "LLMs struggle to learn new factual information through unsupervised fine-tuning" ([Ovadia et al., EMNLP 2024](https://arxiv.org/abs/2312.05934)).
- **Fine-tuning can add hallucinations.** Follow-up work links fine-tuning on new facts to more hallucination ([abstract via ResearchGate](https://www.researchgate.net/publication/386186800_Fine-Tuning_or_Retrieval_Comparing_Knowledge_Injection_in_LLMs)). That's a secondary source; read the paper before citing it further.
- **CPU training is possible but slow, and poorly documented.** Guides show LoRA fine-tuning of models under 3B on CPU ([LLaMA-Factory CPU guide](https://github.com/hiyouga/LlamaFactory/discussions/7733), [arXiv 2507.01806](https://arxiv.org/abs/2507.01806)). Reported times range from minutes to "several days" ([meshworld](https://meshworld.in/blog/ai/tooling/train-local-llm-cpu-only/)). Those are blog-level claims; low confidence.
- **It doesn't fit this product.** Files change daily and retraining can't keep up, while the index updates on every save. The current model is a vision-language model served by Ollama, so training, converting and reimporting it is a heavy pipeline.

### 4.2 Learn in the search layer instead
1. **Record feedback locally.** Store which source the user opens after a question, plus an optional thumbs up or down, as (query, file, chunk, signal) rows in SQLite. Research on search engines that learn from implicit feedback finds clicks "essentially free" and specific to that user and collection ([Joachims](https://www.researchgate.net/publication/2961924_Search_Engines_that_Learn_from_Implicit_Feedback)). Personal-search feedback is sparse and biased ([Bendersky et al., IJCAI 2018](https://mlanthology.org/ijcai/2018/bendersky2018ijcai-learning/)), so use it as a gentle boost, not the ranker.
2. **Use the feedback.** Add a third list to the existing RRF fusion: "files you opened for similar questions" (similar = embedding similarity between queries). That's a small change in `hybrid_ranker.merge`.
3. **Grow the eval set from real use.** Accepted answers become new rows in `eval/questions.jsonl`, so every tuning decision (chunk size, k, reranker, embedder) is measured on your own documents.
4. **Adapt the embedder only if the eval shows it's the bottleneck.** GPL ([Generative Pseudo-Labeling](https://sbert.net/examples/sentence_transformer/domain_adaptation/README.html)) makes up questions for your own passages and trains the embedder on them. It raised average nDCG@10 from 45.2 to 51.4 across six datasets (52.4 combined with TSDAE). But the authors trained "for about 1 day on a V100-GPU", and it needs torch and sentence-transformers. That's out of scope for this laptop unless done elsewhere.

## 5. Recommended plan (cheapest first; measure each step)

| Phase | Change | Effort | Expected effect |
|---|---|---|---|
| 0 | Extend `eval/run_eval.py` to report Hit@1, Hit@5 and MRR per stage (keyword, semantic, fused, reranked); record a baseline on real documents | S | Makes every later step measurable |
| 1 | Porter stemming + bm25 path weight; prepend `File/Folder/Title` to chunk text before embedding and FTS; fetch 20–30 candidates per side; add a "files" view that groups chunks by file | S | Likely the largest cheap gain (inference) |
| 1b | Parsers for `.txt .md .csv .html .eml .rtf .odt` | S–M | Coverage: files that are invisible now become findable |
| 2 | ONNX cross-encoder reranker on the top 20–30 (try `bge-reranker-base` vs `ms-marco-MiniLM-L-12-v2` vs `jina-reranker-v1-turbo-en`) | M | +20pp Hit@1 in one benchmark; confirm on your data and measure CPU latency |
| 3 | Embedder A/B: `qwen3-embedding:0.6b` vs `embeddinggemma` vs current | M | Raises the ceiling that reranking can't fix |
| 4 | Whole-device discovery through the Windows Search index; deep indexing opt-in with a default denylist; USN journal later | M–L | "Find any file" without reading everything |
| 5 | Feedback loop: log opens, boost with a third RRF list, auto-grow eval set | M | Learns your vocabulary and habits with no model training |
| Later | LLM-written chunk context for chosen folders overnight; GPL embedder adaptation on a GPU machine | L | Only if phases 0–3 plateau |

**Not recommended:** fine-tuning the chat model on your files (Section 4.1), or indexing the whole drive by default without exclusions (Section 3.2).

## 6. Measured in this codebase (2026-10-01)
`eval/run_eval.py --retrieval`:
- **Corpus:** a synthetic "personal documents" folder written by `eval/retrieval_corpus.py`, 36 files in 5 folders, with near-duplicates.
- **Questions:** 43 in `eval/retrieval_questions.jsonl`, fixed before the first run.
- **Embedder:** `granite-embedding:278m`, via Ollama on this laptop.
- **Metrics:** hit@1 = right file ranked first. top-6 = right file among the 6 chunks the chat model reads. MRR = mean of 1/rank of the right file.

| Change | Keyword hit@1 | Semantic hit@1 | Combined hit@1 | Combined top-6 | Combined MRR | Kept? |
|---|---|---|---|---|---|---|
| Baseline | 0.674 | 0.628 | 0.698 | 0.721 | 0.709 | – |
| + text/Markdown/CSV/HTML/email parsers | 0.930 | 0.884 | 0.977 | 1.000 | 0.988 | yes |
| + porter stemming | 0.930 | 0.884 | 0.953 | 1.000 | 0.977 | **no** |
| + file name/folder in embedded text (with stemming) | 0.930 | 0.930 | 0.953 | 1.000 | 0.977 | yes |
| + candidate pool 20 instead of 8 (with the above) | 0.930 | 0.930 | 0.953 | 1.000 | 0.977 | **no**, no change |
| **Shipped: parsers + file context, no stemming** | 0.930 | 0.930 | **0.977** | **1.000** | **0.988** | |

What this shows and doesn't:
- **The parsers are the real win.** All 12 baseline misses were `.txt/.md/.csv/.html/.eml` files the app couldn't read.
- **Ranking had little headroom here.** On PDF and Office files, combined search already ranked the right file first for 30 of 31 questions, so the ranking changes are judged per stage and by regressions. With 43 questions, one question is 2.3 points, which is noise.
- **File context helped semantic search** on two questions ("How much is the Initech invoice for?", "monthly EMI for the home loan") and hurt none.
- **Stemming never helped and cost one question.** "what CTC was I offered…": stemming let "offer" and "join" match a second file, and merging the two lists ranked that file above the offer letter, which only keyword search found. Every "variant" question matched on its other words anyway. Retry on real documents: it's one line in `sqlite_fts.py` plus a schema bump.
- **A bigger candidate pool changed nothing** without a reranker to reorder it. Revisit it with the reranker (phase 2).
- **Remaining misses:** the CTC question (semantic search doesn't link the acronym to the offer letter), and "how much did the refrigerator cost", which ranks the warranty card above the inventory spreadsheet. Both are what a reranker is for.
- **Synthetic corpus caveat:** these are one person's guesses at realistic files and questions. Run `--retrieval --folder <your folder> --questions <yours>` on real documents before trusting any gain.

## Key takeaways
- Do the cheap fixes first: stemming, file context in the embedded text, a bigger candidate pool, results grouped by file, and more formats. Measure each with the eval harness.
- Add an ONNX reranker next; it's the most consistently measured gain and needs no GPU or torch. Pick the model by testing on your own files, since published leaderboards disagree.
- For "all the data", use Windows' own index to find files, and read in depth only folders you opt into, with secrets excluded by default.
- "Self-training" should mean learning from which results you open, not retraining the model.

## Sources
1. [Anthropic: Introducing Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval): contextual embeddings + BM25 + reranking cut top-20 failures by 35%, 49% and 67%.
2. [Claude cookbook: contextual embeddings](https://platform.claude.com/cookbook/capabilities-contextual-embeddings-guide): Pass@10 87% → 92% → ~95%.
3. [AIMultiple: Reranker benchmark (Feb 2026)](https://aimultiple.com/rerankers): 8 rerankers; Hit@1 62.67% → 83.00%; small models can tie large ones; the retriever caps gains.
4. [Presenc AI: open-weight reranker leaderboard 2026](https://presenc.ai/research/best-open-weight-reranker-models-2026): BEIR figures; conflict with source 5.
5. [Anas Rabhi: Reranker comparison (Jun 2026)](https://ianas.fr/en/blog/2026/06/07/reranker-comparatif-cohere-bge-jina-voyage/): bge-reranker-v2-m3 CPU note; different BEIR figure.
6. [Splunk: selecting a reranking model](https://www.splunk.com/en_us/blog/artificial-intelligence/reranker-models.html): reranker architecture trade-offs.
7. [Qdrant: Reranking with FastEmbed](https://qdrant.tech/documentation/fastembed/fastembed-rerankers/): ONNX cross-encoders on CPU without torch.
8. [FastEmbed supported models](https://qdrant.github.io/fastembed/examples/Supported_Models/): reranker names, sizes, licenses.
9. [Ollama rerank adapter](https://github.com/jtianling/dify-ollama-rerank-adapter): third-party wrapper, evidence there's no native rerank API.
10. [Glukhov: Qwen3 embedding & reranker on Ollama](https://www.glukhov.org/rag/embeddings/qwen3-embedding-qwen3-reranker-on-ollama/): community write-up on reranking with Ollama.
11. [Ollama library: qwen3-embedding](https://ollama.com/library/qwen3-embedding): tags, sizes, context windows.
12. [morphllm: Best Ollama embedding models 2026](https://www.morphllm.com/ollama-embedding-models): MTEB comparison of Ollama embedders.
13. [d-central: Local embedding models 2026](https://d-central.tech/local-embedding-models/): per-benchmark scores incl. granite-embedding:278m; comparability warning.
14. [SurrealDB: Embedding models comparison](https://surrealdb.com/blog/embedding-models-comparison): EmbeddingGemma details and prefix requirement.
15. [SQLite FTS5 documentation](https://www.sqlite.org/fts5.html): porter and trigram tokenizers, bm25 column weights.
16. [Microsoft Learn: Querying the index programmatically](https://learn.microsoft.com/en-us/windows/win32/search/-search-3x-wds-qryidx-overview): Windows Search query APIs.
17. [Microsoft Learn: Windows Search SQL syntax](https://learn.microsoft.com/en-us/windows/win32/search/-search-sql-ovwofsearchquery): `SELECT … FROM SystemIndex`.
18. [Microsoft Learn: fsutil usn](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/fsutil-usn): USN change journal efficiency.
19. [voidtools: Everything SDK](https://www.voidtools.com/support/everything/sdk/): IPC file-name search; Everything must be running.
20. [Ovadia et al.: Fine-Tuning or Retrieval? (EMNLP 2024)](https://arxiv.org/abs/2312.05934): RAG beats unsupervised fine-tuning for knowledge.
21. [Sentence Transformers: Domain Adaptation / GPL](https://sbert.net/examples/sentence_transformer/domain_adaptation/README.html): 45.2 → 51.4 avg nDCG@10; ~1 GPU-day.
22. [OWASP LLM01:2025 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) and [OWASP RAG Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html): indirect injection via ingested documents.
23. [The Register: Recall still captures card info (Aug 2025)](https://www.theregister.com/2025/08/01/microsoft_recall_captures_credit_card_info/), [Tom's Hardware](https://www.tomshardware.com/software/windows/microsoft-recall-screenshots-credit-cards-and-social-security-numbers-even-with-the-sensitive-information-filter-enabled), [Windows Central](https://www.windowscentral.com/software-apps/windows-11/windows-recall-automatically-filter-passwords-and-credit-card-information): limits of automatic sensitive-data filtering.
24. [Joachims: Search Engines that Learn from Implicit Feedback](https://www.researchgate.net/publication/2961924_Search_Engines_that_Learn_from_Implicit_Feedback) and [Bendersky et al.: Learning with Sparse and Biased Feedback for Personal Search (IJCAI 2018)](https://mlanthology.org/ijcai/2018/bendersky2018ijcai-learning/): learning from clicks and its biases.

Supporting, low confidence: [LLaMA-Factory CPU LoRA guide](https://github.com/hiyouga/LlamaFactory/discussions/7733), [arXiv 2507.01806](https://arxiv.org/abs/2507.01806), [meshworld CPU training guide](https://meshworld.in/blog/ai/tooling/train-local-llm-cpu-only/).

## Methodology
- **Queries:** 18 web searches, 13 through Firecrawl (3 rate-limited) and 5 through WebSearch.
- **Read in full:** 7 sources (AIMultiple, Sentence Transformers GPL, FastEmbed models, three Microsoft Learn pages, SQLite FTS5).
- **Codebase:** read the retrieval code (`fts_search.py`, `vector_search.py`, `hybrid.py`, `chunking.py`, `sqlite_fts.py`, parsers, `requirements.txt`) to map findings onto the current app.
- **Sub-questions:**
  1. What measurably improves local retrieval accuracy?
  2. Which rerankers and embedders fit a CPU, no-torch, Ollama stack?
  3. How can a Windows app discover and track files across a whole drive?
  4. What are the privacy and security risks of indexing everything?
  5. Can the assistant "train itself" usefully, and how?
- **Gaps:**
  - No CPU latency benchmarks for rerankers on laptop hardware.
  - No benchmark of the deterministic "file/folder/title prefix" variant of contextual retrieval.
  - The privileges needed to read the USN journal weren't confirmed.
  - Ollama's rerank status comes from community sources, not official docs.
- **Prompt injection:** no source contained agent-directed instructions.
