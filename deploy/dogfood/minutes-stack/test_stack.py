import copy
import unittest
import tempfile
from pathlib import Path
from stack import compare
from compose import compose_command


class DeploymentChecks(unittest.TestCase):
    def setUp(self):
        self.expected = {'files': {'compose.json': 'hash'}, 'containers': {'mcp': 'image'},
                         'units': {'agent-mcp': ['python', 'server.py']},
                         'routes': {'worker_mcp': 'http://expected/mcp',
                                    'agent_worker_image': 'worker', 'runtime_worker_image': 'worker'},
                         'required_worker_tools': ['connection_request', 'connections_status'],
                         'required_worker_route': 'http://expected/mcp'}
        self.actual = copy.deepcopy(self.expected)
        self.actual.update(worker_tools=self.expected['required_worker_tools'],
                           worker_auth_rejects_invalid=True, health_findings=[])

    def test_exact_deployment(self):
        self.assertEqual(compare(self.expected, self.actual), [])

    def test_healthy_wrong_mcp_is_not_success(self):
        self.actual['routes']['worker_mcp'] = 'http://legacy/mcp'
        self.actual['worker_tools'] = ['workspace_read']
        findings = compare(self.expected, self.actual)
        self.assertTrue(any('worker_mcp' in x for x in findings))
        self.assertTrue(any('missing tools' in x for x in findings))
        self.assertTrue(any('bypasses' in x for x in findings))

    def test_mutable_input_and_image_drift(self):
        self.actual['files']['compose.json'] = 'changed'
        self.actual['containers']['mcp'] = 'different'
        self.actual['routes']['runtime_worker_image'] = 'different'
        self.assertEqual(len(compare(self.expected, self.actual)), 4)

    def test_auth_failure_and_unready(self):
        self.actual['worker_auth_rejects_invalid'] = False
        self.actual['health_findings'] = ['Container not ready: mcp']
        self.assertEqual(len(compare(self.expected, self.actual)), 2)

    def test_compose_refuses_changed_input_and_implicit_service(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'compose.json')
            Path(path).write_text('{}')
            lock = {'files': {path: 'wrong'}, 'compose_projects': {'minutes': {
                'name': 'vexa-minutes', 'services': ['mcp'], 'inputs': [path]}}}
            with self.assertRaises(ValueError):
                compose_command(lock, 'minutes', ['mcp'])
            with self.assertRaises(ValueError):
                compose_command(lock, 'minutes', [])


if __name__ == '__main__':
    unittest.main()
