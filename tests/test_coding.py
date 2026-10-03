"""Provider wire contracts and filesystem/Docker isolation; no paid requests."""
import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from domains.coding.runtime import Runtime, job_name, configure_harbor
from domains.coding.harbor_entry import agent_kwargs
from domains.coding import docker_resources as docker
from rrsi import deepseek, llm


class WireServer:
    def __init__(self, statuses=None, content='{"ok":true}', finish="stop"):
        self.requests = []
        self.statuses = list(statuses or [200])
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                owner.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                status = owner.statuses[min(len(owner.requests)-1, len(owner.statuses)-1)]
                data = {"error": {"message": "test-secret-key invalid", "type": "invalid_request_error"}}
                if status == 200:
                    data = {"id": "fake", "object": "chat.completion", "created": 1,
                            "model": "deepseek-flash", "choices": [{"index": 0,
                            "message": {"role": "assistant", "content": content, "reasoning_content": "private reasoning"},
                            "finish_reason": finish}],
                            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        self.env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret-key",
            "DEEPSEEK_BASE_URL": f"http://127.0.0.1:{self.server.server_port}",
            "DEEPSEEK_REASONING_EFFORT": "low", "RRSI_LLM_USAGE_PATH": ""})
        self.env.start()
        return self

    def __exit__(self, *_):
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        runtime = Runtime(ROOT)
        runtime.activate()
        self.tmp = tempfile.TemporaryDirectory(dir=runtime.base / "tmp")
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_path_traversal_and_symlink_escape(self):
        runtime = Runtime(ROOT)
        for path in (ROOT.parent / "escape", "../escape", ROOT):
            with self.subTest(path=path), self.assertRaises(ValueError):
                runtime.inside(path)
        link = Path(self.tmp.name) / "external"
        link.symlink_to(ROOT.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            runtime.inside(link / "escape")

    def test_job_names_cannot_escape(self):
        for name in ("../outside", "/tmp/job", ".", "..", "job/path"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                job_name(name)

    def test_venv_python_symlink_is_allowed(self):
        runtime = Runtime(ROOT)
        # Restrict environment directories, but allow the usual system-Python link.
        with patch.dict(os.environ, {"RRSI_CODING_PYTHON": str(ROOT / '.venv/bin/python')}):
            self.assertEqual(runtime.python.parent, ROOT / '.venv/bin')
        with patch.dict(os.environ, {"RRSI_CODING_VENV": str(ROOT.parent / 'outside')}):
            with self.assertRaises(ValueError):
                Runtime(ROOT)

    def test_cli_rejects_external_runs_before_creating_them(self):
        result = subprocess.run([sys.executable, str(ROOT / 'rrsi.py'), '--domain', 'coding',
            '--runs', str(ROOT.parent / 'rrsi-should-not-create'), 'preflight'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('writable path', result.stderr)
        self.assertFalse((ROOT.parent / 'rrsi-should-not-create').exists())

    def test_cleanup_only_uses_manifest_projects(self):
        runtime = Runtime(ROOT)
        manifest = Path(self.tmp.name) / 'docker.jsonl'
        project = docker.prefix(runtime) + 'trial'
        manifest.write_text(json.dumps({'project': project}) + '\n')
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            listing = '--filter' in args
            return subprocess.CompletedProcess(args, 0, 'owned-id\n' if listing else '', '')
        with patch.object(Runtime, 'docker', return_value='/docker'), patch.object(docker.subprocess, 'run', side_effect=run):
            docker.cleanup(runtime, manifest)
        self.assertTrue(all(f'label=com.docker.compose.project={project}' in c for c in calls if '--filter' in c))
        self.assertTrue(all('prune' not in c for c in calls))
        self.assertTrue(all(c[-1] == 'owned-id' for c in calls if 'rm' in c))
        manifest.write_text(json.dumps({'project': 'foreign-project'}) + '\n')
        with self.assertRaises(ValueError):
            docker.projects(runtime, manifest)

    def test_docker_dispatch_records_namespace_and_drops_key(self):
        manifest = Path(self.tmp.name) / 'docker.jsonl'
        runtime = Runtime(ROOT)
        with patch.dict(os.environ, {'RRSI_DOCKER_MANIFEST': str(manifest), 'DEEPSEEK_API_KEY': 'secret'}), \
                patch.object(docker, 'Runtime', return_value=runtime), \
                patch.object(Runtime, 'docker', return_value='/docker'), \
                patch.object(docker.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            docker.dispatch(['compose', '--project-name', 'trial', 'up', '-d'])
        self.assertTrue(run.call_args.args[0][3].startswith(docker.prefix(Runtime(ROOT))))
        self.assertNotIn('DEEPSEEK_API_KEY', run.call_args.kwargs['env'])
        self.assertEqual(len(docker.projects(Runtime(ROOT), manifest)), 1)

    def test_old_jobs_are_never_reused(self):
        from domains.coding.adapter import CodingDomain
        runs = Path(self.tmp.name)
        (runs / 'jobs/old/trial').mkdir(parents=True)
        (runs / 'jobs/old/trial/result.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError, 'provenance'):
            CodingDomain()._harbor(ROOT, runs, 'old', ['fix-git'], 1, 'dataset', 'old')

    def test_smoke_restores_policy_effort_after_success_and_failure(self):
        from domains.coding.adapter import CodingDomain
        domain = CodingDomain()
        for failure in (False, True):
            with self.subTest(failure=failure), patch.dict(os.environ, {'DEEPSEEK_REASONING_EFFORT': 'high'}):
                def run(*args):
                    self.assertEqual(os.environ['DEEPSEEK_REASONING_EFFORT'], 'low')
                    if failure:
                        raise RuntimeError('interrupted')
                    return True, {}
                with patch.object(domain, '_smoke', side_effect=run):
                    if failure:
                        with self.assertRaises(RuntimeError):
                            domain.smoke(ROOT, Path(self.tmp.name), 'smoke', ['fix-git'])
                    else:
                        self.assertEqual(domain.smoke(ROOT, Path(self.tmp.name), 'smoke', ['fix-git']), (True, {}))
                self.assertEqual(os.environ['DEEPSEEK_REASONING_EFFORT'], 'high')

    def test_frontier_cannot_mix_policy_efforts(self):
        from domains.coding import adapter
        from domains.coding.adapter import CodingDomain
        domain = CodingDomain()
        with patch.dict(os.environ, {'DEEPSEEK_REASONING_EFFORT': 'low'}):
            frontier = {'coding_policy': domain.run_metadata()}
            domain.validate_frontier(frontier)
        with patch.dict(os.environ, {'DEEPSEEK_REASONING_EFFORT': 'high'}):
            with self.assertRaisesRegex(RuntimeError, 'policy'):
                domain.validate_frontier(frontier)
        with patch.dict(os.environ, {'DEEPSEEK_REASONING_EFFORT': 'low'}), patch.object(adapter, 'POLICY_MAX_TOKENS', 30000):
            with self.assertRaisesRegex(RuntimeError, 'policy'):
                domain.validate_frontier(frontier)

    def test_credentials_are_redacted_recursively(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'secret'}):
            value = deepseek.redact({'api_key': 'secret', 'headers': {'Authorization': 'Bearer secret'},
                                     'message': 'failed secret'})
        self.assertNotIn('secret', json.dumps(value))

    def test_calibration_refuses_legacy_results_and_cached_noise_band(self):
        from domains.coding.adapter import CodingDomain
        from rrsi.config import RRSIConfig
        from rrsi.loop import Run
        domain = CodingDomain()
        run = Run(domain, RRSIConfig.load(ROOT / 'domains/coding/rrsi.json'), ROOT, Path(self.tmp.name))
        with self.assertRaisesRegex(RuntimeError, 'provenance'):
            run.calibrate(['legacy'])
        self.assertFalse(run.calibration_path.exists())
        run.calibration_path.write_text('{"delta":0.017}')
        with self.assertRaisesRegex(RuntimeError, 'policy'):
            run.delta()


@unittest.skipUnless(importlib.util.find_spec('openai'), 'OpenAI SDK not installed')
class SDKTests(unittest.TestCase):
    def test_real_sdk_wire_and_final_content(self):
        with WireServer() as server:
            result = llm.generate('JSON please', system='system', model='deepseek/deepseek-flash',
                                  json_only=True, cache_prefix='stable', max_tokens=8192)
            body = server.requests[0]
        self.assertEqual(json.loads(result), {'ok': True})
        self.assertEqual(body['model'], 'deepseek-flash')
        self.assertEqual(body['reasoning_effort'], 'low')
        self.assertEqual(body['thinking'], {'type': 'enabled'})
        self.assertEqual(body['response_format'], {'type': 'json_object'})
        self.assertEqual(body['messages'][1]['content'], 'stable\n\nJSON please')
        self.assertNotIn('cache_control', json.dumps(body))

    def test_permanent_errors_do_not_retry_or_leak_credentials(self):
        for status in (400, 401, 402, 403):
            with self.subTest(status=status), WireServer([status]) as server:
                with self.assertRaises(RuntimeError) as error:
                    llm.generate('test', model='deepseek-flash')
                self.assertNotIn('test-secret-key', str(error.exception))
                self.assertEqual(len(server.requests), 1)

    def test_only_transient_errors_retry_and_are_bounded(self):
        with WireServer([429, 503, 200]) as server, patch.object(deepseek.time, 'sleep'):
            self.assertEqual(llm.generate('test', model='deepseek-flash'), '{"ok":true}')
            self.assertEqual(len(server.requests), 3)
        with WireServer([503]) as server, patch.object(deepseek.time, 'sleep'):
            with self.assertRaises(RuntimeError):
                llm.generate('test', model='deepseek-flash', max_retries=6)
            self.assertEqual(len(server.requests), 3)

    def test_empty_and_truncated_outputs_fail(self):
        for content, finish in (('', 'stop'), ('{"ok":', 'length')):
            with self.subTest(content=content), WireServer(content=content, finish=finish) as server:
                with self.assertRaises(RuntimeError):
                    llm.generate('test', model='deepseek-flash', json_only=True)
                self.assertEqual(len(server.requests), 1)


@unittest.skipUnless(importlib.util.find_spec('harbor'), 'Harbor not installed')
class HarborTests(unittest.TestCase):
    def test_harbor_provider_retry_budget_and_permanent_errors(self):
        for statuses, expected in (([400], 1), ([401], 1), ([402], 1), ([403], 1), ([503], 3)):
            with self.subTest(status=statuses[0]), patch.dict(os.environ, {}, clear=False), WireServer(statuses) as server:
                runtime = Runtime(ROOT)
                configure_harbor(runtime)
                sys.path.insert(0, str(ROOT / 'third_party'))
                from harbor_terminus2 import AgentHarness
                from harbor.llms.chat import Chat
                from harbor.llms.lite_llm import LiteLLM
                with tempfile.TemporaryDirectory(dir=runtime.base / 'tmp') as tmp:
                    agent = AgentHarness(logs_dir=Path(tmp), model_name='deepseek/deepseek-flash',
                                         **agent_kwargs('deepseek/deepseek-flash', True))
                    with patch.object(LiteLLM.call._rrsi_deepseek_call.retry, 'sleep', new=AsyncMock()):
                        with self.assertRaises(Exception) as error:
                            asyncio.run(agent._query_llm(Chat(agent._llm, interleaved_thinking=False), 'request'))
                    self.assertNotIn('test-secret-key', str(error.exception))
                    self.assertEqual(len(server.requests), expected)

    def test_truncation_recovery_uses_request_budget(self):
        with patch.dict(os.environ, {}, clear=False), WireServer():
            runtime = Runtime(ROOT)
            configure_harbor(runtime)
            sys.path.insert(0, str(ROOT / 'third_party'))
            from harbor_terminus2 import AgentHarness
            from harbor.llms.base import LLMResponse, OutputLengthExceededError
            with tempfile.TemporaryDirectory(dir=runtime.base / 'tmp') as tmp:
                agent = AgentHarness(logs_dir=Path(tmp), model_name='deepseek/deepseek-flash',
                                     **agent_kwargs('deepseek/deepseek-flash', True))
                chat = Mock(messages=[])
                chat.chat = AsyncMock(side_effect=[OutputLengthExceededError('truncated', '{"analysis":'),
                                                   LLMResponse(content='{"ok":true}')])
                response = asyncio.run(agent._query_llm(chat, 'original request'))
                self.assertEqual(response.content, '{"ok":true}')
                recovery = chat.chat.call_args.args[0]
                self.assertIn('20000 tokens', recovery)
                self.assertNotIn('393216', recovery)

    def test_real_litellm_multi_turn_wire_keeps_low(self):
        with patch.dict(os.environ, {}, clear=False), WireServer() as server:
            runtime = Runtime(ROOT)
            configure_harbor(runtime)
            from harbor.llms.chat import Chat
            from harbor.llms.lite_llm import LiteLLM
            values = agent_kwargs('deepseek/deepseek-flash', True)
            backend = LiteLLM(model_name='deepseek/deepseek-flash', api_base=values['api_base'],
                reasoning_effort=values['reasoning_effort'], model_info=values['model_info'], **values['llm_kwargs'])
            async def call():
                chat = Chat(backend, interleaved_thinking=False)
                for _ in range(2):
                    response = await chat.chat('Return JSON {"ok":true}', **values['llm_call_kwargs'])
                    self.assertEqual(json.loads(response.content), {'ok': True})
            asyncio.run(call())
            self.assertEqual(len(server.requests), 2)
            for body in server.requests:
                self.assertEqual(body['reasoning_effort'], 'low')
                self.assertEqual(body['thinking'], {'type': 'enabled'})
                self.assertEqual(body['max_tokens'], 20000)
                self.assertNotIn('tools', body)
            self.assertTrue(any(m['role'] == 'assistant' for m in server.requests[1]['messages']))

    def test_harbor_caches_and_logger_stay_local_and_private(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'secret'}):
            runtime = Runtime(ROOT)
            configure_harbor(runtime)
            import harbor.constants as constants
            from harbor.environments.docker.utils import _docker_build_cache_dir
            from harbor.llms.lite_llm import LiteLLM
            for path in (constants.CACHE_DIR, constants.TASK_CACHE_DIR, _docker_build_cache_dir()):
                self.assertTrue(path.is_relative_to(ROOT))
            with tempfile.TemporaryDirectory(dir=runtime.base / 'tmp') as tmp:
                path = Path(tmp) / 'call.json'
                backend = LiteLLM(model_name='deepseek/deepseek-flash')
                backend._init_logger_fn(path)({'log_event_type': 'post_api_call', 'api_key': 'secret',
                                             'headers': {'authorization': 'Bearer secret'}})
                self.assertNotIn('secret', path.read_text())


if __name__ == '__main__':
    unittest.main()
