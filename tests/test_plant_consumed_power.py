"""Exercise the plant's direct and fallback consumption calculations."""
from __future__ import annotations

import ast
from decimal import InvalidOperation
from math import isfinite
from pathlib import Path
import unittest
from unittest.mock import Mock


COMPONENT = Path(__file__).resolve().parents[1] / "custom_components/sigen"


class TestPlantConsumedPower(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = ast.parse((COMPONENT / "calculated_sensor.py").read_text(encoding="utf-8"))
        calculations = next(node for node in source.body
                            if isinstance(node, ast.ClassDef) and node.name == "SigenergyCalculations")
        calculations.body = [node for node in calculations.body
                             if isinstance(node, ast.FunctionDef) and node.name == "calculate_plant_consumed_power"]
        all_ones_marker = next(node for node in source.body
                               if isinstance(node, ast.FunctionDef) and node.name == "_is_all_ones_power_marker")
        common = ast.parse((COMPONENT / "common.py").read_text(encoding="utf-8"))
        safe_float = next(node for node in common.body
                          if isinstance(node, ast.FunctionDef) and node.name == "safe_float")
        annotations = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
        module = ast.Module(body=[annotations, safe_float, all_ones_marker, calculations], type_ignores=[])
        scope = {"InvalidOperation": InvalidOperation, "_LOGGER": Mock(), "isfinite": isfinite}
        exec(compile(ast.fix_missing_locations(module), "plant_consumed_power", "exec"), scope)
        cls.calculations = scope["SigenergyCalculations"]

    def data(self, chargers):
        return {
            "plant": {
                "plant_active_power": 1,
                "plant_grid_sensor_active_power": 2,
                "plant_third_party_photovoltaic_power": 1,
            },
            "ac_chargers": chargers,
        }

    def consumed(self, data):
        return self.calculations.calculate_plant_consumed_power(None, data)

    def test_fallback_subtracts_configured_chargers(self):
        self.assertEqual(4, self.consumed(self.data({})))
        data = self.data({"First": {"ac_charger_charging_power": 2}})
        self.assertEqual(2, self.consumed(data))

    def test_fallback_skips_missing_or_invalid_charger_readings(self):
        data = self.data({"First": {"ac_charger_charging_power": 1}, "Second": {}})
        for reading in ({}, *({"ac_charger_charging_power": value} for value in
                              (None, True, "invalid", float("nan"), float("inf")))):
            with self.subTest(reading=reading):
                data["ac_chargers"]["Second"] = reading
                self.assertEqual(3, self.consumed(data))
        data["ac_chargers"]["Second"] = {"ac_charger_charging_power": 0}
        self.assertEqual(3, self.consumed(data))
        data["ac_chargers"]["Second"]["ac_charger_charging_power"] = 1
        self.assertEqual(2, self.consumed(data))

    def test_unreadable_charger_does_not_blank_consumption(self):
        # A configured charger that is offline (empty row) or whose reading is
        # missing must not make household consumption unknown.
        for chargers in ({"Garage": {}}, {"Garage": {"ac_charger_charging_power": None}},
                         {"Garage": {}, "Driveway": {}}):
            with self.subTest(chargers=chargers):
                self.assertEqual(4, self.consumed(self.data(chargers)))

    def test_direct_consumption_wins_over_fallback(self):
        data = self.data({"Charger": {}})
        data["plant"]["plant_general_load_power"] = 2
        self.assertEqual(2, self.consumed(data))

    def test_all_ones_direct_reading_falls_back_to_calculation(self):
        # A device that does not implement plant_general_load_power answers
        # with all-ones words: -0.001 for signed S32, 4294967.295 for unsigned
        # U32. Neither must be reported as household consumption.
        for marker in (-0.001, 4294967.295):
            with self.subTest(marker=marker):
                data = self.data({})
                data["plant"]["plant_general_load_power"] = marker
                self.assertEqual(4, self.consumed(data))
