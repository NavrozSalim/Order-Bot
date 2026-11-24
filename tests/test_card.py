"""The purchase card is read from the environment or .env and never shown."""
import pytest

from core.card import CardError, load_card

GOOD = {"ORDERBOT_CARD_NUMBER": "4111 1111 1111 1111", "ORDERBOT_CARD_EXPIRY": "8/2029", "ORDERBOT_CARD_CVC": "123"}


def test_card_from_environment(tmp_path):
    card = load_card(GOOD, tmp_path / "none.env")
    assert (card.number, card.month, card.year, card.cvc, card.name) == ("4111111111111111", "08", "29", "123", "")
    assert repr(card) == str(card) == "Card(ending 1111)"


def test_card_from_env_file_and_environment_wins(tmp_path):
    f = tmp_path / ".env"
    f.write_text('# card\nORDERBOT_CARD_NUMBER="4111111111111111"\nORDERBOT_CARD_EXPIRY=09/28\n'
                 "ORDERBOT_CARD_CVC=999\nORDERBOT_CARD_NAME=Piyush Goplani\n", encoding="utf-8")
    card = load_card({}, f)
    assert (card.expiry, card.cvc, card.name) == ("09/28", "999", "Piyush Goplani")
    assert load_card({"ORDERBOT_CARD_CVC": "123"}, f).cvc == "123"


def test_missing_card_names_the_keys_not_values(tmp_path):
    with pytest.raises(CardError) as e:
        load_card({"ORDERBOT_CARD_NUMBER": "4111111111111111"}, tmp_path / "none.env")
    msg = str(e.value)
    assert "ORDERBOT_CARD_EXPIRY" in msg and "ORDERBOT_CARD_CVC" in msg and "4111" not in msg


@pytest.mark.parametrize("key,value,why", [
    ("ORDERBOT_CARD_NUMBER", "4111111111111112", "valid card number"),
    ("ORDERBOT_CARD_EXPIRY", "13/29", "MM/YY"),
    ("ORDERBOT_CARD_CVC", "12", "3 or 4 digits"),
])
def test_wrong_card_values_are_refused_without_showing_them(tmp_path, key, value, why):
    with pytest.raises(CardError, match=why) as e:
        load_card({**GOOD, key: value}, tmp_path / "none.env")
    assert value not in str(e.value)
