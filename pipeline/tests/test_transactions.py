from pipeline.ingest.transactions import classify, parse


def test_classify():
    assert classify("Placed C Seth Jarvis on the injured non-roster list") == ("long", "on the injured non-roster list")
    assert classify("Placed F Leon Draisaitl on long-term injured reserve") == ("long", "on long-term injured reserve")
    assert classify("Transferred C Mathew Barzal to long-term injured reserve.") == ("long", "on long-term injured reserve")
    assert classify("Placed F Joel Armia on injured reserve.") == ("ir", None)
    assert classify("Activated D Luke Hughes from long-term injured reserve.") == ("activated", None)
    assert classify("Reinstated D Charlie McAvoy from suspension.") == ("activated", None)
    assert classify("Assigned Fs Cruz Lucius and Anton Wahlberg to San Diego (AHL)") == ("assigned", "to San Diego (AHL)")
    assert classify("Loaned F Avery Hayes to WB/Scranton") == ("assigned", "to WB/Scranton")
    assert classify("Recalled F Jakob Pelletier from Syracuse.") == ("recalled", None)
    assert classify("Suspended D Logan Stanley for one game.") == ("suspended", None)
    assert classify("Sent D Nick Blankensburg to Colorado in exchange for a fifth-round pick in the 2027 NHL draft.") is None
    assert classify("Placed G Georgii Romanov and D Calle Rosen on waivers") is None
    assert classify("Signed F Connor Bedard to a five-year contract extension.") is None


def test_parse_finds_each_player_in_each_sentence():
    known = {"victor olofsson": 1, "max crotty": 2, "ville heinola": 3, "juuso valimaki": 4}
    rows = parse([["2026-10-11", "VGK", "Placed LW Victor Olofsson and D Max Crotty on injured reserve. Recalled D Ville Heinola from Henderson (AHL)."],
                  ["2026-09-27", "CAR", "Placed LD Juuso Välimäki on waivers."]], known)
    assert [(r["player_id"], r["status"]) for r in rows] == [(1, "ir"), (2, "ir"), (3, "recalled")]
