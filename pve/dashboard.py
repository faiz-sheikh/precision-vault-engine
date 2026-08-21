"""Read-only dashboard projections; render paths can never invoke adapters."""

from __future__ import annotations

from typing import Any

from pve.settings import Settings
from pve.store import Store

SECTIONS = (
    "Overview",
    "Manifest",
    "Sources",
    "Pipeline Control",
    "Jobs / Provider Ledger",
    "Quality / Verification",
    "Artifacts",
    "Metrics / Cost",
    "Failures / Recovery",
    "Settings",
)


def snapshot(store: Store) -> dict[str, Any]:
    with store.connect() as db:

        def scalar(query: str) -> int:
            value = db.execute(query).fetchone()[0]
            return int(value)

        return {
            "runs": scalar("SELECT count(*) FROM runs"),
            "jobs": scalar("SELECT count(*) FROM jobs"),
            "provider_operations": scalar("SELECT count(*) FROM provider_operations"),
            "artifacts": scalar("SELECT count(*) FROM artifacts"),
            "sources": scalar("SELECT count(*) FROM sources"),
            "failures": scalar("SELECT count(*) FROM failures"),
            "sections": SECTIONS,
        }


def render(store: Store) -> None:
    import streamlit as st

    data = snapshot(store)
    st.title("Precision Vault Engine")
    section = st.sidebar.radio("Area", SECTIONS)
    st.subheader(section)
    st.json(data)


def main() -> None:
    settings = Settings.from_env()
    store = Store(settings.database_path)
    store.initialize()
    render(store)


if __name__ == "__main__":
    main()
