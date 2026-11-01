"""Radio provenance, ambiguity, bounds, import and LAN-access contracts."""
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tarfile
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from carrier_radio import RadioModel, parse_message, demo_model
from carrier_radio_import import import_capture, extract_logarchive, json_records
from carrier_radio_dashboard import Collector, DashboardServer

WHEN = '2026-11-01T12:00:00+00:00'
EPOCH = datetime.fromisoformat(WHEN).timestamp()
QOS = ('slotID=CTSubscriptionSlotOne, interfaceName=pdp_ip0, apnName=example.data, state=Connected, '
       '<CTCellularQoSFlow 0x1234, flowId=7, bearerId=5, is5G=true, '
       'txQos=<CTQoS 0x12, qci=9, filters=(<CTIPFilter, ipFamilyProtocol=4>)>, rxQos=<CTQoS 0x13, qci=8>>')
CELL = ('slotID=CTSubscriptionSlotOne { kCTCellMonitorCellRadioAccessTechnology = kCTCellMonitorRadioAccessTechnologyNR; '
        'kCTCellMonitorBandInfo = 41; kCTCellMonitorCellType = kCTCellMonitorCellTypeServing; '
        'kCTCellMonitorRSRP = -95; kCTCellMonitorCellId = 12345; }')


class RadioContracts(unittest.TestCase):
    def test_qos_values_are_distinct_from_flow_and_bearer(self):
        row = parse_message(QOS, WHEN)[0]
        self.assertEqual((row['tx'], row['rx'], row['flow'], row['bearer']), (9, 8, 7, 5))
        self.assertEqual(row['apn'], 'example.data')

    def test_multiple_nested_flows_do_not_cross_tx_rx(self):
        rows = parse_message(QOS + QOS.replace('qci=9', 'qci=6').replace('flowId=7', 'flowId=8'), WHEN)
        self.assertEqual([r['tx'] for r in rows], [9, 6])

    def test_two_connections_keep_their_own_apns(self):
        rows = parse_message(QOS + QOS.replace('example.data', 'ims').replace('pdp_ip0', 'pdp_ip1'), WHEN)
        self.assertEqual([r['apn'] for r in rows], ['example.data', 'ims'])

    def test_ambiguous_sim_is_not_guessed(self):
        rows = parse_message(QOS + ' slotID=CTSubscriptionSlotTwo', WHEN)
        self.assertEqual(rows[0]['slot'], 'Unknown')

    def test_nr_flag_without_same_slot_mode_does_not_become_5qi(self):
        model = RadioModel(); model.feed(QOS, WHEN)
        model.feed('slotID=CTSubscriptionSlotTwo reg.dataMode=NR (17)', WHEN)
        self.assertEqual(model.snapshot(EPOCH)['observations'][0]['qos_kind'], 'QCI / 5QI')

    def test_mode_classification_is_timed_and_same_slot(self):
        for mode, expected in [('NR', '5QI'), ('NRNSA', 'QCI'), ('LTE', 'QCI'), ('NoData', 'QCI / 5QI')]:
            model = RadioModel()
            model.feed('slotID=CTSubscriptionSlotOne reg.dataMode=' + mode + ' (17)', WHEN)
            model.feed(QOS, WHEN)
            self.assertEqual(model.snapshot(EPOCH)['observations'][0]['qos_kind'], expected)
        model = RadioModel()
        model.feed('slotID=CTSubscriptionSlotOne reg.dataMode=NR (17)', '2026-11-01T11:59:54+00:00')
        model.feed(QOS, WHEN)
        self.assertEqual(model.snapshot(EPOCH)['observations'][0]['qos_kind'], 'QCI / 5QI')

    def test_conflicting_same_time_modes_remain_unresolved(self):
        model = RadioModel()
        for mode in ['NR', 'LTE']:
            model.feed('slotID=CTSubscriptionSlotOne reg.dataMode=' + mode + ' (17)', WHEN)
        model.feed(QOS, WHEN)
        self.assertEqual(model.snapshot(EPOCH)['observations'][0]['qos_kind'], 'QCI / 5QI')

    def test_only_recent_explicit_serving_cells_are_connected(self):
        model = RadioModel(); model.reset('live'); model.state('streaming', 'test')
        model.feed(CELL, WHEN)
        self.assertTrue(model.snapshot(EPOCH)['observations'][0]['connected'])
        self.assertFalse(model.snapshot(EPOCH + 31)['observations'][0]['connected'])
        model.state('disconnected', 'test')
        self.assertFalse(model.snapshot(EPOCH)['observations'][0]['connected'])

    def test_neighbor_and_unknown_role_are_not_serving(self):
        for role in ['Neighbor', 'Unrecognized']:
            model = RadioModel(); model.reset('live'); model.state('streaming', 'test')
            model.feed(CELL.replace('TypeServing', 'Type' + role), WHEN)
            self.assertFalse(model.snapshot(EPOCH)['observations'][0]['connected'])

    def test_no_band_is_derived_from_supported_lists_or_arfcn(self):
        self.assertEqual(parse_message('supportedBands={LTE=(2,4,12); NR=(41,71)}', WHEN), [])
        self.assertEqual(parse_message(CELL.replace('kCTCellMonitorBandInfo = 41;', 'kCTCellMonitorNRARFCN = 520000;'), WHEN), [])

    def test_future_naive_and_missing_timestamps_are_not_fresh(self):
        for when in ['2026-11-02T12:00:00+00:00', '2026-11-01T12:00:00', None]:
            model = RadioModel(); model.reset('live'); model.state('streaming', 'test'); model.feed(CELL, when)
            self.assertFalse(model.snapshot(EPOCH)['observations'][0]['fresh'])

    def test_invalid_measurements_are_unknown(self):
        row = parse_message(CELL.replace('-95', '-3276.8'), WHEN)[0]
        self.assertIsNone(row['rsrp'])
        self.assertEqual(parse_message(QOS.replace('qci=9', 'qci=0').replace('qci=8', 'qci=999'), WHEN), [])

    def test_out_of_order_events_do_not_replace_newer_observations(self):
        model = RadioModel(); model.feed(QOS, WHEN)
        model.feed(QOS.replace('qci=9', 'qci=6'), '2026-11-01T11:59:00+00:00')
        self.assertEqual(model.snapshot(EPOCH)['observations'][0]['tx'], 9)

    def test_historical_and_demo_never_present_as_current(self):
        model = demo_model()
        self.assertEqual(model.snapshot()['source'], 'demo')
        self.assertFalse(any(r['fresh'] for r in model.snapshot()['observations']))

    def test_raw_identifiers_and_messages_are_not_retained(self):
        model = RadioModel(); model.feed(CELL + ' imsi=310240123456789 iccid=89012601234567890123', WHEN)
        output = json.dumps(model.snapshot())
        self.assertNotIn('310240123456789', output)
        self.assertNotIn('89012601234567890123', output)
        self.assertNotIn('kCTCellMonitorCellId', output)
        self.assertNotIn('cell_key', output)
        self.assertNotIn('12345', output)

    def test_retained_observations_are_bounded(self):
        model = RadioModel()
        for i in range(300): model.feed(QOS.replace('flowId=7', 'flowId=' + str(i)), WHEN)
        self.assertEqual(len(model.snapshot()['observations']), 256)
        self.assertEqual(model.snapshot()['dropped'], 44)


