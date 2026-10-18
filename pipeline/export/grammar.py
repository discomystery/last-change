"""Grammar shared by every generated sentence: lists, capitals, number words, ordinals, articles, plurals and
possessives. One place for these small jobs, so a fix reaches every page at once (natural-text plan, stage 2: the
same jobs had been written seven different ways across the export modules, one of them giving "21th").

Team naming (places, nicknames, the New York clubs) stays in `wording.py`.
"""

WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]
ORDINAL_WORDS = ["", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
                 "eleventh", "twelfth", "13th", "14th", "15th", "16th", "17th", "18th", "19th", "20th"]


def cap(t: str) -> str:
    """First letter up, the rest as written ("the Hurricanes" -> "The Hurricanes", "deBrusk" stays)."""
    return t[:1].upper() + t[1:] if t else t


def and_list(xs) -> str:
    """ "A", "A and B", "A, B and C" (no serial comma, house style); empty items are dropped."""
    xs = [x for x in xs if x]
    if not xs:
        return ""
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def num(n: int, zero: str = "no") -> str:
    """Counts a fan would spell out: "no", "one" ... "twelve", then digits ("13"). `zero` sets the word for 0."""
    if n == 0:
        return zero
    return WORDS[n] if 0 < n < len(WORDS) else f"{n:,}"


def ordinal(n: int) -> str:
    """ "1st", "22nd", "113th", "1,000th"."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n:,}{suffix}"


def ordinal_word(n: int, upto: int = 10) -> str:
    """ "eighth" for small numbers ("his eighth NHL game"), "23rd" beyond `upto` (at most 12 spelled out)."""
    return ORDINAL_WORDS[n] if 0 < n <= min(upto, 12) else ordinal(n)


_AN_WORDS = ("hour", "honest", "honor")


def article(text: str) -> str:
    """The text with the article it takes when spoken: "an 8-3 rout", "an 11-game streak", "a 1-0 win", "an 18%",
    "an extra", "a one-timer", "a Utah"."""
    t = text.lstrip()
    lead = t.split()[0] if t else ""
    if lead[:1].isdigit():
        digits = ""
        for ch in lead:
            if not ch.isdigit():
                break
            digits += ch
        # spoken "eight", "eighteen", "eighty...", "eleven", "eleven hundred": the numbers that start with a vowel sound
        n = int(digits)
        an = digits[0] == "8" or n in (11, 18) or (len(digits) in (4, 7) and digits[:2] in ("11", "18"))
        return ("an " if an else "a ") + t
    low = lead.lower()
    if low.startswith(_AN_WORDS):
        return "an " + t
    if low.startswith(("one", "once", "uni", "use", "usu", "eu", "ut")):  # "a one-goal game", "a Utah", "a usual"
        return "a " + t
    return ("an " if low[:1] in "aeiou" else "a ") + t


def plural(n: int, word: str, many: str | None = None) -> str:
    """The word for n of them: plural(1, "goal") = "goal", plural(3, "goal") = "goals", plural(2, "save", ...)."""
    return word if n == 1 else (many or word + "s")


def count(n: int, word: str, many: str | None = None, zero: str = "no", spell: bool = True) -> str:
    """ "one goal", "three goals", "no shots", "14 saves" (`spell=False` keeps digits: "3 goals")."""
    return f"{num(n, zero) if spell else n} {plural(n, word, many)}"


def possessive(name: str) -> str:
    """ "Carolina’s", "Matthews’", "the Rangers’": words ending in s take only the apostrophe (house style)."""
    return name + ("’" if name.endswith("s") else "’s")


def clock(seconds: int) -> str:
    """Ice time and game clocks as minutes:seconds, always ("9:18", never 9.3 minutes)."""
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def seconds_left(s: int) -> str:
    """Time left on a clock, the way it is said: "less than a second", "1 second", "48 seconds", "2:13"."""
    if s <= 0:
        return "less than a second"
    if s == 1:
        return "1 second"
    return f"{s} seconds" if s < 60 else clock(s)
