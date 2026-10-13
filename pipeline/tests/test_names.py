from pipeline.ingest import names


def test_accented_version_is_used():
    last = {"default": "Teravainen", "cs": "Teräväinen", "fi": "Teräväinen", "sk": "Teräväinen", "sv": "Teräväinen"}
    assert names.pick(last, "FIN") == "Teräväinen"
    assert names.pick({"default": "Pastrnak", "cs": "Pastrňák", "sk": "Pastrňák"}, "CZE") == "Pastrňák"


def test_only_accents_are_added():
    # a nickname's full form, a different spelling or a feed typo is never taken
    assert names.pick({"default": "Will", "cs": "William"}, "USA") == "Will"
    assert names.pick({"default": "DiPietro", "cs": "Dipietro"}, "CAN") == "DiPietro"
    assert names.pick({"default": "Malott", "cs": "Malottt"}, "CAN") == "Malott"
    assert names.pick({"default": "Blueger", "cs": "Blugers"}, "LVA") == "Blueger"
    # never takes accents away
    assert names.pick({"default": "Välimäki", "cs": "Valimaki"}, "FIN") == "Välimäki"


def test_transliterations_of_cyrillic_are_skipped():
    assert names.pick({"default": "Namestnikov", "cs": "Naměstnikov"}, "RUS") == "Namestnikov"
    # and nothing changes while the country is unknown
    assert names.pick({"default": "Pastrnak", "cs": "Pastrňák"}, None) == "Pastrnak"


def test_own_language_first():
    assert names.pick({"default": "Sogaard", "fi": "Søgaard", "sv": "Søgaard"}, "DNK") == "Søgaard"
    assert names.pick({"default": "Ohgren", "sv": "Öhgren", "de": "Ohgrén"}, "SWE") == "Öhgren"


def test_fixes_must_match_the_plain_name():
    pid = next(iter(names.FIXES))
    first, last = names.FIXES[pid]
    plain = (names.strip(first), names.strip(last))
    assert names.spell(pid, {"default": plain[0]}, {"default": plain[1]}) == (first, last)
    assert names.spell(pid, {"default": "Someone"}, {"default": "Else"}) == ("Someone", "Else")
    for f, l in names.FIXES.values():  # every reviewed spelling adds accents to a plain name
        assert names._marks(f + l) > 0


def test_fold_matches_across_sources():
    assert names.fold("Mads Søgaard") == names.fold("Mads Sogaard") == "mads sogaard"
    assert names.fold("Juraj Slafkovský") == "juraj slafkovsky"
