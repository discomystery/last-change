from pipeline.export.ledger import short


def test_trophy_short_names():
    assert short("Hart Memorial Trophy") == "Hart"
    assert short("Maurice “Rocket” Richard Trophy") == "Richard"
    assert short("William M. Jennings Trophy") == "Jennings"
    assert short("Conn Smythe Trophy") == "Conn Smythe"
    assert short("Art Ross Trophy") == "Art Ross"
    assert short("Calder Memorial Trophy") == "Calder"
    assert short("Stanley Cup") == "Stanley Cup"
