"""
app.py — sPHENIX Workflow Assistant (Streamlit UI)

Run with:  streamlit run app.py
API key:   add ANTHROPIC_API_KEY or OPENAI_API_KEY to a .env file
"""

import html
import os
import threading
import time
import streamlit as st
from dotenv import load_dotenv
from rag import query, resolve_api_credentials
from runtime_config import ENV_FILE

load_dotenv(ENV_FILE)

# ── Rate limiting ─────────────────────────────────────────────────────────────
# Caps total queries against the shared server-side API key within a sliding
# window. Deliberately process-global (not st.session_state): session state
# is wiped by a page reload or a new browser tab, which would let anyone
# reset their own quota for free. A global counter can't be bypassed that
# way, at the cost of the limit being shared across all concurrent users
# rather than enforced per-person — acceptable here since there is no login
# system to key a per-user limit off of.
MAX_QUERIES     = 20
WINDOW_SECONDS  = 3600   # 1 hour
MAX_HISTORY_MESSAGES = 8

_query_times: list[float] = []
_query_times_lock = threading.Lock()


def _env_flag(name: str, default: bool = False) -> bool:
    """Parse a boolean feature flag from the environment."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


ALLOW_BROWSER_API_KEY = _env_flag("ALLOW_BROWSER_API_KEY", default=False)


def _default_browser_provider() -> str:
    configured = os.environ.get("LLM_PROVIDER")
    if configured in {"anthropic", "openai"}:
        return configured
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "anthropic"

def _check_rate_limit() -> bool:
    """Return True if within the global rate limit, False if exceeded."""
    now = time.time()
    with _query_times_lock:
        _query_times[:] = [t for t in _query_times if now - t < WINDOW_SECONDS]
        if len(_query_times) >= MAX_QUERIES:
            return False
        _query_times.append(now)
        return True


# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="sPHENIX Workflow Assistant",
    page_icon="⚛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .stApp { background: #0d1117; color: #e6edf3; }
  .main .block-container { padding-top: 2rem; max-width: 900px; }
  .source-pill {
    display: inline-block;
    background: #21262d;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 0.75rem;
    font-family: monospace;
    color: #58a6ff;
    margin: 2px;
  }
  .disclaimer {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 10px 14px;
    font-size: 0.78rem;
    color: #8b949e;
    margin-bottom: 1rem;
  }
  .rag-header { text-align: center; padding: 1rem 0 0.5rem; }
  .rag-header h1 { color: #58a6ff; font-size: 1.8rem; margin-bottom: 0; }
  .rag-header p  { color: #8b949e; font-size: 0.9rem; }
</style>
""", unsafe_allow_html=True)

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚛️ sPHENIX RAG")
    st.markdown(
        "Ask anything about sPHENIX software, macros, "
        "calibration workflows, and Fun4All."
    )

    st.divider()
    st.markdown("### Indexed repositories")
    for repo in ["macros", "tutorials", "coresoftware", "analysis", "Singularity"]:
        st.markdown(
            f"- [`sPHENIX-Collaboration/{repo}`]"
            f"(https://github.com/sPHENIX-Collaboration/{repo})"
        )

    st.divider()
    st.markdown("### Example questions")
    examples = [
        "How do I run a Fun4All macro for calorimeter simulation?",
        "Generate a skeleton steering macro for HCAL reconstruction",
        "How do I set up the Singularity container for sPHENIX?",
        "What is the workflow for running the TPC track reconstruction?",
        "How do I add a custom analysis module to Fun4All?",
        "What calibration constants does the EMCal need?",
    ]
    for ex in examples:
        if st.button(ex, key=ex, use_container_width=True):
            st.session_state["pending_query"] = ex

    st.divider()
    show_sources = st.toggle("Show retrieved source chunks", value=False)

    # Optional: allow user to supply their own API key in the UI.
    # Disabled by default because pasting secrets into a browser is only
    # appropriate on localhost or a trusted HTTPS deployment.
    st.divider()
    user_key = ""
    user_provider = _default_browser_provider()
    if ALLOW_BROWSER_API_KEY:
        st.markdown("### API key (optional)")
        st.caption(
            "Only paste a personal API key on localhost or over a trusted HTTPS deployment. "
            "Do not enter it into a plain HTTP page on a shared network."
        )
        user_provider = st.radio(
            "Provider",
            options=["anthropic", "openai"],
            horizontal=True,
            format_func=lambda value: "Anthropic" if value == "anthropic" else "OpenAI",
        )
        user_key = st.text_input(
            "Paste your Anthropic or OpenAI API key to use your own account",
            type="password",
            help="If left blank, the server's configured key is used. Only enter your own key on a trusted connection.",
        )
    else:
        st.markdown("### API key")
        st.caption(
            "Browser-pasted API keys are disabled by default. Set "
            "`ALLOW_BROWSER_API_KEY=true` only for localhost or a trusted HTTPS deployment."
        )

