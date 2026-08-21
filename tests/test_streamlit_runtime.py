from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from pve.dashboard import SECTIONS
from pve.store import Store


class StreamlitRuntimeTests(unittest.TestCase):
    def test_real_app_navigation_reruns_and_restart_create_no_provider_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory) / "state"
            artifact_dir = Path(directory) / "artifacts"
            env = {
                "PVE_STATE_DIR": str(state_dir),
                "PVE_ARTIFACT_DIR": str(artifact_dir),
                "PVE_ALLOW_LIVE_PROVIDERS": "0",
                "PVE_ALLOW_GOOGLE_LIVE": "0",
                "PVE_ALLOW_NVIDIA_LIVE": "0",
                "PVE_ALLOW_OPENAI_LIVE": "0",
                "PVE_ALLOW_ANTHROPIC_LIVE": "0",
            }
            app_path = Path(__file__).parents[1] / "pve" / "dashboard.py"
            with patch.dict(os.environ, env, clear=False):
                app = AppTest.from_file(str(app_path), default_timeout=10).run()
                self.assertFalse(app.exception)
                self.assertEqual(app.title[0].value, "Precision Vault Engine")
                for section in SECTIONS:
                    app.sidebar.radio[0].set_value(section)
                    app.run()
                    self.assertFalse(app.exception)
                    self.assertEqual(app.subheader[0].value, section)
                for _ in range(5):
                    app.run()
                    self.assertFalse(app.exception)
                restarted = AppTest.from_file(str(app_path), default_timeout=10).run()
                self.assertFalse(restarted.exception)

            store = Store(state_dir / "operations.sqlite3")
            with store.connect() as db:
                self.assertEqual(
                    db.execute("SELECT count(*) FROM provider_events").fetchone()[0], 0
                )
                self.assertEqual(
                    db.execute("SELECT count(*) FROM provider_operations").fetchone()[0], 0
                )
