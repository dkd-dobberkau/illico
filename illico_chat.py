"""
illico chat — befragt die kompilierte Wiki ohne Vektordatenbank.
Das LLM navigiert die Wiki direkt anhand des Index.

Usage:
    python chat.py
    python chat.py --data ./illico-data
    python chat.py --model claude-sonnet-4-6
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
import typer
from rich.console import Console

import illico_llm
from illico_graph import load_graph_data, expand_with_graph, build_graph_context
from illico_chat_core import (
    SYSTEM_PROMPT,
    answer_sync,
    get_index,
    load_wiki,
    route,
)
from rich.panel import Panel
from rich.markdown import Markdown
from rich.prompt import Prompt
app = typer.Typer()
console = Console()


def format_sources(relevant_articles: list[str]) -> str:
    """Formatiert die Quellenangabe."""
    if not relevant_articles:
        return ""
    names = [f.replace(".md", "") for f in relevant_articles if not f.startswith("_")]
    return f"\n[dim]Durchsuchte Artikel: {', '.join(names)}[/dim]"


@app.command()
def chat(
    data: Path = typer.Option(Path(os.environ.get("ILLICO_DATA", "./illico-data")), "--data", "-d", help="Illico-Datenverzeichnis"),
    model: str = typer.Option(None, "--model", "-m", help="LLM-Modell (default: ILLICO_ANSWER_MODEL env)"),
):
    """
    Interaktiver Chat über die kompilierte Illico-Wiki.
    """
    wiki_dir = data / "wiki"
    effective_model = model or illico_llm.ANSWER_MODEL

    if not wiki_dir.exists() or not any(wiki_dir.glob("*.md")):
        console.print(f"[red]✗ Keine Wiki gefunden in {wiki_dir}[/red]")
        console.print("  Zuerst ausführen: [cyan]python compile.py[/cyan]")
        raise typer.Exit(1)

    articles = load_wiki(wiki_dir)
    index = get_index(articles)
    system = SYSTEM_PROMPT.format(index=index)

    nodes, edges = load_graph_data(data)

    non_meta = [a for a in articles if not a.startswith("_")]

    console.print()
    console.rule("[bold blue]ILLICO CHAT[/bold blue]")
    console.print(f"  Wiki:   [cyan]{len(non_meta)} Artikel[/cyan]")
    console.print(f"  Modell: [cyan]{effective_model}[/cyan]")
    console.print(f"  Data:   [cyan]{data}[/cyan]")
    console.print()
    console.print("[dim]Tippe deine Frage. 'exit' oder Ctrl+C zum Beenden.[/dim]")
    console.print()

    history = []

    while True:
        try:
            question = Prompt.ask("[bold blue]Du[/bold blue]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Auf Wiedersehen.[/dim]")
            break

        if question.strip().lower() in ("exit", "quit", "q", "bye"):
            console.print("[dim]Auf Wiedersehen.[/dim]")
            break

        if not question.strip():
            continue

        try:
            with console.status("[dim]Navigiere Wiki...[/dim]", spinner="dots"):
                relevant = route(question, articles, effective_model, nodes=nodes)

            graph_context = ""
            if nodes and edges and relevant:
                relevant = expand_with_graph(relevant, articles, nodes, edges)
                graph_context = build_graph_context(relevant, articles, nodes, edges)

            with console.status("[dim]Illico denkt...[/dim]", spinner="dots"):
                answer = answer_sync(
                    question, relevant, articles, history, system, effective_model,
                    graph_context=graph_context,
                )
        except illico_llm.LLMAuthError as exc:
            console.print(f"[red]✗ LLM authentication failed: {exc}[/red]")
            console.print("  Check your provider API key and ILLICO_ANSWER_MODEL.")
            raise typer.Exit(1)

        console.print()
        console.print(Panel(
            Markdown(answer),
            title="[bold green]Illico[/bold green]",
            border_style="green",
            padding=(1, 2)
        ))
        console.print(format_sources(relevant))
        console.print()

        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})

        if len(history) > 20:
            history = history[-20:]


if __name__ == "__main__":
    app()
