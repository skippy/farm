"""Tests for grazing consumption model."""

from datetime import date, datetime, timedelta

import pytest

from agriwebb.data.grazing import (
    BASE_INTAKE_PCT,
    DEFAULT_WEANING_DAYS,
    # Constants
    DEFAULT_WEIGHTS,
    LACTATION_MULTIPLIERS,
    calculate_animal_intake,
    calculate_paddock_consumption,
    find_nursing_lambs,
    # Functions
    get_latest_weight,
    get_wean_date,
)


class TestDefaultWeights:
    """Tests for default weight constants."""

    def test_all_age_classes_have_weights(self):
        """All common age classes should have default weights."""
        expected_classes = [
            "ewe", "ram", "wether", "lamb",
            "ewe_lamb", "ram_lamb", "wether_lamb",
            "ewe_weaner", "ram_weaner", "wether_weaner",
            "ewe_hogget", "ram_hogget", "wether_hogget",
            "maiden_ewe",
        ]
        for age_class in expected_classes:
            assert age_class in DEFAULT_WEIGHTS
            assert DEFAULT_WEIGHTS[age_class] > 0

    def test_weights_are_reasonable(self):
        """Weights should be in reasonable ranges for sheep."""
        for age_class, weight in DEFAULT_WEIGHTS.items():
            if "lamb" in age_class:
                assert 20 <= weight <= 50, f"{age_class} weight {weight} out of range"
            elif "weaner" in age_class or "hogget" in age_class:
                assert 50 <= weight <= 120, f"{age_class} weight {weight} out of range"
            else:
                assert 100 <= weight <= 200, f"{age_class} weight {weight} out of range"


class TestBaseIntakePercentages:
    """Tests for base intake percentages."""

    def test_all_age_classes_have_intake(self):
        """All age classes with weights should have intake percentages."""
        for age_class in DEFAULT_WEIGHTS:
            assert age_class in BASE_INTAKE_PCT

    def test_intake_percentages_reasonable(self):
        """Intake percentages should be 2-5% of body weight."""
        for age_class, pct in BASE_INTAKE_PCT.items():
            assert 0.02 <= pct <= 0.05, f"{age_class} intake {pct} out of range"

    def test_young_animals_eat_more(self):
        """Young animals should have higher intake percentages."""
        assert BASE_INTAKE_PCT["lamb"] > BASE_INTAKE_PCT["ewe"]
        assert BASE_INTAKE_PCT["ewe_weaner"] > BASE_INTAKE_PCT["ewe"]


class TestLactationMultipliers:
    """Tests for lactation intake multipliers."""

    def test_no_lambs_no_multiplier(self):
        """Non-lactating ewes have multiplier of 1.0."""
        assert LACTATION_MULTIPLIERS[0] == 1.0

    def test_multipliers_increase_with_lambs(self):
        """More lambs = higher intake multiplier."""
        assert LACTATION_MULTIPLIERS[1] > LACTATION_MULTIPLIERS[0]
        assert LACTATION_MULTIPLIERS[2] > LACTATION_MULTIPLIERS[1]
        assert LACTATION_MULTIPLIERS[3] > LACTATION_MULTIPLIERS[2]

    def test_multipliers_reasonable(self):
        """Lactation multipliers should be reasonable."""
        # Single lamb: 50-100% increase
        assert 1.5 <= LACTATION_MULTIPLIERS[1] <= 2.0
        # Twins: 100-150% increase
        assert 2.0 <= LACTATION_MULTIPLIERS[2] <= 2.5
        # Triplets: 150-200% increase
        assert 2.5 <= LACTATION_MULTIPLIERS[3] <= 3.0


