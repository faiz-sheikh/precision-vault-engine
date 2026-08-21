# Local Deployment Guide

PVE is a single-user local MVP. Use local disk for SQLite and artifacts; do not place WAL files on network storage. Install the core package, optionally install the pinned dashboard extra, create a private `.env`, and run the dashboard only on a trusted interface.

```bash
python -m pip install -e '.[dashboard]'
streamlit run pve/dashboard.py
```

No public deployment, distributed queue, or remote database is part of this release.
