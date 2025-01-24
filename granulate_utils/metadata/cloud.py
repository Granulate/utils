#
# Copyright (C) 2023 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import logging
import os
from dataclasses import dataclass
from http.client import NOT_FOUND
from typing import Dict, List, Optional, Union

import requests
from requests import Response
from requests.exceptions import ConnectionError

from granulate_utils.exceptions import BadResponseCode
from granulate_utils.futures import call_in_parallel
from granulate_utils.linux.ns import run_in_ns_wrapper
from granulate_utils.metadata import Metadata

METADATA_REQUEST_TIMEOUT = 5


@dataclass
class CloudMetadataBase:
    provider: str


@dataclass
class AwsInstanceMetadata(CloudMetadataBase):
    region: str
    zone: str
    instance_type: str
    life_cycle: str
    account_id: str
    image_id: str
    instance_id: str


@dataclass
class AwsContainerMetadata(CloudMetadataBase):
    execution_env: str
    region: str
    container_arn: str


@dataclass
class GcpInstanceMetadata(CloudMetadataBase):
    provider: str
    zone: str
    instance_type: str
    preempted: bool
    preemptible: bool
    instance_id: str
    image_id: str
    name: str


@dataclass
class AzureInstanceMetadata(CloudMetadataBase):
    provider: str
    instance_type: str
    zone: str
    region: str
    subscription_id: str
    resource_group_name: str
    resource_id: str
    instance_id: str
    name: str
    image_info: Optional[Dict[str, str]]


def get_aws_metadata() -> Optional[Union[AwsInstanceMetadata, AwsContainerMetadata]]:
    aws_execution_env = get_aws_execution_env()
    if aws_execution_env == "AWS_ECS_FARGATE":
        return get_aws_container_metadata()
    else:
        return get_aws_instance_metadata()


def get_aws_instance_metadata() -> Optional[AwsInstanceMetadata]:
    # Documentation:
    # on the format: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instancedata-data-categories.html
    # on the protocol: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instancedata-data-retrieval.html
    token_resp = send_request(
        "http://169.254.169.254/latest/api/token", method="put", headers={"X-aws-ec2-metadata-token-ttl-seconds": "120"}
    )
    if token_resp is None:
        return None

    token = token_resp.text
    metadata_response = send_request(
        "http://169.254.169.254/latest/dynamic/instance-identity/document", headers={"X-aws-ec2-metadata-token": token}
    )
    life_cycle_response = send_request(
        "http://169.254.169.254/latest/meta-data/instance-life-cycle", headers={"X-aws-ec2-metadata-token": token}
    )
    if life_cycle_response is None or metadata_response is None:
        return None
    instance = metadata_response.json()
    return AwsInstanceMetadata(
        provider="aws",
        region=instance["region"],
        zone=instance["availabilityZone"],
        instance_type=instance["instanceType"],
        life_cycle=life_cycle_response.text,
        account_id=instance["accountId"],
        image_id=instance["imageId"],
        instance_id=instance["instanceId"],
    )


def get_aws_container_metadata() -> Optional[AwsContainerMetadata]:
    ecs_container_metadata_uri_v4 = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")
    if ecs_container_metadata_uri_v4 is None:
        return None
    response = send_request(ecs_container_metadata_uri_v4)
    if response is None:
        return None
    metadata = response.json()
    return AwsContainerMetadata(
        provider="aws",
        execution_env=os.environ["AWS_EXECUTION_ENV"],
        region=os.environ["AWS_REGION"],
        container_arn=metadata["ContainerARN"],
    )


def get_gcp_metadata() -> Optional[GcpInstanceMetadata]:
    # Documentation: https://cloud.google.com/compute/docs/storing-retrieving-metadata
    # https://cloud.google.com/compute/docs/metadata/default-metadata-values
    response = send_request(
        "http://metadata.google.internal/computeMetadata/v1/instance/?recursive=true",
        headers={"Metadata-Flavor": "Google"},
    )
    if response is None:
        return None
    instance = response.json()
    # Keep only the last part for these:
    #   machineType format is "projects/PROJECT_NUM/machineTypes/MACHINE_TYPE"
    #   zone format is "projects/PROJECT_NUM/zones/ZONE"
    return GcpInstanceMetadata(
        provider="gcp",
        zone=instance["zone"].rpartition("/")[2],
        instance_type=instance["machineType"].rpartition("/")[2],
        preemptible=instance["scheduling"]["preemptible"] == "TRUE",
        preempted=instance["preempted"] == "TRUE",
        instance_id=str(instance["id"]),
        image_id=instance["image"],
        name=instance["name"],
    )


def get_azure_metadata() -> Optional[AzureInstanceMetadata]:
    # Documentation: https://docs.microsoft.com/en-us/azure/virtual-machines/linux/instance-metadata-service?tabs=linux
    response = send_request(
        "http://169.254.169.254/metadata/instance/compute/?api-version=2019-08-15", headers={"Metadata": "true"}
    )
    if response is None:
        return None
    instance = response.json()
    image_info = None
    storage_profile = instance.get("storageProfile")
    if isinstance(storage_profile, dict):
        image_reference = storage_profile.get("imageReference")
        if isinstance(image_reference, dict):
            image_info = {
                "image_id": image_reference["id"],
                "image_offer": image_reference["offer"],
                "image_publisher": image_reference["publisher"],
                "image_sku": image_reference["sku"],
                "image_version": image_reference["version"],
            }

    return AzureInstanceMetadata(
        provider="azure",
        instance_type=instance["vmSize"],
        zone=instance["zone"],
        region=instance["location"],
        subscription_id=instance["subscriptionId"],
        resource_group_name=instance["resourceGroupName"],
        resource_id=instance["resourceId"],
        instance_id=instance["vmId"],
        name=instance["name"],
        image_info=image_info,
    )


def send_request(url: str, headers: Dict[str, str] = None, method: str = "get") -> Optional[Response]:
    response = requests.request(method, url, headers=headers or {}, timeout=METADATA_REQUEST_TIMEOUT)
    if response.status_code == NOT_FOUND:
        # It's most likely the wrong cloud provider
        return None
    elif not response.ok:
        raise BadResponseCode(response.status_code)
    return response


def get_static_cloud_metadata(logger: Union[logging.LoggerAdapter, logging.Logger]) -> Optional[Metadata]:
    raised_exceptions: List[Exception] = []
    cloud_metadata_fetchers = [
        get_aws_metadata,
        get_gcp_metadata,
        get_azure_metadata,
    ]

    def _fetch() -> Optional[Metadata]:
        for future in call_in_parallel(cloud_metadata_fetchers, timeout=METADATA_REQUEST_TIMEOUT + 1):
            try:
                response = future.result()
                if response is not None:
                    return response.__dict__
            except (ConnectionError, BadResponseCode):
                pass
            except Exception as exception:
                raised_exceptions.append(exception)

        return None

    try:
        metadata = run_in_ns_wrapper(["net"], _fetch)
        if metadata is not None:
            return metadata
    except TimeoutError as exception:
        raised_exceptions.append(exception)

    formatted_exceptions = (
        ", ".join([repr(exception) for exception in raised_exceptions]) if raised_exceptions else "(none)"
    )
    logger.debug(
        f"Could not get any cloud instance metadata because of the following exceptions: {formatted_exceptions}."
        " The most likely reason is that we're not installed on a an AWS, GCP or Azure instance."
    )
    return None


def get_aws_execution_env() -> Optional[str]:
    """
    Possible values include:
    - AWS_ECS_FARGATE
    - AWS_ECS_EC2
    - CloudShell
    """
    return os.environ.get("AWS_EXECUTION_ENV")
