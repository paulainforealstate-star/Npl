import math
import unittest

from npldesk.engine import (
    Route,
    Scenario,
    expected_value,
    irr,
    max_purchase_price,
    recommend,
)


def sc(p, sale, t, costs=0.0, charges=0.0, debtor=0.0, name="s"):
    return Scenario(name, p, sale, costs, charges, debtor, t)


class NetValueTests(unittest.TestCase):
    def test_net_value_formula(self):
        s = sc(1, sale=100_000, t=1, costs=5_000, charges=10_000, debtor=2_000)
        self.assertEqual(s.net_value(), 83_000)


class ExpectedValueTests(unittest.TestCase):
    def test_single_scenario_discounted(self):
        s = sc(1, sale=110_000, t=1)
        self.assertAlmostEqual(expected_value([s], rate=0.10), 100_000)

    def test_probability_weighting(self):
        scenarios = [sc(0.5, 120_000, 1), sc(0.5, 80_000, 1)]
        self.assertAlmostEqual(expected_value(scenarios, rate=0.0), 100_000)

    def test_invalid_rate(self):
        with self.assertRaises(ValueError):
            expected_value([sc(1, 1, 1)], rate=-1)


class MaxPriceTests(unittest.TestCase):
    def test_safety_margin(self):
        self.assertAlmostEqual(max_purchase_price(100_000, 0.2), 80_000)

    def test_margin_bounds(self):
        with self.assertRaises(ValueError):
            max_purchase_price(100_000, 1.0)


class IrrTests(unittest.TestCase):
    def test_irr_of_known_flows(self):
        # Invertir 100 hoy y recibir 110 al año: TIR 10 %.
        r = irr([(0, -100), (1, 110)])
        self.assertIsNotNone(r)
        self.assertAlmostEqual(r, 0.10, places=6)

    def test_no_sign_change_returns_none(self):
        self.assertIsNone(irr([(0, 100), (1, 100)]))


class RecommendTests(unittest.TestCase):
    def test_picks_highest_expected_value(self):
        negotiation = Route("negociación", (sc(1, 90_000, 0.5),))
        judicial = Route(
            "continuación judicial",
            (sc(0.6, 150_000, 3, costs=20_000), sc(0.4, 50_000, 3, costs=20_000)),
        )
        rec = recommend([negotiation, judicial], rate=0.05, safety_margin=0.1)
        self.assertEqual(rec.best.route, "negociación")
        self.assertEqual(
            [r.route for r in rec.ranking], ["negociación", "continuación judicial"]
        )
        self.assertTrue(math.isclose(
            rec.best.max_price, rec.best.expected_value * 0.9
        ))

    def test_rejects_bad_probabilities(self):
        bad = Route("mala", (sc(0.5, 1, 1), sc(0.4, 1, 1)))
        with self.assertRaises(ValueError):
            recommend([bad], rate=0.0, safety_margin=0.0)

    def test_requires_routes(self):
        with self.assertRaises(ValueError):
            recommend([], rate=0.0, safety_margin=0.0)


if __name__ == "__main__":
    unittest.main()