class ImportContracts(unittest.TestCase):
    def test_apple_json_and_jsonl_parse(self):
        records = [{'eventMessage':QOS, 'timestamp':WHEN}, {'message':CELL, 'timestamp':WHEN}]
        for raw in [json.dumps(records), '\n'.join(map(json.dumps, records))]:
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / 'capture.json'; p.write_text(raw)
                report = import_capture(p).snapshot(EPOCH)
                self.assertEqual(len(report['observations']), 2)
                self.assertEqual(report['source'], 'historical')
                self.assertFalse(any(r['fresh'] for r in report['observations']))

    def test_export_reimport_preserves_values_but_not_liveness(self):
        model = RadioModel(); model.reset('live'); model.state('streaming', 'test'); model.feed(QOS, WHEN)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'export.json'; p.write_text(json.dumps(model.snapshot(EPOCH)))
            r = import_capture(p).snapshot(EPOCH)
            self.assertEqual(r['observations'][0]['tx'], 9)
            self.assertFalse(r['observations'][0]['fresh'])

    def test_truncated_and_oversized_json_is_rejected(self):
        with self.assertRaises(ValueError): list(json_records(io.StringIO('[{"a":1}')))
        with patch('carrier_radio_import.MAX_RECORD', 10), self.assertRaises(ValueError):
            list(json_records(io.StringIO(json.dumps({'message':'x'*100}))))

    def test_tar_rejects_traversal_symlink_and_multiple_captures(self):
        for names, link in [(['../escape'], False), (['capture.logarchive/a'], True),
                            (['one.logarchive/a','two.logarchive/b'], False)]:
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / 'input.tar'
                with tarfile.open(p, 'w') as tar:
                    for name in names:
                        info = tarfile.TarInfo(name); info.size = 1
                        if link: info.type = tarfile.SYMTYPE; info.linkname = '/etc/passwd'
                        tar.addfile(info, io.BytesIO(b'x'))
                with self.assertRaises(ValueError): extract_logarchive(p, Path(d) / 'out')

    def test_tar_only_extracts_logarchive_and_handles_nested_capture(self):
        with tempfile.TemporaryDirectory() as d:
            inner = io.BytesIO()
            with tarfile.open(fileobj=inner, mode='w') as tar:
                for name in ['log/capture.logarchive/Persist/a.tracev3','log/qdss/raw.bin']:
                    info = tarfile.TarInfo(name); info.size = 1; tar.addfile(info, io.BytesIO(b'x'))
            p = Path(d) / 'log.tar.gz'
            with tarfile.open(p, 'w:gz') as tar:
                info = tarfile.TarInfo('log.tar'); info.size = len(inner.getvalue()); tar.addfile(info, io.BytesIO(inner.getvalue()))
            out = extract_logarchive(p, Path(d) / 'out')
            self.assertEqual((out/'Persist/a.tracev3').read_bytes(), b'x')
            self.assertFalse(list(out.rglob('raw.bin')))


