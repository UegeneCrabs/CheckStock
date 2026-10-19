import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx


SITE = Path(__file__).resolve().parents[1] / 'rbe-yandex-site'
sys.path.insert(0, str(SITE))
from campaign_access import campaign_access_issue, connection_access_status
import rbe2_service


def campaign(campaign_id, status='AVAILABLE', business=216948789, name='FBS Екатеринбург'):
    return {'id': campaign_id, 'domain': name, 'business': {'id': business},
            'placementType': 'FBS', 'apiAvailability': status}


def test_partial_business_reports_exact_campaign_and_reason():
    rows = [campaign(149250832, 'MANUALLY_DISABLED'), campaign(149250833)]
    result = connection_access_status(rows, 216948789)
    assert result['status'] == 'partial'
    assert '1 из 2' in result['detail']
    assert '149250832' in result['detail']
    assert 'MANUALLY_DISABLED' in result['detail']
    assert 'выключено вручную' in result['detail']
    assert '149250833' not in result['detail']


def test_all_disabled_is_not_connected_and_unknown_is_not_green():
    assert connection_access_status([campaign(1, 'MANUALLY_DISABLED')], 216948789)['status'] == 'unavailable'
    unknown = campaign(2)
    del unknown['apiAvailability']
    assert connection_access_status([unknown], 216948789)['status'] == 'unavailable'
    assert 'UNKNOWN' in campaign_access_issue(unknown)


def test_business_with_all_available_campaigns_is_connected():
    rows = [campaign(1), campaign(2), campaign(3, business=2)]
    assert connection_access_status(rows, 216948789) == {
        'status': 'connected', 'detail': 'API доступно для всех магазинов (2)'}
    assert connection_access_status(rows, 999)['status'] == 'error'


def test_checker_reads_every_campaign_page(monkeypatch):
    spec = importlib.util.spec_from_file_location('rbe_access_test', SITE / 'main.py')
    site = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(site)
    calls = []

    def get(url, *, params, headers, timeout):
        calls.append(params.copy())
        rows = [campaign(149250833)] if not params.get('pageToken') else [campaign(149250832, 'MANUALLY_DISABLED')]
        token = 'page-two' if len(calls) == 1 else ''
        return httpx.Response(200, request=httpx.Request('GET', url),
                              json={'campaigns': rows, 'paging': {'nextPageToken': token}})

    monkeypatch.setattr(site.httpx, 'get', get)
    result = site.check_connection({'api_key': 'test-key', 'business_id': 216948789})
    assert result['status'] == 'partial'
    assert calls == [{'limit': 100}, {'limit': 100, 'pageToken': 'page-two'}]


def test_new_connection_does_not_start_green_with_a_disabled_shop(monkeypatch):
    spec = importlib.util.spec_from_file_location('rbe_access_save_test', SITE / 'main.py')
    site = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(site)
    saved = []
    monkeypatch.setattr(site, '_require_admin', lambda request: {'role': 'admin'})
    monkeypatch.setattr(site, 'connection_campaigns', lambda key: [
        campaign(149250832, 'MANUALLY_DISABLED'), campaign(149250833)])
    monkeypatch.setattr(site, 'load_connections', lambda: [])
    monkeypatch.setattr(site, 'save_connections', lambda rows: saved.extend(rows))
    site.ConnectionRequest.model_rebuild(_types_namespace=vars(site))
    result = site.add_connection(None, site.ConnectionRequest(
        store_slug='gogol', business_id=216948789, api_key='test-key'))
    assert result['ok']
    assert saved[0]['status'] == 'partial'
    assert '149250832' in saved[0]['detail']


def test_rbe2_explains_disabled_shop_without_counting_it_as_available():
    api = SimpleNamespace(
        get_campaigns=lambda key: [campaign(149250832, 'MANUALLY_DISABLED'), campaign(149250833)],
        request=lambda path, key, **kwargs: {'orders': []} if path.endswith('/stats/orders') else {'returns': []},
    )
    packet = rbe2_service.fetch_packet(
        {'business_id': 216948789, 'api_key': 'test-key'}, date(2026, 10, 12), date(2026, 10, 12),
        api=api, report_loader=lambda *args: {'orders_and_offers_transactions': [], 'services_and_orders_margin': []},
        expense_loader=lambda *args: {},
    )
    assert not packet['complete']
    assert [row['id'] for row in packet['campaigns']] == [149250833]
    assert 'MANUALLY_DISABLED' in packet['notes'][0]
