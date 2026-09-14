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
The image's entry point — the whole integration with Fred.

    python -m fred_samples_webdav_kb publish   # declare this KB, then exit
    python -m fred_samples_webdav_kb run       # serve runs until stopped

The SDK owns both commands. An author writes no plumbing for either.
"""

from fred_sdk.knowledge_base import knowledge_base_main

from fred_samples_webdav_kb.knowledge_base import kb

raise SystemExit(knowledge_base_main(kb))