# ── Session state ──────────────────────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state["messages"] = []
if "pending_query" not in st.session_state:
    st.session_state["pending_query"] = ""

# ── Header ─────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="rag-header">
  <h1>⚛️ sPHENIX Workflow Assistant</h1>
  <p>RAG-powered assistant grounded in sPHENIX macros, tutorials, and coresoftware</p>
</div>
""", unsafe_allow_html=True)

# FIX: show data disclaimer prominently so collaborators know what leaves their machine
st.markdown("""
<div class="disclaimer">
  ⚠️ <strong>Data notice:</strong> Your questions and retrieved code snippets are sent
  to the configured LLM provider API to generate answers. All content is from public sPHENIX GitHub
  repositories. <strong>Do not paste unpublished results, internal BNL data, or
  proprietary information into this tool.</strong>
</div>
""", unsafe_allow_html=True)

# ── Chat history ───────────────────────────────────────────────────────────────
for msg in st.session_state["messages"]:
    if msg["role"] == "user":
        with st.chat_message("user"):
            st.markdown(msg["content"])
    else:
        with st.chat_message("assistant"):
            st.markdown(msg["content"])
            if msg.get("sources"):
                st.markdown("**Sources consulted:**")
                pills = "".join(
                    f'<span class="source-pill">{html.escape(s)}</span>'
                    for s in msg["sources"]
                )
                st.markdown(pills, unsafe_allow_html=True)
            if show_sources and msg.get("chunks"):
                with st.expander("Retrieved context chunks"):
                    for i, c in enumerate(msg["chunks"]):
                        st.markdown(
                            f"**Chunk {i+1}** — `{c['source']}` "
                            f"(score: {c['score']:.3f})"
                        )
                        st.code(c["text"][:800], language="cpp")

# ── Input ──────────────────────────────────────────────────────────────────────
pending    = st.session_state.pop("pending_query", "")
user_input = st.chat_input("Ask about sPHENIX workflows, macros, calibration, Fun4All...")
if pending:
    user_input = pending

if user_input:
    effective_api_key = user_key or None
    effective_provider = user_provider if user_key else None

    # Only throttle usage of the server's shared key — a collaborator using
    # their own key is spending their own quota, not the shared one.
    if not user_key and not _check_rate_limit():
        st.warning(
            f"This assistant has received {MAX_QUERIES} queries in the last "
            "hour, which is its shared limit. Please wait before sending more."
        )
        st.stop()

    # Check API key availability
    try:
        resolve_api_credentials(
            api_key=effective_api_key,
            provider=effective_provider,
        )
    except EnvironmentError:
        st.error(
            "No API key configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY on the server."
        )
        st.stop()

    # Enforce input length
    if len(user_input) > 2000:
        st.warning("Query truncated to 2000 characters.")
        user_input = user_input[:2000]

    with st.chat_message("user"):
        st.markdown(user_input)
    st.session_state["messages"].append({"role": "user", "content": user_input})

    history_window = st.session_state["messages"][:-1][-MAX_HISTORY_MESSAGES:]
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in history_window
    ]

    with st.chat_message("assistant"):
        with st.spinner("Retrieving from sPHENIX repos and generating answer..."):
            try:
                result = query(
                    user_input,
                    history=history,
                    api_key=effective_api_key,
                    provider=effective_provider,
                )
            except FileNotFoundError:
                # FIX: sanitised error — don't expose host file paths
                st.error(
                    "The search index is not ready. "
                    "Ask the administrator to run `python ingest.py`."
                )
                st.stop()
            except (EnvironmentError, ValueError):
                # FIX: don't expose the actual key or its absence details
                st.error(
                    "API key configuration error. "
                    "Contact the administrator."
                )
                st.stop()
            except Exception:
                # FIX: catch-all — never show raw exception to users
                st.error(
                    "An unexpected error occurred. Please try again "
                    "or rephrase your question."
                )
                st.stop()

        st.markdown(result["answer"])

        if result["sources"]:
            st.markdown("**Sources consulted:**")
            pills = "".join(
                f'<span class="source-pill">{html.escape(s)}</span>'
                for s in result["sources"]
            )
            st.markdown(pills, unsafe_allow_html=True)

        if show_sources and result["chunks"]:
            with st.expander("Retrieved context chunks"):
                for i, c in enumerate(result["chunks"]):
                    st.markdown(
                        f"**Chunk {i+1}** — `{c['source']}` "
                        f"(score: {c['score']:.3f})"
                    )
                    st.code(c["text"][:800], language="cpp")

    st.session_state["messages"].append({
        "role":    "assistant",
        "content": result["answer"],
        "sources": result["sources"],
        "chunks":  result["chunks"],
    })
