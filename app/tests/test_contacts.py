import pytest

from app.services import (
    StockError,
    create_reseller,
    create_supplier,
    deactivate_reseller,
    deactivate_supplier,
    get_reseller,
    get_supplier,
    list_resellers,
    list_suppliers,
    update_reseller,
    update_supplier,
)

# Les deux carnets d'adresses partagent la même logique — un seul jeu de
# tests paramétré par entité plutôt que de le dupliquer mot pour mot.
_ENTITIES = {
    "reseller": (create_reseller, get_reseller, update_reseller, deactivate_reseller, list_resellers),
    "supplier": (create_supplier, get_supplier, update_supplier, deactivate_supplier, list_suppliers),
}


@pytest.mark.parametrize("entity", ["reseller", "supplier"])
def test_create_requires_name(db_session, entity):
    create, *_ = _ENTITIES[entity]
    with pytest.raises(StockError):
        create(db_session, name="   ")


@pytest.mark.parametrize("entity", ["reseller", "supplier"])
def test_create_strips_and_persists_fields(db_session, entity):
    create, get, *_ = _ENTITIES[entity]
    contact = create(
        db_session, name="  Karim Benali  ", phone=" 0555 12 34 56 ", address=" Alger ", notes=" VIP "
    )
    db_session.flush()

    fetched = get(db_session, contact.id)
    assert fetched.name == "Karim Benali"
    assert fetched.phone == "0555 12 34 56"
    assert fetched.address == "Alger"
    assert fetched.notes == "VIP"
    assert fetched.is_active is True


@pytest.mark.parametrize("entity", ["reseller", "supplier"])
def test_get_raises_when_missing(db_session, entity):
    _, get, *_ = _ENTITIES[entity]
    with pytest.raises(StockError):
        get(db_session, 999)


@pytest.mark.parametrize("entity", ["reseller", "supplier"])
def test_update_modifies_fields(db_session, entity):
    create, get, update, _, _ = _ENTITIES[entity]
    contact = create(db_session, name="Karim")

    update(db_session, contact.id, name="Karim B.", phone="0666", address="Oran", notes="")

    fetched = get(db_session, contact.id)
    assert fetched.name == "Karim B."
    assert fetched.phone == "0666"
    assert fetched.address == "Oran"


@pytest.mark.parametrize("entity", ["reseller", "supplier"])
def test_update_requires_name(db_session, entity):
    create, _, update, _, _ = _ENTITIES[entity]
    contact = create(db_session, name="Karim")

    with pytest.raises(StockError):
        update(db_session, contact.id, name="", phone="", address="", notes="")


@pytest.mark.parametrize("entity", ["reseller", "supplier"])
def test_list_filters_by_search(db_session, entity):
    create, _, _, _, list_all = _ENTITIES[entity]
    create(db_session, name="Karim Benali", phone="0555")
    create(db_session, name="Yacine Amrani", phone="0666")

    results = list_all(db_session, search="karim")

    assert [c.name for c in results] == ["Karim Benali"]


@pytest.mark.parametrize("entity", ["reseller", "supplier"])
def test_deactivate_excludes_from_active_list_but_keeps_row(db_session, entity):
    create, get, _, deactivate, list_all = _ENTITIES[entity]
    contact = create(db_session, name="Karim")

    deactivate(db_session, contact.id)

    assert contact.id not in {c.id for c in list_all(db_session)}
    fetched = get(db_session, contact.id)
    assert fetched.is_active is False
