from django.test import SimpleTestCase

from config.settings import _allowed_hosts_config


class AllowedHostsSettingsTests(SimpleTestCase):
    def test_debug_allows_lan_hosts_even_with_localhost_env(self):
        hosts = _allowed_hosts_config({"DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1"}, debug=True)

        self.assertIn("*", hosts)

    def test_non_debug_respects_configured_hosts(self):
        hosts = _allowed_hosts_config({"DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1"}, debug=False)

        self.assertEqual(hosts, ["localhost", "127.0.0.1"])
