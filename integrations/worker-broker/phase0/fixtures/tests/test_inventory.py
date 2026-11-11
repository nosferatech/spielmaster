from acme.inventory import create_widget, adjust_stock, low_stock


def test_create_and_adjust():
    create_widget("w-1", "bolt", 10)
    assert adjust_stock("w-1", -3)
    assert low_stock(threshold=8) == {"w-1"}


def test_adjust_missing_widget():
    assert adjust_stock("nope", 1) is False
