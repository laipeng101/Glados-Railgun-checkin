import json
import logging
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import checkin

logging.disable(logging.CRITICAL)


def response(payload):
    result = checkin.requests.Response()
    result.status_code = 200
    result._content = json.dumps(payload).encode('utf-8')
    return result


class AuthenticationTests(unittest.TestCase):
    def run_main(self, status=None, checkin_payload=None, cookie='test-session'):
        calls = []
        if status is None:
            status = {'code': 0, 'data': {'leftDays': 411}}
        def request(api, url, method, data=None, cookies='', user_agent=None):
            calls.append((url, method))
            if url.endswith('/status'):
                return response(status)
            if url.endswith('/checkin'):
                return None if checkin_payload is None else response(checkin_payload)
            if url.endswith('/points'):
                return response({'code': 0, 'points': 10})
            raise AssertionError(f'Unexpected endpoint: {url}')
        with patch.dict(os.environ, {'GLADOS_COOKIES': cookie}, clear=True), \
                patch.object(checkin.API, '_make_request', request):
            try:
                checkin.main()
                code = 0
            except SystemExit as error:
                code = error.code
        return code, calls

    def test_missing_cookie_is_an_authentication_failure_without_requests(self):
        self.assertEqual(self.run_main(cookie=''), (2, []))

    def test_expired_cookie_in_status_stops_before_checkin_post(self):
        code, calls = self.run_main(status={'code': -2})
        self.assertEqual(code, 2)
        self.assertEqual([method for _, method in calls], ['GET'])
        self.assertTrue(calls[0][0].endswith('/status'))

    def test_expired_cookie_in_checkin_stops_before_points_query(self):
        code, calls = self.run_main(checkin_payload={'code': -2, 'message': 'expired'})
        self.assertEqual(code, 2)
        self.assertEqual([method for _, method in calls], ['GET', 'POST'])

    def test_network_failure_uses_generic_exit_code(self):
        self.assertEqual(self.run_main()[0], 1)

    def test_unknown_payload_does_not_mislabel_cookie_as_expired(self):
        self.assertEqual(self.run_main(checkin_payload={'message': 'unexpected response'})[0], 1)

    def test_device_or_server_rejection_uses_generic_exit_code(self):
        self.assertEqual(self.run_main(checkin_payload={'code': 4, 'reason': 'unknown'})[0], 1)

    def test_success_keeps_zero_exit_code(self):
        self.assertEqual(self.run_main(checkin_payload={'code': 0, 'points': 10})[0], 0)

    def test_already_checked_in_keeps_zero_exit_code(self):
        self.assertEqual(self.run_main(checkin_payload={'code': 1})[0], 0)

    def test_http_access_failures_are_distinct_from_server_errors(self):
        for status, expected in ((401, True), (403, True), (500, False), (429, False)):
            with self.subTest(status=status), checkin.API('glados.cloud') as api:
                result = checkin.requests.Response()
                result.status_code = status
                result._content = b'error'
                with patch.object(api.session, 'get', return_value=result):
                    self.assertIsNone(api._make_request('https://glados.cloud/api/user/status', 'GET', cookies='test-session'))
                self.assertEqual(api.authentication_failed, expected)


if __name__ == '__main__':
    unittest.main()
