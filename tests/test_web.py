from __future__ import annotations

import io
import json
import unittest

from web import QuantWebHandler


class WebRequestValidationTest(unittest.TestCase):
    def test_invalid_utf8_is_reported_as_invalid_json(self) -> None:
        handler = object.__new__(QuantWebHandler)
        handler.headers = {"Content-Length": "1"}
        handler.rfile = io.BytesIO(b"\xff")

        with self.assertRaises(json.JSONDecodeError):
            handler._read_json()

    def test_non_standard_json_constants_are_rejected(self) -> None:
        raw_body = b'{"initial_cash": NaN}'
        handler = object.__new__(QuantWebHandler)
        handler.headers = {"Content-Length": str(len(raw_body))}
        handler.rfile = io.BytesIO(raw_body)

        with self.assertRaises(json.JSONDecodeError):
            handler._read_json()


if __name__ == "__main__":
    unittest.main()
