"""
colab_app.py — Notebook/Google Colab UI for the sPHENIX Workflow Assistant

Usage in Colab:
    from colab_app import create_session
    session = create_session(provider="openai")
    session.launch()

Or ask one question at a time:
    from colab_app import ask_once
    ask_once("How do I run a Fun4All macro for calorimeter simulation?")
"""

from __future__ import annotations

import os
from typing import Any

from rag import (
    provider_env_var,
    query,
    resolve_api_credentials,
    resolve_provider,
)

MAX_HISTORY_MESSAGES = 8
DEFAULT_EXAMPLES = [
    "How do I run a Fun4All macro for calorimeter simulation?",
    "Generate a skeleton steering macro for HCAL reconstruction.",
    "How do I set up the Singularity container for sPHENIX?",
    "What is the workflow for running the TPC track reconstruction?",
]


def _display_markdown(text: str) -> None:
    from IPython.display import Markdown, display

    display(Markdown(text))


def _display_preformatted(text: str) -> None:
    from IPython.display import Markdown, display

    display(Markdown(f"```text\n{text}\n```"))


def _load_colab_secret(name: str) -> str | None:
    try:
        from google.colab import userdata  # type: ignore
    except ImportError:
        return None

    try:
        value = userdata.get(name)
    except Exception:
        return None

    return value or None


def resolve_notebook_credentials(
    api_key: str | None = None,
    provider: str | None = None,
    prompt_if_missing: bool = True,
) -> tuple[str, str]:
    """Resolve provider + API key from args, env, Colab secrets, or prompt."""
    if api_key:
        resolved_provider, resolved_api_key = resolve_api_credentials(
            api_key=api_key,
            provider=provider,
        )
        os.environ[provider_env_var(resolved_provider)] = resolved_api_key
        return resolved_provider, resolved_api_key

    try:
        resolved_provider = resolve_provider(provider=provider)
    except EnvironmentError:
        resolved_provider = provider
    except ValueError:
        raise
    else:
        try:
            _, resolved_api_key = resolve_api_credentials(provider=resolved_provider)
            return resolved_provider, resolved_api_key
        except EnvironmentError:
            pass

    if provider:
        candidate_providers = [provider]
    else:
        candidate_providers = ["anthropic", "openai"]

    for candidate in candidate_providers:
        env_name = provider_env_var(candidate)
        resolved = os.environ.get(env_name) or _load_colab_secret(env_name)
        if resolved:
            os.environ[env_name] = resolved
            return candidate, resolved

    if not prompt_if_missing:
        raise EnvironmentError(
            "No notebook API key is configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY, "
            "store one as a Colab secret, or pass api_key=..."
        )

    try:
        from getpass import getpass
    except ImportError as exc:
        raise EnvironmentError(
            "No notebook API key is configured."
        ) from exc

    entered = getpass("Anthropic or OpenAI API key: ").strip()
    if not entered:
        raise EnvironmentError("An API key is required.")

    resolved_provider = resolve_provider(api_key=entered, provider=provider)
    os.environ[provider_env_var(resolved_provider)] = entered
    return resolved_provider, entered


def render_result(result: dict[str, Any], show_sources: bool = True) -> None:
    """Render a query result in a notebook-friendly format."""
    _display_markdown("### Assistant")
    _display_markdown(result["answer"])

    if show_sources and result.get("sources"):
        sources = "\n".join(f"- `{source}`" for source in result["sources"])
        _display_markdown(f"**Sources consulted**\n{sources}")


class ColabChatSession:
    """Stateful notebook chat wrapper around rag.query()."""

    def __init__(self,
                 api_key: str | None = None,
                 provider: str | None = None,
                 max_history_messages: int = MAX_HISTORY_MESSAGES,
                 show_sources: bool = True):
        self.provider, self.api_key = resolve_notebook_credentials(
            api_key=api_key,
            provider=provider,
        )
        self.max_history_messages = max_history_messages
        self.show_sources = show_sources
        self.messages: list[dict[str, Any]] = []

    def ask(self,
            question: str,
            show_sources: bool | None = None,
            render: bool = True) -> dict[str, Any]:
        question = question.strip()
        if not question:
            raise ValueError("Question must not be empty.")

        history_window = self.messages[-self.max_history_messages:]
        history = [
            {"role": message["role"], "content": message["content"]}
            for message in history_window
        ]

        result = query(
            question,
            history=history,
            api_key=self.api_key,
            provider=self.provider,
        )
        self.messages.append({"role": "user", "content": question})
        self.messages.append({
            "role": "assistant",
            "content": result["answer"],
            "sources": result["sources"],
            "chunks": result["chunks"],
        })

        if render:
            _display_markdown(f"### You\n{question}")
            render_result(
                result,
                show_sources=self.show_sources if show_sources is None else show_sources,
            )

        return result

    def launch(self) -> Any:
        """Launch a lightweight widget chat interface inside Jupyter/Colab."""
        try:
            import ipywidgets as widgets
            from IPython.display import clear_output, display
        except ImportError as exc:
            raise ImportError(
                "ipywidgets is required for launch(). Install it with "
                "`pip install ipywidgets`."
            ) from exc

        title = widgets.HTML(
            "<h2>sPHENIX Workflow Assistant</h2>"
            "<p>Ask about sPHENIX software, macros, calibration workflows, and Fun4All.</p>"
        )
        examples = widgets.HTML(
            "<b>Example questions</b><ul>"
            + "".join(f"<li>{example}</li>" for example in DEFAULT_EXAMPLES)
            + "</ul>"
        )
        prompt = widgets.Textarea(
            placeholder="Ask about sPHENIX workflows, macros, calibration, Fun4All...",
            layout=widgets.Layout(width="100%", height="120px"),
        )
        ask_button = widgets.Button(
            description="Ask",
            button_style="primary",
            icon="paper-plane",
        )
        show_sources = widgets.Checkbox(
            value=self.show_sources,
            description="Show sources",
        )
        status = widgets.HTML("")
        output = widgets.Output()

        def submit(_: Any) -> None:
            question = prompt.value.strip()
            if not question:
                status.value = "<span style='color:#b91c1c'>Enter a question.</span>"
                return

            status.value = "<span>Retrieving from indexed repos and generating answer...</span>"
            ask_button.disabled = True

            try:
                with output:
                    self.ask(question, show_sources=show_sources.value, render=True)
                    display(widgets.HTML("<hr>"))
                prompt.value = ""
                status.value = ""
            except Exception as exc:
                with output:
                    clear_output(wait=False)
                    _display_markdown("### Error")
                    _display_preformatted(str(exc))
                status.value = "<span style='color:#b91c1c'>Request failed.</span>"
            finally:
                ask_button.disabled = False

        ask_button.on_click(submit)

        container = widgets.VBox([
            title,
            examples,
            prompt,
            widgets.HBox([ask_button, show_sources]),
            status,
            output,
        ])
        display(container)
        return container


def create_session(api_key: str | None = None,
                   provider: str | None = None,
                   show_sources: bool = True) -> ColabChatSession:
    """Create a notebook chat session with message history."""
    return ColabChatSession(
        api_key=api_key,
        provider=provider,
        show_sources=show_sources,
    )


def ask_once(question: str,
             api_key: str | None = None,
             provider: str | None = None,
             show_sources: bool = True) -> dict[str, Any]:
    """Ask a single question without creating a persistent session explicitly."""
    session = create_session(
        api_key=api_key,
        provider=provider,
        show_sources=show_sources,
    )
    return session.ask(question, show_sources=show_sources, render=True)
