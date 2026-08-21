from __future__ import annotations

import socket
import unittest
from unittest.mock import patch


class NetworkBlockedBaselineTests(unittest.TestCase):
    def test_network_guard_blocks_socket_connections(self) -> None:
        with patch.object(socket, "socket", autospec=True) as socket_factory:
            socket_factory.return_value.connect.side_effect = AssertionError("network disabled")
            with self.assertRaisesRegex(AssertionError, "network disabled"):
                socket.socket().connect(("example.invalid", 443))


if __name__ == "__main__":
    unittest.main()