class TestGetLatestWeight:
    """Tests for getting animal weight from records."""

    def test_no_records_uses_default(self):
        """Animal without records uses default weight."""
        animal = {
            "animalId": "test-1",
            "characteristics": {"ageClass": "ewe"},
            "records": [],
        }
        weight, source = get_latest_weight(animal)
        assert weight == DEFAULT_WEIGHTS["ewe"]
        assert source == "default"

    def test_uses_weight_record(self):
        """Uses weight from most recent record."""
        animal = {
            "animalId": "test-1",
            "characteristics": {"ageClass": "ewe"},
            "records": [
                {
                    "recordType": "weigh",
                    "observationDate": 1700000000000,  # Earlier
                    "weight": {"value": 120, "unit": "lb"},
                },
                {
                    "recordType": "weigh",
                    "observationDate": 1705000000000,  # Later
                    "weight": {"value": 135, "unit": "lb"},
                },
            ],
        }
        weight, source = get_latest_weight(animal)
        assert weight == 135
        assert source == "record"

    def test_ignores_zero_weights(self):
        """Ignores weight records with zero value."""
        animal = {
            "animalId": "test-1",
            "characteristics": {"ageClass": "ewe"},
            "records": [
                {
                    "recordType": "weigh",
                    "observationDate": 1705000000000,
                    "weight": {"value": 0, "unit": "lb"},
                },
            ],
        }
        weight, source = get_latest_weight(animal)
        assert source == "default"

    def test_handles_missing_characteristics(self):
        """Handles animal without characteristics."""
        animal = {
            "animalId": "test-1",
            "records": [],
        }
        weight, source = get_latest_weight(animal)
        assert weight == DEFAULT_WEIGHTS["ewe"]  # Falls back to ewe


class TestGetWeanDate:
    """Tests for determining lamb wean date."""

    def test_no_birth_date_returns_none(self):
        """No birth date returns None."""
        animal = {
            "animalId": "lamb-1",
            "characteristics": {},
            "records": [],
        }
        assert get_wean_date(animal) is None

    def test_uses_wean_record(self):
        """Uses wean record if present."""
        wean_ts = 1700000000000  # Some timestamp
        animal = {
            "animalId": "lamb-1",
            "characteristics": {"birthDate": 1690000000000},
            "records": [
                {"recordType": "wean", "observationDate": wean_ts},
            ],
        }
        wean_date = get_wean_date(animal)
        assert wean_date is not None

    def test_calculates_from_birth_date(self):
        """Calculates wean date from birth if no wean record."""
        # Birth date as timestamp (ms) - use a specific datetime to avoid timezone issues
        birth_dt = datetime(2024, 1, 1, 12, 0, 0)  # Noon Jan 1
        birth_ts = int(birth_dt.timestamp() * 1000)
        animal = {
            "animalId": "lamb-1",
            "characteristics": {"birthDate": birth_ts},
            "records": [],
        }
        wean_date = get_wean_date(animal)
        # Should be ~4 months after birth
        expected = birth_dt.date() + timedelta(days=DEFAULT_WEANING_DAYS)
        assert wean_date == expected


class TestFindNursingLambs:
    """Tests for finding which lambs are nursing which dams."""

    def test_empty_list(self):
        """Empty animal list returns empty dict."""
        assert find_nursing_lambs([]) == {}

    def test_no_lambs(self):
        """No lambs returns empty dict."""
        animals = [
            {
                "animalId": "ewe-1",
                "characteristics": {"ageClass": "ewe"},
                "state": {"onFarm": True},
                "parentage": {},
                "records": [],
            }
        ]
        assert find_nursing_lambs(animals) == {}

    def test_finds_nursing_lamb(self):
        """Finds lamb still nursing its dam."""
        today = date.today()
        birth_dt = datetime.combine(today - timedelta(days=60), datetime.min.time())
        birth_ts = int(birth_dt.timestamp() * 1000)

        animals = [
            {
                "animalId": "ewe-1",
                "characteristics": {"ageClass": "ewe"},
                "state": {"onFarm": True},
                "parentage": {},
                "records": [],
            },
            {
                "animalId": "lamb-1",
                "characteristics": {"ageClass": "ewe_lamb", "birthDate": birth_ts},
                "state": {"onFarm": True},
                "parentage": {
                    "dams": [{"parentAnimalId": "ewe-1"}],
                },
                "records": [],
            },
        ]
        result = find_nursing_lambs(animals, reference_date=today)
        assert "ewe-1" in result
        assert len(result["ewe-1"]) == 1

    def test_excludes_weaned_lamb(self):
        """Excludes lambs that have been weaned."""
        today = date.today()
        # Lamb born 5 months ago (past default 4-month weaning)
        birth_dt = datetime.combine(today - timedelta(days=150), datetime.min.time())
        birth_ts = int(birth_dt.timestamp() * 1000)

        animals = [
            {
                "animalId": "ewe-1",
                "characteristics": {"ageClass": "ewe"},
                "state": {"onFarm": True},
                "parentage": {},
                "records": [],
            },
            {
                "animalId": "lamb-1",
                "characteristics": {"ageClass": "ewe_lamb", "birthDate": birth_ts},
                "state": {"onFarm": True},
                "parentage": {
                    "dams": [{"parentAnimalId": "ewe-1"}],
                },
                "records": [],
            },
        ]
        result = find_nursing_lambs(animals, reference_date=today)
        assert "ewe-1" not in result

    def test_excludes_off_farm_lambs(self):
        """Excludes lambs not on farm."""
        today = date.today()
        birth_dt = datetime.combine(today - timedelta(days=60), datetime.min.time())
        birth_ts = int(birth_dt.timestamp() * 1000)

        animals = [
            {
                "animalId": "lamb-1",
                "characteristics": {"ageClass": "ewe_lamb", "birthDate": birth_ts},
                "state": {"onFarm": False},  # Off farm
                "parentage": {
                    "dams": [{"parentAnimalId": "ewe-1"}],
                },
                "records": [],
            },
        ]
        result = find_nursing_lambs(animals, reference_date=today)
        assert len(result) == 0


