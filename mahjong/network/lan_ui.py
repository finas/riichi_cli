"""Terminal UI workflows for Hosting and Joining LAN games."""

import time
from rich.console import Console
from rich.panel import Panel

from mahjong.engine.ai_delay import AIDelay
from mahjong.engine.time_control import TimeControl
from mahjong.network.client import LANClient
from mahjong.network.server import LANServer, get_local_ip
from mahjong.player.human import HumanPlayer
from mahjong.ui.i18n import t
from mahjong.ui.renderer import Renderer


def host_lan_game(console: Console, time_control: TimeControl, ai_delay: AIDelay):
    """Interactive screen to configure and host a LAN multiplayer room."""
    console.print()
    console.print(Panel(f"[bold cyan]{t('lan.host_title')}[/bold cyan]", border_style="cyan"))

    console.print(f"  {t('mode.select')}")
    console.print(f"    1. {t('mode.4p')}")
    console.print(f"    2. {t('mode.4p_tonpuu')}")
    console.print(f"    3. {t('mode.3p')}")
    console.print(f"    4. {t('mode.3p_tonpuu')}")
    console.print(f"    0. {t('settings.back')}")
    console.print()

    while True:
        try:
            choice = int(console.input(f"  > {t('prompt.choose_mode', n=4)} ").strip())
            if 0 <= choice <= 4:
                break
        except (ValueError, EOFError):
            pass
        console.print(f"  [red]{t('prompt.invalid_input')}[/red]")

    if choice == 0:
        return

    is_sanma = choice in (3, 4)
    is_tonpuu = choice in (2, 4)
    num_players = 3 if is_sanma else 4

    # Player Name
    host_name = console.input(f"  > {t('lan.enter_name')} [房主 (Host)]: ").strip()
    if not host_name:
        host_name = "房主 (Host)"

    # Port
    port_str = console.input(f"  > {t('lan.enter_port')} [7777]: ").strip()
    port = int(port_str) if port_str.isdigit() else 7777

    local_ip = get_local_ip()

    server = LANServer(
        host="0.0.0.0",
        port=port,
        num_players=num_players,
        is_sanma=is_sanma,
        is_tonpuu=is_tonpuu,
        host_player_name=host_name,
        time_control=time_control,
        ai_delay=ai_delay,
    )

    try:
        server.start_lobby()
    except Exception as e:
        console.print(f"\n  [bold red]{t('lan.host_failed')}: {e}[/bold red]\n")
        return

    console.clear()
    console.print(Panel(
        f"[bold green]{t('lan.room_created')}[/bold green]\n\n"
        f"  [bold]{t('lan.share_ip')}:[/bold] [bold yellow]{local_ip}:{port}[/bold yellow]\n"
        f"  [dim]{t('lan.players_needed', n=num_players)}[/dim]\n\n"
        f"  [bold cyan]1. {host_name} (Host)[/bold cyan]",
        border_style="green"
    ))

    console.print(f"\n  {t('lan.host_controls')}")
    console.print(f"    [bold green]S[/bold green] - {t('lan.start_game_now')}")
    console.print(f"    [bold red]Q[/bold red] - {t('lan.cancel_room')}\n")

    renderer = Renderer(console)
    host_player = HumanPlayer(
        host_name,
        console=console,
        renderer=renderer,
        time_control=time_control,
    )

    while True:
        try:
            cmd = console.input(f"  > [S/Q]: ").strip().lower()
            if cmd == 's':
                console.print(f"\n  [bold green]{t('lan.starting_game')}[/bold green]\n")
                server.run_game(host_player, renderer)
                break
            elif cmd == 'q':
                server.stop()
                console.print(f"\n  {t('lan.room_cancelled')}\n")
                break
        except (KeyboardInterrupt, EOFError):
            server.stop()
            break


def join_lan_game(console: Console):
    """Interactive screen to join an existing LAN room."""
    console.print()
    console.print(Panel(f"[bold cyan]{t('lan.join_title')}[/bold cyan]", border_style="cyan"))

    host_ip = console.input(f"  > {t('lan.enter_host_ip')} [127.0.0.1]: ").strip()
    if not host_ip:
        host_ip = "127.0.0.1"

    port_str = console.input(f"  > {t('lan.enter_port')} [7777]: ").strip()
    port = int(port_str) if port_str.isdigit() else 7777

    player_name = console.input(f"  > {t('lan.enter_name')} [Player]: ").strip()
    if not player_name:
        player_name = "Player"

    client = LANClient(console)
    console.print(f"\n  {t('lan.connecting', ip=host_ip, port=port)}...")

    if not client.connect(host_ip, port, player_name):
        reason = f": {client.last_error}" if client.last_error else ""
        console.print(
            f"\n  [bold red]{t('lan.connect_failed')}{reason}[/bold red]\n"
        )
        return

    if client.run_lobby():
        client.run_game_loop()
