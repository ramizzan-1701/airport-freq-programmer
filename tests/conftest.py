from pathlib import Path

import pytest

from afp.adapters.faa import FAAAdapter

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def faa_adapter() -> FAAAdapter:
    return FAAAdapter(
        apt_base=FIXTURES / "apt_base_sample.csv",
        frq=FIXTURES / "frq_sample.csv",
        ils_base=FIXTURES / "ils_base_sample.csv",
    )
