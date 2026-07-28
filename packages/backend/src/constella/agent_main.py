from __future__ import annotations

import asyncio

from .agent import AgentConfig, run_agent


def main() -> None:
    asyncio.run(run_agent(AgentConfig.from_env()))


if __name__ == "__main__":
    main()
