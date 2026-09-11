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
The whole entry point of a Knowledge Base image.

`python -m fred_samples_local_folder_kb publish` posts the declaration at
deployment time; `run` serves runs. Both come from the SDK — this file is the
entire integration an author writes.
"""

from fred_sdk.knowledge_base import knowledge_base_main

from fred_samples_local_folder_kb.knowledge_base import kb

raise SystemExit(knowledge_base_main(kb))
