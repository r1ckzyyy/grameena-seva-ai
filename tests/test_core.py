"""Small regression tests for the shared identity and persistence boundaries."""

import tempfile
import unittest
from pathlib import Path

from models.conversation import AgentResult, ConversationState
from repositories.conversations import ConversationRepository
from repositories.database import SQLiteDatabase
from repositories.farmers import FarmerRepository, normalize_phone
from repositories.models import FarmerRecord


class CoreRegressionTests(unittest.TestCase):
    def test_phone_normalization_is_consistent(self):
        self.assertEqual(normalize_phone("+91 98765-43210"), "9876543210")
        self.assertEqual(normalize_phone("9876543210"), "9876543210")
        self.assertEqual(normalize_phone("123"), "")

    def test_phone_lookup_uses_one_identity_for_formatted_input(self):
        with tempfile.TemporaryDirectory() as directory:
            farmers = FarmerRepository(SQLiteDatabase(Path(directory) / "test.sqlite3"))
            farmer = FarmerRecord(id="farmer-1", phone="+91 98765 43210")
            farmers.save(farmer)
            found = farmers.find_by_phone("9876543210")
            self.assertIsNotNone(found)
            self.assertEqual(found.id, farmer.id)
            self.assertEqual(found.phone, "9876543210")

    def test_conversation_snapshot_survives_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "test.sqlite3")
            FarmerRepository(database).save(FarmerRecord(id="farmer-1"))
            conversations = ConversationRepository(database)
            state = ConversationState(farmer_id="farmer-1", language_code="te")
            state.add_turn("farmer", "I need a subsidy")
            state.result = AgentResult(scheme_name="Test Scheme", conversation_complete=True)
            state.summary_persisted = True
            conversations.save(state, "farmer-1")
            loaded = conversations.load_for_farmer("farmer-1")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.language_code, "te")
            self.assertEqual(loaded.result.scheme_name, "Test Scheme")
            self.assertTrue(loaded.summary_persisted)

    def test_corrupt_farmer_lists_use_safe_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            database = SQLiteDatabase(Path(directory) / "test.sqlite3")
            with database.connect() as connection:
                connection.execute("INSERT INTO farmers (id, phone, phone_normalized, current_crops, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)", ("farmer-1", "9876543210", "9876543210", "not-json", "now", "now"))
            farmer = FarmerRepository(database).get("farmer-1")
            self.assertIsNotNone(farmer)
            self.assertEqual(farmer.current_crops, [])


if __name__ == "__main__":
    unittest.main()
