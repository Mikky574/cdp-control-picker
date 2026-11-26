"""Local settings fixtures; never reads or writes the real user's settings."""
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from picker_settings import PickerSettings, SettingsError, clean_settings, default_settings_path, recommended_workspace


class PickerSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='picker-settings-')
        self.base=Path(self.temporary.name)
        self.store=PickerSettings(self.base/'app-data'/'settings.json')

    def tearDown(self):self.temporary.cleanup()

    def settings(self):
        return {'schemaVersion':1,'workspaceDir':str(self.base/'learning'),'zcodeCli':'zcode-cli',
                'chromeExe':'C:/Program Files/Chrome/chrome.exe','desktopBridgeFile':'',
                'modelSelection':{'providerId':'configured-provider','modelId':'configured-model'}}

    def test_paths_and_model_ids_survive_reload_without_credentials_or_catalog_metadata(self):
        saved=self.settings()
        self.store.save(saved)
        self.assertEqual(PickerSettings(self.store.path).load(),saved)
        payload=json.loads(self.store.path.read_text(encoding='utf-8'))
        self.assertEqual(set(payload),{'schemaVersion','workspaceDir','zcodeCli','chromeExe','desktopBridgeFile','modelSelection'})
        self.assertEqual(set(payload['modelSelection']),{'providerId','modelId'})
        self.assertFalse((self.base/'learning').exists(),'Saving paths is not moving or creating a workspace')

    def test_workspace_precedence_preserves_explicit_saved_and_existing_legacy_data(self):
        explicit=self.base/'explicit';saved=self.base/'saved';legacy=self.base/'legacy';docs=self.base/'redirected-documents'
        legacy.mkdir()
        with patch('picker_settings.documents_directory',return_value=docs):
            self.assertEqual(PickerSettings.choose_workspace(explicit,str(saved),legacy),explicit.resolve())
            self.assertEqual(PickerSettings.choose_workspace(None,str(saved),legacy),saved.resolve())
            self.assertEqual(PickerSettings.choose_workspace(None,None,legacy),legacy.resolve())
            self.assertEqual(PickerSettings.choose_workspace(None,None,self.base/'missing'),docs/'CDP-Picker-Learning')
            self.assertEqual(recommended_workspace(),docs/'CDP-Picker-Learning')
        self.assertEqual(list(legacy.iterdir()),[])

    def test_per_user_config_location_is_stable_across_executable_locations(self):
        with patch.dict(os.environ,{'LOCALAPPDATA':str(self.base)}):
            self.assertEqual(default_settings_path(),self.base/'CDP-Control-Picker'/'settings.json')

    def test_alternate_loopback_cdp_address_survives_reload(self):
        self.store.save({**self.settings(), 'cdpEndpoint': 'http://localhost:9223/'})
        self.assertEqual(self.store.load()['cdpEndpoint'], 'http://127.0.0.1:9223')
        with self.assertRaises(SettingsError):
            self.store.save({**self.settings(), 'cdpEndpoint': 'http://example.test:9223'})

    def test_unknown_keys_secrets_and_invalid_models_never_replace_existing_config(self):
        self.store.save(self.settings());before=self.store.path.read_bytes()
        invalid=[{**self.settings(),'apiKey':'must-not-persist'},
                 {**self.settings(),'modelSelection':{'providerId':'p','modelId':'m','apiKey':'secret'}},
                 {**self.settings(),'modelSelection':{'providerId':'','modelId':'m'}},
                 {**self.settings(),'zcodeCli':'zcode-cli\n--api-key=secret'},
                 {**self.settings(),'workspaceDir':str(Path(self.base.anchor))}]
        for value in invalid:
            with self.subTest(value=list(value)),self.assertRaises(SettingsError):self.store.save(value)
            self.assertEqual(self.store.path.read_bytes(),before)

    def test_corrupt_or_unknown_schema_fails_without_rewriting_or_creating_directories(self):
        self.store.path.parent.mkdir()
        for value in ('{not-json}',json.dumps({'schemaVersion':99,'apiKey':'private'})):
            self.store.path.write_text(value,encoding='utf-8')
            before=self.store.path.read_bytes()
            with self.assertRaises(SettingsError):self.store.load()
            self.assertEqual(self.store.path.read_bytes(),before)
        self.assertFalse((self.base/'learning').exists())


if __name__=='__main__':unittest.main()
