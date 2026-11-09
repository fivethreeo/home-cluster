import base64
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
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
    def render(self, tls, media=False, filename="nginx.conf.j2", **variables):
        environment = Environment(loader=FileSystemLoader(ROLE / "templates"), undefined=StrictUndefined)
        environment.filters["bool"] = bool
        template = environment.get_template(filename)
        return template.render({
            "app_name": "example",
            "app_domains": ["example.com", "www.example.com"],
            "app_home": "/srv/example",
            "app_static_location": "/static/",
            "app_media_location": "/media/",
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

    def test_internal_endpoints_are_not_exposed_through_public_gateway_prefixes(self):
        for django_location, ws_location in [("/", "/ws/"), ("/django/", "/socket/")]:
            with self.subTest(django_location=django_location):
                site = self.render(
                    False, app_django_location=django_location,
                    app_ts_ws_backend_enabled=True, app_ts_ws_backend_location=ws_location,
                )
                for prefix in [django_location, ws_location]:
                    self.assertIn(f"location ^~ {prefix}internal/ {{\n        return 404;", site)

    def test_shared_site_snippet_contains_only_namespaced_routes(self):
        site = self.render(
            False, media=True, filename="nginx-locations.conf.j2",
            app_django_location="/template/", app_static_location="/template/static/",
            app_media_location="/template/media/", app_ts_backend_enabled=True,
            app_ts_backend_location="/template/api/", app_ts_ws_backend_enabled=True,
            app_ts_ws_backend_location="/template/ws/",
        )
        self.assertNotIn("server {", site)
        self.assertNotIn("location / {", site)
        self.assertNotIn("location /static/", site)
        self.assertIn("location /template/static/", site)
        self.assertIn("location ^~ /template/media/", site)
        self.assertIn("location = /template {", site)
        self.assertIn("proxy_pass http://127.0.0.1:8000/;", site)
        self.assertIn("location ^~ /template/api/", site)
        self.assertIn("location ^~ /template/ws/", site)


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
            app_django_address=variables.get("app_django_address", "127.0.0.1"),
            django_internal_auth_token=variables.get("django_internal_auth_token", ""),
            app_session_cookie_name=variables.get("app_session_cookie_name", "sessionid"),
            app_csrf_cookie_name=variables.get("app_csrf_cookie_name", "csrftoken"),
            app_cookie_path=variables.get("app_cookie_path", "/"),
            app_static_location=variables.get("app_static_location", "/static/"),
            app_media_location=variables.get("app_media_location", "/media/"),
            app_ts_backend_enabled=variables.get("app_ts_backend_enabled", False),
            app_ts_backend_location=variables.get("app_ts_backend_location", "/api/"),
            app_ts_ws_backend_enabled=variables.get("app_ts_ws_backend_enabled", False),
            app_ts_ws_backend_location=variables.get("app_ts_ws_backend_location", "/ws/"),
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

    def test_backend_urls_use_public_gateway_prefixes_and_empty_values_when_disabled(self):
        for http_enabled, ws_enabled in [(False, False), (True, False), (False, True), (True, True)]:
            with self.subTest(http_enabled=http_enabled, ws_enabled=ws_enabled):
                values = self.render(
                    app_ts_backend_enabled=http_enabled, app_ts_backend_location="/v1/",
                    app_ts_ws_backend_enabled=ws_enabled, app_ts_ws_backend_location="/socket/",
                )
                self.assertEqual(values["DJANGO_TS_BACKEND_URL"], "/v1/" if http_enabled else "")
                self.assertEqual(values["DJANGO_TS_WS_BACKEND_URL"], "/socket/" if ws_enabled else "")

    def test_internal_auth_key_is_optional_and_private_destination_is_an_allowed_host(self):
        self.assertEqual(self.render()["DJANGO_INTERNAL_AUTH_TOKEN"], "")
        values = self.render(
            django_internal_auth_token='test-only-key "quoted"',
            app_django_address="192.168.1.101",
        )
        self.assertEqual(values["DJANGO_INTERNAL_AUTH_TOKEN"], 'test-only-key "quoted"')
        self.assertEqual(
            values["DJANGO_ALLOWED_HOSTS"].split(","),
            ["192.0.2.100", "127.0.0.1", "localhost", "192.168.1.101"],
        )

    def test_prefixed_runtime_urls_and_cookies_match_shared_nginx_routes(self):
        values = self.render(
            app_django_location="/template/", app_static_location="/template/static/",
            app_media_location="/template/media/", app_cookie_path="/template/",
            app_session_cookie_name="template_sessionid", app_csrf_cookie_name="template_csrftoken",
        )
        self.assertEqual(values["DJANGO_SCRIPT_NAME"], "/template")
        self.assertEqual(values["DJANGO_STATIC_URL"], "/template/static/")
        self.assertEqual(values["DJANGO_MEDIA_URL"], "/template/media/")
        self.assertEqual(values["RSBUILD_BASE_URL"], "/template/static")
        self.assertEqual(values["DJANGO_SESSION_COOKIE_NAME"], "template_sessionid")
        self.assertEqual(values["DJANGO_CSRF_COOKIE_NAME"], "template_csrftoken")
        self.assertEqual(values["DJANGO_SESSION_COOKIE_PATH"], "/template/")
        self.assertEqual(values["DJANGO_CSRF_COOKIE_PATH"], "/template/")


class ApplicationRuntimeTests(unittest.TestCase):
    def render_node(self, **variables):
        environment = Environment(undefined=StrictUndefined)
        environment.filters["bool"] = bool
        environment.filters["to_json"] = json.dumps
        template = environment.from_string((ROLE / "templates/node.env.j2").read_text())
        rendered = template.render({
            "app_name": "template_staging",
            "app_session_cookie_name": "template_sessionid",
            "app_node_component": {
                "name": "typescript-ws-backend", "host": "django",
                "bind_host": "127.0.0.1", "port": 8002,
            },
            "app_django_enabled": True,
            "app_django_host": "django",
            "app_bind": "127.0.0.1:8000",
            "app_django_address": "192.168.1.101",
            "app_django_origin_port": 8080,
            "django_internal_auth_token": 'test-only-auth "quoted"',
            "ws_internal_notify_token": 'test-only-notify "quoted"',
            "app_ws_allowed_origins": ["https://example.com", "https://www.example.com"],
        } | variables)
        return dict(line.split("=", 1) for line in shlex.split(rendered))

    def test_node_auth_uses_direct_local_or_remote_origin_without_public_mount_prefix(self):
        local = self.render_node()
        self.assertEqual(local["DJANGO_INTERNAL_URL"], "http://127.0.0.1:8000/")
        self.assertEqual(local["DJANGO_INTERNAL_AUTH_TOKEN"], 'test-only-auth "quoted"')
        self.assertEqual(local["DJANGO_SESSION_COOKIE_NAME"], "template_sessionid")
        self.assertEqual(local["WS_INTERNAL_NOTIFY_TOKEN"], 'test-only-notify "quoted"')
        self.assertEqual(local["WS_ALLOWED_ORIGINS"], "https://example.com,https://www.example.com")
        remote = self.render_node(app_node_component={
            "name": "typescript-backend", "host": "http",
            "bind_host": "192.168.1.102", "port": 8001,
        })
        self.assertEqual(remote["DJANGO_INTERNAL_URL"], "http://192.168.1.101:8080/")
        self.assertNotIn("WS_INTERNAL_NOTIFY_TOKEN", remote)

    def test_missing_keys_keep_anonymous_configuration_and_node_only_has_no_django_destination(self):
        values = self.render_node(django_internal_auth_token="", ws_internal_notify_token="")
        self.assertEqual(values["DJANGO_INTERNAL_AUTH_TOKEN"], "")
        self.assertEqual(values["WS_INTERNAL_NOTIFY_TOKEN"], "")
        node_only = self.render_node(app_django_enabled=False)
        self.assertEqual(node_only["DJANGO_INTERNAL_URL"], "")
        self.assertEqual(node_only["DJANGO_INTERNAL_AUTH_TOKEN"], "")
        self.assertEqual(node_only["WS_ALLOWED_ORIGINS"], "https://example.com,https://www.example.com")

    def test_gunicorn_and_celery_use_selected_module_not_application_instance_name(self):
        environment = Environment(undefined=StrictUndefined)
        template = environment.from_string((ROLE / "templates/gunicorn.service.j2").read_text())
        play = yaml.safe_load((ROLE.parents[1] / "deploy-code.yml").read_text())[0]
        celery_app = environment.from_string(play["vars"]["celery_app"])
        for module in ["prosango", "template"]:
            with self.subTest(module=module):
                unit = template.render(
                    app_name=f"{module}_staging", app_user=f"{module}_staging",
                    app_code_dir=f"/srv/{module}_staging/current/django-project",
                    app_virtualenv=f"/srv/{module}_staging/.venv",
                    app_environment_file=f"/srv/{module}_staging/.env",
                    app_bind="127.0.0.1:8000", app_workers=2, app_django_module=module,
                )
                self.assertIn(f"/bin/gunicorn {module}.wsgi:application ", unit)
                self.assertEqual(celery_app.render(app_django_module=module), module)

    def test_private_origin_is_enabled_when_node_is_remote_even_if_django_is_on_gateway(self):
        defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
        environment = Environment(undefined=StrictUndefined)
        environment.filters["bool"] = bool
        template = environment.from_string(defaults["app_django_origin_enabled"])
        for django_host, node_host, django_enabled, expected in [
            ("gateway", "gateway", True, "False"),
            ("gateway", "node", True, "True"),
            ("django", "django", True, "True"),
            ("gateway", "node", False, "False"),
        ]:
            with self.subTest(django_host=django_host, node_host=node_host):
                result = template.render(
                    inventory_hostname="gateway", app_django_enabled=django_enabled,
                    app_django_host=django_host, app_ts_backend_enabled=True,
                    app_ts_backend_host=node_host, app_ts_ws_backend_enabled=False,
                )
                self.assertEqual(result, expected)

    def test_default_ws_origins_match_gateway_http_or_https_scheme(self):
        defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
        environment = Environment(undefined=StrictUndefined)
        environment.filters["bool"] = bool
        environment.filters["regex_replace"] = lambda value, pattern, replacement: re.sub(
            pattern, replacement, value,
        )
        expression = environment.compile_expression(
            defaults["app_ws_allowed_origins"].removeprefix("{{").removesuffix("}}").strip()
        )
        for tls, scheme in [(False, "http"), (True, "https")]:
            self.assertEqual(
                expression(app_domains=["example.com", "www.example.com"], certbot_enabled=tls),
                [f"{scheme}://example.com", f"{scheme}://www.example.com"],
            )


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

    def test_default_result_backend_reuses_database_credentials_with_safe_url_encoding(self):
        defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
        environment = Environment(undefined=StrictUndefined)
        environment.filters["bool"] = bool
        template = environment.from_string(defaults["celery_result_backend"])
        for host in ["127.0.0.1", "2001:db8::1"]:
            with self.subTest(host=host):
                backend = template.render(
                    app_deploy_code=True, app_django_enabled=True,
                    app_database_user="user/name", app_database_password='p/a:ss@word"%',
                    app_database_host=host, app_database="app/name",
                )
                parsed = urllib.parse.urlsplit(backend)
                self.assertEqual(parsed.scheme, "db+postgresql+psycopg")
                self.assertEqual(parsed.hostname, host)
                self.assertEqual(parsed.port, 5432)
                self.assertEqual(urllib.parse.unquote(parsed.username), "user/name")
                self.assertEqual(urllib.parse.unquote(parsed.password), 'p/a:ss@word"%')
                self.assertEqual(urllib.parse.unquote(parsed.path[1:]), "app/name")

    def test_non_django_deployments_do_not_need_database_credentials_for_results(self):
        defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
        environment = Environment(undefined=StrictUndefined)
        environment.filters["bool"] = bool
        template = environment.from_string(defaults["celery_result_backend"])
        for deploy_code, django_enabled in [(False, True), (True, False)]:
            with self.subTest(deploy_code=deploy_code, django_enabled=django_enabled):
                self.assertEqual(template.render(
                    app_deploy_code=deploy_code, app_django_enabled=django_enabled,
                ), "")


if __name__ == "__main__":
    unittest.main()
