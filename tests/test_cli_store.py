import json

from overseer import cli


def test_record_and_list(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OVERSEER_DIR", str(tmp_path))
    cli.main(["record", "c1", "--title", "Add X"])
    cli.main(["record", "c1", "--question", "why", "--text", "because"])
    cli.main(["list"])
    out = json.loads(capsys.readouterr().out)
    assert out[0]["id"] == "c1" and out[0]["answers"] == {"why": "because"}
