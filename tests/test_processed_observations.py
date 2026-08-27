from helis.domain import Observation
from helis.store import HelisStore


def test_store_returns_only_unprocessed_observations(tmp_path) -> None:
    store = HelisStore(tmp_path / "helis.db")
    store.initialize()
    first = Observation(text="First market signal", source="fixture:1")
    second = Observation(text="Second market signal", source="fixture:2")
    store.save_observation(first)
    store.save_observation(second)

    store.mark_observations_processed([first.id])
    pending = store.list_unprocessed_observations()

    assert [item.id for item in pending] == [second.id]
