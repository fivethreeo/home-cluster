import base64
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse

from jinja2 import Environment, FileSystemLoader, StrictUndefined
import yaml


ROLE = Path(__file__).resolve().parents[1] / "roles/application"
spec = importlib.util.spec_from_file_location("ddns", ROLE / "files/domeneshop-ddns.py")
ddns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ddns)


class DynamicDnsTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "token": "test-token",
            "secret": "test-secret",
            "domains": ["example.com", "www.example.com"],
            "ip": "",
        }

    @patch.object(ddns.urllib.request, "urlopen")
    def test_updates_all_domains_using_detected_public_ip(self, urlopen):
        ddns.update(self.config)
        request = urlopen.call_args.args[0]
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        self.assertEqual(query, {"hostname": ["example.com,www.example.com"]})
        self.assertNotIn("test-secret", request.full_url)
        self.assertEqual(
            request.get_header("Authorization"),
            "Basic " + base64.b64encode(b"test-token:test-secret").decode(),
        )
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 30)

    @patch.object(ddns.urllib.request, "urlopen")
    def test_explicit_ipv4_and_ipv6_addresses_are_encoded(self, urlopen):
        self.config["ip"] = "192.0.2.1,2001:db8::1"
        ddns.update(self.config)
        request = urlopen.call_args.args[0]
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        self.assertEqual(query["myip"], ["192.0.2.1,2001:db8::1"])

    def test_failed_api_request_exits_without_logging_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(self.config))
            output = io.StringIO()
            with (
                patch.object(sys, "argv", ["ddns", str(path)]),
                patch.object(
                    ddns.urllib.request,
                    "urlopen",
                    side_effect=urllib.error.URLError("test-secret"),
                ),
                contextlib.redirect_stderr(output),
            ):
                self.assertEqual(ddns.main(), 1)
            self.assertNotIn("test-secret", output.getvalue())
            self.assertIn("update failed", output.getvalue())


class NginxSiteTests(unittest.TestCase):
    def render(self, tls, media=False, **variables):
        environment = Environment(undefined=StrictUndefined)
        environment.filters["bool"] = bool
        template = environment.from_string((ROLE / "templates/nginx.conf.j2").read_text())
        return template.render({
            "app_name": "example",
            "app_domains": ["example.com", "www.example.com"],
            "app_home": "/srv/example",
            "app_upstream": "http://127.0.0.1:8000",
            "app_certbot_config_dir": "/etc/letsencrypt/example",
            "certbot_enabled": tls,
            "app_media_enabled": media,
            "inventory_hostname": "web",
            "app_django_enabled": True,
            "app_django_host": "web",
            "app_django_location": "/",
            "app_ts_backend_enabled": False,
            "app_ts_backend_location": "/api/",
            "app_ts_backend_upstream": "http://127.0.0.1:8001",
            "app_ts_ws_backend_enabled": False,
            "app_ts_ws_backend_location": "/ws/",
            "app_ts_ws_backend_upstream": "http://127.0.0.1:8002",
        } | variables)

    def test_http_site_does_not_reference_missing_certificates(self):
        site = self.render(False)
        self.assertNotIn("ssl_certificate", site)
        self.assertNotIn("return 301", site)
        self.assertIn("proxy_pass http://127.0.0.1:8000;", site)
        self.assertIn("alias /srv/example/static/;", site)
        self.assertNotIn("location ^~ /media/", site)

    def test_https_redirect_and_certificate_use_same_application(self):
        site = self.render(True)
        self.assertIn("return 301 https://$host$request_uri;", site)
        self.assertIn(
            "ssl_certificate /etc/letsencrypt/example/live/example/fullchain.pem;", site
        )
        self.assertEqual(site.count("server_name example.com www.example.com;"), 2)
        self.assertIn("proxy_set_header X-Forwarded-Proto $scheme;", site)

    def test_public_media_is_served_for_http_and_https_when_enabled(self):
        for tls in [False, True]:
            with self.subTest(tls=tls):
                site = self.render(tls, media=True)
                self.assertIn("location ^~ /media/ {", site)
                self.assertIn("alias /srv/example/media/;", site)
                self.assertIn("autoindex off;", site)
                self.assertIn("disable_symlinks on;", site)
                self.assertIn("add_header X-Content-Type-Options nosniff always;", site)


    def test_optional_backends_strip_prefixes_and_upgrade_websockets(self):
        for tls in [False, True]:
            with self.subTest(tls=tls):
                site = self.render(tls, app_ts_backend_enabled=True, app_ts_ws_backend_enabled=True)
                self.assertIn("location ^~ /api/ {", site)
                self.assertIn("proxy_pass http://127.0.0.1:8001/;", site)
                self.assertIn("location ^~ /ws/ {", site)
                self.assertIn("proxy_pass http://127.0.0.1:8002/;", site)
                self.assertIn("proxy_set_header Upgrade $http_upgrade;", site)
                self.assertIn('proxy_set_header Connection "upgrade";', site)

    def test_remote_django_proxies_static_and_media_and_strips_mount_prefix(self):
        site = self.render(
            False, media=True, app_django_host="django",
            app_django_location="/django/", app_upstream="http://10.0.0.2:8080",
        )
        self.assertNotIn("alias /srv/example", site)
        self.assertIn("location /django/ {", site)
        self.assertIn("proxy_pass http://10.0.0.2:8080/;", site)
        self.assertIn("return 308 /django/;", site)
        self.assertIn("return 404;", site)

    def test_node_only_site_does_not_expose_django_assets(self):
        site = self.render(False, media=True, app_django_enabled=False, app_ts_backend_enabled=True)
        self.assertNotIn("location /static/", site)
        self.assertNotIn("location ^~ /media/", site)
        self.assertNotIn("proxy_pass http://127.0.0.1:8000;", site)
        self.assertIn("proxy_pass http://127.0.0.1:8001/;", site)


