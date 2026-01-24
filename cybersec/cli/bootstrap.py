"""Bootstrap CLI commands using Typer.

Provides CLI interface with full parity to Web UI and MCP tools.
"""

import typer
import asyncio
from typing import Optional
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich import print as rprint

from ..bootstrap import (
    BootstrapService,
    BootstrapConfig,
    SettingsManager,
    EventType,
    BootstrapEvent,
)
from ..bootstrap.events import PromptOption

app = typer.Typer(
    name="bootstrap",
    help="Bootstrap and configure the cybersec devenv environment",
    no_args_is_help=True,
)

console = Console()


def _get_service() -> BootstrapService:
    """Create a bootstrap service instance."""
    return BootstrapService()


@app.command()
def info():
    """Show bootstrap system information and current configuration.

    Displays:
    - Configuration file location
    - Current settings
    - Environment variable overrides
    """
    service = _get_service()
    config = service.get_config()
    settings = service.settings

    # Header
    console.print(Panel.fit(
        "[bold blue]Cybersec Bootstrap Configuration[/bold blue]",
        border_style="blue"
    ))

    # Config file status
    config_path = settings.config_path
    if config_path.exists():
        console.print(f"\n[green]Config file:[/green] {config_path}")
    else:
        console.print(f"\n[yellow]Config file:[/yellow] {config_path} [dim](not created yet)[/dim]")

    # Bootstrap status
    status = "[green]Completed[/green]" if config.completed else "[yellow]Not completed[/yellow]"
    console.print(f"[bold]Bootstrap status:[/bold] {status}")
    if config.last_run:
        console.print(f"[dim]Last run: {config.last_run}[/dim]")

    # Paths table
    console.print("\n[bold]Paths:[/bold]")
    paths_table = Table(show_header=False, box=None, padding=(0, 2))
    paths_table.add_column("Setting", style="cyan")
    paths_table.add_column("Value")

    flink_home = config.get_flink_home()
    paths_table.add_row("Flink Home", str(flink_home) if flink_home else "[dim]Not configured[/dim]")
    paths_table.add_row("Flink State", str(config.get_flink_state_dir()))
    paths_table.add_row("MinIO Data", str(config.get_minio_data_dir()))
    paths_table.add_row("Log Dir", str(config.get_log_dir()))
    console.print(paths_table)

    # Services table
    console.print("\n[bold]Service Endpoints:[/bold]")
    services_table = Table(show_header=False, box=None, padding=(0, 2))
    services_table.add_column("Service", style="cyan")
    services_table.add_column("Endpoint")

    services_table.add_row("PostgreSQL", f"{config.postgres_host}:{config.postgres_port}")
    services_table.add_row("Polaris API", config.polaris_api_url)
    services_table.add_row("Polaris Admin", config.polaris_admin_url)
    services_table.add_row("Flink", config.flink_url)
    services_table.add_row("MinIO", config.minio_endpoint)
    services_table.add_row("MinIO Console", config.minio_console)
    services_table.add_row("Iceberg Browser", f"http://localhost:{config.iceberg_browser_port}")
    services_table.add_row("NiFi", config.nifi_url)
    services_table.add_row("NiFi OTLP", f"localhost:{config.nifi_otlp_port}")
    console.print(services_table)

    # NiFi configuration
    console.print("\n[bold]NiFi:[/bold]")
    nifi_home = config.get_nifi_home()
    console.print(f"  [cyan]Home:[/cyan] {nifi_home if nifi_home else '[dim]Not configured[/dim]'}")
    console.print(f"  [cyan]Version:[/cyan] {config.nifi_version}")

    # Catalog
    console.print("\n[bold]Catalog:[/bold]")
    console.print(f"  [cyan]Name:[/cyan] {config.catalog_name}")
    console.print(f"  [cyan]Warehouse:[/cyan] {config.catalog_warehouse}")


@app.command()
def status():
    """Check current status of all services and bootstrap state.

    Performs health checks on:
    - PostgreSQL
    - Polaris (API and Admin)
    - MinIO
    - Flink
    """
    service = _get_service()

    console.print(Panel.fit(
        "[bold blue]Service Status[/bold blue]",
        border_style="blue"
    ))

    # Run async health checks
    results = asyncio.run(service.check_all_services())

    # Display results
    table = Table(show_header=True, header_style="bold")
    table.add_column("Service")
    table.add_column("Status")
    table.add_column("Details")

    all_healthy = True
    for result in results:
        is_healthy = result.get("healthy", False)
        if not is_healthy:
            all_healthy = False

        status_str = "[green]Healthy[/green]" if is_healthy else "[red]Unhealthy[/red]"
        details = result.get("error", "") or result.get("message", "")
        if not details and is_healthy:
            details = "[dim]OK[/dim]"
        elif not is_healthy and not details:
            details = "[dim]Not responding[/dim]"

        table.add_row(result["service"], status_str, details)

    console.print(table)

    # Summary
    if all_healthy:
        console.print("\n[green]All services are healthy![/green]")
    else:
        console.print("\n[yellow]Some services are not healthy. Run 'cybersec bootstrap run' to fix.[/yellow]")

    # Raise exit code for unhealthy
    if not all_healthy:
        raise typer.Exit(code=1)


