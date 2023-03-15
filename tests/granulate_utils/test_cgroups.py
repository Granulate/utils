#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from pathlib import Path
from unittest.mock import patch

import pytest
from pytest import TempPathFactory

from granulate_utils.exceptions import CgroupInterfaceNotSupported
from granulate_utils.linux.cgroups.cgroup import (
    CGROUP_PROCS_FILE,
    CgroupCoreV1,
    CgroupCoreV2,
    ControllerType,
    get_cgroup_for_process,
)
from granulate_utils.linux.cgroups.cpu_controller import CpuControllerFactory
from granulate_utils.linux.cgroups.cpuacct_controller import CpuAcctController
from granulate_utils.linux.cgroups.memory_controller import MemoryControllerFactory

DUMMY_CONTROLLER: ControllerType = "cpu"
DUMMY2_CONTROLLER: ControllerType = "memory"


# Cgroup
def test_cgroup_v1(tmp_path_factory: TempPathFactory):
    tmp_dir = tmp_path_factory.mktemp("base_controller")
    cgroup_dir = Path(tmp_dir)
    SUB_CGROUP_NAME = "sub_cgroup"
    cpu_procs = cgroup_dir / CGROUP_PROCS_FILE

    cpu_procs.write_text("1 2 3")

    cgroup_v1 = CgroupCoreV1(cgroup_dir)

    assert cgroup_v1.get_pids_in_cgroup() == set([1, 2, 3])
    cgroup_v1.assign_process_to_cgroup(4)
    assert cgroup_v1.get_pids_in_cgroup() == set([4])  # write_text overwrites.

    sub_cgroup_dir = cgroup_dir / SUB_CGROUP_NAME
    assert not sub_cgroup_dir.exists()
    sub_cgroup = cgroup_v1.get_subcgroup(DUMMY2_CONTROLLER, SUB_CGROUP_NAME)
    assert sub_cgroup_dir.exists()
    assert sub_cgroup is not None

    sub_cgroup_procs = sub_cgroup_dir / CGROUP_PROCS_FILE
    sub_cgroup.assign_process_to_cgroup(5)
    assert sub_cgroup.get_pids_in_cgroup() == set([5])
    assert sub_cgroup_procs.read_text() == "5"

    same_cgroup = sub_cgroup.get_subcgroup(DUMMY_CONTROLLER, sub_cgroup.cgroup_full_path.name)
    assert same_cgroup.cgroup_full_path == sub_cgroup.cgroup_full_path


def test_cgroup_v2(tmp_path_factory: TempPathFactory):
    root_cgroup = tmp_path_factory.mktemp("root")
    parent_cgroup = root_cgroup / "base_controller"
    current_leaf = parent_cgroup / "leaf"
    current_leaf.mkdir(exist_ok=True, parents=True)

    (root_cgroup / "cgroup.controllers").touch()
    (root_cgroup / "cgroup.subtree_control").touch()
    parent_supported_controllers = parent_cgroup / "cgroup.controllers"
    parent_supported_controllers.write_text(DUMMY_CONTROLLER)
    parent_delegated_controllers = parent_cgroup / "cgroup.subtree_control"
    parent_delegated_controllers.touch()

    SUB_CGROUP_NAME = "sub_cgroup"
    sub_cgroup_dir = parent_cgroup / SUB_CGROUP_NAME

    cgroup = CgroupCoreV2(current_leaf, root_cgroup)
    sub_cgroup = cgroup.get_subcgroup(DUMMY_CONTROLLER, SUB_CGROUP_NAME)
    assert sub_cgroup_dir.absolute() == sub_cgroup.cgroup_full_path.absolute()
    assert parent_delegated_controllers.read_text().strip() == f"+{DUMMY_CONTROLLER}"

    with pytest.raises(AssertionError) as exception:
        sub_cgroup = cgroup.get_subcgroup(DUMMY2_CONTROLLER, SUB_CGROUP_NAME)
    assert exception.value.args[0] == f"Controller '{DUMMY2_CONTROLLER}' is not supported under {str(root_cgroup)}"


