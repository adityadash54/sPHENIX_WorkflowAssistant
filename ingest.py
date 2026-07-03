"""
ingest.py — sPHENIX RAG Ingestion Pipeline (Incremental)

On the FIRST run:
  - Clones all 5 sPHENIX repos
  - Parses, chunks, embeds every relevant file
  - Saves a FAISS IndexIDMap + state.json (commit hashes + file→chunk ID map)
  - Saves chunks as chunks.json (safe, no pickle)

On SUBSEQUENT runs:
  - Pulls each repo
  - Compares new HEAD commit to the last-indexed commit
  - Only re-embeds files that were added, modified, or deleted
  - Removes stale chunks from the index; adds new ones
  - Updates state.json

Usage:
    python ingest.py              # incremental update (or full build if first run)
    python ingest.py --full       # force a full rebuild from scratch
"""

import argparse
import json
import sys
import nbformat
import numpy as np
from pathlib import Path
from tqdm import tqdm
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import git
import faiss
from sentence_transformers import SentenceTransformer

console = Console()

# ── Config ────────────────────────────────────────────────────────────────────

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
    "coresoftware": {".md", ".h", ".C"},   # headers + READMEs only (huge repo)
    "analysis":     {".C", ".h", ".py", ".md"},
    "Singularity":  {".sh", ".md"},
}

SHALLOW_REPOS = {"coresoftware"}   # only index top 3 dir levels

CLONE_DIR   = Path("./repos")
INDEX_DIR   = Path("./index")
STATE_FILE  = INDEX_DIR / "state.json"
CHUNKS_FILE = INDEX_DIR / "chunks.json"    # FIX: JSON instead of pickle
FAISS_FILE  = INDEX_DIR / "sphenix.index"

EMBED_MODEL   = "BAAI/bge-large-en-v1.5"
CHUNK_SIZE    = 600    # tokens (~4 chars each)
CHUNK_OVERLAP = 100

# Maximum file size to parse (avoids reading huge generated files)
MAX_FILE_BYTES = 500_000   # 500 KB


# ── .gitignore guard ──────────────────────────────────────────────────────────

def ensure_gitignore():
    """
    Write a .gitignore so repos/ and index/ are never accidentally
    committed to git (they're large and may contain cached source code).
    """
    gitignore = Path(".gitignore")
    lines_needed = {"repos/", "index/", ".env", "__pycache__/", "*.pyc"}
    existing = set()
    if gitignore.exists():
        existing = set(gitignore.read_text().splitlines())
    missing = lines_needed - existing
    if missing:
        with gitignore.open("a") as f:
            f.write("\n" + "\n".join(sorted(missing)) + "\n")
        console.print(f"[green]✓[/green] Updated .gitignore")


# ── State helpers ─────────────────────────────────────────────────────────────

def load_state() -> dict:
    """
    state.json schema:
    {
      "commit_hashes": {"macros": "abc123", ...},
      "file_chunk_ids": {"macros/sim/G4.C": [0, 1, 2], ...},
      "next_id": 1234
    }
    """
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"commit_hashes": {}, "file_chunk_ids": {}, "next_id": 0}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── Chunk storage (JSON, not pickle) ─────────────────────────────────────────
# FIX: pickle.load() executes arbitrary code if the file is tampered with.
# JSON is safe — it only deserialises data, never code.

def load_chunks() -> list[dict]:
    if CHUNKS_FILE.exists():
        return json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
    return []


def save_chunks(chunks: list[dict]):
    CHUNKS_FILE.write_text(
        json.dumps(chunks, ensure_ascii=False, indent=None),
        encoding="utf-8"
    )


# ── Parsing helpers ───────────────────────────────────────────────────────────

def parse_notebook(path: Path) -> str:
    try:
        nb = nbformat.read(str(path), as_version=4)
        parts = []
        for cell in nb.cells:
            if cell.cell_type in ("code", "markdown"):
                src = cell.source.strip()
                if src:
                    tag = "# CODE\n" if cell.cell_type == "code" else "# MARKDOWN\n"
                    parts.append(tag + src)
        return "\n\n".join(parts)
    except Exception:
        return ""


def parse_file(path: Path) -> str:
    # FIX: skip files that are too large to avoid memory issues
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            console.print(f"[dim]  Skipping large file: {path.name} "
                          f"({path.stat().st_size // 1024} KB)[/dim]")
            return ""
    except OSError:
        return ""

    if path.suffix == ".ipynb":
        return parse_notebook(path)
    try:
        return path.read_text(errors="ignore")
    except Exception:
        return ""


