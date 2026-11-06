import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import checkin
from logging_config import AlignedFormatter

logging.disable(logging.CRITICAL)


LOGIN_STATUS = {'code': 0, 'data': {'leftDays': 411}}
SESSION_COOKIE = 'gld:sess=fixture-session; gld:sess.sig=fixture-signature'
BROWSER_UA = 'Mozilla/5.0 fixture-browser'
CHALLENGE_REPLY = {'code': 0, 'nonce': 'fixture-nonce'}


def response(payload, status=200):
    result = checkin.requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode('utf-8')
    return result


class AuthenticationTests(unittest.TestCase):
    def run_main(self, status=LOGIN_STATUS, checkin_payload=None, cookie=SESSION_COOKIE,
                 *, challenge=CHALLENGE_REPLY, retry_challenge=CHALLENGE_REPLY,
                 retry_payload=None, points=None, exchange_plan=''):
        calls = []
        results = {
            'status': status,
            'points': {'code': 0, 'points': 10} if points is None else points,
            'exchange': {'code': 0},
        }
        attempts = iter((checkin_payload, retry_payload))
        challenges = iter((challenge, retry_challenge))

        def request(method, url, **kwargs):
            calls.append((url, method, kwargs))
            endpoint = url.rsplit('/', 1)[-1]
            if endpoint == 'challenge':
                payload = next(challenges)
            elif endpoint == 'checkin':
                payload = next(attempts)
            else:
                payload = results[endpoint]
            if payload is None:
                raise checkin.requests.Timeout()
            return payload if isinstance(payload, checkin.requests.Response) else response(payload)

        env = {'GLADOS_COOKIES': cookie, 'GLADOS_EXCHANGE_PLAN': exchange_plan}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(checkin.time, 'sleep'), \
                patch.object(checkin.requests.Session, 'get', side_effect=lambda *a, **k: request('GET', *a, **k)), \
                patch.object(checkin.requests.Session, 'post', side_effect=lambda *a, **k: request('POST', *a, **k)):
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
        self.assertEqual([method for _, method, _ in calls], ['GET'])
        self.assertTrue(calls[0][0].endswith('/status'))

    def test_expired_cookie_in_checkin_stops_before_points_query(self):
        code, calls = self.run_main(checkin_payload={'code': -2, 'message': 'expired'})
        self.assertEqual(code, 2)
        self.assertEqual([url.rsplit('/', 1)[-1] for url, _, _ in calls], ['status', 'challenge', 'checkin'])

    def test_challenge_authentication_failure_stops_before_checkin(self):
        for challenge in ({'code': -2}, response({}, 401), response({}, 403)):
            with self.subTest(challenge=challenge):
                code, calls = self.run_main(challenge=challenge, checkin_payload={'code': 0})
                self.assertEqual(code, 2)
                self.assertEqual([url.rsplit('/', 1)[-1] for url, _, _ in calls], ['status', 'challenge'])

    def test_unavailable_or_invalid_challenge_preserves_legacy_form_post(self):
        for challenge in (None, response({}, 404), [], {'code': 0}, {'code': False, 'nonce': 'bad'}):
            with self.subTest(challenge=challenge):
                code, calls = self.run_main(challenge=challenge, checkin_payload={'code': 0})
                self.assertEqual(code, 0)
                posts = [kwargs for _, method, kwargs in calls if method == 'POST']
                self.assertEqual(len(posts), 1)
                self.assertEqual(posts[0]['data'], {'token': 'glados.cloud'})
                self.assertNotIn('json', posts[0])

    def test_challenge_waits_before_posting_the_proof(self):
        events = []
        replies = iter((response(CHALLENGE_REPLY), response({'code': 0})))

        def reply(url, method, *args, **kwargs):
            events.append(method)
            return next(replies)

        with checkin.API('glados.cloud', user_agent=BROWSER_UA) as api, \
                patch.object(api, '_make_request', side_effect=reply), \
                patch.object(checkin.time, 'sleep', side_effect=lambda seconds: events.append(('wait', seconds))):
            self.assertEqual(api.checkin(SESSION_COOKIE)['code'], checkin.CheckinStatus.SUCCESS)
        self.assertEqual(events, ['GET', ('wait', 2.5), 'POST'])

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
        for status, expected in ((401, 2), (403, 2), (500, 1), (429, 1), (307, 1), (308, 1)):
            with self.subTest(status=status):
                reply = response({}, status)
                reply.headers['Location'] = 'https://example.invalid/'
                code, calls = self.run_main(status=reply)
                self.assertEqual(code, expected)
                self.assertEqual([method for _, method, _ in calls], ['GET'])
                self.assertFalse(calls[0][2]['allow_redirects'])

    def test_status_must_confirm_login_before_post(self):
        for payload in (None, [], {}, {'code': 0}, {'code': False, 'data': {}}):
            with self.subTest(payload=payload):
                code, calls = self.run_main(status=payload)
                self.assertEqual(code, 1)
                self.assertEqual([method for _, method, _ in calls], ['GET'])

    def test_only_explicit_success_can_trigger_points_lookup_or_exchange(self):
        for payload in (
            {'code': 0, 'message': 'Please checkin via https://example.invalid'},
            {'code': False},
            {'code': True},
            response({}, 503),
            response({}, 429),
        ):
            with self.subTest(payload=payload):
                code, calls = self.run_main(checkin_payload=payload, exchange_plan='plan100')
                self.assertEqual(code, 1)
                self.assertEqual([url.rsplit('/', 1)[-1] for url, _, _ in calls], ['status', 'challenge', 'checkin'])

    def test_points_failure_preserves_completed_checkin(self):
        code, calls = self.run_main(checkin_payload={'code': 0}, points=response({}, 503))
        self.assertEqual(code, 0)
        self.assertEqual([url.rsplit('/', 1)[-1] for url, _, _ in calls], ['status', 'challenge', 'checkin', 'points'])

    def test_earned_points_prefers_the_checkin_record(self):
        with checkin.API('glados.cloud') as api:
            data = {'code': 0, 'list': [{'change': '7'}], 'points': 200}
            self.assertEqual(api._parse_checkin(data)['points'], '7')
            data['list'][0]['change'] = None
            self.assertEqual(api._parse_checkin(data)['points'], '200')

    def test_explicit_exchange_runs_only_after_confirmed_checkin(self):
        for checkin_code in (0, 1):
            with self.subTest(checkin_code=checkin_code):
                code, calls = self.run_main(checkin_payload={'code': checkin_code}, exchange_plan='plan100')
                self.assertEqual(code, 0)
                self.assertEqual([url.rsplit('/', 1)[-1] for url, _, _ in calls],
                                 ['status', 'challenge', 'checkin', 'points', 'exchange'])

    def test_legacy_and_json_cookie_are_sent_only_to_login_domain(self):
        entry = {'cookie': SESSION_COOKIE, 'origin': 'glados.cloud', 'name': '主账号', 'user-agent': BROWSER_UA}
        for raw, ua in (
            (SESSION_COOKIE, checkin.PLATFORM_UA['Windows']),
            (f'{SESSION_COOKIE} & {SESSION_COOKIE}', checkin.PLATFORM_UA['Windows']),
            (json.dumps([entry, entry]), BROWSER_UA),
        ):
            with self.subTest(config=raw[:1]):
                code, calls = self.run_main(checkin_payload={'code': 0}, cookie=raw)
                self.assertEqual(code, 0)
                self.assertEqual(len(calls), 4)
                for url, _, kwargs in calls:
                    self.assertTrue(url.startswith('https://glados.cloud/'))
                    self.assertEqual(kwargs['headers']['cookie'], SESSION_COOKIE)
                    self.assertEqual(kwargs['headers']['user-agent'], ua)
                    self.assertFalse(kwargs['allow_redirects'])
                self.assertEqual(calls[1][0], 'https://glados.cloud/api/user/checkin/challenge')
                self.assertEqual(calls[2][2]['json'], {
                    'token': 'glados.cloud',
                    'nonce': 'fixture-nonce',
                    'proof': hashlib.sha256(b'fixture-nonce.glados-observe-v1').hexdigest(),
                })

    def test_invalid_account_config_fails_before_any_request(self):
        for raw in (
            '[', '[]', json.dumps({'cookie': SESSION_COOKIE}),
            json.dumps([{'cookie': 10}]),
            json.dumps([{'cookie': 'gld:sess.sig=fixture'}]),
            json.dumps([{'cookie': SESSION_COOKIE, 'origin': 'railgun.info'}]),
            json.dumps([{'cookie': SESSION_COOKIE, 'origin': 'glados.cloud.example.invalid'}]),
            json.dumps([{'cookie': SESSION_COOKIE, 'user-agent': 'UA\r\nCookie: forged'}]),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(self.run_main(cookie=raw), (1, []))

    def test_explicit_user_agent_is_not_overridden(self):
        cookie = json.dumps([{'cookie': SESSION_COOKIE, 'user-agent': BROWSER_UA}])
        mismatch = {'code': 4, 'reason': 'device-mismatch', 'loginDevice': 'Android'}
        code, calls = self.run_main(cookie=cookie, checkin_payload=mismatch, retry_payload={'code': 0})
        self.assertEqual(code, 1)
        self.assertEqual([method for _, method, _ in calls], ['GET', 'GET', 'POST'])
        self.assertEqual(calls[-1][2]['headers']['user-agent'], BROWSER_UA)

    def test_default_user_agent_switches_once_on_explicit_mismatch(self):
        mismatch = {'code': 4, 'reason': 'device-mismatch', 'loginDevice': 'iOS'}
        code, calls = self.run_main(checkin_payload=mismatch, retry_payload={'code': 0},
                                    retry_challenge={'code': 0, 'nonce': 'retry-nonce'})
        self.assertEqual(code, 0)
        self.assertEqual([method for _, method, _ in calls], ['GET', 'GET', 'POST', 'GET', 'POST', 'GET'])
        for _, _, kwargs in calls[:3]:
            self.assertEqual(kwargs['headers']['user-agent'], checkin.PLATFORM_UA['Windows'])
        for _, _, kwargs in calls[3:]:
            self.assertEqual(kwargs['headers']['user-agent'], checkin.PLATFORM_UA['iPhone'])
        self.assertEqual(calls[4][2]['json']['nonce'], 'retry-nonce')
        code, calls = self.run_main(checkin_payload=mismatch, retry_payload=mismatch)
        self.assertEqual(code, 1)
        self.assertEqual([method for _, method, _ in calls], ['GET', 'GET', 'POST', 'GET', 'POST'])

    def test_only_status_has_transport_retries(self):
        with checkin.API('glados.cloud') as api:
            status_retry = api.session.get_adapter(api._get_full_url(api.STATUS_URL)).max_retries
            self.assertTrue(status_retry.is_retry('GET', 503))
            self.assertFalse(status_retry.is_retry('GET', 429, has_retry_after=True))
            for endpoint in (api.CHECKIN_URL, checkin.APIEndpoint.CHECKIN_CHALLENGE.value,
                             api.POINTS_URL, api.EXCHANGE_URL):
                retry = api.session.get_adapter(api._get_full_url(endpoint)).max_retries
                self.assertEqual(retry.total, 0)

    def test_logging_masks_formatted_credentials_without_mutating_record(self):
        formatter = AlignedFormatter('%(levelname)s %(message)s')
        record = logging.LogRecord(
            'test', logging.INFO, '', 0, '%s | %s | %s',
            (SESSION_COOKIE, 'Bearer fixture-token', json.dumps({'cookie': 'other"secret'})), None,
        )
        formatted = formatter.format(record)
        self.assertNotIn('fixture-', formatted)
        self.assertNotIn('secret', formatted)
        self.assertIn('[REDACTED]', formatted)
        self.assertEqual(record.levelname, 'INFO')
        self.assertIn(SESSION_COOKIE, record.getMessage())


if __name__ == '__main__':
    unittest.main()
