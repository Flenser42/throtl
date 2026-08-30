import unittest

from throtl import units


class ParseRateTest(unittest.TestCase):
    def test_kbps(self):
        self.assertEqual(units.parse_rate("512kbps"), 512)
        self.assertEqual(units.parse_rate("1kbps"), 1)

    def test_mbps(self):
        self.assertEqual(units.parse_rate("1.5mbps"), 1500)
        self.assertEqual(units.parse_rate("2mbps"), 2000)

    def test_gbps(self):
        self.assertEqual(units.parse_rate("2gbps"), 2_000_000)

    def test_plain_number(self):
        self.assertEqual(units.parse_rate("1000"), 1000)
        self.assertEqual(units.parse_rate(1000), 1000)
        self.assertEqual(units.parse_rate(512.6), 513)

    def test_case_and_spaces(self):
        self.assertEqual(units.parse_rate(" 1.5 Mbps "), 1500)

    def test_none(self):
        self.assertIsNone(units.parse_rate(None))

    def test_invalid(self):
        for bad in ("abc", "-5kbps", "-3", "12xyz"):
            with self.assertRaises(ValueError):
                units.parse_rate(bad)


class ConversionTest(unittest.TestCase):
    def test_kbit_to_kbs(self):
        self.assertEqual(units.kbit_to_kBs(8), 1.0)

    def test_roundtrip(self):
        for value in (1, 512, 1000, 12345):
            self.assertAlmostEqual(units.kBs_to_kbit(units.kbit_to_kBs(value)), value)


class FormatRateTest(unittest.TestCase):
    def test_units(self):
        self.assertEqual(units.format_rate(1500, "kbps"), "1500.0 kbit/s")
        self.assertEqual(units.format_rate(1500, "mbps"), "1.5 Mbit/s")
        self.assertEqual(units.format_rate(1500, "kBs"), "187.5 KB/s")
        self.assertEqual(units.format_rate(1500, "mBs"), "0.2 MB/s")

    def test_auto(self):
        self.assertEqual(units.format_rate(500, "auto"), "500.0 kbit/s")
        self.assertEqual(units.format_rate(5000, "auto"), "5.0 Mbit/s")

    def test_none(self):
        self.assertEqual(units.format_rate(None), "∞")

    def test_precision(self):
        self.assertEqual(units.format_rate(1536, "mbps", precision=2), "1.54 Mbit/s")


if __name__ == "__main__":
    unittest.main()
