from __future__ import annotations

from .agent import AgentConfig, run_agent_service


def main() -> None:
    run_agent_service(AgentConfig.from_env())


if __name__ == "__main__":
    main()
