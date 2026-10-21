import subprocess
import sys
from unittest.mock import patch

from django.test import SimpleTestCase

from scripts.run_services import supervise


class SupervisorTests(SimpleTestCase):
    def test_worker_exit_stops_web_sibling_and_causes_restart(self):
        processes = []
        spawn = subprocess.Popen

        def tracked(command):
            process = spawn(command)
            processes.append(process)
            return process

        with patch("scripts.run_services.subprocess.Popen", side_effect=tracked):
            result = supervise(
                [
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    [sys.executable, "-c", "pass"],
                ]
            )
        self.assertEqual(result, 1)
        self.assertEqual(len(processes), 2)
        self.assertTrue(all(process.poll() is not None for process in processes))
