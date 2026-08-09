import datetime

import pytest

from app.cloudflare import CloudflareError


class FakeClient:
    """Stands in for CloudflareClient: returns canned payloads keyed by dataset.

    `responses` maps the dataset name appearing in the query to the `viewer` payload.
    A value that is an Exception instance is raised instead, to exercise the
    "one dataset is blocked by the plan, keep collecting the rest" path.
    """

    def __init__(self, responses=None, tunnels=None, zones=None, accounts=None):
        self.responses = responses or {}
        self.tunnels = tunnels or {}
        self.zones = zones or [("zid", "example.com")]
        self.accounts = accounts or [{"id": "aid", "name": "acct"}]
        self.errors = 0
        self.queries = []

    def graphql(self, query, variables=None):
        self.queries.append((query, variables))
        for dataset, payload in self.responses.items():
            if dataset in query:
                if isinstance(payload, Exception):
                    self.errors += 1
                    raise payload
                return payload
        raise CloudflareError("no canned response for this query")

    def list_zones(self, only=None):
        return [z for z in self.zones if not only or z[1] in only]

    def list_accounts(self):
        return self.accounts

    def list_tunnels(self, account_id):
        result = self.tunnels.get(account_id, [])
        if isinstance(result, Exception):
            self.errors += 1
            raise result
        return result


def viewer(dataset, rows):
    return {"zones": [{dataset: rows}]}


@pytest.fixture
def now():
    return datetime.datetime(2026, 8, 8, 15, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture
def overview_rows():
    def row(minute, status, requests, extra=0):
        return {"dimensions": {"datetimeMinute": minute, "edgeResponseStatus": status},
                "sum": {"requests": requests, "bytes": 100 + extra, "cachedRequests": 1,
                        "cachedBytes": 10, "visits": 2, "pageViews": 3}}
    return [row("2026-08-08T14:57:00Z", 200, 5),
            row("2026-08-08T14:58:00Z", 200, 7),
            row("2026-08-08T14:58:00Z", 530, 11, extra=50)]


@pytest.fixture
def full_responses(overview_rows):
    """Canned payloads for every dataset collect_zone touches."""
    return {
        "httpRequestsOverviewAdaptiveGroups(limit:2000":
            viewer("httpRequestsOverviewAdaptiveGroups", overview_rows),
        "httpRequestsOverviewAdaptiveGroups(limit:500": viewer(
            "httpRequestsOverviewAdaptiveGroups",
            [{"dimensions": {"clientCountryName": "IT", "clientRequestHTTPProtocol": "HTTP/2"},
              "sum": {"requests": 10, "bytes": 1000}},
             {"dimensions": {"clientCountryName": "DE", "clientRequestHTTPProtocol": "HTTP/2"},
              "sum": {"requests": 4, "bytes": 400}}]),
        "httpRequestsAdaptiveGroups": viewer(
            "httpRequestsAdaptiveGroups",
            [{"count": 9, "dimensions": {"clientRequestHTTPHost": "a.example.com",
                                         "edgeResponseStatus": 530, "cacheStatus": "dynamic"},
              "sum": {"edgeResponseBytes": 900}},
             {"count": 3, "dimensions": {"clientRequestHTTPHost": "b.example.com",
                                         "edgeResponseStatus": 200, "cacheStatus": "hit"},
              "sum": {"edgeResponseBytes": 300}}]),
        "dnsAnalyticsAdaptiveGroups": viewer(
            "dnsAnalyticsAdaptiveGroups",
            [{"count": 100, "dimensions": {"queryName": "a.example.com", "queryType": "A",
                                           "responseCode": "NOERROR", "responseCached": 0,
                                           "coloName": "MXP"},
              "avg": {"processingTimeUs": 2000.0}},
             {"count": 300, "dimensions": {"queryName": "b.example.com", "queryType": "AAAA",
                                           "responseCode": "NXDOMAIN", "responseCached": 1,
                                           "coloName": "FRA"},
              "avg": {"processingTimeUs": 1000.0}}]),
        "firewallEventsAdaptive": viewer(
            "firewallEventsAdaptive",
            [{"action": "block", "source": "firewallManaged", "clientCountryName": "US",
              "clientRequestHTTPHost": "a.example.com"},
             {"action": "block", "source": "firewallManaged", "clientCountryName": "CN",
              "clientRequestHTTPHost": "a.example.com"},
             {"action": "challenge", "source": "waf", "clientCountryName": "US",
              "clientRequestHTTPHost": "b.example.com"}]),
        "httpRequests1dGroups": viewer(
            "httpRequests1dGroups",
            [{"dimensions": {"date": "2026-08-08"},
              "sum": {"requests": 1000, "bytes": 5000, "threats": 6},
              "uniq": {"uniques": 42}}]),
    }
