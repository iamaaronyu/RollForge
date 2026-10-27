import subprocess

import pytest

from scripts import e2b_gateway_firewall as firewall


def test_gateway_address_accepts_only_tailnet_host_ipv4():
    assert firewall.gateway_address("100.102.201.53") == "100.102.201.53/32"
    for address in (
        "192.168.50.102",
        "100.102.201.53/24",
        "2001:db8::1",
        "not-an-ip",
    ):
        with pytest.raises(ValueError):
            firewall.gateway_address(address)


def test_rules_are_limited_to_gateway_ip_and_single_port():
    rules = firewall.rules_for_gateway("100.102.201.53")

    assert len(rules) == 4
    assert all("100.102.201.53/32" in rule for _table, rule in rules)
    assert "11441" in rules[0][1]
    assert "11441" in rules[2][1]
    assert rules[1][1][-1] == "DROP"
    assert "10.11.0.1-10.11.0.33" in rules[3][1]
    assert "tailscale0" in rules[3][1]
    assert firewall.TAG in rules[0][1]


def test_raw_allow_is_inserted_before_the_drop_rule():
    rules = firewall.rules_for_gateway("100.102.201.53")

    assert firewall.insertion_position(*rules[0]) == "1"
    assert firewall.insertion_position(*rules[1]) == "2"
    assert firewall.insertion_position(*rules[2]) == "1"


def fake_iptables(monkeypatch, fail_insert=None):
    existing = set()

    def run(*arguments):
        command = list(arguments)
        table = command[command.index("-t") + 1]
        action_index = (
            command.index("-C")
            if "-C" in command
            else (command.index("-I") if "-I" in command else command.index("-D"))
        )
        action = command[action_index]
        chain = command[action_index + 1]
        start = action_index + (3 if action == "-I" else 2)
        rule = tuple(command[start:])
        key = (table, chain, rule)
        if action == "-C":
            return subprocess.CompletedProcess(command, 0 if key in existing else 1)
        if action == "-I":
            if fail_insert == (table, chain):
                return subprocess.CompletedProcess(command, 2)
            existing.add(key)
            return subprocess.CompletedProcess(command, 0)
        existing.discard(key)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(firewall, "iptables", run)
    return existing


def test_apply_is_idempotent_and_remove_clears_only_owned_rules(monkeypatch):
    existing = fake_iptables(monkeypatch)

    assert firewall.apply("100.102.201.53") == 4
    assert len(existing) == 4
    assert firewall.apply("100.102.201.53") == 0
    assert len(existing) == 4
    assert firewall.remove("100.102.201.53") == 4
    assert existing == set()
    assert firewall.remove("100.102.201.53") == 0


def test_partial_apply_failure_rolls_back_new_rules(monkeypatch):
    existing = fake_iptables(monkeypatch, fail_insert=("nat", "POSTROUTING"))

    with pytest.raises(RuntimeError, match="无法安装"):
        firewall.apply("100.102.201.53")
    assert existing == set()
