# sPHENIX Workflow Assistant

A Retrieval-Augmented Generation (RAG) tool that can answer
natural-language questions about the sPHENIX software stack,
grounded directly in the official, publicly available source code and documentation.

Questions like these get accurate, source-cited answers:

- *"How do I run a Fun4All macro for calorimeter simulation?"*
- *"Generate a steering macro skeleton for HCAL reconstruction."*
- *"What does `PHCompositeNode` do and how do I use it?"*
- *"How do I add a custom analysis module to the Fun4All framework?"*
- *"What is the workflow for TPC track reconstruction?"*
- *"How do I set up the Singularity container environment?"*

---

## For collaborators: no installation required

If someone on your team has already deployed the assistant, installation is not required. You can open the URL they share in any web browser and start asking questions.

There are three ways the assistant can be made available:

**Option A — Shared public URL (Streamlit Cloud)**
The maintainer deploys the tool to Streamlit Community Cloud. The user recieves a URL
like `https://sphenix-rag.streamlit.app` and can open it in a browser. No account,
no installation, no API key needed — the maintainer covers API costs.

**Option B — You supply your own API key**
The maintainer shares a URL (public or on the local network). The user
will see an **API key** field in the left sidebar and paste their own Anthropic
API key there (get one free at [console.anthropic.com](https://console.anthropic.com)).
The key is used only for the session and is never stored by the application.
This lets the tool run at no cost to the maintainer, but it is now disabled by
default. To enable it, set `ALLOW_BROWSER_API_KEY=true` and only do so on
`localhost` or over a trusted HTTPS deployment. Do not paste API keys into a
plain HTTP page on a shared network.

**Option C — Local network URL**
The maintainer runs the assistant on a machine accessible within the user network (e.g.
an RCF login node or a shared lab machine). The user receives an address like
`http://192.168.x.x:8501` and can open it in their browser. Nothing to install.

In all three cases your experience is the same: type a question in the chat box
at the bottom of the page and receive an answer with source file citations.

> **If you are the first person on your team setting this up**, continue reading
> from [Requirements](#requirements) below. Setup takes about 30 minutes the
> first time.

---

## How it works

The assistant never makes things up. Every answer is generated from text retrieved
directly from the sPHENIX GitHub repositories at the moment of the query.

```
┌─────────────────────────────────────────────────────────────┐
│                      INGESTION (one-time)                   │
│                                                             │
│  GitHub repos ──► parse files ──► chunk text ──► embed     │
│  (public only)     .C .h .py       ~600 tokens    vectors  │
│                    .md .sh .ipynb                    │      │
│                                               FAISS index   │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    QUERY (every question)                   │
│                                                             │
│  User question ──► embed ──► retrieve ──► Anthropic API    │
│                   vector     top-8 chunks   (Claude)       │
│                              from index         │          │
│                                           grounded answer  │
└─────────────────────────────────────────────────────────────┘
```

**Indexed repositories** (all public, under `github.com/sPHENIX-Collaboration`):

| Repository | Content indexed |
|---|---|
| `macros` | All `.C`, `.h`, `.py`, `.md`, `.sh` files |
| `tutorials` | All `.ipynb`, `.md`, `.C`, `.py` files |
| `coresoftware` | Top-level `.md`, `.h`, `.C` files (README and headers) |
| `analysis` | All `.C`, `.h`, `.py`, `.md` files |
| `Singularity` | All `.sh`, `.md` files |

The index is built incrementally — on each update run, only files that changed
since the last indexed commit are re-processed. A full rebuild takes 15–30 minutes;
subsequent updates take seconds.

---

## Requirements

- Python 3.10 or later
- Git
- An [Anthropic API key](https://console.anthropic.com/) (for answer generation)
- ~5 GB disk space (for cloned repos and the FAISS vector index)
- Internet access to reach GitHub and the Anthropic API

The embedding model (`BAAI/bge-large-en-v1.5`, ~1.3 GB) is downloaded automatically
from HuggingFace on first run and cached locally. No GPU is required.

---

## Installation

**1. Clone this repository**

```bash
git clone https://github.com/<your-org>/sphenix-rag.git
cd sphenix-rag
```

**2. Install Python dependencies**

```bash
pip install -r requirements.txt
```

Using a virtual environment is recommended:

```bash
python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**3. Set your Anthropic API key**

Copy the example environment file and add your key:

```bash
cp .env.example .env
```

Then open `.env` and replace the placeholder:

```
ANTHROPIC_API_KEY=sk-ant-your-key-here
ALLOW_BROWSER_API_KEY=false
```

The `.env` file is listed in `.gitignore` and will never be committed to git.
Never paste your API key directly into any Python file or a crontab line.
Leave `ALLOW_BROWSER_API_KEY=false` unless you explicitly want the sidebar key
field and you trust the connection your collaborators are using.

---

## Building the index

Run the ingestion pipeline once before launching the assistant:

```bash
python ingest.py
```

This will:

1. Clone the 5 sPHENIX repositories listed above into `./repos/`
2. Parse all relevant source files
3. Split files into overlapping text chunks (~600 tokens each)
4. Embed each chunk using `BAAI/bge-large-en-v1.5` (a strong scientific text model)
5. Save a FAISS vector index to `./index/sphenix.index`
6. Save chunk metadata to `./index/chunks.json`
7. Record the current git commit hash for each repo in `./index/state.json`

**First run:** approximately 15–30 minutes depending on your connection and hardware.

**Subsequent runs** (after a `git pull` on any repo): seconds, because only changed
files are re-processed.

**Force a full rebuild from scratch:**

```bash
python ingest.py --full
```

---

## Running the assistant

**Web interface (recommended):**

```bash
streamlit run app.py
```

Open your browser at `http://localhost:8501`. The interface includes a sidebar with
example questions, source file citations for every answer, and an optional view of
the raw retrieved text chunks.

**Command-line interface:**

```bash
python rag.py "How do I run the HCAL tower calibration macro?"
python rag.py "Generate a Fun4All steering macro for Au+Au GEANT4 simulation"
```

---

## Keeping the index current

The index does not update automatically. To stay current with the latest commits
across all five repositories, schedule a nightly update using cron:

```bash
crontab -e
```

Add this line (adjust paths to match your installation):

```
0 2 * * * cd /path/to/sphenix-rag && source .venv/bin/activate && python ingest.py >> /tmp/sphenix-rag-ingest.log 2>&1
```

This runs at 2:00 AM every night. The log file at `/tmp/sphenix-rag-ingest.log`
records what changed. On nights when no repositories have new commits, the run
completes in under 10 seconds.

---

## Project structure

```
sphenix-rag/
├── ingest.py         # Ingestion pipeline: clone, parse, embed, index
├── rag.py            # Query engine: retrieve + generate via Anthropic API
├── app.py            # Streamlit web interface
├── requirements.txt  # Python dependencies
├── .env.example      # Template for your API key (copy to .env)
├── .gitignore        # Auto-generated on first ingest run
│
├── repos/            # Cloned sPHENIX repositories (auto-created, not committed)
│   ├── macros/
│   ├── tutorials/
│   ├── coresoftware/
│   ├── analysis/
│   └── Singularity/
│
└── index/            # Vector index and metadata (auto-created, not committed)
    ├── sphenix.index # FAISS vector index (binary)
    ├── chunks.json   # Chunk text and source metadata
    └── state.json    # Per-repo commit hashes for incremental updates
```

The `repos/` and `index/` directories are large and are excluded from git
automatically. They are rebuilt locally by running `ingest.py`.

---

## Data and privacy

**What data leaves your machine:**
When you submit a question, the question text and the retrieved code/documentation
chunks are sent to the Anthropic API to generate an answer. All retrieved content
comes from the public sPHENIX GitHub repositories.

**Anthropic API data policy (as of 2025):**
API inputs and outputs are retained for up to 7 days and are then permanently
deleted. They are never used to train Anthropic's models. See
[Anthropic's privacy documentation](https://privacy.claude.com) for details.

**Important:** Do not paste unpublished physics results, proprietary analysis code,
internal configuration files, BNL credentials, or any non-public information into
this tool. The assistant is designed for questions about the public sPHENIX software
stack only.

**What stays local:**
The FAISS index, all cloned repository files, and the embedding model run entirely
on your machine. No data is sent to any external service during ingestion.

---

## Extending the assistant

**Add more public repositories:**

Edit the `REPOS` and `INCLUDE_EXTENSIONS` dictionaries in `ingest.py`:

```python
REPOS = {
    ...
    "new-repo": "https://github.com/sPHENIX-Collaboration/new-repo.git",
}
INCLUDE_EXTENSIONS = {
    ...
    "new-repo": {".C", ".h", ".md"},
}
```

Then run `python ingest.py` to index the new content.

**Add local documentation:**

Place `.md` or plain text files in a `local_docs/` directory. In `ingest.py`,
add a loop that reads files from that directory and passes them through the same
`chunk_text()` → `embed_and_add()` pipeline. These files are never committed to
git.

**Change the language model:**

In `rag.py`, change the `model` parameter in the `client.messages.create()` call.
The current default is `claude-sonnet-4-6`. Available options and their trade-offs
are documented at [docs.anthropic.com](https://docs.anthropic.com).

**Change the number of retrieved chunks:**

Adjust `TOP_K` in `rag.py` (default: 8). Higher values give the model more context
but increase API cost and latency. For complex multi-step questions, increasing to
12–16 can improve answer quality.

---

## Sharing with collaborators

By default the assistant runs on your local machine. To share it:

**Streamlit Community Cloud (free, simplest):**
Push this repository to GitHub, connect it at
[share.streamlit.io](https://share.streamlit.io), and add `ANTHROPIC_API_KEY` as
a secret in the Streamlit dashboard. Collaborators get a public URL. Note that
all queries will use your API key.

**Collaborator-supplied API keys:**
The sidebar in the web interface includes an optional field where each collaborator
can paste their own Anthropic API key. When a key is entered there, it is used for
that session only and is never stored or logged by the application.

**Local network deployment:**
To make the assistant available within a local network, run:

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

Collaborators on the same network can then reach it at `http://<your-ip>:8501`.
Do not ask collaborators to paste personal API keys into this HTTP endpoint unless
you put it behind TLS and access controls. For LAN sharing, prefer using the
server's own API key or a reverse proxy that provides HTTPS.

---

## Limitations

- The assistant can only answer questions about the content in the indexed
  repositories. It will say so clearly rather than guess if the relevant
  information is not in its context.

- Code generation (e.g., steering macros) produces skeletons based on patterns
  in the indexed codebase. Generated code should always be reviewed and tested
  before use.

- The index reflects the state of the repositories at the time of the last
  `ingest.py` run. If a repository was updated today and the index has not been
  rebuilt, the assistant will not know about those changes.

- Very large files (over 500 KB) are skipped during ingestion to avoid memory
  issues. These are typically auto-generated files unlikely to contain useful
  documentation.

---

## Troubleshooting

**`FileNotFoundError: Index not found`**
Run `python ingest.py` to build the index before starting the assistant.

**`ANTHROPIC_API_KEY is not set`**
Ensure your `.env` file exists, contains a valid key, and is in the same directory
as `rag.py` and `app.py`.

**Pull fails during ingestion**
The cached repository in `repos/<name>/` may be corrupted. Delete that subdirectory
and re-run `ingest.py` to re-clone it.

**Answers seem outdated**
Run `python ingest.py` to pull the latest commits and rebuild the changed portions
of the index.

**Index uses too much disk space**
Run `python ingest.py --full` to rebuild from scratch, which removes any
accumulated deleted-chunk overhead from incremental updates.

---

## Contributing

Contributions are welcome. If you are a sPHENIX collaborator and find that the
assistant consistently fails to answer a particular category of question, please
open an issue describing the question and what a correct answer would look like.
This helps calibrate which repositories or file types should be added to the index.

To contribute code, please open a pull request. All changes to `ingest.py` should
be tested with both a fresh build (`--full`) and an incremental run.

---

## License

This tool indexes and retrieves content from public sPHENIX GitHub repositories.
All retrieved content remains under the licenses of its respective source repositories.
This project itself is released under the Apache License 2.0.