def chunk_text(text: str, source: str,
               chunk_size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> list[dict]:
    cs = chunk_size * 4
    ov = overlap * 4
    chunks, start, idx = [], 0, 0
    while start < len(text):
        end   = min(start + cs, len(text))
        chunk = text[start:end].strip()
        if len(chunk) > 50:
            chunks.append({"text": chunk, "source": source, "chunk_id": idx})
        start += cs - ov
        idx   += 1
    return chunks


def collect_all_files(repo_path: Path, repo_name: str) -> list[Path]:
    exts = INCLUDE_EXTENSIONS.get(repo_name, set())
    result = []
    for p in repo_path.rglob("*"):
        if not p.is_file() or p.suffix not in exts:
            continue
        if repo_name in SHALLOW_REPOS:
            depth = len(p.relative_to(repo_path).parts)
            if depth > 3:
                continue
        result.append(p)
    return result


# ── Git helpers ───────────────────────────────────────────────────────────────

def clone_or_pull(name: str, url: str) -> tuple[Path, git.Repo]:
    dest = CLONE_DIR / name
    if dest.exists():
        console.print(f"[yellow]↺[/yellow] Pulling {name}...")
        repo = git.Repo(dest)
        try:
            repo.remotes.origin.pull()
        except Exception as e:
            console.print(f"[red]  Pull failed ({e}), using cached version[/red]")
    else:
        console.print(f"[green]↓[/green] Cloning {name}...")
        repo = git.Repo.clone_from(url, dest, depth=50)
    return dest, repo


def get_changed_files(repo: git.Repo, old_commit: str,
                      repo_name: str) -> tuple[list[str], list[str]]:
    try:
        old  = repo.commit(old_commit)
        new  = repo.head.commit
        diff = old.diff(new)

        changed, deleted = [], []
        exts = INCLUDE_EXTENSIONS.get(repo_name, set())

        for d in diff:
            if d.change_type == "D":
                if Path(d.a_path).suffix in exts:
                    deleted.append(f"{repo_name}/{d.a_path}")
            else:
                if d.change_type == "R":
                    if Path(d.a_path).suffix in exts:
                        deleted.append(f"{repo_name}/{d.a_path}")
                new_path = d.b_path or d.a_path
                if Path(new_path).suffix in exts:
                    changed.append(f"{repo_name}/{new_path}")

        return changed, deleted

    except Exception as e:
        console.print(f"[red]  Could not diff {repo_name}: {e}[/red]")
        return [], []


# ── Index operations ──────────────────────────────────────────────────────────

def load_index() -> faiss.Index:
    return faiss.read_index(str(FAISS_FILE))


def save_index(index: faiss.Index):
    faiss.write_index(index, str(FAISS_FILE))


def build_id_lookup(chunks: list[dict]) -> dict[int, int]:
    """
    FIX (critical bug): Build a mapping from global_id → list position.

    Previously rag.py did _chunks[idx] where idx was a FAISS global_id,
    not a list index. After incremental updates these diverge, causing
    wrong results or IndexError crashes. This lookup fixes that.
    """
    return {c["global_id"]: i for i, c in enumerate(chunks)}


def remove_chunks_by_ids(index: faiss.Index,
                          chunks: list[dict],
                          ids_to_remove: list[int]) -> list[dict]:
    if not ids_to_remove:
        return chunks
    id_arr = np.array(ids_to_remove, dtype=np.int64)
    sel    = faiss.IDSelectorArray(len(id_arr), faiss.swig_ptr(id_arr))
    index.remove_ids(sel)
    id_set = set(ids_to_remove)
    return [c for c in chunks if c["global_id"] not in id_set]


def embed_and_add(model: SentenceTransformer,
                  index: faiss.Index,
                  new_chunks: list[dict]) -> None:
    if not new_chunks:
        return
    texts      = [c["text"] for c in new_chunks]
    embeddings = model.encode(
        texts, batch_size=64, show_progress_bar=False, normalize_embeddings=True,
    ).astype("float32")
    ids = np.array([c["global_id"] for c in new_chunks], dtype=np.int64)
    index.add_with_ids(embeddings, ids)


# ── Full build ────────────────────────────────────────────────────────────────

def full_build(repos: dict, model: SentenceTransformer) -> tuple:
    console.print("\n[bold cyan]Full build — indexing all files[/bold cyan]")

    all_chunks: list[dict] = []
    state = {"commit_hashes": {}, "file_chunk_ids": {}, "next_id": 0}
    gid   = 0

    for repo_name, (repo_path, repo) in repos.items():
        files = collect_all_files(repo_path, repo_name)
        console.print(f"  [bold]{repo_name}[/bold]: {len(files)} files")

        for fpath in tqdm(files, desc="    Parsing", leave=False):
            text = parse_file(fpath)
            if not text.strip():
                continue
            rel    = f"{repo_name}/{fpath.relative_to(repo_path)}"
            chunks = chunk_text(text, source=rel)
            ids    = []
            for c in chunks:
                c["global_id"] = gid
                ids.append(gid)
                gid += 1
            all_chunks.extend(chunks)
            state["file_chunk_ids"][rel] = ids

        state["commit_hashes"][repo_name] = repo.head.commit.hexsha

    state["next_id"] = gid
    console.print(f"\n  Total chunks: [green]{len(all_chunks):,}[/green]")

    console.print(f"\n[cyan]Embedding {len(all_chunks):,} chunks...[/cyan]")
    texts      = [c["text"] for c in all_chunks]
    embeddings = model.encode(
        texts, batch_size=64, show_progress_bar=True, normalize_embeddings=True,
    ).astype("float32")

    dim   = embeddings.shape[1]
    inner = faiss.IndexFlatIP(dim)
    index = faiss.IndexIDMap(inner)
    ids   = np.array([c["global_id"] for c in all_chunks], dtype=np.int64)
    index.add_with_ids(embeddings, ids)

    return index, all_chunks, state


# ── Incremental update ────────────────────────────────────────────────────────

def incremental_update(repos: dict, model: SentenceTransformer,
                        state: dict) -> tuple:
    console.print("\n[bold cyan]Incremental update[/bold cyan]")

    index  = load_index()
    chunks = load_chunks()
    gid    = state["next_id"]

    summary = Table(title="Changes detected", show_header=True)
    summary.add_column("Repo")
    summary.add_column("Changed", style="yellow")
    summary.add_column("Deleted", style="red")
    summary.add_column("New commit")

    for repo_name, (repo_path, repo) in repos.items():
        new_commit = repo.head.commit.hexsha
        old_commit = state["commit_hashes"].get(repo_name)

        if old_commit == new_commit:
            summary.add_row(repo_name, "0", "0", new_commit[:8] + " (no change)")
            continue

        if not old_commit:
            changed_rel = [
                f"{repo_name}/{f.relative_to(repo_path)}"
                for f in collect_all_files(repo_path, repo_name)
            ]
            deleted_rel = []
        else:
            changed_rel, deleted_rel = get_changed_files(repo, old_commit, repo_name)

        to_remove_paths = set(deleted_rel) | set(changed_rel)
        ids_to_remove   = []
        for rel in to_remove_paths:
            ids_to_remove.extend(state["file_chunk_ids"].pop(rel, []))

        chunks = remove_chunks_by_ids(index, chunks, ids_to_remove)

        new_chunks: list[dict] = []
        for rel in changed_rel:
            abs_path = CLONE_DIR / rel
            if not abs_path.exists():
                continue
            text = parse_file(abs_path)
            if not text.strip():
                continue
            file_chunks = chunk_text(text, source=rel)
            file_ids    = []
            for c in file_chunks:
                c["global_id"] = gid
                file_ids.append(gid)
                gid += 1
            new_chunks.extend(file_chunks)
            state["file_chunk_ids"][rel] = file_ids

        embed_and_add(model, index, new_chunks)
        chunks.extend(new_chunks)

        state["commit_hashes"][repo_name] = new_commit
        summary.add_row(
            repo_name,
            str(len(changed_rel)),
            str(len(deleted_rel)),
            new_commit[:8],
        )

    state["next_id"] = gid
    console.print(summary)
    console.print(f"\n  Index now contains [green]{index.ntotal:,}[/green] vectors")

    return index, chunks, state


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="sPHENIX RAG ingestion pipeline")
    parser.add_argument("--full", action="store_true",
                        help="Force a full rebuild from scratch")
    args = parser.parse_args()

    CLONE_DIR.mkdir(exist_ok=True)
    INDEX_DIR.mkdir(exist_ok=True)
    ensure_gitignore()

    console.print(Panel(
        "[bold cyan]sPHENIX RAG — Ingestion Pipeline[/bold cyan]\n"
        "Tracking latest commits from github.com/sPHENIX-Collaboration",
        border_style="cyan"
    ))

    repos: dict = {}
    for name, url in REPOS.items():
        path, repo = clone_or_pull(name, url)
        repos[name] = (path, repo)

    console.print(f"\n[cyan]Loading embedding model:[/cyan] {EMBED_MODEL}")
    model = SentenceTransformer(EMBED_MODEL)

    index_exists = FAISS_FILE.exists()
    state        = load_state()

    if args.full or not index_exists:
        if args.full:
            console.print("[yellow]--full flag set: rebuilding from scratch[/yellow]")
        index, chunks, state = full_build(repos, model)
    else:
        index, chunks, state = incremental_update(repos, model, state)

    save_index(index)
    save_chunks(chunks)
    save_state(state)

    console.print(Panel(
        "[bold green]✓ Index is up to date![/bold green]\n\n"
        f"Vectors in index : [cyan]{index.ntotal:,}[/cyan]\n"
        f"Chunks tracked   : [cyan]{len(chunks):,}[/cyan]\n"
        f"State saved to   : [cyan]{STATE_FILE}[/cyan]\n\n"
        "Run [yellow]streamlit run app.py[/yellow] to launch the assistant.\n"
        "Run [yellow]python ingest.py --full[/yellow] to force a complete rebuild.",
        border_style="green"
    ))


if __name__ == "__main__":
    main()