def test_cgroup_v2_fallback_to_root(tmp_path_factory: TempPathFactory):
    root_cgroup = tmp_path_factory.mktemp("root")
    parent_cgroup = root_cgroup / "base_controller"
    current_leaf = parent_cgroup / "leaf"
    current_leaf.mkdir(exist_ok=True, parents=True)

    root_supported_controllers = root_cgroup / "cgroup.controllers"
    root_supported_controllers.write_text(DUMMY_CONTROLLER)
    root_delegated_controllers = root_cgroup / "cgroup.subtree_control"
    root_delegated_controllers.touch()
    parent_supported_controllers = parent_cgroup / "cgroup.controllers"
    parent_supported_controllers.touch()
    parent_delegated_controllers = parent_cgroup / "cgroup.subtree_control"
    parent_delegated_controllers.touch()

    SUB_CGROUP_NAME = "sub_cgroup"
    sub_cgroup_dir = root_cgroup / SUB_CGROUP_NAME

    cgroup = CgroupCoreV2(current_leaf, root_cgroup)
    sub_cgroup = cgroup.get_subcgroup(DUMMY_CONTROLLER, SUB_CGROUP_NAME)
    assert sub_cgroup_dir.absolute() == sub_cgroup.cgroup_full_path.absolute()
    assert root_delegated_controllers.read_text().strip() == f"+{DUMMY_CONTROLLER}"


def test_get_cgroup_current_process():
    root_path = Path("/root_path")
    cgroup_path = "/dummy"
    full_path = Path("/root_path/dummy")

    with patch("granulate_utils.linux.cgroups.cgroup.get_cgroup_mount", return_value=CgroupCoreV1(root_path)):
        with patch(
            "granulate_utils.linux.cgroups.cgroup.read_proc_file",
            return_value=f"1:{DUMMY_CONTROLLER}:{cgroup_path}\n".encode(),
        ):
            cgroup = get_cgroup_for_process(DUMMY_CONTROLLER)
            assert cgroup.cgroup_full_path == full_path

    with patch(
        "granulate_utils.linux.cgroups.cgroup.get_cgroup_mount", return_value=CgroupCoreV2(root_path, root_path)
    ):
        with patch(
            "granulate_utils.linux.cgroups.cgroup.read_proc_file",
            return_value=f"0::{cgroup_path}\n".encode(),
        ):
            cgroup = get_cgroup_for_process(DUMMY_CONTROLLER)
            assert cgroup.cgroup_full_path == full_path

    with pytest.raises(Exception) as exception:
        with patch(
            "granulate_utils.linux.cgroups.cgroup.get_cgroup_mount", return_value=CgroupCoreV2(root_path, root_path)
        ):
            with patch(
                "granulate_utils.linux.cgroups.cgroup.read_proc_file",
                return_value="".encode(),
            ):
                cgroup = get_cgroup_for_process(DUMMY_CONTROLLER)
    assert exception.value.args[0] == f"'{DUMMY_CONTROLLER}' not found"


# CpuController
def test_cpu_controller_v1(tmp_path_factory: TempPathFactory):
    tmp_dir = tmp_path_factory.mktemp("cpu_controller")
    cpu_controller_dir = Path(tmp_dir)
    cpu_period = cpu_controller_dir / "cpu.cfs_period_us"
    cpu_quota = cpu_controller_dir / "cpu.cfs_quota_us"
    cpu_stat = cpu_controller_dir / "cpu.stat"

    cpu_period.write_text("100")
    cpu_quota.write_text("50")
    cpu_stat.write_text("stat_value 1")

    cgroup_v1 = CgroupCoreV1(cpu_controller_dir)
    cpu_controller = CpuControllerFactory.get_cpu_controller(cgroup_v1)
    assert cpu_controller.get_cpu_limit_cores() == 0.5
    stat_data = cpu_controller.get_stat()
    assert len(stat_data) == 1
    assert stat_data["stat_value"] == 1

    cpu_controller.set_cpu_limit_cores(3.5)
    assert cpu_controller.get_cpu_limit_cores() == 3.5
    assert int(cpu_quota.read_text()) == 350

    cpu_controller.reset_cpu_limit()
    assert cpu_controller.get_cpu_limit_cores() == -1
    assert int(cpu_quota.read_text()) == -1


