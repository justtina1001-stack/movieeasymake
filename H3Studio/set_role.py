from __future__ import annotations

import argparse
from pathlib import Path

from settings import SettingsStore


APP_DIR = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Set the local H3 Studio workstation role.")
    parser.add_argument("role", choices=("host", "client"))
    args = parser.parse_args()
    updated = SettingsStore(APP_DIR / "config.json", APP_DIR).set_studio_role(args.role)
    label = "管理主機" if updated.studio_role == "host" else "一般使用者"
    print(f"H3 Studio 工作站角色已設為：{label}")
    print("請重新啟動 H3 Studio 讓角色設定生效。")


if __name__ == "__main__":
    main()