class ComponentHostTests(unittest.TestCase):
    def setUp(self):
        tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
        topology = next(task for task in tasks if task["name"] == "Validate selected component topology")
        environment = Environment(undefined=StrictUndefined)
        environment.filters["bool"] = bool
        self.conditions = [
            environment.compile_expression(condition)
            for condition in topology["ansible.builtin.assert"]["that"]
            if "ansible_user" in condition
        ]
        self.variables = {
            "app_user": "example",
            "ansible_user": "operator",
            "app_django_enabled": True,
            "app_django_host": "django",
            "app_ts_backend_enabled": True,
            "app_ts_backend_host": "http",
            "app_ts_ws_backend_enabled": True,
            "app_ts_ws_backend_host": "websocket",
            "hostvars": {host: {"ansible_user": "operator"} for host in ["django", "http", "websocket"]},
        }

    def permitted(self):
        return all(condition(**self.variables) for condition in self.conditions)

    def test_each_component_preserves_its_ssh_login_account(self):
        self.assertTrue(self.permitted())
        for host in ["django", "http", "websocket"]:
            with self.subTest(host=host):
                self.variables["hostvars"][host]["ansible_user"] = "example"
                self.assertFalse(self.permitted())
                self.variables["hostvars"][host]["ansible_user"] = "operator"

    def test_disabled_component_does_not_restrict_its_hosts_ssh_login(self):
        self.variables["hostvars"]["http"]["ansible_user"] = "example"
        self.variables["app_ts_backend_enabled"] = False
        self.assertTrue(self.permitted())


