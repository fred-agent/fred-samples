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

import asyncio
import logging

from fred_sdk.contracts.models import FieldSpec, UIHints
from fred_sdk.knowledge_base import (
    KnowledgeBase,
    KnowledgeBaseIssue,
    KnowledgeBaseRunContext,
    KnowledgeBaseRunOutcome,
    KnowledgeBaseSyncResult,
)

from fred_samples_git_kb.git_source import (
    GitRepositorySource,
    default_mirror_root,
    mirror_for,
)
from fred_samples_git_kb.knowledge_flow import open_library
from fred_samples_git_kb.matching import DEFAULT_INCLUDE
from fred_samples_git_kb.providers import Provider
from fred_samples_git_kb.report import Issue, RunReport
from fred_samples_git_kb.settings import (
    DEFAULT_MAX_FILES,
    ConfigurationError,
    read_settings,
)
from fred_samples_git_kb.synchronize import synchronize as reconcile

logger = logging.getLogger(__name__)

kb = KnowledgeBase(
    id="fred.samples.git-repository",
    version="1.0.0",
    name="Git repository",
    description=(
        "Synchronize documents from a branch of a GitHub or GitLab repository."
    ),
    configuration_fields=[
        FieldSpec(
            key="provider",
            type="select",
            title="Provider",
            description="Which forge the repository is on.",
            required=True,
            default=Provider.github.value,
            enum=[provider.value for provider in Provider],
        ),
        FieldSpec(
            key="repository",
            type="string",
            title="Repository",
            description=(
                "owner/name on GitHub, group/project on GitLab — or the address "
                "copied from the browser."
            ),
            required=True,
            ui=UIHints(placeholder="ThalesGroup/fred"),
        ),
        FieldSpec(
            key="branch",
            type="string",
            title="Branch",
            description="Leave empty for the branch the repository treats as default.",
        ),
        FieldSpec(
            key="token",
            type="secret",
            title="Access token",
            description=(
                "A read-only personal access token. Only needed for a private "
                "repository; a public one synchronizes without one."
            ),
        ),
        FieldSpec(
            key="host",
            type="string",
            title="Host",
            description=(
                "For a self-hosted forge — GitHub Enterprise, or GitLab on your "
                "own servers. Leave empty for github.com or gitlab.com."
            ),
        ),
        FieldSpec(
            key="subdirectory",
            type="string",
            title="Folder",
            description=(
                "Synchronize only this folder of the repository, and drop it "
                "from the layout here. Leave empty for the whole repository."
            ),
            ui=UIHints(placeholder="docs"),
        ),
        FieldSpec(
            key="include",
            type="string",
            title="Files",
            description=(
                "Which files to take, written the way .gitignore is. Separate "
                "several with commas."
            ),
            default=DEFAULT_INCLUDE[0],
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
                "A run is refused rather than half done if the repository has "
                "more matching files than this."
            ),
            default=DEFAULT_MAX_FILES,
            min=1,
        ),
    ],
)


@kb.synchronize
async def synchronize(context: KnowledgeBaseRunContext) -> KnowledgeBaseSyncResult:
    """Bring this run's library to what its branch now holds."""
    try:
        settings = read_settings(context.configuration)
    except ConfigurationError as error:
        return _refused(error.code, str(error))

    source = GitRepositorySource(
        url=settings.forge.clone_url,
        branch=settings.branch,
        mirror=mirror_for(
            default_mirror_root(),
            url=settings.forge.clone_url,
            branch=settings.branch,
            instance=context.instance_id,
        ),
        username=settings.forge.username,
        token=settings.token,
    )
    library = open_library(context.library_id)
    try:
        report = await reconcile(settings=settings, source=source, library=library)
    finally:
        await library.aclose()
        # Closing the mirror releases its file handles; the objects stay, which
        # is the whole point of it being a cache rather than a temporary.
        await asyncio.to_thread(source.close)

    logger.info(
        "[GIT KB] %s %s: %s",
        settings.forge.web_url,
        settings.branch or "(default branch)",
        report.summary(),
    )
    return _result(report)


def _result(report: RunReport) -> KnowledgeBaseSyncResult:
    """Say what happened in Fred's vocabulary.

    The one place the two vocabularies meet. Fred asks for five counters;
    `unchanged` is not among the things this implementation can prove, because
    neither a difference between two revisions nor a full pass enumerates what
    was left alone — so it is reported as nothing rather than guessed.
    """
    return KnowledgeBaseSyncResult(
        outcome=(
            KnowledgeBaseRunOutcome.succeeded
            if report.succeeded
            else KnowledgeBaseRunOutcome.failed
        ),
        reconciliation_complete=report.exhaustive,
        summary=report.summary(),
        discovered=report.considered,
        created=report.written_new,
        updated=report.written_existing,
        removed=report.retracted,
        warnings=[_issue(issue) for issue in report.warnings],
        errors=[_issue(issue) for issue in report.errors],
        metrics=report.metrics(),
    )


def _issue(issue: Issue) -> KnowledgeBaseIssue:
    return KnowledgeBaseIssue(
        code=issue.code, message=issue.message, subject=issue.subject
    )


def _refused(code: str, message: str) -> KnowledgeBaseSyncResult:
    """A run that never read the repository proves nothing about the library."""
    return KnowledgeBaseSyncResult(
        outcome=KnowledgeBaseRunOutcome.failed,
        reconciliation_complete=False,
        summary=message,
        errors=[KnowledgeBaseIssue(code=code, message=message)],
    )
