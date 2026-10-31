import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import checkin_notification_policy as policy


class NotificationPolicyTests(unittest.TestCase):
    def decision(self, *, day='true', outcome='failure', code='1', event='schedule', schedule='18 1 * * *'):
        return policy.decide(day, outcome, code, event, schedule)

    def test_primary_network_failure_waits_for_fallback(self):
        self.assertFalse(self.decision().notify)

    def test_primary_cookie_failure_notifies_immediately(self):
        self.assertTrue(self.decision(code='2').notify)

    def test_failed_fallback_notifies(self):
        self.assertTrue(self.decision(schedule='18 4 * * *').notify)

    def test_successful_fallback_does_not_notify(self):
        self.assertFalse(self.decision(outcome='success', code='0', schedule='18 4 * * *').notify)

    def test_successful_primary_does_not_notify(self):
        self.assertFalse(self.decision(outcome='success', code='0').notify)

    def test_confirmed_day_skips_without_notification(self):
        self.assertFalse(self.decision(day='false', outcome='', code='', schedule='18 4 * * *').notify)

    def test_primary_history_or_setup_failure_waits(self):
        self.assertFalse(self.decision(day='', outcome='', code='').notify)

    def test_fallback_history_or_setup_failure_notifies(self):
        self.assertTrue(self.decision(day='', outcome='', code='', schedule='18 4 * * *').notify)

    def test_manual_failure_notifies(self):
        self.assertTrue(self.decision(event='workflow_dispatch', schedule='').notify)

    def test_unknown_schedule_cannot_suppress_a_failure(self):
        self.assertTrue(self.decision(schedule='changed cron').notify)

    def test_main_sets_native_workflow_failure_only_for_actionable_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            summary = Path(temporary) / 'summary'
            environ = {'GITHUB_EVENT_NAME': 'schedule', 'CHECKIN_SCHEDULE': '18 1 * * *',
                       'DAILY_SHOULD_RUN': 'true', 'CHECKIN_OUTCOME': 'failure',
                       'CHECKIN_EXIT_CODE': '1', 'GITHUB_STEP_SUMMARY': str(summary),
                       'GLADOS_COOKIES': 'DO-NOT-PRINT-THIS'}
            with patch.dict(os.environ, environ, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(policy.main(), 0)
                os.environ['CHECKIN_EXIT_CODE'] = '2'
                self.assertEqual(policy.main(), 1)
            self.assertNotIn(environ['GLADOS_COOKIES'], output.getvalue() + summary.read_text())


if __name__ == '__main__':
    unittest.main()
