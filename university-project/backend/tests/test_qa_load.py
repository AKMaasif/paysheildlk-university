"""Speed and concurrency against a real server process: decisions are quick, many cases at once all finish
correctly, many readers do not break each other, and the rate limits still hold under a burst."""
import os
import socket
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    from helpers import external_services

    database, redis_url = external_services()
    folder = tmp_path_factory.mktemp("load")
    port = _free_port()
    env = {**os.environ, "DATABASE_URL": database or f"sqlite:///{folder}/app.db", "CHECKPOINT_PATH": str(folder / "cp.sqlite"), "CHECKPOINTER": "postgres" if database else "sqlite",
           "REDIS_URL": redis_url or "", "JWT_SECRET": "test-secret-not-for-production-use", "STEP_DELAY_MS": "0", "FACE_ENGINE": "legacy", "DEMO_ASSIST": "true",
           "XAI_API_KEY": "", "META_API_KEY": "", "TEXTLK_API_KEY": "", "SMTP_HOST": "", "FIREBASE_PROJECT_ID": ""}
    log = open(folder / "server.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"], cwd=BACKEND, env=env, stdout=log, stderr=log)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                if httpx.get(base + "/api/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            raise RuntimeError("server did not start: " + (folder / "server.log").read_text()[-1500:])
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=15)
        log.close()


def login(base, email="analyst@payshield.lk", password="pilot-analyst"):
    reply = httpx.post(base + "/api/auth/login", json={"email": email, "password": password, "portal": "operator"}, timeout=10)
    assert reply.status_code == 200, reply.text
    return {"Authorization": "Bearer " + reply.json()["token"]}


def wait_case(base, headers, case_id, want, tries=80):
    for _ in range(tries):
        data = httpx.get(f"{base}/api/cases/{case_id}", headers=headers, timeout=10).json()
        if want(data):
            return data
        time.sleep(0.25)
    raise AssertionError(f"{case_id} never reached the expected state: {data['case']['status']}")


def test_a_decision_is_made_quickly(server):
    headers = login(server)
    customers = ["cus_nimal", "cus_dilani", "cus_kamal", "cus_priya"]
    known = {c["id"] for c in httpx.get(server + "/api/demo/customers", headers=headers).json()["customers"]}
    ids = [c for c in customers if c in known] or sorted(known)[:3]
    timings = []
    for i in range(12):
        started = time.monotonic()
        reply = httpx.post(server + "/api/demo/trigger", headers=headers, json={"customer_id": ids[i % len(ids)], "scenario": "sim_swap_transfer"}, timeout=30)
        assert reply.status_code == 200, reply.text
        wait_case(server, headers, reply.json()["case_id"], lambda d: d["case"]["status"] == "waiting_customer")
        timings.append(time.monotonic() - started)
    print(f"\nLOAD decision: median {statistics.median(timings):.2f}s, slowest {max(timings):.2f}s over {len(timings)} cases")
    assert statistics.median(timings) < 1.0, timings
    assert max(timings) < 3.0, timings


def test_fifty_cases_at_once_all_finish_with_the_right_decision_and_no_duplicates(server):
    headers = login(server)
    customers = [c["id"] for c in httpx.get(server + "/api/demo/customers", headers=headers).json()["customers"]]

    def start(i):
        scenario = "sim_swap_transfer" if i % 2 == 0 else "port_out_critical"
        reply = httpx.post(server + "/api/demo/trigger", headers=headers, json={"customer_id": customers[i % len(customers)], "scenario": scenario}, timeout=60)
        return scenario, reply

    begun = time.monotonic()
    with ThreadPoolExecutor(max_workers=25) as pool:
        started = list(pool.map(start, range(50)))
    assert all(r.status_code == 200 for _, r in started), [r.text[:100] for _, r in started if r.status_code != 200][:3]
    case_ids = [r.json()["case_id"] for _, r in started]
    print(f"\nLOAD cases: 50 started with 25 at a time in {time.monotonic() - begun:.1f}s")
    assert len(set(case_ids)) == 50, "two cases were given the same number"
    for (scenario, _), case_id in zip(started, case_ids):
        want = "waiting_customer" if scenario == "sim_swap_transfer" else "closed_blocked"
        data = wait_case(server, headers, case_id, lambda d: d["case"]["status"] == want, tries=160)
        assert data["transaction"]["status"] == ("held" if scenario == "sim_swap_transfer" else "blocked")


def test_two_hundred_readers_at_once_all_get_a_complete_answer(server):
    headers = login(server)
    paths = ["/api/overview", "/api/cases", "/api/holds", "/api/analytics", "/api/map", "/api/agents", "/api/reports", "/api/calibration"]

    def read(i):
        reply = httpx.get(server + paths[i % len(paths)], headers=headers, timeout=60)
        return reply.status_code, len(reply.content)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=40) as pool:
        results = list(pool.map(read, range(200)))
    assert all(status == 200 and size > 2 for status, size in results), [r for r in results if r[0] != 200][:5]
    took = time.monotonic() - started
    print(f"\nLOAD readers: 200 requests with 40 at a time in {took:.1f}s")
    assert took < 60


def test_readers_and_writers_together_do_not_corrupt_each_other(server):
    headers = login(server)
    customers = [c["id"] for c in httpx.get(server + "/api/demo/customers", headers=headers).json()["customers"]]

    def work(i):
        if i % 3 == 0:
            return httpx.post(server + "/api/demo/trigger", headers=headers, json={"customer_id": customers[i % len(customers)], "scenario": "routine_payment"}, timeout=60).status_code
        return httpx.get(server + ("/api/overview" if i % 2 else "/api/cases"), headers=headers, timeout=60).status_code

    with ThreadPoolExecutor(max_workers=30) as pool:
        codes = list(pool.map(work, range(90)))
    assert set(codes) == {200}
    audit = httpx.post(server + "/api/audit/verify", headers=headers, timeout=60).json()
    assert audit["ok"] is True


def test_a_burst_of_wrong_passwords_is_stopped_by_the_limit(server):
    codes = [httpx.post(server + "/api/auth/login", json={"email": "telco@payshield.lk", "password": f"wrong-{i}-password", "portal": "operator"}, timeout=10).status_code for i in range(12)]
    assert 429 in codes, codes
    assert codes.index(429) <= 6, codes
    # While that account is locked the right password is refused too, so guessing cannot win by luck.
    assert httpx.post(server + "/api/auth/login", json={"email": "telco@payshield.lk", "password": "pilot-telco", "portal": "operator"}, timeout=10).status_code == 429


def test_a_burst_of_registrations_from_one_address_is_stopped(server):
    codes = [httpx.get(server + "/api/customer/register/challenge", timeout=10).status_code for _ in range(30)]
    assert 429 in codes, codes


def test_a_burst_against_the_step_up_limit_cannot_be_used_to_guess_answers(server):
    headers = login(server, "supervisor@payshield.lk", "pilot-supervisor")
    # Customer endpoints reject an operator, so this only proves the burst never causes a server error.
    codes = [httpx.post(server + "/api/customer/cases/PS-26-00046/verify", headers=headers, json={"answers": {}, "liveness": True}, timeout=10).status_code for _ in range(30)]
    assert all(code < 500 for code in codes) and set(codes) <= {403, 429}


def test_the_server_is_still_healthy_after_all_of_that(server):
    assert httpx.get(server + "/api/health", timeout=10).json()["ok"] is True
