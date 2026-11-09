"""Run the MCP server on stdio: ``python -m aip.mcp_server --tenant-id 1``."""

import argparse
import os

from aip.db import make_engine, make_session_factory
from aip.mcp_server.server import build_server


def main() -> None:
    parser = argparse.ArgumentParser(prog="aip.mcp_server")
    parser.add_argument("--tenant-id", type=int, required=True)
    args = parser.parse_args()
    engine = make_engine(os.environ["DATABASE_URL"])
    cap = int(os.environ.get("AIP_HARD_CAP_MINOR", "50000"))
    build_server(args.tenant_id, make_session_factory(engine), cap).run("stdio")


if __name__ == "__main__":
    main()