def test_cpu_controller_v2(tmp_path_factory: TempPathFactory):
    tmp_dir = tmp_path_factory.mktemp("cpu_controller")
    cpu_controller_dir = Path(tmp_dir)
    cpu_max = cpu_controller_dir / "cpu.max"
    cpu_stat = cpu_controller_dir / "cpu.stat"

    cpu_max.write_text("50 100")
    cpu_stat.write_text("stat_value 1")

    cgroup_v2 = CgroupCoreV2(cpu_controller_dir, cpu_controller_dir)
    cpu_controller = CpuControllerFactory.get_cpu_controller(cgroup_v2)
    assert cpu_controller.get_cpu_limit_cores() == 0.5
    stat_data = cpu_controller.get_stat()
    assert len(stat_data) == 1
    assert stat_data["stat_value"] == 1

    cpu_controller.set_cpu_limit_cores(3.5)
    assert cpu_controller.get_cpu_limit_cores() == 3.5
    assert cpu_max.read_text() == "350 100"

    cpu_controller.reset_cpu_limit()
    assert cpu_controller.get_cpu_limit_cores() == -1
    assert cpu_max.read_text() == "max 100"


# MemoryController
def test_memory_controller_v1(tmp_path_factory: TempPathFactory):
    tmp_dir = tmp_path_factory.mktemp("memory_controller")
    memory_controller_dir = Path(tmp_dir)

    bytes_limit = memory_controller_dir / "memory.limit_in_bytes"
    swap_bytes_limit = memory_controller_dir / "memory.memsw.limit_in_bytes"
    max_bytes_usage = memory_controller_dir / "memory.max_usage_in_bytes"
    usage_in_bytes = memory_controller_dir / "memory.usage_in_bytes"

    bytes_limit.write_text("128")
    swap_bytes_limit.write_text("100")
    max_bytes_usage.write_text("400")
    usage_in_bytes.write_text("500")

    cgroup_v1 = CgroupCoreV1(memory_controller_dir)
    memory_controller = MemoryControllerFactory.get_memory_controller(cgroup_v1)
    assert memory_controller.get_memory_limit() == 128
    assert memory_controller.get_max_usage_in_bytes() == 400
    assert memory_controller.get_usage_in_bytes() == 500

    memory_controller.set_limit_in_bytes(50)
    assert memory_controller.get_memory_limit() == 50
    assert int(bytes_limit.read_text()) == 50
    assert int(swap_bytes_limit.read_text()) == 50

    memory_controller.reset_memory_limit()
    assert memory_controller.get_memory_limit() == -1
    assert int(bytes_limit.read_text()) == -1
    assert int(swap_bytes_limit.read_text()) == -1


def test_memory_controller_v2(tmp_path_factory: TempPathFactory):
    tmp_dir = tmp_path_factory.mktemp("memory_controller")
    memory_controller_dir = Path(tmp_dir)

    bytes_limit = memory_controller_dir / "memory.max"
    swap_bytes_limit = memory_controller_dir / "memory.swap.max"
    usage_in_bytes = memory_controller_dir / "memory.current"

    bytes_limit.write_text("128")
    swap_bytes_limit.write_text("100")
    usage_in_bytes.write_text("500")

    cgroup_v2 = CgroupCoreV2(memory_controller_dir, memory_controller_dir)
    memory_controller = MemoryControllerFactory.get_memory_controller(cgroup_v2)
    assert memory_controller.get_memory_limit() == 128
    assert memory_controller.get_usage_in_bytes() == 500

    memory_controller.set_limit_in_bytes(50)
    assert memory_controller.get_memory_limit() == 50
    assert int(bytes_limit.read_text()) == 50
    assert int(swap_bytes_limit.read_text()) == 0

    memory_controller.reset_memory_limit()
    assert memory_controller.get_memory_limit() == -1
    assert bytes_limit.read_text() == "max"
    assert swap_bytes_limit.read_text() == "max"

    with pytest.raises(CgroupInterfaceNotSupported) as exception:
        memory_controller.get_max_usage_in_bytes()
    assert exception.value.args[0] == "Interface file max_usage_in_bytes is not supported in cGroup v2"


# CpuAcctController
def test_cpuacct_controller(tmp_path_factory: TempPathFactory):
    tmp_dir = tmp_path_factory.mktemp("cpuacct_controller")
    cpuacct_controller_dir = Path(tmp_dir)

    cpuacct_usage = cpuacct_controller_dir / "cpuacct.usage"
    cpuacct_usage.write_text("128")

    cgroup_v1 = CgroupCoreV1(cpuacct_controller_dir)
    cpuacct_controller = CpuAcctController(cgroup_v1)
    assert cpuacct_controller.get_cpu_time_ns() == 128
