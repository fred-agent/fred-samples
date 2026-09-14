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
The whole authoring surface: what a team fills in, and one handler.

Everything this file does is declare and delegate. The declaration is what
`publish` sends to Fred, so it is what a team's form is drawn from; the handler
is what a dispatched run calls. The synchronization itself lives in
`synchronize.py` and knows nothing about Fred.
"""

from __future__ import annotations

import logging

from fred_sdk.contracts.models import FieldSpec, UIHints
from fred_sdk.knowledge_base import (
    KnowledgeBase,
    KnowledgeBaseIssue,
    KnowledgeBaseRunContext,
    KnowledgeBaseRunOutcome,
    KnowledgeBaseSyncResult,
)

from fred_samples_webdav_kb.document_boundary import open_boundary
from fred_samples_webdav_kb.ledger import ledger_path_for
from fred_samples_webdav_kb.report import Issue, RunReport
from fred_samples_webdav_kb.settings import (
    DEFAULT_INCLUDE,
    DEFAULT_MAX_FILES,
    ConfigurationError,
    read_settings,
)
from fred_samples_webdav_kb.synchronize import synchronize as reconcile
from fred_samples_webdav_kb.webdav import (
    TrustStoreError,
    WebDavSource,
    configured_ca_file,
    tls_policy,
)

logger = logging.getLogger(__name__)

kb = KnowledgeBase(
    id="fred.samples.webdav",
    version="1.0.0",
    name="WebDAV share",
    description="Synchronize documents from a folder published over WebDAV.",
    configuration_fields=[
        FieldSpec(
            key="url",
            type="url",
            title="Address",
            description=(
                "The folder to synchronize, as you would open it in a browser."
            ),
            required=True,
            ui=UIHints(placeholder="https://share.example.com/documents/"),
        ),
        FieldSpec(
            key="username",
            type="string",
            title="User name",
            description="Leave empty for a share that needs no sign-in.",
        ),
        FieldSpec(
            key="password",
            type="secret",
            title="Password",
            description="Only needed with a user name.",
        ),
        FieldSpec(
            key="include",
            type="string",
            title="Files",
            description=(
                "Which files to take, written the way .gitignore is. Separate "
                "several with commas — **/*.md,**/*.pdf takes both."
            ),
            default=DEFAULT_INCLUDE,
        ),
        FieldSpec(
            key="exclude",
            type="string",
            title="Except",
            description="Which of those to leave out. Same way of writing them.",
        ),
        FieldSpec(
            key="max_files",
            type="integer",
            title="Maximum documents",
            description=(
                "A run carries at most this many and leaves the rest until the "
                "bound is raised. A bounded run never removes anything."
            ),
            default=DEFAULT_MAX_FILES,
            min=1,
        ),
        FieldSpec(
            key="trust_any_certificate",
            type="boolean",
            title="Accept any certificate (not protected against interception)",
            description=(
                "Only for a share whose certificate authority this deployment "
                "cannot be given. Prefer trusting the authority itself through "
                "$SSL_CERT_FILE. Every run using this reports a warning."
            ),
            default=False,
        ),
    ],
)


@kb.synchronize
async def synchronize(context: KnowledgeBaseRunContext) -> KnowledgeBaseSyncResult:
    """Bring this run's library to what its share now holds."""
    try:
        settings = read_settings(context.configuration)
        verify = tls_policy(
            trust_any_certificate=settings.trust_any_certificate,
            ca_file=configured_ca_file(),
        )
    except ConfigurationError as error:
        return _refused(error.code, str(error))
    except TrustStoreError as error:
        # A deployment fault, not a team's: worth its own code so it is not
        # read as "the share is down".
        return _refused("ca_file_unreadable", str(error))

    source = WebDavSource(
        where=settings.where,
        username=settings.username,
        password=settings.password,
        verify=verify,
        max_file_bytes=settings.max_file_bytes,
    )
    boundary = open_boundary(library_id=context.library_id)
    try:
        report = await reconcile(
            settings=settings,
            source=source,
            boundary=boundary,
            ledger_path=ledger_path_for(context.instance_id),
        )
    finally:
        await boundary.aclose()
        await source.aclose()

    logger.info("[WEBDAV KB] %s: %s", settings.where.url, report.summary())
    return _result(report)


def _result(report: RunReport) -> KnowledgeBaseSyncResult:
    """Say what happened in Fred's vocabulary.

    The one place the two vocabularies meet. `reconciliation_complete` is the
    field that matters: it is what tells Fred this run saw the whole share, and
    it is false for every pass that was bounded, cut short, or run without a
    readable ledger.
    """
    return KnowledgeBaseSyncResult(
        outcome=(
            KnowledgeBaseRunOutcome.succeeded
            if report.succeeded
            else KnowledgeBaseRunOutcome.failed
        ),
        reconciliation_complete=report.exhaustive,
        summary=report.summary(),
        discovered=report.discovered,
        created=report.created,
        updated=report.updated,
        removed=report.removed,
        unchanged=report.unchanged,
        warnings=[_issue(issue) for issue in report.warnings],
        errors=[_issue(issue) for issue in report.errors],
        metrics=report.metrics(),
    )


def _issue(issue: Issue) -> KnowledgeBaseIssue:
    return KnowledgeBaseIssue(
        code=issue.code, message=issue.message, subject=issue.subject
    )


def _refused(code: str, message: str) -> KnowledgeBaseSyncResult:
    """A run that never read the share proves nothing about the library."""
    return KnowledgeBaseSyncResult(
        outcome=KnowledgeBaseRunOutcome.failed,
        reconciliation_complete=False,
        summary=message,
        errors=[KnowledgeBaseIssue(code=code, message=message)],
    )
