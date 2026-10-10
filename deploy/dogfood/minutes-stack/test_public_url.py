"""The agent MCP's public address is an explicit lock input, and an unusable one stops the boot.

Every link the rig hands a person is built from `VEXA_PUBLIC_MCP_URL`. It used to be derived from
the listen host, so a deployment listening on a Docker bridge handed out links to that bridge.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent_mcp

RUNTIME = Path(__file__).resolve().parents[1] / 'rig' / 'vexa_control_mcp.py'


def cfg(**over):
    return {'runtime': str(RUNTIME), 'host': '172.18.0.1', 'port': 18321, **over}


class PublicUrl(unittest.TestCase):
    def test_the_configured_public_url_is_the_published_address(self):
        self.assertEqual(agent_mcp.public_mcp_url(cfg(public_url='https://mcp.example.com/mcp')),
                         'https://mcp.example.com/mcp')

    def test_a_missing_public_url_refuses_boot(self):
        with self.assertRaises(SystemExit) as stop:
            agent_mcp.public_mcp_url(cfg())
        self.assertIn('public_url', str(stop.exception))

    def test_a_private_public_url_refuses_boot(self):
        for url in ('https://172.18.0.1:18321/mcp', 'https://10.1.2.3/mcp',
                    'https://169.254.0.9/mcp', 'https://agent-mcp:18321/mcp',
                    'https://rig.internal/mcp'):
            with self.subTest(url=url), self.assertRaises(SystemExit):
                agent_mcp.public_mcp_url(cfg(public_url=url))

    def test_a_loopback_or_plain_http_public_url_refuses_boot(self):
        for url in ('https://localhost/mcp', 'https://127.0.0.1:18321/mcp',
                    'http://mcp.example.com/mcp', 'https://mcp.example.com/'):
            with self.subTest(url=url), self.assertRaises(SystemExit):
                agent_mcp.public_mcp_url(cfg(public_url=url))

    def test_main_refuses_before_reading_anything_else(self):
        """The check runs first: nothing is exported and the rig is never imported."""
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'agent-mcp.json'
            path.write_text(json.dumps(cfg(public_url='http://172.18.0.1:18321/mcp',
                                           environment_file=str(Path(d) / 'absent.json'))))
            with mock.patch.dict(os.environ, {}, clear=False), \
                    mock.patch.object(agent_mcp, 'load', wraps=agent_mcp.load) as loaded:
                os.environ.pop('VEXA_PUBLIC_MCP_URL', None)
                with self.assertRaises(SystemExit):
                    agent_mcp.main(str(path))
                self.assertNotIn('VEXA_PUBLIC_MCP_URL', os.environ)
            self.assertEqual([c.args[0] for c in loaded.call_args_list], ['minutes_public_origin'])


if __name__ == '__main__':
    unittest.main()
