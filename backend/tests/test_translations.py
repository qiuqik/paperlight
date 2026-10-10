"""Translation caching, queue deduplication, visibility and account isolation."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event, Lock
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
        self.assertEqual(self.targets['block:f1'], 'A figure')
        self.assertEqual(self.targets['section:s1'], 'Introduction')
        self.assertEqual(self.targets['title'], 'A paper')
        self.assertEqual(self.targets['block:h1'], 'Details')

    def test_figure_and_table_captions_are_translatable_but_visuals_are_not(self):
        model = {'metadata': {}, 'sections': [{'id': 'captions', 'title': '', 'blocks': [
            {'id': 'fig', 'type': 'figure', 'number': 1, 'caption': 'The framework.', 'text': 'Unrelated OCR from image'},
            {'id': 'table', 'type': 'table', 'label': 'Table 2', 'caption': 'Stale caption',
             'captionContent': [{'text': 'Evaluation '}, {'display': '[3]'}, {'text': ' results.'}], 'items': ['Unrelated cells']},
            {'id': 'empty', 'type': 'figure', 'number': 3, 'caption': '  ', 'src': '/image.png'},
            {'id': 'equation', 'type': 'equation', 'text': 'Untranslated formula'}]}]}
        targets = translation_targets(model)
        self.assertEqual(targets, {'block:fig': 'Figure 1 The framework.', 'block:table': 'Table 2 Evaluation [3] results.'})

    def test_bulk_adds_new_captions_without_retranslating_saved_paragraphs(self):
        original = {key: value for key, value in self.targets.items() if key != 'block:f1'}
        with patch('backend.app.translations.translate_text', return_value='译文') as provider:
            self.store.request(self.folder, original, None)
            self.wait()
            provider.reset_mock()
            self.store.request(self.folder, self.targets, None)
            result = self.wait()
            provider.assert_called_once_with('A figure')
            self.assertEqual(result['ready'], len(self.targets))
            self.assertEqual(result['items']['block:f1']['text'], '译文')

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

    def test_workers_overlap_and_do_not_exceed_configured_limit(self):
        self.store.executor.shutdown(wait=True)
        self.store = TranslationStore(max_workers=3)
        entered, release, counter_lock = Event(), Event(), Lock()
        active = peak = calls = 0
        def provider(text):
            nonlocal active, peak, calls
            with counter_lock:
                active += 1; calls += 1; peak = max(peak, active)
                if active == 3: entered.set()
            release.wait(5)
            with counter_lock: active -= 1
            return '译文 ' + text
        with patch('backend.app.translations.translate_text', side_effect=provider):
            try:
                self.store.request(self.folder, self.targets, None)
                self.assertTrue(entered.wait(3), 'Three requests must run concurrently')
                self.assertEqual(calls, 3)
            finally:
                release.set()
            result = self.wait()
        self.assertEqual(peak, 3)
        self.assertEqual(result['ready'], len(self.targets))
        for key, source in self.targets.items():
            self.assertEqual(result['items'][key]['text'], '译文 ' + source)

    def test_worker_environment_defaults_and_limits(self):
        for value, expected in [('6', 6), ('3', 3), ('0', 1), ('100', 16), ('invalid', 6)]:
            with patch.dict('os.environ', {'PAPERLIGHT_TRANSLATION_WORKERS': value}):
                store = TranslationStore()
                try: self.assertEqual(store.workers, expected)
                finally: store.executor.shutdown(wait=True)

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

    def seed_translation(self):
        with patch('backend.app.main.publication_api_key', return_value='test'), patch('backend.app.translations.translate_text', return_value='中文译文😀适合做笔记。'):
            self.alice.post(self.url, json={'all': True})
            until = time.monotonic()+5
            while self.alice.get(self.url).json()['processing'] and time.monotonic() < until:
                time.sleep(.01)
        return {'type': 'note', 'style': 'underline', 'noteEnabled': True, 'color': '#ef7474', 'note': '中文笔记',
                'anchor': {'start': {'blockId': 'translation:block:p1', 'offset': 0},
                           'end': {'blockId': 'translation:block:p1', 'offset': 4},
                           'quote': '中文译文', 'prefix': '', 'suffix': '😀适合做笔记。'}}

    def test_translation_notes_persist_and_remain_valid_after_collapse(self):
        note = self.seed_translation()
        url = f'/api/documents/{self.id}/annotations'
        response = self.alice.post(url, json=note)
        self.assertEqual(response.status_code, 201, response.text)
        identifier = response.json()['id']
        self.assertEqual(response.json()['anchor'], note['anchor'])
        self.assertEqual(response.json()['style'], 'underline')
        self.alice.patch(self.url, json={'expanded': False})
        self.assertEqual(self.alice.get(url).json()[0]['note'], '中文笔记')
        self.assertEqual(self.alice.patch(url+'/'+identifier, json={'note': '更新笔记'}).status_code, 200)
        self.assertEqual(self.alice.get(url).json()[0]['note'], '更新笔记')
        state = self.alice.get(self.url).json()
        self.assertEqual(state['items']['block:p1']['text'], '中文译文😀适合做笔记。')
        self.assertEqual(self.bob.post(url, json=note).status_code, 404)
        self.assertEqual(self.alice.delete(url+'/'+identifier).status_code, 204)

    def test_translation_note_validation_excludes_missing_mixed_and_pdf_anchors(self):
        note = self.seed_translation()
        url = f'/api/documents/{self.id}/annotations'
        for identifier in ['translation:block:missing', 'translation:../secret', 'p1']:
            changed = json.loads(json.dumps(note))
            changed['anchor']['end']['blockId'] = identifier
            self.assertEqual(self.alice.post(url, json=changed).status_code, 422)
        for offset in [1000, True]:
            changed = json.loads(json.dumps(note)); changed['anchor']['end']['offset'] = offset
            self.assertEqual(self.alice.post(url, json=changed).status_code, 422)
        changed = json.loads(json.dumps(note)); changed['anchor']['pdfRects'] = []
        self.assertEqual(self.alice.post(url, json=changed).status_code, 422)

    def test_cross_translation_and_title_caption_annotations(self):
        note = self.seed_translation()
        url = f'/api/documents/{self.id}/annotations'
        for identifier in ['translation:title', 'translation:block:f1', 'translation:section:s1']:
            changed = json.loads(json.dumps(note))
            changed['anchor']['start']['blockId'] = identifier
            changed['anchor']['end']['blockId'] = identifier
            self.assertEqual(self.alice.post(url, json=changed).status_code, 201)
        note['anchor']['end']['blockId'] = 'translation:block:p3'
        self.assertEqual(self.alice.post(url, json=note).status_code, 201)

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