class ServerContracts(unittest.TestCase):
    def setUp(self):
        self.collector = Collector()
        self.collector.model = demo_model()
        self.server = DashboardServer(('127.0.0.1',0), self.collector, token='a'*43)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(2)

    def request(self, path, headers=None, data=None):
        r = urllib.request.Request(self.base + path, headers=headers or {}, data=data)
        try:
            with urllib.request.urlopen(r, timeout=3) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def test_state_requires_access_key(self):
        self.assertEqual(self.request('/api/state')[0],401)
        code, raw, _ = self.request('/api/state', {'Authorization':'Bearer '+'a'*43})
        self.assertEqual(code,200); self.assertEqual(json.loads(raw)['source'],'demo')

    def test_foreign_origin_and_rebinding_host_are_rejected_even_with_token(self):
        for headers in [{'Origin':'https://elsewhere.example'}, {'Host':'elsewhere.example'}]:
            headers['Authorization']='Bearer '+'a'*43
            self.assertEqual(self.request('/api/state',headers)[0],403)

    def test_html_contains_no_access_key_and_is_not_cacheable(self):
        code, raw, headers = self.request('/')
        self.assertEqual(code,200); self.assertNotIn(b'a'*43,raw)
        self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])

    def test_import_replaces_session_as_historical(self):
        code, _, _ = self.request('/api/import', {'Authorization':'Bearer '+'a'*43}, json.dumps({'message':QOS,'timestamp':WHEN}).encode())
        self.assertEqual(code,200)
        self.assertEqual(self.collector.model.snapshot()['source'],'historical')


if __name__ == '__main__': unittest.main()