class ProsangoEnvironmentTests(unittest.TestCase):
    def render(self, **variables):
        environment = Environment(
            loader=FileSystemLoader(ROLE / "templates"), undefined=StrictUndefined
        )
        environment.filters["bool"] = bool
        environment.filters["ternary"] = lambda value, yes, no: yes if value else no
        environment.filters["to_json"] = json.dumps
        secret = 'secret ${literal} "quotes" \\ slash \u00e9'
        rendered = environment.get_template("prosango.env.j2").render(
            app_name=variables.get("app_name", "example"),
            app_django_location=variables.get("app_django_location", "/"),
            app_memcached_enabled=variables.get("app_memcached_enabled", False),
            app_memcached_location="127.0.0.1:11211",
            django_secret_key=secret,
            app_domains=["192.0.2.100"],
            app_database="example",
            app_database_user="example",
            app_database_password=secret,
            app_database_host="127.0.0.1",
            app_home="/srv/example",
            certbot_enabled=False,
            celery_result_backend="rpc://",
            celery_broker_host="127.0.0.1",
            app_rabbitmq_user="example",
            app_rabbitmq_password=secret,
            app_rabbitmq_vhost="example",
        )
        return dict(line.split("=", 1) for line in shlex.split(rendered))

    def test_secrets_survive_environment_file_quoting(self):
        secret = 'secret ${literal} "quotes" \\ slash \u00e9'
        values = self.render()
        self.assertEqual(values["DJANGO_SECRET_KEY"], secret)
        self.assertEqual(values["DJANGO_DATABASE_PASSWORD"], secret)
        self.assertEqual(values["DJANGO_SESSION_COOKIE_SECURE"], "false")
        self.assertEqual(values["DJANGO_CSRF_COOKIE_SECURE"], "false")

    def test_memcached_is_disabled_by_default_and_namespaced_when_enabled(self):
        self.assertEqual(self.render()["DJANGO_MEMCACHED_LOCATION"], "")
        for app_name in ["prosango", "prosango_staging"]:
            with self.subTest(app_name=app_name):
                values = self.render(app_name=app_name, app_memcached_enabled=True)
                self.assertEqual(values["DJANGO_MEMCACHED_LOCATION"], "127.0.0.1:11211")
                self.assertEqual(values["DJANGO_CACHE_KEY_PREFIX"], app_name)

    def test_django_mount_prefix_is_empty_for_root_and_configured_for_subpath(self):
        self.assertEqual(self.render()["DJANGO_SCRIPT_NAME"], "")
        self.assertEqual(self.render(app_django_location="/django/")["DJANGO_SCRIPT_NAME"], "/django")


class CeleryServiceTests(unittest.TestCase):
    def render(self, filename, **variables):
        environment = Environment(undefined=StrictUndefined)
        template = environment.from_string((ROLE / "templates" / filename).read_text())
        defaults = {
            "app_name": "example",
            "app_user": "example",
            "app_code_dir": "/srv/example/current",
            "app_virtualenv": "/srv/example/.venv",
            "app_environment_file": "/srv/example/.env",
            "app_secrets_dir": "/etc/ansible-app/example",
            "celery_app": "project.celery:app",
        }
        return template.render(defaults | variables)

    def test_worker_uses_app_runtime_without_putting_credentials_in_unit(self):
        unit = self.render("celery.service.j2", celery_mode="worker")
        self.assertIn("User=example\nGroup=example", unit)
        self.assertIn("WorkingDirectory=/srv/example/current", unit)
        self.assertIn("EnvironmentFile=/srv/example/.env", unit)
        self.assertIn("EnvironmentFile=/etc/ansible-app/example/celery.env", unit)
        self.assertIn("--app=project.celery:app worker --loglevel=INFO", unit)
        self.assertNotIn("--schedule=", unit)
        self.assertNotIn("amqp://", unit)
        self.assertIn("TimeoutStopSec=300", unit)
        self.assertIn("KillMode=mixed", unit)

    def test_beat_schedule_survives_code_release_changes(self):
        unit = self.render(
            "celery.service.j2", celery_mode="beat", app_code_dir="/srv/example/releases/new"
        )
        self.assertIn("--app=project.celery:app beat --loglevel=INFO", unit)
        self.assertIn("--schedule=/var/lib/celery/example/celerybeat-schedule", unit)
        self.assertNotIn("--schedule=/srv/example/releases", unit)

    def test_broker_credentials_and_vhost_are_encoded_with_ipv6_support(self):
        environment = self.render(
            "celery.env.j2",
            celery_broker_host="2001:db8::1",
            app_rabbitmq_user="user/name",
            app_rabbitmq_password='p/a:ss@word"%',
            app_rabbitmq_vhost="/application",
        )
        url = environment.strip().split("=", 1)[1].strip('"')
        parsed = urllib.parse.urlsplit(url)
        self.assertEqual(parsed.hostname, "2001:db8::1")
        self.assertEqual(parsed.port, 5672)
        self.assertEqual(urllib.parse.unquote(parsed.username), "user/name")
        self.assertEqual(urllib.parse.unquote(parsed.password), 'p/a:ss@word"%')
        self.assertEqual(urllib.parse.unquote(parsed.path[1:]), "/application")
        self.assertNotIn('p/a:ss@word"%', environment)


if __name__ == "__main__":
    unittest.main()
