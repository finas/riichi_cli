"""Regression coverage for the root command-line entrypoint."""


def test_root_entrypoint_imports_cli():
    import main
    from mahjong.cli import main as cli_main

    assert main.main is cli_main