class TestCalculateAnimalIntake:
    """Tests for individual animal intake calculation."""

    def test_basic_intake(self):
        """Calculates basic intake correctly."""
        animal = {
            "animalId": "ewe-1",
            "identity": {"name": "Dolly"},
            "characteristics": {"ageClass": "ewe"},
            "state": {"onFarm": True, "currentLocationId": "paddock-1"},
            "records": [
                {
                    "recordType": "weigh",
                    "observationDate": 1705000000000,
                    "weight": {"value": 140, "unit": "lb"},
                },
            ],
        }
        intake = calculate_animal_intake(animal, nursing_lambs=0)

        assert intake["animal_id"] == "ewe-1"
        assert intake["name"] == "Dolly"
        assert intake["weight_kg"] == 140
        assert intake["weight_source"] == "record"
        assert intake["is_lactating"] is False
        assert intake["lactation_multiplier"] == 1.0
        # Base intake = 140 * 0.025 = 3.5 kg/day
        assert intake["base_intake_kg"] == 3.5

    def test_lactation_increases_intake(self):
        """Lactating ewes have higher intake."""
        animal = {
            "animalId": "ewe-1",
            "identity": {"name": "Dolly"},
            "characteristics": {"ageClass": "ewe"},
            "state": {"onFarm": True},
            "records": [],
        }

        dry = calculate_animal_intake(animal, nursing_lambs=0)
        single = calculate_animal_intake(animal, nursing_lambs=1)
        twins = calculate_animal_intake(animal, nursing_lambs=2)

        assert single["total_intake_kg"] > dry["total_intake_kg"]
        assert twins["total_intake_kg"] > single["total_intake_kg"]

    def test_twins_correct_multiplier(self):
        """Twins give correct lactation multiplier."""
        animal = {
            "animalId": "ewe-1",
            "identity": {"name": "Dolly"},
            "characteristics": {"ageClass": "ewe"},
            "state": {"onFarm": True},
            "records": [],
        }
        intake = calculate_animal_intake(animal, nursing_lambs=2)
        assert intake["lactation_multiplier"] == LACTATION_MULTIPLIERS[2]

    def test_paddock_name_from_fields(self):
        """Gets paddock name from fields dict."""
        animal = {
            "animalId": "ewe-1",
            "identity": {"name": "Dolly"},
            "characteristics": {"ageClass": "ewe"},
            "state": {"onFarm": True, "currentLocationId": "paddock-1"},
            "records": [],
        }
        fields = {
            "paddock-1": {"id": "paddock-1", "name": "North Field", "area_ha": 5.0}
        }
        intake = calculate_animal_intake(animal, fields=fields)
        assert intake["paddock_name"] == "North Field"


