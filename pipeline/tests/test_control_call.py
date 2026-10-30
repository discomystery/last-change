from pipeline.export import previews

PLACES = {f"T{i:02d}": f"Town {i}" for i in range(32)}


def _shares(top=0.58, bot=0.42):
    s = {t: 0.46 + 0.08 * i / 31 for i, t in enumerate(PLACES)}
    s["T00"], s["T31"] = bot, top
    return s


def test_called_for_an_edge_apart():
    c = previews.control_claim(1, "T00", "T31", PLACES, _shares())
    assert c and c["kind"] == "control" and c["team"] == "T31" and c["opp"] == "T00"
    assert c["check"]["gap"] > 0 and c["check"]["league"] == 0.5


def test_not_called_for_neighbours():
    s = _shares()
    assert previews.control_claim(1, "T15", "T16", PLACES, s) is None


def test_never_worded_as_a_result():
    banned = ("win", "favor", "favour", "beat", "odds", "lock", "deserve")
    for gid in range(40):
        c = previews.control_claim(gid, "T00", "T31", PLACES, _shares())
        text = " ".join([c["head"], c["body"], c["call"], c["gist"]]).lower()
        assert not any(f" {b}" in f" {text}" for b in banned), text
