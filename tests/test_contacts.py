from printcrastinator.sources.contacts import _bday_of, _unfold, parse_bday, set_bday_line

CARD = "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Anna Example\r\nN:Example;Anna;;;\r\nEND:VCARD\r\n"


def test_set_and_replace_birthday():
    c1 = set_bday_line(CARD, "1990-05-21")
    assert "BDAY:1990-05-21" in c1 and c1.endswith("END:VCARD\r\n")
    assert _bday_of(_unfold(c1)) == "1990-05-21"
    c2 = set_bday_line(c1, "--05-22")
    assert c2.count("BDAY") == 1 and "X-APPLE-OMIT-YEAR=1604:1604-05-22" in c2
    assert _bday_of(_unfold(c2)) == "--05-22"
    c3 = set_bday_line(c2, None)
    assert "BDAY" not in c3 and "FN:Anna Example" in c3


def test_parse_bday_forms():
    assert parse_bday("19900521") == "1990-05-21"
    assert parse_bday("1990-05-21T00:00:00") == "1990-05-21"
    assert parse_bday("--0521") == "--05-21"
    assert parse_bday("1604-05-21", "BDAY;X-APPLE-OMIT-YEAR=1604") == "--05-21"