@app.command()
def settings(
    show: bool = typer.Option(False, "--show", "-s", help="Show current settings"),
    edit: bool = typer.Option(False, "--edit", "-e", help="Edit settings interactively"),
    set_value: Optional[list[str]] = typer.Option(
        None, "--set", help="Set a specific value (key=value format)"
    ),
    reset: bool = typer.Option(False, "--reset", help="Reset settings to defaults"),
):
    """View or modify bootstrap settings.

    Examples:
        cybersec bootstrap settings --show
        cybersec bootstrap settings --set flink_home=/path/to/flink
        cybersec bootstrap settings --edit
        cybersec bootstrap settings --reset
    """
    service = _get_service()

    if reset:
        if Confirm.ask("Reset all settings to defaults?", default=False):
            config = BootstrapConfig()
            service.settings.save(config)
            console.print("[green]Settings reset to defaults.[/green]")
        return

    if set_value:
        updates = {}
        for item in set_value:
            if "=" not in item:
                console.print(f"[red]Invalid format:[/red] {item} (expected key=value)")
                raise typer.Exit(code=1)
            key, value = item.split("=", 1)

            # Type conversion for known fields
            config = service.get_config()
            if hasattr(config, key):
                field_type = type(getattr(config, key))
                if field_type == int:
                    value = int(value)
                elif field_type == bool:
                    value = value.lower() in ("true", "1", "yes")

            updates[key] = value

        service.update_config(**updates)
        console.print(f"[green]Updated {len(updates)} setting(s).[/green]")
        return

    if edit:
        _interactive_settings(service)
        return

    # Default: show settings (same as --show)
    info()


def _interactive_settings(service: BootstrapService):
    """Interactive settings editor."""
    config = service.get_config()

    console.print(Panel.fit(
        "[bold blue]Interactive Settings Editor[/bold blue]\n"
        "[dim]Press Enter to keep current value, or enter new value[/dim]",
        border_style="blue"
    ))

    updates = {}

    # Flink configuration
    console.print("\n[bold]Flink Configuration[/bold]")
    current_flink = config.flink_home or "[default: build from source]"
    new_flink = Prompt.ask(
        "  Flink home directory",
        default=config.flink_home or "",
        show_default=False
    )
    if new_flink != config.flink_home:
        updates["flink_home"] = new_flink

    # MinIO data directory
    console.print("\n[bold]MinIO Configuration[/bold]")
    current_minio = config.minio_data_dir or str(config.get_minio_data_dir())
    new_minio = Prompt.ask(
        "  MinIO data directory",
        default=config.minio_data_dir or "",
        show_default=False
    )
    if new_minio != config.minio_data_dir:
        updates["minio_data_dir"] = new_minio

    # Ports
    console.print("\n[bold]Service Ports[/bold]")
    new_pg_port = Prompt.ask(
        "  PostgreSQL port",
        default=str(config.postgres_port)
    )
    if int(new_pg_port) != config.postgres_port:
        updates["postgres_port"] = int(new_pg_port)

    new_browser_port = Prompt.ask(
        "  Iceberg Browser port",
        default=str(config.iceberg_browser_port)
    )
    if int(new_browser_port) != config.iceberg_browser_port:
        updates["iceberg_browser_port"] = int(new_browser_port)

    # Catalog
    console.print("\n[bold]Catalog Configuration[/bold]")
    new_catalog = Prompt.ask(
        "  Catalog name",
        default=config.catalog_name
    )
    if new_catalog != config.catalog_name:
        updates["catalog_name"] = new_catalog

    # Save changes
    if updates:
        console.print(f"\n[bold]Changes to save:[/bold]")
        for key, value in updates.items():
            console.print(f"  {key} = {value}")

        if Confirm.ask("\nSave these changes?", default=True):
            service.update_config(**updates)
            console.print("[green]Settings saved.[/green]")
    else:
        console.print("\n[dim]No changes made.[/dim]")


@app.command()
def verify():
    """Verify the bootstrap configuration is correct and complete.

    Checks:
    - Required directories exist
    - Services are accessible
    - Catalog is configured
    - Git submodules are initialized
    """
    service = _get_service()

    console.print(Panel.fit(
        "[bold blue]Bootstrap Verification[/bold blue]",
        border_style="blue"
    ))

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Verifying configuration...", total=None)
        result = asyncio.run(service.verify())
        progress.update(task, completed=True)

    # Display results
    table = Table(show_header=True, header_style="bold")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Details")

    for check in result.get("checks", []):
        status_str = "[green]Pass[/green]" if check["passed"] else "[red]Fail[/red]"
        table.add_row(check["name"], status_str, check.get("message", ""))

    console.print(table)

    # Summary
    if result.get("all_passed", False):
        console.print("\n[green]All checks passed! Environment is ready.[/green]")
    else:
        console.print("\n[yellow]Some checks failed. Run 'cybersec bootstrap run' to fix.[/yellow]")
        raise typer.Exit(code=1)


