#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
# (C) Datadog, Inc. 2018-present. All rights reserved.
# Licensed under a 3-clause BSD style license (see LICENSE.bsd3).
#
from typing import Any, Dict, Generator, List, Optional

from granulate_utils.metrics import json_request, set_metrics_from_json

YARN_CLUSTER_METRICS = {
    metric: f"yarn_cluster_{metric}"
    for metric in (
        "appsSubmitted",
        "appsCompleted",
        "appsPending",
        "appsRunning",
        "appsFailed",
        "appsKilled",
        "totalMB",
        "availableMB",
        "allocatedMB",
        "availableVirtualCores",
        "allocatedVirtualCores",
        "totalNodes",
        "activeNodes",
        "lostNodes",
        "decommissioningNodes",
        "decommissionedNodes",
        "rebootedNodes",
        "shutdownNodes",
        "unhealthyNodes",
        "containersAllocated",
        "containersPending",
    )
}
YARN_NODES_METRICS = {
    metric: f"yarn_node_{metric}"
    for metric in (
        "numContainers",
        "usedMemoryMB",
        "availMemoryMB",
        "usedVirtualCores",
        "availableVirtualCores",
        "nodePhysicalMemoryMB",
        "nodeVirtualMemoryMB",
        "nodeCPUUsage",
        "containersCPUUsage",
        "aggregatedContainersPhysicalMemoryMB",
        "aggregatedContainersVirtualMemoryMB",
    )
}

YARN_RM_CLASSNAME = "org.apache.hadoop.yarn.server.resourcemanager.ResourceManager"


class ResourceManagerAPI:
    def __init__(self, rm_address: str):
        self._apps_url = f"{rm_address}/ws/v1/cluster/apps"
        self._metrics_url = f"{rm_address}/ws/v1/cluster/metrics"
        self._nodes_url = f"{rm_address}/ws/v1/cluster/nodes"

    def apps(self, **kwargs) -> List[Dict]:
        return json_request(self._apps_url, **kwargs).get("apps", {}).get("app", [])

    def metrics(self, **kwargs) -> Optional[Dict]:
        return json_request(self._metrics_url, **kwargs).get("clusterMetrics")

    def nodes(self, **kwargs) -> List[Dict]:
        return json_request(self._nodes_url, **kwargs).get("nodes", {}).get("node", [])


class YarnCollector:
    name = "yarn"

    def __init__(self, rm_address: str, logger: Any) -> None:
        self.rm_address = rm_address
        self.rm = ResourceManagerAPI(rm_address)
        self.logger = logger

    def collect(self) -> Generator[Dict[str, Any], None, None]:
        collected_metrics: Dict[str, Dict[str, Any]] = {}
        self._cluster_metrics(collected_metrics)
        self._nodes_metrics(collected_metrics)
        yield from collected_metrics.values()

    def _cluster_metrics(self, collected_metrics: Dict[str, Dict[str, Any]]) -> None:
        try:
            if cluster_metrics := self.rm.metrics():
                set_metrics_from_json(collected_metrics, {}, cluster_metrics, YARN_CLUSTER_METRICS)
        except Exception:
            self.logger.exception("Could not gather yarn cluster metrics")

    def _nodes_metrics(self, collected_metrics: Dict[str, Dict[str, Any]]) -> None:
        try:
            for node in self.rm.nodes(states="RUNNING"):
                for metric, value in node.get("resourceUtilization", {}).items():
                    node[metric] = value  # this will create all relevant metrics under same dictionary

                labels = {"node_hostname": node["nodeHostName"]}
                set_metrics_from_json(collected_metrics, labels, node, YARN_NODES_METRICS)
        except Exception:
            self.logger.exception("Could not gather yarn nodes metrics")