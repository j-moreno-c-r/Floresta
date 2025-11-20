"""
A simple test that restart a Floresta node and a related data directory.

The directories used between each power-on/power-off must not be corrupted.
"""

import pytest
import filecmp
import os


@pytest.mark.integration
def test_restart_data_dir_integrity(node_creator, tmp_path):
    data_dir_1 = tmp_path / "floresta_data_1"
    data_dir_2 = tmp_path / "floresta_data_2"
    os.makedirs(data_dir_1, exist_ok=True)
    os.makedirs(data_dir_2, exist_ok=True)

    node1 = node_creator(
        variant="florestad",
        extra_args=[f"--data-dir={data_dir_1}"],
        testname="pytest_restart_1",
    )
    node_creator.start(node1)
    node1.rpc.wait_for_connections(opened=True)
    node1.stop()

    node2 = node_creator(
        variant="florestad",
        extra_args=[f"--data-dir={data_dir_2}"],
        testname="pytest_restart_2",
    )
    node_creator.start(node2)
    node2.rpc.wait_for_connections(opened=True)
    node2.stop()

    result = filecmp.dircmp(str(data_dir_1), str(data_dir_2))
    assert len(result.diff_files) == 0, f"Data directories differ: {result.diff_files}"
