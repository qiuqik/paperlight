"""Translation caching, queue deduplication, visibility and account isolation."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch, Mock

from fastapi.testclient import TestClient
from backend.app import main
from backend.app.accounts import AccountStore
from backend.app.translations import TranslationStore, translation_targets, source_hash, translate_text

MODEL = {'id': 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'metadata': {'title': 'A paper'}, 'sections': [
    {'id': 's1', 'title': 'Introduction', 'blocks': [
        {'id': 'p1', 'type': 'paragraph', 'text': 'This sentence'},
        {'id': 'p2', 'type': 'paragraph', 'text': 'continues here.', 'continuesPrevious': True},
        {'id': 'f1', 'type': 'figure', 'caption': 'A figure'},
        {'id': 'p3', 'type': 'paragraph', 'text': 'Another paragraph.'},
        {'id': 'h1', 'type': 'heading', 'text': 'Details'}]}]}

class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        (self.folder/'document.json').write_text(json.dumps(MODEL))
        self.targets = translation_targets(MODEL)
        self.store = TranslationStore()

    def tearDown(self):
        self.store.executor.shutdown(wait=True)
        self.temp.cleanup()

    def wait(self):
        until = time.monotonic()+5
        while time.monotonic() < until:
            state = self.store.read(self.folder, self.targets)
            if not state['processing']:
                return state
            time.sleep(.01)
        self.fail('Translation worker did not finish')

    def test_continuations_and_titles_match_rendered_units(self):
        self.assertEqual(self.targets['block:p1'], 'This sentence continues here.')
        self.assertNotIn('block:p2', self.targets)
        self.assertNotIn('block:f1', self.targets)
        self.assertEqual(self.targets['section:s1'], 'Introduction')
        self.assertEqual(self.targets['title'], 'A paper')
        self.assertEqual(self.targets['block:h1'], 'Details')

    def test_bulk_cache_survives_new_store_and_no_repeat_calls(self):
        with patch('backend.app.translations.translate_text', side_effect=lambda text: '译文 '+text) as provider:
            self.store.request(self.folder, self.targets, None)
            state = self.wait()
            self.assertEqual(state['ready'], len(self.targets))
            self.assertTrue(state['bulkRequested'])
            self.store.request(self.folder, self.targets, None)
            self.assertEqual(provider.call_count, len(self.targets))
            restored = TranslationStore()
            try:
                cached = restored.read(self.folder, self.targets)
                self.assertEqual(cached['items']['block:p1']['text'], '译文 This sentence continues here.')
                restored.visibility(self.folder, self.targets, False, None)
                self.assertFalse(restored.read(self.folder, self.targets)['items']['block:p1']['expanded'])
                restored.visibility(self.folder, self.targets, True, 'block:p1')
                items = restored.read(self.folder, self.targets)['items']
                self.assertTrue(items['block:p1']['expanded'])
                self.assertFalse(items['title']['expanded'])
            finally:
                restored.executor.shutdown(wait=True)
        self.assertEqual(json.loads((self.folder/'document.json').read_text()), MODEL)

    def test_inflight_dedup_and_collapse_before_result(self):
        entered, release = Event(), Event()
        def provider(text):
            entered.set()
            release.wait(5)
            return '同一段译文'
        with patch('backend.app.translations.translate_text', side_effect=provider) as call:
            try:
                self.store.request(self.folder, self.targets, 'block:p1')
                self.assertTrue(entered.wait(2))
                self.store.request(self.folder, self.targets, 'block:p1')
                self.store.visibility(self.folder, self.targets, False, None)
            finally:
                release.set()
            result = self.wait()['items']['block:p1']
            self.assertFalse(result['expanded'])
            self.assertEqual(call.call_count, 1)

    def test_failure_is_retryable_and_source_changes_invalidate_cache(self):
        with patch('backend.app.translations.translate_text', side_effect=RuntimeError('secret-api-key')):
            self.store.request(self.folder, self.targets, 'block:p1')
            state = self.wait()
            self.assertEqual(state['items']['block:p1']['status'], 'failed')
            self.assertNotIn('secret-api-key', (self.folder/'translations.json').read_text())
        with patch('backend.app.translations.translate_text', return_value='成功'):
            self.store.request(self.folder, self.targets, 'block:p1')
            self.assertEqual(self.wait()['items']['block:p1']['text'], '成功')
        changed = {**self.targets, 'block:p1': 'Different source.'}
        self.assertNotIn('block:p1', self.store.read(self.folder, changed)['items'])

    def test_interrupted_queue_is_retryable(self):
        (self.folder/'translations.json').write_text(json.dumps({'items': {'block:p1': {
            'status': 'translating', 'sourceHash': source_hash(self.targets['block:p1']), 'text': '', 'expanded': True}}}))
        self.assertEqual(self.store.read(self.folder, self.targets)['items']['block:p1']['status'], 'failed')

    def test_deleted_document_is_not_recreated(self):
        entered, release = Event(), Event()
        def provider(text):
            entered.set(); release.wait(5); return '译文'
        with patch('backend.app.translations.translate_text', side_effect=provider):
            try:
                self.store.request(self.folder, self.targets, 'block:p1')
                self.assertTrue(entered.wait(2))
                (self.folder/'document.json').unlink()
            finally:
                release.set()
            self.store.executor.shutdown(wait=True)
        self.assertFalse((self.folder/'document.json').exists())
        self.assertNotEqual(json.loads((self.folder/'translations.json').read_text())['items']['block:p1']['status'], 'ready')

    def test_provider_rejects_truncated_response(self):
        response = Mock()
        response.json.return_value = {'choices': [{'finish_reason': 'length', 'message': {'content': '截断的译文'}}]}
        with patch('backend.app.translations.api_key', return_value='test-key'), patch('backend.app.translations.httpx.post', return_value=response):
            with self.assertRaises(ValueError):
                translate_text('source')

class TranslationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.previous = (main.DATA_DIR, main.ACCOUNTS, main.TRANSLATIONS)
        main.DATA_DIR = root/'documents'; main.DATA_DIR.mkdir()
        main.ACCOUNTS = AccountStore(root/'paperlight.db'); main.ACCOUNTS.initialize()
        main.TRANSLATIONS = TranslationStore()
        alice = main.ACCOUNTS.create_user('alice', 'alice-password', 'user')
        main.ACCOUNTS.create_user('bob', 'bob-password', 'user')
        self.id = 'a'*32
        self.folder = root/'users'/alice['id']/'documents'/self.id; self.folder.mkdir(parents=True)
        (self.folder/'document.json').write_text(json.dumps(MODEL))
        main.ACCOUNTS.add_document(self.id, alice['id'], 'b'*64, 'test.pdf', str(self.folder.relative_to(root)), 'ready')
        self.alice, self.bob, self.anonymous = TestClient(main.app), TestClient(main.app), TestClient(main.app)
        self.alice.post('/api/auth/login', json={'username': 'alice', 'password': 'alice-password'})
        self.bob.post('/api/auth/login', json={'username': 'bob', 'password': 'bob-password'})
        self.url = f'/api/documents/{self.id}/translations'

    def tearDown(self):
        main.TRANSLATIONS.executor.shutdown(wait=True)
        main.DATA_DIR, main.ACCOUNTS, main.TRANSLATIONS = self.previous
        self.temp.cleanup()

    def test_all_routes_require_owner_or_admin(self):
        for client, expected in ((self.anonymous, 401), (self.bob, 404)):
            self.assertEqual(client.get(self.url).status_code, expected)
            self.assertEqual(client.post(self.url, json={'all': True}).status_code, expected)
            self.assertEqual(client.patch(self.url, json={'expanded': False}).status_code, expected)
        self.assertEqual(self.alice.get(self.url).status_code, 200)

    def test_targets_use_the_same_normalization_as_the_reader(self):
        normalized = main.DocumentModel(**MODEL)
        normalized.sections[0].blocks[0].text = 'Normalized visible paragraph.'
        with patch('backend.app.main.finalize_document_model', return_value=normalized) as finalize:
            state = self.alice.get(self.url).json()
            self.assertIn('block:p1', state['targetIds'])
            self.assertEqual(finalize.call_count, 1)
            self.assertEqual(finalize.call_args.args[2], self.folder)
            with patch('backend.app.main.publication_api_key', return_value='test'), patch('backend.app.translations.translate_text', return_value='译文') as provider:
                self.alice.post(self.url, json={'targetId': 'block:p1'})
                until = time.monotonic()+5
                while self.alice.get(self.url).json()['processing'] and time.monotonic() < until:
                    time.sleep(.01)
                self.assertIn('Normalized visible paragraph.', provider.call_args.args[0])

    def test_body_and_missing_key_validation(self):
        for body in ({}, {'all': True, 'targetId': 'block:p1'}):
            self.assertEqual(self.alice.post(self.url, json=body).status_code, 422)
        self.assertEqual(self.alice.post(self.url, json={'targetId': '../secret'}).status_code, 404)
        with patch('backend.app.main.publication_api_key', return_value=''):
            self.assertEqual(self.alice.post(self.url, json={'all': True}).status_code, 503)
        self.assertEqual(self.alice.patch(self.url, json={'expanded': 'false'}).status_code, 422)

    def test_single_then_bulk_can_load_and_toggle_persisted_translations(self):
        with patch('backend.app.main.publication_api_key', return_value='test'), patch('backend.app.translations.translate_text', return_value='保存的译文'):
            self.assertEqual(self.alice.post(self.url, json={'targetId': 'block:p1'}).status_code, 202)
            until = time.monotonic()+5
            while self.alice.get(self.url).json()['processing'] and time.monotonic() < until:
                time.sleep(.01)
            state = self.alice.get(self.url).json()
            self.assertEqual(state['items']['block:p1']['text'], '保存的译文')
            self.assertFalse(state['bulkRequested'])
            self.assertEqual(self.alice.patch(self.url, json={'targetId': 'block:p1', 'expanded': False}).status_code, 200)
            self.assertFalse(self.alice.get(self.url).json()['items']['block:p1']['expanded'])
            self.assertTrue(self.alice.post(self.url, json={'all': True}).json()['bulkRequested'])
            until = time.monotonic()+5
            while self.alice.get(self.url).json()['processing'] and time.monotonic() < until:
                time.sleep(.01)
            completed = self.alice.get(self.url).json()
            self.assertEqual(completed['ready'], completed['total'])
            self.assertGreater(completed['total'], 0)