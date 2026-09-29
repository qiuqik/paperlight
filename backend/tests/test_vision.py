import os
import unittest
from unittest.mock import patch

from backend.app.vision import ChatVisionProvider, configured_provider


class VisionProviderTests(unittest.TestCase):
    def test_only_image_capable_configuration_is_enabled(self) -> None:
        with patch.dict(os.environ, {"PAPERLIGHT_DEEPSEEK_API_KEY": "", "PAPERLIGHT_DOUBAO_API_KEY": "key",
                                   "PAPERLIGHT_DOUBAO_VISION_MODEL": "doubao-text-only"}):
            self.assertIsNone(configured_provider())
        with patch.dict(os.environ, {"PAPERLIGHT_DEEPSEEK_API_KEY": "deepseek-key", "PAPERLIGHT_DOUBAO_API_KEY": "doubao-key",
                                   "PAPERLIGHT_DOUBAO_VISION_MODEL": "doubao-1-5-vision-pro"}):
            provider = configured_provider()
            self.assertIsInstance(provider, ChatVisionProvider)
            self.assertEqual(provider.name, "deepseek")
            self.assertEqual(provider.model, "deepseek-flash")


if __name__ == "__main__":
    unittest.main()
