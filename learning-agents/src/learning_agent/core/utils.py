import json

import pandas as pd
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

console = Console()


def print_table(df: pd.DataFrame) -> None:
    # Create a Rich Table
    print("\n")
    table = Table()
    # Add columns
    for col in df.columns:
        table.add_column(str(col), style="bold cyan")
    # Add rows
    for _, row in df.iterrows():
        table.add_row(*[str(val) for val in row])
    # Print the table
    console.print(table)


def print_header(text: str) -> None:
    console.print(Markdown(f"# {text}"))


def print_panel(text: str | dict, title: str) -> None:
    print("\n")
    try:
        text = str(text)
        text_syntax = Syntax(
            text, "python", theme="monokai", line_numbers=False, word_wrap=True
        )
        console.print(Panel(text_syntax, title=title))
    except Exception as e:
        pretty_str = json.dumps(text, indent=2)
        console.print(
            Panel(
                Syntax(
                    pretty_str,
                    "json",
                    theme="monokai",
                    line_numbers=False,
                    word_wrap=True,
                ),
                title=title,
            )
        )


def print_panel_text(
    text: str,
    title: str,
    text_style: str = "steel_blue",
    border_style: str = "steel_blue",
) -> None:
    print("\n")
    message = Text(text, style=text_style)
    # Display the message inside a panel
    console.print(Panel(message, title=title, border_style=border_style))


def print_code(code: str, title: str = "Generated Code"):
    """Print the code in a pretty way."""
    syntax = Syntax(
        code,
        "python",
        theme="monokai",
        line_numbers=True,
        word_wrap=True,
        background_color="grey23",
    )
    console.print(Panel(syntax, title=title))
