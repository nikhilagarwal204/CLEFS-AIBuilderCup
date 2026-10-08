"""Site health (04 spec 5.6, 5.12): CrUX thresholds and 404, PSI, JSON-LD
(including @graph), robots.txt, and KB mismatches becoming site_fact_fix."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from tests.modules.visibility.test_gbp import GoogleFake, env  # noqa: F401  (shared fixtures)

NOW = datetime(2026, 10, 4, 3, 0, tzinfo=ZoneInfo("Asia/Dubai"))  # Sunday
SITE = "https://hotel.example"

CRUX = {"record": {"key": {"origin": SITE}, "metrics": {
    "largest_contentful_paint": {"percentiles": {"p75": 3100}},
    "interaction_to_next_paint": {"percentiles": {"p75": 150}},
    "cumulative_layout_shift": {"percentiles": {"p75": "0.05"}}}}}
PSI = {"lighthouseResult": {"fetchTime": "2026-10-04T00:00:00Z", "categories": {"performance": {"score": 0.62}},
                            "audits": {"largest-contentful-paint": {"numericValue": 3456.7},
                                       "cumulative-layout-shift": {"numericValue": 0.04}}}}

HOTEL_LD = {"@context": "https://schema.org", "@type": "Hotel", "name": "Test Hotel Dubai",
            "address": {"@type": "PostalAddress", "addressLocality": "Dubai"},
            "makesOffer": [{"@type": "Offer", "price": "450"}], "checkoutTime": "12:00",
            "starRating": {"@type": "Rating", "ratingValue": "3"}, "petsAllowed": False,
            "amenityFeature": [{"@type": "LocationFeatureSpecification", "name": "Swimming pool", "value": True}]}
GRAPH_LD = {"@context": "https://schema.org", "@graph": [
    {"@type": "WebSite", "name": "Site"},
    {"@type": ["Hotel"], "name": "Test Hotel Dubai", "address": "1 Road", "offers": {"@type": "Offer"},
     "checkinTime": "T14:00:00", "containsPlace": [{"@type": "HotelRoom", "name": "Deluxe", "bed": "King"}]}]}


def page(*blocks: dict | str) -> str:
    scripts = "".join(f'<script type="application/ld+json">{b if isinstance(b, str) else json.dumps(b)}</script>'
                      for b in blocks)
    return f"<html><head>{scripts}</head><body>Hi</body></html>"


class WebFake(GoogleFake):
    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for method, fragment, answer in self.answers:
            if request.method == method and fragment in str(request.url):
                if callable(answer):
                    answer = answer(request)
                status, body = answer if isinstance(answer, tuple) else (200, answer)
                if isinstance(body, str):
                    return httpx.Response(status, text=body, request=request)
                return httpx.Response(status, json=body, request=request)
        return httpx.Response(404, text="nope", request=request)


@pytest.fixture
def web():
    from clefs.modules.visibility import http

    fake = WebFake()
    http.set_transport(httpx.MockTransport(fake.handle))
    yield fake
    http.set_transport(None)


@pytest.fixture
def hotel(cur, hotel_uae, clock, env, monkeypatch):  # noqa: F811
    from clefs.modules.concierge import api as concierge_api
    from clefs.modules.visibility import action_types, store  # noqa: F401  (registers the types)

    clock.freeze(NOW)
    env(GOOGLE_API_KEY="g-key")
    monkeypatch.setattr(concierge_api, "kb_facts", lambda cur, slug, **kw: [
        {"fact_key": "checkin_time", "value": "14:00"}, {"fact_key": "checkout_time", "value": "11:00"},
        {"fact_key": "pool", "value": "true"}, {"fact_key": "pets", "value": "false"}])
    store.update_profile(cur, hotel_uae.slug, {"website_url": SITE + "/", "room_page_urls": [SITE + "/rooms"]},
                         user_id=None)
    return hotel_uae


# ─── Pure parts ───


def test_crux_thresholds_and_404():
    from clefs.modules.visibility.site_health import parse_crux

    c = parse_crux(CRUX)
    assert c["status"] == "ok" and c["metrics"] == {"lcp_ms": "3100", "inp_ms": "150", "cls": "0.05"}
    assert [o["metric"] for o in c["outside_good"]] == ["lcp_ms"]
    assert c["outside_good"][0]["note"] == "outside Google's 'good' threshold"
    assert parse_crux(None) == {"status": "insufficient_data", "metrics": {}, "outside_good": []}
    edge = parse_crux({"record": {"metrics": {"largest_contentful_paint": {"percentiles": {"p75": 2500}}}}})
    assert edge["outside_good"] == []                        # 2.5 s exactly is still good


def test_jsonld_graph_rooms_and_issues():
    from clefs.modules.visibility.site_health import check_nodes, extract_nodes, site_facts

    nodes, errors = extract_nodes(page(GRAPH_LD, "{not json"))
    assert errors == 1
    types, fields, issues = check_nodes(nodes, SITE)
    assert "Hotel" in types and "HotelRoom" in types and "WebSite" in types
    assert fields["checkinTime"] == "T14:00:00" and site_facts(fields)["checkin_time"] == "14:00"
    missing = {(i["severity"], i["field"]) for i in issues}
    assert ("recommended", "occupancy") in missing and ("useful", "checkoutTime") in missing
    assert not any(s == "required" for s, _ in missing)

    none_nodes, _ = extract_nodes("<html><body>No data</body></html>")
    _, _, none_issues = check_nodes(none_nodes, SITE)
    assert none_issues[0]["severity"] == "required" and none_issues[0]["field"] == "@type"


def test_site_facts_vs_kb():
    from clefs.modules.visibility.site_health import compare_kb, site_facts

    claims = site_facts(HOTEL_LD)
    assert claims["checkout_time"] == "12:00" and claims["star_rating"] == "3" and claims["pool"] == "true"
    out = {m["fact_key"]: m["status"] for m in compare_kb(
        {"checkout_time": "11:00", "star_rating": "4", "pool": "true", "checkin_time": "14:00"}, claims, SITE)}
    assert out == {"checkout_time": "mismatch", "star_rating": "mismatch", "pool": "match", "checkin_time": "missing"}


# ─── The weekly run ───


def test_weekly_run_signals_and_proposes_site_fact_fix(cur, hotel, web):
    from clefs.core import actions, signals
    from clefs.modules.visibility import api, site_health

    web.on("GET", SITE + "/", page(HOTEL_LD))          # registered first = matched last (substring routes)
    web.on("POST", "chromeuxreport.googleapis.com", CRUX)
    web.on("GET", "pagespeedonline", PSI)
    web.on("GET", "/robots.txt", "User-agent: *\nDisallow: /private\n")
    web.on("GET", SITE + "/rooms", page(GRAPH_LD))
    out = site_health.run_weekly(cur, hotel)
    # checkout 12:00 vs 11:00, and 3 stars on the site vs the hotel's 4; pets and pool match.
    assert out["crux"] == "ok" and out["jsonld_found"] and out["mismatches"] == 2 and out["action_id"]

    crux_req = next(r for r in web.requests if "chromeuxreport" in str(r.url))
    body = json.loads(crux_req.content)
    assert body["formFactor"] == "PHONE" and body["origin"] == SITE
    assert set(body["metrics"]) == {"largest_contentful_paint", "interaction_to_next_paint", "cumulative_layout_shift"}
    psi_req = next(r for r in web.requests if "pagespeedonline" in str(r.url))
    assert "strategy=mobile" in str(psi_req.url) and "category=performance" in str(psi_req.url)
    assert all(r.headers["user-agent"].startswith("ClefsBot/1.0") for r in web.requests)

    kinds = {s["kind"] for s in signals.list_signals(cur, hotel.slug, modules=["visibility"])}
    assert {"vis.site_health_not_good", "vis.jsonld_mismatch"} <= kinds
    act = actions.get_action(cur, hotel.slug, out["action_id"])
    assert act["type"] == "visibility.site_fact_fix" and set(act["params"]["facts"]) == {"checkout_time", "star_rating"}
    errs = {e["fact_key"]: e for e in api.open_fact_errors(cur, hotel.slug)}
    assert errs["checkout_time"]["theirs"] == "12:00" and errs["checkout_time"]["ours"] == "11:00"


def test_crux_404_is_insufficient_data_and_robots_is_obeyed(cur, hotel, web):
    from clefs.modules.visibility import site_health

    web.on("GET", SITE + "/", page(HOTEL_LD))
    web.on("POST", "chromeuxreport.googleapis.com", (404, {"error": {"code": 404}}))
    web.on("GET", "pagespeedonline", PSI)
    web.on("GET", "/robots.txt", "User-agent: ClefsBot\nDisallow: /rooms\n")
    web.on("GET", SITE + "/rooms", page(GRAPH_LD))
    out = site_health.run_weekly(cur, hotel)
    assert out["crux"] == "insufficient_data"
    assert not any(str(r.url) == SITE + "/rooms" for r in web.requests)
    row = site_health.latest_check(cur, hotel.slug)
    statuses = {p["url"]: p["status"] for p in row["jsonld_fields"]["pages"]}
    assert statuses[SITE + "/rooms"] == "robots_disallowed"


def test_site_fact_fix_verifier_reads_the_next_check(cur, hotel, web, clock):
    from clefs.core import actions
    from clefs.modules.visibility import site_health

    t = actions.get_action_type("visibility.site_fact_fix")
    act = {"id": None, "params": {"facts": ["checkout_time"]}, "executed_at": NOW}
    ctx = actions.ActionCtx(cur=cur, hotel=hotel, action=act, action_type=t, actor=None)
    assert t.verifier(ctx).status == "pending"                # no check since
    clock.advance(days=7)
    web.on("GET", SITE + "/", page({**HOTEL_LD, "checkoutTime": "11:00", "petsAllowed": False}))
    web.on("GET", "/robots.txt", (404, "none"))
    web.on("POST", "chromeuxreport.googleapis.com", CRUX)
    web.on("GET", "pagespeedonline", PSI)
    site_health.run_weekly(cur, hotel)
    assert t.verifier(ctx).status == "verified"


def test_no_website_and_no_key_are_setup_states(cur, hotel_uae, env):  # noqa: F811
    from clefs.modules.visibility import site_health

    env(GOOGLE_API_KEY=None)
    assert site_health.run_weekly(cur, hotel_uae) == {"skipped": "no_website"}
    assert site_health.overview(cur, hotel_uae)["setup_missing"] == ["website_url", "google_api_key"]


def test_private_addresses_are_never_fetched():
    """The website URL is owner-typed and fetched from the worker (SSRF)."""
    from clefs.modules.visibility import http

    http.set_transport(None)
    for url in ("http://127.0.0.1/", "http://169.254.169.254/latest/meta-data", "http://10.1.2.3/", "file:///etc"):
        with pytest.raises(http.VendorError) as exc:
            http.fetch_text(url)
        assert exc.value.detail == "website: address not allowed"


# ─── SSRF: resolve once, dial the vetted IP, web ports only ───

PUBLIC_IP = "93.184.216.34"


def _addrinfo(*ips: str):
    import socket

    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))
            for ip in ips]


@pytest.fixture
def pinned(monkeypatch):
    """The real pinned transport with DNS and the TCP dial faked: `answers`
    maps host -> list of answers (one popped per lookup, last one sticks) and
    `dialled` records every (ip, port) the backend actually connects to."""
    import socket

    from clefs.modules.visibility import http

    http.set_transport(None)
    state = {"answers": {}, "lookups": [], "dialled": [], "reply": None, "conns": []}

    def fake_getaddrinfo(host, port, *a, **kw):
        state["lookups"].append(host)
        seq = state["answers"].get(host)
        if not seq:
            raise socket.gaierror("no such host")
        return _addrinfo(*(seq.pop(0) if len(seq) > 1 else seq[0]))

    srv = socket.create_server(("127.0.0.1", 0))

    def fake_connect(address, *a, **kw):
        state["dialled"].append(address)
        if state["reply"] is None:
            raise ConnectionRefusedError("test: no dial")
        # A raw connect to a numeric tuple: socket.create_connection would go
        # back through the patched getaddrinfo.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect(srv.getsockname())
        conn, _ = srv.accept()
        conn.sendall(state["reply"])
        state["conns"].append(conn)
        return sock

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", fake_connect)
    yield state
    for conn in state["conns"]:
        conn.close()
    srv.close()


def test_rebinding_name_is_resolved_once_and_dialled_by_vetted_ip(pinned):
    """A rebinding name answers public to the first lookup and loopback to the
    next: the check and the connect must share ONE lookup, and the socket must
    be opened to the vetted IP (never the hostname, which would re-resolve)."""
    from clefs.modules.visibility import http

    pinned["answers"]["rebind.example"] = [[PUBLIC_IP], ["127.0.0.1"]]
    with pytest.raises(http.VendorError) as exc:
        http.fetch_text("http://rebind.example/")
    assert exc.value.detail == "website: ConnectError"  # the faked dial refused
    assert pinned["lookups"] == ["rebind.example"]
    assert pinned["dialled"] == [(PUBLIC_IP, 80)]


def test_name_with_any_private_answer_is_refused_before_dialling(pinned):
    from clefs.modules.visibility import http

    for ips in ([PUBLIC_IP, "10.0.0.5"], ["::ffff:127.0.0.1"], ["64:ff9b::a9fe:a9fe"], ["100.64.0.1"]):
        pinned["answers"]["mixed.example"] = [ips]
        with pytest.raises(http.VendorError) as exc:
            http.fetch_text("https://mixed.example/")
        assert exc.value.detail == "website: address not allowed", ips
    assert pinned["dialled"] == []


def test_only_web_ports_are_fetched(pinned):
    from clefs.modules.visibility import http

    pinned["answers"]["hotel.example"] = [[PUBLIC_IP]]
    for url in ("http://hotel.example:8080/", "https://hotel.example:22/", "http://hotel.example:6379/"):
        with pytest.raises(http.VendorError) as exc:
            http.fetch_text(url)
        assert exc.value.detail == "website: address not allowed", url
    assert pinned["lookups"] == [] and pinned["dialled"] == []


def test_redirect_hop_to_metadata_is_revetted(pinned):
    """A public page that 302s to a name resolving to the cloud metadata
    address: the second hop is resolved and vetted afresh, never dialled."""
    from clefs.modules.visibility import http

    pinned["answers"]["hotel.example"] = [[PUBLIC_IP]]
    pinned["answers"]["meta.example"] = [["169.254.169.254"]]
    pinned["reply"] = (b"HTTP/1.1 302 Found\r\nLocation: http://meta.example/latest/meta-data\r\n"
                       b"Content-Length: 0\r\n\r\n")
    with pytest.raises(http.VendorError) as exc:
        http.fetch_text("http://hotel.example/")
    assert exc.value.detail == "website: address not allowed"
    assert pinned["lookups"] == ["hotel.example", "meta.example"]
    assert pinned["dialled"] == [(PUBLIC_IP, 80)]


def test_public_page_is_fetched_through_the_pinned_transport(pinned):
    from clefs.modules.visibility import http

    pinned["answers"]["hotel.example"] = [[PUBLIC_IP]]
    pinned["reply"] = (b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                       b"Content-Length: 12\r\nConnection: close\r\n\r\n<p>hello</p>")
    status, text = http.fetch_text("http://hotel.example/")
    assert (status, text) == (200, "<p>hello</p>")
    assert pinned["dialled"] == [(PUBLIC_IP, 80)]