@app.command()
def run(
    skip_flink: bool = typer.Option(False, "--skip-flink", help="Skip Flink build/setup"),
    flink_path: Optional[Path] = typer.Option(
        None, "--flink-path", "-f",
        help="Path to existing Flink installation"
    ),
    skip_nifi: bool = typer.Option(False, "--skip-nifi", help="Skip NiFi setup"),
    nifi_path: Optional[Path] = typer.Option(
        None, "--nifi-path",
        help="Path to existing NiFi installation"
    ),
    non_interactive: bool = typer.Option(
        False, "--non-interactive", "-y",
        help="Run without prompts (use defaults)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n",
        help="Show what would be done without making changes"
    ),
):
    """Run the bootstrap process to set up the environment.

    This will:
    1. Check environment requirements
    2. Verify port availability
    3. Set up directories
    4. Initialize git submodules
    5. Build/configure Flink (unless skipped)
    6. Download/configure NiFi (unless skipped)
    7. Verify all services

    Examples:
        cybersec bootstrap run
        cybersec bootstrap run --flink-path ~/local/flink
        cybersec bootstrap run --skip-flink --skip-nifi
        cybersec bootstrap run --non-interactive
    """
    service = _get_service()

    console.print(Panel.fit(
        "[bold blue]Cybersec Bootstrap[/bold blue]",
        border_style="blue"
    ))

    if dry_run:
        console.print("[yellow]DRY RUN - No changes will be made[/yellow]\n")

    # Set up prompt handler for CLI
    async def cli_prompt_handler(event: BootstrapEvent) -> Optional[str]:
        """Handle prompts from bootstrap service."""
        if non_interactive:
            # Find default option
            for opt in event.prompt_options:
                if opt.default:
                    console.print(f"[dim]Auto-selecting: {opt.label}[/dim]")
                    return opt.key
            return event.prompt_options[0].key if event.prompt_options else None

        console.print(f"\n[bold]{event.message}[/bold]")
        for opt in event.prompt_options:
            default_marker = " [default]" if opt.default else ""
            console.print(f"  [{opt.key}] {opt.label}{default_marker}")
            if opt.description:
                console.print(f"      [dim]{opt.description}[/dim]")

        if event.prompt_allow_custom:
            console.print("  [c] Enter custom value")

        # Get user input
        default_key = next((o.key for o in event.prompt_options if o.default), None)
        choice = Prompt.ask(
            "Select option",
            default=default_key or event.prompt_options[0].key if event.prompt_options else ""
        )

        if choice == "c" and event.prompt_allow_custom:
            return Prompt.ask("Enter custom value")

        return choice

    # Run bootstrap with progress display
    async def run_bootstrap():
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            current_task = None
            task_id = None

            async for event in service.run(
                skip_flink=skip_flink,
                flink_path=str(flink_path) if flink_path else None,
                skip_nifi=skip_nifi,
                nifi_path=str(nifi_path) if nifi_path else None,
                prompt_handler=cli_prompt_handler,
                dry_run=dry_run,
            ):
                if event.event_type == EventType.TASK_STARTED:
                    if task_id is not None:
                        progress.update(task_id, completed=100)
                    task_id = progress.add_task(event.message or event.task_id, total=100)
                    current_task = event.task_id

                elif event.event_type == EventType.TASK_PROGRESS:
                    if task_id is not None and event.progress is not None:
                        progress.update(task_id, completed=event.progress * 100)
                    if event.message:
                        progress.update(task_id, description=event.message)

                elif event.event_type == EventType.TASK_COMPLETED:
                    if task_id is not None:
                        progress.update(task_id, completed=100)
                    console.print(f"  [green]\u2713[/green] {event.message or event.task_id}")

                elif event.event_type == EventType.TASK_FAILED:
                    console.print(f"  [red]\u2717[/red] {event.task_id}: {event.message}")

                elif event.event_type == EventType.TASK_SKIPPED:
                    console.print(f"  [yellow]-[/yellow] {event.message or event.task_id}")

                elif event.event_type == EventType.LOG_INFO:
                    console.print(f"  [dim]{event.message}[/dim]")

                elif event.event_type == EventType.LOG_WARN:
                    console.print(f"  [yellow]Warning:[/yellow] {event.message}")

                elif event.event_type == EventType.LOG_ERROR:
                    console.print(f"  [red]Error:[/red] {event.message}")

                elif event.event_type == EventType.BOOTSTRAP_COMPLETED:
                    console.print(f"\n[green bold]{event.message}[/green bold]")

                elif event.event_type == EventType.BOOTSTRAP_FAILED:
                    console.print(f"\n[red bold]{event.message}[/red bold]")
                    raise typer.Exit(code=1)

    asyncio.run(run_bootstrap())


@app.command()
def assess():
    """Assess environment readiness (for devenv bootstrap-check).

    Returns machine-readable JSON output suitable for automated checks.
    This is typically called by devenv on startup to determine if
    bootstrap is needed.
    """
    import json

    service = _get_service()
    result = asyncio.run(service.assess())

    # Output JSON for machine consumption
    print(json.dumps(result, indent=2))

    # Exit code based on readiness
    if not result.get("ready", False):
        raise typer.Exit(code=1)
