"""Timeout-aware stdin input with optional immediate TTY hotkeys."""

import sys
import time
from typing import Optional, Set


def _timer_text(deadline: float, base_end: Optional[float]) -> str:
    from mahjong.ui.i18n import t
    now = time.monotonic()
    if base_end is not None and base_end > now:
        return t('tc.countdown_base', base=int(base_end - now),
                 bank=int(deadline - base_end))
    return t('tc.countdown_bank', bank=max(0, int(deadline - now)))


def _update_timer(deadline: float, base_end: Optional[float]) -> None:
    sys.stdout.write('\033[s\033[A\r\033[2K' +
                     _timer_text(deadline, base_end) + '\033[u')
    sys.stdout.flush()


def timed_input(prompt: str, deadline: Optional[float],
                base_end: Optional[float] = None,
                instant_keys: Optional[Set[str]] = None) -> Optional[str]:
    """Read a line, optionally returning configured TTY keys immediately."""
    if deadline is None and not instant_keys:
        return input(prompt)
    if deadline is not None and deadline - time.monotonic() <= 0:
        sys.stdout.write('\n')
        sys.stdout.flush()
        return None
    is_tty = sys.stdout.isatty()
    if instant_keys and is_tty:
        return _timed_input_with_instant_keys(prompt, deadline, base_end,
                                              instant_keys)
    if is_tty:
        sys.stdout.write(_timer_text(deadline, base_end) + '\n')
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        import select
    except ImportError:
        line = sys.stdin.readline()
        return line.rstrip('\n').strip() if line else None
    while True:
        remaining = deadline - time.monotonic() if deadline is not None else 1.0
        if deadline is not None and remaining <= 0:
            _flush_stdin()
            sys.stdout.write('\n')
            sys.stdout.flush()
            return None
        ready, _, _ = select.select([sys.stdin], [], [], min(1.0, remaining))
        if ready:
            line = sys.stdin.readline()
            return line.rstrip('\n').strip() if line else None
        if is_tty and deadline is not None and deadline - time.monotonic() > 0:
            _update_timer(deadline, base_end)


def _timed_input_with_instant_keys(prompt: str, deadline: Optional[float],
                                   base_end: Optional[float],
                                   instant_keys: Set[str]) -> Optional[str]:
    """Read a line while returning configured keys immediately in a TTY."""
    import select
    import termios
    import tty

    if deadline is not None:
        sys.stdout.write(_timer_text(deadline, base_end) + '\n')
    sys.stdout.write(prompt)
    sys.stdout.flush()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    chars = []
    try:
        tty.setcbreak(fd)
        while True:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _flush_stdin()
                    sys.stdout.write('\n')
                    sys.stdout.flush()
                    return None
                timeout = min(1.0, remaining)
            else:
                timeout = 1.0
            ready, _, _ = select.select([sys.stdin], [], [], timeout)
            if not ready:
                if deadline is not None and deadline - time.monotonic() > 0:
                    _update_timer(deadline, base_end)
                continue
            ch = sys.stdin.read(1)
            if ch in instant_keys and not chars:
                sys.stdout.write(ch + '\n')
                sys.stdout.flush()
                return ch
            if ch in ('\r', '\n'):
                sys.stdout.write('\n')
                sys.stdout.flush()
                return ''.join(chars).strip()
            if ch == '\x03':
                raise KeyboardInterrupt
            if ch in ('\x7f', '\b'):
                if chars:
                    chars.pop()
                    sys.stdout.write('\b \b')
                    sys.stdout.flush()
                continue
            chars.append(ch)
            sys.stdout.write(ch)
            sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _flush_stdin() -> None:
    try:
        import termios
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:
        pass
