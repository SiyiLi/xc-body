import os
import unittest
from unittest.mock import patch

from stackchan_mcp.gateway import Gateway


class GatewayConfigTests(unittest.TestCase):
    def test_interaction_token_authenticates_private_capture_uploads(self):
        with patch.dict(
            os.environ,
            {
                "XC_BODY_INTERACTION_HTTP_TOKEN": "interaction-token",
                "STACKCHAN_TOKEN": "gateway-token",
            },
            clear=True,
        ):
            self.assertEqual(Gateway().audio_hook_token, "interaction-token")


if __name__ == "__main__":
    unittest.main()
