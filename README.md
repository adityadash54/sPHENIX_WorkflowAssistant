# sPHENIX Workflow Assistant

A Retrieval-Augmented Generation (RAG) tool that can answer
natural-language questions about the sPHENIX software stack,
grounded directly in the official, publicly available source code and documentation.

Questions like these get accurate, source-cited answers:

- *"How do I run a Fun4All macro for calorimeter simulation?"*
- *"Generate a steering macro skeleton for HCAL reconstruction."*
- *"What does `PHCompositeNode` do and how do I use it?"*
- *"Where is the main Fun4All server loop defined in coresoftware?"*
- *"What calibration constants does the EMCal need?"*

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
will see an **API key** field in the left sidebar and paste their own Anthropic or
OpenAI API key there.
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
> from [Requirements](#requirements) below. Setup typically takes a few minutes the
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
│                    .md .sh                          │      │
│                                               FAISS index   │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    QUERY (every question)                   │
│                                                             │
│  User question ──► embed ──► retrieve ──► LLM API          │
│                   vector     top-8 chunks   (Anthropic or  │
│                              from index      OpenAI)       │
│                                               │            │
│                                           grounded answer  │
└─────────────────────────────────────────────────────────────┘
```

By default, this repository currently indexes two public repositories under
`github.com/sPHENIX-Collaboration`:

| Repository | Content indexed |
|---|---|
| `macros` | All `.C`, `.h`, `.py`, `.md`, `.sh` files |
| `coresoftware` | Top-level `.md`, `.h`, `.C` files (README and headers) |

Additional repositories such as `tutorials`, `analysis`, and `Singularity` are
available later as an opt-in expansion. The default stays smaller on purpose so
first-time setup is faster and less CPU-intensive.

The index is built incrementally — on each update run, only files that changed
since the last indexed commit are re-processed. A full rebuild typically takes a few minutes;
subsequent updates take seconds.

---

## Requirements

- Python 3.10 or later
- Git
- An Anthropic or OpenAI API key (for answer generation)
- ~3 GB disk space (for cloned repos, caches, and the FAISS vector index)
- Internet access to reach GitHub and your selected LLM provider API

The embedding model (`BAAI/bge-large-en-v1.5`, ~1.3 GB) is downloaded automatically
from HuggingFace on first run and cached locally. No GPU is required.

---

## Installation

You can set up the assistant in one of two ways: a **Docker** install (most isolated and recommended for secure deployments) or a **pip** install directly into a local Python environment.

**1. Clone this repository**

```bash
git clone https://github.com/<your-org>/sPHENIX_WorkflowAssistant.git
cd sPHENIX_WorkflowAssistant
```

**2. Install Python dependencies**

Using a virtual environment is recommended:

```bash
python -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

If you do not want to set up a new virtual environment, install directly into your current Python environment:

```bash
pip install -r requirements.txt
```

**Docker installation (secure default)**

The Docker flow below is the most isolated default setup in this README. Follow
these steps as written if you want the safest first install:

- no bind mounts
- no host networking
- the web UI is published on `127.0.0.1:8501` only
- no `--privileged`
- runtime image does not include `git`
- both containers can run with a read-only root filesystem
- model caches, cloned repos, and the FAISS index stay under `/app`
- serving happens from a separate runtime image after ingestion completes

In this hardened setup, the application root at `/app` remains read-only at runtime.
Only the mounted subdirectories such as `/app/.cache`, `/app/index`, and `/app/repos`
are writable where needed.

Keep the host-network isolation defaults in place:

- keep Docker Desktop **Enable host networking** turned off
- do not run either container with `--network host`
- keep the localhost-only publish binding `127.0.0.1:8501:8501`
- if the host runs sensitive services, keep them bound to `127.0.0.1` where possible
- if you need stricter host-side filtering, use the OS-specific firewall guidance in [Additional host-side hardening](#additional-host-side-hardening-optional)

**Recommended: `docker-compose.yml` (avoids hand-typing flags)**

The repo ships a `docker-compose.yml` that encodes every flag from the manual
walkthrough below — read-only root filesystem, `cap_drop: ALL`,
`no-new-privileges`, `127.0.0.1`-only publishing, a CPU cap on ingestion, and
**no bind mounts** (only the three named volumes below). Using it means you
never have to correctly retype ~10 flags by hand:

```bash
cp .env.example .env      # then edit .env and add your API key

docker compose --profile ingest run --rm ingest   # one-time index build
docker compose up -d app                          # start the web UI
```

The only exception to "not a hand-typed flag" is `.env` itself, which you
still edit directly. Rebuilding after a `git pull`:

```bash
git pull --ff-only
docker compose build
docker compose --profile ingest run --rm ingest
docker compose up -d app
```

One trade-off versus the manual `docker run` form below: Compose's `tmpfs`
option only supports a size limit, not the `noexec,nosuid` mount flags used
in the manual commands. If you need that extra `/tmp` restriction, use the
manual `docker run` steps instead.

If you'd rather run the containers by hand (no Compose, or you want to see/
adjust every flag yourself), follow the numbered steps below — they are
equivalent to what the Compose file does.

1. Create a `.env` file from the example:

```bash
cp .env.example .env
```

2. Build the one-time ingestion image:

```bash
docker build --target ingest -t sphenix-rag-ingest .
```

3. Create Docker-managed volumes for the app state:

```bash
docker volume create sphenix-rag-cache
docker volume create sphenix-rag-index
docker volume create sphenix-rag-repos
```

4. Run ingestion in an isolated container with no host directory mounts:

```bash
docker run --rm \
  --name sphenix-rag-ingest \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=256m \
  --env-file .env \
  --mount source=sphenix-rag-cache,target=/app/.cache \
  --mount source=sphenix-rag-index,target=/app/index \
  --mount source=sphenix-rag-repos,target=/app/repos \
  --cpus=2 \
  --cap-drop=ALL \
  --security-opt no-new-privileges:true \
  sphenix-rag-ingest
```

This step clones the public sPHENIX repositories, downloads the embedding model,
and writes all state under `/app/.cache`, `/app/index`, and `/app/repos`.
Nothing from your host filesystem is mounted into the container.

`--cpus=2` caps how much CPU the embedding step can use — embedding
`macros`/`coresoftware` is CPU-heavy and will otherwise spin up every core
(and your fans) for the duration of the build. Raise or drop the flag to
trade heat for ingest speed; add `TORCH_NUM_THREADS` to `.env` for the same
throttling when running `ingest.py` outside Docker.

5. Build the runtime image:

```bash
docker build --target runtime -t sphenix-rag .
```

6. Run the web app from the runtime image with localhost-only publishing:

```bash
docker run -d \
  --name sphenix-rag \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=256m \
  --publish 127.0.0.1:8501:8501 \
  --env-file .env \
  --mount source=sphenix-rag-cache,target=/app/.cache \
  --mount source=sphenix-rag-index,target=/app/index,readonly \
  --cap-drop=ALL \
  --security-opt no-new-privileges:true \
  sphenix-rag
```

7. Open the assistant in your browser:

```text
http://localhost:8501
```

If the GitHub repository has changed and you want the latest version of the app with the same hardened Docker settings, pull the latest commits, rebuild the images, refresh the index, and restart the runtime container:

```bash
git pull --ff-only
docker build --target ingest -t sphenix-rag-ingest .
docker build --target runtime -t sphenix-rag .
docker stop sphenix-rag
docker rm sphenix-rag
docker run --rm \
  --name sphenix-rag-ingest \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=256m \
  --env-file .env \
  --mount source=sphenix-rag-cache,target=/app/.cache \
  --mount source=sphenix-rag-index,target=/app/index \
  --mount source=sphenix-rag-repos,target=/app/repos \
  --cpus=2 \
  --cap-drop=ALL \
  --security-opt no-new-privileges:true \
  sphenix-rag-ingest
docker run -d \
  --name sphenix-rag \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=256m \
  --publish 127.0.0.1:8501:8501 \
  --env-file .env \
  --mount source=sphenix-rag-cache,target=/app/.cache \
  --mount source=sphenix-rag-index,target=/app/index,readonly \
  --cap-drop=ALL \
  --security-opt no-new-privileges:true \
  sphenix-rag
```

The Docker volumes only need to be created once. Keep them if you want to retain the downloaded model, cloned repos, and FAISS index between updates.

Useful container commands:

```bash
docker stop sphenix-rag
docker start sphenix-rag
docker run --rm \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=256m \
  --env-file .env \
  --mount source=sphenix-rag-cache,target=/app/.cache \
  --mount source=sphenix-rag-index,target=/app/index \
  --mount source=sphenix-rag-repos,target=/app/repos \
  --cpus=2 \
  --cap-drop=ALL \
  --security-opt no-new-privileges:true \
  sphenix-rag-ingest
```

Important notes:

- Do not use `-v` or `--mount type=bind` if you want the container isolated from local files.
- Do not replace `127.0.0.1:8501:8501` with `0.0.0.0:8501:8501` unless you intentionally want the UI reachable from other machines.
- Do not add `--network host`.
- Do not use `--privileged`.
- The runtime container has no `git` executable, so it cannot refresh remote repos itself.
- The application now stores its cache under `/app/.cache`; the repo already ignores that directory locally.
- Do not mount `/var/run/docker.sock`, SSH agent sockets, `~/.ssh`, `~/.aws`, `~/.config`, or other host credential/config directories into either container.
- The runtime container only needs `/app/.cache` as writable state and `/app/index` as read-only data. It does not need `/app/repos`.
- If you also need to block access to services running on the host machine, apply the host-side controls below.
- `docker-compose.yml` applies all of the above by construction — if you're hand-rolling `docker run` commands instead (e.g. a custom deployment), double check each flag against this list rather than copying an older command from shell history.

**Additional host-side hardening (optional)**

The Docker commands above already keep the app on `localhost` and avoid host networking.
If you want stricter separation from services running on the host machine itself, add the
following OS-specific controls on top of that default.

**macOS (Docker Desktop)**

1. Keep using localhost-only port publishing:

```bash
docker create \
  --name sphenix-rag \
  --publish 127.0.0.1:8501:8501 \
  --env-file .env \
  --cap-drop=ALL \
  --security-opt no-new-privileges:true \
  sphenix-rag \
  tail -f /dev/null
```

2. In Docker Desktop, leave **Enable host networking** turned off.

3. Turn on the macOS firewall:
   `System Settings -> Network -> Firewall`

4. Click **Options** and either:
   - turn on **Block all incoming connections** for broad protection, or
   - add specific host apps/services and set them to **Block**

5. If you have sensitive sharing services enabled on the Mac, also turn them off in **Sharing** settings.

**Linux (Docker Engine or Docker Desktop for Linux)**

1. Keep using localhost-only port publishing:

```bash
docker create \
  --name sphenix-rag \
  --publish 127.0.0.1:8501:8501 \
  --env-file .env \
  --cap-drop=ALL \
  --security-opt no-new-privileges:true \
  sphenix-rag \
  tail -f /dev/null
```

2. Do not run the container with `--network host`.

3. Bind sensitive host services to `127.0.0.1` where possible so they are not listening on external host interfaces.

4. If you need firewall rules around Docker traffic, prefer `iptables` / `nftables` rules that work with Docker's own chains.

5. On Ubuntu or Debian, do not rely on UFW alone for published Docker ports. Docker publishes ports through `nat` rules before UFW's normal `INPUT` / `OUTPUT` chains.

6. For Linux hosts that need stricter filtering of Docker-forwarded traffic, add rules in Docker's `DOCKER-USER` chain rather than appending ordinary `FORWARD` rules. Example pattern:

```bash
sudo iptables -I DOCKER-USER -m state --state RELATED,ESTABLISHED -j ACCEPT
sudo iptables -I DOCKER-USER -i eth0 ! -s 192.0.2.0/24 -j DROP
```

Replace `eth0` and `192.0.2.0/24` with the real external interface and source range you want to allow.

**3. Set your LLM provider API key**

Copy the example environment file and add your key:

```bash
cp .env.example .env
```

Then open `.env` and replace the placeholder:

```
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-your-key-here
OPENAI_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-4-6
OPENAI_MODEL=gpt-4.1-mini
ALLOW_BROWSER_API_KEY=false
TORCH_NUM_THREADS=
```

The `.env` file is listed in `.gitignore` and will never be committed to git.
Never paste your API key directly into any Python file or a crontab line.
Set `LLM_PROVIDER=openai` and `OPENAI_API_KEY=...` if you want to use OpenAI instead.
Leave `ALLOW_BROWSER_API_KEY=false` unless you explicitly want the sidebar key
field and you trust the connection your collaborators are using.
Set `TORCH_NUM_THREADS` (e.g. `2`) to cap CPU threads used during embedding —
useful if ingestion is spinning up laptop fans. Leave it blank to use all
available cores (fastest, but hottest). This applies whether you run
`ingest.py` directly or inside Docker (via `--env-file .env`).

---

## Building the index

Run the ingestion pipeline once before launching the assistant:

```bash
python ingest.py
```

This will:

1. Clone the default `macros` and `coresoftware` repositories into `./repos/`
2. Parse all relevant source files
3. Split files into overlapping text chunks (~600 tokens each)
4. Embed each chunk using `BAAI/bge-large-en-v1.5` (a strong scientific text model)
5. Save a FAISS vector index to `./index/sphenix.index`
6. Save chunk metadata to `./index/chunks.json`
7. Record the current git commit hash for each repo in `./index/state.json`

**First run:** typically a few minutes depending on your connection and hardware.

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

**Google Colab / Jupyter notebook:**

The retrieval and answer-generation backend is not tied to Streamlit. You can run
the same assistant in Google Colab with a notebook-native UI:

```python
!git clone https://github.com/<your-org>/sPHENIX_WorkflowAssistant.git
%cd sPHENIX_WorkflowAssistant
```

```python
!pip install -r requirements.txt
```

```python
!python ingest.py
```

```python
from colab_app import create_session

session = create_session(provider="openai")
session.launch()
```

`create_session()` resolves credentials in this order:

1. `api_key=...` and optional `provider=...` passed in code
2. environment variables `LLM_PROVIDER` plus the matching provider key
3. Google Colab secrets named `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`
4. an interactive password prompt

If you prefer one-off notebook calls instead of the widget chat UI:

```python
from colab_app import ask_once

ask_once(
    "How do I run a Fun4All macro for calorimeter simulation?",
    provider="openai",
)
```

**Command-line interface:**

```bash
python rag.py "How do I run the HCAL tower calibration macro?"
python rag.py "Generate a Fun4All steering macro for Au+Au GEANT4 simulation"
```

---

## Keeping the index current

The index does not update automatically. To stay current with the latest commits
across the default indexed repositories, schedule a nightly update using cron:

```bash
crontab -e
```

Add this line (adjust paths to match your installation):

```
0 2 * * * cd /path/to/sPHENIX_WorkflowAssistant && source .venv/bin/activate && python ingest.py >> /tmp/sphenix-rag-ingest.log 2>&1
```

This runs at 2:00 AM every night. The log file at `/tmp/sphenix-rag-ingest.log`
records what changed. On nights when no repositories have new commits, the run
completes in under 10 seconds.

---

## Project structure

```
sPHENIX_WorkflowAssistant/
├── ingest.py         # Ingestion pipeline: clone, parse, embed, index
├── rag.py            # Query engine: retrieve + generate via Anthropic or OpenAI
├── app.py            # Streamlit web interface
├── colab_app.py      # Notebook / Google Colab interface
├── requirements.txt  # Python dependencies
├── .env.example      # Template for your API key (copy to .env)
├── .gitignore        # Keeps local caches, indexes, and secrets out of git
│
├── repos/            # Cloned sPHENIX repositories (auto-created, not committed)
│   ├── macros/
│   └── coresoftware/
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
chunks are sent to the configured LLM provider API to generate an answer. All retrieved content
comes from the public sPHENIX GitHub repositories.

**Important:** Do not paste unpublished physics results, proprietary analysis code,
internal configuration files, BNL credentials, or any non-public information into
this tool. The assistant is designed for questions about the public sPHENIX software
stack only.

Review the retention and training policy for whichever provider you configure before
sharing the app with collaborators.

**What stays local:**
The FAISS index, all cloned repository files, and the embedding model run entirely
on your machine. No data is sent to any external service during ingestion.

---

## Extending the assistant

**Add the other sPHENIX repositories (optional):**

This repository defaults to `macros` and `coresoftware`. If you later want to
also index `tutorials`, `analysis`, and `Singularity`, edit the `REPOS` and
`INCLUDE_EXTENSIONS` dictionaries in `ingest.py` so they look like this:

```python
REPOS = {
    "macros":       "https://github.com/sPHENIX-Collaboration/macros.git",
    "tutorials":    "https://github.com/sPHENIX-Collaboration/tutorials.git",
    "coresoftware": "https://github.com/sPHENIX-Collaboration/coresoftware.git",
    "analysis":     "https://github.com/sPHENIX-Collaboration/analysis.git",
    "Singularity":  "https://github.com/sPHENIX-Collaboration/Singularity.git",
}

INCLUDE_EXTENSIONS = {
    "macros":       {".C", ".h", ".py", ".md", ".sh"},
    "tutorials":    {".ipynb", ".md", ".C", ".py"},
    "coresoftware": {".md", ".h", ".C"},
    "analysis":     {".C", ".h", ".py", ".md"},
    "Singularity":  {".sh", ".md"},
}
```

Then refresh the index:

- local Python install: run `python ingest.py`
- Docker install: rebuild the ingest image and rerun the hardened ingestion container

This expansion can be noticeably more CPU-intensive and take longer than the
default two-repository setup, especially on the first full run.

**Add more public repositories beyond those defaults:**

To index an entirely new public repository, extend the same dictionaries with a
new entry and then rerun ingestion.

**Add local documentation:**

Place `.md` or plain text files in a `local_docs/` directory. In `ingest.py`,
add a loop that reads files from that directory and passes them through the same
`chunk_text()` → `embed_and_add()` pipeline. These files are never committed to
git.

**Change the language model:**

Set `ANTHROPIC_MODEL` or `OPENAI_MODEL` in `.env` to override the default model
for that provider. You can also change the defaults directly in `rag.py`.

**Change the number of retrieved chunks:**

Adjust `TOP_K` in `rag.py` (default: 8). Higher values give the model more context
but increase API cost and latency. For complex multi-step questions, increasing to
12–16 can improve answer quality.

---

## Sharing with collaborators

By default the assistant runs on your local machine. To share it:

**Streamlit Community Cloud (free, simplest):**
Push this repository to GitHub, connect it at
[share.streamlit.io](https://share.streamlit.io), and add `LLM_PROVIDER` plus the
matching `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` as secrets in the Streamlit dashboard.
Collaborators get a public URL. Note that
all queries will use your API key.

**Collaborator-supplied API keys:**
The sidebar in the web interface includes an optional field where each collaborator
can paste their own Anthropic or OpenAI API key. When a key is entered there, it is used for
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

**`ANTHROPIC_API_KEY is not set` or `OPENAI_API_KEY is not set`**
Ensure your `.env` file exists, `LLM_PROVIDER` matches the provider you want, and
the corresponding API key is present in the same directory as `rag.py` and `app.py`.

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
