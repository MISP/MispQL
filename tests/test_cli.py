import subprocess
import sys

import pytest

from mispql import __version__
from mispql.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(['--version'])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f'mispql {__version__}'


def test_module_entry_point():
    result = subprocess.run(
        [sys.executable, '-m', 'mispql', '--version'], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert result.stdout.strip() == f'mispql {__version__}'
