import hashlib
from io import BytesIO
import json
import os
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from weather.external import CUSTOMER_API, ExternalSourceError, SOURCE_CARDS, fetch_open_meteo


def example():
    return {
        "latitude": 56.5, "longitude": 33.0, "elevation": 260.0,
        "utc_offset_seconds": 0, "hourly_units": {"time": "iso8601", "temperature_2m": "°C"},
        "hourly": {"time": [f"2024-04-01T{hour:02}:00" for hour in range(24)],
                   "temperature_2m": [0.0, None] + [12.1] * 22},
    }


def pull(**kwargs):
    return fetch_open_meteo(56.5, 32.9, "2024-04-01", "2024-04-01", **kwargs)


class ExternalArchives(unittest.TestCase):
    def test_raw_grid_provenance_nulls_and_distinct_dataset_type(self):
        dataset = example()
        raw = json.dumps(dataset, ensure_ascii=False).encode()
        with patch("weather.external.urlopen", return_value=BytesIO(raw)) as loader:
            series = pull()
        url = loader.call_args.args[0].full_url
        self.assertEqual(urlsplit(url).netloc, "archive-api.open-meteo.com")
        params = parse_qs(urlsplit(url).query)
        self.assertEqual(params["models"], ["era5"])
        self.assertEqual(params["timezone"], ["UTC"])
        self.assertNotIn("apikey", params)
        meta, rows = series["metadata"], series["observations"]
        self.assertEqual(meta["data_type"], "reanalysis")
        self.assertEqual(meta["requested_coordinates"]["longitude"], 32.9)
        self.assertEqual(meta["grid_coordinates"]["longitude"], 33.0)
        self.assertEqual(meta["valid_hours"], 23)
        self.assertEqual(rows[0]["t_air"], 0.0)
        self.assertIsNone(rows[1]["t_air"])
        self.assertEqual(rows[23]["source_ref"], "/hourly/temperature_2m/23")
        self.assertEqual(series["source_response"].encode(), raw)
        self.assertEqual(meta["source_sha256"], hashlib.sha256(raw).hexdigest())
        dataset["generationtime_ms"] = 91
        with patch("weather.external.urlopen", return_value=BytesIO(json.dumps(dataset).encode())):
            same = pull()
        self.assertNotEqual(same["metadata"]["source_sha256"], meta["source_sha256"])
        self.assertEqual(same["metadata"]["series_sha256"], meta["series_sha256"])

    def test_customer_api_key_stays_out_of_export_and_errors(self):
        raw = json.dumps(example()).encode()
        with patch.dict(os.environ, {"OPEN_METEO_API_KEY": "test-secret"}):
            with patch("weather.external.urlopen", return_value=BytesIO(raw)) as loader:
                series = pull(commercial=True)
            address = loader.call_args.args[0].full_url
            self.assertTrue(address.startswith(CUSTOMER_API + "?"))
            self.assertEqual(parse_qs(urlsplit(address).query)["apikey"], ["test-secret"])
            self.assertNotIn("test-secret", json.dumps(series))
            with patch("weather.external.urlopen", side_effect=HTTPError(address, 403, "unavailable", {}, None)):
                with self.assertRaisesRegex(ExternalSourceError, "403") as ctx:
                    pull(commercial=True)
            self.assertNotIn("test-secret", str(ctx.exception))
        with patch.dict(os.environ, {"OPEN_METEO_API_KEY": ""}), patch("weather.external.urlopen") as loader:
            with self.assertRaises(ValueError):
                pull(commercial=True)
            loader.assert_not_called()

    def test_invalid_request_and_inconsistent_time_axis(self):
        with patch("weather.external.urlopen") as load:
            for lat, start, end in [(91, "2024-01-01", "2024-01-01"),
                                    (0, "2024-02-30", "2024-03-01"),
                                    (0, "2024-04-01", "2024-03-01")]:
                with self.subTest(lat=lat, start=start), self.assertRaises(ValueError):
                    fetch_open_meteo(lat, 0, start, end)
            load.assert_not_called()
        malformed = example()
        malformed["hourly"]["time"][8] = malformed["hourly"]["time"][7]
        with patch("weather.external.urlopen", return_value=BytesIO(json.dumps(malformed).encode())):
            with self.assertRaises(ExternalSourceError):
                pull()

    def test_russian_sources_link_to_provider_and_have_manual_status(self):
        self.assertEqual(len({s["id"] for s in SOURCE_CARDS}), len(SOURCE_CARDS))
        for source in SOURCE_CARDS[1:]:
            self.assertEqual(source["integration"], "manual")
            self.assertEqual(urlsplit(source["url"]).netloc, "eip.meteo.ru")
            self.assertTrue(source["instructions"])