class TestCalculatePaddockConsumption:
    """Tests for aggregated paddock consumption."""

    @pytest.fixture
    def sample_animals(self):
        """Sample animals in paddocks."""
        return [
            {
                "animalId": "ewe-1",
                "identity": {"name": "Dolly"},
                "characteristics": {"ageClass": "ewe"},
                "state": {"onFarm": True, "currentLocationId": "paddock-1"},
                "parentage": {},
                "records": [],
            },
            {
                "animalId": "ewe-2",
                "identity": {"name": "Molly"},
                "characteristics": {"ageClass": "ewe"},
                "state": {"onFarm": True, "currentLocationId": "paddock-1"},
                "parentage": {},
                "records": [],
            },
            {
                "animalId": "ewe-3",
                "identity": {"name": "Polly"},
                "characteristics": {"ageClass": "ewe"},
                "state": {"onFarm": True, "currentLocationId": "paddock-2"},
                "parentage": {},
                "records": [],
            },
        ]

    @pytest.fixture
    def sample_fields(self):
        """Sample paddock data."""
        return {
            "paddock-1": {"id": "paddock-1", "name": "North Field", "area_ha": 5.0},
            "paddock-2": {"id": "paddock-2", "name": "South Field", "area_ha": 3.0},
        }

    def test_groups_by_paddock(self, sample_animals, sample_fields):
        """Groups animals by paddock correctly."""
        result = calculate_paddock_consumption(sample_animals, sample_fields)

        assert "paddock-1" in result
        assert "paddock-2" in result
        assert result["paddock-1"]["animal_count"] == 2
        assert result["paddock-2"]["animal_count"] == 1

    def test_calculates_per_hectare(self, sample_animals, sample_fields):
        """Calculates per-hectare consumption."""
        result = calculate_paddock_consumption(sample_animals, sample_fields)

        p1 = result["paddock-1"]
        expected_per_ha = p1["total_intake_kg_day"] / 5.0
        assert abs(p1["intake_per_ha_kg_day"] - expected_per_ha) < 0.1

    def test_excludes_small_paddocks(self, sample_animals, sample_fields):
        """Excludes paddocks below minimum area."""
        sample_fields["paddock-3"] = {"id": "paddock-3", "name": "Tiny", "area_ha": 0.1}
        sample_animals.append({
            "animalId": "ewe-4",
            "identity": {"name": "Tiny Ewe"},
            "characteristics": {"ageClass": "ewe"},
            "state": {"onFarm": True, "currentLocationId": "paddock-3"},
            "parentage": {},
            "records": [],
        })

        result = calculate_paddock_consumption(sample_animals, sample_fields, min_area_ha=0.2)
        assert "paddock-3" not in result

    def test_excludes_off_farm_animals(self, sample_animals, sample_fields):
        """Excludes animals not on farm."""
        sample_animals[0]["state"]["onFarm"] = False

        result = calculate_paddock_consumption(sample_animals, sample_fields)
        assert result["paddock-1"]["animal_count"] == 1  # Only one left

    def test_includes_animal_names(self, sample_animals, sample_fields):
        """Result includes list of animal names."""
        result = calculate_paddock_consumption(sample_animals, sample_fields)
        assert "Dolly" in result["paddock-1"]["animals"]
        assert "Molly" in result["paddock-1"]["animals"]


class TestIntakeCalculations:
    """Integration tests for realistic intake scenarios."""

    def test_lactating_ewe_with_twins(self):
        """Lactating ewe with twins has high intake."""
        animal = {
            "animalId": "ewe-1",
            "identity": {"name": "Super Mom"},
            "characteristics": {"ageClass": "ewe"},
            "state": {"onFarm": True},
            "records": [
                {
                    "recordType": "weigh",
                    "observationDate": 1705000000000,
                    "weight": {"value": 150, "unit": "lb"},
                },
            ],
        }
        intake = calculate_animal_intake(animal, nursing_lambs=2)

        # 150 kg * 2.5% base * 2.3 lactation = ~8.6 kg/day
        assert intake["total_intake_kg"] > 8
        assert intake["total_intake_kg"] < 10

    def test_lamb_intake_relative_to_weight(self):
        """Lamb intake is higher % of body weight."""
        lamb = {
            "animalId": "lamb-1",
            "identity": {"name": "Baby"},
            "characteristics": {"ageClass": "lamb"},
            "state": {"onFarm": True},
            "records": [],  # Uses default weight
        }
        intake = calculate_animal_intake(lamb)

        # Default lamb weight is 30 kg, intake is 4.5% = 1.35 kg
        assert 1.0 < intake["total_intake_kg"] < 2.0
