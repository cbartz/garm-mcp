from garm_mcp.redact import redact

def test_redacts_registration_token():
    out = redact({"name": "runner-1", "registration_token": "ABC123", "status": "pending_create"})
    assert out["registration_token"] == "[REDACTED]"
    assert out["name"] == "runner-1" and out["status"] == "pending_create"

def test_redacts_nested_and_in_lists():
    data = [{"status_messages": [{"message": "creating"}], "provider": {"secret": "s", "token": "t"}},
            {"oauth_token": "x"}]
    out = redact(data)
    assert out[0]["status_messages"][0]["message"] == "creating"
    assert out[0]["provider"] == {"secret": "[REDACTED]", "token": "[REDACTED]"}
    assert out[1]["oauth_token"] == "[REDACTED]"

def test_non_dict_payloads_pass_through():
    assert redact({"count": 5, "flag": True, "note": None}) == {"count": 5, "flag": True, "note": None}
    assert redact("plain") == "plain"
