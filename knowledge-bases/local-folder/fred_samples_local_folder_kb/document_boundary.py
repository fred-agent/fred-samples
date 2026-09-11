# Copyright Thales 2026
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
The seam where a real Knowledge Base hands documents to Fred.

Both functions only log, on purpose: documents will go through Knowledge Flow's
REST API, which a run cannot reach yet, and this is the one place that changes
when it can. A Knowledge Base never writes to OpenSearch, S3 or any store
directly — that is outside the contract.
"""

import logging

logger = logging.getLogger(__name__)


def publish_document(*, relative_path: str, content_hash: str, size_bytes: int) -> None:
    """Hand one created or updated document to Fred. Logs instead, for now."""
    logger.info(
        "would publish %s (%d bytes, sha256:%s)",
        relative_path,
        size_bytes,
        content_hash[:12],
    )


def retract_document(*, relative_path: str) -> None:
    """Tell Fred one document is gone from the source. Logs instead, for now."""
    logger.info("would retract %s", relative_path)
