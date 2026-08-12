import unittest

from app.stores import STORES
from app.web.access import accessible_store_items, accessible_store_slugs, has_store_access


class StoreAccessTests(unittest.TestCase):
    def test_superadmin_can_access_every_store(self) -> None:
        user = {"role": "superadmin", "store_slugs": []}

        self.assertEqual(accessible_store_slugs(user), tuple(STORES))

    def test_user_access_is_ordered_and_restricted(self) -> None:
        user = {"role": "user", "store_slugs": ["trusthome", "rimili", "unknown"]}

        self.assertEqual(accessible_store_slugs(user), ("rimili", "trusthome"))
        self.assertEqual(
            [item.slug for item in accessible_store_items(user).root],
            ["rimili", "trusthome"],
        )
        self.assertTrue(has_store_access(user, "TRUSTHOME"))
        self.assertFalse(has_store_access(user, "tris"))

    def test_anonymous_user_has_no_access(self) -> None:
        self.assertEqual(accessible_store_slugs(None), ())


if __name__ == "__main__":
    unittest.main()
