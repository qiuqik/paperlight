"""First-admin bootstrapping does not depend on public signup ordering."""

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import bootstrap_admin, main
from backend.app.accounts import AccountStore


class BootstrapAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = AccountStore(Path(self.temp.name) / 'accounts.db')
        self.store.initialize()
        self.account_patch = patch.object(main, 'ACCOUNTS', self.store)
        self.account_patch.start()

    def tearDown(self):
        self.account_patch.stop()
        self.temp.cleanup()

    def invoke(self, username='admin', password='test-owner-password'):
        with patch('sys.argv', ['bootstrap_admin', '--username', username]), patch('sys.stdin', io.StringIO(password+'\n')), patch.object(main, '_migrate_legacy_documents') as migrate:
            bootstrap_admin.run()
            return migrate

    def test_first_admin_can_be_created_after_public_registration(self):
        reader = self.store.create_user('reader', 'reader-password')
        migrate = self.invoke()
        users = self.store.list_users()
        admin = next(user for user in users if user['role']=='admin')
        migrate.assert_called_once_with(admin['id'])
        self.assertEqual(self.store.get_user(reader['id'])['role'], 'user')
        self.assertIsNotNone(self.store.authenticate('admin', 'test-owner-password'))

    def test_existing_admin_blocks_cli_and_database_bootstrap(self):
        self.invoke()
        with self.assertRaises(SystemExit):
            self.invoke('another_admin')
        with self.assertRaisesRegex(ValueError, 'administrator already exists'):
            self.store.create_user('another_admin', 'password', 'admin', initial_admin=True)
        self.assertEqual(self.store.user_count(), 1)

    def test_taken_username_is_not_promoted_and_owner_can_choose_another(self):
        reader = self.store.create_user('admin', 'reader-password')
        with self.assertRaisesRegex(SystemExit, 'choose another'):
            self.invoke()
        self.assertEqual(self.store.get_user(reader['id'])['role'], 'user')
        self.invoke('owner')
        self.assertEqual(self.store.authenticate('owner', 'test-owner-password')['role'], 'admin')

    def test_interactive_password_confirmation_and_hidden_prompt(self):
        stdin = io.StringIO()
        with patch('sys.argv', ['bootstrap_admin']), patch('sys.stdin', stdin), patch.object(stdin, 'isatty', return_value=True), patch.object(bootstrap_admin.getpass, 'getpass', side_effect=['password-one', 'password-two']):
            with self.assertRaisesRegex(SystemExit, 'Passwords do not match'):
                bootstrap_admin.run()
        self.assertEqual(self.store.user_count(), 0)
        with patch('sys.argv', ['bootstrap_admin']), patch('sys.stdin', stdin), patch.object(stdin, 'isatty', return_value=True), patch.object(bootstrap_admin.getpass, 'getpass', side_effect=['matching-password']*2), patch.object(main, '_migrate_legacy_documents'):
            bootstrap_admin.run()
        self.assertEqual(self.store.user_count(), 1)

    def test_empty_or_short_password_does_not_create_an_account(self):
        for password in ['', 'abc']:
            with self.assertRaises(SystemExit):
                self.invoke(password=password)
        self.assertEqual(self.store.user_count(), 0)


if __name__ == '__main__':
    unittest.main()
