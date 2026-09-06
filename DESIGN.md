# DESIGN.md

## What it does

When a user uploads an operating procedure, the app figures out which of 30 SANS security policies it belongs to, or says no match. It then checks the document against every rule in that policy and marks each rule aligned, contradicted, missing, or flagged for review. Contradicted and missing rules come with a quote from the document, and the app confirms the quote is really there before showing it.

- Standards library. SANS Security Policy Templates from the GitHub mirror in the assignment. 63 PDFs, 50 real policies, 13 incident-handling forms. I loaded 30 policies and skipped the forms since they contain no rules.
- Models. gpt-4.1 and text-embedding-ada-002, both through Stanford's AI API Gateway.

## How it works

- Ingest (offline, once). `ingest.py` sends each policy PDF to gpt-4.1 and asks for every rule as one short testable sentence, skipping the boilerplate all SANS policies share. 543 rules total. Each rule is embedded, plus one summary vector per policy, for 573 vectors committed to the repo.
- Intake. File type checked by bytes, not extension. 10 MB cap. Empty files and text-less PDFs rejected. Nothing written to disk. Text split into chunks of at most about 400 words.
- Matching. Chunks embedded and compared to all 573 vectors with cosine similarity in NumPy. Each policy scores the average of its three best-matching chunks (rule chunks, or its one profile-summary chunk). Under 0.86 is no match. Above it, the top three policies go to gpt-4.1, which picks one or says none. This second gate exists because Password Protection and Password Construction score within 0.01 to 0.03 of each other. On my violations document retrieval had Construction at 0.87 and Protection at 0.86, and the LLM correctly picked Protection.
- Verification. The policy's rules go to gpt-4.1 in batches of 8, each with the full document, in JSON mode. 18 rules for Password Protection means 3 calls. Each rule gets a verdict, a one-line reason, a confidence, and for contradicted or missing a word-for-word quote. The document is wrapped in delimiters and labeled untrusted.
- Checking the model. Pydantic validates the JSON, with one retry, then the batch is flagged for review instead of a 500. The quote verifier searches the document for each quote and downgrades any verdict whose quote isn't there. Any skipped rule gets a flagged entry, so every rule has exactly one verdict.
- Cost. One run on the violations document is 5 LLM calls and about 6,100 tokens.

## Decisions

- Stanford gateway with gpt-4.1. It's what Stanford runs, and I confirmed JSON mode and temperature 0 work through it with a quick Python script before writing any pipeline code. Model name is one env var.
- ada-002 embeddings. Only embedding model on the gateway, and no 2 GB PyTorch download for the grader. Its scores bunch up: unrelated text scored anywhere from 0.72 to 0.86 in my testing, higher when it happened to share vocabulary with a policy, which is why the threshold is 0.86 with a thin margin.
- NumPy instead of a vector database. 573 vectors, milliseconds, nothing to install. Swapping in FAISS at 10,000 policies wouldn't touch the rest of the code.
- LLM extraction instead of regex. The spec asks for atomic requirements anyway, and this keeps the shared boilerplate out of the index.
- Whole document to the verifier. Procedures fit in context. Chunk retrieval per rule is the fallback for long documents and wasn't needed.
- JSON mode plus Pydantic, not `json_schema`. JSON mode is confirmed on the gateway, `json_schema` is untested.
- Quotes verified against source text. Models invent quotes. Unsupported verdicts are downgraded, never shown as fact.
- Synchronous endpoint, plain HTML. Under a second to about 20 seconds per request depending on how many rules the matched policy has, one user at a time. The grader needs Python and nothing else.

## What I tested and found

- Three test documents as assigned. Compliant, 7 planted violations with an answer key (5 contradictions, 2 omissions), and an unrelated lab-equipment procedure. Regression catches 7 of 7 on repeat runs. Violations doc returns 3 aligned, 6 contradicted, 9 missing, 0 flagged. The extra contradiction is real (password sharing also breaks the role-management rule), and the extra missing rules are ones the document never mentions.
- Threshold bypass. With gate 1 off, the LLM gate matched the lab document to Lab Security Policy on word overlap alone. So the threshold is doing real work. Making the LLM gate stricter fixed that case but flipped the violations doc to the wrong sibling, so I reverted it.
- A Sanskrit PDF with no paragraph breaks became one 16,000-token chunk and the embedding call failed with a 400, which my handler mislabeled as an outage. Fixed both. Chunks now cap at 400 words, 4xx errors return 422, outages return 503. The PDF now gets no match at 0.72.
- PDF and DOCX fixtures found two more bugs, Windows line endings and a UTF-8 byte order mark. Both fixed. They also showed that stripping headings flips Password Protection to Password Construction even when retrieval had Protection ahead (0.8915 vs 0.8792). That case stays in the suite as an expected failure.
- 30 unit tests over chunker, quote verifier, reconcile, file validation, and batching, with the LLM mocked. The regression script uses the real LLM.

## Limitations

- Threshold set on three documents, margin under 0.01.
- Password Protection vs Password Construction within 0.03 on every input, and formatting alone can flip it.
- Prompt injection protection is partial. The verifier catches invented quotes, not an aligned verdict citing a real but irrelevant sentence.
- Verdict counts were byte-identical across every repeat run I tried at temperature 0, but the gateway gives no guarantee of bit-for-bit reproducibility, so I wouldn't assume that holds at scale.
- Non-English and documents over about 60,000 characters untested.
- Re-running ingest re-embeds everything and the vectors aren't byte-identical.
- 30 of 50 policies loaded, titles and rule counts checked, but nobody has read all 543 rules.
- Built and tested on macOS with Python 3.14. Windows steps in the README are untested.

## Measuring accuracy at scale

- Hand-labeled set first. Each procedure gets a known policy and a human verdict per rule. My three inputs are the seed.
- On every prompt or model change, track match accuracy (top-1 plus precision and recall on no match), per-verdict precision and recall with a confusion matrix, share of quotes passing the verifier, and share of flagged-for-review verdicts.
- Once the set is too big to label by hand, add an LLM judge scoring each verdict and quote against the label, with humans spot-checking the judge. The regression script is version one of this.

## What I'd build next

- Evaluate against both policies when two are within a small margin, instead of forcing a pick.
- A BM25 keyword score next to the embedding score, for the exact terms that separate sibling policies.
- More real PDF and DOCX files in the regression set, since that's where today's bugs were.
- Chunk retrieval per rule for long documents.
- Policy versioning, starting from the metadata block already on each cached policy.
- Cache results by file hash.
- A React results view with filtering by verdict.
