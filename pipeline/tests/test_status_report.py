from pipeline.ingest import status_report as sr

BODY = ("*Welcome.*## **Philadelphia Flyers**\n\nForwards Travis Konecny (lower body) and Cam York (upper body) did not play against "
        "the Boston Bruins on Saturday and are each day to day. Defenseman Rasmus Ristolainen was placed on injured reserve "
        "retroactive to Oct. 3 with an upper-body injury. ... Forward Nikita Grebenkin had back surgery and will be out at least "
        "four months.## **Los Angeles Kings**\n\nAlex Turcotte is day to day, with Quinton Byfield promoted to the No. 1 center "
        "following the retirement of Anze Kopitar.## **St. Louis Blues**\n\nZach Dean will move into the lineup for St. Louis on "
        "Saturday. Jake Neighbours was activated from injured reserve and made his season debut.")
LINKS = [[1, "Travis Konecny"], [2, "Cam York"], [3, "Rasmus Ristolainen"], [4, "Nikita Grebenkin"], [5, "Alex Turcotte"],
         [6, "Quinton Byfield"], [7, "Anze Kopitar"], [8, "Zach Dean"], [9, "Jake Neighbours"]]


def test_rules_read_each_player_from_his_own_words():
    rows = {r["player_id"]: r for r in sr.parse({"body": BODY, "links": LINKS}, "2026-10-10")}
    assert rows[1]["status"] == rows[2]["status"] == "day"  # a shared sentence
    assert rows[1]["injury"] == "lower body" and rows[2]["injury"] == "upper body"
    assert rows[3]["status"] == "ir"
    assert rows[4]["status"] == "long" and rows[4]["timeline"] == "out at least four months"
    assert rows[5]["status"] == "day"
    assert 6 not in rows and 7 not in rows  # named in someone else's news, nothing said about them
    assert rows[8]["status"] == "playing" and rows[8]["note"].endswith("on Saturday.")  # "St. Louis" does not end it
    assert rows[9]["status"] == "playing"  # activated from IR is a return, not an injury
    assert rows[1]["section"] == "Philadelphia Flyers"
    assert rows[1]["url"].endswith("october-10-2026")


def test_no_report_no_rows():
    assert sr.parse({}, "2026-10-10") == []
