"""Main CLI entry point using Typer."""

import typer
from typing import Optional
from pathlib import Path

app = typer.Typer(
    name="cybersec",
    help="Cybersec Toolkit - Security Data Lakehouse CLI",
    no_args_is_help=True,
)

# Import subcommands
from . import bootstrap

# Register bootstrap subcommand
app.add_typer(bootstrap.app, name="bootstrap")


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose output"),
):
    """Cybersec Toolkit CLI."""
    if verbose:
        import logging

        logging.basicConfig(level=logging.DEBUG)


def run():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    run()
