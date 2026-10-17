#!/usr/bin/env python3
"""CLI: python -m uno_q_linux [--config PATH] [--once] [--ticks N]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Uno Q Linux coordinator (AgriDrone)")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config.env or config.yaml",
    )
    parser.add_argument("--once", action="store_true", help="Single tick then exit")
    parser.add_argument("--ticks", type=int, default=None, help="Run N ticks then exit")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Force UNO_Q_MOCK=1 (laptop/CI)",
    )
    args = parser.parse_args(argv)

    if args.mock:
        import os

        os.environ["UNO_Q_MOCK"] = "1"

    # Allow `python -m uno_q_linux` from repo root or linux/
    root = Path(__file__).resolve().parents[3]  # repo root (…/uno_q/linux/uno_q_linux)
    linux_dir = Path(__file__).resolve().parents[1]
    for p in (str(root), str(linux_dir)):
        if p not in sys.path:
            sys.path.insert(0, p)

    from .config import load_config
    from .coordinator import run_coordinator

    cfg = load_config(args.config)
    print(
        f"uno_q_linux start mock={cfg.mock} host={cfg.host_url} sd={cfg.sd_dir} "
        f"force_offline={cfg.force_offline} force_online={cfg.force_online}"
    )
    run_coordinator(cfg, once=args.once, max_ticks=args.ticks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
