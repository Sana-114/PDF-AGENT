import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", [
    "app.services.claim_semantics", "app.services.grounded_text",
    "app.services.answer_completeness",
])
def test_shared_services_import_in_fresh_process_without_eager_provider_cycle(module):
    result = subprocess.run([sys.executable, "-c", f"import {module}; print('import-ok')"],
                            capture_output=True, text=True, timeout=15, check=True)
    assert result.stdout.strip() == "import-ok"
