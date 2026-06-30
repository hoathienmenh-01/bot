from __future__ import annotations

import unittest

from nimo_shop.main import is_configured_bot_token


class FirstRunSetupTest(unittest.TestCase):
    def test_placeholder_and_bad_tokens_do_not_crash_bot_startup_path(self):
        self.assertFalse(is_configured_bot_token(""))
        self.assertFalse(is_configured_bot_token("token_botfather"))
        self.assertFalse(is_configured_bot_token("PASTE_TOKEN_BOTFATHER_VAO_DAY"))
        self.assertFalse(is_configured_bot_token("not-a-token"))
        self.assertFalse(is_configured_bot_token("123456789:no"))

    def test_realistic_botfather_token_shape_is_accepted_for_aiogram_validation(self):
        self.assertTrue(is_configured_bot_token("123456789:AAHabcdefghijklmnopqrstuvwxyz_123456789"))


if __name__ == "__main__":
    unittest.main()
