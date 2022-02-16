#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from dataclasses import dataclass
from typing import Optional


@dataclass
class Container:
    """
    Shared "Container" descriptor class, used for Docker containers & CRI containers.
    """

    runtime: str  # docker / containerd / crio
    # container name for Docker
    # reconstructed container name (as if it were Docker) for CRI
    name: str
    id: str
    labels: dict
    # follows CRI convention: created, running, exited, unknown
    state: str
    # None if not requested / container is dead
    pid: Optional[int]