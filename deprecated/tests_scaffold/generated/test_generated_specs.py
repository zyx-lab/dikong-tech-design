import json
import unittest
from pathlib import Path

class GeneratedCaseSpecTests(unittest.TestCase):
    def test_generated_cases_have_required_fields(self):
        path = Path('.tdd_ai/artifacts/cases/latest.json')
        self.assertTrue(path.exists(), 'cases artifact missing')
        payload = json.loads(path.read_text(encoding='utf-8'))
        required = {'case_id', 'event_id', 'flow_id', 'request', 'expected_status'}
        for case in payload.get('cases', []):
            self.assertTrue(required.issubset(case.keys()))

if __name__ == '__main__':
    unittest.main()
