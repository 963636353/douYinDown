import json
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from payment_kit.computer import ComputerIdentity, normalize
from payment_kit.identity import DeviceIdentity


HARDWARE = "13572468-1234-4321-8765-123456789abc"
GUID = "24681357-1234-4321-8765-123456789abc"
OTHER_HARDWARE = "13572468-1234-4321-8765-123456789abd"


class ComputerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def computer(self, path=None, hardware=HARDWARE, guid=GUID):
        return ComputerIdentity(path or self.folder / "computer.json", smbios_reader=lambda: hardware,
                                guid_reader=lambda: guid)

    def test_same_hardware_produces_same_uuid_without_saved_file(self):
        first = self.computer()
        (self.folder / "computer.json").unlink()
        second = self.computer()
        self.assertEqual(first.uuid, second.uuid)
        self.assertEqual(uuid.UUID(first.uuid).version, 5)
        other = self.computer(self.folder / "other.json", hardware=OTHER_HARDWARE)
        self.assertNotEqual(first.uuid, other.uuid)

    def test_temporarily_missing_hardware_reuses_selected_source_on_same_windows(self):
        first = self.computer()
        restored = self.computer(hardware=None)
        self.assertEqual(first.uuid, restored.uuid)
        self.assertEqual(json.loads((self.folder / "computer.json").read_text())["source"], "smbios")

    def test_invalid_hardware_falls_back_once_and_does_not_switch_when_hardware_appears(self):
        first = self.computer(hardware="00000000-0000-0000-0000-000000000000")
        restored = self.computer()
        self.assertEqual(first.uuid, restored.uuid)
        self.assertEqual(json.loads((self.folder / "computer.json").read_text())["source"], "machine_guid")

    def test_different_computer_cannot_use_copied_identity(self):
        self.computer()
        with self.assertRaises(ValueError):
            self.computer(hardware=OTHER_HARDWARE)
        with self.assertRaises(ValueError):
            self.computer(hardware=None, guid=OTHER_HARDWARE)

    def test_no_readable_identity_does_not_generate_random_trial_identity(self):
        with self.assertRaises(ValueError):
            self.computer(hardware=None, guid=None)
        self.assertFalse((self.folder / "computer.json").exists())
        self.assertIsNone(normalize("ffffffff-ffff-ffff-ffff-ffffffffffff"))

    def test_concurrent_first_startups_keep_same_computer_without_credentials(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            computers = list(executor.map(lambda _: self.computer(), range(8)))
        self.assertEqual(len({computer.uuid for computer in computers}), 1)
        path = self.folder / "profile" / "identity.json"
        with ThreadPoolExecutor(max_workers=4) as executor:
            identities = list(executor.map(lambda _: DeviceIdentity(path, "https://example.invalid/api/pc",
                                         device_id=computers[0].uuid), range(8)))
        self.assertFalse(path.exists())
        self.assertTrue(all(not hasattr(identity, "token") for identity in identities))
        self.assertEqual({identity.device_id for identity in identities}, {computers[0].uuid})

    def test_software_and_credentials_removed_still_use_same_uuid(self):
        api = "https://example.invalid/api/pc"
        legacy = self.folder / "software" / "identity.json"
        legacy.parent.mkdir()
        old_id = uuid.uuid4().hex
        legacy.write_text(json.dumps({"apiBase": api, "deviceId": old_id}), encoding="utf-8")
        computer = self.computer(self.folder / "profile" / "computer.json")
        profile = self.folder / "profile" / "credentials.json"
        imported = DeviceIdentity(profile, api, device_id=computer.uuid, legacy_path=legacy)
        self.assertEqual(imported.legacy_device_id, old_id)
        imported.bind(computer.uuid)
        legacy.unlink()
        legacy.parent.rmdir()
        restored = DeviceIdentity(profile, api, device_id=computer.uuid)
        self.assertEqual(restored.device_id, computer.uuid)
        self.assertIsNone(restored.legacy_device_id)
        self.assertFalse(profile.exists())

    def test_broken_or_unreadable_old_credentials_do_not_block_uuid_identity(self):
        computer = self.computer()
        path = self.folder / "credentials.json"
        for contents in ("broken json", "[]", "null", '{"apiBase": 1}',
                         json.dumps({"apiBase": "https://other.invalid", "deviceId": uuid.uuid4().hex})):
            path.write_text(contents, encoding="utf-8")
            identity = DeviceIdentity(path, "https://example.invalid", device_id=computer.uuid)
            self.assertEqual(identity.device_id, computer.uuid)
            self.assertIsNone(identity.legacy_device_id)
            self.assertEqual(path.read_text(encoding="utf-8"), contents)
        path.unlink()
        path.mkdir()
        self.assertEqual(DeviceIdentity(path, "https://example.invalid", device_id=computer.uuid).device_id, computer.uuid)

    def test_existing_uuid_profile_does_not_hide_restored_legacy_id(self):
        computer = self.computer()
        api = "https://example.invalid"
        path, legacy = self.folder / "profile.json", self.folder / "legacy.json"
        path.write_text(json.dumps({"apiBase": api, "deviceId": computer.uuid, "deviceToken": "wrong"}))
        old_id = uuid.uuid4().hex
        legacy.write_text(json.dumps({"apiBase": api, "deviceId": old_id}))
        identity = DeviceIdentity(path, api, device_id=computer.uuid, legacy_path=legacy)
        self.assertEqual(identity.legacy_device_id, old_id)
        self.assertEqual(identity.device_id, computer.uuid)

    def test_identity_requires_stable_uuid_and_rejects_response_for_another_computer(self):
        path = self.folder / "identity.json"
        for invalid in ("random-install", uuid.uuid4().hex, None):
            with self.assertRaises(ValueError):
                DeviceIdentity(path, "https://example.invalid", device_id=invalid)
        identity = DeviceIdentity(path, "https://example.invalid", device_id=self.computer().uuid)
        with self.assertRaises(ValueError):
            identity.bind(uuid.uuid5(uuid.NAMESPACE_URL, "another-computer").hex)


if __name__ == "__main__":
    unittest.main()
